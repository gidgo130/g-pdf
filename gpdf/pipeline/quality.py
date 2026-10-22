# SPDX-License-Identifier: AGPL-3.0-or-later
"""Text-quality score of an extracted text layer, and the language of each page.

A word is known when the wordfreq "large" list of the page language has it at zipf >= 1.0,
or another language of LANGS has it at zipf >= 2.5 (the lists hold web typos such as
"tbis", and the corpus quotes real German and French). Only lowercase-initial words count
toward the rate: capitalised words are mostly names. Suspect words are unknown lowercase
words, case-chaos words ("opouyIy") and words with a digit between letters ("Jap6n").
No accent folding: period spelling ("fué", "á", "dió") is in the lists.

Known limitations: a word misread into another real word is not caught ("mitologia" for
"mitología" is in the es list at 2.19), and Tesseract prefers dictionary words, so this
score may flatter a re-OCR. Bump SCORE_VERSION whenever scoring changes: the catalogue
re-measures files scored with another version.
"""

from __future__ import annotations

import re
import statistics
import unicodedata
from collections import Counter
from dataclasses import asdict, dataclass
from functools import lru_cache
from math import ceil

from wordfreq import zipf_frequency

SCORE_VERSION = 1
LANGS = ("es", "en", "fr", "pt", "it", "de", "ca")
_INDEX = {lang: i for i, lang in enumerate(LANGS)}
TESSERACT_LANGS = {"es": "spa", "en": "eng+spa", "fr": "fra", "pt": "por", "it": "ita",
                   "de": "deu", "ca": "cat"}
KNOWN_ZIPF = 1.0           # in the page language
KNOWN_OTHER_ZIPF = 2.5     # in any other language of LANGS
MERGED_PART_ZIPF = 3.0     # the content half of a merged word, in the page language
MERGED_MIN = 5             # letters
LANG_MIN_VOTES = 30        # a page with fewer votes takes the file language
LANG_SHARE_MIN = 10        # % of the votes for a language to be reported
MIN_SCORED = 30            # lowercase words for a page to be scored
FAIR, POOR, POOR_JUNK = 3.0, 8.0, 2.0       # suspect %, suspect %, junk %
GOOD, FAIR_CLASS, POOR_CLASS, UNSCORED = "good", "fair", "poor", "unscored"

FUNCTION_WORDS = {
    "es": frozenset("a al como con de del el en entre es ha la las le les lo los más mas mi "
                    "muy no o para pero por porque que se si sin sobre su sus un una uno y "
                    "ya".split()),
    "en": frozenset("a an and as at be but by for from had has have he her his in is it its "
                    "not of on or our that the their this to was we were which with".split()),
}

_L = r"[^\W\d_]"                                              # a letter
_SOFT_BREAK = re.compile(rf"(?<={_L})­[ \t]*\r?\n[ \t]*(?={_L})")
_SOFT_LETTERS = re.compile(rf"(?<={_L})­(?={_L})")
_SOFT_DIGITS = re.compile(r"(?<=\d)­(?=\d)")
_SPACED = re.compile(rf"(?<!{_L}){_L}(?: {_L}){{3,}}(?!{_L})")
_HARD_BREAK = re.compile(rf"({_L}+)-[ \t]*\r?\n[ \t]*({_L}+)")
_TOKEN = re.compile(r"[^\W_]+(?:['’][^\W_]+)*")              # letters and digits
_EDGE_DIGITS = re.compile(r"^\d+|\d+$")
_REPEATED = re.compile(rf"({_L})\1{{3,}}", re.I)
_PUNCT = "!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~¡¿«»“”‘’–—…§°ºª­"
_NOT_JUNK = re.compile(r"[\sA-Za-z0-9!-/:-@\[-`{-~¡¿«»“”‘’–—…§°ºª­"
                       r"À-ÖØ-öø-ɏḀ-ỿ]+")


@lru_cache(maxsize=1 << 20)
def zipfs(word: str) -> tuple[float, ...]:
    """zipf frequency of the lowercased word in each language of LANGS."""
    w = word.lower().replace("’", "'")
    return tuple(zipf_frequency(w, lang, wordlist="large") for lang in LANGS)


def zipf(word: str, lang: str) -> float:
    return zipfs(word.lower())[_INDEX[lang]]


def known(word: str, lang: str | None) -> bool:
    z = zipfs(word.lower())
    i = _INDEX.get(lang)
    if i is not None and z[i] >= KNOWN_ZIPF:
        return True
    return any(v >= KNOWN_OTHER_ZIPF for k, v in enumerate(z) if k != i)


# ---------------------------------------------------------------------------- text prep
def prepare(text: str) -> tuple[str, int]:
    """NFC, soft hyphens resolved, letter-spaced runs ("h e t e n d i d o") rejoined.
    Returns the text and the number of letter-spaced runs. Line-end hard hyphens need the
    language: see join_hyphens."""
    text = unicodedata.normalize("NFC", text)
    text = _SOFT_BREAK.sub("", text)
    text = _SOFT_LETTERS.sub("", text)
    text = _SOFT_DIGITS.sub("-", text)                       # "39­69": a page range
    runs = 0

    def join(m):
        nonlocal runs
        runs += 1
        return m.group(0).replace(" ", "")

    return _SPACED.sub(join, text), runs


def join_hyphens(text: str, lang: str | None) -> str:
    """"a-\\nb" becomes "ab" when "ab" is known or either part is unknown, else "a-b"."""
    def fix(m):
        a, b = m.group(1), m.group(2)
        if known(a + b, lang) or not known(a, lang) or not known(b, lang):
            return a + b
        return f"{a}-{b}"

    return _HARD_BREAK.sub(fix, text)


def _tokens(text):
    """(word, has a digit between letters) for each word of >= 2 characters; digits at
    either end (footnote markers, "1920s") are dropped."""
    for m in _TOKEN.finditer(text):
        core = _EDGE_DIGITS.sub("", m.group())
        if len(core) >= 2 and not core[0].isdigit():
            yield core, any(c.isdigit() for c in core)


def _case_chaos(word) -> bool:
    return any(a.islower() and b.isupper() for a, b in zip(word[1:], word[2:]))


def merged(word: str, lang: str | None) -> bool:
    """A function word glued to a word: "porqueel", "mayorde", "unacivilización"."""
    fw = FUNCTION_WORDS.get(lang)
    if not fw or len(word) < MERGED_MIN:
        return False
    w = word.lower()
    for i in range(1, len(w)):
        a, b = w[:i], w[i:]
        if (a in fw and zipf(b, lang) >= MERGED_PART_ZIPF) or (
                b in fw and zipf(a, lang) >= MERGED_PART_ZIPF):
            return True
    return False


@lru_cache(maxsize=4096)
def _junk_char(c) -> bool:
    if c.isspace() or c.isdigit() or c in _PUNCT:
        return False
    if c.isalpha():
        return not unicodedata.name(c, "").startswith("LATIN")
    return True                 # symbols, U+FFFD, controls, private use


def junk_count(text) -> tuple[int, int]:
    """(junk characters, non-space characters)."""
    rest = _NOT_JUNK.sub("", text)
    return sum(_junk_char(c) for c in rest), len("".join(text.split()))


# ---------------------------------------------------------------------------- language
def votes(text: str) -> Counter:
    """Each word of >= 3 letters votes for the language that has it most often."""
    v = Counter()
    for word, digit in _tokens(text):
        if digit or len(word) < 3:
            continue
        z = zipfs(word.lower())
        best = max(z)
        if best > 0:
            v[LANGS[z.index(best)]] += 1
    return v


def winner(v: Counter) -> str | None:
    return max(LANGS, key=lambda lang: v[lang]) if sum(v.values()) else None


def page_language(v: Counter, file_lang: str | None) -> str | None:
    return winner(v) if sum(v.values()) >= LANG_MIN_VOTES else file_lang


def shares(v: Counter) -> dict[str, int]:
    """Languages with >= LANG_SHARE_MIN % of the votes, in %."""
    total = sum(v.values())
    if not total:
        return {}
    return {lang: round(100 * v[lang] / total) for lang in LANGS
            if 100 * v[lang] / total >= LANG_SHARE_MIN}


# ---------------------------------------------------------------------------- scoring
@dataclass
class PageScore:
    page: int
    lang: str | None
    words: int = 0             # all words
    lower: int = 0             # lowercase-initial words: the rate's denominator
    suspect: int = 0
    pct: float | None = None   # suspect %, None if unscored
    cls: str = UNSCORED
    merged: int = 0
    spaced: int = 0            # letter-spaced runs
    digit: int = 0             # digit between letters
    chaos: int = 0             # case chaos
    repeated: int = 0          # >= 4 of the same letter
    junk: int = 0              # junk characters
    chars: int = 0             # non-space characters
    caps: int = 0              # capitalised words (not in the rate)
    caps_unknown: int = 0

    @property
    def junk_pct(self) -> float:
        return 100 * self.junk / self.chars if self.chars else 0.0

    def row(self) -> dict:
        d = asdict(self)
        d["junk_pct"] = round(self.junk_pct, 2)
        return d


def score_prepared(text: str, lang: str | None, page: int = 1, spaced: int = 0) -> PageScore:
    """Score a prepared text (prepare + join_hyphens) in the page language."""
    s = PageScore(page, lang, spaced=spaced)
    for word, digit in _tokens(text):
        s.words += 1
        lower = word[0].islower()
        chaos = _case_chaos(word)
        s.lower += lower
        s.digit += digit
        s.chaos += chaos
        if digit or chaos:
            s.suspect += 1
        elif lower:
            if not known(word, lang):
                s.suspect += 1
                s.merged += merged(word, lang)
        else:
            s.caps += 1
            s.caps_unknown += not known(word, lang)
    s.repeated = len(_REPEATED.findall(text))
    s.junk, s.chars = junk_count(text)
    if s.lower >= MIN_SCORED:
        s.pct = round(100 * s.suspect / s.lower, 2)
        if s.pct >= POOR or s.junk_pct >= POOR_JUNK:
            s.cls = POOR_CLASS
        elif s.pct >= FAIR:
            s.cls = FAIR_CLASS
        else:
            s.cls = GOOD
    return s


def score_text(text: str, lang: str | None, page: int = 1) -> PageScore:
    """Score one raw text (e.g. a trial OCR page) in a given language."""
    prepared, spaced = prepare(text)
    return score_prepared(join_hyphens(prepared, lang), lang, page, spaced)


@dataclass
class Scored:
    pages: list[PageScore]
    texts: list[str]           # prepared texts, per page
    lang: str | None           # file language
    shares: dict[str, int]


def score_pages(texts: list[str]) -> Scored:
    """Score raw page texts: languages first (per page and for the file), then each page
    in its language."""
    prepped, page_votes, total = [], [], Counter()
    for t in texts:
        p, spaced = prepare(t)
        v = votes(p)
        prepped.append((p, spaced))
        page_votes.append(v)
        total.update(v)
    file_lang = winner(total)
    pages, out = [], []
    for i, ((p, spaced), v) in enumerate(zip(prepped, page_votes)):
        lang = page_language(v, file_lang)
        p = join_hyphens(p, lang)
        out.append(p)
        pages.append(score_prepared(p, lang, i + 1, spaced))
    return Scored(pages, out, file_lang, shares(total))


def _p95(values):
    v = sorted(values)
    return v[max(0, ceil(0.95 * len(v)) - 1)]


def summarise(pages: list[PageScore]) -> dict:
    """File score: word-weighted mean suspect % (scored pages), median and p95, page
    classes, per-1000-word rates and junk %."""
    scored = [p for p in pages if p.pct is not None]
    words = sum(p.words for p in pages)
    lower = sum(p.lower for p in scored)
    chars = sum(p.chars for p in pages)
    caps = sum(p.caps for p in pages)

    def rate(n):
        return round(1000 * n / words, 2) if words else 0.0

    return {
        "weighted": round(100 * sum(p.suspect for p in scored) / lower, 2) if lower else None,
        "median": round(statistics.median(p.pct for p in scored), 2) if scored else None,
        "p95": round(_p95([p.pct for p in scored]), 2) if scored else None,
        "scored": len(scored),
        "good": sum(p.cls == GOOD for p in pages),
        "fair": sum(p.cls == FAIR_CLASS for p in pages),
        "poor": sum(p.cls == POOR_CLASS for p in pages),
        "unscored": sum(p.cls == UNSCORED for p in pages),
        "words": words,
        "per_1000": {"merged": rate(sum(p.merged for p in pages)),
                     "spaced": rate(sum(p.spaced for p in pages)),
                     "digit": rate(sum(p.digit for p in pages)),
                     "chaos": rate(sum(p.chaos for p in pages)),
                     "repeated": rate(sum(p.repeated for p in pages))},
        "junk_pct": round(100 * sum(p.junk for p in pages) / chars, 3) if chars else 0.0,
        "caps_unknown_pct": round(100 * sum(p.caps_unknown for p in pages) / caps, 2)
        if caps else None,
    }
