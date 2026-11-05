# SPDX-License-Identifier: AGPL-3.0-or-later
r"""LaTeX math as normalized token lists, and the pieces of a Markdown page.

The normalization is the trial scorer's (mathocr-trial/scripts/score.py), ported as is:
sizing, spacing and style wrappers dropped; synonyms folded (\leq -> \le, \boldsymbol ->
\mathbf, \text -> \mathrm, operator names -> \mathrm{..}); alignment environments and
their column specs removed, cases -> \{, matrices -> their delimiters; sub/superscript
order canonicalised; braces kept only where they are an argument; whitespace ignored.
\cdots and \dots stay distinct (a swap is cosmetic). The equation tag (\tag{..}, or a
trailing "(47.12)") is split off and compared separately.

Edit distance is pure Python: a bit-parallel distance (Hyyrö's version of Myers'
algorithm, on Python's long integers) for the long prose strings, and a plain
dynamic-programming backtrace for the edit operations of two short formulas.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------- extraction from Markdown

DISPLAY_RE = re.compile(
    r"\$\$(?P<a>.+?)\$\$"
    r"|\\\[(?P<b>.+?)\\\]"
    r"|(?P<c>\\begin\{(?P<env>equation\*?|align\*?|eqnarray\*?|gather\*?|multline\*?)\}"
    r".+?\\end\{(?P=env)\})",
    re.S)
TRAIL_TAG_RE = re.compile(r"[ \t]*\((\d+\s*\.\s*\d+[a-z]?)\)")          # used with .match(s, pos)
INLINE_RE = re.compile(r"\\\((.+?)\\\)|(?<![\\$])\$(?!\$)(.+?)(?<![\\$])\$(?!\$)", re.S)
COMMENT_RE = re.compile(r"<!--.*?-->", re.S)


def extract(md: str) -> tuple[list[tuple[str, str | None]], list[str], str]:
    """-> displays [(latex, trailing tag)], inline [latex], prose (math and comments
    taken out)."""
    md = COMMENT_RE.sub("", md)
    displays, prose, pos = [], [], 0
    for m in DISPLAY_RE.finditer(md):
        prose.append(md[pos:m.start()])
        body = m.group("a") or m.group("b") or m.group("c")
        pos = m.end()
        t = TRAIL_TAG_RE.match(md, pos)
        tag = None
        if t:
            tag, pos = t.group(1), t.end()
        displays.append((body, tag))
        prose.append(" ")
    prose.append(md[pos:])
    text = "".join(prose)
    inline = [a or b for a, b in INLINE_RE.findall(text)]
    text = INLINE_RE.sub(" ", text)
    return displays, inline, text


# ---------------------------------------------------------------- normalization

DROP_CMDS = {r"\left", r"\right", r"\middle", r"\big", r"\Big", r"\bigg", r"\Bigg", r"\bigl",
             r"\bigr", r"\Bigl", r"\Bigr", r"\biggl", r"\biggr", r"\Biggl", r"\Biggr", r"\bigm",
             r"\Bigm", r"\displaystyle", r"\textstyle", r"\scriptstyle", r"\limits", r"\nolimits",
             r"\quad", r"\qquad", r"\,", r"\;", r"\!", r"\:", r"\>", r"\ ", r"\enspace",
             r"\thinspace", r"\hfill", r"~", r"&", r"\nonumber", r"\notag"}
SYN = {r"\leq": r"\le", r"\geq": r"\ge", r"\neq": r"\ne", r"\rightarrow": r"\to",
       r"\lbrace": r"\{", r"\rbrace": r"\}", r"\vert": "|", r"\lvert": "|", r"\rvert": "|",
       r"\ldots": r"\dots", r"\dotsc": r"\dots", r"\dotsb": r"\cdots", r"\dfrac": r"\frac",
       r"\tfrac": r"\frac", r"\widehat": r"\hat", r"\widetilde": r"\tilde", r"\overline": r"\bar",
       r"\bm": r"\mathbf", r"\pmb": r"\mathbf", r"\boldsymbol": r"\mathbf", r"\mathbold": r"\mathbf",
       r"\text": r"\mathrm", r"\textrm": r"\mathrm", r"\mbox": r"\mathrm",
       r"\operatorname": r"\mathrm", r"\textnormal": r"\mathrm", r"\hbox": r"\mathrm",
       r"\lVert": r"\|", r"\rVert": r"\|", r"\Vert": r"\|", r"\gets": r"\leftarrow",
       r"\land": r"\wedge", r"\lor": r"\vee", r"\operatorname*": r"\mathrm", r"\mid": "|",
       r"\overset": r"\stackrel"}
OPNAMES = {"exp", "log", "ln", "max", "min", "lim", "sup", "inf", "det", "Pr", "sin", "cos",
           "tan", "arg", "deg", "dim", "ker", "sinh", "cosh", "tanh", "liminf", "limsup", "gcd",
           "mod"}
TOKEN_RE = re.compile(r"\\[A-Za-z]+\*?|\\.|[^\s]")
TAG_RE = re.compile(r"\\tag\*?\s*\{([^{}]*)\}")
END_TAG_RE = re.compile(r"\(\s*(\d+\s*\.\s*\d+[a-z]?)\s*\)\s*[.,;]?\s*$")
ARG_CMDS = {r"\frac", r"\sqrt", r"\mathrm", r"\mathbf", r"\mathcal", r"\mathbb", r"\mathsf",
            r"\mathit", r"\mathfrak", r"\mathscr", r"\hat", r"\bar", r"\tilde", r"\dot", r"\ddot",
            r"\vec", r"\check", r"\breve", r"\acute", r"\grave", r"\underline", r"\binom",
            r"\stackrel", r"\overset", r"\underbrace", r"\overbrace", r"\boxed", r"\phantom"}
TWO_ARG = {r"\frac", r"\binom", r"\stackrel", r"\overset"}
ALIGN_ENVS = (r"aligned|align\*?|eqnarray\*?|array|gathered|gather\*?|split|multline\*?"
              r"|equation\*?|alignedat|subarray")
MATRIX_ENVS = {"pmatrix": ("(", ")"), "bmatrix": ("[", "]"), "vmatrix": ("|", "|"),
               "Bmatrix": (r"\{", r"\}"), "matrix": ("", ""), "smallmatrix": ("", "")}


def split_tag(latex: str, trailing: str | None = None) -> tuple[str, str | None]:
    """(body without its tag, tag): \\tag{..} inside, else the trailing "(47.12)" given,
    else a "(47.12)" written at the end of the body."""
    tags = TAG_RE.findall(latex)
    latex = TAG_RE.sub(" ", latex)
    tag = tags[-1] if tags else trailing
    if tag is None:
        bare = re.sub(r"\\(q?quad|[,;!: ])|\\hfill|~", " ", latex)
        bare = re.sub(r"\\end\{[a-z*]+\}\s*$", "", bare.rstrip())
        m = END_TAG_RE.search(bare)
        if m:
            tag = m.group(1)
            i = latex.rfind("(")
            latex = latex[:i] + latex[latex.find(")", i) + 1:]
    return latex, (re.sub(r"\s", "", tag) if tag else None)


def strip_groups(toks: list[str]) -> list[str]:
    """Drop braces that only group (not an argument of ^, _ or an argument-taking
    command)."""
    out, stack, first_arg_close = [], [], set()
    for t in toks:
        if t == "{":
            prev = out[-1] if out else None
            second = prev == "}" and len(out) - 1 in first_arg_close
            keep = prev in ("^", "_") or prev in ARG_CMDS or second
            stack.append((keep, prev in TWO_ARG))
            if keep:
                out.append("{")
        elif t == "}" and stack:
            keep, is_first_of_two = stack.pop()
            if keep:
                out.append("}")
                if is_first_of_two:
                    first_arg_close.add(len(out) - 1)
        else:
            out.append(t)
    return out


def atom_end(toks: list[str], i: int) -> int:
    """Index after the atom starting at i (a token or a balanced {...} group)."""
    if i < len(toks) and toks[i] == "{":
        depth = 0
        for j in range(i, len(toks)):
            depth += toks[j] == "{"
            depth -= toks[j] == "}"
            if depth == 0:
                return j + 1
        return len(toks)
    return i + 1


def order_scripts(toks: list[str]) -> list[str]:
    """x^a_b -> x_b^a so both script orders compare equal."""
    out, i = [], 0
    while i < len(toks):
        if toks[i] in ("^", "_"):
            scripts = []
            while i < len(toks) and toks[i] in ("^", "_"):
                e = atom_end(toks, i + 1)
                scripts.append((toks[i], toks[i + 1:e]))
                i = e
            if len(scripts) == 2 and scripts[0][0] == "^" and scripts[1][0] == "_":
                scripts.reverse()
            for op, a in scripts:
                if a[:1] != ["{"]:
                    out += [op, *order_scripts(a)]
                else:
                    out += [op, "{", *order_scripts(a[1:-1]), "}"]
            continue
        out.append(toks[i])
        i += 1
    return out


def _content(atom: list[str]) -> list[str]:
    return atom[1:-1] if atom[:1] == ["{"] and atom[-1:] == ["}"] else atom


def merge_primes(toks: list[str]) -> list[str]:
    r"""Notation-only prime forms made equal: x'^2 (two superscripts, the first a prime)
    -> x^{\prime 2}, x'' -> x^{\prime \prime}, and inside a superscript a prime raised
    once more (a^{*^{\prime}}) -> a^{* \prime}."""
    out, i = [], 0
    while i < len(toks):
        if toks[i] == "^":
            sups = []
            while i < len(toks) and toks[i] == "^":
                e = atom_end(toks, i + 1)
                sups.append(_content(toks[i + 1:e]))
                i = e
            if len(sups) > 1 and sups[0][:1] == [r"\prime"]:
                sups = [[t for s in sups for t in s]]
            for s in sups:
                inner = merge_primes(s)
                inner = [t for k, t in enumerate(inner)
                         if not (t == "^" and inner[k + 1:k + 2] == [r"\prime"]
                                 and k and inner[k - 1] in ("*", r"\ast"))]
                out += ["^", inner[0]] if len(inner) == 1 else ["^", "{", *inner, "}"]
            continue
        if toks[i] == "{":
            e = atom_end(toks, i)
            out += ["{", *merge_primes(toks[i + 1:e - 1]), "}"]
            i = e
            continue
        out.append(toks[i])
        i += 1
    return out


def _stacked_limits(toks: list[str]) -> list[str]:
    r"""_{\stackrel{a}{b}} (two stacked lines of limits) -> _{a \\ b}, as \substack."""
    out, i = [], 0
    while i < len(toks):
        if toks[i] == "_" and i + 1 < len(toks):
            e = atom_end(toks, i + 1)
            c = _content(toks[i + 1:e])
            if c[:1] == [r"\stackrel"]:
                a_end = atom_end(c, 1)
                b_end = atom_end(c, a_end)
                if b_end == len(c):
                    out += ["_", "{", *_content(c[1:a_end]), r"\\", *_content(c[a_end:b_end]), "}"]
                    i = e
                    continue
        out.append(toks[i])
        i += 1
    return out


def _text_punct_out(toks: list[str]) -> list[str]:
    r"""\mathrm{otherwise,} -> \mathrm{otherwise} , (punctuation inside or after text)."""
    out, i = [], 0
    while i < len(toks):
        if toks[i] == r"\mathrm" and toks[i + 1:i + 2] == ["{"]:
            e = atom_end(toks, i + 1)
            inner = toks[i + 2:e - 1]
            tail = []
            while inner and inner[-1] in (",", ".", ";"):
                tail.insert(0, inner.pop())
            out += [r"\mathrm", "{", *inner, "}", *tail] if inner else [*tail]
            i = e
            continue
        out.append(toks[i])
        i += 1
    return out


BRACED = r"\{((?:[^{}]|\{(?:[^{}]|\{[^{}]*\})*\})*)\}"


def tokens(latex: str) -> list[str]:
    """The normalized token list of a formula (its tag already split off)."""
    s = latex
    s = s.replace("\u201c", '"').replace("\u201d", '"')
    s = re.sub(r"\\operatorname\s*\*", r"\\operatorname", s)
    s = re.sub(r"\\underset\s*\{((?:[^{}]|\{[^{}]*\})*)\}\s*\{((?:[^{}]|\{[^{}]*\})*)\}",
               r"{\2}_{\1}", s)
    s = re.sub(r"\\[hv]?phantom\s*" + BRACED, " ", s)
    s = re.sub(r"\\[dt]?binom\s*" + BRACED + r"\s*" + BRACED, r" ( \1 \\\\ \2 ) ", s)
    s = re.sub(r"\{\s*\\bf\s+([^{}]*)\}", r"\\mathbf{\1}", s)
    s = re.sub(r"\{\s*\\rm\s+([^{}]*)\}", r"\\mathrm{\1}", s)
    s = re.sub(r"\\bf\b", r"\\mathbf", s)
    s = re.sub(r"\\mbox\s*\{\s*\\boldmath\s*\$(.*?)\$\s*\}", r"\\mathbf{\1}", s)
    s = re.sub(r"\{\s*\\boldmath\s*\$(.*?)\$\s*\}", r"\\mathbf{\1}", s)
    s = re.sub(r"\\(left|right)\s*\.", " ", s)
    s = re.sub(r"\\(hspace|vspace)\*?\s*\{[^{}]*\}", " ", s)
    s = re.sub(r"\\\\\s*\[[^\]]*\]", r"\\\\ ", s)
    s = re.sub(r"\\begin\s*\{array\}\s*\{[^{}]*\}", r"\\begin{array}", s)
    s = re.sub(r"\\begin\s*\{(" + ALIGN_ENVS + r")\}(\{[^{}]*\})?", " ", s)
    s = re.sub(r"\\end\s*\{(" + ALIGN_ENVS + r")\}", " ", s)
    s = re.sub(r"\\begin\s*\{cases\}", r"\\{ ", s)
    s = re.sub(r"\\end\s*\{cases\}", " ", s)
    for env, (l, r) in MATRIX_ENVS.items():
        s = re.sub(r"\\begin\s*\{%s\}" % env, " %s " % l.replace("\\", "\\\\"), s)
        s = re.sub(r"\\end\s*\{%s\}" % env, " %s " % r.replace("\\", "\\\\"), s)
    toks = []
    for t in TOKEN_RE.findall(s):
        t = SYN.get(t, t)
        if t in DROP_CMDS:
            continue
        if t.startswith("\\") and t[1:] in OPNAMES:
            toks += [r"\mathrm", "{", *t[1:], "}"]
            continue
        if t == "'":
            toks += ["^", r"\prime"]
            continue
        if t == r"\substack":
            continue
        toks.append(t)
    toks = order_scripts(merge_primes(strip_groups(toks)))
    toks = _text_punct_out(_stacked_limits(toks))
    changed = True
    while changed:                      # braces around nothing or one token, repeatedly
        changed, out, i = False, [], 0
        while i < len(toks):
            if toks[i] == "{" and i + 1 < len(toks) and toks[i + 1] == "}":
                i += 2
                changed = True
                continue
            if (toks[i] == "{" and i + 2 < len(toks) and toks[i + 2] == "}"
                    and toks[i + 1] not in "{}"):
                out.append(toks[i + 1])
                i += 3
                changed = True
                continue
            out.append(toks[i])
            i += 1
        toks = out
    while toks and toks[-1] == r"\\":
        toks.pop()
    return toks


def norm_str(toks: list[str]) -> str:
    return " ".join(toks)


def value_only(s: str) -> str:
    """A normalized string without the markup that never changes the value: \\mathbf /
    \\mathrm (their argument kept), line breaks, dot style, trailing punctuation."""
    t, out, skip_close, depth = s.split(), [], [], 0
    i = 0
    while i < len(t):
        x = t[i]
        if x in (r"\mathbf", r"\mathrm"):
            if i + 1 < len(t) and t[i + 1] == "{":
                skip_close.append(depth)
                depth += 1
                i += 2
                continue
            i += 1
            continue
        if x == "{":
            depth += 1
        elif x == "}":
            depth -= 1
            if skip_close and skip_close[-1] == depth:
                skip_close.pop()
                i += 1
                continue
        if x != r"\\":
            out.append(r"\dots" if x == r"\cdots" else x)
        i += 1
    while out and out[-1] in (",", ".", ";"):
        out.pop()
    return " ".join(out)


STYLE_CMDS = {r"\mathbf", r"\mathrm", r"\mathit", r"\mathcal", r"\mathbb", r"\mathsf",
              r"\mathfrak", r"\mathscr"}


def style_free(norm: str) -> str:
    """value_only, with every style command (not just \\mathbf / \\mathrm) taken out and
    the braces it leaves behind normalized again (\\mathbf{X} in a subscript -> X)."""
    return value_only(norm_str(tokens(" ".join(t for t in norm.split() if t not in STYLE_CMDS))))


# ---------------------------------------------------------------- edit distance

def distance(a, b) -> int:
    """Levenshtein distance of two strings or two token lists (bit-parallel)."""
    if len(a) < len(b):
        a, b = b, a
    m = len(a)
    if m == 0:
        return len(b)
    if len(b) == 0:
        return m
    peq: dict = {}
    for i, c in enumerate(a):
        peq[c] = peq.get(c, 0) | (1 << i)
    mask = (1 << m) - 1
    hbit = 1 << (m - 1)
    pv, mv, score = mask, 0, m
    for c in b:
        eq = peq.get(c, 0)
        xv = eq | mv
        xh = (((eq & pv) + pv) ^ pv) | eq
        ph = (mv | ~(xh | pv)) & mask
        mh = pv & xh
        if ph & hbit:
            score += 1
        elif mh & hbit:
            score -= 1
        ph = ((ph << 1) | 1) & mask
        mh = (mh << 1) & mask
        pv = (mh | ~(xv | ph)) & mask
        mv = ph & xv
    return score


def ned(a, b) -> float:
    """Distance divided by the longer length; 0 for two empty inputs."""
    if not a and not b:
        return 0.0
    return distance(a, b) / max(len(a), len(b))


def editops(a: list, b: list) -> list[tuple[str, str, str]]:
    """The edit operations turning a into b: (replace|insert|delete, a token, b token),
    with "" for the missing side. Dynamic programming with backtrace (short inputs)."""
    n, m = len(a), len(b)
    d = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        d[i][0] = i
    for j in range(1, m + 1):
        d[0][j] = j
    for i in range(1, n + 1):
        ai = a[i - 1]
        row, prev = d[i], d[i - 1]
        for j in range(1, m + 1):
            row[j] = min(prev[j] + 1, row[j - 1] + 1, prev[j - 1] + (ai != b[j - 1]))
    ops = []
    i, j = n, m
    while i > 0 or j > 0:
        if i > 0 and j > 0 and d[i][j] == d[i - 1][j - 1] and a[i - 1] == b[j - 1]:
            i, j = i - 1, j - 1
        elif i > 0 and j > 0 and d[i][j] == d[i - 1][j - 1] + 1:
            ops.append(("replace", a[i - 1], b[j - 1]))
            i, j = i - 1, j - 1
        elif i > 0 and d[i][j] == d[i - 1][j] + 1:
            ops.append(("delete", a[i - 1], ""))
            i -= 1
        else:
            ops.append(("insert", "", b[j - 1]))
            j -= 1
    ops.reverse()
    return ops


# edits that never change the value: dot style, trailing punctuation, bold/roman markup,
# line breaks
COSMETIC = {r"\cdots", r"\dots", ",", ".", ";", r"\mathrm", r"\mathbf", r"\\", r"\cdot"}


def is_cosmetic(diffs: list[tuple[str, str, str]]) -> bool:
    """A non-empty edit list whose every token is cosmetic."""
    return bool(diffs) and all((x in COSMETIC or x == "") and (y in COSMETIC or y == "")
                               for _, x, y in diffs)


# ---------------------------------------------------------------- prose

def prose_norm(t: str) -> str:
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"\\(begin|end)\{[^}]*\}(\{[^}]*\})?|\\hline|\\\\|&", " ", t)
    t = re.sub(r"(?m)^\s*#+\s*", "", t)
    t = re.sub(r"[*_|]+", " ", t)
    t = re.sub(r"(?m)^\s*:?-{3,}:?\s*$", " ", t)
    t = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", t)
    t = re.sub(r"\s+", " ", t)
    return t.strip()


# ---------------------------------------------------------------- syntax helpers

CMD_RE = re.compile(r"\\([A-Za-z]+\*?)")
ENV_RE = re.compile(r"\\(begin|end)\s*\{([A-Za-z*]+)\}")


def commands(latex: str) -> set[str]:
    """The command names used (without the backslash; "operatorname*" keeps its star)."""
    return set(CMD_RE.findall(latex))


TRAIL_BREAK_RE = re.compile(r"\\\\\s*(?:\[[^\]]*\])?(?:\s*\\hline)*\s*(?=\\end\s*\{|\\tag\b|$)")
SUBSTACK_RE = re.compile(r"\\substack\s*" + BRACED)


def row_breaks(latex: str) -> int:
    """The `\\\\` row breaks of a display, a trailing one (before \\end or the end) and the
    rows of a \\substack left out."""
    s = TRAIL_BREAK_RE.sub(" ", SUBSTACK_RE.sub(" ", latex))
    return sum(1 for t in TOKEN_RE.findall(s) if t == "\\\\")


def structure(latex: str) -> dict:
    """What a display's layout rests on: row breaks, alignment points, environments,
    \\left/\\right pairs, control spaces (`\\ `) and balance problems."""
    toks = TOKEN_RE.findall(latex)
    # alignment: the rows that carry an `&` (an `r c l` array's `& = &` and an aligned
    # `&=` align a row alike)
    rows = " ".join(toks).split("\\\\")
    return {"row_breaks": row_breaks(latex), "align": sum(1 for r in rows if "&" in r.split()),
            "envs": len(re.findall(r"\\begin\s*\{", latex)),
            "left": len(re.findall(r"\\left\b", latex)),
            "control_space": toks.count("\\ "), "balance": balance_problems(latex)}


def balance_problems(latex: str) -> list[str]:
    """Unbalanced braces, \\left/\\right or \\begin/\\end, as short messages."""
    problems = []
    depth, bad = 0, False
    for t in TOKEN_RE.findall(latex):
        if t == "{":
            depth += 1
        elif t == "}":
            depth -= 1
            if depth < 0:
                bad = True
    if bad or depth:
        problems.append("unbalanced braces")
    lr = 0
    for t in re.findall(r"\\(left|right)\b", latex):
        lr += 1 if t == "left" else -1
        if lr < 0:
            problems.append("\\right without \\left")
            lr = 0
    if lr > 0:
        problems.append("\\left without \\right")
    stack = []
    for kind, env in ENV_RE.findall(latex):
        if kind == "begin":
            stack.append(env)
        elif stack and stack[-1] == env:
            stack.pop()
        elif env in stack:
            problems.append(f"\\begin{{{stack[-1]}}} without \\end")
            while stack and stack[-1] != env:
                stack.pop()
            stack.pop()
        else:
            problems.append(f"\\end{{{env}}} without \\begin")
    for env in stack:
        problems.append(f"\\begin{{{env}}} without \\end")
    return problems
