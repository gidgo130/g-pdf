# SPDX-License-Identifier: AGPL-3.0-or-later
r"""Numeric identity checks on the displays that are real expressions: a density must
integrate to 1, a cdf must run from 0 to 1 without decreasing, a moment generating
function must be 1 at 0 with a non-negative variance, F' = f for a pair in one section.
They catch wrong exponents, signs and constants, whether the engine misread the formula
or the book printed it wrong; a failure is a review item that says so.

Needs the optional ``mathcheck`` extra (sympy, mpmath, antlr4-python3-runtime 4.11 for
sympy's parse_latex). A display that will not parse is skipped, never flagged: this pass
never judges syntax. Parameters are unknown, so every check runs at DRAWS stratified
positive draws of each parameter; a check that fails for some draws and passes for
others is inconclusive and not flagged. Each display gets TIME_LIMIT seconds.

Classification, by the left-hand side (or the text just before it):

    density   f(x) f_X(x) p(x_1, x_2)        cdf       F(x)  Pr[X <= x]
    survival  \bar F(x)  S(x)  Pr[X > x]      hazard    h(x)
    mgf       M(t)  E[e^{tX}]  E[e^{t_1X_1+t_2X_2}]
    cf        \varphi(t)                      pgf       G(z)  E[z^X]
    mean      E[X]  (only for the pair with M'(0))
    text:     "density", "distribution function", "survival", "hazard",
              "moment generating", "characteristic function", "probability generating"
              decide a function the table does not know (\psi(x), ...).
"""

from __future__ import annotations

import random
import re
import time
from collections import Counter
from dataclasses import dataclass, field

from gpdf.pipeline.mathocr import latex as L
from gpdf.pipeline.mathocr import watch

try:
    import mpmath
    import sympy
    from sympy.core.function import AppliedUndef
    from sympy.parsing.latex import parse_latex
    import antlr4  # noqa: F401  (parse_latex needs it at call time)
    AVAILABLE = True
except ImportError:                                   # pragma: no cover - depends on extras
    AVAILABLE = False

TIME_LIMIT = 5.0           # seconds per display (read between function evaluations)
ITEM_TIMEOUT = 20.0        # hard limit per display (watch.hard_timeout), whatever it is doing
_clock = time.monotonic    # the deadline's clock (tests inject a counting one)
DRAWS = 4                  # parameter draws, stratified over (LO, HI)
LO, HI = 0.3, 2.5
SEED = "g-pdf mathcheck"
TOL = 1e-6                 # relative, for identities
TOL_INT = 1e-3             # for integrals
TOL_INT_2D = 1e-2          # a bounded-degree cubature is coarser
LIMIT_TOL = 0.02           # for cdf / survival limits
BIG = 1e6
SUPPORTS = {"(0,inf)": ([0.1, 0.5, 1.0, 2.0, 5.0], 1e-9, BIG),
            "(-inf,inf)": ([-3.0, -1.0, 0.0, 1.0, 3.0], -BIG, BIG),
            "(0,1)": ([0.1, 0.3, 0.5, 0.7, 0.9], 1e-9, 1 - 1e-9)}
QUAD_RANGES = {"(0,inf)": [0, 1, "inf"], "(-inf,inf)": ["-inf", 0, "inf"], "(0,1)": [0, 1]}
PAIR_POINTS = [0.5, 1.0, 2.0]
NOTE = ("an identity failed: this may be a printed error rather than a misread. Compare the "
        "crop; if the LaTeX matches the page, mark possible_source_typo instead of changing it")
KINDS = {"f": "density", "p": "density", "g": "density", "F": "cdf", r"\bar{F}": "survival",
         r"\bar F": "survival", r"\overline{F}": "survival", "S": "survival", "h": "hazard",
         "M": "mgf", r"\varphi": "cf", "G": "pgf"}
GREEK_LOWER = {"alpha", "beta", "gamma", "delta", "epsilon", "varepsilon", "zeta", "eta",
               "theta", "vartheta", "iota", "kappa", "lambda", "mu", "nu", "xi", "rho", "varrho",
               "sigma", "varsigma", "tau", "upsilon", "phi", "varphi", "chi", "psi", "omega"}
FUNCTION_GREEK = {"phi", "varphi", "psi"}             # written before a bracket, a function
# pi is a constant, never a function: \pi (p - 0.5) is a product too
GREEK_BRACKET = re.compile(r"(\\(?:" + "|".join(sorted((GREEK_LOWER - FUNCTION_GREEK) | {"pi"}, key=len,
                                                       reverse=True))
                           + r")(?![A-Za-z])(?:_\{[^{}]*\}|_[A-Za-z0-9])?)\s*\(")
UNKNOWN_CONSTANTS = {"c", "C", "K", "const"}           # a normalizing constant left unnamed
HINTS = [("density", "density"), ("distribution function", "cdf"), ("survival", "survival"),
         ("hazard", "hazard"), ("moment generating", "mgf"), ("characteristic function", "cf"),
         ("probability generating", "pgf")]
LHS_FN = re.compile(r"^(?P<name>\\bar\{F\}|\\bar F|\\overline\{F\}|\\[A-Za-z]+|[A-Za-z])\s*"
                    r"(?:_(?:\{(?P<sub>(?:[^{}]|\{[^{}]*\})*)\}|(?P<sub1>[A-Za-z0-9])))?"
                    r"\s*\((?P<args>[^()]*)\)$")
PR_RE = re.compile(r"^\\Pr\s*[\[(]\s*(?P<inner>.*)[\])]$")
E_RE = re.compile(r"^E\s*[\[(]\s*(?P<inner>.*?)\s*[\])]$")
REL_VAR = re.compile(r"(>|\\ge|\\geq|<|\\le|\\leq)\s*([A-Za-z](?:_\{?[A-Za-z0-9]+\}?)?)")
T_VAR = re.compile(r"(?<![A-Za-z\\])t(?:_\{?(\d+)\}?)?")
RELATION = re.compile(r"<|>|\\le\b|\\ge\b|\\leq\b|\\geq\b|\\ne\b|\\neq\b|\\in\b")
NOT_IDENTITY = re.compile(r"\\(doteq|approx|propto|equiv|sim|cong)\b")
UNSUPPORTED_ENV = re.compile(r"\\begin\{(cases|[pbvB]?matrix|smallmatrix)\}")
UNSUPPORTED = re.compile(r"\\text\b|\\mathrm\{(?!e\}|d\})|\\partial\b"
                         r"|_\s*\{?\s*\d\s*\}?\s*F\s*_\s*\{?\s*\d"        # {}_2F_1[...]
                         r"|\\(int|iint|iiint|oint|sum|prod|dots|cdots|ldots)(?![A-Za-z])")
VECTOR = re.compile(r"boldsymbol|mathbf|pmb|^bm|dots")
# words between the equations of a row ("and", "for", "if") separate them like \quad
TEXT_WORD = re.compile(r"\\(?:text|mathrm|textrm|mbox)\s*\{\s*(?![ed]\s*\})[^{}]*\}")
VAR_NAME = re.compile(r"\\(?:operatorname\*?|mathrm|text|textrm|mbox)\s*\{\s*[Vv]\s*a\s*r\s*\}")
E_NAME = re.compile(r"\\(?:operatorname\*?|mathrm|mathbb|text|mathsf)\s*\{\s*E\s*\}")
CHOOSE = re.compile(r"\{\s*([^{}]*?)\s*\\choose\s*([^{}]*?)\s*\}")
DISCRETE = ("pmf", "pmf_point", "recurrence", "pgf")
MOMENT_KINDS = ("mean", "variance")
DEF_NAMES = {"q", "Q"} | {"c", "C", "K"}              # names a section may define: C = ...
INT_PARAMS = {"n": [5, 6, 8, 10], "m": [5, 6, 8, 10], "M": [5, 6, 8, 10], "N": [12, 15, 18, 21],
              "a": [7, 9, 12, 15], "b": [7, 9, 12, 15]}     # hypergeometric counts (b >= n)
PROB_PARAMS = {"p", "theta", "pi", "omega", "q"}
PROB_LO, PROB_HI = 0.1, 0.9
REC_POINTS = [1, 2, 3]
PMF_VARS = "xyknrjim"
PMF_TERM = re.compile(
    r"\\Pr\s*[\[(]\s*X\s*=\s*(?P<a>[^\[\]()]+?)\s*[\])]"
    r"|(?<![A-Za-z\\])P\s*\(\s*X\s*=\s*(?P<b>[^()]+?)\s*\)"
    r"|(?<![A-Za-z\\])[pP]_\{(?P<c>[^{}]+)\}"
    r"|(?<![A-Za-z\\])[pP]_(?P<d>[A-Za-z])(?![A-Za-z])")
MEAN_LHS = re.compile(r"^(?:\\mu|\\mu(?:_\{?1\}?)?(?:'|\^\{?\\prime\}?)(?:_\{?1\}?)?)$")
VARIANCE_LHS = re.compile(r"^(?:\\Var\s*[\[(]\s*X\s*[\])]|\\sigma\^\{?2\}?|\\mu_\{?2\}?)$")


class _Timeout(BaseException):
    """The display's time budget is spent (not an Exception: the numeric error handlers
    must not swallow it)."""


@dataclass
class Check:
    identity: str
    status: str                      # passed | failed | inconclusive | skipped
    details: dict = field(default_factory=dict)


@dataclass
class Result:
    kind: str | None
    status: str                      # passed | failed | inconclusive | skipped
    checks: list[Check] = field(default_factory=list)
    support: str | None = None
    reason: str | None = None        # why skipped
    note: str | None = None


@dataclass
class _Expr:
    kind: str
    sub: str
    variables: list[str]
    fn: object
    params: list[str]
    lhs: str
    terms: list = field(default_factory=list)     # recurrence: (symbol, shift | None, const)


class Context:
    """The classified expressions of the current section, for the pair checks, and the
    constants the section defines (``C = ...``, ``q = 1 - p``), as LaTeX."""

    def __init__(self):
        self.exprs: list[_Expr] = []
        self.section = ""
        self.defs: dict[str, str] = {}

    def new_section(self, heading: str, defs: dict | None = None) -> None:
        self.section = heading
        self.exprs = []
        self.defs = dict(defs or {})

    def find(self, kind: str, sub: str | None = None, nvars: int | None = None,
             sub_first: str | None = None) -> _Expr | None:
        for e in reversed(self.exprs):
            if e.kind != kind:
                continue
            if sub is not None and e.sub != sub:
                continue
            if nvars is not None and len(e.variables) != nvars:
                continue
            if sub_first is not None and e.sub.split(",")[0].strip() != sub_first:
                continue
            return e
        return None


# ---------------------------------------------------------------- preprocessing

_CLEAN = [
    (re.compile(r"\\(left|right)\s*\."), " "),
    (re.compile(r"\\(left|right|middle)\b"), ""),
    (re.compile(r"\\[bB]igg?[lrm]?\b"), ""),
    (re.compile(r"\\(displaystyle|textstyle|scriptstyle|limits|nolimits|nonumber|notag)\b"), ""),
    (re.compile(r"\\[,;:!>]|\\quad\b|\\qquad\b|~|\\ "), " "),
    (re.compile(r"\\(hspace|vspace)\*?\s*\{[^{}]*\}"), " "),
    (re.compile(r"\\mathrm\{e\}"), "e"),
    (re.compile(r"\\mathrm\{d\}"), "d"),
    (re.compile(r"\\operatorname\s*\*?\s*\{\s*exp\s*\}"), r"\\exp"),
    (re.compile(r"\\operatorname\s*\*?\s*\{\s*P\s*r\s*\}"), r"\\Pr"),
    (re.compile(r"\s*([_^{}])\s*"), r"\1"),          # the engine's "X _ {1}" is X_{1}
    (re.compile(r"\\prime"), "'"),
    (re.compile(r"\^\{'\}"), "'"),
]
_INDICATOR = [
    re.compile(r"\\(mathbf|mathbb|boldsymbol|pmb|bm)\{1\}(?:_\{[^{}]*\}|\([^()]*\))?"),
    re.compile(r"(?<![A-Za-z\\])I\s*[\[(][^\[\]()]*(?:<|>|\\le|\\ge|\\leq|\\geq)[^\[\]()]*[\])]"),
    re.compile(r"(?<![A-Za-z\\])I_\{[^{}]*\}\s*\([^()]*\)"),
]
ERF_NAME = re.compile(r"\\(?:operatorname\*?|mathrm|text|rm)\s*\{\s*(erfc|erf)\s*\}")
PHI_INV = re.compile(r"\\Phi\s*\^\s*\{\s*-\s*1\s*\}")
ALIGN_ENV = re.compile(r"\\(begin|end)\s*\{(aligned|align\*?|array|eqnarray\*?|gathered|gather\*?"
                       r"|split|multline\*?|equation\*?|alignedat)\}(\{[^{}]*\})?")


def _clean(s: str) -> str:
    for rx, rep in _CLEAN:
        s = rx.sub(rep, s)
    return " ".join(s.split())


def _split_top(s: str, seps) -> list[str]:
    """Split at the separators (single characters or commands) at brace/bracket depth 0."""
    out, depth, i, start = [], 0, 0, 0
    while i < len(s):
        c = s[i]
        if c in "{([":
            depth += 1
        elif c in "})]":
            depth -= 1
        elif depth == 0:
            for sep in seps:
                if s.startswith(sep, i) and (not sep[-1].isalpha()
                                             or not s[i + len(sep):i + len(sep) + 1].isalpha()):
                    out.append(s[start:i])
                    i += len(sep)
                    start = i
                    break
            else:
                i += 1
            continue
        i += 1
    out.append(s[start:])
    return out


SEPARATORS = [",", ";", r"\qquad", r"\quad"]


def _strip_conditions(rhs: str) -> str:
    """The expression before a top-level comma, semicolon or \\quad (a side condition)."""
    first = _split_top(rhs, SEPARATORS)[0]
    return first.strip().rstrip(".,;").strip()


def _pre(body: str) -> str:
    """Names the classifier knows (Var, E, binomials) in one spelling; words between
    equations become separators."""
    body = VAR_NAME.sub(r"\\Var ", body)
    body = E_NAME.sub("E", body)
    body = CHOOSE.sub(r"\\binom{\1}{\2}", body)
    body = TEXT_WORD.sub(" ; ", body)
    return re.sub(r"\\q?quad\b", " ; ", body)       # _clean would make spaces of them


def equations(latex: str, conditions: bool = False) -> list[tuple]:
    """(lhs, rhs) pairs of a display: aligned rows, = chains and equations separated by a
    comma, \\quad or a word split; side conditions dropped; an equation whose side holds a
    sum, an integral or dots is left out. Empty for a display that is no identity. With
    ``conditions``: (lhs, rhs, condition) triples, the condition being what followed the
    equation on its row (or a row of its own that is only a relation), "" if none."""
    body, _ = L.split_tag(latex)
    if NOT_IDENTITY.search(body) or UNSUPPORTED_ENV.search(body):
        return []
    body = ALIGN_ENV.sub(" ", _pre(body))
    rows = [r.replace("&", " ") for r in re.split(r"\\\\(?:\[[^\]]*\])?", body)]
    eqs, conds, lhs = [], [], None
    for row in rows:
        row = _clean(row).strip().strip(",.;").strip()
        if not row:
            continue
        parts = _split_top(row, ["="])
        if len(parts) >= 2:
            if parts[0].strip():
                lhs = parts[0].strip()
            elif lhs is None:
                continue
            k = 1
            while k < len(parts):
                pieces = _split_top(parts[k], SEPARATORS)
                r = pieces[0].strip().rstrip(".,;").strip()
                chain = len(pieces) > 1 and k + 1 < len(parts)
                if r:
                    eqs.append((lhs, r))
                    conds.append(" , ".join(p.strip() for p in pieces[1:-1 if chain else None]
                                            if p.strip()))
                if chain:
                    # "mu = np, sigma^2 = npq": the last piece before the next "=" is a
                    # new left-hand side; a condition without "=" ends the chain
                    lhs = pieces[-1].strip()
                    if not lhs:
                        break
                k += 1
        elif eqs and not RELATION.search(row):
            l, r = eqs[-1]
            eqs[-1] = (l, r + " " + _strip_conditions(row))
        elif eqs and not conds[-1]:
            conds[-1] = row                  # a row that is only a relation: t > 0
    out = [(l, r, c) for (l, r), c in zip(eqs, conds)
           if not UNSUPPORTED.search(l) and not UNSUPPORTED.search(r)]
    return out if conditions else [(l, r) for l, r, _ in out]


def definitions(latex: str) -> dict[str, str]:
    """The constants a display defines (``C = ...``, ``q = 1 - p``): {name: LaTeX}."""
    out = {}
    for lhs, rhs in equations(latex):
        name = _canon(lhs)
        if name in DEF_NAMES and name not in _canon(rhs).split():
            out.setdefault(name, rhs)
    return out


# ---------------------------------------------------------------- classification

def _canon(name: str) -> str:
    """x_{1} / x _ {1} / \\theta -> x_1 / theta (sympy's names, without braces)."""
    return name.replace("{", "").replace("}", "").replace(" ", "").replace("\\", "")


def classify(lhs: str, before: str = "") -> tuple[str | None, list[str], str]:
    """(kind, variables, subscript) of a left-hand side."""
    s = _clean(lhs).strip()
    if MEAN_LHS.match(s):
        return "mean", [], ""
    if VARIANCE_LHS.match(s):
        return "variance", [], ""
    m = PR_RE.match(s)
    if m:
        inner = m.group("inner")
        rels = REL_VAR.findall(inner)
        terms = _split_top(inner, [","])
        # Pr[X <= x] or Pr[X_1 > x_1, X_2 > x_2]: one relation per term, one direction;
        # an interval Pr[a < X < b] or an event with \cap is no distribution function
        if rels and len(rels) == len(terms) and not re.search(r"\\(cap|cup|mid)\b|\|", inner) \
                and len({r in (">", r"\ge", r"\geq") for r, _ in rels}) == 1 \
                and all(len(RELATION.findall(t)) == 1 for t in terms):
            kind = "survival" if rels[0][0] in (">", r"\ge", r"\geq") else "cdf"
            return kind, [_canon(v) for _, v in rels], ""
        return None, [], ""
    m = E_RE.match(s)
    if m:
        inner = m.group("inner").strip()
        if VECTOR.search(inner):
            return None, [], ""                      # E[e^{t'X}] with vectors: not scalar
        if re.match(r"^(?:e|\\exp)\s*(?:\^)?\s*[{(]?", inner) and ("^" in inner or r"\exp" in inner):
            found = []
            for t in T_VAR.finditer(inner):
                name = "t" + (f"_{t.group(1)}" if t.group(1) else "")
                if name not in found:
                    found.append(name)
            return "mgf", found, ""
        if re.match(r"^z\s*\^", inner):
            return "pgf", ["z"], ""
        if re.match(r"^X(?:_\{?[A-Za-z0-9]+\}?)?$", inner):
            return "mean", [], ""
        return None, [], ""
    m = LHS_FN.match(s)
    if not m:
        return None, [], ""
    name = m.group("name")
    sub = _canon(m.group("sub") or m.group("sub1") or "")
    args = m.group("args").split(";")[0]              # p(x; \lambda): parameters after ";"
    variables = [_canon(a) for a in args.split(",") if a.strip()]
    if any(not re.fullmatch(r"[A-Za-z]+(?:_[A-Za-z0-9]+)?", v) for v in variables):
        return None, [], ""                          # G(e^t), G(1 + t), G(k | m, n)
    if any(VECTOR.search(v) for v in variables) or VECTOR.search(sub):
        return None, [], ""                          # p_X(x) with bold vectors, (t_1, ..., t_k)
    kind = KINDS.get(name)
    if kind is None:
        text = before.lower()
        hits = [(text.rfind(word), k) for word, k in HINTS if word in text]
        if hits:
            kind = max(hits)[1]
    if kind is None:
        return None, [], ""
    if kind == "mgf":
        variables = variables or ["t"]
    return kind, variables, sub


# ---------------------------------------------------------------- SymPy

def _parse(s: str, defs: dict | None = None, depth: int = 0):
    """A sympy expression of cleaned LaTeX, the section's constants put in, Gamma, B, Phi
    and phi mapped; None when it will not parse or leaves an unknown function."""
    for rx in _INDICATOR:
        s = rx.sub(" 1 ", s)
    # erfc / erf and the normal quantile Phi^{-1}: commands the parser reads as function calls
    s = ERF_NAME.sub(lambda m: "\\" + m.group(1), s)
    s = PHI_INV.sub(r"\\Phiinv", s)
    # \theta (a + b)^{k}: parse_latex reads theta(...) as a call and raises the call to
    # the power; an explicit product keeps the power on the bracket
    s = GREEK_BRACKET.sub(lambda m: m.group(1) + r" \cdot (", s)
    try:
        expr = parse_latex(s)
    except Exception:  # noqa: BLE001 - any parse failure means "skip"
        return None
    if expr is None or isinstance(expr, (sympy.Eq, sympy.Rel)):
        return None
    subs = {}
    for sym in expr.free_symbols:
        if sym.name == "e":
            subs[sym] = sympy.E
        elif sym.name == "i":
            subs[sym] = sympy.I
        elif sym.name == "pi":
            subs[sym] = sympy.pi
    # xreplace, not subs: sympy 1.14's subs(e, E) can collapse an unevaluated parse_latex
    # product to 1 (the standard inverse Gaussian density, with an x^{-3/2} factor)
    expr = expr.xreplace(subs)

    def known(a):
        return isinstance(a, AppliedUndef) and (
            (a.func.__name__ == "Gamma" and len(a.args) == 1)
            or (a.func.__name__ == "B" and len(a.args) == 2)
            or (a.func.__name__ in ("Phi", "phi", "erf", "erfc", "Phiinv") and len(a.args) == 1)
            # N(p): the standard normal quantile (Gilchrist), only of a probability variable
            or (a.func.__name__ == "N" and len(a.args) == 1 and isinstance(a.args[0], sympy.Symbol)
                and a.args[0].name in ("p", "u")))

    def mapped(a):
        n = a.func.__name__
        if n == "Gamma":
            return sympy.gamma(a.args[0])
        if n == "B":
            return sympy.beta(*a.args)
        if n == "phi":                                   # the standard normal density
            return sympy.exp(-a.args[0] ** 2 / 2) / sympy.sqrt(2 * sympy.pi)
        if n == "erf":
            return sympy.erf(a.args[0])
        if n == "erfc":
            return sympy.erfc(a.args[0])
        if n in ("Phiinv", "N"):                         # sqrt(2) erfinv(2p - 1)
            return sympy.sqrt(2) * sympy.erfinv(2 * a.args[0] - 1)
        return (1 + sympy.erf(a.args[0] / sympy.sqrt(2))) / 2

    expr = expr.replace(known, mapped)

    def juxtaposed(a):
        """\\lambda(z - 1): a Greek parameter times a bracket, not a function call. A
        Latin letter before a bracket (g(x), f_1(x), q(t)) stays a function: unknown,
        so the display is skipped."""
        base = a.func.__name__.split("_")[0]
        return isinstance(a, AppliedUndef) and len(a.args) == 1 and (
            (base in GREEK_LOWER and base not in FUNCTION_GREEK) or base == "Xi")

    expr = expr.replace(juxtaposed, lambda a: sympy.Symbol(a.func.__name__) * a.args[0])
    if expr.atoms(AppliedUndef):
        return None
    if defs and depth < 2:
        put = {}
        for sym in expr.free_symbols:
            if _canon(sym.name) in defs:
                d = _parse(_clean(defs[_canon(sym.name)]), defs, depth + 1)
                if d is not None:
                    put[sym] = d
        expr = expr.xreplace(put)
    # the books' conventions, also where p (or P) is not in this expression: q = 1 - p, Q = 1 + P
    names = {_canon(x.name): x for x in expr.free_symbols}
    try:              # sympy refuses inside d/dq ("Can't calculate derivative wrt 1 - p")
        if "q" in names and not (defs and "q" in defs):
            expr = expr.xreplace({names["q"]: 1 - names.get("p", sympy.Symbol("p"))})
        if "Q" in names and not (defs and "Q" in defs):
            expr = expr.xreplace({names["Q"]: 1 + names.get("P", sympy.Symbol("P"))})
    except (ValueError, TypeError):
        return None
    return expr


_EXP_MAG = 4096


def _exp(x):
    """mpmath.exp, except past |x| = 2^4096, where exp is 0 or overflows at any working
    precision and mpmath would first compute ln 2 to log2|x| bits: e^{-e^{-y}} at a
    quadrature node far below 0 never returned (JKB p706), and the deadline is only
    read between evaluations."""
    if mpmath.mag(x) > _EXP_MAG:
        if not isinstance(x, mpmath.mpc) and x < 0:
            return mpmath.mpf(0)
        raise OverflowError("exp argument too large")
    return mpmath.exp(x)


def _lambdify(expr, variables: list[str]):
    """(fn(vars..., params...), params) or None."""
    if any(_canon(s.name) in UNKNOWN_CONSTANTS for s in expr.free_symbols):
        return None                                      # "c e^{...}": c is not known
    syms = sorted(expr.free_symbols, key=lambda x: x.name)
    var_syms = []
    for v in variables:
        found = [x for x in syms if _canon(x.name) == v]
        var_syms.append(found[0] if found else sympy.Symbol(v))
    params = [x for x in syms if _canon(x.name) not in variables]
    try:
        fn = sympy.lambdify(var_syms + params, expr, modules=[{"exp": _exp}, "mpmath"],
                            dummify=True)
    except Exception:  # noqa: BLE001
        return None
    return fn, [_canon(p.name) for p in params]


def _to_sympy(rhs: str, variables: list[str], defs: dict | None = None,
              need_variables: bool = False):
    """(fn(vars..., params...), params) or None when the expression will not parse (or,
    with need_variables, leaves a variable out: a pmf in x written in another index)."""
    expr = _parse(_clean(rhs), defs)
    if expr is None:
        return None
    if need_variables and any(v not in {_canon(s.name) for s in expr.free_symbols}
                              for v in variables):
        return None
    return _lambdify(expr, variables)


def _pmf_terms(s: str):
    """The pmf values a side names (Pr[X = x + 1], p_x, P(X = 0)): the side with each
    replaced by a symbol, and [(symbol, variable, shift, constant)]; None when a term's
    argument is neither the variable plus an integer nor (for Pr/P) a number."""
    terms = []

    def repl(m):
        arg = next(g for g in m.groups() if g is not None).strip()
        subscript = m.group("c") is not None or m.group("d") is not None
        try:
            a = parse_latex(arg)
        except Exception:  # noqa: BLE001
            if subscript:
                return m.group(0)
            raise ValueError(arg) from None
        free = sorted(a.free_symbols, key=lambda x: x.name)
        if not free:
            if subscript:                    # p_1, p_2: weights or parameters, not pmf values
                return m.group(0)
            terms.append((f"Xi_{{{len(terms)}}}", None, None, a))
        elif len(free) == 1 and free[0].name in PMF_VARS and (a - free[0]).is_integer:
            terms.append((f"Xi_{{{len(terms)}}}", free[0].name, int(a - free[0]), None))
        else:
            if subscript:
                return m.group(0)
            raise ValueError(arg)
        return rf" \Xi_{{{len(terms) - 1}}} "

    try:
        out = PMF_TERM.sub(repl, s)
    except ValueError:
        return None
    return out, terms


def _draw(name: str, k: int, discrete: bool = False) -> float:
    rng = random.Random(f"{SEED}:{k}:{name}")
    if discrete and name in INT_PARAMS:
        return INT_PARAMS[name][k % len(INT_PARAMS[name])]
    if discrete and name in PROB_PARAMS:
        return PROB_LO + (k + rng.random()) * (PROB_HI - PROB_LO) / DRAWS
    return LO + (k + rng.random()) * (HI - LO) / DRAWS


def _real(v):
    if isinstance(v, mpmath.mpc):
        if abs(v.imag) > 1e-9 * max(1.0, abs(v.real)):
            raise ValueError("complex")
        v = v.real
    v = mpmath.mpf(v)
    if not mpmath.isfinite(v):
        raise ValueError("not finite")
    return v


def _close(a, b, tol=TOL) -> bool:
    return abs(a - b) <= tol * max(1.0, abs(a), abs(b))


def _status(outcomes: list) -> str:
    known = [o for o in outcomes if o is not None]
    if not known:
        return "skipped"
    if all(known):
        return "passed"
    if not any(known):
        return "failed"
    return "inconclusive"


class _Checker:
    """Runs the checks of one expression at every draw; collects Check records."""

    def __init__(self, e: _Expr, ctx: Context, deadline: float):
        self.e, self.ctx, self.deadline = e, ctx, deadline
        self.checks: list[Check] = []
        self.supports: Counter = Counter()
        self.discrete = e.kind in DISCRETE

    def tick(self) -> None:
        """Checked before every draw and at every function evaluation (so inside the
        quadratures): past the deadline the display is abandoned."""
        if _clock() > self.deadline:
            raise _Timeout()

    def params(self, k: int, extra=(), discrete: bool | None = None) -> dict:
        """Positive draws; for a discrete distribution n, m, N are integers and p, theta
        lie in (0.1, 0.9)."""
        self.tick()
        names = sorted(set(self.e.params) | set(extra))
        d = self.discrete if discrete is None else discrete
        return {n: _draw(n, k, d) for n in names}

    # ---- probability mass functions
    def pmf_support(self, e: _Expr, p: dict):
        """(support, sum, non-negative) of the support whose sum is nearest 1: {0,1,...},
        {1,2,...} or {0..n} for an integer parameter n; None when nothing evaluates."""
        cands = [("{0,1,...}", 0, None), ("{1,2,...}", 1, None)]
        cands += [(f"{{0..{n}}}", 0, int(p[n])) for n in sorted(INT_PARAMS) if n in e.params]
        best = None
        for name, lo, hi in cands:
            try:
                f = lambda x: self.call(e, p, x)              # noqa: E731
                head = [f(x) for x in range(lo, lo + 4 if hi is None else min(hi, lo + 3) + 1)]
                if hi is None:
                    total = _real(mpmath.nsum(f, [lo, mpmath.inf]))
                else:
                    total = _real(mpmath.fsum(f(x) for x in range(lo, hi + 1)))
            except (ValueError, ZeroDivisionError, OverflowError, TypeError, ArithmeticError):
                continue
            cand = (abs(total - 1), name, total, all(v >= -TOL for v in head), lo, hi)
            if best is None or cand[0] < best[0]:
                best = cand
            if cand[0] <= TOL_INT:
                break
        return best

    def pmf_moments(self, e: _Expr, p: dict):
        best = self.pmf_support(e, p)
        if best is None or best[0] > TOL_INT:
            raise ValueError("no support sums to 1")
        _, _, _, _, lo, hi = best
        f1 = lambda x: x * self.call(e, p, x)                 # noqa: E731
        f2 = lambda x: x * x * self.call(e, p, x)             # noqa: E731
        if hi is None:
            m1, m2 = mpmath.nsum(f1, [lo, mpmath.inf]), mpmath.nsum(f2, [lo, mpmath.inf])
        else:
            m1 = mpmath.fsum(f1(x) for x in range(lo, hi + 1))
            m2 = mpmath.fsum(f2(x) for x in range(lo, hi + 1))
        m1, m2 = _real(m1), _real(m2)
        return m1, m2 - m1 ** 2

    def pmf(self):
        e = self.e
        if len(e.variables) != 1:
            return
        nonneg, sums, det_n, det_s = [], [], [], []
        for k in range(DRAWS):
            p = self.params(k)
            best = self.pmf_support(e, p)
            if best is None:
                nonneg.append(None)
                sums.append(None)
                det_n.append({"params": p})
                det_s.append({"params": p})
                continue
            dist, name, total, nn, _, _ = best
            self.supports[name] += 1
            nonneg.append(nn)
            sums.append(dist <= TOL_INT)
            det_n.append({"params": p, "support": name})
            det_s.append({"params": p, "support": name, "sum": float(total)})
        self.record("non-negative", nonneg, det_n)
        self.record("sums to 1", sums, det_s)

    def latest_pmf(self) -> _Expr | None:
        return self.ctx.find("pmf", nvars=1)

    def recurrence(self):
        """Both sides at x = 1, 2, 3 with the section's pmf put in for each pmf value."""
        e, pmf = self.e, self.latest_pmf()
        if pmf is None or not set(e.params) <= set(pmf.params):
            return                       # another parametrization (or no pmf) in the section
        fl, fr = e.fn
        outs, dets = [], []
        for k in range(DRAWS):
            p = self.params(k, pmf.params, discrete=True)
            res, ok = [], True
            try:
                points = REC_POINTS if e.variables else [None]
                for x in points:
                    vals = []
                    for _, var, shift, const in e.terms:
                        at = const if var is None else x + shift
                        if at < 0:
                            raise ValueError("outside the support")
                        vals.append(self.call(pmf, p, at))
                    xs = [x] if e.variables else []
                    a = self.call_side(fl, e, p, xs, vals)
                    b = self.call_side(fr, e, p, xs, vals)
                    res.append((a, b))
                    if abs(a - b) > 1e-6 * max(abs(a), abs(b), mpmath.mpf(10) ** -300):
                        ok = False
                outs.append(ok)
                dets.append({"params": p, "with": pmf.lhs,
                             "values": [[float(a), float(b)] for a, b in res]})
            except (ValueError, ZeroDivisionError, OverflowError, TypeError, ArithmeticError):
                outs.append(None)
                dets.append({"params": p, "with": pmf.lhs})
        self.record("recurrence" if e.kind == "recurrence" else "pmf at a point", outs, dets)

    def call_side(self, fn, e: _Expr, p: dict, xs: list, vals: list):
        self.tick()
        return _real(fn(*xs, *vals, *[p[q] for q in e.params]))

    # ---- printed mean and variance against a generating function or the pmf
    def moment_pair(self, src: _Expr, tgt: _Expr):
        label = {"mgf": ("M'(0) = mean", "M''(0) - M'(0)^2 = variance"),
                 "pgf": ("G'(1) = mean", "G''(1) + G'(1) - G'(1)^2 = variance"),
                 "pmf": ("sum x p(x) = mean", "sum x^2 p(x) - mean^2 = variance")}[src.kind]
        which = 0 if tgt.kind == "mean" else 1
        outs, dets = [], []
        for k in range(DRAWS):
            p = self.params(k, set(src.params) | set(tgt.params),
                            discrete=src.kind in DISCRETE or self.discrete)
            try:
                if src.kind == "pmf":
                    derived = self.pmf_moments(src, p)[which]
                else:
                    at = 0 if src.kind == "mgf" else 1
                    f = lambda t: src.fn(t, *[p[q] for q in src.params])     # noqa: E731
                    d1 = _real(mpmath.diff(f, at))
                    d2 = _real(mpmath.diff(f, at, 2)) if which else 0
                    derived = d1 if not which else (
                        d2 - d1 ** 2 if src.kind == "mgf" else d2 + d1 - d1 ** 2)
                printed = self.call(tgt, p)
                outs.append(_close(derived, printed, 1e-5))
                dets.append({"params": p, "with": src.lhs if tgt is self.e else tgt.lhs,
                             "derived": float(derived), "printed": float(printed)})
            except (ValueError, ZeroDivisionError, OverflowError, TypeError, ArithmeticError):
                outs.append(None)
                dets.append({"params": p})
        self.record(label[which], outs, dets)

    def moments(self):
        """Only a pair in one parametrization: every parameter of the printed mean or
        variance occurs in the source (a section often prints a distribution in two)."""
        e, ctx = self.e, self.ctx
        if e.kind in MOMENT_KINDS:
            for kind in ("pgf", "mgf", "pmf"):
                src = ctx.find(kind, nvars=1)
                if src and set(e.params) <= set(src.params):
                    self.moment_pair(src, e)
        elif e.kind in ("pgf", "mgf", "pmf") and len(e.variables) == 1:
            for kind in MOMENT_KINDS:
                tgt = ctx.find(kind)
                if tgt and set(tgt.params) <= set(e.params):
                    self.moment_pair(e, tgt)

    def call(self, e: _Expr, pvals: dict, *xs):
        self.tick()
        return _real(e.fn(*xs, *[pvals[p] for p in e.params]))

    def record(self, identity: str, outcomes: list, details: list[dict]) -> str:
        st = _status(outcomes)
        bad = [d for o, d in zip(outcomes, details) if o is False]
        self.checks.append(Check(identity, st, bad[0] if bad else (details[0] if details else {})))
        return st

    # ---- densities
    def density(self):
        e = self.e
        nonneg, integ, det_n, det_i = [], [], [], []
        for k in range(DRAWS):
            p = self.params(k)
            best = None
            for name, (pts, _, _) in SUPPORTS.items():
                try:
                    if len(e.variables) == 1:
                        vals = [self.call(e, p, x) for x in pts]
                        rng = QUAD_RANGES[name]
                        total = _real(mpmath.quad(lambda x: self.call(e, p, x),
                                                  [mpmath.mpf(r) if isinstance(r, str) else r
                                                   for r in rng], maxdegree=6))
                    else:
                        vals = [self.call(e, p, x, y) for x in pts for y in pts]
                        rng = [mpmath.mpf(r) if isinstance(r, str) else r for r in QUAD_RANGES[name]]
                        total = _real(mpmath.quad(lambda x, y: self.call(e, p, x, y), rng, rng,
                                                  maxdegree=3))
                except (ValueError, ZeroDivisionError, OverflowError, TypeError, ArithmeticError):
                    continue
                nn = all(v >= -TOL for v in vals)
                cand = (abs(total - 1), name, nn, total)
                if best is None or cand[0] < best[0]:
                    best = cand
                if cand[0] <= (TOL_INT if len(e.variables) == 1 else TOL_INT_2D):
                    break                                 # this support is the one
            if len(e.variables) == 1 and (best is None or best[0] > TOL_INT):
                disc = self.pmf_support(e, p)               # a pmf written f(x) or p(x)
                if disc is not None and disc[0] <= TOL_INT:
                    best = (disc[0], disc[1], disc[3], disc[2])
            if best is None:
                nonneg.append(None)
                integ.append(None)
                det_n.append({"params": p})
                det_i.append({"params": p})
                continue
            _, name, nn, total = best
            self.supports[name] += 1
            nonneg.append(nn)
            integ.append(abs(total - 1) <= (TOL_INT if len(e.variables) == 1 else TOL_INT_2D))
            det_n.append({"params": p, "support": name})
            det_i.append({"params": p, "support": name, "integral": float(total)})
        self.record("non-negative", nonneg, det_n)
        self.record("integrates to 1", integ, det_i)

    # ---- cdf and survival
    def cdf_like(self, survival: bool):
        e = self.e
        if len(e.variables) != 1:
            return
        within, mono, limits, dets = [], [], [], []
        for k in range(DRAWS):
            p = self.params(k)
            best = None
            for name, (pts, lo, hi) in SUPPORTS.items():
                xs = [lo, *pts, hi]
                try:
                    vals = [self.call(e, p, x) for x in xs]
                except (ValueError, ZeroDivisionError, OverflowError, TypeError, ArithmeticError):
                    continue
                w = all(-TOL <= v <= 1 + TOL for v in vals)
                seq = vals[::-1] if survival else vals
                m = all(b >= a - TOL for a, b in zip(seq, seq[1:]))
                lim = (abs(seq[0]) <= LIMIT_TOL and abs(seq[-1] - 1) <= LIMIT_TOL)
                cand = (w + m + lim, name, w, m, lim, [float(v) for v in vals])
                if best is None or cand[0] > best[0]:
                    best = cand
            if best is None:
                within.append(None)
                mono.append(None)
                limits.append(None)
                dets.append({"params": p})
                continue
            _, name, w, m, lim, vals = best
            self.supports[name] += 1
            within.append(w)
            mono.append(m)
            limits.append(lim)
            dets.append({"params": p, "support": name, "values": vals})
        self.record("within [0,1]", within, dets)
        self.record("monotone", mono, dets)
        self.record("limits 0 and 1", limits, dets)

    def hazard(self):
        e = self.e
        if len(e.variables) != 1:
            return
        outs, dets = [], []
        for k in range(DRAWS):
            p = self.params(k)
            try:
                vals = [self.call(e, p, x) for x in SUPPORTS["(0,inf)"][0]]
                outs.append(all(v >= -TOL for v in vals))
            except (ValueError, ZeroDivisionError, OverflowError, TypeError, ArithmeticError):
                outs.append(None)
            dets.append({"params": p})
        self.record("non-negative", outs, dets)

    # ---- generating functions
    def generating(self, kind: str):
        e = self.e
        n = len(e.variables)
        at = 1.0 if kind == "pgf" else 0.0
        label = {"mgf": "M", "cf": "phi", "pgf": "G"}[kind]
        ones, var, d_one, d_var = [], [], [], []
        for k in range(DRAWS):
            p = self.params(k)
            try:
                one = self.call(e, p, *([at] * n))
                ones.append(_close(one, 1))
                d_one.append({"params": p, "value": float(one)})
            except (ValueError, ZeroDivisionError, OverflowError, TypeError, ArithmeticError):
                ones.append(None)
                d_one.append({"params": p})
            try:
                if n == 1:
                    f = lambda t: e.fn(t, *[p[q] for q in e.params])     # noqa: E731
                    d1 = mpmath.diff(f, at)
                    d2 = mpmath.diff(f, at, 2)
                    if kind == "mgf":
                        v = _real(d2 - d1 ** 2)
                    elif kind == "cf":          # E[X] = -i phi'(0), E[X^2] = -phi''(0)
                        v = _real(-d2 + d1 ** 2)
                    else:
                        v = _real(d2 + d1 - d1 ** 2)
                    var.append(v >= -1e-6)
                    d_var.append({"params": p, "variance": float(v)})
                elif n == 2 and kind == "mgf":
                    f = lambda a, b: e.fn(a, b, *[p[q] for q in e.params])     # noqa: E731
                    g = [_real(mpmath.diff(f, (0, 0), (1, 0))), _real(mpmath.diff(f, (0, 0), (0, 1)))]
                    h = [[_real(mpmath.diff(f, (0, 0), (2, 0))), _real(mpmath.diff(f, (0, 0), (1, 1)))],
                         [None, _real(mpmath.diff(f, (0, 0), (0, 2)))]]
                    a = h[0][0] - g[0] ** 2
                    b = h[0][1] - g[0] * g[1]
                    c = h[1][1] - g[1] ** 2
                    psd = a >= -1e-6 and c >= -1e-6 and a * c - b * b >= -1e-6
                    var.append(psd)
                    d_var.append({"params": p, "matrix": [[float(a), float(b)], [float(b), float(c)]]})
                else:
                    var.append(None)
                    d_var.append({"params": p})
            except (ValueError, ZeroDivisionError, OverflowError, TypeError, ArithmeticError):
                var.append(None)
                d_var.append({"params": p})
        self.record(f"{label}({at:g}) = 1", ones, d_one)
        if n == 1:
            ident = {"mgf": "M''(0) - M'(0)^2 >= 0", "cf": "-phi''(0) + phi'(0)^2 >= 0",
                     "pgf": "G''(1) + G'(1) - G'(1)^2 >= 0"}[kind]
            self.record(ident, var, d_var)
        elif n == 2 and kind == "mgf":
            self.record("H(0) - g g^T is PSD", var, d_var)

    # ---- pairs
    def pair(self, identity: str, other: _Expr, fn):
        """fn(p) -> (left, right) values at the shared draws."""
        outs, dets = [], []
        for k in range(DRAWS):
            p = self.params(k, other.params)
            try:
                res = fn(p)
                outs.append(all(_close(a, b, 1e-5) for a, b in res))
                dets.append({"params": p, "with": other.lhs,
                             "values": [[float(a), float(b)] for a, b in res]})
            except (ValueError, ZeroDivisionError, OverflowError, TypeError, ArithmeticError):
                outs.append(None)
                dets.append({"params": p, "with": other.lhs})
        self.record(identity, outs, dets)

    def pairs(self):
        e, ctx = self.e, self.ctx
        one = len(e.variables) == 1
        if e.kind in ("density", "cdf") and one:
            other = ctx.find("cdf" if e.kind == "density" else "density", e.sub, 1)
            if other:
                F, f = (other, e) if e.kind == "density" else (e, other)
                self.pair("F' = f", other, lambda p: [
                    (_real(mpmath.diff(lambda x: F.fn(x, *[p[q] for q in F.params]), x)),
                     self.call(f, p, x)) for x in PAIR_POINTS])
        if e.kind in ("cdf", "survival") and one:
            other = ctx.find("survival" if e.kind == "cdf" else "cdf", e.sub, 1)
            if other:
                S, F = (other, e) if e.kind == "cdf" else (e, other)
                self.pair("S = 1 - F", other, lambda p: [
                    (self.call(S, p, x), 1 - self.call(F, p, x)) for x in PAIR_POINTS])
        if e.kind == "hazard" and one:
            f = ctx.find("density", e.sub, 1)
            S = ctx.find("survival", e.sub, 1)
            F = ctx.find("cdf", e.sub, 1) if S is None else None
            if f and (S or F):
                other = S or F
                surv = (lambda p, x: self.call(S, p, x)) if S else (
                    lambda p, x: 1 - self.call(F, p, x))
                self.pair("h = f / S", other, lambda p: [
                    (self.call(e, p, x), self.call(f, p, x) / surv(p, x)) for x in PAIR_POINTS])
        if e.kind == "survival" and len(e.variables) == 2:
            marg = ctx.find("survival", sub=e.sub.split(",")[0].strip(), nvars=1)
            if marg:
                self.pair("joint survival at x_2 = 0 = marginal", marg, lambda p: [
                    (self.call(e, p, x, 0.0), self.call(marg, p, x)) for x in PAIR_POINTS])
        self.moments()

    def run(self):
        k = self.e.kind
        if k == "density":
            self.density()
        elif k == "pmf":
            self.pmf()
        elif k in ("recurrence", "pmf_point"):
            self.recurrence()
            return
        elif k == "cdf":
            self.cdf_like(False)
        elif k == "survival":
            self.cdf_like(True)
        elif k == "hazard":
            self.hazard()
        elif k in ("mgf", "cf", "pgf"):
            self.generating(k)
        self.pairs()


def _slow_start() -> None:          # a hook for the timeout test
    pass


def _overall(checks: list[Check]) -> str:
    sts = {c.status for c in checks}
    if "failed" in sts:
        return "failed"
    if "inconclusive" in sts:
        return "inconclusive"
    if "passed" in sts:
        return "passed"
    return "skipped"


def _expression(lhs: str, rhs: str, before: str, ctx: Context):
    """(kind, _Expr or None) of one equation: a pmf (Pr[X = x] = ..., p_x = ...), a pmf
    value at a point, a recurrence (pmf values on either side), or by its left-hand side."""
    cl, cr = _clean(lhs), _clean(rhs)
    tl, tr = _pmf_terms(cl), _pmf_terms(cr)
    if tl is None or tr is None:
        return None, None
    (sl, terms_l), (sr, terms_r) = tl, tr
    if len(terms_l) == 1 and sl.strip() == r"\Xi_{0}" and not terms_r:
        sym, var, shift, const = terms_l[0]
        if var is not None and shift == 0:                 # Pr[X = x] = ...
            parsed = _to_sympy(rhs, [var], ctx.defs, need_variables=True)
            return "pmf", (_Expr("pmf", "", [var], parsed[0], parsed[1], lhs)
                           if parsed else None)
        if var is None:                                    # Pr[X = 0] = ...
            terms_l = [(sym, None, None, const)]
    if terms_l or terms_r:
        kind = "pmf_point" if all(t[1] is None for t in terms_l + terms_r) else "recurrence"
        terms = terms_l + [(rf"Xi_{{{len(terms_l) + i}}}", v, s, c)
                           for i, (_, v, s, c) in enumerate(terms_r)]
        # the right side's symbols are renumbered after the left side's
        for i in reversed(range(len(terms_r))):
            sr = sr.replace(rf"\Xi_{{{i}}}", rf"\Xi_{{{len(terms_l) + i}}}")
        variables = sorted({t[1] for t in terms if t[1] is not None})
        if len(variables) > 1:
            return None, None
        el, er = _parse(sl, ctx.defs), _parse(sr, ctx.defs)
        if el is None or er is None:
            return kind, None
        names = variables + [_canon(t[0]) for t in terms]
        both = sympy.Tuple(el, er)
        lam = _lambdify(both, names)
        if lam is None:
            return kind, None
        fn, params = lam
        fl = lambda *a, _f=fn: _f(*a)[0]                  # noqa: E731
        fr = lambda *a, _f=fn: _f(*a)[1]                  # noqa: E731
        return kind, _Expr(kind, "", variables, (fl, fr), params, lhs, terms)
    kind, variables, sub = classify(lhs, before)
    if kind is None:
        return None, None
    parsed = _to_sympy(rhs, variables, ctx.defs)
    return kind, (_Expr(kind, sub, variables, parsed[0], parsed[1], lhs) if parsed else None)


def check_display(latex: str, before: str = "", ctx: Context | None = None) -> Result:
    """Classify and check one display; never raises for a formula that will not do.
    Past ITEM_TIMEOUT seconds (parsing included) the display is skipped as a timeout."""
    if not AVAILABLE:                       # pragma: no cover - depends on extras
        return Result(None, "skipped", reason="mathcheck extra not installed")
    ctx = ctx or Context()
    try:
        with watch.hard_timeout(ITEM_TIMEOUT):
            return _check_display(latex, before, ctx)
    except watch.ItemTimeout:
        return Result(None, "skipped", reason="timeout")


def _check_display(latex: str, before: str, ctx: Context) -> Result:
    eqs = equations(latex)
    if not eqs:
        return Result(None, "skipped", reason="no identity")
    kind, exprs, first = None, [], True
    for lhs, rhs in eqs:
        k, e = _expression(lhs, rhs, before if first else "", ctx)
        first = False
        if k is None:
            continue
        kind = kind or k
        if e is not None:
            exprs.append(e)
    if kind is None:
        return Result(None, "skipped", reason="unclassified")
    if not exprs:
        return Result(kind, "skipped", reason="does not parse")
    mpmath.mp.dps = 15
    deadline = _clock() + TIME_LIMIT
    checks, supports = [], Counter()
    try:
        _slow_start()
        for e in exprs:
            c = _Checker(e, ctx, deadline)
            c.run()
            checks += c.checks
            supports += c.supports
    except _Timeout:
        return Result(kind, "skipped", reason="timeout")
    for e in exprs:
        ctx.exprs.append(e)
    status = _overall(checks) if checks else "skipped"
    support = supports.most_common(1)[0][0] if supports else None
    return Result(kind, status, checks, support, None if checks else "nothing to check",
                  NOTE if status == "failed" else None)


def counts(results: list[Result]) -> dict:
    c = Counter(r.status for r in results)
    return {"checked": c["passed"] + c["failed"] + c["inconclusive"], "passed": c["passed"],
            "failed": c["failed"], "inconclusive": c["inconclusive"], "skipped": c["skipped"]}
