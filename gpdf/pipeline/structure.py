# SPDX-License-Identifier: AGPL-3.0-or-later
"""gpdf-cli structure: where each part, section and text of an exported entry starts and
ends, who wrote it and what role it plays, so the search app can cite "Naín Nómez,
«Pablo de Rokha...», p. 607" and keep bibliographies out of semantic search.

Inputs (read only): the export folder (<stem>.jsonl units, <stem>.pages.jsonl, <stem>.
apparatus.jsonl, export.json) and the working PDF's outline, page count and metadata.
Outputs: OUTDIR/<stem>.sections.json per entry and OUTDIR/structure.json (run log).

Evidence, in order of trust: the owner's decisions (part B), contents and index pages,
bookmarks (claims verified against the running heads and the page's units, re-anchored
or dropped), running-head runs, heading units and the first units at a page. A section
starts before a unit, never at a page: every unit belongs to exactly one section.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher

SCHEMA = 1
RUN_LOG = "structure.json"
SECTIONS_SUFFIX = ".sections.json"
ROLES = ("front", "editorial", "primary", "criticism", "notes", "bibliography", "index",
         "back", "other")
HIGH, MEDIUM, LOW = "high", "medium", "low"

WORD_MIN = 4                # letters: a title word that counts for matching
WORD_RATIO = 0.85           # fuzzy ratio per word
HEAD_LOOKAHEAD = 3          # pages after a bookmark's page where its words may be in a head
JUNK_PER_PAGE = 0.8         # bookmarks per page: a one-per-page outline is junk


# ---------------------------------------------------------------------------- text keys
def fold(text: str) -> str:
    """Casefolded, accents removed, spaces collapsed."""
    d = unicodedata.normalize("NFD", text.casefold())
    return " ".join("".join(c for c in d if not unicodedata.combining(c)).split())


def ratio(a: str, b: str) -> float:
    fa, fb = fold(a), fold(b)
    if not fa or not fb:
        return 0.0
    return SequenceMatcher(None, fa, fb).ratio()


_WORD = re.compile(r"[^\W\d_]+")


def words(text: str, min_len: int = WORD_MIN) -> list[str]:
    return [w for w in _WORD.findall(fold(text)) if len(w) >= min_len]


def word_in(word: str, haystack: list[str]) -> bool:
    return any(w == word or (abs(len(w) - len(word)) <= 2
                             and SequenceMatcher(None, w, word).ratio() >= WORD_RATIO)
               for w in haystack)


def title_match(title: str, text: str) -> bool:
    """>= 2 of the first 4 words of length >= 4 of ``title`` appear in ``text`` (fuzzy per
    word); a title with fewer such words needs all of them; the test runs both ways so
    "Rosa náutica" matches "Movimiento vanguardista chileno: Rosa náutica"."""
    tw, hw = words(title)[:4], words(text)
    if not tw or not hw:
        return False
    need = min(2, len(tw))
    if sum(word_in(w, hw) for w in tw) >= need:
        return True
    hw4 = hw[:4]
    need = min(2, len(hw4))
    return sum(word_in(w, tw) for w in hw4) >= need and len(hw) <= 6


_PARTICLES = {"de", "del", "la", "las", "los", "y", "e", "van", "von", "da", "das", "dos",
              "do", "di", "le", "du", "der", "/", "-"}
_INITIAL = re.compile(r"^[A-ZÁÉÍÓÚÑÜ]\.?$")


def name_like(text: str) -> bool:
    """A person's name (or two): 2-6 tokens, each a capitalised word, an initial or a
    particle; no digits, no colon, no sentence end."""
    t = text.strip().rstrip(".").replace("[", " ").replace("]", " ")
    if not t or ":" in t or any(c.isdigit() for c in t) or t.endswith((",", ";")):
        return False
    toks = [p for tok in t.split() for p in re.split(r"(/)", tok) if p]
    if not 2 <= len(toks) <= 7:
        return False
    caps = 0
    for tok in toks:
        if tok in _PARTICLES or tok.lower() in _PARTICLES or tok == ".":
            continue
        core = tok.strip("'’\"“”()")
        if not core:
            return False
        if _INITIAL.match(core):
            caps += 1
            continue
        parts = [p for p in re.split(r"[-/]", core) if p]
        if not parts or not all(p[0].isupper() for p in parts):
            return False
        caps += 1
    return caps >= 2 and len(t) <= 60


# ---------------------------------------------------------------------------- contents
@dataclass
class ContentsEntry:
    title: str
    author: str | None
    label: str | None             # the printed page as written ("257", "xi"); None: missing
    level: int                    # relative nesting within the page (1 = top)
    header: bool = False          # an unnumbered header line (a part or block name)
    line: int = 0                 # first source line (0-based)
    page: int | None = None       # the PDF page, once resolved
    comma: bool = False           # written "Title, N" (a text listed under a section)
    inline: bool = False          # one item of an inline list ("Acto I, 5.—Acto II, 35")
    bullet: bool = False          # written "- Title N" (a leaf under header lines)


_INDEX_HEAD = re.compile(r"^\W*(í|i)ndice( general)?\W*$|^\W*(sumario|contents|index|"
                         r"table of contents|tabla de contenido)\W*$", re.I)
_NUM_ONLY = re.compile(r"^\s*(\d{1,4}|[ivxlc]{1,8}|[IVXLC]{1,8})\s*$")
_SPACED = re.compile(r"(?<![\d\s])\s(\d(?:\s\d){1,3})\s*$")
_TRAIL = re.compile(r"^(?P<title>.*?)(?P<lead>\s*(?:[…·]|\.\s*){1,}\s*|\s+|,\s*)"
                    r"(?P<num>\d{1,4}|[ivxlc]{1,8}|[IVXLC]{1,8})\s*[-+xX*.]?\s*$")
_LEADER = re.compile(r"(?:\.\s*){3,}|…")
_ORDINAL = re.compile(r"^(?P<n>\d{1,2}|[ivx]{1,5}|[IVX]{1,5}|ni|nl|lll|ll|il)\.\s+(?P<rest>.+)$")
_INLINE_SEP = re.compile(r"\s*\.\s*[—–]+\s*(?=\S)|\s*\.\s*-[–-]+\s*(?=[A-ZÁÉÍÓÚÑ“(])"
                         r"|\s*[—–]{1,2}\s*(?=[A-ZÁÉÍÓÚÑ“(])")
_AFTER_NUM = re.compile(r"(?<=\d)\s*[;,]\s+(?!\d)")
_QUOTED = re.compile(r"^[“\"](?P<title>[^”\"]+)[”\"]\s*(?:\[(?P<author>[^\]]+)\])?\s*$")
_COLON = re.compile(r"^(?P<author>[^:“”\"]+?):\s*(?P<title>.+)$")


def _strip_junk(title: str) -> str:
    """Trailing OCR junk on a contents line: a single capital that is no roman numeral,
    a leader, loose punctuation."""
    t = title.strip()
    t = re.sub(r"(?:…|(?:\.\s*){3,})\s*$", "", t).strip()
    toks = t.split()
    while toks and (re.fullmatch(r"[A-Z]", toks[-1]) and toks[-1] not in "IVX"
                    or (len(toks) > 1 and re.fullmatch(r"[o0c.«»+]+|[ocnmrsae0.]{6,}",
                                                       toks[-1]))):
        toks.pop()                                    # "E", "0...", "ooooccorcnancaa"
    t = " ".join(toks).strip(" .:,;·-–—>")
    if t.startswith("“") and "”" not in t:
        t = t[1:]                                     # an opening quote the OCR left alone
    return t


def _number_at_end(line: str) -> tuple[str, str | None, bool, bool]:
    """(title, number, comma, had_leader) of a contents line ending in a page number."""
    s = line.strip()
    m = _SPACED.search(s)
    if m:
        return _strip_junk(s[:m.start()]), m.group(1).replace(" ", ""), False, False
    m = _TRAIL.match(s)
    if not m:
        return s, None, False, False
    num, lead, title = m.group("num"), m.group("lead"), m.group("title")
    leader = bool(_LEADER.search(lead)) or bool(_LEADER.search(title[-6:]))
    if num.isalpha() and s.rstrip()[-1:] in "-­":
        return s, None, False, False       # "de la li-": a hyphenated word, not 51
    if num.isupper() and num.isalpha() and not leader and "," not in lead:
        return s, None, False, False       # "ACTO I": a roman word, not a page number
    if not title.strip():
        return s, None, False, False       # the line is only a number
    return _strip_junk(title), num, lead.strip().startswith(","), leader


def _join(a: str, b: str) -> str:
    if a.endswith(("-", "­")):
        return a[:-1] + b.lstrip()
    return (a + " " + b).strip()


def _inline_ish(line: str) -> bool:
    s = line.strip()
    if _INLINE_SEP.search(s):
        return True
    return not _lost_number(s) and (s.endswith(";") or "; " in s)


def _parse_item(text: str, authored: bool, carry: str | None,
                standalone: bool = False) -> tuple[str, str | None, str | None]:
    """(title, author, number) of one inline item: "Author: “Title”, N", "“Title”
    [Author], N", "Title, N", "Title". An unquoted "Name: Title" names an author only in
    a list of authored texts or on a line of its own (``standalone``)."""
    t = text.strip().strip(",;").strip()
    t = re.sub(r"[\s,]*\.$", "", t).strip()
    title, num, _, _ = _number_at_end(t)
    if num is None:
        title = _strip_junk(t)
    author = None
    m = _QUOTED.match(title)
    if m:
        return m.group("title").strip(), (m.group("author") or carry or None), num
    m = _COLON.match(title)
    if m and (authored or (standalone and name_like(m.group("author")))):
        a, rest = m.group("author").strip(), m.group("title").strip()
        q = _QUOTED.match(rest)
        return (q.group("title") if q else rest).strip(), a, num
    m = re.match(r"^(?P<title>.+?)\s*\[(?P<author>[^\]]+)\]$", title)
    if m:
        return m.group("title").strip(), m.group("author").strip(), num
    if re.fullmatch(r"[“\"][^“”\"]*[”\"]", title):
        title = title[1:-1]
    return title.strip(), (carry if authored else None), num


def _authored(item: str) -> bool:
    """The first item of an inline list reads "Author: “Title”, N": the list is of texts
    by named authors, and later items without a name keep the last one."""
    m = _COLON.match(_number_at_end(item.strip().strip(",;"))[0])
    return bool(m) and bool(_QUOTED.match(m.group("title").strip()))


def _split_inline(text: str) -> list[str]:
    if _INLINE_SEP.search(text):
        parts = _INLINE_SEP.split(text)
    else:
        parts = _AFTER_NUM.split(text)
    return [p.strip() for p in parts if p and p.strip(" .,;")]


def _numbered_prefix(title: str) -> tuple[str, int | None]:
    """("arabic"|"roman", value) of a "1." / "I." / "ni." prefix, else ("", None)."""
    m = _ORDINAL.match(title)
    if not m:
        return "", None
    n = m.group("n")
    if n.isdigit():
        return "arabic", int(n)
    low = n.lower()
    fake = {"ni": 3, "nl": 3, "lll": 3, "ll": 2, "il": 2}
    if low in fake:
        return "roman", fake[low]
    v = _roman(low)
    return ("roman", v) if v else ("", None)


_ROMAN_RE = re.compile(r"^m{0,3}(cm|cd|d?c{0,3})(xc|xl|l?x{0,3})(ix|iv|v?i{0,3})$")
_ROMAN_VALUES = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}


def _roman(token: str) -> int | None:
    t = token.lower()
    if not t or not _ROMAN_RE.match(t):
        return None
    total, prev = 0, 0
    for c in reversed(t):
        v = _ROMAN_VALUES[c]
        total, prev = (total - v, prev) if v < prev else (total + v, v)
    return total or None


def _is_header(text: str, headers: set[str] | None) -> bool:
    if not headers:
        return False
    f = fold(text)
    return any(f == h or ratio(f, h) >= 0.9 for h in headers)


def _shares_pattern(a: str, b: str) -> bool:
    """Two sub-list titles of one kind: both numerals, or sharing a long word."""
    if a.strip().isdigit() and b.strip().isdigit():
        return True
    wa, wb = set(words(a)), set(words(b))
    return bool(wa & wb)


def parse_contents(lines: list[str], columns: int = 1, headers: set[str] | None = None,
                   heads: set[str] | None = None) -> list[ContentsEntry]:
    """The entries of one contents or index page. ``headers``: folded part and block names
    known from bookmarks (an unnumbered line equal to one is a header, never a title
    continuation). Two-column pages (titles first, numbers after) are paired only when
    the entry and number counts match, else the page yields nothing."""
    raw = [s.strip() for s in lines if s and s.strip()]
    for k, s in enumerate(raw[:4]):
        if _INDEX_HEAD.match(s):
            raw = raw[k + 1:]                         # the page title (and a book title
            break                                     # above it on a first page)
    headers = {fold(h) for h in headers} if headers else set()
    numbers = [s for s in raw if _NUM_ONLY.match(s)]
    if columns == 2 or (len(numbers) >= 4 and raw[-len(numbers):] == numbers):
        texts = [s for s in raw if not _NUM_ONLY.match(s)]
        entries = _group(texts, headers, with_numbers=False)
        slots = [e for e in entries if not e.header]
        if len(slots) != len(numbers):
            return []
        for e, n in zip(slots, numbers):
            e.label = n
        return _levels(entries)
    return _levels(_group(raw, headers, with_numbers=True))


def _unspace(line: str) -> str:
    """A letter-spaced line ("p r o l o g o , p o r Nelson", "X X X IX") joined."""
    toks = line.split()
    if len(toks) >= 3 and sum(len(t) == 1 for t in toks) >= 0.7 * len(toks):
        if all(re.fullmatch(r"[IVXLCivxlc]+", t) for t in toks):
            return "".join(toks)
        out, run = [], []
        for t in toks + [""]:
            if len(t) == 1 and t.isalnum():
                run.append(t)
            else:
                if run:
                    out.append("".join(run))
                    run = []
                if t:
                    out.append(t)
        return " ".join(out).replace(" ,", ",")
    return line


_CONNECTORS = {"y", "e", "o", "u", "de", "del", "en", "a", "and", "of", "the", "for", "to",
               "in", "or", "con", "por", "para", "sobre", "entre", "desde", "hasta", "sin",
               "según"}
_ARTICLES = {"las", "los", "la", "el", "un", "una", "lo"}


def _wrapped(first: str, nxt: str) -> bool:
    """``nxt`` continues ``first``: a connector (or a lowercase article) opens it, or
    ``first`` ends open."""
    toks = nxt.split()
    head = toks[0].strip("“\"(") if toks else ""
    return (head.lower() in _CONNECTORS or (head.islower() and head in _ARTICLES)
            or (len(toks) > 1 and head in ("I", "E", "O"))      # "Y" misread as "I"
            or first.rstrip().endswith((":", ",", "-", "­"))
            or first.split()[-1].lower() in _CONNECTORS)


def _author_ish(text: str) -> bool:
    """On an author-style page (ALL-CAPS titles, mixed-case author lines) a short
    mixed-case line is an author even when the OCR damaged a name ("fosé Antonio")."""
    toks = text.split()
    return (name_like(text) or (1 <= len(toks) <= 4 and not text.isupper()
                                and toks[0].lower() not in _CONNECTORS
                                and not any(c.isdigit() for c in text)))


_BY = re.compile(r"^(?P<title>.+?),\s+(?:de|por|by)\s+(?P<author>[A-ZÁÉÍÓÚÑ][^,;:]+)$")


def _by_author(title: str) -> tuple[str, str | None]:
    """"Prólogo: Perfiles encontrados, de Helio Piñón" -> (title, author)."""
    m = _BY.match(title)
    if m and name_like(m.group("author")) and not re.search(r"[\[\]“”\"]", title):
        return m.group("title").strip(" ,"), m.group("author").strip()
    return title, None


_GLUED_AUTHOR = re.compile(r"^(?P<t>.*?[a-záéíóúñü)\]”'’.])\s+(?P<a>[A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ .'’-]{3,})$")


def _lost_number(line: str) -> bool:
    """A contents line whose page number the OCR lost: it ends in a leader remnant."""
    return bool(re.search(r"(\s\.|\.{2,}|…|\so+\.*|\s[.o]+|\s[ocmn0.]{3,})\s*$",
                          line.rstrip(">-– ")))


def _ordinal_only(line: str) -> bool:
    return bool(re.fullmatch(r"(\d{1,2}|[IVX]{1,5})\.", line.strip()))


def _group(raw: list[str], headers: set[str], with_numbers: bool) -> list[ContentsEntry]:
    """Lines into entries: a title (possibly wrapped) with its number on the same line, on
    a following "Author N" line, or (two columns) assigned later; a name-like unnumbered
    line after a numbered entry is that entry's author; inline lists become items."""
    raw = [_unspace(s) for s in raw]
    out: list[ContentsEntry] = []
    pending: list[str] = []     # title lines waiting for their number
    pending_at = 0
    last_arabic = 0
    # author style: ALL-CAPS titles, each followed by a mixed-case "Author N" line (or,
    # in two columns, a mixed-case name line)
    pairs = [(a, b) for a, b in zip(raw, raw[1:])
             if a.isupper() and _number_at_end(a)[1] is None and not _NUM_ONLY.match(b)
             and (_number_at_end(b)[1] or not with_numbers)]
    author_style = len(pairs) >= 3 and sum(
        _author_ish(_number_at_end(b)[0]) and not b.isupper() for _, b in pairs
    ) >= 0.6 * len(pairs)
    # ALL-CAPS author lines ("FRANCINE MASIELLO 173"): a caps run glued to the end of a
    # mixed-case title is the author ("... instalaciones ANACAMBLONG 295")
    caps_authors = sum(1 for b in raw if _number_at_end(b)[1] and _number_at_end(b)[0].isupper()
                       and name_like(_number_at_end(b)[0])) >= 3

    def joined() -> str:
        full = pending[0]
        for p in pending[1:]:
            full = _join(full, p)
        return _strip_junk(full)

    def flush(author=None, num=None, comma=False, extra=None, bullet=False):
        """The pending lines become entries: leading short or ALL-CAPS lines followed by a
        differently cased line are headers or lost-number entries of their own; the rest
        is one (wrapped) title. ``extra``: the numbered line's own text, the last line of
        the title unless it is a bullet ("- Antologías 3") under header lines."""
        nonlocal pending
        if extra and bullet:
            for k, h in enumerate(pending):
                out.append(ContentsEntry(_strip_junk(h), None, None, 1, header=True,
                                         line=pending_at + k))
            pending = []
            out.append(ContentsEntry(_strip_junk(extra), None, num, 0, line=i, bullet=True))
            return
        if extra:
            pending = pending + [extra]
        while len(pending) > 1:
            first, nxt = pending[0], pending[1]
            nxt_cap = next((c.isupper() for c in nxt if c.isalpha()), False)
            short = (len(first.split()) <= 3 and first[:1].isupper() and nxt_cap
                     and not first.isupper() and not _wrapped(first, nxt))
            caps = first.isupper() != nxt.isupper() and not _wrapped(first, nxt)
            two_caps = first.isupper() and nxt.isupper() and not _wrapped(first, nxt)
            if _is_header(first, headers) or short:
                out.append(ContentsEntry(_strip_junk(first), None, None, 1, header=True,
                                         line=pending_at))
            elif caps or two_caps or _lost_number(first) or _ordinal_only(nxt):
                out.append(ContentsEntry(_strip_junk(first), None, None, 0, line=pending_at))
            else:
                break
            pending = pending[1:]
        if pending:
            title = joined()
            if title and author is None:
                title, author = _by_author(title)
            if title:
                out.append(ContentsEntry(title, author, num, 0, line=pending_at, comma=comma))
        pending = []

    i = 0
    while i < len(raw):
        s = raw[i]
        if _NUM_ONLY.match(s):
            if pending and pending[-1].rstrip().endswith(","):
                pending[-1] = pending[-1].rstrip() + " " + s     # the number wrapped
                t, author, n = _parse_item(joined(), False, None, standalone=True)
                out.append(ContentsEntry(t, author, n, 0, line=pending_at, comma=True))
                pending = []
            i += 1                                   # else an orphan: its title is lost
            continue
        title, num, comma, leader = _number_at_end(s)
        if (num is not None and num.isalpha() and num.islower() and not leader
                and last_arabic and (_roman(num) or 0) < last_arabic):
            title, num, comma = s, None, False            # "siglo xx": a word, not p. 20
        if (num is not None and num.isdigit() and not leader and not comma
                and (1500 <= int(num) <= 2100 and last_arabic < 1000
                     or len(num) <= 2 and int(num) < last_arabic / 2)):
            if not re.search(r"\s\.\s*\d+\s*$", s):      # "Benjamin . 11": OCR junk
                title = s                                 # "Proa en 1924", "los años 20"
            s, num = title, None
        if num is not None and title.strip().isdigit() and not leader:
            i += 1                                        # "17 206": junk
            continue
        bullet = s.startswith(("- ", "– ", "— "))
        if num is not None and num.isdigit():
            last_arabic = int(num)
        if bullet:
            title = title.lstrip("-–— ")
            if num is not None:
                flush(num=num, extra=title, bullet=True)
                i += 1
                continue
        if num is None and _is_header(s, headers):
            flush()
            out.append(ContentsEntry(_strip_junk(s), None, None, 1, header=True, line=i))
            i += 1
            continue
        if _inline_ish(s) or (num is not None and comma and pending and
                              pending[-1].rstrip().endswith((",", ":", "“"))):
            block, start = [], i
            if (pending and pending[-1].rstrip().endswith((",", ":", "“", "-"))
                    and not _lost_number(pending[-1])):
                block, start = list(pending), pending_at      # the list began on that line
                pending = []
            else:
                flush()
            block.append(s)
            i += 1
            while i < len(raw):
                nxt, prev = raw[i], block[-1].rstrip()
                if _NUM_ONLY.match(nxt):
                    if prev.endswith(","):
                        block[-1] = prev + " " + nxt           # "“Inicial”," then "249"
                        i += 1
                        continue
                    break
                open_ = prev.endswith((",", ":", "“", "-", "y"))
                unfinished = _number_at_end(prev)[1] is None and not prev.endswith(".")
                if _is_header(nxt, headers) or _ordinal_only(nxt) or _LEADER.search(nxt):
                    break
                if (open_ or _inline_ish(nxt)
                        or (unfinished and not _lost_number(prev))
                        or (nxt[:1].islower() and not _number_at_end(nxt)[1])
                        or (nxt[:1].isdigit() and prev.endswith(","))):
                    block.append(nxt)
                    i += 1
                else:
                    break
            text = block[0]
            for b in block[1:]:
                text = _join(text, b)
            items = _split_inline(text)
            authored = bool(items) and _authored(items[0])
            carry = None
            for it in items:
                t, author, n = _parse_item(it, authored, carry, len(items) == 1)
                if author:
                    carry = author
                if t:
                    out.append(ContentsEntry(t, author, n, 0, line=start, comma=True,
                                             inline=len(items) > 1))
            continue
        if num is None:
            prev_title = pending[-1] if pending else (out[-1].title if out else "")
            initials = all(_INITIAL.match(t) for t in s.split())
            caps_wrap = s.isupper() and prev_title.isupper() and not initials
            if author_style and s.isupper() and not initials:
                caps_wrap = True                     # titles are ALL CAPS on this page
            if (not pending and out and not out[-1].header and out[-1].author is None
                    and not out[-1].comma and name_like(s) and not caps_wrap
                    and not (s.isupper() and (author_style or any(
                        e.title.isupper() for e in out)))):
                out[-1].author = s.strip()
            elif pending and not with_numbers and name_like(s) and not caps_wrap:
                flush(author=s.strip())
            else:
                if pending and (_lost_number(pending[-1]) or _ordinal_only(s)
                                or _numbered_prefix(s)[0]):
                    flush()
                if not pending:
                    pending_at = i
                if pending and _ordinal_only(pending[-1]):
                    pending[-1] = pending[-1] + " " + s
                else:
                    pending.append(s)
            i += 1
            continue
        if pending and (_numbered_prefix(title)[0] or _lost_number(pending[-1])):
            flush()                                   # the pending entry stands on its own
        if pending and (name_like(title) or not title or (
                author_style and pending[-1].isupper() and _author_ish(title)) or (
                caps_authors and re.fullmatch(r"[A-ZÁÉÍÓÚÑ]{5,}", title))) and not (
                title.isupper() and pending[-1].isupper()):
            flush(author=title.strip() or None, num=num, comma=comma)
        elif pending and pending[-1].isupper() and title.isupper() and not _wrapped(
                pending[-1], title) and len(pending[-1].split()) <= 3:
            flush()                                   # "ARGENTINA" then "BUENOS AIRES 111"
            out.append(ContentsEntry(title, None, num, 0, line=i, comma=comma))
        elif pending and pending[-1].isupper() and not title.isupper() and not author_style:
            for k, h in enumerate(pending):           # "MANIFIESTOS" then "Chile 95"
                out.append(ContentsEntry(_strip_junk(h), None, None, 1, header=True,
                                         line=pending_at + k))
            pending = []
            out.append(ContentsEntry(title, None, num, 0, line=i, comma=comma))
        elif pending and comma:
            pending.append(title)
            t, author, _ = _parse_item(joined(), False, None, standalone=True)
            out.append(ContentsEntry(t, author, num, 0, line=pending_at, comma=True))
            pending = []
        elif pending:
            flush(num=num, comma=comma, extra=title)
        elif caps_authors and (m := _GLUED_AUTHOR.match(title)):
            out.append(ContentsEntry(_strip_junk(m.group("t")), m.group("a").strip(), num, 0,
                                     line=i, comma=comma))
        elif comma:                                   # "“Klaxon” [Mário de Andrade], 262"
            t, author, _ = _parse_item(s, False, None, standalone=True)
            out.append(ContentsEntry(t, author, num, 0, line=i, comma=True))
        else:
            title, author = _by_author(title)
            out.append(ContentsEntry(title, author, num, 0, line=i, comma=comma))
        i += 1
    if pending:
        flush()
    return out


def _levels(entries: list[ContentsEntry]) -> list[ContentsEntry]:
    """Relative levels: headers 1 (nested headers +1); entries under a header one deeper;
    mixed-case entries one deeper than ALL-CAPS ones on the same page; "Title, N" items
    and inline lists one deeper than the entry before them; a same-page sub-list, a
    numbered "N." sub-list and the sub-chapters of a roman-numbered chapter one deeper
    than their parent."""
    plain = [e for e in entries if not e.header and not e.comma]
    caps = [e for e in plain if e.title.isupper() and len(e.title) > 2]
    mixed = [e for e in plain if not e.title.isupper()]
    split_case = (len(caps) >= 2 and len(mixed) >= 2
                  and not any(e.author for e in plain))        # not a list of texts
    header_level = 0
    last_header = False
    entry_level = 1
    parent: ContentsEntry | None = None       # the entry a sub-list hangs from
    sub_kind = ""                              # "page" | "numbered" | "roman"
    sub_first: ContentsEntry | None = None
    last_num = 0
    last_entry: ContentsEntry | None = None
    last_comma: ContentsEntry | None = None
    for e in entries:
        if e.header:
            header_level = header_level + 1 if last_header else 1
            e.level = min(3, header_level)
            last_header = True
            parent, sub_kind, sub_first, last_entry = None, "", None, None
            continue
        last_header = False
        base = header_level + 1 if header_level else 1
        if e.comma:
            above = last_entry.level if last_entry else base
            if e.inline and last_comma is not None:
                above = last_comma.level          # "Ariel, 131." then "Su nombre, .—..."
            e.level = min(3, above + 1)
            if not e.inline:
                last_comma = e
            continue
        last_comma = None
        if split_case and not e.title.isupper():
            base = max(base, 2)
        kind, n = _numbered_prefix(e.title)
        level = base
        if e.bullet:
            parent, sub_kind, sub_first = None, "", None
        if parent is not None:
            same_page = e.label is not None and e.label == parent.label
            if sub_first is None and same_page:
                sub_kind = "numbered" if kind == "arabic" else "page"
                if _numbered_prefix(parent.title)[0] == "roman":
                    sub_kind = "roman"
                sub_first, last_num, level = e, n or 0, parent.level + 1
            elif sub_first is not None:
                goes_on = False
                if kind == "arabic" and n is not None:
                    goes_on = n > last_num
                    if goes_on:
                        last_num = n
                elif kind == "roman" and sub_kind == "numbered" and n == last_num + 1:
                    goes_on, last_num = True, n
                elif kind == "":
                    goes_on = (_shares_pattern(sub_first.title, e.title)
                               or (sub_kind == "roman" and e.label != parent.label)
                               or (sub_kind == "numbered" and sub_first.title.strip().isdigit()
                                   and e.title.strip().isdigit()))
                    if sub_kind == "page" and e.label is not None and e.label == parent.label:
                        goes_on = True
                if goes_on:
                    level = parent.level + 1
                else:
                    parent, sub_kind, sub_first = None, "", None
        if level == base:
            parent, sub_kind, sub_first = e, "", None
            if kind == "roman":
                sub_kind = "roman"
        e.level = min(3, level)
        last_entry = e
    return entries


def is_name_index(lines: list[str]) -> bool:
    """A back-of-book index of names: many lines carry several page numbers."""
    texts = [s for s in lines if s.strip()]
    if not texts:
        return False
    if texts and re.match(r"^\W*(í|i)ndice onom", texts[0], re.I):
        return True
    multi = sum(bool(re.search(r"\d{1,4}(?:n+\d+)?,\s*\d{1,4}\b|\b\d{1,4}[-–]\d{1,4}\b", s))
                for s in texts)
    return multi >= 2 and multi >= 0.25 * len(texts)


# ---------------------------------------------------------------------------- labels
class LabelMap:
    """Printed page label -> PDF page(s), from the export's page rows (found or inferred
    labels, arabic or roman, compared accent- and case-folded)."""

    def __init__(self, rows: list[dict]):
        self.by_label: dict[str, list[int]] = {}
        self.kinds: dict[int, str] = {}
        self.rows = {r["page"]: r for r in rows if "page" in r}
        for r in rows:
            lab = r.get("label")
            if lab and r.get("label_status", "none") != "none":
                self.by_label.setdefault(fold(str(lab)), []).append(r["page"])

    def page(self, label: str | None, after: int | None = None) -> int | None:
        """The PDF page carrying ``label``; when several do, the first one after
        ``after`` (in sequence), else the last."""
        if label is None:
            return None
        pages = self.by_label.get(fold(str(label)))
        if not pages:
            return None
        if after is not None:
            later = [p for p in pages if p >= after]
            return later[0] if later else pages[-1]
        return pages[0]


# ---------------------------------------------------------------------------- claims
@dataclass
class Claim:
    """A claim that a section starts on a page, from one source."""
    title: str
    page: int
    source: str                      # bookmark / contents / head / heading
    level: int | None = None         # as the source gives it (outline level, contents level)
    author: str | None = None
    evidence: list[str] = field(default_factory=list)
    confidence: str = MEDIUM
    original_page: int | None = None
    key: str = ""                    # for head claims: the head's canonical key


@dataclass
class OutlineResult:
    confirmed: list[Claim] = field(default_factory=list)
    reanchored: list[tuple[Claim, int]] = field(default_factory=list)    # (claim, old page)
    unconfirmed: list[Claim] = field(default_factory=list)
    dropped: list[tuple[Claim, str]] = field(default_factory=list)
    junk: bool = False

    @property
    def kept(self) -> list[Claim]:
        return sorted(self.confirmed + self.unconfirmed, key=lambda c: c.page)


def outline_is_junk(toc: list, page_count: int) -> bool:
    """An outline whose titles differ only by a trailing number, or with >= JUNK_PER_PAGE
    entries per page, is one bookmark per page: ignored entirely."""
    if not toc:
        return False
    if page_count and len(toc) >= 4 and len(toc) / page_count >= JUNK_PER_PAGE:
        return True
    stems = {re.sub(r"\s*\d+\s*$", "", t[1]).strip().lower() for t in toc}
    return len(toc) >= 4 and len(stems) == 1


def _numerals(text: str) -> set[str]:
    """The numbers in a title, roman ones as values: "Canto IV" -> {"4"}."""
    out = set()
    for tok in re.findall(r"\b(?:\d{1,4}|[IVXLC]{1,6}|[ivxlc]{1,6})\b", text):
        if tok.isdigit():
            out.add(str(int(tok)))
        else:
            v = _roman(tok)
            if v:
                out.add(str(v))
    return out


def same_numerals(a: str, b: str) -> bool:
    na, nb = _numerals(a), _numerals(b)
    return not na or not nb or bool(na & nb)


def heads_of(rows: list[dict]) -> dict[int, list[str]]:
    """Page -> the running-head parts on it ("A | B" split), OCR-ish bits dropped."""
    out = {}
    for r in rows:
        h = r.get("head")
        if not h:
            continue
        parts = [p.strip(" *|") for p in str(h).split("|")]
        parts = [p for p in parts if len(fold(p)) >= 3]
        if parts:
            out[r["page"]] = parts
    return out


def units_on(units: list[dict]) -> dict[int, list[dict]]:
    """Page -> the units that start on it (reading order)."""
    out: dict[int, list[dict]] = {}
    for u in units:
        pg = (u.get("pages") or u.get("lines") or [1])[0]
        out.setdefault(pg, []).append(u)
    return out


def head_matches(title: str, head: str) -> bool:
    return (ratio(title, head) >= 0.8 or (title_match(title, head) and title_match(head, title))
            ) and same_numerals(title, head)


def _confirm(title: str, page: int, heads: dict, on: dict) -> tuple[bool, list[str], str | None]:
    """(confirmed, evidence, better title) for a claim at ``page``: the title's words in the
    heads of pages page..page+HEAD_LOOKAHEAD, or in the units on the page."""
    ev, better = [], None
    span = [q for q in range(page, page + HEAD_LOOKAHEAD + 1) if q in heads]
    head_text = " ".join(" ".join(heads[q]) for q in span)
    if head_text and title_match(title, head_text):
        ev.append(f"heads pp. {span[0]}-{span[-1]}" if len(span) > 1 else f"head p{span[0]}")
    texts = on.get(page, [])
    for u in texts:
        if u["kind"] == "heading" and ratio(title, u["text"]) >= 0.8:
            ev.append(f"heading unit on p{page}")
            if not same_numerals(title, u["text"]):
                better = u["text"].strip()          # "Canto VI" -> the heading "CANTO VII"
            break
    else:
        if texts and title_match(title, " ".join(u["text"][:300] for u in texts[:6])):
            ev.append(f"title words in the first units on p{page}")
    return bool(ev), ev, better


def _find_pages(title: str, heads: dict, on: dict, after: int) -> list[tuple[int, str]]:
    """Pages after ``after`` where a head or a short unit matches ``title``, as (page, how),
    the first page of each head run only."""
    found = []
    prev_hit = -9
    for q in sorted(heads):
        if q <= after:
            continue
        if any(head_matches(title, h) for h in heads[q]):
            if q > prev_hit + 2:
                found.append((q, "head"))
            prev_hit = q
    for q in sorted(on):
        if q <= after:
            continue
        for u in on[q]:
            if len(u["text"]) <= 120 and ratio(title, u["text"]) >= 0.85 and same_numerals(
                    title, u["text"]):
                found.append((q, "heading unit" if u["kind"] == "heading" else "unit"))
                break
    return sorted(found)


def verify_outline(toc: list, rows: list[dict], units: list[dict],
                   contents: list[ContentsEntry]) -> OutlineResult:
    """Bookmarks as claims to verify: confirmed when the title's words appear in the heads
    of the page and the next HEAD_LOOKAHEAD pages or in the page's units; else re-anchored
    to the contents page, head run or heading that carries the title (nearest in outline
    order); else dropped, unless the outline is otherwise verified and the page is in
    order (kept as a medium claim). Two bookmarks on one page: the grouping is dropped."""
    res = OutlineResult()
    page_count = len(rows)
    if outline_is_junk(toc, page_count):
        res.junk = True
        return res
    heads, on = heads_of(rows), units_on(units)
    claims = [Claim(t[1].strip(), int(t[2]), "bookmark", level=int(t[0]), original_page=int(t[2]))
              for t in toc if t[1] and str(t[1]).strip()]
    # same page: the one that verifies (or the later one, a title under a grouping) stays
    by_page: dict[int, list[Claim]] = {}
    for c in claims:
        by_page.setdefault(c.page, []).append(c)
    grouped = set()
    for pg, group in by_page.items():
        if len(group) < 2:
            continue
        ok = [c for c in group if _confirm(c.title, pg, heads, on)[0]]
        keep = ok[-1] if ok else group[-1]
        for c in group:
            if c is not keep:
                res.dropped.append((c, f"same page as «{keep.title}» (grouping)"))
                grouped.add(id(c))
    claims = [c for c in claims if id(c) not in grouped]
    prev_page = 0
    pending_order = []                       # (index, claim) to judge by order at the end
    for k, c in enumerate(claims):
        in_range = 1 <= c.page <= page_count
        ok, ev, better = _confirm(c.title, c.page, heads, on) if in_range else (False, [], None)
        if ok:
            c.evidence = [f"bookmark p{c.page} confirmed by " + ", ".join(ev)]
            if better:
                c.evidence.append(f"title from the heading unit «{better}»")
                c.title = better
            c.confidence = HIGH
            res.confirmed.append(c)
            prev_page = c.page
            continue
        # re-anchor: a contents entry with this title, else a head run or heading elsewhere
        target, how = None, ""
        for e in contents:
            if e.page and (ratio(c.title, e.title) >= 0.8 or (
                    title_match(c.title, e.title) and title_match(e.title, c.title))) and \
                    same_numerals(c.title, e.title):
                target, how = e.page, f"contents {e.label}"
                if _confirm(c.title, e.page, heads, on)[0]:
                    how += ", heads"
                break
        if target is None:
            found = [(q, h) for q, h in _find_pages(c.title, heads, on, prev_page)]
            if found:
                target, how = found[0]
        if target is not None:
            old = c.page
            c.page = target
            c.evidence = [f"bookmark p{old} re-anchored to p{target} ({how})"]
            c.confidence = HIGH if "," in how else MEDIUM
            res.reanchored.append((c, old))
            prev_page = target
            continue
        pending_order.append((k, c))
    # unconfirmed bookmarks in an otherwise verified outline, in order: kept as claims
    kept_pages = sorted(x.page for x in res.confirmed + [r for r, _ in res.reanchored])
    verified = len(claims) and (len(claims) - len(pending_order)) / len(claims) >= 0.8
    for k, c in pending_order:
        before = [x.page for x in claims[:k] if x in res.confirmed or any(
            x is r for r, _ in res.reanchored)]
        after = [x.page for x in claims[k + 1:] if x in res.confirmed or any(
            x is r for r, _ in res.reanchored)]
        lo = max(before) if before else 0
        hi = min(after) if after else page_count + 1
        if verified and lo < c.page < hi and 1 <= c.page <= page_count:
            c.evidence = [f"bookmark p{c.page} unverified: title not found on p{c.page}; "
                          "outline in order and otherwise verified"]
            c.confidence = MEDIUM
            res.unconfirmed.append(c)
        else:
            why = ("verifies nowhere: not on a contents page, no head or heading carries it"
                   if 1 <= c.page <= page_count else f"page {c.page} is outside the file")
            res.dropped.append((c, why))
    del kept_pages
    return res
