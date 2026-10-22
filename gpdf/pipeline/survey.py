# SPDX-License-Identifier: AGPL-3.0-or-later
"""Read-only survey of the corpus: measure each PDF and .txt, find files that hold the
same work, and suggest what to do with each (never applied here).

Measuring a PDF: page facts (text pages, scan or born-digital, scan dpi, bookmarks,
metadata), pages without text sorted into blank and ink pages, the page language and
the text-quality score (quality.py). A .txt is read as UTF-8 (cp1252 as a flagged
fallback), Project Gutenberg boilerplate and URL / site header lines are removed and
counted, and the rest is scored in pseudo-pages of 400 words.

Duplicates: 5-word shingles (accent-folded, lowercase) hashed with crc32;
containment = |A n B| / min(|A|, |B|): >= 0.5 is the same work, 0.05-0.5 related. One
file per same-work group is kept: a PDF before a .txt, then the superset, then the lower
suspect rate, then more bookmarks.

Nothing here writes outside the folder it is given (catalogue cache, a temporary folder).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import statistics
import tempfile
import time
import unicodedata
import zlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from functools import cmp_to_key
from pathlib import Path

import numpy as np
import pymupdf

from gpdf import __version__
from gpdf.pipeline import catalogue, ocr, quality, tesseract

TEXT_CHARS = 100            # a page with this many characters counts toward text %
SCAN_COVER = 0.8            # one image covering this share of the page: a scanned page
SCAN_PAGES = 0.9            # share of scanned pages for a scanned file
INK_DPI = 50                # pages without text: render at this dpi to look for ink
PSEUDO_PAGE = 400           # words per pseudo-page of a .txt
SAMPLE_CHARS = 600
WORST_SAMPLES = 3

SHINGLE = 5
MIN_SHINGLES = 100
SAME_WORK, RELATED = 0.5, 0.05
SUPERSET, SUPERSET_REVERSE = 0.9, 0.8

# rebuild trial triggers (scans only)
TRIGGER_SUSPECT = 3.0       # weighted suspect %
TRIGGER_FAIR_POOR = 0.25    # share of scored pages that are fair or poor
TRIGGER_MERGED = 2.0        # per 1000 words
TRIGGER_DIGIT = 2.0         # per 1000 words
TRIGGER_JUNK = 0.5          # %
TRIAL_WORST, TRIAL_SPREAD = 4, 4
TRIAL_GAIN_RATIO, TRIAL_GAIN_POINTS = 0.6, 1.0
TRIAL_MIN_PAGES = 4
OCR_MISSING_WORDS, OCR_MISSING_SUSPECT = 20, 8.0     # an ink page holds text
TRIAL_NOTE = ("Tesseract prefers dictionary words, so this score may flatter re-OCR "
              "(an audit against ground truth is still to come)")

KEEP_TEXT, OCR_MISSING, REBUILD, EXCLUDE, REVIEW = (
    "keep_text", "ocr_missing", "rebuild_layer", "exclude", "review")
ACTIONS = (KEEP_TEXT, OCR_MISSING, REBUILD, EXCLUDE, REVIEW)
DUPLICATE, OTHER_LANGUAGE, OFF_TOPIC, OTHER = "duplicate", "other_language", "off_topic", "other"
WORK_LANGS = ("es", "en")
PAGE_FIELDS = ("page", "lang", "words", "lower", "suspect", "pct", "cls", "merged", "spaced",
               "digit", "chaos", "junk_pct", "cap_unknown_pct")


class MeasureError(Exception):
    """The file cannot be read (encrypted, damaged, not a PDF)."""


# ---------------------------------------------------------------------------- PDFs
def _cover(bbox, page) -> float:
    """Share of the page covered by an image's box (in either orientation of the page)."""
    r = pymupdf.Rect(bbox)
    best = 0.0
    for frame in (page.rect, pymupdf.Rect(0, 0, page.cropbox.width, page.cropbox.height)):
        area = frame.get_area()
        if area > 0:
            best = max(best, (r & frame).get_area() / area)
    return best


def ink_fraction(page) -> float:
    pix = page.get_pixmap(dpi=INK_DPI, colorspace=pymupdf.csGRAY, alpha=False)
    g = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.stride)[:, :pix.width]
    return float((g < ocr.INK_LEVEL).mean())


def _rows(scored, images=None) -> list[dict]:
    rows = []
    for p in scored.pages:
        d = p.row()
        row = {k: d[k] for k in PAGE_FIELDS}
        if images is not None:
            row["image"] = images[p.page - 1]
        rows.append(row)
    return rows


def _samples(scored, raw) -> list[dict]:
    """The worst scored pages with the start of their extracted text."""
    worst = sorted((p for p in scored.pages if p.pct is not None),
                   key=lambda p: (-p.pct, p.page))[:WORST_SAMPLES]
    return [{"page": p.page, "pct": p.pct, "text": raw[p.page - 1][:SAMPLE_CHARS]}
            for p in sorted(worst, key=lambda p: p.page)]


def _language(scored) -> dict:
    lang = scored.lang
    return {"lang": lang, "shares": scored.shares,
            "tesseract": quality.TESSERACT_LANGS.get(lang, "spa")}


def measure_pdf(path) -> tuple[dict, list[str]]:
    """(measurement, prepared page texts) of a PDF."""
    t0 = time.perf_counter()
    try:
        doc = pymupdf.open(path)
    except Exception as e:  # noqa: BLE001 - any open failure is "cannot read"
        raise MeasureError(f"cannot open: {e}") from None
    with doc:
        if not doc.is_pdf:
            raise MeasureError("not a PDF")
        if doc.needs_pass:
            raise MeasureError("encrypted (password-protected)")
        errors = ["damaged: repaired when opened"] if doc.is_repaired else []
        raw, images, dpis = [], [], []
        text_pages = long_pages = scan_pages = 0
        no_text = {"blank": [], "ink": [], "empty": []}
        for i, page in enumerate(doc):
            try:
                text = page.get_text()
                infos = page.get_image_info()
            except Exception as e:  # noqa: BLE001 - a damaged page is recorded, not fatal
                errors.append(f"p. {i + 1}: {e}")
                text, infos = "", []
            raw.append(text)
            images.append(bool(infos))
            if any(_cover(info["bbox"], page) >= SCAN_COVER for info in infos):
                scan_pages += 1
                if (d := ocr.source_dpi(page)) is not None:
                    dpis.append(d)
            if len(text.strip()) >= TEXT_CHARS:
                long_pages += 1
            if text.split():
                text_pages += 1
            elif not infos:
                no_text["empty"].append(i + 1)
            else:
                ink = ink_fraction(page)
                no_text["blank" if ink < ocr.BLANK_INK else "ink"].append(i + 1)
        meta = doc.metadata or {}
        n = len(doc)
        bookmarks = len(doc.get_toc(simple=True))
    scored = quality.score_pages(raw)
    title = (meta.get("title") or "").strip()
    m = {
        "score_version": quality.SCORE_VERSION,
        "kind": "pdf",
        "pages": n,
        "text_pages": text_pages,
        "text_pct": round(100 * long_pages / n, 1) if n else 0.0,
        "scan": bool(n) and scan_pages >= SCAN_PAGES * n,
        "scan_pages": scan_pages,
        "dpi": round(statistics.median(dpis)) if dpis else None,
        "producer": meta.get("producer") or "",
        "creator": meta.get("creator") or "",
        "bookmarks": bookmarks,
        "title": title,
        "author": (meta.get("author") or "").strip(),
        "suggested_title": title or Path(path).stem,
        "language": _language(scored),
        "score": quality.summarise(scored.pages),
        "page_scores": _rows(scored, images),
        "no_text": no_text,
        "samples": _samples(scored, raw),
        "errors": errors,
    }
    m["seconds"] = round(time.perf_counter() - t0, 2)
    return m, scored.texts


# ---------------------------------------------------------------------------- .txt
_GUTENBERG_START = re.compile(r"^\s*\*\*\*\s*START OF", re.I)
_GUTENBERG_END = re.compile(r"^\s*\*\*\*\s*END OF", re.I)
_URL_LINE = re.compile(r"https?://|www\.", re.I)
_SITE_LINE = re.compile(r"ciudadseva|^\s*\[[^\]]*texto completo\.?\]\s*$", re.I)
_WORD_SPAN = re.compile(r"\S+\s*")


def read_txt(path) -> tuple[str, str]:
    """(text, encoding): UTF-8, else cp1252."""
    data = Path(path).read_bytes()
    try:
        return data.decode("utf-8-sig"), "utf-8"
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace"), "cp1252"


def strip_boilerplate(text) -> tuple[str, dict]:
    """The text without Project Gutenberg header/footer, URL lines and site header lines,
    and how many lines of each were removed."""
    lines = text.splitlines()
    counts = {"gutenberg_header": 0, "gutenberg_footer": 0, "url": 0, "site": 0}
    start = next((i for i, s in enumerate(lines) if _GUTENBERG_START.match(s)), None)
    end = next((i for i, s in enumerate(lines) if _GUTENBERG_END.match(s)), None)
    if end is not None and (start is None or end > start):
        counts["gutenberg_footer"] = len(lines) - end
        lines = lines[:end]
    if start is not None:
        counts["gutenberg_header"] = start + 1
        lines = lines[start + 1:]
    kept = []
    for s in lines:
        if _URL_LINE.search(s):
            counts["url"] += 1
        elif _SITE_LINE.search(s):
            counts["site"] += 1
        else:
            kept.append(s)
    return "\n".join(kept), counts


def pseudo_pages(text, words=PSEUDO_PAGE) -> list[str]:
    spans = [m.group() for m in _WORD_SPAN.finditer(text)]
    return ["".join(spans[i:i + words]) for i in range(0, len(spans), words)]


def measure_txt(path) -> tuple[dict, list[str]]:
    t0 = time.perf_counter()
    text, encoding = read_txt(path)
    body, boiler = strip_boilerplate(text)
    pages = pseudo_pages(body)
    scored = quality.score_pages(pages)
    m = {
        "score_version": quality.SCORE_VERSION,
        "kind": "txt",
        "pages": None,
        "pseudo_pages": len(pages),
        "text_pct": None,
        "scan": False,
        "encoding": encoding,
        "boilerplate": boiler,
        "suggested_title": Path(path).stem,
        "title": "",
        "author": "",
        "bookmarks": 0,
        "language": _language(scored),
        "score": quality.summarise(scored.pages),
        "page_scores": _rows(scored),
        "no_text": {"blank": [], "ink": [], "empty": []},
        "samples": _samples(scored, pages),
        "errors": [],
    }
    if encoding != "utf-8":
        m["errors_note"] = f"not UTF-8: read as {encoding}"
    m["seconds"] = round(time.perf_counter() - t0, 2)
    return m, scored.texts


def measure(path, kind) -> tuple[dict, np.ndarray]:
    """(measurement, shingles) of a file; raises MeasureError."""
    m, texts = (measure_pdf if kind == "pdf" else measure_txt)(path)
    return m, shingles("\n".join(texts))


# ---------------------------------------------------------------------------- duplicates
_LETTERS = re.compile(r"[^\W\d_]+")


def fold(text) -> str:
    """Lowercase without accents."""
    d = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in d if not unicodedata.combining(c))


def shingles(text) -> np.ndarray:
    """Sorted unique crc32 hashes of the 5-word shingles of the folded text."""
    words = [w for w in _LETTERS.findall(fold(text)) if len(w) >= 2]
    hashes = {zlib.crc32(" ".join(words[i:i + SHINGLE]).encode("utf-8"))
              for i in range(len(words) - SHINGLE + 1)}
    return np.array(sorted(hashes), dtype=np.uint32)


def overlaps(sets: dict[str, np.ndarray]) -> dict[tuple[str, str], int]:
    """|A n B| for every pair of ids with >= MIN_SHINGLES shingles (keys sorted)."""
    ids = sorted(k for k, v in sets.items() if len(v) >= MIN_SHINGLES)
    out = {}
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            out[(a, b)] = int(np.intersect1d(sets[a], sets[b], assume_unique=True).size)
    return out


def _better(a, b, inter, sizes) -> int:
    """-1 if entry ``a`` should be kept over ``b``, 1 if not."""
    pa, pb = a["kind"] == "pdf", b["kind"] == "pdf"
    if pa != pb:
        return -1 if pa else 1
    na, nb = sizes[a["id"]], sizes[b["id"]]
    b_in_a, a_in_b = inter / nb, inter / na
    if b_in_a >= SUPERSET and a_in_b < SUPERSET_REVERSE:
        return -1
    if a_in_b >= SUPERSET and b_in_a < SUPERSET_REVERSE:
        return 1
    inf = float("inf")
    sa = a["measured"]["score"]["weighted"]
    sb = b["measured"]["score"]["weighted"]
    sa, sb = (inf if sa is None else sa), (inf if sb is None else sb)
    if sa != sb:
        return -1 if sa < sb else 1
    ba, bb = a["measured"].get("bookmarks", 0), b["measured"].get("bookmarks", 0)
    if ba != bb:
        return -1 if ba > bb else 1
    return -1 if a["path"].lower() < b["path"].lower() else 1


def duplicates(entries: dict[str, dict], sets: dict[str, np.ndarray]) -> dict[str, dict]:
    """Per id: {group, same_work, related, keeper} (keeper: the id kept in its group).
    ``entries`` are the present, measured entries; ``sets`` their shingles."""
    inter = overlaps({k: sets[k] for k in entries if k in sets})
    sizes = {k: len(v) for k, v in sets.items()}
    out = {k: {"group": None, "same_work": [], "related": [], "keeper": None} for k in entries}
    parent = {k: k for k in entries}

    def find(k):
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    for (a, b), n in inter.items():
        c = round(n / min(sizes[a], sizes[b]), 3)
        kind = "same_work" if c >= SAME_WORK else "related" if c >= RELATED else None
        if not kind:
            continue
        out[a][kind].append({"id": b, "containment": c})
        out[b][kind].append({"id": a, "containment": c})
        if kind == "same_work":
            parent[find(a)] = find(b)
    groups = {}
    for k in entries:
        if out[k]["same_work"]:
            groups.setdefault(find(k), []).append(k)

    def name_key(k):
        e = entries[k]
        return (e["root"].lower(), e["rel"].lower())

    ordered = sorted(groups.values(), key=lambda g: min(name_key(k) for k in g))
    for n, members in enumerate(ordered, 1):
        def cmp(a, b):
            pair = (a, b) if a < b else (b, a)
            return _better(entries[a], entries[b], inter.get(pair, 0), sizes)

        keeper = sorted(members, key=cmp_to_key(cmp))[0]
        for k in members:
            out[k]["group"] = f"G{n}"
            out[k]["keeper"] = keeper
    for d in out.values():
        for kind in ("same_work", "related"):
            d[kind].sort(key=lambda x: (-x["containment"], x["id"]))
    return out


# ---------------------------------------------------------------------------- suggestions
def rebuild_triggers(score) -> list[str]:
    """Why a scan's text layer deserves a rebuild trial (empty: it does not)."""
    why = []
    w = score.get("weighted")
    if w is not None and w >= TRIGGER_SUSPECT:
        why.append(f"weighted suspect {w:.1f}% (>= {TRIGGER_SUSPECT:g}%)")
    if score.get("scored"):
        share = (score["fair"] + score["poor"]) / score["scored"]
        if share >= TRIGGER_FAIR_POOR:
            why.append(f"{100 * share:.0f}% of scored pages fair or poor "
                       f"(>= {100 * TRIGGER_FAIR_POOR:.0f}%)")
    rates = score.get("per_1000", {})
    if rates.get("merged", 0) >= TRIGGER_MERGED:
        why.append(f"merged words {rates['merged']:.1f}/1000 (>= {TRIGGER_MERGED:g})")
    if rates.get("digit", 0) >= TRIGGER_DIGIT:
        why.append(f"digits inside words {rates['digit']:.1f}/1000 (>= {TRIGGER_DIGIT:g})")
    if score.get("junk_pct", 0) >= TRIGGER_JUNK:
        why.append(f"junk characters {score['junk_pct']:.2f}% (>= {TRIGGER_JUNK:g}%)")
    return why


def _pct(v) -> str:
    return "n/a" if v is None else f"{v:.1f}%"


def _pages_text(pages) -> str:
    return ", ".join(str(p) for p in pages)


def suggest(entry: dict, dup: dict | None, names: dict[str, str]) -> dict:
    """The suggested action for a present entry, with its reasons in plain words.
    ``dup`` is the entry's duplicates record; ``names`` maps ids to display names."""
    m = entry.get("measured")
    if entry.get("status") == "error" or not m:
        errs = (m or {}).get("errors") or [entry.get("error") or "unknown error"]
        return {"action": REVIEW, "pages": None, "reason": "could not read",
                "duplicate_of": None, "reasons": [f"could not read: {e}" for e in errs]}
    if dup and dup.get("keeper") and dup["keeper"] != entry["id"]:
        keeper = dup["keeper"]
        c = next((x["containment"] for x in dup["same_work"] if x["id"] == keeper), None)
        why = [f"same work as {names.get(keeper, keeper)}"
               + (f" (containment {c:.2f})" if c is not None else "")
               + ", which is kept"]
        if m["kind"] == "txt":
            why.append("reference text: useful as ground truth later")
        return {"action": EXCLUDE, "pages": None, "reason": DUPLICATE, "duplicate_of": keeper,
                "reasons": why}
    lang = m["language"]["lang"]
    if lang not in WORK_LANGS:
        shares = ", ".join(f"{k} {v}%" for k, v in m["language"]["shares"].items())
        return {"action": EXCLUDE, "pages": None, "reason": OTHER_LANGUAGE, "duplicate_of": None,
                "reasons": [f"language {lang or 'unknown'} ({shares or 'no words'})"]}

    s = m["score"]
    reasons, action, pages, reason = [], KEEP_TEXT, None, None
    trial = entry.get("trial") or {}
    ran = bool(trial.get("ran"))
    if entry.get("working_file"):
        reasons.append(f"measured on the working file {Path(entry['working_file']['path']).name}")
        sl = entry.get("source_layer") or {}
        src = sl.get("score")
        if src and not sl.get("text_pages"):
            reasons.append("the source itself has no text layer")
        elif src:
            would = rebuild_triggers(src) if sl.get("scan") else []
            reasons.append(f"source's own layer: median suspect {_pct(src.get('median'))}, "
                           f"weighted {_pct(src.get('weighted'))}, {src.get('poor', 0)} poor "
                           "pages" + (f" (would trigger a rebuild trial: {'; '.join(would)})"
                                      if would else ""))
    if m["kind"] == "txt":
        reasons.append(f"plain text, median suspect {_pct(s['median'])}")
    elif not m["scan"]:
        reasons.append(f"born-digital: nothing to rebuild from (median suspect "
                       f"{_pct(s['median'])}, informational)")
    elif not m["text_pages"]:
        reasons.append("scan without a text layer")
    else:
        triggers = rebuild_triggers(s)
        if not triggers:
            reasons.append(f"text layer good: median suspect {_pct(s['median'])}, weighted "
                           f"{_pct(s['weighted'])}")
        else:
            reasons.append("rebuild trial: " + "; ".join(triggers))
            v = trial.get("verdict") if ran else None
            if not ran:
                action, reason = REVIEW, "trial not run"
                reasons.append(trial.get("note") or "trial not run")
            elif v and v["action"] == REBUILD:
                action, pages = REBUILD, "all"
                reasons.append(v["why"])
            else:
                reasons.append(v["why"] if v else "trial: no clear gain")
    ink = m["no_text"]["ink"]
    if ink:
        done = set((entry.get("working_file") or {}).get("ocr_pages") or ())
        already = [p for p in ink if p in done]
        if already:
            reasons.append(f"{len(already)} page(s) without text were already read by the OCR "
                           f"step (no text found): p. {_pages_text(already)}")
        ink = [p for p in ink if p not in done]
    if ink:
        if ran and "ink_text" in trial:
            text_pages, pictures = trial["ink_text"], trial["pictures"]
            if text_pages:
                reasons.append(f"{len(text_pages)} page(s) without text hold text (trial OCR): "
                               f"p. {_pages_text(text_pages)}")
                if action == KEEP_TEXT:
                    action, pages = OCR_MISSING, text_pages
            if pictures:
                reasons.append(f"picture page(s), no text: p. {_pages_text(pictures)}")
        elif action != EXCLUDE:
            reasons.append(f"{len(ink)} page(s) without text but with ink not checked "
                           f"(trial not run): p. {_pages_text(ink[:20])}"
                           + (" ..." if len(ink) > 20 else ""))
            if action == KEEP_TEXT:
                action, reason = REVIEW, "trial not run"
    blank = m["no_text"]["blank"]
    if blank:
        reasons.append(f"{len(blank)} blank page(s) without text")
    if dup and dup.get("keeper") == entry["id"]:
        others = [names.get(x["id"], x["id"]) for x in dup["same_work"]]
        reasons.append("kept in its duplicate group over " + ", ".join(others))
    return {"action": action, "pages": pages, "reason": reason, "duplicate_of": None,
            "reasons": reasons}


# ---------------------------------------------------------------------------- the run
DERIVED_SUFFIXES = ("_split_ocr.pdf", "_ocr.pdf", "_split.pdf")
KINDS = {".pdf": "pdf", ".txt": "txt"}
HASH_CHUNK = 1 << 20


class SurveyError(Exception):
    """A refusal (bad root, catalogue folder inside a root...); meant for the user."""


def sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(HASH_CHUNK):
            h.update(chunk)
    return h.hexdigest()


def walk(root, skip) -> list[Path]:
    """*.pdf and *.txt under ``root`` (recursively, sorted), outside the ``skip`` folders."""
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames
                             if not any(catalogue.under(os.path.join(dirpath, d), s)
                                        for s in skip))
        for name in sorted(filenames):
            if Path(name).suffix.lower() in KINDS:
                out.append(Path(dirpath) / name)
    return out


def find_working(src, derived) -> Path | None:
    """The derived working file of ``src``: <stem>_split_ocr.pdf, then _ocr.pdf, then
    _split.pdf, in the first --derived folder that has one."""
    stem = Path(src).stem
    for suffix in DERIVED_SUFFIXES:
        for d in derived:
            p = Path(d) / f"{stem}{suffix}"
            if p.is_file():
                return p
    return None


def _ocr_pages(working) -> tuple[str | None, list[int]]:
    """(report path, pages the OCR step read) from <working stem>.report.json."""
    report = Path(working).with_suffix(".report.json")
    if not report.is_file():
        return None, []
    try:
        data = json.loads(report.read_text("utf-8"))
        pages = [p["page"] for p in data.get("pages", []) if p.get("status") == ocr.OCR]
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return str(report), []
    return str(report), pages


@dataclass
class SurveyResult:
    folder: Path
    catalogue: dict
    decisions: dict
    scanned: list[str]                 # ids of the entries under the roots of this run
    measured: int = 0
    reused: int = 0
    hashed_bytes: int = 0
    shingles_recomputed: int = 0
    trial_pages: int = 0
    trial_failed: int = 0
    trial_note: str = ""
    trial_reused: int = 0              # entries whose earlier trial still holds
    sheet_bytes: int = 0
    seconds: float = 0.0
    counts: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    written: list[Path] = field(default_factory=list)

    @property
    def unreadable(self) -> list[dict]:
        files = self.catalogue["files"]
        return [files[k] for k in self.scanned
                if files[k]["status"] == catalogue.ERROR
                or (files[k].get("measured") or {}).get("errors")]

    @property
    def exit_code(self) -> int:
        """0 ok; 2 a file could not be read or a trial page failed."""
        return 2 if self.unreadable or self.trial_failed else 0


def _source_layer(m) -> dict:
    keep = ("for", "score_version", "pages", "text_pages", "text_pct", "scan", "dpi",
            "language", "score", "no_text")
    return {k: m[k] for k in keep if k in m}


def _stale(m, for_sha, remeasure) -> bool:
    return (remeasure or not m or m.get("for") != for_sha
            or m.get("score_version") != quality.SCORE_VERSION)


def _measure_entry(e, folder, derived, remeasure, result):
    """Measure (or reuse) one present entry, with its working file; keep its shingles in
    the cache."""
    wf = find_working(e["path"], derived) if e["kind"] == "pdf" else None
    if wf:
        st = wf.stat()
        sha = sha256(wf)
        result.hashed_bytes += st.st_size
        report, ocr_done = _ocr_pages(wf)
        e["working_file"] = {"path": str(wf), "sha256": sha, "size": st.st_size,
                             "mtime": catalogue.stamp(st.st_mtime), "report": report,
                             "ocr_pages": ocr_done}
        target, target_kind = wf, "pdf"
    else:
        e.pop("working_file", None)
        e.pop("source_layer", None)
        sha, target, target_kind = e["id"], Path(e["path"]), e["kind"]
    try:
        if _stale(e.get("measured"), sha, remeasure):
            m, sh = measure(target, target_kind)
            m["for"] = sha
            e["measured"] = m
            e.pop("trial", None)
            catalogue.save_shingles(folder, sha, sh)
            result.measured += 1
        else:
            result.reused += 1
            if not catalogue.cache_path(folder, sha).is_file():
                catalogue.save_shingles(folder, sha, measure(target, target_kind)[1])
                result.shingles_recomputed += 1
        if wf and _stale(e.get("source_layer"), e["id"], remeasure):
            m, _ = measure(e["path"], e["kind"])
            m["for"] = e["id"]
            e["source_layer"] = _source_layer(m)
        e.pop("error", None)
    except MeasureError as err:
        e["status"], e["error"], e["measured"] = catalogue.ERROR, str(err), None
        e.pop("source_layer", None)
        e.pop("trial", None)


def _shingle_sets(files, folder, result) -> dict:
    """Shingles of every present, measured entry (from the cache; recomputed when missing
    and the file is unchanged)."""
    sets = {}
    for k, e in files.items():
        m = e.get("measured")
        if e["status"] != catalogue.PRESENT or not m:
            continue
        sh = catalogue.load_shingles(folder, m["for"])
        if sh is None:
            wf = e.get("working_file")
            target, kind = (wf["path"], "pdf") if wf else (e["path"], e["kind"])
            try:
                if sha256(target) != m["for"]:
                    raise MeasureError("changed")
                sh = measure(target, kind)[1]
            except (OSError, MeasureError):
                result.warnings.append(f"no shingles for {e['path']}: left out of duplicates")
                continue
            catalogue.save_shingles(folder, m["for"], sh)
            result.shingles_recomputed += 1
        sets[k] = sh
    return sets


def run(roots, folder, derived=(), trial=True, remeasure=False, jobs=None,
        tesseract_path=None, progress=None) -> SurveyResult:
    """Survey ``roots`` into the catalogue folder ``folder``. Reads the roots and the
    ``derived`` folders; writes only into ``folder`` (and a temporary folder)."""
    t0 = time.perf_counter()
    roots = [catalogue.norm(r) for r in roots]
    derived = [catalogue.norm(d) for d in derived]
    folder = Path(catalogue.norm(folder))
    for r in roots:
        if not os.path.isdir(r):
            raise SurveyError(f"no such folder: {r}")
        if catalogue.under(folder, r):
            raise SurveyError(f"the catalogue folder {folder} is inside the source folder {r}; "
                              "put it elsewhere (the survey never writes under a source folder)")
    for d in derived:
        if not os.path.isdir(d):
            raise SurveyError(f"no such --derived folder: {d}")
    cat = catalogue.load(folder)
    decisions = catalogue.load_decisions(folder)
    folder.mkdir(parents=True, exist_ok=True)
    when = catalogue.now()

    found = []
    skip = [str(folder), *derived]
    for r in roots:
        for p in walk(r, skip):
            st = p.stat()
            found.append({"sha": sha256(p), "path": str(p), "root": r,
                          "rel": os.path.relpath(p, r), "size": st.st_size,
                          "mtime": catalogue.stamp(st.st_mtime),
                          "kind": KINDS[p.suffix.lower()]})
    result = SurveyResult(folder, cat, decisions, [])
    result.hashed_bytes = sum(f["size"] for f in found)
    result.counts = catalogue.merge(cat, roots, found, when)
    files = cat["files"]
    result.scanned = sorted({f["sha"] for f in found},
                            key=lambda k: (files[k]["root"].lower(), files[k]["rel"].lower()))
    for i, k in enumerate(result.scanned):
        if progress:
            progress(i + 1, len(result.scanned), files[k]["rel"])
        _measure_entry(files[k], folder, derived, remeasure, result)

    users = {}
    for k in result.scanned:
        if wf := files[k].get("working_file"):
            users.setdefault(os.path.normcase(wf["path"]), []).append(files[k]["rel"])
    for path, rels in users.items():
        if len(rels) > 1:
            result.warnings.append(f"{path} is the working file of several sources: "
                                   + ", ".join(rels))

    present = {k: e for k, e in files.items()
               if e["status"] == catalogue.PRESENT and e.get("measured")}
    sets = _shingle_sets(present, folder, result)
    dups = duplicates({k: present[k] for k in sets}, sets)
    for k, e in files.items():
        if e["status"] == catalogue.PRESENT:
            e["duplicates"] = dups.get(k, {"group": None, "same_work": [], "related": [],
                                           "keeper": None})

    run_trials(files, result, trial, jobs, tesseract_path)

    names = {k: e["rel"] for k, e in files.items()}
    for k, e in files.items():
        if e["status"] != catalogue.MISSING:
            e["suggestion"] = suggest(e, e.get("duplicates"), names)

    for r in roots:
        catalogue.set_root(cat, r, when)
    cat.update({"schema": catalogue.SCHEMA, "tool_version": __version__,
                "score_version": quality.SCORE_VERSION, "updated": when})
    result.written.append(catalogue.save(folder, cat))
    sheet, result.sheet_bytes = catalogue.write_sheet(cat, decisions, folder)
    result.written.append(sheet)
    result.seconds = round(time.perf_counter() - t0, 1)
    return result


# ---------------------------------------------------------------------------- trial
def needs_trial(e) -> bool:
    """A present, kept, es/en PDF whose scan layer triggers a rebuild trial or that has
    ink pages without text."""
    m = e.get("measured")
    if e["status"] != catalogue.PRESENT or not m or m["kind"] != "pdf":
        return False
    dup = e.get("duplicates") or {}
    if dup.get("keeper") and dup["keeper"] != e["id"]:
        return False
    if m["language"]["lang"] not in WORK_LANGS:
        return False
    rebuild = m["scan"] and m["text_pages"] and rebuild_triggers(m["score"])
    done = set((e.get("working_file") or {}).get("ocr_pages") or ())
    return bool(rebuild or [p for p in m["no_text"]["ink"] if p not in done])


def trial_pages(m, skip=()) -> list[int]:
    """Up to 8 scored pages with an image: the 4 worst by suspect % and 4 spread evenly
    over the other scored pages. ``skip``: pages whose layer is already this pipeline's
    OCR."""
    rows = [r for r in m["page_scores"]
            if r["pct"] is not None and r.get("image") and r["page"] not in skip]
    worst = {r["page"] for r in sorted(rows, key=lambda r: (-r["pct"], r["page"]))[:TRIAL_WORST]}
    rest = [r["page"] for r in rows if r["page"] not in worst]
    if len(rest) > TRIAL_SPREAD:
        rest = [rest[round(i * (len(rest) - 1) / (TRIAL_SPREAD - 1))] for i in range(TRIAL_SPREAD)]
    return sorted(worst | set(rest))


def with_cap(pct, cap) -> float:
    """suspect % + cap_unknown_pct: what the trial compares (Tesseract's reading of an
    ornament is short foreign and capitalised junk that suspect % does not see)."""
    return pct + (cap or 0.0)


def ink_text(p) -> bool:
    """A trial page without a text layer holds text: enough words, scored, and suspect %
    + cap_unknown_pct below OCR_MISSING_SUSPECT."""
    return (p["trial_words"] >= OCR_MISSING_WORDS and p["trial_pct"] is not None
            and with_cap(p["trial_pct"], p.get("trial_cap")) < OCR_MISSING_SUSPECT)


def verdict(pages) -> dict:
    """rebuild_layer if the trial's median suspect % + cap_unknown_pct is <= 0.6 x the
    layer's on the same pages and at least 1 point lower, over >= 4 pages scored on both
    sides."""
    both = [p for p in pages if p["kind"] == "layer" and p["layer_pct"] is not None
            and p["trial_pct"] is not None]
    if len(both) < TRIAL_MIN_PAGES:
        return {"action": KEEP_TEXT, "pages": len(both), "layer_median": None,
                "trial_median": None,
                "why": f"trial: only {len(both)} page(s) scored on both sides: no clear gain"}
    lm = statistics.median(with_cap(p["layer_pct"], p.get("layer_cap")) for p in both)
    tm = statistics.median(with_cap(p["trial_pct"], p.get("trial_cap")) for p in both)
    gain = tm <= TRIAL_GAIN_RATIO * lm and lm - tm >= TRIAL_GAIN_POINTS
    change = (f"median suspect + cap-unknown {lm:.1f}% -> {tm:.1f}% on {len(both)} "
              "pages")
    return {"action": REBUILD if gain else KEEP_TEXT, "pages": len(both),
            "layer_median": round(lm, 2), "trial_median": round(tm, 2),
            "why": f"trial: {change}" if gain else f"trial: no clear gain ({change})"}


def _recognize(tess, image, outbase, langs, dpi) -> str:
    """tesseract.recognize, tried once more on failure (as the OCR step does)."""
    err = ""
    for _ in range(ocr.ATTEMPTS):
        err = tesseract.recognize(tess, image, outbase, langs, dpi)
        if not err:
            return ""
        for ext in (".pdf", ".tsv"):
            Path(f"{outbase}{ext}").unlink(missing_ok=True)
    return err


def _plan(e) -> list[dict]:
    """The trial pages of an entry: layer pages (if the scan triggers a rebuild trial) and
    ink pages without text."""
    m = e["measured"]
    skip = set((e.get("working_file") or {}).get("ocr_pages") or ())
    rows = {r["page"]: r for r in m["page_scores"]}
    plan = []
    if m["scan"] and m["text_pages"] and rebuild_triggers(m["score"]):
        for p in trial_pages(m, skip):
            r = rows[p]
            plan.append({"page": p, "kind": "layer", "lang": r["lang"], "layer_pct": r["pct"],
                         "layer_cap": r.get("cap_unknown_pct"), "layer_words": r["lower"]})
    for p in m["no_text"]["ink"]:
        if p not in skip:
            plan.append({"page": p, "kind": "ink", "lang": m["language"]["lang"],
                         "layer_pct": None, "layer_words": 0})
    return plan


def _finish_trial(e, pages, tess, langs) -> dict:
    m = e["measured"]
    ok = [p for p in pages if not p.get("error")]
    text_pages = [p["page"] for p in ok if p["kind"] == "ink" and ink_text(p)]
    trial = {"ran": True, "for": m["for"], "score_version": quality.SCORE_VERSION,
             "langs": langs, "tesseract": tess.version, "pages": pages,
             "ink_text": text_pages,
             "pictures": [p["page"] for p in ok if p["kind"] == "ink"
                          and p["page"] not in text_pages],
             "failed": sum(bool(p.get("error")) for p in pages), "verdict": None,
             "note": TRIAL_NOTE}
    if m["scan"] and m["text_pages"] and rebuild_triggers(m["score"]):
        if any(p["kind"] == "layer" for p in pages):
            trial["verdict"] = verdict(pages)
        else:
            trial["verdict"] = {"action": KEEP_TEXT, "pages": 0, "layer_median": None,
                                "trial_median": None,
                                "why": "trial: no pages to try (the scored pages already "
                                       "hold this pipeline's OCR)"}
    return trial


def run_trials(files, result, enabled, jobs, tesseract_path) -> None:
    """Run the rebuild trial (Tesseract on a few pages, in a temporary folder) for every
    entry of this run that needs one and has no valid trial yet."""
    todo = []
    if not enabled:
        result.trial_note = "not run (--no-trial)"
    for k in result.scanned:
        e = files[k]
        if not needs_trial(e):
            e.pop("trial", None)
            continue
        t = e.get("trial") or {}
        if (t.get("ran") and not t.get("failed") and t.get("for") == e["measured"]["for"]
                and t.get("score_version") == quality.SCORE_VERSION):
            result.trial_reused += 1
            continue
        todo.append(e)
    if not todo:
        return
    tess = None
    if enabled:
        try:
            tess = tesseract.probe(tesseract.find(tesseract_path))
        except tesseract.TesseractError as err:
            result.trial_note = f"not run ({err})"
    if tess is None:
        for e in todo:
            e["trial"] = {"ran": False, "note": f"trial {result.trial_note}"}
        return
    jobs = max(1, jobs or (os.cpu_count() or 2) - 1)
    tmp = tempfile.mkdtemp(prefix="gpdf-survey-")
    pool = ThreadPoolExecutor(max_workers=jobs)
    futures = {}
    try:
        plans = []
        for e in todo:
            langs = e["measured"]["language"]["tesseract"]
            try:
                tesseract.check(tess, langs)
            except tesseract.TesseractError as err:
                e["trial"] = {"ran": False, "note": f"trial not run ({err})"}
                continue
            pages = _plan(e)
            plans.append((e, langs, pages))
            wf = e.get("working_file")
            with pymupdf.open(wf["path"] if wf else e["path"]) as doc:
                for p in pages:
                    page = doc[p["page"] - 1]
                    d = ocr.source_dpi(page)
                    if d is None:
                        p["error"] = "no image"
                        continue
                    im, dpi, _, _ = ocr.render(page, d)
                    base = Path(tmp) / f"t{len(futures):05d}"
                    im.save(f"{base}.png", compress_level=1)
                    futures[pool.submit(_recognize, tess, f"{base}.png", base, langs, dpi)] = (
                        p, base)
        for fut in as_completed(list(futures)):
            p, base = futures[fut]
            err = fut.result()
            if err:
                p["error"] = err
                continue
            with pymupdf.open(f"{base}.pdf") as text_pdf:
                text = text_pdf[0].get_text() if len(text_pdf) else ""
            s = quality.score_text(text, p["lang"], p["page"])
            p.update({"trial_pct": s.pct, "trial_cap": s.cap_unknown_pct,
                      "trial_words": s.lower, "trial_class": s.cls, "text": text[:SAMPLE_CHARS]})
            for ext in (".png", ".pdf", ".tsv"):
                Path(f"{base}{ext}").unlink(missing_ok=True)
        for e, langs, pages in plans:
            for p in pages:
                p.setdefault("trial_pct", None)
                p.setdefault("trial_words", 0)
            e["trial"] = _finish_trial(e, pages, tess, langs)
            result.trial_pages += len(pages)
            result.trial_failed += e["trial"]["failed"]
    finally:
        for fut in futures:
            fut.cancel()
        pool.shutdown(wait=True)
        shutil.rmtree(tmp, ignore_errors=True)
