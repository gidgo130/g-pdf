# SPDX-License-Identifier: AGPL-3.0-or-later
r"""Matrix expressions for gpdf-cli math-crosscheck (multivariate step 2).

A table row with ``"matrix": true`` (or a ``matrices`` / ``vectors`` list) is matrix variate.
Its points give matrices and vectors as nested lists (``"Sigma": [[1, 0.3], [0.3, 2]]``,
``"mu": [0.5, -1]``: a flat list is a column vector). In its formulas a bold symbol
(``\mathbf{X}``, ``\boldsymbol{\Sigma}``, ``\bm{x}``) or a name in ``matrices`` / ``vectors``
(``\Sigma``, ``X``; a subscript is part of the name: ``\Sigma_{11}`` is ``Sigma_11``) is a
matrix or vector, and ``I`` / ``I_p`` is the identity unless the point gives an ``I``.
Before the scalar parser:

- ``|A|``, ``\det A``, ``\operatorname{tr}(A)``, ``\operatorname{etr}(A)`` (exp of the trace) and
  ``\|v\|`` whose argument holds a matrix or vector are evaluated (mpmath) and put in as
  numbers; bars around a scalar stay an absolute value;
- every other maximal product holding a matrix or vector (a quadratic form ``(x - \mu)'
  \Sigma^{-1} (x - \mu)``) must come out 1 x 1 and is put in as a number;
- ``\Gamma_p(a)`` is ``\pi^{p(p-1)/4} \prod_{j=1}^{p} \Gamma(a - (j - 1)/2)`` and ``B_p(a, b)``
  is Gamma_p(a) Gamma_p(b) / Gamma_p(a + b) (``gamma_p``; the product is written out later).

The grammar: sums, products, scalar factors (each goes to the scalar parser), ``\frac`` with a
scalar denominator, ``\otimes``, transposes (``'``, ``^T``, ``^\top``), the inverse ``^{-1}``,
the symmetric square roots ``^{1/2}`` and ``^{-1/2}``, integer powers, and exp, log and sqrt of
a 1 x 1 result. Anything else raises MatrixError, and the entry is inconclusive ("matrix
expression not read"), never a guess. ``reduce`` gives the text for the scalar parser, or the
matrix itself when the whole piece is a matrix (a mean ``n \Sigma``, compared element by
element).
"""

from __future__ import annotations

import re

from gpdf.pipeline.mathocr import crosscheck as C
from gpdf.pipeline.mathocr import identities as I

OPEN, CLOSE = "\u27e6", "\u27e7"          # a marked matrix atom: \u27e6Sigma_11\u27e7
BOLD = re.compile(r"\\(?:mathbf|boldsymbol|bm|pmb)\s*(?:\{\s*(\\?[A-Za-z]+)\s*\}|(\\[A-Za-z]+|[A-Za-z]))")
SUBSCRIPT = re.compile(r"\s*_\s*(\{[^{}]*\}|[A-Za-z0-9])")
CMD_ARGS = {"frac": 2, "dfrac": 2, "tfrac": 2, "binom": 2, "sqrt": 1, "operatorname": 1, "mathrm": 1,
            "text": 1, "textrm": 1, "mbox": 1, "mathcal": 1, "mathbb": 1, "mathit": 1, "hat": 1,
            "tilde": 1, "bar": 1, "overline": 1, "widehat": 1, "widetilde": 1}
STOPS = re.compile(r"\\(?:leqslant|geqslant|leq|geq|neq|le|ge|ne|lt|gt|approx|sim)(?![A-Za-z])")
FUNCS = {"\\exp": "exp", "\\log": "log", "\\ln": "log"}
NAME = r"(?:\\operatorname\s*\{\s*%s\s*\}|\\mathrm\s*\{\s*%s\s*\}|\\text\s*\{\s*%s\s*\}|(?<![A-Za-z\\])%s(?![A-Za-z]))"
TR = re.compile(NAME % (("tr",) * 4))
ETR = re.compile(NAME % (("etr",) * 4))
DET = re.compile(r"\\det(?![A-Za-z])|" + NAME % (("det",) * 4))


class MatrixError(ValueError):
    """The matrix expression cannot be read; the message says why."""


def is_matrix(dist: dict) -> bool:
    return bool(dist.get("matrix") or dist.get("matrices") or dist.get("vectors"))


# ---------------------------------------------------------------- Gamma_p, B_p

def _arg_group(s: str, i: int) -> int | None:
    """s[i] opens a bracket: the index after its match."""
    pairs = {"(": ")", "[": "]", "{": "}"}
    if i >= len(s) or s[i] not in pairs:
        return None
    depth = 0
    for j in range(i, len(s)):
        if s[j] in "([{":
            depth += 1
        elif s[j] in ")]}":
            depth -= 1
            if depth == 0:
                return j + 1
    return None


def gamma_p(s: str) -> str:
    r"""\Gamma_p(a) and B_p(a, b) written as products over j = 1..p (p: the subscript)."""
    s = I._clean(s)
    for _ in range(50):
        m = re.search(r"(\\Gamma|(?<![A-Za-z\\])B|\\mathcal\{B\}|\\beta)_(\{[^{}]*\}|[A-Za-z0-9])\s*(?=[(\[{])", s)
        if not m:
            return s
        e = _arg_group(s, m.end())
        if e is None:
            return s
        dim, arg = m.group(2).strip("{}"), s[m.end() + 1:e - 1]
        idx = next(c for c in "jluvw" if not re.search(rf"(?<![A-Za-z\\]){c}(?![A-Za-z])", arg + dim))

        def gp(a):
            return (rf"(\pi^{{({dim})(({dim}) - 1)/4}} \prod_{{{idx}=1}}^{{{dim}}} "
                    rf"\Gamma(({a}) - ({idx} - 1)/2))")
        if m.group(1) == "\\Gamma":
            rep = gp(arg)
        else:
            parts = I._split_top(arg, [","])
            if len(parts) != 2:
                return s
            a, b = parts[0].strip(), parts[1].strip()
            rep = rf"\frac{{{gp(a)} {gp(b)}}}{{{gp(a + ' + ' + b)}}}"
        s = s[:m.start()] + rep + s[e:]
    return s


# ---------------------------------------------------------------- marking atoms

def _sub(text: str | None) -> str:
    return I._canon(text.strip("{}")) if text else ""


def mark(s: str, dist: dict, values: dict) -> str:
    """Matrix and vector atoms written as OPEN name CLOSE (subscript in the name)."""
    def atom(name, sub):
        return OPEN + name + (f"_{sub}" if sub else "") + CLOSE

    def bold(m):
        name = (m.group(1) or m.group(2)).lstrip("\\")
        rest = s_[m.end():]
        sm = SUBSCRIPT.match(rest)
        bold.skip = sm.end() if sm else 0
        return atom(name, _sub(sm.group(1)) if sm else "")
    out, i = [], 0
    s_ = s
    for m in BOLD.finditer(s_):
        if m.start() < i:
            continue
        out.append(s_[i:m.start()])
        out.append(bold(m))
        i = m.end() + bold.skip
    s = "".join(out) + s_[i:]
    names = list(dist.get("matrices", [])) + list(dist.get("vectors", []))
    if "I" not in values and dist.get("identity", True):
        names.append("I")
    for name in sorted(set(names), key=len, reverse=True):
        rx = re.compile(r"(?<![A-Za-z\\" + OPEN + r"])" + ("\\\\" if len(name) > 1 else "") + re.escape(name)
                        + r"(?![A-Za-z" + CLOSE + r"])" + r"(\s*_\s*(\{[^{}]*\}|[A-Za-z0-9]))?")
        def plain(m, name=name):
            sub = _sub(m.group(2))
            # x_1 beside a vector x is its component (a scalar) unless the point gives x_1 a matrix
            if sub and name != "I" and not isinstance(values.get(f"{name}_{sub}"), (list, tuple)):
                return m.group(0)
            return atom(name, sub)
        s = rx.sub(plain, s)
    return s


# ---------------------------------------------------------------- tokens

def _match(s: str, i: int) -> int:
    """s[i:] starts a group ( [ { or \\{: the index after its match."""
    depth, j = 0, i
    while j < len(s):
        if s.startswith("\\{", j) or s.startswith("\\}", j):
            depth += 1 if s[j + 1] == "{" else -1
            j += 2
        elif s[j] in "([{":
            depth += 1
            j += 1
        elif s[j] in ")]}":
            depth -= 1
            j += 1
        else:
            j += 1
        if depth == 0:
            return j
    raise MatrixError("unbalanced brackets")


def tokens(s: str) -> list[dict]:
    """Top-level tokens: {kind, raw, base, post}; kinds atom, group, cmd, num, sym, sign, mul,
    kron, stop, other. ``post`` holds the postfixes (', ^x, _x, !) in order."""
    out, i, n = [], 0, len(s)
    while i < n:
        c = s[i]
        if c.isspace():
            i += 1
            continue
        start = i
        if c == OPEN:
            j = s.index(CLOSE, i) + 1
            tok = {"kind": "atom", "base": s[i + 1:j - 1]}
        elif s.startswith("\\{", i) or c in "([{":
            j = _match(s, i)
            k = 2 if c == "\\" else 1
            tok = {"kind": "group", "base": s[i + k:j - k], "open": s[i:i + k]}
        elif c == "\\" and i + 1 < n and s[i + 1].isalpha():
            m = re.match(r"\\[A-Za-z]+", s[i:])
            name, j = m.group(0), i + len(m.group(0))
            args = []
            if name in ("\\cdot", "\\times"):
                tok = {"kind": "mul"}
            elif name == "\\otimes":
                tok = {"kind": "kron"}
            elif STOPS.fullmatch(name):
                tok = {"kind": "stop"}
            else:
                if name == "\\sqrt" and j < n and s[j] == "[":
                    k = _match(s, j)
                    args.append(("opt", s[j + 1:k - 1]))
                    j = k
                for _ in range(CMD_ARGS.get(name[1:], 0)):
                    while j < n and s[j] == " ":
                        j += 1
                    if j < n and s[j] == "{":
                        k = _match(s, j)
                        args.append(("arg", s[j + 1:k - 1]))
                        j = k
                tok = {"kind": "cmd", "name": name, "args": args}
        elif c.isdigit() or (c == "." and i + 1 < n and s[i + 1].isdigit()):
            j = re.match(r"\d*\.?\d+|\d+", s[i:]).end() + i
            tok = {"kind": "num"}
        elif c.isalpha():
            j, tok = i + 1, {"kind": "sym"}
        elif c in "+-":
            j, tok = i + 1, {"kind": "sign"}
        elif c in "=,;<>&":
            j, tok = i + 1, {"kind": "stop"}
        elif c in "'^_!":
            if not out:
                raise MatrixError(f"a postfix {c} with nothing before it")
            if c in "'!":
                out[-1]["post"].append(c)
                j = i + 1
            else:
                k = i + 1
                while k < n and s[k] == " ":
                    k += 1
                if k < n and s[k] == "{":
                    j = _match(s, k)
                    arg = s[k + 1:j - 1]
                elif k < n and s[k] == "\\":
                    m = re.match(r"\\[A-Za-z]+|\\.", s[k:])
                    j, arg = k + len(m.group(0)), m.group(0)
                else:
                    j, arg = k + 1, s[k:k + 1]
                out[-1]["post"].append(c + arg)
            out[-1]["raw"] += s[i:j]
            i = j
            continue
        else:
            j, tok = i + 1, {"kind": "other"}
        tok.setdefault("base", s[start:j])
        tok.update(raw=s[start:j], post=[])
        out.append(tok)
        i = j
    return out


def has_atom(s: str) -> bool:
    return OPEN in s


# ---------------------------------------------------------------- values

class Eye:
    """c times an identity whose size the context gives."""

    def __init__(self, c=1):
        self.c = c


def _mp():
    import mpmath
    mpmath.mp.dps = 30
    return mpmath


def _to_value(v):
    mp = _mp()
    if isinstance(v, (list, tuple)):
        m = mp.matrix(v)
        return m[0, 0] if (m.rows, m.cols) == (1, 1) else m
    return mp.mpmathify(v)


def _is_mat(v) -> bool:
    return hasattr(v, "rows")


def _scalarish(v):
    """A 1 x 1 matrix as its number."""
    if _is_mat(v) and v.rows == 1 and v.cols == 1:
        return v[0, 0]
    return v


def _add(a, b):
    mp = _mp()
    if isinstance(a, Eye) and isinstance(b, Eye):
        return Eye(a.c + b.c)
    if isinstance(a, Eye):
        a, b = b, a
    if isinstance(b, Eye):
        if not _is_mat(a) or a.rows != a.cols:
            raise MatrixError("an identity added to a non-square term")
        return a + b.c * mp.eye(a.rows)
    if _is_mat(a) != _is_mat(b):
        raise MatrixError("a matrix added to a number")
    if _is_mat(a) and (a.rows, a.cols) != (b.rows, b.cols):
        raise MatrixError(f"sizes {a.rows}x{a.cols} and {b.rows}x{b.cols} do not add")
    return _scalarish(a + b)


def _mul(a, b, kron=False):
    mp = _mp()
    if kron:
        if not (_is_mat(a) and _is_mat(b)):
            raise MatrixError("a Kronecker product needs two matrices")
        out = mp.matrix(a.rows * b.rows, a.cols * b.cols)
        for i in range(a.rows):
            for j in range(a.cols):
                for k in range(b.rows):
                    for m in range(b.cols):
                        out[i * b.rows + k, j * b.cols + m] = a[i, j] * b[k, m]
        return out
    if isinstance(a, Eye) and isinstance(b, Eye):
        return Eye(a.c * b.c)
    if isinstance(a, Eye):
        return _scalarish(a.c * b) if _is_mat(b) else Eye(a.c * b)
    if isinstance(b, Eye):
        return _scalarish(b.c * a) if _is_mat(a) else Eye(b.c * a)
    if _is_mat(a) and _is_mat(b):
        if a.cols != b.rows:
            raise MatrixError(f"sizes {a.rows}x{a.cols} and {b.rows}x{b.cols} do not multiply")
        return _scalarish(a * b)
    return _scalarish(a * b)


def _concrete(v, what):
    if isinstance(v, Eye):
        raise MatrixError(f"the {what} of an identity of unknown size")
    return v


def trace(v):
    v = _concrete(v, "trace")
    if not _is_mat(v):
        return v
    if v.rows != v.cols:
        raise MatrixError("the trace of a non-square matrix")
    return sum(v[i, i] for i in range(v.rows))


def det(v):
    v = _concrete(v, "determinant")
    if not _is_mat(v):
        return v
    if v.rows != v.cols:
        raise MatrixError("the determinant of a non-square matrix")
    return _mp().det(v)


# ---------------------------------------------------------------- evaluation

class Evaluator:
    def __init__(self, values: dict, defs: dict | None = None):
        self.values = values
        self.defs = defs or {}
        self.scalars = {k: v for k, v in values.items() if not isinstance(v, (list, tuple))}

    # a scalar piece of LaTeX through the scalar parser
    def scalar(self, text: str):
        if has_atom(text):
            raise MatrixError("a matrix inside a scalar function")
        expr = C.book_expr(text, (), self.defs)
        if expr is None:
            raise MatrixError(f"scalar part does not parse: {text.strip()[:40]}")
        v, why = C.evaluate(expr, self.scalars)
        if v is None:
            raise MatrixError(why)
        return _mp().mpc(v) if v.imag else _mp().mpf(v.real)

    def atom(self, name: str):
        if name in self.values:
            return _to_value(self.values[name])
        if name == "I" or name.startswith("I_"):
            size = name[2:]
            if size:
                n = self.scalar(size)
                if abs(n.imag if hasattr(n, "imag") else 0) > 0 or int(n.real) != n.real or n.real < 1:
                    raise MatrixError(f"identity size {size} is not a positive integer")
                return _mp().eye(int(n.real))
            return Eye()
        raise MatrixError(f"{name} is not given")

    def power(self, v, ex: str):
        e = re.sub(r"[\s{}]", "", ex)
        if e in ("T", "\\top", "'", "\\prime", "\\mathrm{T}", "\\intercal"):
            if isinstance(v, Eye):
                return v
            return v.T if _is_mat(v) else v
        if _is_mat(v) or isinstance(v, Eye):
            v = _concrete(v, "power")
            mp = _mp()
            if v.rows != v.cols:
                raise MatrixError("a power of a non-square matrix")
            if e in ("1/2", "\\frac12", "\\frac{1}{2}", "\\tfrac12", "\\tfrac{1}{2}"):
                return mp.sqrtm(v)
            if e in ("-1/2", "-\\frac12", "-\\frac{1}{2}", "-\\tfrac12", "-\\tfrac{1}{2}"):
                return mp.sqrtm(v) ** -1
            if re.fullmatch(r"-?\d+", e):
                return _scalarish(v ** int(e))
            raise MatrixError(f"matrix power {ex} not read")
        p = self.sum(ex) if has_atom(ex) else self.scalar(ex)
        if _is_mat(p):
            raise MatrixError("a matrix exponent")
        return _mp().power(v, p)

    def token(self, t: dict):
        k = t["kind"]
        if k == "atom":
            v = self.atom(t["base"])
        elif k == "group":
            v = self.sum(t["base"])
        elif k == "cmd" and t["name"] in ("\\frac", "\\dfrac", "\\tfrac") and len(t["args"]) == 2:
            num, den = self.sum(t["args"][0][1]), self.sum(t["args"][1][1])
            if _is_mat(den) or isinstance(den, Eye):
                raise MatrixError("a matrix in a denominator")
            v = _mul(num, 1 / den)
        elif k == "cmd" and t["name"] == "\\sqrt" and t["args"]:
            inner = self.sum(t["args"][-1][1])
            if _is_mat(inner):
                raise MatrixError("a square root sign over a matrix")
            root = self.scalar(t["args"][0][1]) if t["args"][0][0] == "opt" else 2
            v = _mp().root(inner, int(root)) if root != 2 else _mp().sqrt(inner)
        elif not has_atom(t["raw"]):
            return self.scalar(t["raw"])
        else:
            raise MatrixError(f"{t['raw'][:30]} not read")
        for p in t["post"]:
            if p == "'":
                v = self.power(v, "T")
            elif p.startswith("^"):
                v = self.power(v, p[1:])
            else:
                raise MatrixError(f"postfix {p} on a matrix term")
        return v

    def product(self, toks: list[dict]):
        acc, buf, kron = None, [], False

        def take(v):
            nonlocal acc, kron
            acc = v if acc is None else _mul(acc, v, kron)
            kron = False

        def flush():
            if buf:
                take(self.scalar(" ".join(t["raw"] for t in buf)))
                buf.clear()
        for t in toks:
            if t["kind"] in ("mul",):
                continue
            if t["kind"] == "kron":
                flush()
                kron = True
                continue
            if t["kind"] in ("stop", "sign"):
                raise MatrixError("a relation or sign inside a product")
            if not has_atom(t["raw"]):
                buf.append(t)
                continue
            fn = None
            if buf and buf[-1]["kind"] == "cmd" and buf[-1]["name"] in FUNCS and not buf[-1]["post"]:
                fn = FUNCS[buf.pop()["name"]]
            flush()
            v = self.token(t)
            if fn:
                if _is_mat(v) or isinstance(v, Eye):
                    raise MatrixError(f"{fn} of a matrix")
                v = getattr(_mp(), fn)(v)
            take(v)
        flush()
        if acc is None:
            raise MatrixError("an empty term")
        return acc

    def sum(self, s: str):
        toks = tokens(s)
        terms, cur, sign = [], [], 1
        for t in toks:
            if t["kind"] == "sign" and not t["post"]:
                if cur:
                    terms.append((sign, cur))
                    cur, sign = [], 1
                sign = sign * (-1 if t["raw"] == "-" else 1)
                continue
            cur.append(t)
        if cur:
            terms.append((sign, cur))
        if not terms:
            raise MatrixError("an empty expression")
        acc = None
        for sg, ts in terms:
            v = self.product(ts)
            v = _mul(-1, v) if sg < 0 else v
            acc = v if acc is None else _add(acc, v)
        return acc


# ---------------------------------------------------------------- reduction to a scalar text

def number(z) -> str:
    """A value as LaTeX the scalar parser reads exactly enough (no e-notation: e is Euler's)."""
    def real(x):
        m, e = f"{float(x):.17e}".split("e")
        return f"{m} \\cdot 10^{{{int(e)}}}"
    z = complex(z)
    if abs(z.imag) <= 1e-300:
        return f"({real(z.real)})"
    return f"({real(z.real)} + {real(z.imag)} \\cdot i)"


def _construct(s: str):
    """The innermost tr / etr / det / norm / bar construct holding an atom:
    (start, end, kind, argument) or None."""
    found = []
    for kind, rx in (("etr", ETR), ("tr", TR), ("det", DET)):
        for m in rx.finditer(s):
            j = m.end()
            while j < len(s) and s[j] == " ":
                j += 1
            if j < len(s) and (s[j] in "([{" or s.startswith("\\{", j)):
                e = _match(s, j)
                k = 2 if s[j] == "\\" else 1
                found.append((m.start(), e, kind, s[j + k:e - k]))
            else:                        # tr A, det A: the next token with its postfixes
                ts = tokens(s[j:])
                if ts and ts[0]["kind"] == "atom":
                    end = j + s[j:].index(ts[0]["raw"]) + len(ts[0]["raw"])
                    found.append((m.start(), end, kind, ts[0]["raw"]))
    for m in re.finditer(r"\\\|", s):          # \|v\|
        e = s.find("\\|", m.end())
        if e > 0 and not any(f[0] == e for f in found):
            found.append((m.start(), e + 2, "norm", s[m.end():e]))
            break
    bars = [i for i, c in enumerate(s) if c == "|" and (i == 0 or s[i - 1] != "\\")]
    depth_at = []
    d = 0
    for i, c in enumerate(s):
        if c in "([{":
            d += 1
        elif c in ")]}":
            d -= 1
        depth_at.append(d)
    stack = []
    for b in bars:
        if stack and depth_at[stack[-1]] == depth_at[b]:
            a = stack.pop()
            found.append((a, b + 1, "det", s[a + 1:b]))
        else:
            stack.append(b)
    found = [f for f in found if has_atom(f[3])]
    inner = [f for f in found if not any(g is not f and f[0] <= g[0] and g[1] <= f[1] for g in found)]
    return min(inner, key=lambda f: f[0]) if inner else None


def prepare(text: str, dist: dict, values: dict, defs: dict | None = None) -> str:
    """Atoms marked and every tr / etr / det / norm construct put in as a number."""
    mp = _mp()
    ev = Evaluator(values, defs)
    s = mark(I._clean(text), dist, values)
    for _ in range(200):
        f = _construct(s)
        if f is None:
            return s
        a, e, kind, arg = f
        v = ev.sum(arg)
        if kind == "tr":
            z = trace(v)
        elif kind == "etr":
            z = mp.exp(trace(v))
        elif kind == "det":
            z = det(v) if _is_mat(v) or isinstance(v, Eye) else abs(v)
        else:
            v = _concrete(v, "norm")
            z = mp.sqrt(sum(abs(v[i, j]) ** 2 for i in range(v.rows) for j in range(v.cols))) \
                if _is_mat(v) else abs(v)
        s = s[:a] + number(z) + s[e:]
    raise MatrixError("too many matrix constructs")


def scalar_text(s: str, values: dict, defs: dict | None = None) -> str:
    """Every remaining run of tokens holding an atom (between top-level signs and relations)
    put in as its 1 x 1 value."""
    ev = Evaluator(values, defs)
    toks = tokens(s)
    out, seg = [], []

    def close():
        idx = [k for k, t in enumerate(seg) if has_atom(t["raw"])]
        if not idx:
            out.extend(t["raw"] for t in seg)
        else:
            a, b = idx[0], idx[-1]
            if a > 0 and seg[a - 1]["kind"] == "cmd" and seg[a - 1]["name"] in FUNCS:
                a -= 1
            v = ev.product(seg[a:b + 1])
            if _is_mat(v) or isinstance(v, Eye):
                raise MatrixError("a matrix where a number is needed")
            out.extend(t["raw"] for t in seg[:a])
            out.append(number(v))
            out.extend(t["raw"] for t in seg[b + 1:])
        seg.clear()
    for t in toks:
        if t["kind"] in ("sign", "stop") and not t["post"]:
            close()
            out.append(t["raw"])
        else:
            seg.append(t)
    close()
    res = " ".join(out)
    if has_atom(res):
        raise MatrixError("a matrix left over")
    return res


def reduce(text: str, dist: dict, values: dict, defs: dict | None = None):
    """(scalar text, None), or (None, the matrix) when the whole piece is a matrix."""
    s = prepare(text, dist, values, defs)
    if not has_atom(s):
        return s, None
    try:
        v = Evaluator(values, defs).sum(s)
        if _is_mat(v):
            return None, v
    except MatrixError:
        pass
    return scalar_text(s, values, defs), None
