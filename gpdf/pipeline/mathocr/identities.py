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

try:
    import mpmath
    import sympy
    from sympy.core.function import AppliedUndef
    from sympy.parsing.latex import parse_latex
    import antlr4  # noqa: F401  (parse_latex needs it at call time)
    AVAILABLE = True
except ImportError:                                   # pragma: no cover - depends on extras
    AVAILABLE = False

TIME_LIMIT = 5.0           # seconds per display
DRAWS = 4                  # parameter draws, stratified over (LO, HI)
LO, HI = 0.3, 2.5
SEED = "g-pdf mathcheck"
TOL = 1e-6                 # relative, for identities
TOL_INT = 1e-3             # for integrals
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
UNSUPPORTED = re.compile(r"\\begin\{(cases|[pbvB]?matrix|smallmatrix)\}|\\text\b|\\mathrm\{(?!e\}|d\})")


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


class Context:
    """The classified expressions of the current section, for the pair checks."""

    def __init__(self):
        self.exprs: list[_Expr] = []
        self.section = ""

    def new_section(self, heading: str) -> None:
        self.section = heading
        self.exprs = []

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
]
_INDICATOR = [
    re.compile(r"\\(mathbf|mathbb|boldsymbol|pmb|bm)\{1\}(?:_\{[^{}]*\}|\([^()]*\))?"),
    re.compile(r"(?<![A-Za-z\\])I\s*[\[(][^\[\]()]*(?:<|>|\\le|\\ge|\\leq|\\geq)[^\[\]()]*[\])]"),
    re.compile(r"(?<![A-Za-z\\])I_\{[^{}]*\}\s*\([^()]*\)"),
]
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


def _strip_conditions(rhs: str) -> str:
    """The expression before a top-level comma, semicolon or \\quad (a side condition)."""
    first = _split_top(rhs, [",", ";", r"\qquad", r"\quad"])[0]
    return first.strip().rstrip(".,;").strip()


def equations(latex: str) -> list[tuple[str, str]]:
    """(lhs, rhs) pairs of a display: aligned rows and = chains split, conditions
    dropped; empty for a display that is no identity."""
    body, _ = L.split_tag(latex)
    if NOT_IDENTITY.search(body) or UNSUPPORTED.search(body):
        return []
    body = ALIGN_ENV.sub(" ", body)
    rows = [r.replace("&", " ") for r in re.split(r"\\\\(?:\[[^\]]*\])?", body)]
    eqs, lhs = [], None
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
            for r in parts[1:]:
                r = _strip_conditions(r)
                if r:
                    eqs.append((lhs, r))
        elif eqs and not RELATION.search(row):
            l, r = eqs[-1]
            eqs[-1] = (l, r + " " + _strip_conditions(row))
    return eqs


# ---------------------------------------------------------------- classification

def _canon(name: str) -> str:
    """x_{1} / x _ {1} / \\theta -> x_1 / theta (sympy's names, without braces)."""
    return name.replace("{", "").replace("}", "").replace(" ", "").replace("\\", "")


def classify(lhs: str, before: str = "") -> tuple[str | None, list[str], str]:
    """(kind, variables, subscript) of a left-hand side."""
    s = _clean(lhs).strip()
    m = PR_RE.match(s)
    if m:
        rels = REL_VAR.findall(m.group("inner"))
        if rels:
            kind = "survival" if rels[0][0] in (">", r"\ge", r"\geq") else "cdf"
            return kind, [_canon(v) for _, v in rels], ""
    m = E_RE.match(s)
    if m:
        inner = m.group("inner").strip()
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
    variables = [_canon(a) for a in m.group("args").split(",") if a.strip()]
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

def _to_sympy(rhs: str, variables: list[str]):
    """(fn(vars..., params...), params) or None when the expression will not parse."""
    s = _clean(rhs)
    for rx in _INDICATOR:
        s = rx.sub(" 1 ", s)
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
    expr = expr.subs(subs)

    def known(a):
        return isinstance(a, AppliedUndef) and (
            (a.func.__name__ == "Gamma" and len(a.args) == 1)
            or (a.func.__name__ == "B" and len(a.args) == 2)
            or (a.func.__name__ == "Phi" and len(a.args) == 1))

    def mapped(a):
        n = a.func.__name__
        if n == "Gamma":
            return sympy.gamma(a.args[0])
        if n == "B":
            return sympy.beta(*a.args)
        return (1 + sympy.erf(a.args[0] / sympy.sqrt(2))) / 2

    expr = expr.replace(known, mapped)

    def juxtaposed(a):
        """\\lambda(z - 1): a parameter times a bracket, not a function call."""
        base = a.func.__name__.split("_")[0]
        return isinstance(a, AppliedUndef) and len(a.args) == 1 and (
            base in GREEK_LOWER or (len(base) == 1 and base.islower()))

    expr = expr.replace(juxtaposed, lambda a: sympy.Symbol(a.func.__name__) * a.args[0])
    if expr.atoms(AppliedUndef):
        return None
    syms = sorted(expr.free_symbols, key=lambda x: x.name)
    var_syms = []
    for v in variables:
        found = [x for x in syms if _canon(x.name) == v]
        var_syms.append(found[0] if found else sympy.Symbol(v))
    params = [x for x in syms if _canon(x.name) not in variables]
    try:
        fn = sympy.lambdify(var_syms + params, expr, modules="mpmath", dummify=True)
    except Exception:  # noqa: BLE001
        return None
    return fn, [_canon(p.name) for p in params]


def _draw(name: str, k: int) -> float:
    rng = random.Random(f"{SEED}:{k}:{name}")
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

    def tick(self) -> None:
        """Checked before every draw and at every function evaluation (so inside the
        quadratures): past the deadline the display is abandoned."""
        if time.monotonic() > self.deadline:
            raise _Timeout()

    def params(self, k: int, extra=()) -> dict:
        self.tick()
        names = sorted(set(self.e.params) | set(extra))
        return {n: _draw(n, k) for n in names}

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
            if best is None:
                nonneg.append(None)
                integ.append(None)
                det_n.append({"params": p})
                det_i.append({"params": p})
                continue
            _, name, nn, total = best
            self.supports[name] += 1
            nonneg.append(nn)
            integ.append(abs(total - 1) <= TOL_INT)
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
        if e.kind in ("mgf", "mean"):
            other = ctx.find("mean" if e.kind == "mgf" else "mgf", e.sub,
                             0 if e.kind == "mgf" else 1)
            if other and (e.kind == "mean" or one):
                M, mean = (other, e) if e.kind == "mean" else (e, other)
                self.pair("M'(0) = mean", other, lambda p: [
                    (_real(mpmath.diff(lambda t: M.fn(t, *[p[q] for q in M.params]), 0)),
                     self.call(mean, p))])

    def run(self):
        k = self.e.kind
        if k == "density":
            self.density()
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


def check_display(latex: str, before: str = "", ctx: Context | None = None) -> Result:
    """Classify and check one display; never raises for a formula that will not do."""
    if not AVAILABLE:                       # pragma: no cover - depends on extras
        return Result(None, "skipped", reason="mathcheck extra not installed")
    ctx = ctx or Context()
    eqs = equations(latex)
    if not eqs:
        return Result(None, "skipped", reason="no identity")
    kind, variables, sub = classify(eqs[0][0], before)
    if kind is None:
        return Result(None, "skipped", reason="unclassified")
    exprs = []
    for lhs, rhs in eqs:
        parsed = _to_sympy(rhs, variables)
        if parsed:
            exprs.append(_Expr(kind, sub, variables, parsed[0], parsed[1], lhs))
    if not exprs:
        return Result(kind, "skipped", reason="does not parse")
    mpmath.mp.dps = 15
    deadline = time.monotonic() + TIME_LIMIT
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
