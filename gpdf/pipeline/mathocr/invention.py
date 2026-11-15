# SPDX-License-Identifier: AGPL-3.0-or-later
"""Engine-invention check: paragraphs of the final pages that are not on the printed page.

MinerU sometimes writes text that the page does not hold, on figure and blank areas:
Chinese passages, "The quick brown fox ...", image descriptions, a fake theorem. Each
paragraph of a page is compared with a reference text of the same page; nothing in the
page is changed, a suspect paragraph only gets an ``invention_suspect`` flag in the page's
flags (a re-run replaces those flags and never touches the others). The owner decides.

Paragraphs: the page split at blank lines after removing ``$$`` displays, comments and
tables; inline math (``\\( \\)``, ``\\[ \\]``, ``$ $``) is removed before words are counted.
Words are letter runs, accents folded, lower case; only words of 4+ letters count. A
paragraph is scored when it has 8+ words and 4+ words of 4+ letters. Its share is the part
of its 4+-letter words found in the reference: in it, or within an OCR slip of a reference
word of similar length (difflib ratio >= 0.8, length within 2). Hyphenated line breaks in
the reference are joined, and both pieces are kept too.

Reference per page: the copy's text layer when it is usable (20+ words of 4+ letters, and
the median share of the page's scored paragraphs against it at least ``usable``), else
Tesseract on a 300 dpi grey render of the page. A page with no usable layer while Tesseract
is missing is unchecked: reported, never failed. A page without scored paragraphs needs no
reference.

Candidates: any paragraph, displays and tables included, with CJK letters (fewer than 4: the
weaker "CJK (few)") or a generic fixed phrase; and a scored paragraph whose share is below
the threshold of the reference used. Calibrated on BATCH_C_CLOSE's known inventions (recall
100 % in both modes; real paragraphs flagged: layer 0.7 %, Tesseract 0.4 % at 0.6).
"""

from __future__ import annotations

import difflib
import json
import re
import statistics
import tempfile
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

FLAG_TYPE = "invention_suspect"
THRESHOLD_LAYER = 0.6
THRESHOLD_TESS = 0.6
USABLE = 0.75
MIN_WORDS, MIN_LONG = 8, 4          # a scored paragraph: words, words of 4+ letters
LAYER_MIN_WORDS = 20                # a usable text layer: words of 4+ letters
SLIP_RATIO = 0.8
TESS_DPI = 300
START_WORDS = 12

CJK = re.compile(r"[　-鿿가-힯＀-￯]")          # any CJK (fullwidth punctuation too)
CJK_LETTER = re.compile(r"[぀-ヿ㐀-鿿가-힯]")   # kana, ideographs, hangul
CJK_CONFIRM = 4                     # 4+ CJK letters: CJK text; fewer, or punctuation only: "CJK (few)"
FIXED = [("quick brown fox", re.compile(r"the\s+quick\s+brown\s+fox", re.I)),
         ("no discernible text",
          re.compile(r"the\s+image\s+contains\s+no\s+(?:discernible\s+)?text", re.I)),
         ("chart is a graphic design", re.compile(r"the\s+chart\s+is\s+a\s+graphic\s+design", re.I))]
WORD = re.compile(r"[^\W\d_]+")
DISPLAY = re.compile(r"\$\$\n.*?\n\$\$", re.S)
INLINE = re.compile(r"\\\(.*?\\\)|\\\[.*?\\\]|\$[^$\n]+?\$", re.S)
COMMENT = re.compile(r"<!--.*?-->", re.S)
LEAD_NOTE = re.compile(r"\*\[(Printed pages? \d|This is the last page)")   # a lead's gap note


def fold(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)).lower()


def words4(s: str) -> list[str]:
    return [w for w in WORD.findall(fold(s)) if len(w) >= 4]


def ref_words(text: str) -> set[str]:
    """The 4+-letter words of a reference text; hyphenated line breaks joined, both pieces
    kept too."""
    joined = re.sub(r"(\w)[-­]\s*\n\s*(\w)", r"\1\2", text)
    return set(words4(text)) | set(words4(joined))


def paragraphs(md: str) -> list[dict]:
    """Each paragraph of a page: {raw, prose, words, w4, kind} with kind prose, display,
    table or lead_note (displays too, for the CJK and fixed-phrase rules)."""
    md = md.replace("\r\n", "\n")
    out, pos = [], 0
    for m in DISPLAY.finditer(md):
        out += _paras(md[pos:m.start()])
        out.append({"raw": m.group(0), "prose": "", "words": 0, "w4": [], "kind": "display"})
        pos = m.end()
    return out + _paras(md[pos:])


def _paras(chunk: str) -> list[dict]:
    res = []
    for para in re.split(r"\n\s*\n", chunk):
        s = COMMENT.sub("", para.strip()).strip()
        if not s:
            continue
        kind = ("table" if s.startswith("<table") or s.startswith("|")
                else "lead_note" if LEAD_NOTE.match(s) else "prose")
        prose = "" if kind != "prose" else re.sub(r"<[^>]+>", " ", INLINE.sub(" ", s))
        res.append({"raw": s, "prose": prose, "words": len(WORD.findall(prose)),
                    "w4": words4(prose), "kind": kind})
    return res


def always(p: dict) -> list[str]:
    """Why a paragraph is a candidate whatever the reference: CJK, a fixed phrase."""
    why = []
    if len(CJK_LETTER.findall(p["raw"])) >= CJK_CONFIRM:
        why.append("CJK")
    elif CJK.search(p["raw"]):
        why.append("CJK (few)")
    why += [name for name, rx in FIXED if rx.search(p["raw"])]
    return why


def confirmed_by_rule(rule: list[str]) -> bool:
    """CJK text (4+ letters) or a fixed phrase: an invention without a reading."""
    return any(r != "CJK (few)" for r in rule)


def scored(p: dict) -> bool:
    return p["kind"] == "prose" and p["words"] >= MIN_WORDS and len(p["w4"]) >= MIN_LONG


class Ref:
    """A page's reference words, with the OCR-slip match."""

    def __init__(self, words):
        self.words = set(words)
        self.by_len: dict[int, list[str]] = {}
        for w in self.words:
            self.by_len.setdefault(len(w), []).append(w)

    def has(self, w: str) -> bool:
        if w in self.words:
            return True
        cands = [c for n in range(len(w) - 2, len(w) + 3) for c in self.by_len.get(n, ())]
        return bool(difflib.get_close_matches(w, cands, n=1, cutoff=SLIP_RATIO))


def share(p: dict, ref: Ref) -> float:
    return sum(ref.has(w) for w in p["w4"]) / len(p["w4"]) if p["w4"] else 1.0


@dataclass
class Thresholds:
    layer: float = THRESHOLD_LAYER
    tess: float = THRESHOLD_TESS
    usable: float = USABLE


@dataclass
class PageCheck:
    status: str                     # layer | tesseract | unchecked | no_prose
    flags: list[dict] = field(default_factory=list)


def check_page(md: str, layer_text: str | None, tess_text=None, th: Thresholds | None = None,
               mode: str = "auto") -> PageCheck:
    """The ``invention_suspect`` flags of one page. ``layer_text``: the page's text layer
    (None: none); ``tess_text``: a callable giving the page's Tesseract text, or None when
    Tesseract can't be had, called only when the layer is not usable. ``mode`` "layer" or
    "tesseract" uses that reference alone (calibration)."""
    th = th or Thresholds()
    paras = paragraphs(md)
    sc = [p for p in paras if scored(p)]
    ref, used, limit = None, None, None
    if sc:
        if mode in ("auto", "layer") and layer_text is not None:
            lw = ref_words(layer_text)
            lref = Ref(lw)
            if mode == "layer" or (len(lw) >= LAYER_MIN_WORDS
                                   and statistics.median(share(p, lref) for p in sc) >= th.usable):
                ref, used, limit = lref, "layer", th.layer
        if ref is None and mode in ("auto", "tesseract") and tess_text is not None:
            text = tess_text()
            if text is not None:
                ref, used, limit = Ref(ref_words(text)), "tesseract", th.tess
    out = []
    for i, p in enumerate(paras):
        why = always(p)
        sh = round(share(p, ref), 3) if ref is not None and scored(p) else None
        if why or (sh is not None and sh < limit):
            out.append({"type": FLAG_TYPE, "para_index": i, "kind": p["kind"], "words": p["words"],
                        "share": sh, "ref": used, "threshold": limit, "rule": why,
                        "start": " ".join(p["raw"].split()[:START_WORDS])})
    return PageCheck(used or ("unchecked" if sc else "no_prose"), out)


def with_flags(flags: list[dict], new: list[dict]) -> list[dict]:
    """``flags`` with its earlier invention flags replaced by ``new`` (before the summary
    record, which stays last); every other flag is kept as it is."""
    keep = [f for f in flags if f.get("type") != FLAG_TYPE]
    at = len(keep) - 1 if keep and keep[-1].get("type") == "summary" else len(keep)
    return keep[:at] + list(new) + keep[at:]


# ---------- the source copy: text layer and page renders ----------

class PdfPages:
    """A PDF copy: its text layer, and pages rendered for Tesseract."""

    def __init__(self, doc):
        self.doc = doc

    def layer(self, f: int) -> str | None:
        return self.doc[f - 1].get_text() if 1 <= f <= self.doc.page_count else None

    def render(self, f: int, png: Path) -> None:
        self.doc[f - 1].get_pixmap(dpi=TESS_DPI, colorspace="gray").save(str(png))


class DjvuPages:
    """A DjVu copy: its hidden text layer (djvutxt; none without it), and pages rendered from
    the proxy PDF when there is one, else by ddjvu."""

    def __init__(self, tools, path, proxy_doc=None):
        self.tools, self.path, self.proxy = tools, Path(path), proxy_doc

    def layer(self, f: int) -> str | None:
        return self.tools.text(self.path, f)

    def render(self, f: int, png: Path) -> None:
        if self.proxy is not None:
            PdfPages(self.proxy).render(f, png)
        else:
            self.tools.render(self.path, f, TESS_DPI, png)


def _tesseract_text(pages, tess, lang: str, f: int, errors: list):
    from gpdf.pipeline import tesseract

    def run():
        with tempfile.TemporaryDirectory(prefix="gpdf-invention-") as tmp:
            png = Path(tmp) / "page.png"
            try:
                pages.render(f, png)
                return tesseract.recognize_text(tess, png, lang, TESS_DPI)
            except Exception as e:  # noqa: BLE001 (a page Tesseract can't read stays unchecked)
                errors.append({"file": f, "error": str(e)[-300:]})
                return None
    return run


def check_pages(mds: dict[int, str], pages, tess=None, lang: str = "eng",
                th: Thresholds | None = None, mode: str = "auto", progress=None) -> dict:
    """Check each page (``mds``: file -> page text) against ``pages`` (PdfPages, DjvuPages or
    None) and Tesseract ``tess`` (or None). Returns {"flags": {file: [flags]}, "summary"}."""
    th = th or Thresholds()
    flags, status, errors = {}, {}, []
    for n, f in enumerate(sorted(mds), 1):
        layer = pages.layer(f) if pages is not None else None
        tt = (_tesseract_text(pages, tess, lang, f, errors)
              if tess is not None and pages is not None else None)
        pc = check_page(mds[f], layer, tt, th, mode)
        flags[f], status[f] = pc.flags, pc.status
        if progress and n % 50 == 0:
            progress(n, len(mds))
    allf = [g for fl in flags.values() for g in fl]
    summary = {
        "pages": len(mds),
        "by_layer": sum(s == "layer" for s in status.values()),
        "by_tesseract": sum(s == "tesseract" for s in status.values()),
        "no_prose": sum(s == "no_prose" for s in status.values()),
        "unchecked": sorted(f for f, s in status.items() if s == "unchecked"),
        "candidates": len(allf),
        "rule_confirmed": sum(confirmed_by_rule(g["rule"]) for g in allf),
        "thresholds": {"layer": th.layer, "tesseract": th.tess, "usable": th.usable},
        "ocr_lang": lang if tess is not None else None,
        "tesseract": getattr(tess, "version", None),
    }
    if errors:
        summary["tesseract_errors"] = errors
    return {"flags": flags, "summary": summary}


def report(summary: dict) -> str:
    s = summary
    un = s["unchecked"]
    return (f"invention check: {s['pages']} pages, {s['by_layer']} by text layer, "
            f"{s['by_tesseract']} by Tesseract, {s['no_prose']} without scored prose, "
            f"{len(un)} unchecked" + (f" (files {', '.join(map(str, un[:20]))}"
                                      + (" ..." if len(un) > 20 else "") + ")" if un else "")
            + f"; {s['candidates']} candidates ({s['rule_confirmed']} rule-confirmed)")


# ---------- gpdf-cli math-invention-check ----------

class InventionError(Exception):
    """The export or the source cannot be used; the message is for the user."""


def open_source(source, meta: dict | None = None, ddjvu=None):
    """(pages, doc to close or None) for a source copy: a PDF, or a DjVu (its proxy PDF from
    the export's meta.json renders the pages when it is there)."""
    import pymupdf

    from gpdf.pipeline.mathocr import djvu

    source = Path(source)
    if not source.is_file():
        raise InventionError(f"source {source} not found (pass --source FILE)")
    if djvu.is_djvu(source):
        try:
            tools = djvu.prepare(ddjvu)
        except djvu.DjvuError as e:
            raise InventionError(str(e)) from None
        proxy = (meta or {}).get("source", {}).get("render_pdf")
        doc = pymupdf.open(proxy) if proxy and Path(proxy).is_file() else None
        return DjvuPages(tools, source, doc), doc
    doc = pymupdf.open(str(source))
    return PdfPages(doc), doc


def check_export(export, source=None, tesseract_exe=None, lang: str = "eng",
                 th: Thresholds | None = None, dry_run: bool = False, report_path=None,
                 ddjvu=None) -> dict:
    """Check the pages of a math-export folder; write the flags (not with ``dry_run``) and a
    JSON report (default ``<export>/invention_check.json``). Returns the report."""
    from gpdf.pipeline import tesseract
    from gpdf.pipeline.manuals import write_atomic

    root = Path(export)
    pages_dir = root / "pages"
    if not pages_dir.is_dir():
        raise InventionError(f"{pages_dir} not found: is {root} a math-export folder?")
    meta = json.loads((root / "meta.json").read_text("utf-8")) if (root / "meta.json").is_file() else {}
    source = source or meta.get("source", {}).get("path")
    if not source:
        raise InventionError("no source copy: pass --source FILE (meta.json names none)")
    notices = []
    try:
        tess = tesseract.probe(tesseract.find(tesseract_exe))
        tesseract.check(tess, lang)
    except tesseract.TesseractError as e:
        if tesseract_exe:
            raise InventionError(str(e)) from None
        tess = None
        notices.append(f"no Tesseract: pages without a usable text layer stay unchecked ({e})")
    mds = {int(p.stem[1:]): p.read_text("utf-8") for p in sorted(pages_dir.glob("p[0-9][0-9][0-9].md"))}
    pages, doc = open_source(source, meta, ddjvu)
    try:
        res = check_pages(mds, pages, tess, lang, th)
    finally:
        if doc is not None:
            doc.close()
    if not dry_run:
        for f, new in res["flags"].items():
            fp = pages_dir / f"p{f:03d}.flags.json"
            old = json.loads(fp.read_text("utf-8")) if fp.is_file() else []
            upd = with_flags(old, new)
            if upd != old:
                write_atomic(fp, json.dumps(upd, ensure_ascii=False, indent=1) + "\n")
    out = {"export": str(root), "source": str(source), "dry_run": dry_run, "notices": notices,
           "summary": res["summary"],
           "flags": [{"file": f, **g} for f, fl in sorted(res["flags"].items()) for g in fl]}
    rp = Path(report_path) if report_path else root / "invention_check.json"
    write_atomic(rp, json.dumps(out, ensure_ascii=False, indent=1) + "\n")
    out["report"] = str(rp)
    return out
