# SPDX-License-Identifier: AGPL-3.0-or-later
"""Page geometry of the text export: words and lines from the text layer, two-column
pages, drop caps, letter-spaced runs measured on the characters, and lines into units by
the "would the next word have fit" test.

Born-digital and ABBYY-style layers give real character boxes (rawdict). Tesseract layers
(GlyphLessFont) stretch each word over its box: the word boxes are right, the characters
inside are not, so they are read as words only and no character gap is ever used.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

import pymupdf

from gpdf.pipeline import cleanup

TESSERACT_FONTS = ("GlyphLessFont",)
FLAGS = (pymupdf.TEXTFLAGS_RAWDICT & ~pymupdf.TEXT_PRESERVE_LIGATURES
         & ~pymupdf.TEXT_PRESERVE_IMAGES)
SAME_LINE = 0.5             # vertical overlap (share of the smaller height): one line
GUTTER_LO, GUTTER_HI = 0.3, 0.7          # where a two-column gutter may lie (page width)
GUTTER_SIDE = 0.25          # each column holds this share of the lines
GUTTER_MIN_LINES = 6
GUTTER_WIDTH = 12           # pt: the narrowest gutter (wider than any word gap)
DROP_CAP = 2.5              # x the body size: a drop cap (or giant junk)
FIT_TOL, FIT_TOL_TESS = 1.5, 3.0         # pt: the fit test's tolerance
SPACE_EM = 0.25             # a space, in em
INDENT_EM = 0.6             # a first-line indent or outdent, in em
GAP_PITCH = 1.5             # x the line pitch: a vertical gap
HEADING_SIZE, HEADING_SIZE_TESS = 1.15, 1.35  # x the body size: a heading
HEADING_CHARS = 90
CENTRED = 0.06              # x the column width: a centred line
LONG_LINE = 30              # characters: a line that measures the column
VERSE_MIN = 3               # hard-broken lines that make a verse unit
SENTENCE_END = ".!?…»”\"’)"


# ---------------------------------------------------------------------------- words, lines
@dataclass
class Word:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    size: float
    base: float = 0.0          # baseline
    sup: bool = False          # superscript flag on every character
    chars: int = 0

    @property
    def width(self) -> float:
        return self.x1 - self.x0


@dataclass
class Line:
    words: list[Word]
    page: int = 0              # 0-based
    lead: str = ""             # leading spaces in the layer (Altazor's verse)
    col: int = 0               # 0, or 1 for the right column of a two-column page
    small: bool = False        # set later: smaller than the body
    heading: bool = False      # set later
    marks: list = field(default_factory=list)   # set later: per-line notes for the record
    lead_x0: float | None = None   # where the leading spaces start

    @property
    def x0(self) -> float:
        return min(w.x0 for w in self.words)

    @property
    def x1(self) -> float:
        return max(w.x1 for w in self.words)

    @property
    def vx0(self) -> float:
        """Where the line starts as set: indentation made of space characters (Altazor's
        staircase lines) is text, not layout."""
        return self.lead_x0 if self.lead and self.lead_x0 is not None else self.x0

    @property
    def y0(self) -> float:
        return min(w.y0 for w in self.words)

    @property
    def y1(self) -> float:
        return max(w.y1 for w in self.words)

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @property
    def base(self) -> float:
        return statistics.median(w.base for w in self.words)

    @property
    def size(self) -> float:
        """Character-weighted median size (a small note marker at the start does not count
        for the line)."""
        sizes = sorted((w.size, max(1, w.chars)) for w in self.words)
        half, acc = sum(n for _, n in sizes) / 2, 0
        for s, n in sizes:
            acc += n
            if acc >= half:
                return s
        return sizes[-1][0]

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)

    @property
    def chars(self) -> int:
        return sum(len(w.text) for w in self.words)


def tesseract_layer(page) -> bool:
    return any(s["font"].startswith(TESSERACT_FONTS) for s in page.get_texttrace())


def _word(chars, sup_markers) -> Word:
    """A Word from (char, bbox, span) triples; a trailing run of superscript digits or "*"
    (a born-digital note marker) becomes "[^n]"."""
    text = "".join(c for c, _, _ in chars)
    sups = [bool(s["flags"] & 1) for _, _, s in chars]
    if sup_markers and sups[-1]:
        k = len(chars)
        while k > 0 and sups[k - 1]:
            k -= 1
        tail = text[k:]
        if tail.isdigit() or set(tail) == {"*"}:
            text = f"{text[:k]}[^{tail}]"
    sizes = sorted(s["size"] for _, _, s in chars)
    return Word(text, min(b[0] for _, b, _ in chars), min(b[1] for _, b, _ in chars),
                max(b[2] for _, b, _ in chars), max(b[3] for _, b, _ in chars),
                sizes[len(sizes) // 2], statistics.median(s["origin"][1] for _, _, s in chars),
                all(sups), len(chars))


def raw_lines(page, sup_markers=False) -> tuple[list[Line], int]:
    """The layer's lines as PyMuPDF groups them, split into words at spaces, and the number
    of lines that are not horizontal (left out)."""
    d = page.get_text("rawdict", flags=FLAGS)
    out, turned = [], 0
    for block in d["blocks"]:
        if block.get("type") != 0:
            continue
        for ln in block["lines"]:
            if abs(ln["dir"][1]) > 0.1 or ln["dir"][0] < 0:
                turned += 1
                continue
            words, cur, lead, lead_x0 = [], [], "", None
            for span in ln["spans"]:
                for ch in span["chars"]:
                    c = ch["c"]
                    if c.isspace() or c == " ":
                        if cur:
                            words.append(_word(cur, sup_markers))
                            cur = []
                        elif not words:
                            lead += " "
                            lead_x0 = ch["bbox"][0] if lead_x0 is None else lead_x0
                        continue
                    cur.append((c, ch["bbox"], {"size": span["size"], "flags": span["flags"],
                                                "origin": ch["origin"]}))
            if cur:
                words.append(_word(cur, sup_markers))
            if words:
                out.append(Line(words, page.number, lead, lead_x0=lead_x0))
    return out, turned


# ---------------------------------------------------------------------------- columns
def gutter(lines: list[Line], width: float) -> float | None:
    """The x of a two-column gutter: a vertical strip in the middle of the page that no line
    crosses, with >= GUTTER_SIDE of the lines entirely on each side; else None."""
    if len(lines) < 2 * GUTTER_MIN_LINES:
        return None
    best = None
    for x in range(int(GUTTER_LO * width), int(GUTTER_HI * width) + 1):
        if any(ln.x0 - GUTTER_WIDTH / 2 < x < ln.x1 + GUTTER_WIDTH / 2 for ln in lines
               if not (ln.x1 <= x - GUTTER_WIDTH / 2 or ln.x0 >= x + GUTTER_WIDTH / 2)):
            continue
        left = sum(ln.x1 <= x for ln in lines)
        right = len(lines) - left
        if min(left, right) >= max(GUTTER_MIN_LINES, GUTTER_SIDE * len(lines)):
            score = min(left, right)
            if best is None or score > best[0]:
                best = (score, x)
    return None if best is None else float(best[1])


def merge_fragments(lines: list[Line]) -> list[Line]:
    """Fragments on the same baseline joined into one line (the anthologies' bibliography
    numbers sit in a span of their own), words left to right; lines top to bottom."""
    out: list[Line] = []
    for ln in sorted(lines, key=lambda l: (l.base, l.x0)):
        for o in reversed(out[-4:]):
            overlap = min(o.y1, ln.y1) - max(o.y0, ln.y0)
            h = min(o.height, ln.height) or 1.0
            if o.col == ln.col and overlap >= SAME_LINE * h and abs(o.base - ln.base) < 0.5 * h:
                if ln.x0 < o.x0:
                    o.lead, o.lead_x0 = ln.lead, ln.lead_x0
                o.words = sorted(o.words + ln.words, key=lambda w: w.x0)
                break
        else:
            out.append(Line(list(ln.words), ln.page, ln.lead, ln.col, lead_x0=ln.lead_x0))
    out.sort(key=lambda l: (l.col, l.base, l.x0))
    return out


def superscripts(line: Line) -> int:
    """A note marker set smaller and raised after a word ("crítica²"), when the layer gave no
    superscript flag (the marker came as a fragment of its own): digits or "*" at <= 0.8 x
    the line's size, baseline raised by >= 0.15 x, close to the word before -> "[^n]" glued
    to it. Born-digital layers only. Returns the markers found."""
    size, base, found, out = line.size, line.base, 0, []
    for w in line.words:
        t = w.text
        if (out and (t.isdigit() and len(t) <= 3 or set(t) == {"*"}) and w.size <= 0.8 * size
                and w.base < base - 0.15 * size and w.x0 - out[-1].x1 < 0.3 * size):
            prev = out[-1]
            prev.text += f"[^{t}]"
            prev.x1 = max(prev.x1, w.x1)
            found += 1
            continue
        out.append(w)
    line.words = out
    return found


def body_size(lines: list[Line]) -> float:
    """Character-weighted median size of the lines."""
    sizes = sorted((w.size, max(1, w.chars)) for ln in lines for w in ln.words)
    if not sizes:
        return 10.0
    half, acc = sum(n for _, n in sizes) / 2, 0
    for s, n in sizes:
        acc += n
        if acc >= half:
            return s
    return sizes[-1][0]


# ---------------------------------------------------------------------------- drop caps
@dataclass
class Giant:
    word: Word
    drop_cap: bool = False


def take_giants(lines: list[Line], size: float) -> tuple[list[Line], list[Giant]]:
    """Words of <= 3 characters set at >= DROP_CAP x the body size, taken out of the lines:
    a single letter left of and overlapping the first lines of a paragraph is a drop cap;
    the rest is page junk (Bürger: "A", "NE" at 40-120 pt above the page)."""
    giants, kept = [], []
    for ln in lines:
        rest = []
        for w in ln.words:
            if len(w.text) <= 3 and w.size >= DROP_CAP * size:
                giants.append(Giant(w))
            else:
                rest.append(w)
        if rest:
            ln.words = rest
            kept.append(ln)
    for g in giants:
        w = g.word
        beside = [ln for ln in kept if ln.y0 < w.y1 and ln.y1 > w.y0 and ln.x0 >= w.x0
                  + 0.5 * w.width]
        g.drop_cap = (len(w.text) == 1 and w.text.isalpha() and w.y0 >= -1
                      and len(beside) >= 2)
    return kept, giants


def apply_drop_cap(lines: list[Line], g: Giant) -> Line | None:
    """Prefix the drop cap to the first word of the first line beside it; drop a lone
    capital that starts a later line beside it with a wide gap after it (the side fragment
    of the drop-cap art: "L pestad"). Returns the line it went into."""
    w = g.word
    beside = [ln for ln in lines if ln.y0 < w.y1 and ln.y1 > w.y0 and ln.x0 >= w.x0]
    if not beside:
        return None
    beside.sort(key=lambda l: l.base)
    first = beside[0]
    first.words[0].text = w.text + first.words[0].text
    for ln in beside[1:]:
        if (len(ln.words) > 1 and len(ln.words[0].text) == 1 and ln.words[0].text.isupper()
                and ln.words[0].x0 < w.x1 <= ln.words[1].x0 + 1):
            ln.marks.append(("drop_cap_art", ln.words[0].text))
            ln.words = ln.words[1:]
    return first


# ---------------------------------------------------------------------------- letter-spacing
def _gap(a: Word, b: Word) -> float:
    return b.x0 - a.x1


def _regroup(words: list[Word], tokens: list[str]) -> list[Word]:
    """Words for ``tokens``, which hold the same letters as ``words`` with spaces moved:
    each new word spans the boxes of the characters it took (split boxes by proportion)."""
    spans = []                                   # (x0, x1, word) per character
    for w in words:
        n = len(w.text) or 1
        for i in range(len(w.text)):
            spans.append((w.x0 + w.width * i / n, w.x0 + w.width * (i + 1) / n, w))
    out, k = [], 0
    for t in tokens:
        part = spans[k:k + len(t)]
        k += len(t)
        src = [p[2] for p in part]
        out.append(Word(t, part[0][0], min(s.y0 for s in src), part[-1][1],
                        max(s.y1 for s in src), statistics.median(s.size for s in src),
                        statistics.median(s.base for s in src), all(s.sup for s in src),
                        len(t)))
    return out


def word_gap_em(lines: list[Line]) -> float | None:
    """The ordinary word gap of a page, in em: the median over its lines without
    letter-spaced runs (and with >= 2 gaps) of the line's median character gap / size.
    None when the page has no such line."""
    ems = []
    for ln in lines:
        gaps = [_gap(a, b) for a, b in zip(ln.words, ln.words[1:])]
        if len(gaps) >= 2 and not cleanup.spaced_runs([w.text for w in ln.words]):
            ems.append(statistics.median(gaps) / max(ln.size, 1))
    return statistics.median(ems) if ems else None


def respace_line(line: Line, lang: str | None, nodict: bool, tesseract: bool,
                 word_gap: float | None = None, tight: bool = False) -> dict:
    """Letter-spaced runs and "m"-split words of one line rejoined (cleanup.respace and
    cleanup.msplit on the gaps between visible characters; no gaps on a Tesseract layer).
    ``word_gap``: the page's ordinary word gap for this line (pt), if the page has one.
    ``tight``: the per-file rule (cleanup.tight_joins) that a tight gap is no word boundary.
    Returns the counts."""
    texts = [w.text for w in line.words]
    gaps = [None if tesseract else _gap(a, b) for a, b in zip(line.words, line.words[1:])]
    tokens, counts = cleanup.respace(texts, gaps, lang, nodict, word_gap=word_gap)
    counts["msplit"] = counts["tight"] = 0
    if tokens != texts:
        line.words = _regroup(line.words, tokens)
    if not tesseract and not nodict:
        texts = [w.text for w in line.words]
        gaps = [_gap(a, b) for a, b in zip(line.words, line.words[1:])]
        tokens, counts["msplit"] = cleanup.msplit(texts, gaps, lang)
        if tokens != texts:
            line.words = _regroup(line.words, tokens)
    if tight and not tesseract:
        texts = [w.text for w in line.words]
        gaps = [_gap(a, b) for a, b in zip(line.words, line.words[1:])]
        tokens, counts["tight"] = cleanup.tight_joins(texts, gaps, line.size, lang)
        if tokens != texts:
            line.words = _regroup(line.words, tokens)
    return counts


# ---------------------------------------------------------------------------- geometry
@dataclass
class Column:
    left: float
    right: float
    ragged: bool = False       # few lines reach the right edge (verse): not a prose measure

    @property
    def width(self) -> float:
        return self.right - self.left

    @property
    def centre(self) -> float:
        return (self.left + self.right) / 2


def _pct(values, q):
    v = sorted(values)
    return v[min(len(v) - 1, int(q * len(v)))]


def column(lines: list[Line], fallback_width: float | None = None,
           page_width: float | None = None, fallback_left: float | None = None) -> Column | None:
    """The text column of a page: 10th percentile x0 to 90th percentile x1 of its lines
    longer than LONG_LINE characters, an edge that >= 2 lines (or a fifth) reach. A ragged
    page (few lines reach that right edge: verse)
    gets ``fallback_width`` (the file's prose column) from its left edge when that is wider,
    or without one the right margin mirrored from the left (``page_width``). So does a page
    whose lines start at the file's left edge (``fallback_left``) whatever the reach: the
    column is never narrower than the file's prose measure there (Altazor p. 25: 8 of 26
    verse lines end within 3 pt of one x, which looked like a justified column)."""
    long_ = [ln for ln in lines if ln.chars > LONG_LINE] or lines
    if not long_:
        return None
    left, right = _pct([l.x0 for l in long_], 0.1), _pct([l.x1 for l in long_], 0.9)
    need = max(2, -(-len(long_) // 5))
    for x in sorted((l.x1 for l in long_), reverse=True):    # one line sticking out (Ariel)
        if sum(l.x1 >= x - 3 for l in long_) >= need:           # does not set the edge
            right = min(right, x)
            break
    reach = sum(l.x1 >= right - 3 for l in long_)
    ragged = reach < 3 or reach < 0.3 * len(long_)
    em = statistics.median(l.size for l in long_)
    same_left = fallback_left is not None and abs(left - fallback_left) < INDENT_EM * em
    if fallback_width and (ragged or same_left):
        right = max(right, left + fallback_width)
    elif ragged and page_width:
        right = max(right, page_width - left)
    return Column(left, right, ragged)


def pitch(lines: list[Line]) -> float:
    """Median baseline distance between consecutive lines of a page (one column)."""
    d = [b.base - a.base for a, b in zip(lines, lines[1:])
         if b.col == a.col and 0 < b.base - a.base < 2.5 * max(a.size, 1)]
    return statistics.median(d) if d else 1.2 * (statistics.median(
        l.size for l in lines) if lines else 10.0)


def fits(line: Line, nxt: Line, col: Column, tesseract: bool) -> bool:
    """Would the next line's first word have fitted on this line? Then the break is hard."""
    if not nxt.words:
        return True
    room = col.right - (FIT_TOL_TESS if tesseract else FIT_TOL) - line.x1
    need = SPACE_EM * line.size + nxt.words[0].width
    return need < room


def heading(line: Line, size: float, col: Column, above: float | None, below: float | None,
            pitch_: float, tesseract: bool) -> bool:
    """A short line set larger than the body, or centred with gaps above and below
    (``above``/``below``: baseline distance to the neighbours, None at the page edge)."""
    text = line.text
    if len(text) > HEADING_CHARS or not any(c.isalpha() for c in text):
        return False
    if line.size >= (HEADING_SIZE_TESS if tesseract else HEADING_SIZE) * size:
        return True
    centred = (abs((line.x0 + line.x1) / 2 - col.centre) <= CENTRED * col.width
               and line.x1 - line.x0 <= 0.7 * col.width and line.x0 > col.left + 2 * line.size)
    gaps = all(g is None or g > GAP_PITCH * pitch_ for g in (above, below))
    return centred and gaps


# ---------------------------------------------------------------------------- units
@dataclass
class Unit:
    kind: str                      # heading, prose, verse, quote
    lines: list[Line]
    text: str = ""
    flags: list[str] = field(default_factory=list)

    @property
    def pages(self) -> list[int]:
        return [self.lines[0].page, self.lines[-1].page]


@dataclass
class Flow:
    """A body line in reading order with what the segmentation needs: its column, the
    page pitch, whether its layer is Tesseract's, its language, and a forced boundary
    before it (an ornament, a page that is not body, a note block in between)."""
    line: Line
    col: Column
    pitch: float
    tesseract: bool
    lang: str | None
    gap_before: float | None = None    # baseline distance to the previous line, same page
    cut_before: bool = False


def _indent(f: Flow) -> float:
    return f.line.vx0 - f.col.left


def _hard(a: Flow, b: Flow | None, nodict: bool) -> bool:
    text = a.line.text.rstrip()
    if b is None:
        return True
    if text.endswith(cleanup.SOFT_HYPHEN) or (text[-1:] in cleanup.HYPHENS and len(text) > 1
                                               and text[-2].isalpha()):
        return False
    return fits(a.line, b.line, a.col, a.tesseract)


def _gapped(f: Flow) -> bool:
    return f.gap_before is not None and f.gap_before > GAP_PITCH * f.pitch


def blocks(flows: list[Flow]) -> list[int]:
    """A block number per flow: runs of lines on the same page and column, in the same type
    size, with no vertical gap or forced cut between them."""
    out, b = [], 0
    for i, f in enumerate(flows):
        if i and (f.cut_before or _gapped(f) or f.line.page != flows[i - 1].line.page
                  or f.line.col != flows[i - 1].line.col
                  or f.line.small != flows[i - 1].line.small):
            b += 1
        out.append(b)
    return out


def wraps(flows: list[Flow], block: list[int], hard: list[bool]) -> list[bool]:
    """Lines that continue the previous one in a ragged block (verse, a list): a line set
    at the hanging indent (right of where the block's lines start and of the line before,
    or where the previous continuation was) whose first word would not have fitted on the
    previous line against the block's own right edge (Girondo's long verse lines, wrapped
    and indented). The break before such a line is soft. Where the block's lines start is
    the lower median of their starts, not the leftmost: a speaker label at the margin
    before a speech set further in (Tempestad) does not make the speech its wrap."""
    out = [False] * len(flows)
    members = {}
    for i, b in enumerate(block):
        members.setdefault(b, []).append(i)
    for idx in members.values():
        if len(idx) < 2 or 2 * sum(hard[i] for i in idx[:-1]) < len(idx) - 1:
            continue                                   # prose: the column decides
        starts = sorted(flows[i].line.vx0 for i in idx)
        left = starts[(len(starts) - 1) // 2]
        right = max(flows[i].line.x1 for i in idx)
        for a, b in zip(idx, idx[1:]):
            la, lb = flows[a].line, flows[b].line
            em = INDENT_EM * lb.size
            need = la.x1 + SPACE_EM * la.size + lb.words[0].width
            tol = FIT_TOL_TESS if flows[b].tesseract else FIT_TOL
            hanging = lb.vx0 > la.vx0 + em or (out[a] and abs(lb.vx0 - la.vx0) < em)
            if (hanging and lb.vx0 > left + em and need > right - tol
                    and not cleanup.reference(lb.text)):   # "(I, II, 355 – 360)": no wrap
                out[b] = True
    return out


def _signal(i: int, flows: list[Flow], block: list[int], hard: list[bool],
            wrap: list[bool]) -> bool:
    """A paragraph signal before line ``i``: a vertical gap, a size change, a first-line
    indent after a hard break (the next line of the block goes back left, or this line
    stands alone), an outdent after a hard break
    outdent (a hanging indent: the next line of the block goes in again), or a speaker label
    after a hard break. The indents compare lines of the same block only. An indent after a
    speaker label is not a signal: the label ("Miranda: …Te tuve lástima,") starts a verse
    unit and the speech, set further in, belongs to it (Tempestad)."""
    prev, cur = flows[i - 1], flows[i]
    if _gapped(cur) or prev.line.small != cur.line.small:
        return True
    if wrap[i]:
        return False
    nxt = flows[i + 1] if i + 1 < len(flows) and block[i + 1] == block[i] else None
    em = INDENT_EM * cur.line.size
    d = _indent(cur) - _indent(prev)
    back = nxt is None or _indent(nxt) < _indent(cur) - em
    speech = cleanup.speaker(prev.line.text) and not cleanup.speaker(cur.line.text)
    if d > em and hard[i - 1] and (back or hard[i]) and not speech:
        return True
    if (d > em and back and not hard[i] and nxt is not None
            and prev.line.text.rstrip()[-1:] in SENTENCE_END):
        return True                  # the last line nearly full, but a sentence ended
    if d < -em and hard[i - 1] and nxt is not None and _indent(nxt) > _indent(cur) + em:
        return True                  # a hanging indent: the previous entry ended
    return hard[i - 1] and cleanup.speaker(cur.line.text)


def _line_text(f: Flow, verse: bool) -> str:
    t = cleanup.soft_hyphens(f.line.text) if not f.line.text.endswith(
        cleanup.SOFT_HYPHEN) else cleanup.soft_hyphens(f.line.text[:-1]) + cleanup.SOFT_HYPHEN
    return (f.line.lead + t) if verse and f.line.lead else t


def _join(flows: list[Flow], nodict: bool, counts: dict) -> str:
    text = _line_text(flows[0], False)
    for f in flows[1:]:
        text, how = cleanup.join_lines(text, _line_text(f, False), f.lang, nodict)
        counts[how] = counts.get(how, 0) + 1
    return text


def segment(flows: list[Flow], nodict: bool = False) -> tuple[list[Unit], dict]:
    """Body lines (in reading order, across pages) into units. A break is hard when the next
    line's first word would have fitted; soft breaks join into prose (hyphens resolved). A
    paragraph signal, a heading or a forced cut starts a new unit. Inside a unit, >= 3
    consecutive hard-broken lines make verse (breaks kept; a hanging-indent wrap counts as
    part of its line); a paragraph of wrapped prose lines, and fewer such lines, are units
    of their own.
    Returns (units, join counts)."""
    counts: dict[str, int] = {}
    if not flows:
        return [], counts
    hard = [_hard(f, flows[i + 1] if i + 1 < len(flows) and not flows[i + 1].cut_before
                  else None, nodict) for i, f in enumerate(flows)]
    block = blocks(flows)
    wrap = wraps(flows, block, hard)
    for i in range(1, len(flows)):
        if wrap[i]:
            hard[i - 1] = False
    groups, cur = [], [0]
    for i in range(1, len(flows)):
        f, p = flows[i], flows[i - 1]
        boundary = (f.cut_before or f.line.heading or p.line.heading
                    or _signal(i, flows, block, hard, wrap))
        if boundary:
            groups.append(cur)
            cur = [i]
        else:
            cur.append(i)
    groups.append(cur)

    units = []
    for g in groups:
        if flows[g[0]].line.heading:
            units.append(Unit("heading", [flows[i].line for i in g],
                              " ".join(_line_text(flows[i], False) for i in g)))
            continue
        logical, run = [], [g[0]]                  # soft-joined runs, split at hard breaks
        for i in g[1:]:
            if hard[i - 1]:
                logical.append(run)
                run = [i]
            else:
                run.append(i)
        logical.append(run)
        small = flows[g[0]].line.small
        stretch = []                               # consecutive one-line (or wrapped) lines

        def flush():
            if len(stretch) >= VERSE_MIN:
                text = "\n".join(_line_text(flows[r[0]], True) if len(r) == 1
                                 else _join([flows[i] for i in r], nodict, counts)
                                 for r in stretch)
                units.append(Unit("verse", [flows[i].line for r in stretch for i in r], text,
                                  ["quote"] if small else []))
            else:
                for r in stretch:
                    units.append(Unit("quote" if small else "prose",
                                      [flows[i].line for i in r],
                                      _join([flows[i] for i in r], nodict, counts)))
            stretch.clear()

        for r in logical:
            if all(wrap[i] for i in r[1:]):        # a line that stands alone: verse-like
                stretch.append(r)
                continue
            flush()                                # a wrapped paragraph: prose
            units.append(Unit("quote" if small else "prose", [flows[i].line for i in r],
                              _join([flows[i] for i in r], nodict, counts)))
        flush()
    return units, counts
