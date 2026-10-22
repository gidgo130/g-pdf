# SPDX-License-Identifier: AGPL-3.0-or-later
"""gpdf-cli export: clean, citable text for every confirmed catalogue entry that is not
excluded, written as NEW files under OUTDIR (sources, working files and the catalogue
folder are only read).

Per entry (stem = the input file's stem):
  <stem>.jsonl            one record per text unit in reading order (heading, prose, verse,
                          quote, note) with its PDF pages, printed page labels, language, flags
  <stem>.pages.jsonl      per page: label, page kind, the stripped running head, quality class,
                          every removed line with its reason
  <stem>.apparatus.jsonl  the text of contents and index pages (kept out of the main text)
  <stem>.txt              a readable rendering
and OUTDIR/export.json (tool version, inputs and their sha256, counts).

Layout first, dictionary last: line breaks, heads, notes and letter-spacing are decided from
where the characters sit (textlayout); word lists only where geometry cannot decide, and
never for an entry whose decision has ``no_dictionary_fixes``.
"""

from __future__ import annotations

import json
import os
import re
import statistics
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from gpdf import __version__
from gpdf.pipeline import catalogue, cleanup, quality
from gpdf.pipeline import textlayout as tl
from gpdf.pipeline.ocr import OcrError, parse_pages
from gpdf.pipeline.split import same_file
from gpdf.pipeline.survey import EXCLUDE, read_txt, sha256

SCHEMA = 1
EXPORT_LOG = "export.json"
SUFFIXES = (".jsonl", ".pages.jsonl", ".apparatus.jsonl", ".txt")
HEADING, PROSE, VERSE, QUOTE, NOTE = "heading", "prose", "verse", "quote", "note"
KINDS = (HEADING, PROSE, VERSE, QUOTE, NOTE)
BODY, CONTENTS, INDEX, BLANK, FRONT = "body", "contents", "index", "blank", "front"
FOUND, INFERRED, NONE = "found", "inferred", "none"

HEAD_WINDOW = 4             # pages each side where a running head must repeat
HEAD_REPEAT = 2             # ... on at least this many other pages
LABEL_WINDOW = 8            # pages each side for the local page-number offset
LABEL_AGREE = 3             # numbers that must agree on an offset
BAND_TOP, BAND_BOTTOM = 0.2, 0.8         # share of the page height
BOILER_PAGES = 3            # a bottom-band URL on this many pages is boilerplate
MISREAD_LABEL = 4           # characters: an isolated line where a page number should be
SMALL, SMALL_TESS = 0.9, 0.86            # x the body size: smaller type
SMALL_PITCH = 0.85          # x the body pitch: a Tesseract line in smaller type
SMALL_SHARE = 0.7           # share of a page's lines in small type: it is the page's body
SMALL_PAGE_PITCH = 0.9      # x the file pitch: a Tesseract page set tighter (Unruh's Notes)
BACK_MATTER = {"bibliography", "selected bibliography", "works cited", "index", "glossary"}
SHORT_LINE = 15             # characters: a Tesseract line too short to judge by its size
GIANT_BOX = 5.0             # x the body size and its own size: a box no text line has
MARGIN_WORDS = 3
INDEX_SHARE = 0.4           # lines ending in a page number or with a dot leader
INDEX_MIN_LINES = 6         # numbered lines (a title page with an ISSN and a year is not)
LATE = 0.9                  # a two-column page in the last 10% of the file: an index
NOTE_GAP = 1.25             # x the pitch: the gap before a note block inside small type
TXT_VERSE = 60              # characters: median line length below which a .txt is verse
SENTENCE_END = ".!?…»”\")]"

# Per-file rules, keyed by the first 16 hex digits of the catalogue id (the source's sha256).
RULES = {
    # La raza cósmica, p. 177 (back cover): the visible "Acceso" / "Abierto" stamp spans and
    # the OCR junk in the left margin ("DADDE MEXICO.", "00771"); the body column starts at
    # x ~101 and keeps the blurb.
    "3a55d3b4dd35f74d": {"drop": {177: ("back_cover_margin", lambda ln: ln.x1 < 98 or
                                        ln.text in ("Acceso", "Abierto"))}},
    # "Vanguardias literarias, ¿una estética que nos sigue interpelando?", p. 1: the SciELO
    # sidebar printed with the web page (right column, "Servicios Personalizados" ...
    # "Permalink").
    "2c416f2e81060b0e": {"drop": {1: ("scielo_sidebar", lambda ln: ln.x0 > 400 and
                                      150 < ln.y0 < 575)}},
    # Manifiestos: unnumbered editorial notes in small type, indented, after a manifesto
    # ("Publicado en ...", "Este ...").
    "e27a278fac5297c8": {"editorial_notes": True},
    # Unruh: endnotes (a Notes section at the back), not footnotes: plain text, no linking.
    "be5a47fac943c90c": {"endnotes": True},
}


class ExportError(Exception):
    """A refusal or failure with a message meant for the user."""


def rules_for(entry_id: str) -> dict:
    return RULES.get(entry_id[:16], {})


# ---------------------------------------------------------------------------- pages
@dataclass
class Label:
    value: int | None = None
    kind: str = ""                 # arabic / roman
    text: str | None = None
    status: str = NONE


@dataclass
class Note:
    number: int | None
    lines: list = field(default_factory=list)       # tl.Line
    editorial: bool = False
    inferred: bool = False         # the number follows the previous note, not the marker
    text: str = ""

    @property
    def pages(self) -> list[int]:
        return [self.lines[0].page, self.lines[-1].page]

    def finished(self) -> bool:
        return self.text.rstrip()[-1:] in SENTENCE_END


@dataclass
class Page:
    index: int                     # 0-based PDF page
    width: float
    height: float
    lines: list = field(default_factory=list)       # tl.Line, reading order
    giants: list = field(default_factory=list)      # tl.Giant
    tesseract: bool = False
    scan_layer: bool = False
    two_col: bool = False
    lang: str | None = None
    quality: str = quality.UNSCORED
    label: Label = field(default_factory=Label)
    head: str | None = None
    kind: str = BODY
    dropped: list = field(default_factory=list)     # (reason, text)
    notes: list = field(default_factory=list)

    def drop(self, line, reason) -> None:
        self.dropped.append((reason, line.text))

    def removed(self) -> dict:
        return dict(Counter(r for r, _ in self.dropped))


def _visible_text(page) -> bool:
    return any(s["type"] != 3 and s["opacity"] > 0 for s in page.get_texttrace())


def read_pages(doc, selection, job, counts) -> tuple[list[Page], float]:
    """Pass 1: every selected page's lines (merged fragments, columns, giants taken out),
    and the file's body size."""
    pages, raw = [], []
    for i in selection:
        page = doc[i]
        tess = tl.tesseract_layer(page)
        lines, turned = tl.raw_lines(page, sup_markers=not tess)
        p = Page(i, page.rect.width, page.rect.height, tesseract=tess,
                 scan_layer=tess or (job["scan"] and _visible_text(page)))
        rows = job["rows"].get(i + 1) or {}
        p.lang = rows.get("lang") or job["lang"]
        p.quality = rows.get("cls") or quality.UNSCORED
        p.dropped += [("not_horizontal", "")] * turned
        pages.append(p)
        raw.append(lines)
    size = tl.body_size([ln for lines in raw for ln in lines])
    for p, lines in zip(pages, raw):
        kept = []
        for ln in lines:
            if ln.y1 < 0 or ln.y0 > p.height or ln.x1 < 0 or ln.x0 > p.width:
                p.drop(ln, "off_page")
            elif ln.height > GIANT_BOX * max(size, ln.size):
                p.drop(ln, "giant_box")      # a box far taller than its type: OCR junk
            else:
                kept.append(ln)
        rule = rules_for(job["id"]).get("drop", {}).get(p.index + 1)
        if rule:                         # per-file rules see the fragments, before fragments
            reason, test = rule          # on one baseline are joined
            for ln in [ln for ln in kept if test(ln)]:
                kept.remove(ln)
                p.drop(ln, reason)
        if p.scan_layer:
            kept = scan_junk(p, kept, job["nodict"])
        kept, p.giants = tl.take_giants(kept, size)
        for g in p.giants:
            if not g.drop_cap:
                p.dropped.append(("giant_junk", g.word.text))
        g = tl.gutter(kept, p.width)
        if g is not None:
            p.two_col = True
            for ln in kept:
                ln.col = 0 if ln.x1 <= g else 1
        p.lines = tl.merge_fragments(kept)
        if not p.tesseract:
            for ln in p.lines:
                tl.superscripts(ln)
    return pages, size


def scan_junk(p: Page, fragments: list, nodict: bool) -> list:
    """On a scan layer, before fragments on one baseline are joined: symbol-only fragments
    (pencil marks: "|", "oo"-free "-", "_ ~") and fragments entirely outside the text column
    (10th percentile x0 to 90th percentile x1 of the fragments longer than LONG_LINE) with
    <= MARGIN_WORDS words, no digit (numbers are left to the page-number rules) and no known
    word of >= 4 letters (with ``nodict``: no letter)."""
    long_ = [ln for ln in fragments if ln.chars > tl.LONG_LINE]
    col = tl.column(long_) if len(long_) >= 3 else None
    kept = []
    for ln in fragments:
        if cleanup.symbol_only(ln.text):
            p.drop(ln, "symbol_line")
        elif (col is not None and (ln.x1 < col.left or ln.x0 > col.right)
              and len(ln.words) <= MARGIN_WORDS and not any(c.isdigit() for c in ln.text)
              and not _real_word(ln.text, p.lang, nodict)):
            p.drop(ln, "margin_junk")
        else:
            kept.append(ln)
    return kept


# ---------------------------------------------------------------------------- labels, heads
def _bands(p: Page) -> tuple[list, list]:
    """Lines on the first two and the last two baselines of the page, in the top and bottom
    fifth: where heads, page numbers and boilerplate may be."""
    if not p.lines:
        return [], []
    bases = sorted({round(ln.base) for ln in p.lines})
    top = [ln for ln in p.lines if round(ln.base) in bases[:2] and ln.y0 < BAND_TOP * p.height]
    bottom = [ln for ln in p.lines if round(ln.base) in bases[-2:]
              and ln.y1 > BAND_BOTTOM * p.height]
    return top, bottom


def _mode(values) -> tuple[int | None, int]:
    if not values:
        return None, 0
    v, n = Counter(values).most_common(1)[0]
    return v, n


class Sequence:
    """The file's page-number sequence: printed = PDF page + offset, the offset most
    detected numbers agree on (within LABEL_WINDOW pages, else over the file)."""

    def __init__(self, found: list[tuple[int, int, str]]):
        self.found = found                      # (pdf page 1-based, value, kind)
        self.glob = {}
        for kind in ("arabic", "roman"):
            off, n = _mode([v - p for p, v, k in found if k == kind])
            self.glob[kind] = off if n >= LABEL_AGREE else None

    def offset(self, page: int, kind: str = "arabic") -> int | None:
        near = [v - p for p, v, k in self.found if k == kind and abs(p - page) <= LABEL_WINDOW]
        off, n = _mode(near)
        return off if n >= LABEL_AGREE else self.glob[kind]

    def fits(self, page: int, value: int, kind: str) -> bool:
        off = self.offset(page, kind)
        return off is not None and value == page + off

    def expected(self, page: int) -> int | None:
        off = self.offset(page, "arabic")
        return None if off is None or page + off < 1 else page + off


def _strip_edges(ln, tokens) -> None:
    """Take the page-number tokens (and the separators next to them) off a line's ends."""
    def edge(w):
        t = w.text.strip(cleanup._EDGE)
        return not t or t in tokens

    while len(ln.words) > 1 and edge(ln.words[0]):
        ln.words.pop(0)
    while len(ln.words) > 1 and edge(ln.words[-1]):
        ln.words.pop()


def furniture(pages: list[Page], counts: dict) -> None:
    """Running heads and page numbers (labels), bottom-band URL boilerplate."""
    bands = {p.index: _bands(p) for p in pages}
    found = []
    for p in pages:
        top, bottom = bands[p.index]
        for ln in top + bottom:
            lab = cleanup.label_line(ln.text)
            if lab:
                found.append((p.index + 1, lab[0], lab[1]))
            elif ln in top:
                _, edge, _ = cleanup.head_parts(ln.text)
                found += [(p.index + 1, v, k) for v, k, _ in edge]
    seq = Sequence(found)

    # running heads: the core of a top-band line (numbers that fit the sequence removed)
    # repeated on >= HEAD_REPEAT other pages within HEAD_WINDOW pages
    cores = {}
    for p in pages:
        for ln in bands[p.index][0]:
            if cleanup.label_line(ln.text):
                continue
            core, edge, inner = cleanup.head_parts(ln.text)
            fit = all(seq.fits(p.index + 1, v, k) for v, k, _ in edge + inner)
            if core:
                cores.setdefault(p.index, []).append((ln, core, edge, inner, fit))

    def repeats(index, core, numbered):
        """Other pages within HEAD_WINDOW with a similar head whose numbers all fit (with
        ``numbered``: that carries a fitting edge number)."""
        return {q for q in range(index - HEAD_WINDOW, index + HEAD_WINDOW + 1)
                if q != index and any(f and (e or not numbered) and cleanup.similar(core, c)
                                      for _, c, e, _, f in cores.get(q, []))}

    for p in pages:
        top, bottom = bands[p.index]
        for ln, core, edge, inner, fit in cores.get(p.index, []):
            # guard 1: numbers in the head fit the page sequence, or (OCR misread the page
            # number: Unruh "12" for 222) an edge number on a head that, nearby, carries
            # fitting page numbers; guard 2: the window
            misread = not fit and edge and not inner
            if not (fit or misread) or len(repeats(p.index, core, misread)) < HEAD_REPEAT:
                continue
            p.head = core if p.head is None else f"{p.head} | {core}"
            if fit:
                for v, k, t in edge:
                    p.label = Label(v, k, t, FOUND)
            p.lines.remove(ln)
            p.drop(ln, "running_head")
        for ln in top + bottom:
            if ln not in p.lines:
                continue
            lab = cleanup.label_line(ln.text)
            if lab and (seq.fits(p.index + 1, lab[0], lab[1]) or (
                    lab[1] == "arabic" and ln in bottom and seq.glob["arabic"] is None)):
                p.label = Label(lab[0], lab[1], lab[2], FOUND)
                p.lines.remove(ln)
                p.drop(ln, "page_number")
                continue
            if ln in top and not lab:
                core, edge, _ = cleanup.head_parts(ln.text)
                fit = [e for e in edge if seq.fits(p.index + 1, e[0], e[1])]
                if fit and core and len(fit) == len(edge):
                    p.label = Label(fit[0][0], fit[0][1], fit[0][2], FOUND)
                    _strip_edges(ln, {t for _, _, t in fit})
                    p.dropped.append(("page_number", " ".join(t for _, _, t in fit)))
    # an isolated short line where the sequence predicts a number (Bürger p. 78 "Tn")
    for p in pages:
        if p.label.status == FOUND:
            continue
        exp = seq.expected(p.index + 1)
        top, bottom = bands[p.index]
        for ln in top + bottom:
            if (exp and ln in p.lines and len(ln.text) <= MISREAD_LABEL
                    and sum(round(o.base) == round(ln.base) for o in p.lines) == 1):
                p.lines.remove(ln)
                p.drop(ln, "page_number_misread")
                p.label = Label(exp, "arabic", str(exp), INFERRED)
                break
    # labels between found ones, from the sequence
    with_label = [p.index for p in pages if p.label.status == FOUND and p.label.kind == "arabic"]
    if with_label:
        lo, hi = min(with_label), max(with_label)
        for p in pages:
            if p.label.status == NONE and lo <= p.index <= hi:
                exp = seq.expected(p.index + 1)
                if exp:
                    p.label = Label(exp, "arabic", str(exp), INFERRED)
    # URL boilerplate in the bottom band, repeated
    urls = [(p, ln) for p in pages for ln in bands[p.index][1]
            if ln in p.lines and cleanup.url(ln.text)]
    for p, ln in urls:
        if sum(cleanup.similar(ln.text, o.text, 0.6) for _, o in urls) >= BOILER_PAGES:
            p.lines.remove(ln)
            p.drop(ln, "boilerplate")
    counts["heads"] = sorted({p.head for p in pages if p.head})


# ---------------------------------------------------------------------------- junk
def junk(pages: list[Page], job, counts) -> None:
    """Ornaments, symbol-only lines and pencil-mark artefacts on scan layers (per-file
    rules and margin junk run on the fragments: read_pages, scan_junk)."""
    for p in pages:
        for ln in list(p.lines):
            if cleanup.ornament(ln.text):
                p.lines.remove(ln)
                p.drop(ln, "ornament")
                ln.marks.append("ornament")
                nxt = [o for o in p.lines if o.col == ln.col and o.base > ln.base]
                if nxt:
                    nxt[0].marks.append("cut_before")
        if not p.scan_layer:
            continue
        for ln in list(p.lines):
            if cleanup.symbol_only(ln.text):
                p.lines.remove(ln)
                p.drop(ln, "symbol_line")
                continue
            if len(ln.words) > 1 and cleanup.edge_mark(ln.words[0].text):
                p.dropped.append(("edge_mark", ln.words.pop(0).text))
                counts["artefacts"]["edge_mark"] += 1
            if len(ln.words) > 1 and cleanup.edge_mark(ln.words[-1].text):
                p.dropped.append(("edge_mark", ln.words.pop().text))
                counts["artefacts"]["edge_mark"] += 1
            for w in ln.words:
                w.text, n = cleanup.underscores(w.text)
                counts["artefacts"]["underscore"] += n
            counts["reported"].extend(f"p. {p.index + 1}: {r}"
                                      for r in cleanup.reported(ln.text))


def _real_word(text, lang, nodict) -> bool:
    if nodict:
        return any(c.isalpha() for c in text)
    return any(len(w) >= 4 and quality.known(w, lang)
               for w in re.findall(r"[^\W\d_]+", text))


# ---------------------------------------------------------------------------- page kinds
def page_kinds(pages: list[Page], total: int) -> None:
    for p in pages:
        texts = [ln.text for ln in p.lines if ln.chars >= 3]
        if not p.lines:
            p.kind = BLANK
            continue
        numbered = sum(cleanup.ends_in_number(t) or cleanup.has_leader(t) for t in texts)
        heading = any(cleanup.index_heading(ln.text) for ln in p.lines[:3])
        if ((numbered >= INDEX_MIN_LINES and numbered >= INDEX_SHARE * len(texts))
                or (p.two_col and p.index >= LATE * total) or heading):
            p.kind = CONTENTS if p.index < total / 2 else INDEX
    marked = [p.index for p in pages if p.head or p.label.status == FOUND]
    if marked:
        for p in pages:
            if p.index < marked[0] and p.kind == BODY:
                p.kind = FRONT


# ---------------------------------------------------------------------------- notes
def _small(p: Page, size: float, pitch: float) -> None:
    """Mark lines set in smaller type: born-digital by font size; Tesseract (sizes come
    from the line boxes) by size, or by a tighter line pitch with a smaller size; a short
    Tesseract line ("cante.") by its size only with the tighter pitch. A born-digital page
    whose text is mostly "smaller" than the file's body (the Mexico anthology states its
    body at 8 pt on some pages) is measured against its own size, and by pitch. When small
    type makes up >= SMALL_SHARE of a page's lines it is that page's body (Ariel's
    prologue, Unruh's Notes section): the lines are judged again by the page's own size
    and pitch, and only type smaller than that is small (its footnotes). On a Tesseract
    layer the sizes are noisy and the page's own pitch is the reliable measure: a page set
    tighter than SMALL_PAGE_PITCH x the file's, with half its lines small, is such a page."""
    lines = p.lines
    if not lines:
        return
    own = tl.body_size(lines)
    unreliable = not p.tesseract and own < SMALL * size
    _mark_small(lines, own if unreliable else size, pitch, p.tesseract, unreliable)
    share = sum(ln.small for ln in lines) / len(lines)
    own_pitch = tl.pitch(lines)
    if share >= SMALL_SHARE or (p.tesseract and share >= 0.5
                                and own_pitch < SMALL_PAGE_PITCH * pitch):
        _mark_small(lines, own, own_pitch, p.tesseract, False)


def _mark_small(lines, ref: float, pitch: float, tesseract: bool, unreliable: bool) -> None:
    for k, ln in enumerate(lines):
        near = [abs(o.base - ln.base) for o in lines[max(0, k - 1):k + 2]
                if o is not ln and o.col == ln.col and abs(o.base - ln.base) < 1.6 * pitch]
        tight = bool(near) and min(near) < SMALL_PITCH * pitch
        if not tesseract:
            ln.small = ln.size < SMALL * ref or (unreliable and tight and ln.size <= ref)
            continue
        by_size = ln.size < SMALL_TESS * ref and (ln.chars >= SHORT_LINE or tight)
        ln.small = by_size or (tight and ln.size < 0.95 * ref)


def find_notes(p: Page, state: dict, rules: dict, nodict: bool) -> None:
    """Footnotes: the block of smaller type at the bottom of a body page, below body text,
    from its first line that starts with a marker (or, first, the lines that continue the
    previous page's unfinished note). Smaller lines above the notes stay as quotes. With
    the editorial rule, an unmarked small block after a gap is an editorial note."""
    if rules.get("endnotes") or p.two_col or p.kind not in (BODY, FRONT):
        return
    lines = p.lines
    k = len(lines)
    while k > 0 and lines[k - 1].small:
        k -= 1
    block, above = lines[k:], lines[:k]
    if not block or not any(not ln.small and ln.chars >= tl.LONG_LINE for ln in above):
        state["pending"] = None
        return
    deltas = sorted(b.base - a.base for a, b in zip(block, block[1:]) if b.base > a.base)
    pitch = deltas[len(deltas) // 4] if deltas else block[0].size * 1.2   # the notes' own
    pending = state.get("pending")
    start = None
    for j, ln in enumerate(block):
        if cleanup.note_start(ln.text) is None:
            continue
        if j == 0 or pending is not None or ln.base - block[j - 1].base > NOTE_GAP * pitch:
            start = j                    # after a gap (a quote may sit above), or in notes
            break
    cont = []
    first_marker = start if start is not None else len(block)
    if pending is not None and first_marker > 0:
        cont = block[:first_marker]      # the previous page's note goes on (it keeps its
        pending.lines += cont            # place in the reading order, with both pages)
        pending.text = _note_text(pending.lines, p.lang, nodict, pending.number)
        state["pending"] = None if pending.finished() else pending
    elif start is None and rules.get("editorial_notes") and above:
        gap = block[0].base - above[-1].base
        if gap > tl.GAP_PITCH * tl.pitch(above):
            note = Note(None, list(block), editorial=True)
            note.text = _note_text(note.lines, p.lang, nodict, None)
            p.notes.append(note)
            p.lines = above
            state["pending"] = None if note.finished() else note
            return
    if start is None:
        p.lines = [ln for ln in lines if ln not in cont]
        if not cont:
            state["pending"] = None
        return
    region = block[start:]
    left = min(ln.x0 for ln in region)
    notes, cur = [], None
    for ln in region:
        m = cleanup.note_start(ln.text)
        new = m is not None and (cur is None or (_plausible(m, cur.number, state)
                                                 and not (m[2] and m[0] is None)))
        forced = (not new and m is not None and cur is not None and ln.x0 > left
                  + tl.INDENT_EM * ln.size and cur.lines[-1].text.rstrip()[-1:] in SENTENCE_END)
        if new or forced:            # forced: an indented note start after a finished note
            n, inferred = _number(m, state, p.tesseract, forced)   # (OCR damaged its number)
            cur = Note(n, [ln], inferred=inferred)
            notes.append(cur)
            if n is not None:
                state["last"] = n
        else:
            cur.lines.append(ln)
    for note in notes:
        note.text = _note_text(note.lines, p.lang, nodict, note.number)
    p.notes += notes
    p.lines = above + block[len(cont):start]
    state["pending"] = notes[-1] if notes and not notes[-1].finished() else None


def _plausible(m, last, state) -> bool:
    n, _, misread = m
    if n is None or misread:
        return True
    prev = last if last is not None else state.get("last")
    return prev is None or prev < n <= prev + 3


def _number(m, state, guess: bool, forced: bool = False) -> tuple[int | None, bool]:
    """(number, inferred). A misread marker, or on a Tesseract layer (``guess``) a number
    that does not follow the last note, takes the last note's number + 1 when its digits
    could be what is left of it ("?1" after 20, "89" after 18, "%"), or whatever they are
    when only the indent made it a note start (``forced``: Schwartz's "8" for 16); a plain
    "1" is a new count."""
    n, _, misread = m
    last = state.get("last")
    if last is not None and (guess or misread):
        if n is not None and last < n <= last + 3:
            return n, False
        if forced and guess and (misread or n != 1):
            return last + 1, True
        want = str(last + 1)
        if n is None or ((misread or n != 1) and (want.endswith(str(n)) or (
                len(str(n)) == len(want) and str(n)[-1] == want[-1]))):
            return last + 1, True
    return n, False


def _note_text(lines, lang, nodict, number) -> str:
    texts = [cleanup.soft_hyphens(ln.text) if not ln.text.endswith(cleanup.SOFT_HYPHEN)
             else ln.text for ln in lines]
    m = cleanup.note_start(texts[0])
    if m:
        texts[0] = texts[0][len(m[1]):].lstrip()
    text = texts[0]
    for t in texts[1:]:
        text, _ = cleanup.join_lines(text, t, lang, nodict)
    return text.rstrip(cleanup.SOFT_HYPHEN)


# ---------------------------------------------------------------------------- the entry
def _respace(pages, job, counts) -> None:
    for p in pages:
        if p.kind in (CONTENTS, INDEX, BLANK):
            continue
        em = None if p.tesseract else tl.word_gap_em(p.lines)     # the page's word gap
        for ln in p.lines:
            c = tl.respace_line(ln, p.lang, job["nodict"], p.tesseract,
                                None if em is None else em * ln.size)
            counts["spacing"].update({k: v for k, v in c.items() if k != "msplit"})
            counts["msplit"] += c["msplit"]
        for g in p.giants:
            if g.drop_cap:
                into = tl.apply_drop_cap(p.lines, g)
                counts["drop_caps"] += into is not None
                for ln in p.lines:
                    for mark in [m for m in ln.marks if isinstance(m, tuple)]:
                        p.dropped.append(mark)
                        ln.marks.remove(mark)


_ONE_DIGIT = re.compile(r"\[\^(\d)\]$")


def _link(pages, counts) -> None:
    """Glued digits after a word become [^n] when note n is on the page or the next one; a
    marker read as two single-digit superscripts ("[^5] [^1]", Mexico p. 277) is one
    marker when the joined number is such a note."""
    numbers = {p.index: {n.number for n in p.notes if n.number is not None} for p in pages}
    for p in pages:
        near = numbers.get(p.index, set()) | numbers.get(p.index + 1, set())
        for ln in p.lines:
            k = 0
            while k + 1 < len(ln.words):
                a, b = ln.words[k], ln.words[k + 1]
                ma, mb = _ONE_DIGIT.search(a.text), _ONE_DIGIT.fullmatch(b.text)
                if ma and mb and int(ma.group(1) + mb.group(1)) in near:
                    a.text = f"{a.text[:ma.start()]}[^{ma.group(1)}{mb.group(1)}]"
                    a.x1 = max(a.x1, b.x1)
                    del ln.words[k + 1]
                    counts["markers"]["digits_joined"] += 1
                    continue
                k += 1
            for w in ln.words:
                counts["markers"]["superscript"] += w.text.count("[^")
                if not near and not any(c.isdigit() for c in w.text):
                    continue
                w.text, linked, left = cleanup.link_markers(w.text, near)
                counts["markers"]["linked"] += linked
                counts["markers"]["left"] += left


def _flows(pages, size, file_col) -> list[tl.Flow]:
    """``file_col``: (left, width) of the file's prose column, or None."""
    flows, prev = [], None
    file_left, file_width = file_col if file_col else (None, None)
    for p in pages:
        if p.kind not in (BODY, FRONT):
            prev = None
            continue
        lines = [ln for ln in p.lines if ln.words]
        cols = {}
        for c in sorted({ln.col for ln in lines}):
            mine = [ln for ln in lines if ln.col == c]
            cols[c] = tl.column(mine, None if p.two_col else file_width,
                                None if p.two_col else p.width,
                                None if p.two_col else file_left)
        pitch = tl.pitch([ln for ln in lines if not ln.small] or lines)
        hsize = max(size, tl.body_size([ln for ln in lines if not ln.small] or lines))
        for k, ln in enumerate(lines):
            same = [o for o in lines if o.col == ln.col]
            j = same.index(ln)
            above = ln.base - same[j - 1].base if j else None
            below = same[j + 1].base - ln.base if j + 1 < len(same) else None
            ln.heading = tl.heading(ln, hsize, cols[ln.col], above, below, pitch, p.tesseract)
            f = tl.Flow(ln, cols[ln.col], pitch, p.tesseract, p.lang, gap_before=above)
            f.cut_before = ("cut_before" in ln.marks or (k == 0 and (
                prev is None or prev != p.index - 1)))
            flows.append(f)
        prev = p.index
    return flows


@dataclass
class Record:
    kind: str
    text: str
    pages: list[int]               # 0-based
    note: int | None = None
    flags: list[str] = field(default_factory=list)


def _records(units, pages) -> list[Record]:
    """Units and notes in reading order: a page's notes after the units that start on it."""
    by_index = {p.index: p for p in pages}
    out, done = [], set()

    def notes_until(index):
        for q in sorted(by_index):
            if q <= index and q not in done:
                done.add(q)
                for n in by_index[q].notes:
                    flags = (["editorial"] if n.editorial else []) + (
                        ["marker_inferred"] if n.inferred else [])
                    out.append(Record(NOTE, n.text, n.pages, n.number, flags))

    for u in units:
        if not u.text.strip():
            continue
        first = u.pages[0]
        notes_until(first - 1)
        out.append(Record(u.kind, u.text.rstrip(cleanup.SOFT_HYPHEN), u.pages,
                          flags=list(u.flags)))
    notes_until(max(by_index) if by_index else -1)
    return out


def _new_counts() -> dict:
    return {"pages": 0, "units": Counter(), "notes": Counter(),
            "markers": Counter(), "heads": [], "labels": Counter(), "joins": Counter(),
            "spacing": Counter({"geometry": 0, "dictionary": 0, "even": 0, "undone": 0,
                                "unchanged": 0}), "msplit": 0,
            "merged_seen": 0, "dropped": Counter(), "apparatus_pages": 0,
            "low_quality_pages": 0, "drop_caps": 0, "artefacts": Counter(), "reported": []}


def export_pdf(job) -> tuple[list[dict], list[dict], list[dict], str, dict]:
    """(records, page rows, apparatus rows, readable text, counts) of one PDF entry."""
    counts = _new_counts()
    with pymupdf.open(job["input"]) as doc:
        selection = job["pages"] if job["pages"] is not None else list(range(len(doc)))
        total = len(doc)
        pages, size = read_pages(doc, selection, job, counts)
    furniture(pages, counts)
    junk(pages, job, counts)
    page_kinds(pages, total)
    long_cols = [c for p in pages if p.kind == BODY and not p.two_col
                 and sum(ln.chars > tl.LONG_LINE for ln in p.lines) >= 10
                 and (c := tl.column(p.lines)) and not c.ragged]    # prose pages only
    file_col = (statistics.median(c.left for c in long_cols),
                statistics.median(c.width for c in long_cols)) if long_cols else None
    pitch = statistics.median([tl.pitch([ln for ln in p.lines if not ln.small] or p.lines)
                               for p in pages if len(p.lines) > 3] or [1.2 * size])
    rules = rules_for(job["id"])
    _respace(pages, job, counts)
    state = {"pending": None, "last": None}
    for p in pages:
        _small(p, size, pitch)
        if p.kind in (BODY, FRONT):
            find_notes(p, state, rules, job["nodict"])
    _link(pages, counts)
    units, joins = tl.segment(_flows(pages, size, file_col), job["nodict"])
    counts["joins"].update(joins)
    records = _records(units, pages)
    if rules.get("endnotes"):
        _flag_endnotes(records)
    return _outputs(job, pages, records, counts, size)


def _flag_endnotes(records: list) -> None:
    """With the endnotes rule (Unruh): the units after a "Notes" heading, up to the next
    back-matter heading, are flagged ``endnotes`` (plain text, no linking)."""
    inside = False
    for r in records:
        if r.kind == HEADING:
            t = cleanup.fold(r.text)
            if t == "notes":
                inside = True
            elif t in BACK_MATTER:
                inside = False
        elif inside:
            r.flags.append("endnotes")


def _label_of(p: Page) -> str | None:
    return p.label.text if p.label.status != NONE else None


def _outputs(job, pages, records, counts, size):
    by_index = {p.index: p for p in pages}
    rows, app, out = [], [], []
    poor = {p.index for p in pages if p.quality == quality.POOR_CLASS}
    front = {p.index for p in pages if p.kind == FRONT}
    for seq, r in enumerate(records, 1):
        first, last = by_index[r.pages[0]], by_index[r.pages[-1]]
        flags = list(r.flags)
        if any(i in poor for i in range(r.pages[0], r.pages[-1] + 1)):
            flags.append("low_quality")
        if r.pages[0] in front:
            flags.append("front_matter")
        rec = {"entry": job["id"], "seq": seq, "kind": r.kind, "text": r.text,
               "pages": [r.pages[0] + 1, r.pages[-1] + 1],
               "labels": [_label_of(first), _label_of(last)],
               "label_inferred": INFERRED in (first.label.status, last.label.status),
               "note": r.note, "lang": first.lang, "flags": flags}
        out.append(rec)
        counts["units"][r.kind] += 1
        if r.kind == NOTE:
            counts["notes"]["editorial" if "editorial" in r.flags else
                            "numbered" if r.note is not None else "unnumbered"] += 1
        for w in re.findall(r"[^\W\d_]+", r.text):
            if w[0].islower() and not quality.known(w, first.lang) and quality.merged(
                    w, first.lang):
                counts["merged_seen"] += 1
    for p in pages:
        counts["pages"] += 1
        counts["labels"][p.label.status] += 1
        counts["dropped"].update(r for r, _ in p.dropped)
        counts["low_quality_pages"] += p.index in poor
        rows.append({"entry": job["id"], "page": p.index + 1, "label": _label_of(p),
                     "label_status": p.label.status, "kind": p.kind, "head": p.head,
                     "quality": p.quality, "lang": p.lang, "removed": p.removed(),
                     "dropped": [{"reason": r, "text": t} for r, t in p.dropped]})
        if p.kind in (CONTENTS, INDEX):
            counts["apparatus_pages"] += 1
            lines = [cleanup.leaders(cleanup.soft_hyphens(ln.text)) for ln in p.lines]
            app.append({"entry": job["id"], "page": p.index + 1, "label": _label_of(p),
                        "kind": p.kind, "columns": 2 if p.two_col else 1, "lines": lines,
                        "text": "\n".join(lines)})
    return out, rows, app, render_txt(out, rows), counts


def render_txt(records, rows) -> str:
    """The readable rendering: "[p. 74 · pdf 74]" where a page starts, a blank line between
    units, notes after the page they sit on as "[^21] text"."""
    labels = {r["page"]: (r["label"], r["label_status"]) for r in rows}

    def marker(page):
        label, status = labels.get(page, (None, NONE))
        if label is None:
            return f"[pdf {page}]"
        return f"[p. {label}{'?' if status == INFERRED else ''} · pdf {page}]"

    parts, shown = [], 0
    for r in records:
        first = r["pages"][0]
        while shown < first:
            shown += 1
            if shown in labels:
                parts.append(marker(shown))
        if r["kind"] == NOTE:
            n = r["note"] if r["note"] is not None else ("ed." if "editorial" in r["flags"]
                                                         else "*")
            parts.append(f"[^{n}] {r['text']}")
        elif r["kind"] == HEADING:
            parts.append(f"## {r['text']}")
        else:
            parts.append(r["text"])
    return "\n\n".join(parts) + "\n"


# ---------------------------------------------------------------------------- .txt entries
_ORNAMENT_LINE = re.compile(r"^\s*[–—-]\s*[IVXLC\d]+\s*[–—-]\s*$")


def export_txt(job) -> tuple[list[dict], list[dict], list[dict], str, dict]:
    """A plain-text entry (a ciudadseva poem): the site header stripped (the title kept
    once as a heading), every other line kept, stanzas split at blank lines, verse when the
    median line is shorter than TXT_VERSE characters. Records cite line numbers."""
    counts = _new_counts()
    text, _ = read_txt(job["input"])
    lines = text.splitlines()
    dropped, title, start = [], None, 0
    url_at = next((i for i, s in enumerate(lines[:15]) if cleanup.url(s)), None)
    if url_at is not None:
        title = next((s.strip() for s in lines[:url_at] if s.strip()), None)
        dropped += [("site_header", s) for s in lines[:url_at + 1] if s.strip()]
        start = url_at + 1
        while start < len(lines):
            s = lines[start].strip()
            if not s:
                start += 1
            elif _ORNAMENT_LINE.match(s) or (title and s == title):
                dropped.append(("site_header", s))
                start += 1
            else:
                break
    records, stanzas, cur = [], [], []
    for i in range(start, len(lines)):
        if lines[i].strip():
            cur.append(i)
        elif cur:
            stanzas.append(cur)
            cur = []
    if cur:
        stanzas.append(cur)
    body = [lines[i] for s in stanzas for i in s]
    verse = bool(body) and statistics.median(len(s) for s in body) < TXT_VERSE
    lang = job["lang"]
    if title:
        records.append({"entry": job["id"], "seq": 0, "kind": HEADING, "text": title,
                        "lines": [1, 1], "labels": [None, None], "label_inferred": False,
                        "note": None, "lang": lang, "flags": []})
    for s in stanzas:
        t = ("\n".join(lines[i].rstrip() for i in s) if verse
             else " ".join(lines[i].strip() for i in s))
        records.append({"entry": job["id"], "seq": 0, "kind": VERSE if verse else PROSE,
                        "text": t, "lines": [s[0] + 1, s[-1] + 1], "labels": [None, None],
                        "label_inferred": False, "note": None, "lang": lang, "flags": []})
    for seq, r in enumerate(records, 1):
        r["seq"] = seq
        counts["units"][r["kind"]] += 1
    counts["dropped"].update(r for r, _ in dropped)
    rows = [{"entry": job["id"], "lines": [1, len(lines)], "kind": BODY,
             "removed": dict(Counter(r for r, _ in dropped)),
             "dropped": [{"reason": r, "text": t} for r, t in dropped]}]
    txt = "\n\n".join((f"## {r['text']}" if r["kind"] == HEADING else r["text"])
                      for r in records) + "\n"
    return records, rows, [], txt, counts


# ---------------------------------------------------------------------------- one entry
def _jsonl(rows) -> str:
    return "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)


def run_job(job) -> dict:
    """Export one entry and write its four files (temp + replace). Runs in a worker
    process with --jobs > 1."""
    t0 = time.perf_counter()
    fn = export_txt if job["kind"] == "txt" else export_pdf
    records, rows, app, txt, counts = fn(job)
    out = Path(job["outdir"])
    stem = job["stem"]
    for suffix, text in ((".jsonl", _jsonl(records)), (".pages.jsonl", _jsonl(rows)),
                         (".apparatus.jsonl", _jsonl(app)), (".txt", txt)):
        catalogue.write_text(out / f"{stem}{suffix}", text)
    counts["seconds"] = round(time.perf_counter() - t0, 2)
    return {k: (dict(v) if isinstance(v, Counter) else v) for k, v in counts.items()}


# ---------------------------------------------------------------------------- the plan
@dataclass
class Item:
    id: str
    rel: str
    source: str
    action: str = ""
    entry: dict | None = None
    input: str = ""
    input_sha: str = ""
    input_from: str = ""           # applied / working file / source
    nodict: bool = False
    pages: list[int] | None = None
    excluded: bool = False
    refused: str = ""

    @property
    def writes(self) -> bool:
        return not self.excluded and not self.refused

    @property
    def stem(self) -> str:
        return Path(self.input).stem


def _matches(only, id_, rel) -> bool:
    return not only or any(id_.startswith(o) or o.lower() in rel.lower() for o in only)


def _latest_applied(applied, id_) -> dict | None:
    for rec in reversed(applied.get("applied", [])):
        if rec.get("id") == id_:
            return rec
    return None


def plan(folder, outdir, only=(), pages=None, force=False) -> list[Item]:
    """One item per catalogue entry (matching ``only``): what would be exported from which
    input file, what is excluded, what is refused and why."""
    folder, outdir = Path(folder), Path(outdir)
    if not (folder / catalogue.CATALOGUE).is_file():
        raise ExportError(f"no {catalogue.CATALOGUE} in {folder}: run the survey first")
    if pages and not only:
        raise ExportError("--pages needs --only (it is for quick checks of one file)")
    cat = catalogue.load(folder)
    decisions = catalogue.load_decisions(folder)
    applied = catalogue.load_applied(folder)
    files = cat["files"]
    for r in cat.get("roots", []):
        if catalogue.under(outdir, r["path"]):
            raise ExportError(f"the output folder {outdir} is inside the source folder "
                              f"{r['path']}; put it elsewhere")
    if catalogue.under(outdir, folder) and catalogue.same_path(outdir, folder):
        raise ExportError("the output folder is the catalogue folder; put it elsewhere")
    items = []
    for id_ in sorted(files, key=lambda k: (files[k]["root"].lower(), files[k]["rel"].lower())):
        e = files[id_]
        if not _matches(only, id_, e["rel"]):
            continue
        it = Item(id_, e["rel"], e["path"], entry=e)
        items.append(it)
        status, d, why = catalogue.decision_status(e, decisions, files)
        it.action = (d or {}).get("action") or ""
        it.nodict = catalogue.no_dictionary_fixes(d)
        if e.get("status") == catalogue.MISSING:
            it.refused = "missing" + (" (replaced by a changed file)" if e.get("replaced_by")
                                      else "")
            continue
        if status not in (catalogue.CONFIRMED, catalogue.APPLIED):
            it.refused = f"{status}: {why}"
            continue
        if it.action == EXCLUDE:
            it.excluded = True
            continue
        if e.get("status") != catalogue.PRESENT or not e.get("measured"):
            it.refused = f"not measured ({e.get('error') or e.get('status')})"
            continue
        _choose_input(it, e, applied)
        if it.refused:
            continue
        if pages:
            if e["kind"] != "pdf":
                it.refused = "--pages is for PDFs"
                continue
            try:
                it.pages = parse_pages(pages, e["measured"]["pages"])
            except OcrError as err:
                it.refused = str(err)
                continue
        outs = [outdir / f"{it.stem}{s}" for s in SUFFIXES]
        if any(same_file(it.input, o) or same_file(it.source, o) for o in outs):
            it.refused = "an output would be the input file"
        elif not force and (old := [o.name for o in outs if o.exists()]):
            it.refused = f"{old[0]} already exists (pass --force to replace it)"
    return items


def _choose_input(it: Item, e: dict, applied: dict) -> None:
    """The applied output (applied.json; sha256 must match), else the working file (its
    catalogue sha256 must match), else the source (re-hashed: it must match the id)."""
    rec = _latest_applied(applied, it.id)
    wf = e.get("working_file")
    if rec:
        path, want, what = rec.get("output", ""), rec.get("output_sha256"), "applied output"
    elif wf:
        path, want, what = wf["path"], wf["sha256"], "working file"
    else:
        path, want, what = e["path"], it.id, "source"
    if not os.path.isfile(path):
        it.refused = f"{what} not found: {path}"
        return
    sha = sha256(path)
    if sha != want:
        it.refused = (f"the {what} changed since it was recorded (hash differs): "
                      + ("apply or survey again" if what != "source" else "survey again"))
        return
    it.input, it.input_sha, it.input_from = str(path), sha, what


def job_for(it: Item, outdir) -> dict:
    m = it.entry["measured"]
    return {"id": it.id, "rel": it.rel, "input": it.input, "kind": it.entry["kind"],
            "pages": it.pages, "outdir": str(outdir), "stem": it.stem, "nodict": it.nodict,
            "scan": bool(m.get("scan")), "lang": (m.get("language") or {}).get("lang"),
            "rows": {r["page"]: {"lang": r.get("lang"), "cls": r.get("cls")}
                     for r in m.get("page_scores", [])} if it.entry["kind"] == "pdf" else {}}


def write_log(outdir, done: list[tuple[Item, dict]], flags: dict) -> Path:
    """OUTDIR/export.json: the tool version and, per entry exported (merged with earlier
    runs), its input file and sha256, the flags used, the counts and the time."""
    path = Path(outdir) / EXPORT_LOG
    data = {"schema": SCHEMA, "entries": {}}
    if path.is_file():
        try:
            old = json.loads(path.read_text("utf-8"))
            if isinstance(old, dict) and isinstance(old.get("entries"), dict):
                data["entries"] = old["entries"]
        except (OSError, ValueError):
            pass
    data["tool_version"] = __version__
    data["updated"] = catalogue.now()
    for it, counts in done:
        data["entries"][it.id] = {
            "rel": it.rel, "input": it.input, "input_sha256": it.input_sha,
            "input_from": it.input_from, "stem": it.stem,
            "flags": {"no_dictionary_fixes": it.nodict, "pages": flags.get("pages"),
                      "force": flags.get("force", False)},
            "counts": counts, "time": catalogue.now()}
    catalogue.write_text(path, json.dumps(data, indent=1, ensure_ascii=False) + "\n")
    return path
