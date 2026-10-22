# SPDX-License-Identifier: AGPL-3.0-or-later
"""Text-quality score of an extracted text layer, and the language of each page.

A word is known when the wordfreq "large" list of the page language has it at zipf >= 1.0,
or another language of LANGS has it at zipf >= 2.5 (the lists hold web typos such as
"tbis", and the corpus quotes real German and French). Only lowercase-initial words count
toward the rate: capitalised words are mostly names. Suspect words are unknown lowercase
words, case-chaos words ("opouyIy"), words with a digit between letters ("Jap6n") and
misread words with digits at the edge ("e1", "10s"). No accent folding: period spelling
("fué", "á", "dió") is in the lists.

Two rates are kept apart from suspect %: cap_unknown_pct (capitalised unknown words and
short words at home in another language: names, and Tesseract's reading of ornaments; the
rebuild trial and the ink-page test add it to suspect %) and missing_accent_pct
(informational: "tambien", "corazon" are in the lists as web spellings).

What it measures (audit of 2026-10-21, 295 page texts against ground truth): suspect %
tracks the word error rate across quality levels (Spearman 0.57, Pearson 0.77) and is
roughly half of it (about 1.9 WER points per suspect point); within one quality level, and
below about 2%, it cannot rank pages. It did not flatter re-OCR: Tesseract's errors are
non-words, which it sees. It does not catch real-word substitutions ("mitologia" for
"mitología" is in the es list at 2.19), accent drops (except as missing_accent_pct) or
words lost to letter-spacing. Bump SCORE_VERSION whenever scoring changes: the catalogue
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

SCORE_VERSION = 2
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
SHORT = 3                  # letters: a short word (cap_unknown_pct)
SHORT_FOREIGN_GAP = 2.0    # zipf: how much more often another language has a short word
MISSING_ACCENT_ZIPF = 1.0  # an accented form this much more frequent: an accent dropped
_ACCENTED = {"a": "á", "e": "é", "i": "í", "o": "ó", "u": "úü", "n": "ñ"}
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
_PAGE_END_HYPHENS = ("-", "­", "‐")
_CONFUSABLE = {"0": "o", "1": "l", "4": "a", "5": "s", "6": ("o", "ó"), "8": "B"}
FOOTNOTE_CORE = 3          # letters: a known word this long before a digit is a footnote
DIGIT_WORD_ZIPF = 2.0      # a misread word is this much more frequent than the token
ONE_LETTER_WORDS = {"es": "aeouy", "en": "ai"}
ONE_LETTER_ANY = "aeiouy"
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
def prepare(text: str, lang: str | None = None) -> tuple[str, int]:
    """NFC, soft hyphens resolved, letter-spaced runs ("h e t e n d i d o") rejoined
    (rejoin_spaced, in ``lang``; None: any language). Returns the text and the number of
    letter-spaced runs. Line-end hard hyphens need the language: see join_hyphens."""
    text = unicodedata.normalize("NFC", text)
    text = _SOFT_BREAK.sub("", text)
    text = _SOFT_LETTERS.sub("", text)
    text = _SOFT_DIGITS.sub("-", text)                       # "39­69": a page range
    runs = 0

    def join(m):
        nonlocal runs
        runs += 1
        return rejoin_spaced(m.group(0), lang)

    return _SPACED.sub(join, text), runs


def rejoin_spaced(run: str, lang: str | None) -> str:
    """A letter-spaced run ("y h e t e n d i d o") joined, keeping a leading or trailing
    one-letter word apart when the rest alone is a known word ("y a l l í" -> "y allí"),
    and a leading one when neither the whole nor the rest is known ("y hetendido")."""
    letters = run.split(" ")
    whole = "".join(letters)
    if known(whole, lang):
        return whole
    singles = ONE_LETTER_WORDS.get(lang, ONE_LETTER_ANY)
    lead, trail = letters[0].lower() in singles, letters[-1].lower() in singles
    if lead and trail and known(whole[1:-1], lang):
        return f"{letters[0]} {whole[1:-1]} {letters[-1]}"
    if lead and known(whole[1:], lang):
        return f"{letters[0]} {whole[1:]}"
    if trail and known(whole[:-1], lang):
        return f"{whole[:-1]} {letters[-1]}"
    return f"{letters[0]} {whole[1:]}" if lead else whole


def join_hyphens(text: str, lang: str | None) -> str:
    """"a-\\nb" becomes "ab" when "ab" is known or either part is unknown, else "a-b"."""
    def fix(m):
        a, b = m.group(1), m.group(2)
        if known(a + b, lang) or not known(a, lang) or not known(b, lang):
            return a + b
        return f"{a}-{b}"

    return _HARD_BREAK.sub(fix, text)


def digit_word(token: str, lang: str | None) -> str | None:
    """The word a token with digits at its edge was misread from ("e1" -> "el", "10s" ->
    "los", "a1" -> "al"): confusable digits mapped to letters give a known word that is
    >= DIGIT_WORD_ZIPF more frequent than the token (wordfreq gives digit tokens a
    frequency of their own; ordinals such as "4a", "5a" map to junk only a little more
    frequent).
    None for footnote markers after a known word ("palabra1", "poema5"), years ("1920s")
    and codes with capitals ("P8", "E48": index entries)."""
    core = _EDGE_DIGITS.sub("", token)
    if not core.islower():
        return None
    if token[-1].isdigit() and len(core) >= FOOTNOTE_CORE and known(core, lang):
        return None
    words = [""]
    for c in token:
        words = [w + x for w in words for x in _CONFUSABLE.get(c, c)]
    z = zipf(token, lang) if lang in _INDEX else max(zipfs(token))
    for w in words:
        if w.isalpha() and known(w, lang) and (
                zipf(w, lang) if lang in _INDEX else max(zipfs(w))) >= z + DIGIT_WORD_ZIPF:
            return w
    return None


def _tokens(text, lang=None):
    """(word, digit) for each word of >= 2 characters: ``digit`` when a digit sits between
    letters, or the token has digits at its edge and is a misread word (digit_word; the
    word yielded is then the mapped one). Other digits at either end (footnote markers,
    "1920s") are dropped."""
    for m in _TOKEN.finditer(text):
        token = m.group()
        core = _EDGE_DIGITS.sub("", token)
        if core != token and core and not any(c.isdigit() for c in core):
            word = digit_word(token, lang)
            if word:
                yield word, True
                continue
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
    cap_junk: int = 0          # capitalised unknown + short foreign words (cap_unknown_pct)
    accent: int = 0            # words missing an accent (missing_accent_pct)

    @property
    def junk_pct(self) -> float:
        return 100 * self.junk / self.chars if self.chars else 0.0

    @property
    def cap_unknown_pct(self) -> float | None:
        """cap_junk per 100 lowercase words: a rate of its own, never in suspect %."""
        return round(100 * self.cap_junk / self.lower, 2) if self.pct is not None else None

    @property
    def missing_accent_pct(self) -> float | None:
        """Words missing an accent per 100 lowercase words (informational)."""
        return round(100 * self.accent / self.lower, 2) if self.pct is not None else None

    def row(self) -> dict:
        d = asdict(self)
        d["junk_pct"] = round(self.junk_pct, 2)
        d["cap_unknown_pct"] = self.cap_unknown_pct
        d["missing_accent_pct"] = self.missing_accent_pct
        return d


@lru_cache(maxsize=1 << 18)
def missing_accent(word: str, lang: str | None) -> bool:
    """A lowercase word without accents whose accented form (one vowel or ñ) is
    >= MISSING_ACCENT_ZIPF more frequent in the page language: "tambien", "corazon".
    The word lists hold such web spellings, so they count as known."""
    if lang not in _INDEX or not (word.isascii() and word.isalpha() and word.islower()):
        return False
    best = max((zipf(word[:i] + a + word[i + 1:], lang)
                for i, c in enumerate(word) for a in _ACCENTED.get(c, "")), default=0.0)
    return best - zipf(word, lang) >= MISSING_ACCENT_ZIPF


def short_foreign(word: str, lang: str | None) -> bool:
    """A word of <= 3 letters at home in another language, not the page's ("il", "AND",
    "AT" on a Spanish page): another language of LANGS has it >= SHORT_FOREIGN_GAP zipf
    more often. Tesseract's reading of an ornament is made of these."""
    i = _INDEX.get(lang)
    if i is None or len(word) > SHORT or not word.isalpha():
        return False
    z = zipfs(word.lower())
    other = max(v for k, v in enumerate(z) if k != i)
    return other >= KNOWN_OTHER_ZIPF and z[i] <= other - SHORT_FOREIGN_GAP


def score_prepared(text: str, lang: str | None, page: int = 1, spaced: int = 0,
                   skip_first: bool = False) -> PageScore:
    """Score a prepared text (prepare + join_hyphens) in the page language. ``skip_first``:
    leave out the first word (the end of a word hyphenated across the page break)."""
    s = PageScore(page, lang, spaced=spaced)
    tokens = _tokens(text, lang)
    if skip_first:
        next(tokens, None)
    for word, digit in tokens:
        s.words += 1
        lower = word[0].islower()
        chaos = _case_chaos(word)
        s.lower += lower
        s.digit += digit
        s.chaos += chaos
        if digit or chaos:
            s.suspect += 1
        elif lower:
            s.accent += missing_accent(word, lang)
            if not known(word, lang):
                s.suspect += 1
                s.merged += merged(word, lang)
            else:
                s.cap_junk += short_foreign(word, lang)
        else:
            s.caps += 1
            unknown = not known(word, lang)
            s.caps_unknown += unknown
            s.cap_junk += (unknown and len(word) > SHORT) or short_foreign(word, lang)
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
    prepared, spaced = prepare(text, lang)
    return score_prepared(join_hyphens(prepared, lang), lang, page, spaced)


@dataclass
class Scored:
    pages: list[PageScore]
    texts: list[str]           # prepared texts, per page
    lang: str | None           # file language
    shares: dict[str, int]


def score_pages(texts: list[str]) -> Scored:
    """Score raw page texts: languages first (per page and for the file), then each page
    in its language. A page after one that ends in a hyphen is scored without its first
    word (a fragment such as "teriosa")."""
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
        if spaced:                              # rejoin the letter-spaced runs in ``lang``
            p = prepare(texts[i], lang)[0]
        p = join_hyphens(p, lang)
        out.append(p)
        broken = i > 0 and texts[i - 1].rstrip().endswith(_PAGE_END_HYPHENS)
        pages.append(score_prepared(p, lang, i + 1, spaced, skip_first=broken))
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
        "cap_unknown_pct": round(100 * sum(p.cap_junk for p in scored) / lower, 2)
        if lower else None,
        "missing_accent_pct": round(100 * sum(p.accent for p in scored) / lower, 2)
        if lower else None,
    }
