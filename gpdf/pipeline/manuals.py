# SPDX-License-Identifier: AGPL-3.0-or-later
"""gpdf-cli manual-pages: every page of a born-digital equipment manual as faithful text in
reading order, stamped with its PDF page and printed page label, with tables, warnings,
numbered steps, spec lists and figures kept as blocks of their own and figures cropped to
PNG. Nothing is paraphrased or corrected: no word lists, no dictionary.

This is separate from the book pipeline (survey/export/structure/handoff) and shares only
the reading of a page's characters (``textlayout``-style words and lines).

Per page: characters (rawdict; controls and U+FFFD dropped and counted; a soft hyphen at a
line end joins the word) -> running heads and feet (repeated band lines) -> printed page
label (PDF label, else a number in the band, accepted in runs) -> figures (image boxes and
drawing clusters with their callout labels and caption) -> tables (``find_tables``) ->
spec lines (dot leaders) -> reading order (bands at full-width elements; left column then
right) -> blocks (heading, step, bullet, warning, specs, table, figure, para).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import statistics
import time
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pymupdf

from gpdf import __version__

FLAGS = (pymupdf.TEXTFLAGS_RAWDICT & ~pymupdf.TEXT_PRESERVE_LIGATURES
         & ~pymupdf.TEXT_PRESERVE_IMAGES)
BAND = 0.07                 # top/bottom (and side, for lone numbers) share of the page
REPEAT_SHARE = 0.25         # a band line on >= this share of pages is a running head/foot
REPEAT_MIN = 3
RUN_MIN = 3                 # consecutive pages that make a page-number run
GUTTER_LO, GUTTER_HI = 0.3, 0.7
GUTTER_SIDE = 0.25
GUTTER_MIN_LINES = 6
GUTTER_HALF = 6.0           # pt: half the narrowest gutter
IMAGE_MIN_AREA = 2000.0
DENSE_DRAWINGS = 30000      # paths: a page this dense is one vector drawing
DRAWING_MIN_AREA = 4000.0
REGION_JOIN = 6.0           # pt: regions this close are one figure
TABLE_COVER = 0.8           # a region mostly covered by a table is the table
THIN_H, THIN_RATIO = 40.0, 12.0
LABEL_WORDS = 8             # a callout label has at most this many words
CAPTION_DIST = 40.0
CAPTION_RE = re.compile(r"^Fig(?:ure)?\.?\s*[\d.]+")
LEADER_RE = re.compile(r"(?:\s*\.){4,}\s*")
STEP_RE = re.compile(r"^(\d{1,2})[.)](?!\d)\s*(.*)$")
BULLET_RE = re.compile(r"^([•▪■●◦‣◆►➢✓*\-–—])\s+(.*)$")
WARN_WORDS = ("WARNING", "CAUTION", "DANGER", "NOTICE", "NOTE", "IMPORTANT")
WARN_RE = re.compile(r"^(" + "|".join(WARN_WORDS) + r")(?:\s*[!:]|\s*[–—-]\s|\s|$)\s*(.*)$")
WARN_TITLE_RE = re.compile(r"^(" + "|".join(w.capitalize() for w in WARN_WORDS) + r")\s*[!:]\s*(.*)$")
GLUE_RE = re.compile(r"[a-z]{3,}[A-Z][a-z]{2,}")
HEADING_SIZE = 1.15
HEADING_CHARS = 90
GAP_PITCH = 1.5
CAPTION_CONT_WORDS = 12
LABEL_PATTERNS = [
    ("a", re.compile(r"^(\d{1,4})$")),
    ("s", re.compile(r"^(\d(?: \d){1,3})$")),              # letter-spaced "8 0"
    ("a", re.compile(r"^-\s*(\d{1,4})\s*-$")),
    ("a", re.compile(r"^Page\s+(\d{1,4})$", re.I)),
    ("a", re.compile(r"^[\w\- ]{2,40}\bPage\s+(\d{1,4})$")),
    ("c", re.compile(r"^(\d{1,2})-(\d{1,3})$")),
    ("r", re.compile(r"^([ivxlcdm]{1,8})$", re.I)),
]
ROMAN = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}


class ManualsError(Exception):
    pass


# ---------------------------------------------------------------------------- words, lines
@dataclass
class Word:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    size: float
    base: float
    bold: bool


@dataclass
class Line:
    words: list[Word]
    soft_end: bool = False      # ended with a soft hyphen
    col: int | None = 0         # 0 left / only, 1 right, None full width
    seq: int = 0                # content order

    @property
    def x0(self):
        return min(w.x0 for w in self.words)

    @property
    def x1(self):
        return max(w.x1 for w in self.words)

    @property
    def y0(self):
        return min(w.y0 for w in self.words)

    @property
    def y1(self):
        return max(w.y1 for w in self.words)

    @property
    def height(self):
        return self.y1 - self.y0

    @property
    def base(self):
        return statistics.median(w.base for w in self.words)

    @property
    def size(self):
        sizes = sorted((w.size, max(1, len(w.text))) for w in self.words)
        half, acc = sum(n for _, n in sizes) / 2, 0
        for s, n in sizes:
            acc += n
            if acc >= half:
                return s
        return sizes[-1][0]

    @property
    def bold(self):
        return all(w.bold for w in self.words)

    @property
    def text(self):
        t = " ".join(w.text for w in self.words)
        return t + "­" if self.soft_end else t

    @property
    def nwords(self):
        return len(self.words)

    @property
    def bbox(self):
        return [round(self.x0, 1), round(self.y0, 1), round(self.x1, 1), round(self.y1, 1)]

    def rect(self):
        return pymupdf.Rect(self.x0, self.y0, self.x1, self.y1)


def _dropped(c: str) -> bool:
    """A character with no Unicode meaning: C0/C1 controls (but not whitespace), private
    use, unassigned, U+FFFD. Such glyphs are key icons and safety symbols in the manuals."""
    if c == "�":
        return True
    return unicodedata.category(c) in ("Cc", "Cn", "Co")


def read_lines(page) -> tuple[list[Line], int]:
    """The page's text runs as PyMuPDF groups them (one per baseline run), cleaned: words
    split at whitespace, control glyphs dropped and counted, a soft hyphen kept only at the
    end of a run. Non-horizontal runs are left out."""
    d = page.get_text("rawdict", flags=FLAGS)
    out, dropped, seq = [], 0, 0
    for block in d["blocks"]:
        if block.get("type") != 0:
            continue
        for ln in block["lines"]:
            if abs(ln["dir"][1]) > 0.1 or ln["dir"][0] < 0:
                continue
            words, cur, soft = [], [], False

            def flush():
                nonlocal cur
                if cur:
                    words.append(Word("".join(c for c, _, _ in cur),
                                      min(b[0] for _, b, _ in cur), min(b[1] for _, b, _ in cur),
                                      max(b[2] for _, b, _ in cur), max(b[3] for _, b, _ in cur),
                                      statistics.median(s[0] for _, _, s in cur),
                                      statistics.median(s[1] for _, _, s in cur),
                                      all(s[2] for _, _, s in cur)))
                    cur = []

            for span in ln["spans"]:
                font = span.get("font", "").lower()
                bold = bool(span["flags"] & 16) or "bold" in font or "black" in font \
                    or "heavy" in font
                for ch in span["chars"]:
                    c = ch["c"]
                    if c.isspace():
                        flush()
                        soft = False
                        continue
                    if c == "­":
                        soft = True
                        continue
                    if _dropped(c):
                        dropped += 1
                        continue
                    soft = False
                    cur.append((c, ch["bbox"], (span["size"], ch["origin"][1], bold)))
            flush()
            if words:
                out.append(Line(words, soft_end=soft, seq=seq))
                seq += 1
    return out, dropped


def _same_base(a: Line, b: Line) -> bool:
    h = min(a.height, b.height) or 1.0
    return abs(a.base - b.base) < 0.5 * h


def resolve_overlaps(lines: list[Line]) -> tuple[list[Line], list[dict]]:
    """Two runs on the same baseline whose boxes overlap by > 30% of the shorter one: a
    hidden span under body text. A figure caption under body text is dropped (the body is
    kept), a duplicate is dropped; otherwise both stay. Each case is warned."""
    warnings, dropped = [], set()
    by_base = sorted(lines, key=lambda l: l.base)
    for i, a in enumerate(by_base):
        for b in by_base[i + 1:]:
            if b.base - a.base > 0.5 * max(a.height, b.height):
                break
            if not _same_base(a, b) or id(a) in dropped or id(b) in dropped:
                continue
            over = min(a.x1, b.x1) - max(a.x0, b.x0)
            if over <= 0.3 * min(a.x1 - a.x0, b.x1 - b.x0):
                continue
            ca, cb = bool(CAPTION_RE.match(a.text)), bool(CAPTION_RE.match(b.text))
            if ca and not cb:
                keep, drop = b, a
            elif cb and not ca:
                keep, drop = a, b
            elif a.text == b.text:
                keep, drop = (b, a) if b.seq > a.seq else (a, b)
            else:
                # neither is a caption: both stay, the reader is warned
                warnings.append({"kind": "overlap", "kept": a.text, "also": b.text,
                                 "bbox": a.bbox})
                continue
            dropped.add(id(drop))
            kind = "duplicate" if keep.text == drop.text else "overlap"
            warnings.append({"kind": kind, "kept": keep.text, "dropped": drop.text,
                             "bbox": keep.bbox})
    return [l for l in lines if id(l) not in dropped], warnings


def merge_fragments(lines: list[Line], regions: list[pymupdf.Rect] = ()) -> list[Line]:
    """Runs on the same baseline and in the same column joined into one line (a step
    number set in a span of its own, "Tools Needed:" and "Qty"). Inside a figure region
    runs stay apart unless they nearly touch: they are callout labels."""
    def inside(ln):
        c = pymupdf.Point((ln.x0 + ln.x1) / 2, (ln.y0 + ln.y1) / 2)
        return any(r.contains(c) for r in regions)

    out: list[Line] = []
    for ln in sorted(lines, key=lambda l: (l.base, l.x0)):
        for o in reversed(out[-6:]):
            if o.col == ln.col and _same_base(o, ln) and not (
                    (inside(ln) or inside(o)) and max(ln.x0 - o.x1, o.x0 - ln.x1) > 1.5 * ln.size):
                o.words = sorted(o.words + ln.words, key=lambda w: w.x0)
                o.soft_end = ln.soft_end if ln.x0 > o.x0 else o.soft_end
                o.seq = min(o.seq, ln.seq)
                break
        else:
            out.append(Line(list(ln.words), ln.soft_end, ln.col, ln.seq))
    return out


def body_size(lines: list[Line]) -> float:
    sizes = sorted((w.size, max(1, len(w.text))) for ln in lines for w in ln.words)
    if not sizes:
        return 10.0
    half, acc = sum(n for _, n in sizes) / 2, 0
    for s, n in sizes:
        acc += n
        if acc >= half:
            return s
    return sizes[-1][0]


def line_pitch(lines: list[Line]) -> float:
    """The baseline distance inside a paragraph: the median of the small baseline steps
    between consecutive lines of a column."""
    heights = sorted(l.height for l in lines)
    h = heights[len(heights) // 2] if heights else 10.0
    steps = []
    for col in (0, 1, None):
        col_lines = sorted((l for l in lines if l.col == col), key=lambda l: l.base)
        for a, b in zip(col_lines, col_lines[1:]):
            d = b.base - a.base
            if 0.5 * h < d < 2.2 * h:
                steps.append(d)
    return statistics.median(steps) if steps else 1.25 * h


# ---------------------------------------------------------------------------- columns
def find_gutter(lines: list[Line], width: float) -> float | None:
    """The x of a two-column gutter: a strip in the middle of the page crossed by few
    lines (full-width headings), with >= GUTTER_SIDE of the lines entirely on each side."""
    if len(lines) < 2 * GUTTER_MIN_LINES:
        return None
    best, xs = None, []
    for x in range(int(GUTTER_LO * width), int(GUTTER_HI * width) + 1, 2):
        left = sum(ln.x1 <= x - GUTTER_HALF for ln in lines)
        right = sum(ln.x0 >= x + GUTTER_HALF for ln in lines)
        cross = len(lines) - left - right
        if min(left, right) < max(GUTTER_MIN_LINES, GUTTER_SIDE * (left + right)):
            continue
        if cross > max(2, 0.15 * len(lines)):
            continue
        score = (cross, -min(left, right))
        if best is None or score < best:
            best, xs = score, [x]
        elif score == best:
            xs.append(x)
    return None if best is None else float(xs[len(xs) // 2])


def _col_of(x0: float, x1: float, gutter: float | None) -> int | None:
    if gutter is None:
        return 0
    if x1 <= gutter + GUTTER_HALF and x0 < gutter:
        return 0
    if x0 >= gutter - GUTTER_HALF:
        return 1
    return None


# ---------------------------------------------------------------------------- labels
def decode_label(raw: str | None) -> str | None:
    """A PDF page label; an InDesign hex UTF-16 prefix ("<FEFF0053006500630031003A>1" =
    "Sec1:1") is decoded and dropped, keeping the number."""
    if not raw:
        return None
    m = re.match(r"^<([0-9A-Fa-f]+)>(.*)$", raw)
    if m:
        rest = m.group(2)
        if rest:
            return rest
        try:
            return bytes.fromhex(m.group(1)).decode("utf-16") or None
        except (UnicodeDecodeError, ValueError):
            return None
    return raw


def roman_to_int(s: str) -> int | None:
    s = s.lower()
    total, prev = 0, 0
    for c in reversed(s):
        v = ROMAN.get(c)
        if v is None:
            return None
        total += -v if v < prev else v
        prev = max(prev, v)
    return total or None


def int_to_roman(n: int) -> str:
    out, vals = [], [(1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"),
                     (50, "l"), (40, "xl"), (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i")]
    for v, r in vals:
        while n >= v:
            out.append(r)
            n -= v
    return "".join(out)


def label_candidates(text: str) -> list[tuple]:
    """(style, value, chapter) readings of a band line as a page number."""
    t = text.strip()
    out = []
    for style, rx in LABEL_PATTERNS:
        m = rx.match(t)
        if not m:
            continue
        if style == "a":
            out.append(("a", int(m.group(1)), None))
        elif style == "s":
            out.append(("a", int(m.group(1).replace(" ", "")), None))
        elif style == "c":
            out.append(("c", int(m.group(2)), int(m.group(1))))
        else:
            v = roman_to_int(m.group(1))
            if v:
                out.append(("r", v, None))
    return out


def _format_label(style, value, chapter) -> str:
    if style == "r":
        return int_to_roman(value)
    if style == "c":
        return f"{chapter}-{value}"
    return str(value)


def assign_labels(page_count: int, cands: dict[int, list[tuple]],
                  pdf_labels: dict[int, str]) -> dict[int, tuple[str | None, str]]:
    """Per page index: (label, source). The PDF's own label wins; else a band number that
    belongs to a run (label minus index constant on >= RUN_MIN pages, single gaps filled
    and marked inferred); else none."""
    keyed: dict[tuple, set[int]] = defaultdict(set)
    for idx, cs in cands.items():
        for style, value, chapter in cs:
            keyed[(style, chapter, value - idx)].add(idx)
    accepted: dict[int, tuple[int, str, str]] = {}     # idx -> (run length, label, source)
    for (style, chapter, off), idxs in keyed.items():
        ordered = sorted(idxs)
        stretch = [ordered[0]]
        stretches = []
        for i in ordered[1:]:
            if i - stretch[-1] <= 2:
                stretch.append(i)
            else:
                stretches.append(stretch)
                stretch = [i]
        stretches.append(stretch)
        for st in stretches:
            if len(st) < RUN_MIN:
                continue
            for i in range(st[0], st[-1] + 1):
                src = "text" if i in idxs else "inferred"
                cur = accepted.get(i)
                if cur is None or len(st) > cur[0] or (len(st) == cur[0] and src == "text"):
                    accepted[i] = (len(st), _format_label(style, i + off, chapter), src)
    out = {}
    for i in range(page_count):
        lab = decode_label(pdf_labels.get(i))
        if lab:
            out[i] = (lab, "pdf_label")
        elif i in accepted:
            out[i] = (accepted[i][1], accepted[i][2])
        else:
            out[i] = (None, "none")
    return out


# ---------------------------------------------------------------------------- analysis
@dataclass
class PageText:
    lines: list[Line]
    dropped: int
    width: float
    height: float
    rotation: int


@dataclass
class Analysis:
    pages: dict[int, PageText]
    stripped_keys: set[str]
    stripped_slots: set[tuple]
    masked_counts: Counter
    labels: dict[int, tuple[str | None, str]]


def _mask(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"\d", "#", text.strip()))


def _wordy(text: str) -> bool:
    """A band line with a letter in it (a running head, not a bare page number)."""
    return any(c.isalpha() for c in text)


def in_band(ln: Line, width: float, height: float) -> bool:
    return ln.y1 <= BAND * height or ln.y0 >= (1 - BAND) * height


def in_side(ln: Line, width: float, height: float) -> bool:
    return ln.x1 <= BAND * width or ln.x0 >= (1 - BAND) * width


def _slot(ln: Line, height: float) -> tuple:
    return ("top" if ln.y1 <= BAND * height else "bottom", round(ln.y0 / 4))


def analyse(doc, slug: str = "") -> Analysis:
    """Pass 1 over every page: lines, repeated band lines, page labels."""
    pages: dict[int, PageText] = {}
    masked = Counter()
    slot_pages: dict[tuple, set[int]] = defaultdict(set)
    cands: dict[int, list[tuple]] = {}
    pdf_labels: dict[int, str] = {}
    for pno in range(doc.page_count):
        page = doc[pno]
        lines, dropped = read_lines(page)
        W, H = page.rect.width, page.rect.height
        pages[pno] = PageText(lines, dropped, W, H, page.rotation)
        raw = page.get_label()
        if raw:
            pdf_labels[pno] = raw
        seen = set()
        cs = []
        for ln in lines:
            if in_band(ln, W, H):
                k = _mask(ln.text)
                if _wordy(ln.text) and k not in seen:
                    masked[k] += 1
                    seen.add(k)
                cs.extend(label_candidates(ln.text))
            elif in_side(ln, W, H) and ln.nwords == 1:
                cs.extend(c for c in label_candidates(ln.text) if c[0] in ("a", "r"))
        cands[pno] = cs
    n = doc.page_count
    threshold = max(REPEAT_MIN, REPEAT_SHARE * n)
    stripped_keys = {k for k, c in masked.items() if c >= threshold}
    for pno, pt in pages.items():
        for ln in pt.lines:
            if in_band(ln, pt.width, pt.height) and _mask(ln.text) in stripped_keys:
                slot_pages[_slot(ln, pt.height)].add(pno)
    stripped_slots = {s for s, ps in slot_pages.items() if len(ps) >= threshold}
    labels = assign_labels(n, cands, pdf_labels)
    return Analysis(pages, stripped_keys, stripped_slots, masked, labels)


# ---------------------------------------------------------------------------- figures
@dataclass
class Figure:
    rect: pymupdf.Rect
    labels: list[Line] = field(default_factory=list)
    caption: list[Line] = field(default_factory=list)
    col: int | None = 0
    file: str = ""

    @property
    def y0(self):
        return self.rect.y0

    @property
    def x0(self):
        return self.rect.x0


@dataclass
class Table:
    rect: pymupdf.Rect
    rows: int
    cols: int
    markdown: str
    col: int | None = 0

    @property
    def y0(self):
        return self.rect.y0

    @property
    def x0(self):
        return self.rect.x0


def _near(a: pymupdf.Rect, b: pymupdf.Rect, d: float) -> bool:
    return not (a.x1 + d < b.x0 or b.x1 + d < a.x0 or a.y1 + d < b.y0 or b.y1 + d < a.y0)


def merge_regions(rects: list[pymupdf.Rect]) -> list[pymupdf.Rect]:
    rects = [pymupdf.Rect(r) for r in rects]
    changed = True
    while changed:
        changed = False
        out: list[pymupdf.Rect] = []
        for r in rects:
            for o in out:
                if _near(o, r, REGION_JOIN):
                    o.include_rect(r)
                    changed = True
                    break
            else:
                out.append(r)
        rects = out
    return rects


def dense_drawing(page) -> pymupdf.Rect | None:
    """The union box of the page's vector paths when there are DENSE_DRAWINGS or more
    (a wiring diagram, a parts explosion): clustering and table search would take minutes
    there, and the whole thing is one figure. None otherwise."""
    try:
        paths = page.get_cdrawings()
    except Exception:  # noqa: BLE001
        return None
    if len(paths) < DENSE_DRAWINGS:
        return None
    r = pymupdf.Rect()
    for pth in paths:
        r.include_rect(pymupdf.Rect(pth["rect"]))
    r &= page.rect
    return r if r.is_valid and not r.is_empty else None


def figure_regions(page, tables: list[Table], lines: list[Line],
                   dense: pymupdf.Rect | None = None) -> list[pymupdf.Rect]:
    rects = []
    for im in page.get_image_info():
        r = pymupdf.Rect(im["bbox"]) & page.rect
        if r.is_valid and not r.is_empty and r.get_area() > IMAGE_MIN_AREA:
            rects.append(r)
    if dense is not None:
        clusters = [dense]
    else:
        try:
            clusters = page.cluster_drawings()
        except Exception:  # noqa: BLE001 - odd drawings must not stop the page
            clusters = []
    for r in clusters:
        r = pymupdf.Rect(r) & page.rect
        if r.is_valid and not r.is_empty and r.get_area() > DRAWING_MIN_AREA:
            rects.append(r)
    out = []
    for r in merge_regions(rects):
        if r.height < THIN_H or r.width / max(r.height, 1e-6) > THIN_RATIO:
            continue
        covered = sum((r & t.rect).get_area() for t in tables if (r & t.rect).is_valid)
        if covered > TABLE_COVER * r.get_area():
            continue
        long_inside = sum(1 for ln in lines if ln.nwords > LABEL_WORDS
                          and r.contains(pymupdf.Point((ln.x0 + ln.x1) / 2, (ln.y0 + ln.y1) / 2)))
        if long_inside >= 2 and r != dense:
            continue              # a box drawn around text, not a figure
        out.append(r)
    out.sort(key=lambda r: (r.y0, r.x0))
    return out


def _adjacent_long(ln: Line, lines: list[Line]) -> bool:
    for o in lines:
        if o is ln or o.nwords <= LABEL_WORDS or o.col != ln.col:
            continue
        if abs(o.base - ln.base) <= 1.6 * max(ln.height, o.height) and o.x0 < ln.x1 and o.x1 > ln.x0:
            return True
    return False


def _caption_of(f: Figure, lines: list[Line], taken: set) -> Line | None:
    best = None
    for ln in lines:
        if id(ln) in taken or not CAPTION_RE.match(ln.text):
            continue
        if ln.x1 <= f.rect.x0 or ln.x0 >= f.rect.x1:
            continue
        if (f.rect.y0 - ln.y1 > CAPTION_DIST) or (ln.y0 - f.rect.y1 > CAPTION_DIST):
            continue
        d = max(f.rect.y0 - ln.y1, ln.y0 - f.rect.y1, 0.0)
        if best is None or d < best[0]:
            best = (d, ln)
    return None if best is None else best[1]


def attach_labels_and_captions(figs: list[Figure], lines: list[Line]) -> list[Line]:
    """The figure's caption (the nearest "Figure n" line within CAPTION_DIST, with its
    continuation lines) and its callout labels (short lines inside or touching the figure)
    leave the text flow and go with the figure. Returns the remaining lines."""
    taken = set()
    by_pos = sorted(lines, key=lambda l: (l.col if l.col is not None else -1, l.base))
    for f in figs:
        cap = _caption_of(f, lines, taken)
        if cap is None:
            continue
        f.caption = [cap]
        taken.add(id(cap))
        prev = cap
        for nxt in by_pos[by_pos.index(cap) + 1:]:
            if nxt.col != cap.col or id(nxt) in taken:
                break
            if (nxt.base - prev.base > 1.3 * max(prev.height, nxt.height) or nxt.nwords > CAPTION_CONT_WORDS
                    or nxt.x0 < cap.x0 - 4 or nxt.x1 > cap.x1 + 4):
                break
            f.caption.append(nxt)
            taken.add(id(nxt))
            prev = nxt
    for ln in lines:
        if id(ln) in taken or ln.nwords > LABEL_WORDS or CAPTION_RE.match(ln.text):
            continue
        c = pymupdf.Point((ln.x0 + ln.x1) / 2, (ln.y0 + ln.y1) / 2)
        for f in figs:
            inter = f.rect & ln.rect()
            touching = inter.is_valid and inter.width >= 2 and inter.height >= 2
            if f.rect.contains(c) or (touching and not _adjacent_long(ln, lines)):
                f.labels.append(ln)
                taken.add(id(ln))
                break
    for f in figs:
        f.labels = _stack_labels(sorted(f.labels, key=lambda l: (l.y0, l.x0)))
    return [l for l in lines if id(l) not in taken]


def _stack_labels(labels: list[Line]) -> list[Line]:
    """Label lines stacked one under the other ("Inside" / "Set") joined into one label:
    the next line sits just below the last line of the stack and is centred on it."""
    groups: list[list[Line]] = []
    for ln in labels:
        for g in groups:
            last = g[-1]
            cx, lcx = (ln.x0 + ln.x1) / 2, (last.x0 + last.x1) / 2
            if (0 < ln.base - last.base <= 1.4 * max(last.height, ln.height)
                    and (last.x0 - 2 <= cx <= last.x1 + 2 or ln.x0 - 2 <= lcx <= ln.x1 + 2)):
                g.append(ln)
                break
        else:
            groups.append([ln])
    out = []
    for g in groups:
        out.append(Line([w for l in g for w in l.words], g[-1].soft_end, g[0].col, g[0].seq))
    return out


def find_tables(page, height: float) -> list[Table]:
    out = []
    if hasattr(pymupdf, "no_recommend_layout"):
        pymupdf.no_recommend_layout()
    try:
        found = page.find_tables()
    except Exception:  # noqa: BLE001 - a page find_tables cannot read is a page without
        return out
    for t in found.tables:
        if t.row_count < 2 or t.col_count < 2:
            continue
        r = pymupdf.Rect(t.bbox)
        if r.y0 >= (1 - BAND) * height or r.y1 <= BAND * height:
            continue
        try:
            md = t.to_markdown().strip()
        except Exception:  # noqa: BLE001
            md = ""
        if not md:
            continue
        out.append(Table(r, t.row_count, t.col_count, md))
    return out


# ---------------------------------------------------------------------------- text, blocks
def join_lines(texts: list[str]) -> str:
    """Lines into one text: a space between lines; a soft hyphen at a line end joins the
    word; a hyphen at a line end joins without a space only when the next line starts
    lowercase. Soft hyphens inside a line are dropped."""
    out, pending_soft = "", False
    for i, t in enumerate(texts):
        t = t.rstrip()
        soft = t.endswith("­")
        t = t.replace("­", "")
        if i == 0:
            out = t
        elif pending_soft:
            out += t
        elif out.endswith("-") and t[:1].islower():
            out = out[:-1] + t
        else:
            out = f"{out} {t}" if out else t
        pending_soft = soft
    return out


def spec_item(text: str) -> tuple[str, list[str]] | None:
    t = text.replace("­", "").strip()
    if not LEADER_RE.search(t):
        return None
    parts = LEADER_RE.split(t)
    label = parts[0].strip()
    values = [p.strip() for p in parts[1:] if p.strip()]
    if not label or not values or len(label) > 120:
        return None
    return label, values


def _kind(ln: Line, body: float) -> tuple:
    t = ln.text.replace("­", "")
    m = WARN_RE.match(t) or WARN_TITLE_RE.match(t)
    if m:
        return ("warning", m.group(1).upper(), t)
    sp = spec_item(t)
    if sp:
        return ("spec",) + sp
    if ln.size >= HEADING_SIZE * body and len(t) <= HEADING_CHARS:
        return ("heading", t)
    m = STEP_RE.match(t)
    if m:
        return ("step", m.group(1), m.group(2))
    m = BULLET_RE.match(t)
    if m:
        return ("bullet", m.group(1), m.group(2))
    if ln.bold and len(t) <= HEADING_CHARS:
        return ("bold", t)
    return ("para", t)


def _bbox_union(lines) -> list[float]:
    r = pymupdf.Rect(lines[0].bbox)
    for ln in lines[1:]:
        r.include_rect(pymupdf.Rect(ln.bbox))
    return [round(v, 1) for v in (r.x0, r.y0, r.x1, r.y1)]


def _specs_markdown(items: list[dict]) -> str:
    width = max(len(it["values"]) for it in items)
    head = ["Item", "Value"] + [f"Value {i}" for i in range(2, width + 1)]
    rows = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for it in items:
        vals = it["values"] + [""] * (width - len(it["values"]))
        rows.append("| " + " | ".join([it["label"]] + vals) + " |")
    return "\n".join(rows)


class _Builder:
    """Lines (in reading order, with figures and tables interleaved) into blocks."""

    def __init__(self, body: float, pitch: float, fig_dir: str):
        self.body, self.pitch, self.fig_dir = body, pitch, fig_dir
        self.blocks: list[dict] = []
        self.cur: dict | None = None
        self.cur_lines: list[Line] = []
        self.pending_step: tuple | None = None

    def flush(self):
        if self.cur is not None:
            b = self.cur
            if b["kind"] == "specs":
                b["text"] = _specs_markdown(b["items"])
            else:
                b["text"] = join_lines([t for t in b.pop("_texts")])
            b["bbox"] = _bbox_union(self.cur_lines)
            self.blocks.append(b)
        self.cur, self.cur_lines = None, []

    def start(self, kind: str, ln: Line, text: str, **extra):
        self.flush()
        self.cur = {"kind": kind, **extra, "_texts": [text]}
        self.cur_lines = [ln]
        self.cur["_x0"] = ln.x0
        self.cur["_textx0"] = self._text_x0(ln, text)

    @staticmethod
    def _text_x0(ln: Line, text: str) -> float:
        """Where the text proper starts on the line (after a step number or bullet)."""
        full = ln.text.replace("­", "")
        if text and full.endswith(text) and len(full) > len(text):
            head = full[: len(full) - len(text)].split()
            k = len(head)
            if 0 < k < len(ln.words):
                return ln.words[k].x0
        return ln.x0

    def add_element(self, block: dict):
        self.flush()
        self.pending_step = None
        self.blocks.append(block)

    def _gap(self, ln: Line) -> float:
        return ln.base - self.cur_lines[-1].base if self.cur_lines else 0.0

    def add(self, ln: Line):
        kind = _kind(ln, self.body)
        k = kind[0]
        text = ln.text
        # a step number alone on its line: the next line is its text
        if self.pending_step is not None:
            n, numline = self.pending_step
            self.pending_step = None
            if (ln.col == numline.col and 0 <= ln.base - numline.base <= 1.6 * self.pitch
                    and k in ("para", "bold", "heading")):
                self.cur = {"kind": "step", "n": n, "_texts": [text]}
                self.cur_lines = [numline, ln]
                self.cur["_x0"] = numline.x0
                self.cur["_textx0"] = ln.x0
                return
            self.start("para", numline, numline.text)
            self.flush()
        cur = self.cur
        gap_ok = cur is not None and self._gap(ln) <= GAP_PITCH * self.pitch and ln.col == self.cur_lines[-1].col
        if cur is not None and cur["kind"] == "warning" and gap_ok and k in ("para", "bold", "bullet"):
            cur["_texts"].append(text)
            self.cur_lines.append(ln)
            return
        if k == "warning":
            self.start("warning", ln, text, level=kind[1])
            return
        if k == "spec":
            item = {"label": kind[1], "values": kind[2]}
            if cur is not None and cur["kind"] == "specs":
                cur["items"].append(item)
                self.cur_lines.append(ln)
                return
            self.flush()
            self.cur = {"kind": "specs", "items": [item]}
            self.cur_lines = [ln]
            return
        if k == "heading":
            if (cur is not None and cur["kind"] == "heading"
                    and ln.col == self.cur_lines[-1].col
                    and self._gap(ln) <= GAP_PITCH * max(self.pitch, self.cur_lines[-1].height)
                    and abs(self.cur_lines[-1].size - ln.size) < 0.5):
                cur["_texts"].append(text)
                self.cur_lines.append(ln)
                return
            self.start("heading", ln, text)
            return
        if k == "step":
            n, rest = kind[1], kind[2]
            if not rest.strip():
                self.flush()
                self.pending_step = (n, ln)
                return
            self.start("step", ln, rest, n=n)
            return
        if k == "bullet":
            self.start("bullet", ln, kind[2], marker=kind[1])
            return
        if k == "bold":
            # a bold short line on its own is a heading; bold lines in a bold run are text
            if cur is not None and cur["kind"] in ("para", "step", "bullet") and gap_ok and self.cur_lines[-1].bold:
                self._continue(ln, text)
                return
            self.start("bold", ln, text)
            return
        # para
        if cur is not None and cur["kind"] in ("para", "step", "bullet", "bold") and gap_ok:
            if cur["kind"] in ("step", "bullet") and ln.x0 < cur["_textx0"] - 3:
                self.start("para", ln, text)
                return
            if cur["kind"] == "bold":
                if self.cur_lines[-1].bold and ln.bold:
                    self._continue(ln, text)
                    return
                self.cur["kind"] = "heading"
                self.start("para", ln, text)
                return
            self._continue(ln, text)
            return
        self.start("para", ln, text)

    def _continue(self, ln: Line, text: str):
        self.cur["_texts"].append(text)
        self.cur_lines.append(ln)

    def finish(self) -> list[dict]:
        if self.pending_step is not None:
            n, numline = self.pending_step
            self.pending_step = None
            self.start("para", numline, numline.text)
        self.flush()
        for b in self.blocks:
            if b["kind"] == "bold":
                b["kind"] = "heading"
            b.pop("_x0", None)
            b.pop("_textx0", None)
        return self.blocks


def _contents_page(blocks: list[dict]) -> bool:
    """A specs page whose values are page numbers in rising order is a contents page."""
    nums, total = [], 0
    for b in blocks:
        if b["kind"] != "specs":
            continue
        for it in b["items"]:
            total += 1
            m = re.match(r"^(\d{1,4})(?:-\d{1,4})?$", it["values"][0])
            if m:
                nums.append(int(m.group(1)))
    if total < 4 or len(nums) < 0.8 * total or len(set(nums)) < 3:
        return False
    return all(a <= b for a, b in zip(nums, nums[1:]))


# ---------------------------------------------------------------------------- a page
def build_page(doc, pno: int, an: Analysis, slug: str, fig_dir: Path | None, dpi: int) -> dict:
    page = doc[pno]
    pt = an.pages[pno]
    W, H = pt.width, pt.height
    warnings: list[dict] = []
    if pt.rotation:
        warnings.append({"kind": "rotated", "rotation": pt.rotation})
    lines, over = resolve_overlaps(list(pt.lines))
    warnings.extend(over)
    label, source = an.labels[pno]
    # running heads and feet, and the page number line
    stripped, flow, label_lines = [], [], []
    band_lines = [ln for ln in lines if in_band(ln, W, H)]
    strip_ids = set()
    for ln in band_lines:
        if _mask(ln.text) in an.stripped_keys:
            strip_ids.add(id(ln))
    for ln in band_lines:
        if id(ln) in strip_ids:
            continue
        k = _mask(ln.text)
        if not _wordy(ln.text):
            continue
        if _slot(ln, H) in an.stripped_slots and an.masked_counts.get(k, 0) >= 2:
            strip_ids.add(id(ln))
            continue
        if an.masked_counts.get(k, 0) >= REPEAT_MIN and any(
                abs(o.base - ln.base) <= 3 for o in band_lines if id(o) in strip_ids):
            strip_ids.add(id(ln))
    for ln in lines:
        if id(ln) in strip_ids:
            stripped.append(ln.text)
            continue
        if source == "text" and label is not None and (in_band(ln, W, H) or (in_side(ln, W, H) and ln.nwords == 1)):
            if any(_format_label(*c) == label or (c[0] == "r" and int_to_roman(c[1]) == label.lower())
                   for c in label_candidates(ln.text)):
                label_lines.append(ln)
                continue
        flow.append(ln)
    if source == "text" and len(label_lines) > 1:
        # the same number printed twice on the page: keep one out of the flow, both recorded
        pass
    # columns on the raw runs (band lines left out), then runs merged into lines
    gutter = find_gutter(flow, W)
    for ln in flow:
        ln.col = _col_of(ln.x0, ln.x1, gutter)
    # tables, figure regions (from the raw runs), then runs merged into lines
    dense = dense_drawing(page)
    if dense is not None:
        warnings.append({"kind": "dense_drawing", "bbox": [round(v, 1) for v in dense]})
    tables = [] if dense is not None else find_tables(page, H)
    for t in tables:
        t.col = _col_of(t.rect.x0, t.rect.x1, gutter)
    regions = figure_regions(page, tables, flow, dense)
    flow = merge_fragments(flow, regions)
    for ln in flow:
        ln.col = _col_of(ln.x0, ln.x1, gutter)
    if tables:
        keep = []
        for ln in flow:
            c = pymupdf.Point((ln.x0 + ln.x1) / 2, (ln.y0 + ln.y1) / 2)
            if any((t.rect + (-1, -1, 1, 1)).contains(c) for t in tables):
                continue
            keep.append(ln)
        flow = keep
    figs = [Figure(r) for r in regions]
    for f in figs:
        f.col = _col_of(f.rect.x0, f.rect.x1, gutter)
    flow = attach_labels_and_captions(figs, flow)
    for ln in flow:
        if CAPTION_RE.match(ln.text) and GLUE_RE.search(ln.text):
            warnings.append({"kind": "glued_text", "text": ln.text.replace("­", ""), "bbox": ln.bbox})
    # reading order: bands at full-width elements; in a band left column then right
    elements: list = list(flow) + figs + tables
    elements.sort(key=lambda e: (e.y0, e.x0))
    ordered: list = []
    band: list = []

    def close_band():
        band.sort(key=lambda e: (0 if e.col == 0 else 1, e.y0))
        ordered.extend(band)
        band.clear()

    for e in elements:
        if e.col is None and gutter is not None:
            close_band()
            ordered.append(e)
        else:
            band.append(e)
    close_band()
    if fig_dir is not None and figs:
        fig_dir.mkdir(parents=True, exist_ok=True)
    for k, f in enumerate((e for e in ordered if isinstance(e, Figure)), 1):
        f.file = f"{slug}_pdf{pno + 1:03d}_f{k}.png"
        if fig_dir is not None:
            clip = (f.rect + (-4, -4, 4, 4)) & page.rect
            pix = page.get_pixmap(dpi=dpi, clip=clip)
            tmp = fig_dir / (f.file + ".tmp")
            pix.save(str(tmp), output="png")
            os.replace(tmp, fig_dir / f.file)
    body = body_size(flow)
    pitch = line_pitch(flow)
    builder = _Builder(body, pitch, "figures")
    for e in ordered:
        if isinstance(e, Figure):
            builder.add_element({
                "kind": "figure", "file": f"figures/{e.file}",
                "caption": join_lines([l.text for l in e.caption]) if e.caption else None,
                "labels": [l.text.replace("­", "") for l in e.labels],
                "bbox": [round(v, 1) for v in (e.rect.x0, e.rect.y0, e.rect.x1, e.rect.y1)]})
        elif isinstance(e, Table):
            builder.add_element({
                "kind": "table", "rows": e.rows, "cols": e.cols, "text": e.markdown,
                "bbox": [round(v, 1) for v in (e.rect.x0, e.rect.y0, e.rect.x1, e.rect.y1)]})
        else:
            builder.add(e)
    blocks = builder.finish()
    contents = _contents_page(blocks)
    if contents:
        for b in blocks:
            if b["kind"] == "specs":
                b["kind"] = "contents"
    rec = {"pdf": pno + 1, "label": label, "label_source": source,
           "columns": 2 if gutter is not None else 1, "blocks": blocks, "stripped": stripped,
           "warnings": warnings, "dropped_chars": pt.dropped}
    if contents:
        rec["contents"] = True
    return rec


# ---------------------------------------------------------------------------- a document
def parse_pages(spec: str | None, page_count: int) -> list[int] | None:
    """"30-35,40" -> 0-based indices inside the document; None for all."""
    if not spec:
        return None
    out = set()
    for part in str(spec).split(","):
        part = part.strip()
        if not part:
            continue
        m = re.match(r"^(\d+)(?:-(\d+))?$", part)
        if not m:
            raise ManualsError(f"bad page range {spec!r}")
        a = int(m.group(1))
        b = int(m.group(2) or a)
        for p in range(a, b + 1):
            if 1 <= p <= page_count:
                out.add(p - 1)
    return sorted(out)


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def process_pdf(path: Path, slug: str, fig_dir: Path | None, pages: str | None = None,
                dpi: int = 150, only_pages: list[int] | None = None) -> tuple[dict, list[dict]]:
    """Analyse the whole PDF, build the records of ``pages`` (the manual's configured range)
    narrowed to ``only_pages`` (0-based, from --pages). Returns (meta, records)."""
    doc = pymupdf.open(str(path))
    try:
        an = analyse(doc, slug)
        idxs = parse_pages(pages, doc.page_count)
        if idxs is None:
            idxs = list(range(doc.page_count))
        if only_pages is not None:
            idxs = [i for i in idxs if i in set(only_pages)]
        records = [build_page(doc, i, an, slug, fig_dir, dpi) for i in idxs]
        meta = {"pages": doc.page_count, "processed": [i + 1 for i in idxs]}
    finally:
        doc.close()
    return meta, records


# ---------------------------------------------------------------------------- outputs
def _warning_md(b: dict) -> str:
    t = b["text"]
    m = re.match(r"^(" + "|".join(WARN_WORDS) + r")\s*[!:]?\s*(.*)$", t, re.I | re.S)
    rest = m.group(2) if m else t
    return f"> **{b['level']}:** {rest}".rstrip()


def block_md(b: dict) -> str:
    k = b["kind"]
    if k == "heading":
        return f"### {b['text']}"
    if k == "step":
        return f"{b['n']}. {b['text']}"
    if k == "bullet":
        return f"- {b['text']}"
    if k == "warning":
        return _warning_md(b)
    if k in ("specs", "contents", "table"):
        return b["text"]
    if k == "figure":
        cap = b["caption"] or "no caption"
        labels = f"; labels: {', '.join(b['labels'])}" if b["labels"] else ""
        return f"[Figure: {cap} — {b['file']}{labels}]"
    return b["text"]


def render_md(entry: dict, meta: dict, records: list[dict]) -> str:
    out = [f"# {entry.get('maker', '')} {entry.get('model', '')}: {entry.get('doctype', '')}".strip(), ""]
    for key in ("slug", "file", "doc_no", "rev", "date", "control", "machines", "pages", "notes"):
        v = entry.get(key)
        if v in (None, "", []):
            continue
        if isinstance(v, list):
            v = ", ".join(str(x) for x in v)
        out.append(f"- {key}: {v}")
    out.append(f"- sha256: {meta.get('sha256', '')}")
    out.append(f"- pdf pages: {meta.get('pages', '')}; made {meta.get('made', '')} with {meta.get('made_with', '')}")
    out.append("")
    for rec in records:
        lab = f"p. {rec['label']}" if rec["label"] is not None else "no label"
        head = f"## pdf {rec['pdf']} · {lab}"
        if rec.get("contents"):
            head += " (contents)"
        out.append(head)
        out.append("")
        for b in rec["blocks"]:
            out.append(block_md(b))
            out.append("")
    return "\n".join(out).rstrip() + "\n"


def write_atomic(path: Path, text: str):
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def _stats(entry: dict, meta: dict, records: list[dict], fig_dir: Path, seconds: float) -> dict:
    labels = Counter(r["label_source"] for r in records)
    kinds = Counter(b["kind"] for r in records for b in r["blocks"])
    figs = [b for r in records for b in r["blocks"] if b["kind"] == "figure"]
    mb = 0.0
    for f in figs:
        p = fig_dir.parent / f["file"]
        if p.is_file():
            mb += p.stat().st_size / 1e6
    overlap = sum(1 for r in records if any(w["kind"] == "overlap" for w in r["warnings"]))
    return {
        "slug": entry["slug"], "file": entry["file"], "pdf_pages": meta["pages"],
        "pages": len(records),
        "labels": {"text": labels.get("text", 0), "pdf_label": labels.get("pdf_label", 0),
                   "inferred": labels.get("inferred", 0), "none": labels.get("none", 0)},
        "figures": {"count": len(figs), "mb": round(mb, 2)},
        "tables": kinds.get("table", 0), "specs": kinds.get("specs", 0) + kinds.get("contents", 0),
        "warnings": kinds.get("warning", 0), "steps": kinds.get("step", 0),
        "headings": kinds.get("heading", 0), "paras": kinds.get("para", 0),
        "chars": sum(len(b.get("text", "")) for r in records for b in r["blocks"]),
        "dropped_chars": sum(r["dropped_chars"] for r in records),
        "stripped_lines": sum(len(r["stripped"]) for r in records),
        "overlap_warnings": overlap,
        "pages_with_warnings": sum(1 for r in records if r["warnings"]),
        "two_column_pages": sum(1 for r in records if r["columns"] == 2),
        "seconds": round(seconds, 1),
    }


@dataclass
class Result:
    manuals: list[dict]
    excluded: list[dict]
    errors: list[str]
    dry_run: bool
    seconds: float = 0.0
    out: Path | None = None


def load_config(path: Path) -> dict:
    try:
        cfg = json.loads(Path(path).read_text("utf-8"))
    except (OSError, ValueError) as e:
        raise ManualsError(f"cannot read {path}: {e}") from e
    if not isinstance(cfg, dict) or not isinstance(cfg.get("manuals"), list) or "source" not in cfg:
        raise ManualsError(f"{path}: needs 'source' and a 'manuals' list")
    slugs = [m.get("slug") for m in cfg["manuals"]]
    if len(set(slugs)) != len(slugs) or not all(slugs):
        raise ManualsError(f"{path}: every manual needs a unique slug")
    cfg.setdefault("excluded", [])
    return cfg


def run(config: Path, out: Path, only: list[str] = (), pages: str | None = None, dpi: int = 150,
        dry_run: bool = False, log=print) -> Result:
    cfg = load_config(config)
    source = Path(cfg["source"])
    entries = cfg["manuals"]
    if only:
        unknown = [s for s in only if s not in {e["slug"] for e in entries}]
        if unknown:
            raise ManualsError(f"unknown slug(s): {', '.join(unknown)}")
        entries = [e for e in entries if e["slug"] in set(only)]
    if pages and len(entries) != 1:
        raise ManualsError("--pages needs --only with a single slug")
    t0 = time.perf_counter()
    res = Result([], list(cfg["excluded"]), [], dry_run, out=out)
    if dry_run:
        for e in entries:
            src = source / e["file"]
            state = "" if src.is_file() else "  [MISSING]"
            rng = f", pages {e.get('pages')}" if e.get("pages") else ""
            log(f"would process {e['slug']}: {src.name}{rng}{state}")
        res.seconds = time.perf_counter() - t0
        return res
    out.mkdir(parents=True, exist_ok=True)
    fig_dir = out / "figures"
    for e in entries:
        src = source / e["file"]
        if not src.is_file():
            res.errors.append(f"{e['slug']}: missing {src}")
            log(f"error    {e['slug']}: missing {src}")
            continue
        t1 = time.perf_counter()
        try:
            doc = pymupdf.open(str(src))
            only_pages = parse_pages(pages, doc.page_count)
            doc.close()
            if fig_dir.is_dir():
                for old in fig_dir.glob(f"{e['slug']}_pdf*_f*.png"):
                    m = re.match(rf"{re.escape(e['slug'])}_pdf(\d+)_f\d+\.png$", old.name)
                    if m and (only_pages is None or int(m.group(1)) - 1 in set(only_pages)):
                        old.unlink()
            meta, records = process_pdf(src, e["slug"], fig_dir, e.get("pages"), dpi, only_pages)
        except Exception as ex:  # noqa: BLE001 - one bad manual must not stop the rest
            res.errors.append(f"{e['slug']}: {type(ex).__name__}: {ex}")
            log(f"error    {e['slug']}: {type(ex).__name__}: {ex}")
            continue
        meta["sha256"] = sha256_of(src)
        meta["made"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        meta["made_with"] = f"g-pdf {__version__}"
        head = {"type": "file", "manual": e, "sha256": meta["sha256"], "pages": meta["pages"],
                "processed": meta["processed"], "made": meta["made"], "made_with": meta["made_with"],
                "dpi": dpi}
        write_atomic(out / f"{e['slug']}.pages.jsonl",
                     "\n".join(json.dumps(r, ensure_ascii=False) for r in [head] + records) + "\n")
        write_atomic(out / f"{e['slug']}.md", render_md(e, meta, records))
        stats = _stats(e, meta, records, fig_dir, time.perf_counter() - t1)
        if only_pages is not None:
            stats["pages_arg"] = pages
        res.manuals.append(stats)
        log(f"done     {e['slug']}: {stats['pages']} pages, {stats['figures']['count']} figures "
            f"({stats['figures']['mb']} MB), labels none {stats['labels']['none']}, "
            f"{stats['seconds']} s")
    res.seconds = time.perf_counter() - t0
    summary_path = out / "manual-pages.json"
    summary = {"made": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "made_with": f"g-pdf {__version__}", "config": str(config), "dpi": dpi,
               "manuals": {}, "excluded": res.excluded, "errors": res.errors,
               "seconds": round(res.seconds, 1)}
    if summary_path.is_file() and (only or pages):
        try:
            old = json.loads(summary_path.read_text("utf-8"))
            summary["manuals"] = old.get("manuals", {})
        except (OSError, ValueError):
            pass
    for s in res.manuals:
        summary["manuals"][s["slug"]] = s
    write_atomic(summary_path, json.dumps(summary, indent=1, ensure_ascii=False) + "\n")
    return res


def report(res: Result) -> str:
    if res.dry_run:
        return f"dry run: nothing written ({res.seconds:.1f} s)"
    lines = [f"processed {len(res.manuals)} manual(s), {len(res.errors)} error(s) "
             f"({res.seconds:.1f} s); summary in {res.out / 'manual-pages.json'}"]
    for s in res.manuals:
        lab = s["labels"]
        lines.append(f"  {s['slug']:<28} pages {s['pages']:>4}  labels text/pdf/inferred/none "
                     f"{lab['text']}/{lab['pdf_label']}/{lab['inferred']}/{lab['none']}  figures "
                     f"{s['figures']['count']} ({s['figures']['mb']} MB)  tables {s['tables']}  "
                     f"specs {s['specs']}  warnings {s['warnings']}  steps {s['steps']}  "
                     f"dropped {s['dropped_chars']}  overlap {s['overlap_warnings']}")
    return "\n".join(lines)
