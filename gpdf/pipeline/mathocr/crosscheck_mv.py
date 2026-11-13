# SPDX-License-Identifier: AGPL-3.0-or-later
r"""Multivariate entries of gpdf-cli math-crosscheck (step 1): discrete multivariate
distributions (multinomial, multivariate hypergeometric, Dirichlet-multinomial, negative
multinomial, bivariate Poisson and binomial by trivariate reduction) and the multivariate
normal.

A table row is multivariate when it gives ``k`` (the dimension: what the book's ``k``
stands for in sums, products and lists). Its points give the vector argument (``"@x":
[2, 3, 1]``, ``"@t"`` for a pgf, mgf or cf) as components ``x_1, x_2, ...`` (``var`` and
``first`` rename and renumber them, ``components`` names them outright: ``["x", "y"]``).
``derived`` parameters are computed in order, each seeing the ones before and the
components. Before parsing, at each point:

- a sum or product over a fixed range (``\sum_{i=1}^{k}``, ``\prod_{i=0}^{2}``, or an upper
  limit the point's values give, such as ``\min(x_1, x_2)``) is written out term by term;
- an entry's free indices (``"index": [1, 2]`` for ``i``, ``j``) are put in;
- a list with dots (``x_1 + x_2 + \cdots + x_k``, ``n_1! n_2! \cdots n_k!``,
  ``x_1, \dots, x_k``) is written out;
- a multinomial coefficient ``\binom{n}{x_1, x_2, x_3}`` becomes ``n!/(x_1! x_2! x_3!)``;
- a bold symbol with a subscript is its component (``\boldsymbol{x}_1`` is ``x_1``).

Quantities: ``pmf`` / ``pdf`` (the joint), ``marginal_pmf`` / ``marginal_pdf`` (``index``;
``sum_out`` sums the book's joint pmf over the other component instead), ``mean``, ``var``
(``index``), ``cov``, ``corr`` (``index`` [i, j]), ``fact_moment`` (``r``: one order per
component, E[prod X_i^(r_i)] with falling factorials), ``pgf``, ``mgf``, ``cf`` (vector
``@t``), ``cond_pmf``, ``cond_mean``, ``cond_var`` (``index`` [i], ``given`` [j, ...]: the
point's components). The reference (crosscheck_ref.py) sums exactly over the support for
the discrete families and uses closed forms for the normal; ``ref_x`` (a Python expression
in the book's names) gives the reference's vector when it differs from ``@x`` (a cell the
book leaves out: ``[n - x_1 - x_2, x_1, x_2]``).
"""

from __future__ import annotations

import re

from gpdf.pipeline.mathocr import crosscheck as C
from gpdf.pipeline.mathocr import crosscheck_mat as MX
from gpdf.pipeline.mathocr import identities as I
from gpdf.pipeline.mathocr import watch

QUANTITIES = ("pmf", "pdf", "marginal_pmf", "marginal_pdf", "mean", "var", "cov", "corr",
              "fact_moment", "pgf", "mgf", "cf", "cond_pmf", "cond_mean", "cond_var",
              "mode", "entropy", "mixed_moment", "cond_pdf", "cond_moment", "sf", "cdf",
              "mean_square", "mean_inverse", "mean_quadratic")
SUMPROD = re.compile(r"\\(sum|prod)_\{\s*([a-z])\s*=\s*(-?\d+)\s*\}\^")
DOTS = re.compile(r"\\(?:ldots|cdots|dotsc|dotsb|dots)(?![A-Za-z])")
BOLD_SUB = re.compile(r"\\(?:mathbf|boldsymbol|bm|pmb)\{(\\?[A-Za-z]+)\}(?=_)")
STOP = ("\\quad", "\\qquad", "\\\\")


# ---------------------------------------------------------------- expansion

def _group_end(s: str, i: int) -> int | None:
    """s[i] is "{": the index after its matching "}"."""
    depth = 0
    for j in range(i, len(s)):
        if s[j] == "{" and (j == 0 or s[j - 1] != "\\"):
            depth += 1
        elif s[j] == "}" and (j == 0 or s[j - 1] != "\\"):
            depth -= 1
            if depth == 0:
                return j + 1
    return None


def _limit(text: str, values: dict, K: int | None) -> int | None:
    """An integer limit: k, a number, \\min/\\max of such, or an expression in the values."""
    t = text.strip()
    if t == "k" and K is not None:
        return K
    if re.fullmatch(r"-?\d+", t):
        return int(t)
    m = re.fullmatch(r"\\(min|max)\s*\((.*)\)", t)
    if m:
        parts = [_limit(p, values, K) for p in I._split_top(m.group(2), [","])]
        if None in parts:
            return None
        return (min if m.group(1) == "min" else max)(parts)
    ex = C.book_expr(t)
    if ex is None:
        return None
    v, _ = C.evaluate(ex, values)
    if v is None or abs(v.imag) > 1e-12 or abs(v.real - round(v.real)) > 1e-9:
        return None
    return int(round(v.real))


def _term_end(s: str, i: int) -> int:
    """End of the summand starting at i: the next top-level +, -, =, comma, &, \\quad or
    row break, or a closing bracket of the enclosing group."""
    n = len(s)
    while i < n and s[i] == " ":
        i += 1
    start = i
    if i < n and s[i] in "+-":
        i += 1
    depth = 0
    while i < n:
        c = s[i]
        if c == "\\":
            if depth == 0 and s.startswith(STOP, i):
                break
            tok = re.match(r"\\(?:[A-Za-z]+|.)", s[i:]).group(0)
            if tok == "\\{":
                depth += 1
            elif tok == "\\}":
                if depth == 0:
                    break
                depth -= 1
            i += len(tok)
            continue
        if c in "{([":
            depth += 1
        elif c in "})]":
            if depth == 0:
                break
            depth -= 1
        elif depth == 0 and c in "+-=,;&" and i > start:
            break
        i += 1
    return i


def _sub_index(s: str, name: str, v: int) -> str:
    return re.sub(r"(?<![A-Za-z\\])" + name + r"(?![A-Za-z])", str(v), s)


def expand_sums(s: str, values: dict, K: int | None) -> str:
    """Sums and products over a range known at this point, written out (innermost first)."""
    for _ in range(100):
        for m in reversed(list(SUMPROD.finditer(s))):
            j = m.end()
            if j < len(s) and s[j] == "{":
                e = _group_end(s, j)
                if e is None:
                    continue
                up = s[j + 1:e - 1]
            else:
                tok = re.match(r"\\[A-Za-z]+|.", s[j:])
                if not tok:
                    continue
                e, up = j + len(tok.group(0)), tok.group(0)
            hi = _limit(up, values, K)
            if hi is None:
                continue
            end = _term_end(s, e)
            body, idx, lo = s[e:end].strip(), m.group(2), int(m.group(3))
            terms = [f"({_sub_index(body, idx, v)})" for v in range(lo, hi + 1)]
            if m.group(1) == "sum":
                joined = " + ".join(terms) or "0"
            else:
                joined = " \\cdot ".join(terms) or "1"
            raw = s[e:end]
            s = s[:m.start()] + "(" + joined + ")" + raw[len(raw.rstrip()):] + s[end:]
            break
        else:
            return s
    return s


def _back(s: str) -> int:
    i, depth = len(s) - 1, 0
    while i >= 0:
        c = s[i]
        if c in ")}]":
            depth += 1
        elif c in "({[":
            if depth == 0:
                break
            depth -= 1
        elif depth == 0 and (c == " " or c in "+-=,;&"):
            break
        i -= 1
    return i + 1


def _fwd(s: str) -> int:
    i, depth = 0, 0
    while i < len(s):
        c = s[i]
        if c in "({[":
            depth += 1
        elif c in ")}]":
            if depth == 0:
                break
            depth -= 1
        elif depth == 0 and (c == " " or c in "+-=,;&"):
            break
        i += 1
    return i


def expand_dots(s: str, values: dict, K: int | None) -> str:
    """T(1) op T(2) op \\cdots op T(k) written out (op: comma, +, \\cdot, \\times or none)."""
    s = re.sub(r"_([A-Za-z0-9])", r"_{\1}", s)
    for _ in range(50):
        for m in reversed(list(DOTS.finditer(s))):
            left, right = s[:m.start()].rstrip(), s[m.end():].lstrip()
            op = next((o for o in (",", "+", "-", "\\cdot", "\\times") if left.endswith(o)), "")
            if op:
                left = left[:-len(op)].rstrip()
                if not right.startswith(op):
                    continue
                right = right[len(op):].lstrip()
            lt, b = left[_back(left):], _fwd(right)
            rt = right[:b]
            subs = list(re.finditer(r"_\{([A-Za-z]|\d+)\}", rt))
            if not lt or not subs:
                continue
            sm = subs[-1]
            hi = _limit(sm.group(1), values, K)
            tmpl = rt[:sm.start()] + "_{@@}" + rt[sm.end():]
            mm = re.fullmatch(re.escape(tmpl).replace("@@", r"(-?\d+)"), lt)
            if hi is None or not mm or hi < int(mm.group(1)):
                continue
            lo = int(mm.group(1))
            sep = ", " if op == "," else f" {op} " if op else " "
            mid = [tmpl.replace("@@", str(v)) for v in range(lo + 1, hi + 1)]
            s = left + "".join(sep + t for t in mid) + right[b:]
            break
        else:
            return s
    return s


def multinomials(s: str) -> str:
    r"""\binom{n}{a, b, c} -> \frac{(n)!}{(a)!(b)!(c)!}."""
    i = 0
    while True:
        j = s.find("\\binom{", i)
        if j < 0:
            return s
        e1 = _group_end(s, j + 6)
        e2 = _group_end(s, e1) if e1 and e1 < len(s) and s[e1] == "{" else None
        if e2:
            top, low = s[j + 7:e1 - 1], s[e1 + 1:e2 - 1]
            parts = [p.strip() for p in I._split_top(low, [","])]
            if len(parts) > 1:
                rep = "\\frac{(" + top + ")!}{" + "".join(f"({p})!" for p in parts) + "}"
                s = s[:j] + rep + s[e2:]
        i = j + 1


# a symbol (subscripted or not) right before "(" is a product, not a call: the parser would
# read p_{1}(1 - p_{1}) as a function p_1 (known functions and B(a, b) excepted)
CALL_LIKE = re.compile(r"(?<![A-Za-z\\])((?:[A-AC-Za-z]|\\(?!(?:Gamma|exp|log|ln|sin|cos|tan|sqrt|frac|binom|"
                       r"Phi|phi|min|max|Pr|operatorname|mathrm|text|cdot|times|div|pm|mp)(?![A-Za-z]))[A-Za-z]+)"
                       r"(?:_\{[^{}]*\}|_[A-Za-z0-9])?(?:\^\{[^{}]*\}|\^[A-Za-z0-9])?)\s*\(")


def expand(text: str, values: dict, K: int | None, index: dict | None = None) -> str:
    s = BOLD_SUB.sub(r"\1", I._clean(text))
    # p_{0 1} (the engine's spacing) is the one subscript 01: the parser splits it otherwise
    s = re.sub(r"_\{([A-Za-z0-9 ]+)\}", lambda m: "_{" + m.group(1).replace(" ", "") + "}", s)
    s = expand_sums(s, values, K)
    for name, v in (index or {}).items():
        s = _sub_index(s, name, v)
    return multinomials(expand_dots(s, values, K))


LEAD_ZERO = re.compile(r"(?<![A-Za-z])(\\?[A-Za-z]+)_\{(0\d+)\}")


def for_parser(s: str, values: dict | None = None) -> str:
    """A product sign where the parser would see a call; a symbol whose subscript has a leading
    zero (p_{01}, p_{00}: sympy reads 01 as a number or fails) replaced by its value."""
    def num(m):
        key = f"{m.group(1).lstrip(chr(92))}_{m.group(2)}"
        return f"({values[key]!r})" if values and key in values else m.group(0)

    def call(m):                          # I_{p/2-1}(kappa) stays a call: the modified Bessel function
        if re.fullmatch(r"I_(?:\{" + I.BESSEL_ORDER + r"\}|[0-9a-z])", m.group(1)):
            return m.group(0)
        return m.group(1) + " \\cdot ("
    return LEAD_ZERO.sub(num, CALL_LIKE.sub(call, s))


# ---------------------------------------------------------------- one entry

def _values(point: dict, dist: dict, e: dict, K: int) -> dict:
    vals = {k: v for k, v in point.items() if not k.startswith("@")}
    if K is not None:
        vals.setdefault("k", K)
    first = int(e.get("first", dist.get("first", 1)))
    x = point.get("@x") or []
    var = e.get("var", dist.get("var", "x"))
    names = e.get("components") or dist.get("components") or [
        f"{var}_{j}" for j in range(first, first + len(x))]
    vals.update(zip(names, x))
    for j, t in enumerate(point.get("@t") or [], first):
        vals[f"{e.get('tvar', 't')}_{j}"] = t
    for j, r in enumerate(e.get("r") or [], first):
        vals[f"r_{j}"] = r
    for name, ex in {**dist.get("derived", {}), **e.get("derived", {})}.items():
        vals[name] = C._eval_all({name: ex}, vals)[name]
    return vals


def _sum_out(expr, vals: dict, so: dict):
    """The book's joint pmf summed over one component (0 .. top - 1)."""
    import mpmath
    import sympy
    over = so["over"]
    syms = {I._canon(s.name): s for s in expr.free_symbols}
    missing = sorted(n for n in syms if n != over and n not in vals)
    if missing:
        return None, "symbols the table does not give: " + ", ".join(missing)
    fixed = expr.xreplace({s: sympy.Float(vals[n], 30) for n, s in syms.items() if n != over})
    if over not in syms:
        return None, f"{over} is not in the formula"
    g = sympy.lambdify([syms[over]], fixed, "mpmath")
    mpmath.mp.dps = 30
    try:
        tot = mpmath.fsum(g(mpmath.mpf(y)) for y in range(int(so.get("top", 300))))
    except (TypeError, ValueError, ZeroDivisionError, OverflowError) as ex:
        return None, f"not a number ({type(ex).__name__})"
    return complex(tot), None


def _listed(m) -> list:
    return [[float(complex(m[i, j]).real) for j in range(m.cols)] for i in range(m.rows)]


def _close_matrix(m, ref, tol: float):
    """(ok, worst error) of a matrix against the reference's nested list; (None, why) when
    the reference has no matrix of that size."""
    if not ref:
        return None, "no matrix value for this quantity"
    rows = ref if isinstance(ref[0], list) else [[v] for v in ref]
    if (len(rows), len(rows[0])) != (m.rows, m.cols):
        return None, f"sizes differ: book {m.rows}x{m.cols}, reference {len(rows)}x{len(rows[0])}"
    worst, ok = 0.0, True
    for i in range(m.rows):
        for j in range(m.cols):
            o, err = C._close(complex(m[i, j]), complex(rows[i][j]), tol)
            ok, worst = ok and o, max(worst, err)
    return ok, worst


def check(e: dict, pages: dict, spec: dict, ref) -> dict:
    res = {"id": e.get("id"), "qty": e.get("qty"), "dist": e["dist"], "multivariate": True,
           "harvested": bool(e.get("harvested")), "status": "inconclusive", "points": []}
    try:
        f, kind, k, latex = C.locate(pages, e["where"])
    except C.CrosscheckError as ex:
        res["reason"] = str(ex)
        return res
    m = re.search(r'<!--\s*page\s+printed="([^"]*)"', pages[f])
    res.update(file=f, printed=m.group(1) if m else None, formula=f"p{f:03d}-{kind}{k:02d}",
               latex=latex)
    q, dist = e.get("qty"), spec["dists"][e["dist"]]
    if q not in QUANTITIES:
        res["reason"] = f"unknown multivariate quantity {q}"
        return res
    src = latex
    for rx, rep in spec.get("notation", []) + e.get("notation", []):
        src = re.sub(rx, rep, src)
    try:
        text, _ = C.cut(src, e.get("row"), e.get("eq", "rhs" if kind == "d" else "full"))
    except (C.CrosscheckError, IndexError) as ex:
        res["reason"] = str(ex)
        return res
    res["piece"] = text
    defs = {**spec.get("defs", {}), **e.get("defs", {})}
    mat = MX.is_matrix(dist)
    K = int(dist["k"]) if dist.get("k") is not None else None
    tol = float(e.get("tol", spec.get("tol", C.TOL)))
    first = int(e.get("first", dist.get("first", 1)))
    offset = int(e.get("ref_offset", dist.get("ref_offset", 0)))

    def pos(js):                          # book index -> reference ([[i, j], [k, l]] for elements)
        return [pos(j) if isinstance(j, list) else int(j) - first + offset for j in js]
    if mat:
        text = MX.gamma_p(text)
    worst, failed = 0.0, False
    try:
        with watch.hard_timeout(C.ITEM_TIMEOUT):
            for point in dist["points"]:
                point = {**point, **e.get("fixed", {})}
                vals = _values(point, dist, e, K)
                index = dict(zip(e.get("index_names", ["i", "j"]), e.get("index") or []))
                txt = expand(text, vals, K, index)
                res["expanded"] = txt
                # q, Q (and c, C, K) the table gives are the table's, not the books' q = 1 - p
                d = {**defs, **{n: repr(vals[n]) for n in I.DEF_NAMES if n in vals and n not in defs}}
                bmat = b = None
                if mat:
                    try:
                        txt, bmat = MX.reduce(txt, dist, vals, d)
                    except MX.MatrixError as ex:
                        res["reason"] = f"matrix expression not read: {ex}"
                        return res
                    res["reduced"] = txt
                if bmat is None:
                    expr = C.book_expr(for_parser(txt, vals), (), d)
                    if expr is None:
                        res["reason"] = "the formula does not parse (after expansion)"
                        return res
                    scal = {k2: v for k2, v in vals.items() if not isinstance(v, (list, tuple))}
                    if e.get("sum_out"):
                        b, why = _sum_out(expr, scal, e["sum_out"])
                    else:
                        b, why = C.evaluate(expr, scal)
                    if b is None:
                        res["points"].append({"values": vals, "book": None})
                        res["reason"] = why
                        return res
                args = C._eval_all(dist.get("args", {}), vals)
                rx = e.get("ref_x", dist.get("ref_x"))
                if rx is None and mat and "@x" not in point:
                    rx = dist.get("var", "X")
                x = C._eval_all({"x": rx}, vals)["x"] if rx else point.get("@x")
                tv = point.get("@t")
                tv = vals[tv] if isinstance(tv, str) else tv
                a = ref.ask({"dist": dist["scipy"], "multivariate": True, "args": args, "qty": q,
                             "x": x, "t": tv, "index": pos(e.get("index") or []),
                             "given": pos(e.get("given") or []), "r": e.get("r"),
                             **({"whole": True} if bmat is not None else {}),
                             **({"expr": dist["expr"]} if dist.get("expr") else {})})
                if "error" in a:
                    res["points"].append({"values": vals, "book": None if b is None else [b.real, b.imag]})
                    res["reason"] = "reference: " + a["error"]
                    return res
                t = tol
                if a.get("tol_floor", 0) * 10 > t:
                    t = a["tol_floor"] * 10
                    res["tol_reason"] = f"loosened to {t:.1e}: {a['how']} error estimate"
                if bmat is not None:          # a matrix: element by element
                    ok, err = _close_matrix(bmat, a.get("matrix"), t)
                    if ok is None:
                        res["reason"] = "reference: " + err
                        return res
                    worst, failed = max(worst, err), failed or not ok
                    res["points"].append({"values": vals, "args": args, "book": _listed(bmat),
                                          "ref": a["matrix"], "err": err, "ok": ok, "tol": t})
                    continue
                s = complex(a["value"], a.get("imag") or 0.0)
                ok, err = C._close(b, s, t)
                worst, failed = max(worst, err), failed or not ok
                res["points"].append({"values": vals, "args": args, "book": [b.real, b.imag],
                                      "ref": [s.real, s.imag], "err": err, "ok": ok, "tol": t})
    except watch.ItemTimeout:
        res["reason"] = f"over {C.ITEM_TIMEOUT:g} s"
        return res
    res["status"] = "failed" if failed else "passed"
    res["worst"] = worst
    return res
