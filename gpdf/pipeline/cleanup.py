# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pure text rules of the text export (no PyMuPDF): page labels, running-head keys, line
joins (soft and hard hyphens, the OCR's "." for a hyphen), letter-spaced runs, the
"m"-split font glitch, note markers, Unruh's pencil-underline artefacts and dot leaders.

Layout decides first: the gap rules take the gaps between visible characters, measured by
textlayout. Word lists (quality.known / zipf) decide only what the layout cannot, and are
never used with ``nodict`` (the owner's "no dictionary fixes" flag). wordfreq's lists hold
fragments such as "insti", "hom" or "avantgarde", so a dictionary join must also be clearly
more frequent than its parts (JOIN_MARGIN zipf), not only known.
"""

from __future__ import annotations

import re
import statistics
import unicodedata
from difflib import SequenceMatcher

from gpdf.pipeline import quality

SOFT_HYPHEN = "­"
HYPHENS = "-‐‑"
HEAD_SIMILAR = 0.8          # running heads: OCR variants ("Bibliografia" / "Bibliografìa")
JOIN_MARGIN = 1.0           # zipf: a dictionary join beats its parts by this much
COMPOUND_ZIPF = 2.0         # zipf: a line-end hyphen pair rarer than this joined stays
GAP_JUMP = 2.0              # letter gaps vs word gaps: the smallest ratio that splits them
GAP_FLOOR = 0.1             # pt: gaps below this count as this (overlapping glyphs)
SYMBOL_LINE_MAX = 6         # characters: a symbol-only line (pencil marks)

_L = r"[^\W\d_]"
_CLOSE = ".,;:!?»”\"’)]—–"


# ---------------------------------------------------------------------------- text keys
def fold(text: str) -> str:
    """Casefolded, accents removed, spaces collapsed."""
    d = unicodedata.normalize("NFD", text.casefold())
    return " ".join("".join(c for c in d if not unicodedata.combining(c)).split())


def similar(a: str, b: str, threshold: float = HEAD_SIMILAR) -> bool:
    fa, fb = fold(a), fold(b)
    return bool(fa) and bool(fb) and SequenceMatcher(None, fa, fb).ratio() >= threshold


# ---------------------------------------------------------------------------- page labels
_ROMAN = re.compile(r"^m{0,3}(cm|cd|d?c{0,3})(xc|xl|l?x{0,3})(ix|iv|v?i{0,3})$")
_ROMAN_VALUES = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}
_PAGE_OF = re.compile(r"^\s*(?:p[áa]gina|page)\s+(\d{1,4})\s+(?:de|of)\s+\d{1,4}\s*$", re.I)
_EDGE = " \t|·•-–—.:,"
_NUMBER = re.compile(r"\d{1,4}|[ivxlcdm]{1,8}|[IVXLCDM]{1,8}")


def roman(token: str) -> int | None:
    """The value of a roman numeral written all lowercase or all uppercase, else None."""
    if not token or not (token.islower() or token.isupper()):
        return None
    t = token.lower()
    if not _ROMAN.match(t):
        return None
    total, prev = 0, 0
    for c in reversed(t):
        v = _ROMAN_VALUES[c]
        total, prev = (total - v, prev) if v < prev else (total + v, v)
    return total or None


def number(token: str) -> tuple[int, str] | None:
    """(value, "arabic" | "roman") of a page-number token."""
    if token.isdigit() and len(token) <= 4:
        return int(token), "arabic"
    v = roman(token)
    return (v, "roman") if v else None


def label_line(text: str) -> tuple[int, str, str] | None:
    """(value, kind, label) of a line that is only a page number: "74", "- 12 -", "xxxv",
    "Página 3 de 92", "Page 2 of 16"."""
    m = _PAGE_OF.match(text)
    if m:
        return int(m.group(1)), "arabic", m.group(1)
    t = text.strip(_EDGE)
    if not _NUMBER.fullmatch(t):
        return None
    n = number(t)
    return (n[0], n[1], t) if n else None


def head_parts(text: str) -> tuple[str, list[tuple[int, str, str]], list[tuple[int, str, str]]]:
    """A top-band line split into (core, edge numbers, inner numbers): numbers at the start
    or end (with their separators: "74 | INTRODUCCIÓN", "BRASIL (REVISTAS) | 165") and
    arabic numbers inside ("Nota Extensa 6. Un despiste"). Numbers are (value, kind, token);
    roman numerals count only at the edges."""
    words = text.split()
    edge = []
    while words and (n := number(words[0].strip(_EDGE))) and words[0].strip(_EDGE):
        edge.append((n[0], n[1], words.pop(0).strip(_EDGE)))
        while words and not words[0].strip(_EDGE):
            words.pop(0)
    while words and (n := number(words[-1].strip(_EDGE))) and words[-1].strip(_EDGE):
        edge.append((n[0], n[1], words.pop().strip(_EDGE)))
        while words and not words[-1].strip(_EDGE):
            words.pop()
    core = " ".join(words).strip(_EDGE)
    inner = [(int(m.group()), "arabic", m.group()) for m in re.finditer(r"\d{1,4}", core)]
    return core, edge, inner


# ---------------------------------------------------------------------------- dictionary
def _bare(word: str) -> str:
    return word.strip(_CLOSE + "«“‘(¿¡[\"'")


def dict_join(parts: list[str], lang: str | None) -> bool:
    """The parts of a split word make one real word: all letters, the joined form is known
    and either a part is unknown or the joined form is JOIN_MARGIN zipf more frequent than
    every part."""
    parts = [_bare(p) for p in parts]
    joined = "".join(parts)
    if not joined or not _WORD.match(joined) or not quality.known(joined, lang):
        return False
    if any(not quality.known(p, lang) for p in parts if p):
        return True
    return _z(joined, lang) >= max(_z(p, lang) for p in parts if p) + JOIN_MARGIN


_WORD = re.compile(rf"^{_L}+$")


def _z(word: str, lang: str | None) -> float:
    return quality.zipf(word, lang) if lang in quality.LANGS else max(quality.zipfs(word))


def keep_compound(a: str, b: str, lang: str | None) -> bool:
    """A hyphen at a line end belongs to a compound (luso-|hispánico, Avant-|garde): both
    halves are known and the joined form is not, or is rare (below COMPOUND_ZIPF: the lists
    hold "avantgarde" at 1.4). Otherwise the hyphen only split the word (menos-|precio:
    quality.join_hyphens, with the rarity check added)."""
    if not (quality.known(a, lang) and quality.known(b, lang)):
        return False
    if quality.join_hyphens(f"{a}-\n{b}", lang) == f"{a}-{b}":
        return True
    return _z(a + b, lang) < COMPOUND_ZIPF


# ---------------------------------------------------------------------------- line joins
_END_WORD = re.compile(rf"({_L}+)[{HYPHENS}]$")
_DOT_WORD = re.compile(rf"(?:^|[\s(«“])({_L}+)\.$")
_START_WORD = re.compile(rf"^({_L}+)")
SPACE, SOFT, JOINED, KEPT, DOT = "space", "soft_hyphen", "hyphen_join", "hyphen_kept", "dot_join"


def join_lines(a: str, b: str, lang: str | None, nodict: bool = False) -> tuple[str, str]:
    """Two lines joined at a soft break (prose wrap): (text, how). Soft hyphens join (a "-"
    between digits); a line-end hyphen before a lowercase letter joins unless it belongs to a
    compound; before a digit or a capital the hyphen stays, without a space (pp. 57-79,
    Wentzlaff-Eggebert); "insti." + "tución" joins when the joined word is real. With
    ``nodict`` a hyphen before a lowercase letter always joins and "." never does."""
    a, b = a.rstrip(), b.lstrip()
    if not a or not b:
        return a + b, SPACE
    if a.endswith(SOFT_HYPHEN):
        head = a.rstrip(SOFT_HYPHEN)
        if head[-1:].isdigit() and b[:1].isdigit():
            return f"{head}-{b}", KEPT
        return head + b, SOFT
    if a[-1] in HYPHENS and len(a) > 1 and not a[-2].isspace():
        first = b[0]
        m, n = _END_WORD.search(a), _START_WORD.match(b)
        if first.islower() and m and n:
            if not nodict and keep_compound(m.group(1), n.group(1), lang):
                return a + b, KEPT
            return a[:-1] + b, JOINED
        return a + b, KEPT
    if not nodict and a.endswith(".") and b[0].islower():
        m, n = _DOT_WORD.search(a), _START_WORD.match(b)
        if m and n and dict_join([m.group(1), n.group(1)], lang):
            return a[:-1] + b, DOT
    return f"{a} {b}", SPACE


def soft_hyphens(text: str) -> str:
    """Soft hyphens inside a line: dropped between letters, "-" between digits."""
    text = re.sub(rf"(?<=\d){SOFT_HYPHEN}(?=\d)", "-", text)
    return text.replace(SOFT_HYPHEN, "")


# ---------------------------------------------------------------------------- letter-spacing
_PIECE = re.compile(rf"^{_L}{{1,2}}[{re.escape(_CLOSE)}]*$")
_PIECE_PUNCT = re.compile(rf"[{re.escape(_CLOSE)}]+$")
RUN_MIN = 3
WORD_GAP = 1.6              # a letter gap is below the line's median word gap / this
_SENTENCE_PUNCT = re.compile(r"[.;:!?]$")
RUN_MARGIN = 0.5      # zipf: a rejoined run beats its two-letter pieces by this much


def spaced_runs(tokens: list[str]) -> list[tuple[int, int]]:
    """[start, end) of each run of >= RUN_MIN consecutive pieces of <= 2 letters ("C h il e",
    "to d a s"); a piece with trailing punctuation ends its run."""
    runs, i, n = [], 0, len(tokens)
    while i < n:
        j = i
        while j < n and _PIECE.match(tokens[j]):
            j += 1
            if _PIECE_PUNCT.search(tokens[j - 1]):
                break
        if j - i >= RUN_MIN:
            runs.append((i, j))
            i = j
        else:
            i = max(j, i + 1)
    return runs


def split_gaps(gaps: list[float]) -> float | None:
    """The threshold between letter gaps and word gaps: at the largest ratio between two
    consecutive sorted gaps, if it is >= GAP_JUMP; None when there is no such jump."""
    v = sorted(max(g, GAP_FLOOR) for g in gaps)
    best, at = 0.0, None
    for lo, hi in zip(v, v[1:]):
        if hi / lo > best:
            best, at = hi / lo, (lo, hi)
    if at is None or best < GAP_JUMP:
        return None
    return (at[0] * at[1]) ** 0.5


def _dict_words(pieces: list[str], lang: str | None) -> list[str] | None:
    """quality.rejoin_spaced on the pieces, only when some piece is not a word by itself,
    and the word it makes is known and either most of its pieces are single letters or it is
    RUN_MARGIN zipf more frequent than its two-letter pieces ("l a t ig u il l o" ->
    "latiguillo", but not "y a la" -> "yala"); one-letter words split off at the edges
    stay. Else None."""
    punct = _PIECE_PUNCT.search(pieces[-1])
    tail = punct.group() if punct else ""
    bare = pieces[:-1] + [pieces[-1][:len(pieces[-1]) - len(tail)]]
    singles = quality.ONE_LETTER_WORDS.get(lang, quality.ONE_LETTER_ANY)
    if all(x.lower() in singles if len(x) == 1 else quality.known(x, lang) for x in bare):
        return None                      # ordinary short words: "y a la", "se lo da"
    words = quality.rejoin_spaced(" ".join(bare), lang).split(" ")
    inner = bare[len(words[0]) == 1 and len(words) > 1:]
    if len(words) > 1 and len(words[-1]) == 1:
        inner = inner[:-1]
    word = "".join(inner)
    if not inner or len(word) < 2 or not quality.known(word, lang):
        return None
    pairs = [_z(x, lang) for x in inner if len(x) >= 2]
    if 2 * len(pairs) > len(inner) and _z(word, lang) < max(pairs) + RUN_MARGIN:
        return None
    words[-1] += tail
    return words


def _dict_run(pieces: list[str], lang: str | None) -> list[str] | None:
    """A letter-spaced run joined by the word list: the longest span of >= RUN_MIN pieces
    that rejoins into known words; the pieces around it (short words such as "en" or "la"
    that the run took in) stay as they are. None if no span works."""
    n = len(pieces)
    for size in range(n, RUN_MIN - 1, -1):
        for i in range(n - size + 1):
            words = _dict_words(pieces[i:i + size], lang)
            if words is not None and len(words) < size:
                left, right = pieces[:i], pieces[i + size:]
                return ((_dict_run(left, lang) or left) + words
                        + (_dict_run(right, lang) or right))
    return None


def respace(tokens: list[str], gaps: list[float | None], lang: str | None,
            nodict: bool = False, trace: list | None = None) -> tuple[list[str], dict]:
    """Letter-spaced runs of one line rejoined. ``gaps[i]`` is the gap (pt) between the
    visible characters of tokens i and i+1, or None (Tesseract: word boxes only).

    With gaps, inside a run plus one neighbouring token each side, the gaps split at their
    largest ratio jump (>= GAP_JUMP): spaces in the small cluster go, if they are also well
    below the line's ordinary word gap (the median gap outside the runs / WORD_GAP), and
    never after sentence punctuation; unless ``nodict``, a join of pieces that are all words
    by themselves into something that is not a word is undone (_veto). A run without a jump
    (evenly spaced: one word, or ordinary short words) and runs without gaps are joined
    only by the word list (never with ``nodict``). Returns (tokens, counts: geometry,
    dictionary, even, undone, unchanged per run). ``trace`` (a list) gets (outcome, the run
    with its neighbours, the result) per run."""
    counts = {"geometry": 0, "dictionary": 0, "even": 0, "undone": 0, "unchanged": 0}
    runs = spaced_runs(tokens)
    if not runs:
        return tokens, counts
    join_after = [False] * len(tokens)            # remove the space after token i
    replace = {}                                  # run start -> (end, words)
    near = {k for a, b in runs for k in range(max(a - 1, 0), min(b + 1, len(tokens)))}
    outside = [g for k, g in enumerate(gaps)
               if g is not None and k not in near and k + 1 not in near]
    ref = statistics.median(outside) / WORD_GAP if len(outside) >= 2 else None
    outcome = {}
    for a, b in runs:
        lo, hi = max(a - 1, 0), min(b + 1, len(tokens))
        span = gaps[lo:hi - 1]
        measured = bool(span) and all(g is not None for g in span)
        cut = split_gaps(span) if measured else None
        if cut is not None and ref is not None:
            cut = min(cut, ref)          # well below the line's ordinary word gap
        if cut is not None and any(g < cut for g in span):
            for k in range(lo, hi - 1):
                if gaps[k] < cut and not _SENTENCE_PUNCT.search(tokens[k]):
                    join_after[k] = True
            outcome[a] = "geometry"
            continue
        if measured and (lo < a or hi > b):
            outcome[a] = "even"          # even gaps beside neighbours: ordinary words
            continue
        words = None if nodict else _dict_run(tokens[a:b], lang)
        if words is not None:
            replace[a] = (b, words)
        outcome[a] = "unchanged" if words is None else "dictionary"
    if not nodict:
        _veto(tokens, join_after, replace, lang)
    for a, b in runs:
        lo, hi = max(a - 1, 0), min(b + 1, len(tokens))
        if outcome[a] == "geometry" and not any(join_after[lo:hi - 1]):
            outcome[a] = "undone"
        counts[outcome[a]] += 1
        if trace is not None:
            if a in replace:
                after = " ".join(tokens[lo:a] + replace[a][1] + tokens[b:hi])
            else:
                after = "".join(t + ("" if join_after[k] or k == hi - 1 else " ")
                                for k, t in enumerate(tokens[lo:hi], lo))
            trace.append((outcome[a], " ".join(tokens[lo:hi]), after))
    out, i = [], 0
    while i < len(tokens):
        if i in replace:
            b, words = replace[i]
            out.extend(words)
            i = b
            continue
        tok = tokens[i]
        while join_after[i] and i + 1 < len(tokens) and i + 1 not in replace:
            i += 1
            tok += tokens[i]
        out.append(tok)
        i += 1
    return out, counts


def _is_word(piece: str, lang: str | None) -> bool:
    """A known word of >= 2 letters. One-letter pieces never count: wordfreq lists every
    letter, and letters are what letter-spaced runs are made of."""
    bare = _bare(piece)
    return len(bare) >= 2 and bool(_WORD.match(bare)) and quality.known(bare, lang)


def _veto(tokens, join_after, replace, lang) -> None:
    """Undo a gap join whose pieces are all words of >= 2 letters by themselves and whose
    result is not one ("ni fosa" with a 1.2 pt gap is not "nifosa"): a tiny gap between
    real words is a typesetting quirk the geometry cannot tell from letter-spacing. A join
    with a one-letter piece is never undone."""
    i = 0
    while i < len(tokens):
        j = i
        while join_after[j] and j + 1 < len(tokens) and j + 1 not in replace:
            j += 1
        if j > i:
            chain = tokens[i:j + 1]
            joined = _bare("".join(chain))
            if (not (_WORD.match(joined) and quality.known(joined, lang))
                    and all(_is_word(t, lang) for t in chain)):
                for k in range(i, j):
                    join_after[k] = False
        i = j + 1


def msplit(tokens: list[str], gaps: list[float | None], lang: str | None) -> tuple[list[str], int]:
    """The font glitch that puts a space after "m" inside words ("m ovim iento", "hom bres",
    "cósm ico"): tokens linked by gaps in the line's small cluster join when they make one
    real word (dict_join). Returns (tokens, joins)."""
    known_gaps = [g for g in gaps if g is not None]
    cut = split_gaps(known_gaps) if len(known_gaps) >= 2 else None
    if cut is None:
        return tokens, 0
    out, joins, i, n = [], 0, 0, len(tokens)
    while i < n:
        j = i
        while j + 1 < n and gaps[j] is not None and gaps[j] < cut:
            j += 1
        best = None
        for end in range(j, i, -1):                     # the longest real word from i
            if dict_join(tokens[i:end + 1], lang):
                best = end
                break
        if best is None:
            out.append(tokens[i])
            i += 1
            continue
        out.append("".join(tokens[i:best + 1]))
        joins += best - i
        i = best + 1
    return out, joins


# ---------------------------------------------------------------------------- notes, markers
_NOTE_START = re.compile(r"^(?:(\d{1,3})\.?|(\*{1,3})|([?!¡¿$%&§])(\d{0,2}))(?=\s)")
_LOW = "a-zà-öø-ÿ"
_P = re.escape(_CLOSE)
_GLUED = re.compile(rf"(?:(?<=[{_LOW}])|(?<=[{_LOW}][{_P}])|(?<=[{_LOW}][{_P}][{_P}]))"
                    rf"(\d{{1,3}})(?=[\s{_P}]|$)")
_ABBREV_BEFORE = re.compile(r"(?:^|[\s(])(?:p|pp|n|nº|vol|t|cap|núm|no|fol|f|cf|ed)\.\s*$", re.I)


def note_start(text: str) -> tuple[int | None, str, bool] | None:
    """The marker a footnote starts with: (number, marker, misread). "24. B. Brecht" ->
    (24, "24.", False); "* Véase" -> (None, "*", False); "?1 El" -> (1, "?1", True): an
    OCR-misread marker whose digits are only the last ones of the number ("$8", "%": the
    digits lost, (None, "%", True))."""
    m = _NOTE_START.match(text)
    if not m:
        return None
    if m.group(1):
        return int(m.group(1)), m.group(0), False
    if m.group(2):
        return None, m.group(2), False
    return (int(m.group(4)) if m.group(4) else None), m.group(0), True


def link_markers(text: str, numbers: set[int]) -> tuple[str, int, int]:
    """Digits glued after a lowercase word or closing punctuation ("vanguardias1",
    "pienso.”45", "estado26—") become "[^n]" when a note numbered n is on the page or the
    next one. Returns (text, linked, left as they are)."""
    linked = left = 0

    def fix(m):
        nonlocal linked, left
        if _ABBREV_BEFORE.search(text[:m.start()]):
            return m.group()
        n = int(m.group(1))
        if n in numbers:
            linked += 1
            return f"[^{n}]"
        left += 1
        return m.group()

    return _GLUED.sub(fix, text), linked, left


# ---------------------------------------------------------------------------- line kinds
_SPEAKER = re.compile(r"^[A-ZÁÉÍÓÚÑÜ][^\s:]{0,24}(?: [^\s:]{1,24}){0,2}"
                      r"(?: \([^)]{0,120}\))?:(?:\s|$)")
_ORNAMENT = re.compile(r"^[\s*·•⁂∗✱✳❋※]+$")
_URL = re.compile(r"https?://|www\.", re.I)
_INDEX_HEAD = re.compile(r"^\s*(?:índice|indice|index|contents|sumario)\b", re.I)
_ENDS_NUMBER = re.compile(r"(?:\d{1,4}|[ivxlcdm]{1,6})\s*$")


def speaker(text: str) -> bool:
    """A drama speaker label at the start of a line ("GALÁN:", "Miranda:", "REINA BIZANTINA
    (moviendo los brazos):", "Una voz remota:")."""
    return bool(_SPEAKER.match(text))


def ornament(text: str) -> bool:
    """"*****", "* * *", "⁂": an ornament between sections."""
    marks = "".join(text.split())
    return bool(_ORNAMENT.match(text)) and (len(marks) >= 3 or marks in ("⁂", "❋", "※"))


def url(text: str) -> bool:
    return bool(_URL.search(text))


def index_heading(text: str) -> bool:
    return bool(_INDEX_HEAD.match(text)) and len(text.split()) <= 3


def ends_in_number(text: str) -> bool:
    return bool(_ENDS_NUMBER.search(text))


# ---------------------------------------------------------------------------- dot leaders
_LEADER = re.compile(r"\s*(?:\.\s*){4,}[.oO0cmnrsae»«:;,'·\s]*?\s*(\d{1,4})?\s*$")
_LEADER_ANY = re.compile(r"(?:\.\s*){4,}")


def has_leader(text: str) -> bool:
    return bool(_LEADER_ANY.search(text))


def leaders(text: str) -> str:
    """A dot leader and the OCR junk after it ("........o.ooooomommmoommm....") normalised
    to " … N" (N the page number, if any)."""
    m = _LEADER.search(text)
    if not m:
        return text
    head = text[:m.start()].rstrip()
    return f"{head} … {m.group(1)}" if m.group(1) else f"{head} …"


# ---------------------------------------------------------------------------- scan artefacts
_SYMBOLS = set("_~|-—–=.,:;'\"/\\«»“”‘’`")
_LEAD_MARK = re.compile(r"^[_~|] +")
_TRAIL_MARK = re.compile(r" +[_~|]$")
_UNDERSCORE = re.compile(rf"(?<={_L})_(?={_L})")
_REPORTED = re.compile(rf"\b{_L}+\.{_L}\b|\b{_L}+-{_L}-{_L}+\b")


def symbol_only(text: str) -> bool:
    """A line of pencil marks: only underscores, tildes, bars, dashes, dots and quotes."""
    t = "".join(text.split())
    return 0 < len(t) <= SYMBOL_LINE_MAX and all(c in _SYMBOLS for c in t)


def edge_mark(token: str) -> bool:
    """A pencil mark read as a token at a line's edge: "_", "~", "|"."""
    return token in ("_", "~", "|")


def underscores(word: str) -> tuple[str, int]:
    """"_" between two letters is a space ("may_be" -> "may be")."""
    return _UNDERSCORE.subn(" ", word)


def reported(text: str) -> list[str]:
    """"as.a", "also-a-protagonist": reported, never changed."""
    return [m.group() for m in _REPORTED.finditer(text)]


def artefacts(text: str) -> tuple[str, dict, list[str]]:
    """Unruh's pencil underlines read into the text: a leading "_ "/"~ "/"| " or trailing
    " _"/" ~"/" |" mark dropped, "_" between two letters a space ("may_be"). Cases such as
    "as.a" or "also-a-protagonist" are only reported. Em dashes are left alone (Unruh's own
    style). Returns (text, counts, reported)."""
    counts = {"edge_mark": 0, "underscore": 0}
    t, n = _LEAD_MARK.subn("", text)
    counts["edge_mark"] += n
    t, n = _TRAIL_MARK.subn("", t)
    counts["edge_mark"] += n
    t, n = underscores(t)
    counts["underscore"] += n
    return t, counts, reported(t)
