# SPDX-License-Identifier: AGPL-3.0-or-later
r"""The review queue: which formulas Claude should check, and why.

Rules, each on its own (kinds in order of precedence):
1. disagree: the two tiers' readings of a display differ after normalization, compared
   loosely (bold/roman markup, line breaks, dot style and trailing punctuation ignored:
   the trial's rule), or the second tier has no reading for it.
2. style: the tiers differ only in \mathbf / \mathrm / \mathit / \mathcal / \mathbb; or
   the same symbol (same subscript) is both bold and not on one page.
3. stack: a big operator whose limits hold two index variables, a relation, or a sum of
   indices: a printed multiple sum collapsed into one.
4. syntax: unbalanced braces / \left \right / \begin \end, or a command outside the
   whitelist (standard LaTeX math plus the trial's outputs).
5. tag: within a contiguous page run a tag repeats, goes backwards or skips a number.
6. inline: the inline spans of the two tiers, aligned paragraph by paragraph, differ.

Items carry both readings and a crop of the page region (300-400 dpi).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from gpdf.pipeline.mathocr import align, second
from gpdf.pipeline.mathocr import latex as L
from gpdf.pipeline.mathocr.assemble import Page, Paragraph, _paragraph

KINDS = ("disagree", "style", "stack", "syntax", "tag", "inline", "identity")
STYLE_CMDS = {r"\mathbf", r"\mathrm", r"\mathit", r"\mathcal", r"\mathbb", r"\mathsf",
              r"\mathfrak", r"\mathscr"}
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


def _style_free(norm: str) -> str:
    """value_only, with every style command (not just \\mathbf / \\mathrm) taken out."""
    toks = [r"\mathrm" if t in STYLE_CMDS else t for t in norm.split()]
    return L.value_only(" ".join(toks))


def style_only(primary: str, other: str | None) -> bool:
    """Rule 2: same value, different markup."""
    if other is None:
        return False
    a, b = _norm(primary), _norm(other)
    return a != b and _style_free(a) == _style_free(b)


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
    return sorted(s + sub for s, sub in bold & plain)


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


# ---------------------------------------------------------------- the queue

def _pid(file: int) -> str:
    return f"p{file:03d}"


def review_items(page: Page, other: dict, runs_tags: dict | None = None,
                 group_inline: bool = False) -> list[Item]:
    """The page's review items. ``other`` = {"displays": [raw latex], "paragraphs":
    [text blocks]} of the second tier; ``runs_tags`` maps (pNNN, display index) to a tag
    problem."""
    runs_tags = runs_tags or {}
    pid = _pid(page.file)
    mixed = bold_mixed(page)
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
                          d.latex, L.split_tag(sec)[0].strip() if sec else None, bbox=d.bbox))
    bad = [m for m in inline_pairs(page, other.get("paragraphs", [])) if not m.ok]
    if bad and group_inline:
        paras = page.paragraphs
        spans = [{"paragraph": m.paragraph + 1, "latex_primary": m.primary,
                  "latex_second": m.second} for m in bad]
        seen = sorted({m.paragraph for m in bad})
        items.append(Item(f"{pid}-inline", page.file, page.label.marker, "inline",
                          [f"{len(bad)} inline spans differ between tiers"], None, None, None,
                          bbox=paras[seen[0]].bbox, bboxes=[paras[i].bbox for i in seen],
                          spans=spans))
    elif bad:
        for k, m in enumerate(bad, 1):
            items.append(Item(f"{pid}-i{k:02d}", page.file, page.label.marker, "inline",
                              ["inline span differs between tiers" if m.second is not None
                               else "no second reading"], None, m.primary, m.second,
                              bbox=page.paragraphs[m.paragraph].bbox))
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


def write_crops(doc, items: list[Item], crops_dir: Path, dpi: int = 300) -> None:
    """One crop per item; a grouped inline item gets one crop per text line of each
    flagged paragraph."""
    crops_dir = Path(crops_dir)
    for it in items:
        if it.bboxes:
            k = 0
            for bb in it.bboxes:
                for line in (line_boxes(doc, it.file, bb) if bb else [None]):
                    k += 1
                    name = f"{it.id}-{k}.png"
                    crop(doc, it.file, line, crops_dir / name, dpi)
                    it.crops.append(f"{crops_dir.name}/{name}")
            it.crop = it.crops[0]
        else:
            name = f"{it.id}.png"
            crop(doc, it.file, it.bbox, crops_dir / name, dpi)
            it.crop = f"{crops_dir.name}/{name}"
