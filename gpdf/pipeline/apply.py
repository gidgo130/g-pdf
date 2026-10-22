# SPDX-License-Identifier: AGPL-3.0-or-later
"""Apply the owner's confirmed catalogue decisions, writing NEW files only.

``rebuild_layer`` and ``ocr_missing`` write OUTDIR/<source stem>_rebuilt.pdf (and its
report): each target page is rendered and read by Tesseract exactly as the OCR step does
(ocr.ocr_pages); only after that page's Tesseract run succeeded is its old invisible text
removed (a redaction that keeps images and line art) and Tesseract's text laid over it.
A page whose text the redaction cannot remove (text in a form XObject...) is found on a
one-page copy first and keeps its old layer; a page with visible text is never touched;
a failed Tesseract page keeps its old layer. Links the redaction drops from /Annots are put
back (the same objects); annotations, bookmarks, named destinations and page objects are
not changed. ``keep_text`` and ``exclude`` write nothing.

Only decisions with ``confirmed: true`` whose catalogue status is confirmed are acted on,
after re-hashing the source. The catalogue folder gets one file of its own, applied.json
(a record per output); catalogue.json and catalogue.decisions.json are only read.
"""

from __future__ import annotations

import json
import os
import re
import statistics
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pymupdf

from gpdf import __version__
from gpdf.pipeline import catalogue, ocr, quality, tesseract
from gpdf.pipeline.split import same_file
from gpdf.pipeline.survey import EXCLUDE, KEEP_TEXT, OCR_MISSING, REBUILD, sha256

SCHEMA = 1
APPLIED = catalogue.APPLIED_LOG
SUFFIX = "_rebuilt"
REBUILT, OCR, VISIBLE_KEPT, NOT_REMOVABLE, NO_WORDS, BLANK, NO_IMAGE, FAILED = (
    "rebuilt", "ocr", "visible_text_kept", "text_not_removable", "no_words", "blank",
    "no_image", "failed")
STATUSES = (REBUILT, OCR, VISIBLE_KEPT, NOT_REMOVABLE, NO_WORDS, BLANK, NO_IMAGE, FAILED)
WRITES = (REBUILD, OCR_MISSING)


class ApplyError(Exception):
    """A refusal or failure with a message meant for the user."""


def output_name(src) -> str:
    return f"{Path(src).stem}{SUFFIX}.pdf"


def report_name(src) -> str:
    return f"{Path(src).stem}{SUFFIX}.report.json"


# ---------------------------------------------------------------------------- pages
def visible_text(page) -> bool:
    """Any text drawn visibly (render mode other than 3, not fully transparent)."""
    return any(s["type"] != 3 and s["opacity"] > 0 for s in page.get_texttrace())


def _redact(page) -> None:
    """Remove every text object of ``page``; images and line art stay."""
    page.add_redact_annot(page.rect, fill=False)
    page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE,
                          graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
                          text=pymupdf.PDF_REDACT_TEXT_REMOVE)


def removable(doc, index) -> bool:
    """The redaction leaves no text on page ``index`` (tried on a one-page copy)."""
    with pymupdf.open() as probe:
        probe.insert_pdf(doc, from_page=index, to_page=index, links=False, annots=False)
        _redact(probe[0])
        return not probe[0].get_text().strip()


def _annot_xrefs(doc, page_xref) -> list[int]:
    """The xrefs in a page's /Annots, read through the xref API (as gpdf.core does)."""
    kind, value = doc.xref_get_key(page_xref, "Annots")
    if kind == "null":
        return []
    if kind == "xref":
        value = doc.xref_object(int(value.split()[0]), compressed=True)
    return [int(n) for n in re.findall(r"(\d+) 0 R", value)]


def _restore_links(doc, page_xref, keep) -> None:
    """apply_redactions drops overlapped links from /Annots (the link objects survive):
    re-append the ones that went missing (review finding #17)."""
    present = _annot_xrefs(doc, page_xref)
    missing = [x for x in keep if x not in present]
    if missing:
        refs = [*present, *missing]
        doc.xref_set_key(page_xref, "Annots", "[" + " ".join(f"{x} 0 R" for x in refs) + "]")


def skip_visible(page) -> str | None:
    return VISIBLE_KEPT if visible_text(page) else None


def replace_layer(page, textpdf) -> str:
    """ocr.ocr_pages ``place``: the old text out (if it can go), Tesseract's text in."""
    doc = page.parent
    if not page.get_texttrace():
        ocr.overlay(page, textpdf)
        return OCR
    if not removable(doc, page.number):
        return NOT_REMOVABLE
    keep = [link["xref"] for link in page.get_links() if link.get("xref")]
    _redact(page)
    if page.get_text().strip():
        raise ApplyError(f"p. {page.number + 1}: text left after the redaction")
    _restore_links(doc, page.xref, keep)
    ocr.overlay(page, textpdf)
    return REBUILT


# ---------------------------------------------------------------------------- report
@dataclass
class PageApply:
    page: int                          # 1-based
    status: str
    lang: str | None = None
    old_pct: float | None = None       # suspect %, quality.score_text in the page language
    new_pct: float | None = None
    old_words: int = 0
    new_words: int = 0
    mean_conf: float | None = None
    seconds: float = 0.0
    error: str = ""


@dataclass
class ApplyReport:
    id: str
    source: str
    output: str
    action: str
    pages_spec: object                 # the decision's pages
    langs: str
    tesseract: str
    tessdata: str
    jobs: int
    pages_total: int
    pages: list[PageApply] = field(default_factory=list)
    seconds: float = 0.0
    size_ratio: float = 0.0
    schema: int = SCHEMA

    def by_status(self, status) -> list[PageApply]:
        return [p for p in self.pages if p.status == status]

    def medians(self) -> tuple[float | None, float | None]:
        """Median suspect % before / after over the pages scored on both sides."""
        both = [p for p in self.pages if p.old_pct is not None and p.new_pct is not None]
        if not both:
            return None, None
        return (round(statistics.median(p.old_pct for p in both), 2),
                round(statistics.median(p.new_pct for p in both), 2))

    def to_dict(self) -> dict:
        d = asdict(self)
        d = {"schema": d.pop("schema"), **d}
        before, after = self.medians()
        d["summary"] = {"counts": {s: len(self.by_status(s)) for s in STATUSES},
                        "median_before": before, "median_after": after,
                        "failed": [p.page for p in self.by_status(FAILED)],
                        "seconds": self.seconds, "size_ratio": self.size_ratio}
        return d

    def save(self, path) -> None:
        catalogue.write_text(path, json.dumps(self.to_dict(), indent=1, ensure_ascii=False)
                             + "\n")


# ---------------------------------------------------------------------------- one file
def target_pages(spec, count) -> list[int]:
    """0-based pages of a decision's ``pages``: "all"/None or a list of 1-based pages."""
    if spec in (None, "all"):
        return list(range(count))
    if isinstance(spec, str):
        return ocr.parse_pages(spec, count)
    if not isinstance(spec, list) or not all(isinstance(p, int) for p in spec):
        raise ApplyError(f"pages {spec!r} is not \"all\" or a list of page numbers")
    bad = [p for p in spec if not 1 <= p <= count]
    if bad:
        raise ApplyError(f"page(s) {bad} outside 1-{count}")
    return sorted({p - 1 for p in spec})


def apply_file(item, out, tess, jobs, progress=None) -> ApplyReport:
    """Write ``out`` for one planned item (see plan): the source with the target pages'
    text layer rebuilt."""
    t0 = time.perf_counter()
    src = Path(item.source)
    rows = {r["page"]: r for r in item.entry["measured"]["page_scores"]}
    file_lang = item.entry["measured"]["language"]["lang"]
    try:
        doc = pymupdf.open(src)
    except Exception as e:  # noqa: BLE001 - any open failure
        raise ApplyError(f"cannot open {src}: {e}") from None
    try:
        selected = target_pages(item.pages, len(doc))
        old = {i: doc[i].get_text() for i in selected}
        results = ocr.ocr_pages(doc, selected, tess, item.langs, jobs, progress,
                                skip=skip_visible, place=replace_layer)
        report = ApplyReport(item.id, str(src), str(out), item.action, item.pages, item.langs,
                             tess.version, tess.tessdata, jobs, len(doc))
        for r in results:
            i = r.page - 1
            lang = (rows.get(r.page) or {}).get("lang") or file_lang
            status = NO_WORDS if r.status == ocr.OCR and not r.words else r.status
            p = PageApply(r.page, status, lang, mean_conf=r.mean_conf, seconds=r.seconds,
                          error=r.error)
            before = quality.score_text(old[i], lang, r.page)
            after = quality.score_text(doc[i].get_text(), lang, r.page)
            p.old_pct, p.old_words = before.pct, before.words
            p.new_pct, p.new_words = after.pct, after.words
            report.pages.append(p)
        ocr.save(doc, out)
    finally:
        doc.close()
    report.size_ratio = round(os.path.getsize(out) / max(1, os.path.getsize(src)), 4)
    report.seconds = round(time.perf_counter() - t0, 2)
    return report


# ---------------------------------------------------------------------------- the plan
@dataclass
class Item:
    id: str
    rel: str
    source: str
    action: str
    pages: object = None
    langs: str = ""
    entry: dict | None = None
    refused: str = ""                  # why it is not done (a gate failed)
    note: str = ""                     # nothing to do, and why

    @property
    def writes(self) -> bool:
        return self.action in WRITES and not self.refused and not self.note


def _matches(only, id_, rel) -> bool:
    return not only or any(id_.startswith(o) or o.lower() in rel.lower() for o in only)


def plan(folder, outdir, only=(), force=False) -> list[Item]:
    """One item per decision in the catalogue folder, gated: what would be written, what
    is refused and why, and what needs nothing."""
    folder = Path(folder)
    if not (folder / catalogue.CATALOGUE).is_file():
        raise ApplyError(f"no {catalogue.CATALOGUE} in {folder}: run the survey first")
    cat = catalogue.load(folder)
    decisions = catalogue.load_decisions(folder)
    files = cat["files"]
    for r in cat.get("roots", []):
        if catalogue.under(outdir, r["path"]):
            raise ApplyError(f"the output folder {outdir} is inside the source folder "
                             f"{r['path']}; put it elsewhere")
    items = []
    for id_, d in sorted(decisions["files"].items(),
                         key=lambda kv: (files.get(kv[0], {}).get("rel")
                                         or kv[1].get("path") or "").lower()):
        e = files.get(id_)
        rel = e["rel"] if e else (d.get("path") or id_)
        if not _matches(only, id_, rel):
            continue
        it = Item(id_, rel, e["path"] if e else (d.get("path") or ""), d.get("action") or "",
                  d.get("pages"), entry=e)
        items.append(it)
        if it.action not in WRITES:
            it.note = "nothing to write"
        elif not d.get("confirmed"):
            it.refused = "not confirmed"
        elif e is None:
            it.refused = "not in the catalogue"
        elif e.get("status") == catalogue.MISSING:
            it.refused = "missing" + (" (replaced by a changed file: re-confirm it)"
                                      if e.get("replaced_by") else "")
        elif e.get("status") != catalogue.PRESENT or not e.get("measured"):
            it.refused = f"not measured ({e.get('error') or e.get('status')})"
        else:
            status, _, why = catalogue.decision_status(e, decisions, files)
            if status == catalogue.APPLIED:
                it.note = "already applied"
            elif status != catalogue.CONFIRMED:
                it.refused = f"{status}: {why}"
            elif e.get("working_file"):
                it.refused = ("not supported yet: file already has a working file "
                              f"({Path(e['working_file']['path']).name})")
            elif it.action == OCR_MISSING and not it.pages:
                it.refused = "ocr_missing without pages"
        if it.refused or it.note:
            continue
        it.langs = e["measured"]["language"].get("tesseract") or quality.TESSERACT_LANGS.get(
            e["measured"]["language"]["lang"], "spa")
        try:
            target_pages(it.pages, e["measured"]["pages"])
        except (ApplyError, ocr.OcrError) as err:
            it.refused = str(err)
            continue
        out = Path(outdir) / output_name(it.source)
        if not os.path.isfile(it.source):
            it.refused = f"source not found: {it.source}"
        elif sha256(it.source) != id_:
            it.refused = "the source changed since the survey (hash differs): survey again"
        elif same_file(it.source, out):
            it.refused = "the output would be the source file"
        elif out.exists() and not force:
            it.refused = f"{out.name} already exists (pass --force to replace it)"
    return items


# ---------------------------------------------------------------------------- applied.json
def record(folder, item, out, report) -> dict:
    """Append one record for ``out`` to DIR/applied.json (temp file + replace)."""
    data = catalogue.load_applied(folder)
    rec = {"id": item.id, "source": item.source, "action": item.action,
           "pages": item.pages, "output": str(Path(out).resolve()),
           "output_sha256": sha256(out), "time": catalogue.now(),
           "tool_version": __version__}
    before, after = report.medians()
    rec["median_before"], rec["median_after"] = before, after
    data["applied"].append(rec)
    catalogue.write_text(Path(folder) / APPLIED, catalogue.dumps(data))
    return rec


def prepare_tesseract(items, tesseract_path) -> dict:
    """A checked Tesseract per language set of the items that write."""
    out, base = {}, None
    for it in items:
        if it.writes and it.langs not in out:
            base = base or tesseract.probe(tesseract.find(tesseract_path))
            tesseract.check(base, it.langs)
            out[it.langs] = base
    return out
