# SPDX-License-Identifier: AGPL-3.0-or-later
r"""The review queue: which formulas Claude should check, and why.

Rules, each on its own (kinds in order of precedence):
1. disagree: the two tiers' readings of a display differ after normalization, compared
   loosely (bold/roman markup, line breaks, dot style and trailing punctuation ignored:
   the trial's rule), or the second tier has no reading for it.
   marker: Marker (the third opinion, when it ran) reads the display differently; how
   different is the rule's variant: any normalized difference, a value-changing one
   (style commands ignored), or a value-changing one of 1-3 tokens.
2. style: the tiers differ only in \mathbf / \mathrm / \mathit / \mathcal / \mathbb;
   opt-in: the same symbol (same subscript) is both bold and not on one page.
3. stack: a big operator whose limits hold two index variables, a relation, or a sum of
   indices: a printed multiple sum collapsed into one.
4. syntax: unbalanced braces / \left \right / \begin \end, or a command outside the
   whitelist (standard LaTeX math plus the trial's outputs).
5. tag: within a contiguous page run a tag repeats, goes backwards or skips a number.
6. missing_display: another engine (Marker, or the basic tier) has a display that none
   of the standard tier's aligns with, or the layout has an equation block that gave no
   display; the item carries the other reading and its crop.
7. inline: an inline span of the standard tier differs from the other engines', aligned
   paragraph by paragraph (against the basic tier, Marker, either or both).

Items carry both readings and a crop of the page region (300-400 dpi).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from gpdf.pipeline.mathocr import align, second
from gpdf.pipeline.mathocr import latex as L
from gpdf.pipeline.mathocr.assemble import Page, Paragraph, _paragraph

KINDS = ("disagree", "marker", "style", "stack", "syntax", "tag", "missing_display", "inline",
         "inline_missing", "identity", "prose")
MARKER_RULES = ("any", "value", "small")
INLINE_WITH = ("basic", "marker", "either", "both")
STYLE_CMDS = L.STYLE_CMDS
OPERATORS = {r"\sum", r"\prod", r"\int", r"\iint", r"\iiint", r"\oint", r"\bigcup", r"\bigcap",
             r"\bigvee", r"\bigwedge", r"\coprod", r"\bigoplus", r"\bigotimes"}
RELATIONS = {"<", ">", r"\le", r"\ge", r"\ne", r"\neq", r"\leq", r"\geq"}
INLINE_MIN_SIM = 0.2          # short spans: a one-token change is a large relative one
GREEK = {"alpha", "beta", "gamma", "delta", "epsilon", "varepsilon", "zeta", "eta", "theta",
         "vartheta", "iota", "kappa", "lambda", "mu", "nu", "xi", "pi", "varpi", "rho", "varrho",
         "sigma", "varsigma", "tau", "upsilon", "phi", "varphi", "chi", "psi", "omega", "Gamma",
         "Delta", "Theta", "Lambda", "Xi", "Pi", "Sigma", "Upsilon", "Phi", "Psi", "Omega", "ell",
         "hbar", "partial", "infty", "nabla"}
WHITELIST = GREEK | {
    # big operators
    "sum", "prod", "int", "iint", "iiint", "oint", "bigcup", "bigcap", "bigvee", "bigwedge",
    "coprod", "bigoplus", "bigotimes", "bigsqcup",
    # relations and arrows
    "le", "leq", "ge", "geq", "ne", "neq", "equiv", "sim", "simeq", "approx", "cong", "propto",
    "subset", "subseteq", "supset", "supseteq", "in", "notin", "ni", "mid", "parallel", "perp",
    "prec", "succ", "preceq", "succeq", "ll", "gg", "doteq", "asymp", "models", "vdash", "not",
    "to", "rightarrow", "leftarrow", "Rightarrow", "Leftarrow", "leftrightarrow",
    "Leftrightarrow", "mapsto", "longrightarrow", "longleftarrow", "Longrightarrow", "uparrow",
    "downarrow", "implies", "iff", "nearrow", "searrow", "hookrightarrow",
    # binary operators
    "pm", "mp", "times", "div", "cdot", "ast", "star", "circ", "bullet", "cap", "cup",
    "setminus", "wedge", "vee", "land", "lor", "oplus", "otimes", "odot", "ominus", "sqcup",
    "sqcap", "amalg", "dagger", "ddagger", "triangleleft", "triangleright", "wr", "diamond",
    # delimiters and sizes
    "left", "right", "middle", "big", "Big", "bigg", "Bigg", "bigl", "bigr", "Bigl", "Bigr",
    "biggl", "biggr", "Biggl", "Biggr", "bigm", "Bigm", "langle", "rangle", "lbrace", "rbrace",
    "lceil", "rceil", "lfloor", "rfloor", "vert", "Vert", "lvert", "rvert", "lVert", "rVert",
    "backslash", "lbrack", "rbrack", "lgroup", "rgroup",
    # accents
    "hat", "bar", "tilde", "vec", "dot", "ddot", "dddot", "check", "breve", "acute", "grave",
    "widehat", "widetilde", "overline", "underline", "overbrace", "underbrace",
    "overrightarrow", "overleftarrow", "overset", "underset", "stackrel", "mathring",
    # fonts and text
    "mathbf", "mathrm", "mathit", "mathcal", "mathbb", "mathsf", "mathfrak", "mathscr",
    "boldsymbol", "bm", "pmb", "bf", "rm", "it", "cal", "sf", "tt", "text", "textrm", "textit",
    "textbf", "textsf", "mbox", "hbox", "operatorname", "operatorname*", "mathop", "textnormal",
    "boldmath", "mathbin", "mathrel", "mathord", "mathstrut", "em",
    # structures
    "frac", "dfrac", "tfrac", "binom", "dbinom", "tbinom", "sqrt", "substack", "atop", "choose",
    "over", "cfrac", "genfrac", "pmod", "bmod", "mod",
    # spacing and layout
    "quad", "qquad", "hspace", "vspace", "phantom", "hphantom", "vphantom", "thinspace",
    "enspace", "hfill", "smash", "kern", "mkern", "mskip", "negthinspace", "medspace",
    "thickspace", "negmedspace", "negthickspace", "allowbreak", "raisebox", "rule",
    # functions
    "sin", "cos", "tan", "cot", "sec", "csc", "arcsin", "arccos", "arctan", "sinh", "cosh",
    "tanh", "coth", "exp", "log", "ln", "lg", "max", "min", "sup", "inf", "lim", "liminf",
    "limsup", "det", "dim", "ker", "deg", "gcd", "Pr", "arg", "hom", "varlimsup", "varliminf",
    # miscellany
    "forall", "exists", "nexists", "neg", "lnot", "emptyset", "varnothing", "aleph", "Re",
    "Im", "wp", "prime", "dots", "ldots", "cdots", "vdots", "ddots", "dotsc", "dotsb", "dotsm",
    "angle", "triangle", "square", "top", "bot", "flat", "natural", "sharp", "surd", "therefore",
    "because", "colon", "S", "P", "dag", "ddag", "copyright", "pounds", "degree", "checkmark",
    "backslash", "lq", "rq",
    # environments, tags, styles
    "begin", "end", "tag", "tag*", "label", "nonumber", "notag", "displaystyle", "textstyle",
    "scriptstyle", "scriptscriptstyle", "limits", "nolimits", "hline", "cline", "multicolumn",
    "newline", "cr", "noalign", "intertext", "shortintertext", "displaybreak",
}


@dataclass
class Match:
    """One of the primary tier's inline spans against the second tier's."""
    paragraph: int
    span: int
    ok: bool
    primary: str
    second: str | None


@dataclass
class Item:
    id: str
    file: int
    printed: str
    kind: str
    reasons: list[str]
    tag: str | None
    latex_primary: str | None
    latex_second: str | None
    bbox: list[float] | None = None
    bboxes: list[list[float] | None] = field(default_factory=list)
    crop: str | None = None
    crops: list[str] = field(default_factory=list)
    spans: list[dict] | None = None
    note: str | None = None
    extra: dict = field(default_factory=dict)

    def record(self) -> dict:
        rec = {"id": self.id, "file": self.file, "printed": self.printed, "kind": self.kind,
               "reasons": self.reasons, "tag": self.tag, "latex_primary": self.latex_primary,
               "latex_second": self.latex_second, "crop": self.crop}
        if self.crops:
            rec["crops"] = self.crops
        if self.spans is not None:
            rec["spans"] = self.spans
        if self.note:
            rec["note"] = self.note
        rec.update(self.extra)
        return rec


# ---------------------------------------------------------------- 1, 2: the two tiers

def _norm(tex: str) -> str:
    return L.norm_str(L.tokens(L.split_tag(tex)[0]))


def disagree(primary: str, other: str | None) -> bool:
    """Rule 1: the readings differ loosely, or there is no second reading."""
    return other is None or L.value_only(_norm(primary)) != L.value_only(_norm(other))


_style_free = L.style_free


def style_only(primary: str, other: str | None) -> bool:
    """Rule 2: same value, different markup."""
    if other is None:
        return False
    a, b = _norm(primary), _norm(other)
    return a != b and _style_free(a) == _style_free(b)


def marker_disagrees(primary: str, mk: str | None, rule: str = "value") -> bool:
    """The Marker display rule. any: the normalized readings differ; value: they differ
    with style commands, line breaks, dot style and trailing punctuation ignored; small:
    value, and by 1-3 tokens."""
    if mk is None:
        return False
    a, b = _norm(primary), _norm(mk)
    if rule == "any":
        return a != b
    va, vb = L.style_free(a), L.style_free(b)
    if va == vb:
        return False
    return rule == "value" or 1 <= L.distance(va.split(), vb.split()) <= 3


def _is_symbol(t: str) -> bool:
    return (len(t) == 1 and t.isalnum()) or (t.startswith("\\") and t[1:] in GREEK)


def _symbols(toks: list[str], bold: bool = False) -> list[tuple[str, str, bool]]:
    """(symbol, subscript, bold) occurrences, inside groups and scripts too."""
    out, i = [], 0
    while i < len(toks):
        t = toks[i]
        if t == r"\mathbf" or t in STYLE_CMDS:
            e = L.atom_end(toks, i + 1)
            inner = toks[i + 2:e - 1] if toks[i + 1:i + 2] == ["{"] else toks[i + 1:e]
            i = e
            if t == r"\mathbf":
                if len(inner) == 1 and _is_symbol(inner[0]):
                    sym, is_bold = inner[0], True
                else:
                    out += _symbols(inner, True)
                    continue
            else:
                continue
        elif t == "{":
            e = L.atom_end(toks, i)
            out += _symbols(toks[i + 1:e - 1], bold)
            i = e
            continue
        elif _is_symbol(t):
            sym, is_bold = t, bold
            i += 1
        else:
            i += 1
            continue
        sub = ""
        while i < len(toks) and toks[i] in ("_", "^"):
            e = L.atom_end(toks, i + 1)
            atom = toks[i + 1:e]
            if toks[i] == "_":
                sub = "_" + "".join(x for x in atom if x not in "{}")
            out += _symbols(atom[1:-1] if atom[:1] == ["{"] else atom, bold)
            i = e
        out.append((sym, sub, is_bold))
    return out


def bold_mixed(page: Page) -> list[str]:
    """Symbols (with their subscript) that appear both bold and not on the page."""
    occ = []
    for d in page.displays:
        occ += _symbols(L.tokens(d.latex))
    for p in page.paragraphs:
        for s in p.inlines:
            occ += _symbols(L.tokens(s))
    bold = {(s, sub) for s, sub, b in occ if b}
    plain = {(s, sub) for s, sub, b in occ if not b}
    # a bare digit (the zero vector against a scalar 0) is on every page: not a signal
    return sorted(s + sub for s, sub in bold & plain if not (s.isdigit() and not sub))


# ---------------------------------------------------------------- 3: stacked operators

def _index_vars(toks: list[str]) -> list[str]:
    out, i = [], 0
    while i < len(toks):
        t = toks[i]
        if t in (r"\in", r"\subset", r"\subseteq", "="):
            break
        if _is_symbol(t) and not t.isdigit():
            name = t
            if toks[i + 1:i + 2] == ["_"]:
                e = L.atom_end(toks, i + 2)
                name += "_" + "".join(x for x in toks[i + 2:e] if x not in "{}")
                i = e
            else:
                i += 1
            if name not in out:
                out.append(name)
            continue
        i += 1
    return out


def stacked(tex: str) -> list[str]:
    """Rule 3 reasons for the big operators in ``tex``."""
    toks = L.tokens(L.split_tag(tex)[0])
    reasons = []
    i = 0
    while i < len(toks):
        if toks[i] in OPERATORS:
            j = i + 1
            sub = None
            while j < len(toks) and toks[j] in ("_", "^"):
                e = L.atom_end(toks, j + 1)
                if toks[j] == "_":
                    atom = toks[j + 1:e]
                    sub = atom[1:-1] if atom[:1] == ["{"] else atom
                j = e
            if sub:
                if any(t in RELATIONS for t in sub) and "relation in the limits" not in reasons:
                    reasons.append("relation in the limits")
                names = _index_vars(sub)
                lhs = sub[:sub.index("=")] if "=" in sub else sub
                if "+" in lhs and len(names) >= 2:
                    if "a sum of indices" not in reasons:
                        reasons.append("a sum of indices")
                elif len(names) >= 2:
                    r = f"two index variables ({names[0]}, {names[1]})"
                    if r not in reasons:
                        reasons.append(r)
            i = j
            continue
        i += 1
    return reasons


# ---------------------------------------------------------------- 4: syntax

def syntax(tex: str) -> list[str]:
    reasons = L.balance_problems(tex)
    seen = []
    for m in L.CMD_RE.finditer(tex):
        c = m.group(1)
        if c not in WHITELIST and c not in seen:
            seen.append(c)
            reasons.append(f"unknown command \\{c}")
    return reasons


# ---------------------------------------------------------------- 5: tags

TAG_PARSE = re.compile(r"^(?:([A-Za-z0-9]+)\.)?(\d+)([a-z]?)$")


def tag_problems(seq: list[tuple]) -> dict[tuple, str]:
    """``seq``: (page, display index, tag or None) in reading order over one contiguous
    page run. Returns the problem per (page, index)."""
    out, prev = {}, None               # prev: (chapter, number, letter, text)
    for page, idx, tag in seq:
        m = TAG_PARSE.match(tag or "")
        if not m:
            continue
        chapter, num, letter = m.group(1) or "", int(m.group(2)), m.group(3)
        if prev and prev[0] == chapter:
            pch, pnum, plet, ptext = prev
            if num == pnum and letter <= plet:
                out[(page, idx)] = f"tag {tag} repeats"
            elif num < pnum:
                out[(page, idx)] = f"tag {tag} goes backwards after {ptext}"
                continue
            elif num > pnum + 1:
                out[(page, idx)] = f"tag {tag} skips {chapter + '.' if chapter else ''}{pnum + 1}"
        prev = (chapter, num, letter, tag)
    return out


# ---------------------------------------------------------------- 6: inline

def _iou(a, b) -> float:
    if not a or not b:
        return 0.0
    x0, y0, x1, y1 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    area = lambda r: max(0.0, r[2] - r[0]) * max(0.0, r[3] - r[1])      # noqa: E731
    union = area(a) + area(b) - inter
    return inter / union if union > 0 else 0.0


def _pair_paragraphs(prim: list[Paragraph], other: list[Paragraph]) -> dict[int, int]:
    cands = sorted(((_iou(p.bbox, q.bbox), i, j) for i, p in enumerate(prim)
                    for j, q in enumerate(other)), reverse=True)
    used_i, used_j, out = set(), set(), {}
    for iou, i, j in cands:
        if iou < 0.3:
            break
        if i not in used_i and j not in used_j:
            out[i] = j
            used_i.add(i)
            used_j.add(j)
    return out


def inline_flagged(page: Page, basic_blocks: list[dict], marker_blocks: list[dict] | None,
                   mode: str = "basic") -> list[dict]:
    """The standard tier's inline spans that the rule flags, as span records: against the
    basic tier, Marker, either of them (flag when one differs) or both (flag only when
    neither agrees). Without Marker for the page every mode falls back to the basic tier."""
    mb = inline_pairs(page, basic_blocks)
    mm = inline_pairs(page, marker_blocks) if marker_blocks is not None else None
    out = []
    for k, b in enumerate(mb):
        m = mm[k] if mm is not None else None
        if m is None or mode == "basic":
            bad = not b.ok
        elif mode == "marker":
            bad = not m.ok
        elif mode == "either":
            bad = not (b.ok and m.ok)
        else:
            bad = not b.ok and not m.ok
        if bad:
            rec = {"paragraph": b.paragraph + 1, "latex_primary": b.primary,
                   "latex_second": b.second}
            if m is not None:
                rec["latex_marker"] = m.second
            para = page.paragraphs[b.paragraph]
            rec["bbox"] = para.bbox
            rec["at"] = span_position(para.text, b.span)
            out.append(rec)
    return out


_INLINE_IN_TEXT = re.compile(r"\\\((.+?)\\\)", re.S)
_INVISIBLE = {"{", "}", "^", "_", "&"} | STYLE_CMDS | L.DROP_CMDS


def _visible_len(tex: str) -> int:
    """About how many characters a formula prints as: one per symbol or command."""
    return max(1, sum(1 for t in L.TOKEN_RE.findall(tex) if t not in _INVISIBLE))


def span_position(text: str, k: int) -> float:
    """Where the k-th (0-based) inline span's middle falls in its paragraph, as a fraction
    of the printed length (prose by characters, math by symbols)."""
    pos, before, mid = 0, 0, None
    for i, m in enumerate(_INLINE_IN_TEXT.finditer(text)):
        before += len(text[pos:m.start()])
        n = _visible_len(m.group(1))
        if i == k:
            mid = before + n / 2
        before += n
        pos = m.end()
    total = before + len(text[pos:])
    return round(mid / total, 3) if mid is not None and total else 0.5


def inline_pairs(page: Page, other_blocks: list[dict]) -> list[Match]:
    """Every inline span of the page's paragraphs against the second tier's."""
    other = [_paragraph(b, i) for i, b in enumerate(other_blocks) if b.get("type") == "text"]
    pairing = _pair_paragraphs(page.paragraphs, other)
    out = []
    for pi, p in enumerate(page.paragraphs):
        q = other[pairing[pi]] if pi in pairing else None
        ref = [{"toks": L.tokens(s), "tag": None} for s in p.inlines]
        cand = [{"toks": L.tokens(s), "tag": None} for s in (q.inlines if q else [])]
        second_of = {}
        if ref and cand and len(ref) == len(cand):          # same count: by position
            second_of = {i: q.inlines[i] for i in range(len(ref))}
        elif ref and cand:
            pairs, _, _ = align.align(ref, cand, min_sim=INLINE_MIN_SIM)
            for ri, ci in pairs:
                for i in ri:
                    second_of[i] = " ".join(q.inlines[j] for j in sorted(ci))
        for si, s in enumerate(p.inlines):
            sec = second_of.get(si)
            ok = sec is not None and L.value_only(_norm(s)) == L.value_only(_norm(sec))
            out.append(Match(pi, si, ok, s, sec))
    return out


# ---------------------------------------------------------------- 8: math left in the prose

GREEK_CHARS = "αβγδεζηθικλμνξοπρστυφχψωϵϑϕϖϱςΓΔΘΛΞΠΣΥΦΨΩ"
MATH_CHARS = "∞∑∏∫√±∓×÷≤≥≠≈≡∈∉⊂⊆∂∇·∼≃∝"
SUPSUB = "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻₀₁₂₃₄₅₆₇₈₉′″"
_LET = "A-Za-z" + GREEK_CHARS
PROSE_MATH = [
    # a letter (or Greek) at a relation with a number or another single letter
    ("relation", re.compile(rf"(?<![\w.])[{_LET}][{SUPSUB}]*\s*[=<>≤≥≠≈∼]\s*"
                            rf"(?:[-−]?\d+(?:\.\d+)?|[{_LET}])(?![\w])")),
    # an operator between single letters or numbers (at least one letter: not 31-50)
    ("operator", re.compile(rf"(?<![\w.])(?:[{_LET}]|\d+)\s*[+\-−*/^]\s*(?:[{_LET}]|\d+)"
                            rf"(?![\w])")),
    # a letter with a digit index, x1 or X2
    ("index", re.compile(r"(?<![\w.])[A-Za-z]\d{1,2}(?![\w]|\.\d)")),
    # Greek letters and math symbols, with any script digits or primes
    ("greek", re.compile(rf"[{GREEK_CHARS}{MATH_CHARS}][{SUPSUB}]*")),
    # a lone letter that is not a word (not a, A, I), an initial (N.), e.g. or (a)
    ("letter", re.compile(r"(?<![\w.'’(])(?:[B-HJ-Z](?![\w'’).])|[b-hj-z](?![\w'’)]|\.\w))")),
    # an "a" that cannot be the article (Paper Capture reads alpha as a): before a comma
    # or a verb, or right after "parameter"
    ("letter_a", re.compile(r"(?<![\w.'’(])a(?=\s*[,;:)]|\s+(?:is|are|was|tends|increases|"
                            r"decreases|and|or|of|in|to)\b)|(?<=parameter )a(?![\w'’])"
                            r"|(?<=parameters )a(?![\w'’])")),
]


def prose_math_runs(text: str) -> list[tuple[int, int, str]]:
    """(start, end, why) of the prose runs of ``text`` that look like math the engine left
    as text; inline math (\\( \\)) is never looked at. Overlapping matches are merged."""
    blank = _INLINE_IN_TEXT.sub(lambda m: "\x00" * len(m.group()), text)
    hits = []
    for why, rx in PROSE_MATH:
        for m in rx.finditer(blank):
            if why == "operator" and not re.search(f"[{_LET}]", m.group()):
                continue
            hits.append([m.start(), m.end(), why])
    hits.sort()
    out = []
    for a, b, why in hits:
        if out and a < out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
            if why not in out[-1][2]:
                out[-1][2] += "+" + why
        else:
            out.append([a, b, why])
    return [tuple(x) for x in out]


def _prose_blank(md: str) -> str:
    """The page with displays and inline math blanked (same length), so offsets agree."""
    md = re.sub(r"\$\$.*?\$\$", lambda m: "\x00" * len(m.group()), md, flags=re.S)
    return _INLINE_IN_TEXT.sub(lambda m: "\x00" * len(m.group()), md)


def prose_occurrences(text: str, md: str) -> list[int]:
    """Offsets of ``text`` in the prose of the page ``md`` (never inside math)."""
    blank, where = _prose_blank(md), []
    i = blank.find(text)
    while i >= 0:
        where.append(i)
        i = blank.find(text, i + 1)
    return where


def inline_missing(page: Page) -> list[dict]:
    """Run records (paragraph, text, why, occurrence in the page prose, context, the
    paragraph's box and the run's position) of the prose that looks like math."""
    try:
        md = page.render()
    except Exception:  # noqa: BLE001 - an unbalanced page gets no occurrences
        md = ""
    out = []
    for pi, p in enumerate(page.paragraphs):
        base = md.find(p.text) if md else -1
        for a, b, why in prose_math_runs(p.text):
            run = p.text[a:b]
            occ = None
            if base >= 0:
                where = prose_occurrences(run, md)
                occ = where.index(base + a) + 1 if base + a in where else None
            out.append({"paragraph": pi + 1, "text": run, "why": why, "occurrence": occ,
                        "context": p.text[max(0, a - SPAN_CONTEXT):b + SPAN_CONTEXT],
                        "bbox": p.bbox, "at": round((a + b) / 2 / max(1, len(p.text)), 3)})
    return out


SPAN_CONTEXT = 80


# ---------------------------------------------------------------- the queue

def _pid(file: int) -> str:
    return f"p{file:03d}"


def unmatched(displays, others: list[str]) -> list[int]:
    """Indices of ``others`` that no display aligns with (tag first, then reading order,
    a split display joined)."""
    ref = [align.unit(d.raw) for d in displays]
    cand = [align.unit(t) for t in others]
    return align.align(ref, cand)[2]


def _missing(page: Page, pid: str, sources, std_blocks) -> list[Item]:
    """missing_display items: displays of another engine that none of the standard tier's
    aligns with (Marker first; a basic-tier one only when Marker has no similar one), and
    equation blocks of the layout that gave no display."""
    items, found = [], []
    for name, eng in sources:
        texts, boxes = eng.get("displays", []), eng.get("boxes") or []
        for j in unmatched(page.displays, texts):
            body = L.split_tag(texts[j])[0].strip()
            toks = L.tokens(body)
            if any(L.ned(toks, f) < 0.5 for f in found):
                continue
            found.append(toks)
            items.append(Item(f"{pid}-m{len(items) + 1:02d}", page.file, page.label.marker,
                              "missing_display", [f"{name} has a display the standard tier lacks"],
                              L.split_tag(texts[j])[1], None, body,
                              bbox=boxes[j] if j < len(boxes) else None))
    shown = {d.index for d in page.displays}
    blocks = list(std_blocks or [])
    if blocks and all(isinstance(b.get("index"), int) for b in blocks):
        blocks.sort(key=lambda b: b["index"])            # numbered as assemble numbers them
    for k, b in enumerate(blocks):
        if b.get("type") == "equation" and k not in shown:
            items.append(Item(f"{pid}-m{len(items) + 1:02d}", page.file, page.label.marker,
                              "missing_display", ["equation block in the layout gave no display"],
                              None, None, str(b.get("content") or "") or None, bbox=b.get("bbox")))
    return items


def review_items(page: Page, other: dict, runs_tags: dict | None = None,
                 group_inline: bool = False, third: dict | None = None, *,
                 marker_rule: str = "value", inline_with: str = "either",
                 style_page: bool = False, std_blocks: list[dict] | None = None,
                 stats: dict | None = None) -> list[Item]:
    """The page's review items. ``other`` = {"displays": [raw latex], "boxes": [bbox],
    "paragraphs": [text blocks]} of the second tier, ``third`` the same of Marker (None
    when it did not run on the page); ``runs_tags`` maps (pNNN, display index) to a tag
    problem; ``std_blocks`` is the standard tier's layout (for equation blocks that gave
    no display). ``stats``, when given, gets the page's display numbers without a Marker
    reading and the number of inline spans flagged."""
    runs_tags = runs_tags or {}
    pid = _pid(page.file)
    mixed = bold_mixed(page) if style_page else []
    mk_of = ({id(d): m for d, m in second.pair_displays(page.displays, third.get("displays", []))}
             if third is not None else {})
    no_marker = []
    items = []
    for n, (d, sec) in enumerate(second.pair_displays(page.displays, other.get("displays", [])),
                                 1):
        reasons, kinds = [], []
        if sec is None:
            reasons.append("no second reading")
            kinds.append("disagree")
        elif style_only(d.latex, sec):
            reasons.append("style differs between tiers")
            kinds.append("style")
        elif disagree(d.latex, sec):
            reasons.append("tiers disagree")
            kinds.append("disagree")
        mk = mk_of.get(id(d))
        if third is not None and mk is None:
            no_marker.append(n)
        if marker_disagrees(d.latex, mk, marker_rule):
            reasons.append("marker disagrees")
            kinds.append("marker")
        here = [s for s in mixed if any(sym + sub == s for sym, sub, _ in _symbols(L.tokens(d.latex)))]
        if here:
            reasons.append(f"symbol both bold and not on this page: {', '.join(here)}")
            kinds.append("style")
        for r in stacked(d.latex):
            reasons.append(r)
            kinds.append("stack")
        for r in syntax(d.latex):
            reasons.append(r)
            kinds.append("syntax")
        t = runs_tags.get((pid, n - 1))
        if t:
            reasons.append(t)
            kinds.append("tag")
        if not reasons:
            continue
        kind = min(kinds, key=KINDS.index)
        items.append(Item(f"{pid}-d{n:02d}", page.file, page.label.marker, kind, reasons, d.tag,
                          d.latex, L.split_tag(sec)[0].strip() if sec else None, bbox=d.bbox,
                          extra={"latex_marker": L.split_tag(mk)[0].strip()} if mk else {}))
    sources = ([("Marker", third)] if third is not None else []) + [("the basic tier", other)]
    items += _missing(page, pid, sources, std_blocks)
    bad = inline_flagged(page, other.get("paragraphs", []),
                         third.get("paragraphs") if third is not None else None, inline_with)
    if stats is not None:
        stats["no_marker_reading"] = no_marker
        stats["inline_flagged"] = len(bad)
    if bad and group_inline:
        paras = page.paragraphs
        seen = sorted({b["paragraph"] - 1 for b in bad})
        items.append(Item(f"{pid}-inline", page.file, page.label.marker, "inline",
                          [f"{len(bad)} inline spans differ ({inline_with})"], None, None, None,
                          bbox=paras[seen[0]].bbox, bboxes=[paras[i].bbox for i in seen],
                          spans=bad))
    elif bad:
        for k, b in enumerate(bad, 1):
            items.append(Item(f"{pid}-i{k:02d}", page.file, page.label.marker, "inline",
                              ["inline span differs" if b["latex_second"] is not None
                               else "no second reading"], None, b["latex_primary"],
                              b["latex_second"], bbox=page.paragraphs[b["paragraph"] - 1].bbox,
                              extra={k2: v for k2, v in b.items() if k2 == "latex_marker"}))
    runs = inline_missing(page)
    if stats is not None:
        stats["inline_missing_runs"] = len(runs)
    if runs:
        items.append(Item(f"{pid}-inline-missing", page.file, page.label.marker, "inline_missing",
                          [f"{len(runs)} prose runs look like math"], None, None, None,
                          extra={"runs": runs}))
    return items


# ---------------------------------------------------------------- crops

CROP_PAD = 0.012


def crop(doc, file: int, bbox, path: Path, dpi: int = 300) -> None:
    """Render the page region (page fractions, padded) or the whole page to ``path``."""
    import pymupdf

    pg = doc[file - 1]
    r = pg.rect
    if bbox:
        x0, y0, x1, y1 = bbox
        clip = pymupdf.Rect(r.x0 + max(0.0, x0 - CROP_PAD) * r.width,
                            r.y0 + max(0.0, y0 - CROP_PAD) * r.height,
                            r.x0 + min(1.0, x1 + CROP_PAD) * r.width,
                            r.y0 + min(1.0, y1 + CROP_PAD) * r.height)
    else:
        clip = r
    path.parent.mkdir(parents=True, exist_ok=True)
    pg.get_pixmap(dpi=dpi, clip=clip, colorspace=pymupdf.csGRAY).save(str(path))


INK = 128                 # grey below this is ink
LINE_DPI = 150
LINE_GAP = 3              # ink-free pixel rows that separate two text lines at LINE_DPI


def line_boxes(doc, file: int, bbox) -> list[list[float]]:
    """The text lines inside ``bbox`` (page fractions) as boxes, from the rows of ink."""
    import pymupdf

    pg = doc[file - 1]
    r = pg.rect
    clip = pymupdf.Rect(r.x0 + bbox[0] * r.width, r.y0 + bbox[1] * r.height,
                        r.x0 + bbox[2] * r.width, r.y0 + bbox[3] * r.height)
    if clip.is_empty:
        return [list(bbox)]
    pix = pg.get_pixmap(dpi=LINE_DPI, clip=clip, colorspace=pymupdf.csGRAY)
    s, w, stride = pix.samples, pix.width, pix.stride
    lines, start, end, gap = [], None, 0, 0
    for y in range(pix.height):
        ink = w and min(s[y * stride:y * stride + w]) < INK
        if ink:
            if start is None:
                start = y
            end, gap = y, 0
        elif start is not None:
            gap += 1
            if gap >= LINE_GAP:
                lines.append((start, end))
                start = None
    if start is not None:
        lines.append((start, end))
    out = []
    for y0, y1 in lines:
        if y1 - y0 < 2:
            continue
        top = (clip.y0 + y0 / pix.height * clip.height - r.y0) / r.height
        bottom = (clip.y0 + (y1 + 1) / pix.height * clip.height - r.y0) / r.height
        out.append([bbox[0], top, bbox[2], bottom])
    return out or [list(bbox)]


def span_line_box(lines: list[list[float]], at: float) -> list[float]:
    """The line a span sits on (by its position in the paragraph) with one line above and
    one below, as one box."""
    i = min(len(lines) - 1, max(0, int(at * len(lines))))
    a, b = lines[max(0, i - 1)], lines[min(len(lines) - 1, i + 1)]
    return [min(a[0], b[0]), a[1], max(a[2], b[2]), b[3]]


def _span_crops(doc, it: Item, spans: list[dict], lines_of: dict, crops_dir: Path,
                dpi: int) -> None:
    """Each span (or prose run) with a paragraph box: its line and the lines around it."""
    for n, s in enumerate(spans, 1):
        bb = s.get("bbox")
        if not bb:
            continue
        lines = lines_of.get(tuple(bb)) or line_boxes(doc, it.file, bb)
        lines_of[tuple(bb)] = lines
        s["line_box"] = span_line_box(lines, s.get("at", 0.5))
        name = f"{it.id}-s{n}.png"
        crop(doc, it.file, s["line_box"], crops_dir / name, dpi)
        s["crop"] = f"{crops_dir.name}/{name}"


def write_crops(doc, items: list[Item], crops_dir: Path, dpi: int = 300) -> None:
    """One crop per item; a grouped inline item gets one crop per text line of each
    flagged paragraph, and each of its spans a crop of its own line with the lines above
    and below (``line_box`` and ``crop`` in the span record)."""
    crops_dir = Path(crops_dir)
    for it in items:
        if it.bboxes:
            k = 0
            lines_of = {}
            for bb in it.bboxes:
                lines = line_boxes(doc, it.file, bb) if bb else [None]
                if bb:
                    lines_of[tuple(bb)] = lines
                for line in lines:
                    k += 1
                    name = f"{it.id}-{k}.png"
                    crop(doc, it.file, line, crops_dir / name, dpi)
                    it.crops.append(f"{crops_dir.name}/{name}")
            it.crop = it.crops[0]
            _span_crops(doc, it, it.spans or [], lines_of, crops_dir, dpi)
        elif it.extra.get("runs"):                  # inline_missing: a crop per run only
            _span_crops(doc, it, it.extra["runs"], {}, crops_dir, dpi)
            it.crop = next((r["crop"] for r in it.extra["runs"] if r.get("crop")), None)
            if it.crop is None:
                name = f"{it.id}.png"
                crop(doc, it.file, None, crops_dir / name, dpi)
                it.crop = f"{crops_dir.name}/{name}"
        else:
            name = f"{it.id}.png"
            crop(doc, it.file, it.bbox, crops_dir / name, dpi)
            it.crop = f"{crops_dir.name}/{name}"
