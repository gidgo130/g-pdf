# SPDX-License-Identifier: AGPL-3.0-or-later
"""The reference side of gpdf-cli math-crosscheck, run by an external Python that has SciPy
(``--scipy-python``; SciPy is never a g-pdf dependency). It imports nothing from g-pdf, reads
one JSON request per line on stdin and writes one JSON answer per line:

    {"dist": "gamma", "args": {"a": 2.5, "scale": 1.5}, "qty": "pdf", "x": 2.2, "r": null}
 -> {"value": 0.2384..., "imag": null, "tol_floor": 0.0, "how": "scipy gamma.pdf"}
 or {"error": "..."}

``tol_floor`` is the relative accuracy the reference itself can promise where it comes from
quadrature (moment generating and characteristic functions, central moments without a
closed form): the quadrature's own error estimate, so a looser tolerance is recorded, never
silently assumed.
"""

import json
import math
import sys
import warnings

warnings.filterwarnings("ignore")

import numpy as np                                         # noqa: E402
from scipy import integrate, optimize, special, stats      # noqa: E402,F401


def _dist(name, args):
    d = getattr(stats, name)
    return d(**args)


def _support(d):
    lo, hi = d.support()
    return float(lo), float(hi)


def _quad(f, d):
    lo, hi = _support(d)
    pts = None
    if math.isinf(lo) or math.isinf(hi):
        v, err = integrate.quad(f, lo, hi, epsabs=1e-14, epsrel=1e-13, limit=500)
    else:
        v, err = integrate.quad(f, lo, hi, epsabs=1e-14, epsrel=1e-13, limit=500, points=pts)
    return v, err


def _discrete_sum(f, d):
    """sum of f(k) over the support; f takes a numpy array of k. An infinite support is cut
    where the pmf has fallen below 1e-20 (the pmf, not sf: some sf take minutes far out)."""
    lo, hi = _support(d)
    lo = int(lo)
    if math.isinf(hi):
        top = lo + 64
        while top < 10 ** 7 and d.pmf(top) > 1e-20:
            top = top * 2
    else:
        top = int(hi)
    ks = np.arange(lo, max(int(top), lo) + 1)
    return float(np.sum(f(ks))), 0.0


def _expect(f, d, discrete):
    """E[f(X)]; f must take numpy arrays (np.cos, x ** k)."""
    if discrete:
        return _discrete_sum(lambda k: f(k) * d.pmf(k), d)
    return _quad(lambda y: f(y) * d.pdf(y), d)


def _rel(err, v):
    return 0.0 if v == 0 else abs(err / v)


def _raw(d, r):
    return float(d.moment(int(r)))


# ---------------------------------------------------------------- multivariate (step 1)

def _compositions(n, k):
    if k == 1:
        yield (n,)
        return
    for a in range(n + 1):
        for rest in _compositions(n - a, k - 1):
            yield (a,) + rest


def _top(d):
    """A cut for an infinite discrete support: sf below 1e-18 (isf gives NaN that far out)."""
    t = int(d.mean() + 10 * d.std() + 20)
    while d.sf(t) > 1e-18 and t < 10 ** 6:
        t *= 2
    return t + 1


def _grid(tops):
    return np.array(np.meshgrid(*[np.arange(t) for t in tops], indexing="ij")).reshape(len(tops), -1).T


def _nm_logpmf(X, n, p, p0):             # negative multinomial: Gamma(n + sum x)/(Gamma(n) prod x!) p0^n prod p^x
    X = np.atleast_2d(np.asarray(X, float))
    return (special.gammaln(n + X.sum(1)) - special.gammaln(n) - special.gammaln(X + 1).sum(1)
            + n * math.log(p0) + (X * np.log(p)).sum(1))


def _bp_pmf(x1, x2, t1, t2, t12):        # X1 = Y1 + Y12, X2 = Y2 + Y12, independent Poissons
    return sum(stats.poisson.pmf(x1 - i, t1) * stats.poisson.pmf(x2 - i, t2) * stats.poisson.pmf(i, t12)
               for i in range(int(min(x1, x2)) + 1))


def _bb_pmf(x1, x2, n, p11, p10, p01):   # (X1, X2) = (Y11 + Y10, Y11 + Y01), Y ~ multinomial(n; p11, p10, p01, p00)
    p00 = 1 - p11 - p10 - p01
    return sum(stats.multinomial.pmf([i, x1 - i, x2 - i, n - x1 - x2 + i], n, [p11, p10, p01, p00])
               for i in range(int(min(x1, x2)) + 1) if n - x1 - x2 + i >= 0)


def _mv_pmf(name, a, x):
    if name == "multinomial":
        return float(stats.multinomial.pmf(x, int(a["n"]), a["p"]))
    if name == "multivariate_hypergeom":
        return float(stats.multivariate_hypergeom.pmf(x, a["m"], int(a["n"])))
    if name == "dirichlet_multinomial":
        return float(stats.dirichlet_multinomial.pmf(x, a["alpha"], int(a["n"])))
    if name == "negative_multinomial":
        p = np.asarray(a["p"], float)
        return float(np.exp(_nm_logpmf([x], float(a["n"]), p, 1 - p.sum()))[0])
    if name == "bivariate_poisson":
        return float(_bp_pmf(x[0], x[1], a["theta1"], a["theta2"], a["theta12"]))
    if name == "bivariate_binomial":
        return float(_bb_pmf(x[0], x[1], int(a["n"]), a["p11"], a["p10"], a["p01"]))
    raise ValueError(f"no multivariate {name}")


def _mv_support(name, a):
    """(X: one support point per row, w: its probability) for exact sums over the support; an
    infinite support is cut where each marginal's tail is below 1e-18."""
    if name in ("multinomial", "dirichlet_multinomial"):
        k = len(a["p"] if name == "multinomial" else a["alpha"])
        X = np.array(list(_compositions(int(a["n"]), k)))
    elif name == "multivariate_hypergeom":
        X = np.array([c for c in _compositions(int(a["n"]), len(a["m"]))
                      if all(ci <= mi for ci, mi in zip(c, a["m"]))])
    elif name == "negative_multinomial":
        p = np.asarray(a["p"], float)
        p0, n = 1 - p.sum(), float(a["n"])
        X = _grid([_top(stats.nbinom(n, p0 / (p0 + pi))) for pi in p])
        return X, np.exp(_nm_logpmf(X, n, p, p0))
    elif name == "bivariate_poisson":
        t1, t2, t12 = a["theta1"], a["theta2"], a["theta12"]
        X = _grid([_top(stats.poisson(t1 + t12)), _top(stats.poisson(t2 + t12))])
    elif name == "bivariate_binomial":
        X = _grid([int(a["n"]) + 1] * 2)
    else:
        raise ValueError(f"no multivariate {name}")
    return X, np.array([_mv_pmf(name, a, list(x)) for x in X])


def _mvn(q):
    a, qty, x, t = q.get("args", {}), q["qty"], q.get("x"), q.get("t")
    mu, S = np.asarray(a["mean"], float), np.asarray(a["cov"], float)
    idx, given = q.get("index") or [], q.get("given") or []
    out = {"value": None, "imag": None, "tol_floor": 0.0, "how": f"multivariate normal {qty} (closed form)"}
    if qty == "pdf":
        out["value"] = float(stats.multivariate_normal.pdf(x, mu, S))
    elif qty == "marginal_pdf":
        i = idx[0]
        out["value"] = float(stats.norm.pdf(x[i], mu[i], math.sqrt(S[i, i])))
    elif qty in ("mean", "var", "cov", "corr"):
        i = idx[0]
        j = idx[1] if len(idx) > 1 else i
        out["value"] = float({"mean": mu[i], "var": S[i, i], "cov": S[i, j],
                              "corr": S[i, j] / math.sqrt(S[i, i] * S[j, j])}[qty])
    elif qty in ("mgf", "cf"):
        tv = np.asarray(t, float)
        z = complex(0, 1) if qty == "cf" else 1.0
        v = np.exp(z * tv @ mu + (z * z) * (tv @ S @ tv) / 2)
        out["value"], out["imag"] = float(np.real(v)), float(np.imag(v))
    elif qty in ("cond_mean", "cond_var") and len(given) == 1:
        i, j = idx[0], given[0]
        out["value"] = float(mu[i] + S[i, j] / S[j, j] * (x[j] - mu[j]) if qty == "cond_mean"
                             else S[i, i] - S[i, j] ** 2 / S[j, j])
    else:
        return {"error": f"no multivariate normal {qty} here"}
    return out


# ---------------------------------------------------------------- matrix variate (step 2)

MATRIX_FAMILIES = ("dirichlet", "multivariate_t", "wishart", "invwishart", "matrix_normal",
                   "vonmises_fisher")
VECTOR_FAMILIES = ("dirichlet", "multivariate_t", "vonmises_fisher", "multivariate_normal")


def _np(a):
    return {k: (np.asarray(v, float) if isinstance(v, list) else v) for k, v in a.items()}


def _family(name, a):
    """(frozen SciPy object or None, mean, mode, cov(i, j)) with i, j element indices (an int
    for a vector family, a pair for a matrix family)."""
    if name == "dirichlet":
        al = a["alpha"]
        a0, k = al.sum(), len(al)
        cov = lambda i, j: ((i == j) * al[i] * a0 - al[i] * al[j]) / (a0 ** 2 * (a0 + 1))  # noqa: E731
        return stats.dirichlet(al), al / a0, (al - 1) / (a0 - k), cov
    if name == "multivariate_t":
        loc, sh, df = a["loc"], a["shape"], a["df"]
        return (stats.multivariate_t(loc, sh, df), loc, loc,
                lambda i, j: df / (df - 2) * sh[i, j])
    if name == "multivariate_normal":
        mu, S = a["mean"], a["cov"]
        return stats.multivariate_normal(mu, S), mu, mu, lambda i, j: S[i, j]
    if name == "wishart":
        n, S = a["df"], a["scale"]
        p = S.shape[0]
        return (stats.wishart(n, S), n * S, (n - p - 1) * S,
                lambda u, v: n * (S[u[0], v[0]] * S[u[1], v[1]] + S[u[0], v[1]] * S[u[1], v[0]]))
    if name == "invwishart":
        n, P = a["df"], a["scale"]
        p = P.shape[0]
        den = (n - p) * (n - p - 1) ** 2 * (n - p - 3)
        cov = lambda u, v: (2 * P[u[0], u[1]] * P[v[0], v[1]] + (n - p - 1) * (  # noqa: E731
            P[u[0], v[0]] * P[u[1], v[1]] + P[u[0], v[1]] * P[v[0], u[1]])) / den
        return stats.invwishart(n, P), P / (n - p - 1), P / (n + p + 1), cov
    if name == "matrix_normal":
        M, U, V = a["mean"], a["rowcov"], a["colcov"]
        return (stats.matrix_normal(M, U, V), M, M, lambda u, v: U[u[0], v[0]] * V[u[1], v[1]])
    if name == "vonmises_fisher":
        mu, kap = a["mu"], a["kappa"]
        p = len(mu)
        A = special.ive(p / 2, kap) / special.ive(p / 2 - 1, kap)
        return stats.vonmises_fisher(mu, kap), A * mu, mu, None
    raise ValueError(f"no matrix-variate {name}")


SECOND_MOMENTS = ("mean_square", "mean_inverse", "mean_quadratic")


def _second_moment(name, a, qty):
    """Matrix second moments in closed form (Gupta-Nagar's conventions, Monte Carlo-checked in the tests):
    W ~ wishart(n, S): E(W W) = n(n + 1) S S + n tr(S) S and E(W^{-1}) = S^{-1} / (n - p - 1);
    X ~ matrix_normal(M, U, V): E(X A X') = M A M' + tr(A V') U (A from the args)."""
    if name == "wishart" and qty in ("mean_square", "mean_inverse"):
        n, S = a["df"], a["scale"]
        p = S.shape[0]
        if qty == "mean_square":
            return n * (n + 1) * S @ S + n * np.trace(S) * S
        return np.linalg.inv(S) / (n - p - 1)
    if name == "matrix_normal" and qty == "mean_quadratic" and "A" in a:
        M, U, V, A = a["mean"], a["rowcov"], a["colcov"], a["A"]
        return M @ A @ M.T + np.trace(A @ V.T) * U
    return None


def _el(arr, i):
    return float(arr[tuple(i)] if isinstance(i, (list, tuple)) else arr[i])


def _mat(q):
    name, qty = q["dist"], q["qty"]
    a = _np(q.get("args", {}))
    x, idx = q.get("x"), q.get("index") or []
    d, mean, mode, cov = _family(name, a)
    out = {"value": None, "imag": None, "tol_floor": 0.0, "how": f"{name} {qty} (scipy / closed form)"}
    vec = name in VECTOR_FAMILIES
    if qty in SECOND_MOMENTS:
        m = _second_moment(name, a, qty)
        if m is None:
            return {"error": f"no {qty} for {name}"}
        if q.get("whole"):
            out["matrix"] = m.tolist()
        else:
            out["value"] = _el(m, idx)
        return out
    if q.get("whole"):                       # the book's side is a whole vector or matrix
        if qty in ("mean", "mode"):
            m = np.atleast_1d(mean if qty == "mean" else mode)
        elif qty == "cov" and cov is not None:
            if vec:
                k = len(mean)
                m = np.array([[cov(i, j) for j in range(k)] for i in range(k)])
            else:                            # cov of vec(X), columns stacked
                r, c = np.shape(mean)
                els = [(i, j) for j in range(c) for i in range(r)]
                m = np.array([[cov(u, v) for v in els] for u in els])
        else:
            return {"error": f"no whole {qty} for {name}"}
        out["matrix"] = (m.reshape(-1, 1) if m.ndim == 1 else m).tolist()
        return out
    if qty == "pdf":
        out["value"] = float(d.pdf(np.asarray(x, float)))
    elif qty == "entropy":
        out["value"] = float(d.entropy())
    elif qty in ("mean", "mode"):
        if not idx:
            return {"error": f"{qty} needs an index (or a whole-vector formula)"}
        out["value"] = _el(mean if qty == "mean" else mode, idx[0] if vec else idx)
    elif qty in ("var", "cov", "corr"):
        if cov is None:
            return {"error": f"no {qty} for {name}"}
        if vec:
            i, j = idx[0], idx[1] if len(idx) > 1 else idx[0]
        else:
            i, j = (idx[0], idx[1]) if qty != "var" else (idx, idx)
        v = cov(i, j)
        if qty == "corr":
            v = v / math.sqrt(cov(i, i) * cov(j, j))
        out["value"] = float(v)
    elif qty == "marginal_pdf":
        X = np.asarray(x, float)
        if name == "dirichlet":
            al = a["alpha"]
            out["value"] = float(stats.beta.pdf(X[idx[0]], al[idx[0]], al.sum() - al[idx[0]]))
        elif name in ("multivariate_t", "multivariate_normal"):
            i = idx[0]
            loc = a["loc"] if name == "multivariate_t" else a["mean"]
            sh = a["shape"] if name == "multivariate_t" else a["cov"]
            m1 = (stats.t(a["df"], loc[i], math.sqrt(sh[i, i])) if name == "multivariate_t"
                  else stats.norm(loc[i], math.sqrt(sh[i, i])))
            out["value"] = float(m1.pdf(X[i]))
        elif name == "wishart" and idx[0] == idx[1]:
            s = a["scale"][idx[0], idx[0]]
            out["value"] = float(stats.chi2.pdf(X[tuple(idx)] / s, a["df"]) / s)
        elif name == "matrix_normal":
            i, j = idx
            out["value"] = float(stats.norm.pdf(X[i, j], a["mean"][i, j],
                                                math.sqrt(a["rowcov"][i, i] * a["colcov"][j, j])))
        else:
            return {"error": f"no marginal of {name} at {idx}"}
    elif qty == "mixed_moment" and name == "dirichlet":
        al, r = a["alpha"], np.asarray(q["r"], float)
        out["value"] = float(np.exp(special.gammaln(al.sum()) - special.gammaln(al.sum() + r.sum())
                                    + (special.gammaln(al + r) - special.gammaln(al)).sum()))
    elif qty in ("mgf", "cf") and name in ("wishart", "matrix_normal"):
        T = np.asarray(q["t"], float)
        z = 1j if qty == "cf" else 1.0
        if name == "wishart":
            p = T.shape[0]
            v = np.linalg.det(np.eye(p) - 2 * z * T @ a["scale"]) ** (-a["df"] / 2)
        else:                                # vec(X) ~ N(vec M, V kron U), columns stacked
            t = T.reshape(-1, order="F")
            m = a["mean"].reshape(-1, order="F")
            v = np.exp(z * t @ m + z * z * (t @ np.kron(a["colcov"], a["rowcov"]) @ t) / 2)
        out["value"], out["imag"] = float(np.real(v)), float(np.imag(v))
    else:
        return {"error": f"no {qty} for {name}"}
    return out


def answer_mv(q):
    name, a, qty = q["dist"], q.get("args", {}), q["qty"]
    if name in MATRIX_FAMILIES or (name == "multivariate_normal" and (
            q.get("whole") or qty in ("mode", "entropy"))):
        return _mat(q)
    if name == "multivariate_normal":
        return _mvn(q)
    x, idx, given = q.get("x"), q.get("index") or [], q.get("given") or []
    out = {"value": None, "imag": None, "tol_floor": 0.0, "how": f"{name} {qty}"}
    if qty in ("pmf", "pdf"):
        out["value"] = _mv_pmf(name, a, x)
        return out
    X, w = _mv_support(name, a)
    out["tol_floor"] = abs(1.0 - float(w.sum()))
    out["how"] = f"exact sum over the {name} support ({len(w)} points)"
    if qty in ("marginal_pmf", "marginal_pdf"):
        out["value"] = float(w[X[:, idx[0]] == x[idx[0]]].sum())
    elif qty in ("mean", "var", "cov", "corr"):
        i = idx[0]
        j = idx[1] if len(idx) > 1 else i
        mi, mj = float(w @ X[:, i]), float(w @ X[:, j])
        cov = float(w @ (X[:, i] * X[:, j])) - mi * mj
        vi, vj = float(w @ X[:, i] ** 2) - mi * mi, float(w @ X[:, j] ** 2) - mj * mj
        out["value"] = {"mean": mi, "var": vi, "cov": cov, "corr": cov / math.sqrt(vi * vj)}[qty]
    elif qty == "fact_moment":
        f = np.ones(len(w))
        for i, r in enumerate(q["r"]):
            for j in range(int(r)):
                f = f * (X[:, i] - j)
        out["value"] = float(w @ f)
    elif qty == "pgf":
        out["value"] = float(w @ np.prod(np.power(np.asarray(q["t"], float), X), axis=1))
    elif qty in ("cond_pmf", "cond_mean", "cond_var"):
        sel = np.all([X[:, g] == x[g] for g in given], axis=0)
        Xs, ws = X[sel], w[sel] / w[sel].sum()
        i = idx[0]
        m = float(ws @ Xs[:, i])
        out["value"] = {"cond_pmf": float(ws[Xs[:, i] == x[i]].sum()), "cond_mean": m,
                        "cond_var": float(ws @ Xs[:, i] ** 2) - m * m}[qty]
    else:
        return {"error": f"unknown multivariate quantity {qty}"}
    return out


# ---------------------------------------------------------------- closed forms (a book's own definitions)

CLOSED_ENV = {}


def _cenv():
    import mpmath as mp
    if not CLOSED_ENV:
        mp.mp.dps = 30
        CLOSED_ENV.update({n: getattr(mp, n) for n in (
            "exp", "log", "sqrt", "sin", "cos", "tan", "asin", "acos", "atan", "sinh", "cosh", "tanh",
            "asinh", "acosh", "atanh", "gamma", "loggamma", "beta", "erf", "erfc", "besseli", "besselk",
            "besselj", "hyp1f1", "hyp2f1", "hyper", "e1", "ei", "expint", "binomial", "factorial",
            "zeta", "polylog", "pi", "e", "inf", "psi", "betainc", "gammainc")})
        CLOSED_ENV.update(mp=mp, max=max, min=min, abs=abs, Gamma=mp.gamma, ln=mp.log)
    return CLOSED_ENV


FLOAT_ENV = {"exp": np.exp, "log": np.log, "ln": np.log, "sqrt": np.sqrt, "sin": np.sin, "cos": np.cos,
             "tan": np.tan, "asin": np.arcsin, "acos": np.arccos, "atan": np.arctan, "sinh": np.sinh,
             "cosh": np.cosh, "tanh": np.tanh, "asinh": np.arcsinh, "acosh": np.arccosh, "atanh": np.arctanh,
             "gamma": special.gamma, "Gamma": special.gamma, "loggamma": special.gammaln, "beta": special.beta,
             "erf": special.erf, "erfc": special.erfc, "besseli": special.iv, "besselk": special.kv,
             "besselj": special.jv, "hyp1f1": special.hyp1f1, "hyp2f1": special.hyp2f1, "e1": special.exp1,
             "ei": special.expi, "binomial": special.binom, "factorial": lambda v: special.gamma(v + 1),
             "psi": special.digamma, "pi": math.pi, "e": math.e, "inf": math.inf, "max": max, "min": min,
             "abs": abs}


def _cfun(src, names, args, fast=False):
    """A function of the named variables from a Python expression in them and the args (mpmath,
    or floats with SciPy's special functions when ``fast``: for quadrature)."""
    code = compile(src, "<closed form>", "eval")
    env = {**(FLOAT_ENV if fast else _cenv()), **args}
    return lambda *v: eval(code, {"__builtins__": {}}, {**env, **dict(zip(names, v))})  # noqa: S307


def _closed_uni(q):
    """A univariate family from its pdf (or pmf) and support, Python expressions in x and the args;
    every quantity by quadrature (or an exact sum) of that density. A quantity given as its own
    expression is evaluated as it stands."""
    ex, a = q["expr"], q.get("args", {})
    qty, x, r = q["qty"], q.get("x"), q.get("r")
    out = {"value": None, "imag": None, "tol_floor": 0.0,
           "how": f"closed form from the book's definition (internal consistency): {qty}"}
    if qty in ex and qty not in ("pdf", "pmf", "support"):
        v = complex(_cfun(ex[qty], ["x", "r"], a)(x, r))
        out["value"], out["imag"] = v.real, v.imag or None
        return out
    lo, hi = (float(eval(str(b), {"__builtins__": {}}, {**FLOAT_ENV, **a})) for b in ex["support"])  # noqa: S307
    discrete = "pmf" in ex
    f = _cfun(ex["pmf" if discrete else "pdf"], ["x"], a, fast=True)
    err = [0.0]
    brk = [float(eval(str(b), {"__builtins__": {}}, {**FLOAT_ENV, **a})) for b in ex.get("breaks", [])]  # noqa: S307

    def integral(g, u=lo, w=hi):
        u, w = max(u, lo), min(w, hi)          # the density is 0 outside its support
        if u >= w:
            return 0.0
        if discrete:
            top = w if math.isfinite(w) else u + 10 ** 5
            return float(sum(g(k) * f(k) for k in range(int(math.ceil(u)), int(math.floor(top)) + 1)))
        pts = [b for b in brk if u < b < w] or None
        if pts and (math.isinf(u) or math.isinf(w)):
            pts = None
        v, e = integrate.quad(lambda y: g(y) * f(y), u, w, points=pts, epsabs=1e-14, epsrel=1e-12, limit=500)
        err[0] = max(err[0], abs(e) / max(abs(v), 1e-300))
        return v
    one = lambda y: 1.0                                                                # noqa: E731
    raw = lambda k: integral(lambda y: y ** k)                                         # noqa: E731
    cdf = lambda v: integral(one, lo, v)                                               # noqa: E731
    if qty in ("pdf", "pmf"):
        v = float(f(x)) if lo <= x <= hi else 0.0
    elif qty == "cdf":
        v = cdf(x)
    elif qty == "sf":
        v = integral(one, x + 1 if discrete else x, hi) if not discrete else 1 - cdf(x)
    elif qty == "hazard":
        v = float(f(x)) / (1 - cdf(x - 1 if discrete else x))
    elif qty == "cum_hazard":
        v = -math.log(1 - cdf(x))
    elif qty in ("quantile", "median", "isf"):
        pr = 0.5 if qty == "median" else (1 - x if qty == "isf" else x)
        a_, b_ = (lo if math.isfinite(lo) else -1e6), (hi if math.isfinite(hi) else 1e6)
        a_, b_ = a_ + 1e-13 * (b_ - a_), b_ - 1e-13 * (b_ - a_)   # never evaluate a pole at an end
        v = optimize.brentq(lambda y: cdf(y) - pr, a_, b_, xtol=1e-14, rtol=1e-13)
    elif qty == "mode":
        a_, b_ = (lo if math.isfinite(lo) else -1e3), (hi if math.isfinite(hi) else 1e3)
        grid = np.linspace(a_, b_, 4001)[1:-1]
        k = int(np.argmax([f(g) for g in grid]))
        res = optimize.minimize_scalar(lambda y: -f(y), bounds=(grid[max(k - 1, 0)], grid[min(k + 1, len(grid) - 1)]),
                                       method="bounded", options={"xatol": 1e-12})
        v, out["tol_floor"] = float(res.x), 1e-8
    elif qty in ("mean", "var", "sd", "cv", "skew", "kurt", "excess_kurt", "raw_moment", "central_moment",
                 "cumulant"):
        n = int(r) if r is not None else 4
        m = [1.0] + [raw(k) for k in range(1, max(n, 4) + 1)]
        mu, var = m[1], m[2] - m[1] ** 2
        cen = lambda k: sum(math.comb(k, j) * (-mu) ** (k - j) * m[j] for j in range(k + 1))  # noqa: E731
        if qty == "raw_moment":
            v = m[n]
        elif qty == "central_moment":
            v = cen(n)
        elif qty == "cumulant":
            kap = [0.0] * (n + 1)
            for t in range(1, n + 1):
                kap[t] = m[t] - sum(math.comb(t - 1, u - 1) * kap[u] * m[t - u] for u in range(1, t))
            v = kap[n]
        else:
            v = {"mean": mu, "var": var, "sd": math.sqrt(var), "cv": math.sqrt(var) / mu,
                 "skew": cen(3) / var ** 1.5, "kurt": cen(4) / var ** 2,
                 "excess_kurt": cen(4) / var ** 2 - 3}[qty]
    elif qty in ("mgf", "cgf", "laplace", "pgf"):
        g = {"mgf": lambda y: math.exp(x * y), "cgf": lambda y: math.exp(x * y),
             "laplace": lambda y: math.exp(-x * y), "pgf": lambda y: x ** y}[qty]
        v = integral(g)
        v = math.log(v) if qty == "cgf" else v
    elif qty in ("cf", "log_cf"):
        z = complex(integral(lambda y: math.cos(x * y)), integral(lambda y: math.sin(x * y)))
        z = complex(math.log(abs(z)), math.atan2(z.imag, z.real)) if qty == "log_cf" else z
        out["value"], out["imag"], out["tol_floor"] = z.real, z.imag, err[0]
        return out
    elif qty == "entropy":
        v = -integral(lambda y: math.log(f(y)) if f(y) > 0 else 0.0)
    else:
        return {"error": f"no closed-form {qty}"}
    out["value"], out["tol_floor"] = float(v), max(out["tol_floor"], err[0])
    return out


def _closed_mv(q):
    """A bivariate family from its joint survival function on [0, inf)^2 (``sf``: singular parts
    allowed) or its joint pdf on a box (``pdf`` and ``support`` [[lo1, hi1], [lo2, hi2]]); a
    quantity given as its own expression is evaluated as it stands. mpmath quadrature and
    differentiation at 30 digits; the quadrature error estimate is the tolerance floor."""
    import mpmath as mp
    ex, a, qty = q["expr"], q.get("args", {}), q["qty"]
    x, t, idx, r = q.get("x") or [0, 0], q.get("t") or [0, 0], q.get("index") or [], q.get("r")
    out = {"value": None, "imag": None, "tol_floor": 0.0,
           "how": f"closed form from the book's definition (internal consistency): {qty}"}
    if qty in ex:
        f = _cfun(ex[qty], ["x_1", "x_2", "t_1", "t_2", "i", "j", "r_1", "r_2"], a)
        rr = list(r or [0, 0]) + [0, 0]
        v = complex(f(x[0], x[1], t[0], t[1], *(list(idx) + [0, 0])[:2], rr[0], rr[1]))
        out["value"], out["imag"] = v.real, v.imag or None
        return out
    err = [mp.mpf(0)]

    def quad(g, *ranges):      # SciPy quadrature (fast); the integrand in mpmath
        f = lambda *v: complex(g(*v)).real                                           # noqa: E731
        rg = [[float(lo), float(hi)] for lo, hi in ranges]
        if len(rg) == 1:
            v, e = integrate.quad(f, *rg[0], epsabs=1e-14, epsrel=1e-12, limit=200)
        else:
            v, e = integrate.nquad(f, rg, opts={"epsabs": 1e-14, "epsrel": 1e-11, "limit": 100})
        err[0] = max(err[0], abs(e) / max(abs(v), 1e-300))
        return mp.mpf(v)
    if "sf" in ex:
        S = _cfun(ex["sf"], ["x_1", "x_2"], a)
        Sf = _cfun(ex["sf"], ["x_1", "x_2"], a, fast=True)
        box = [[mp.mpmathify(eval(str(s), {"__builtins__": {}}, {**_cenv(), **a})) for s in rg]  # noqa: S307
               for rg in ex.get("support", [[0, "inf"], [0, "inf"]])]
        dens = lambda u, v: mp.diff(S, (u, v), (1, 1))                           # noqa: E731
        L1, L2 = box[0][0], box[1][0]                                              # the marginals: the
        m1 = lambda u: -mp.diff(lambda w: S(w, L2), u)                             # noqa: E731 other at
        m2 = lambda v: -mp.diff(lambda w: S(L1, w), v)                             # noqa: E731 its bottom

    else:
        P = _cfun(ex["pdf"], ["x_1", "x_2"], a, fast=True)
        box = [[mp.mpmathify(eval(str(s), {"__builtins__": {}}, {**_cenv(), **a})) for s in rg]  # noqa: S307
               for rg in ex["support"]]
        dens = lambda u, v: P(float(u), float(v))                                  # noqa: E731
        m1 = lambda u: quad(lambda v: P(u, v), box[1])                             # noqa: E731
        m2 = lambda v: quad(lambda u: P(u, v), box[0])                             # noqa: E731
        S = lambda u, v: quad(lambda s, w: P(s, w), [max(u, box[0][0]), box[0][1]],   # noqa: E731
                              [max(v, box[1][0]), box[1][1]])

    def moment(r1, r2):    # E[X1^r1 X2^r2]
        if "sf" in ex and mp.isfinite(box[0][0]) and mp.isfinite(box[1][0]):
            # bounded below: Y = X - L on [0, inf)^2 through the survival function (singular parts
            # included), then the binomial expansion of (Y1 + L1)^r1 (Y2 + L2)^r2
            l1, l2 = float(box[0][0]), float(box[1][0])
            Sy = lambda u, v: Sf(l1 + u, l2 + v)                                   # noqa: E731
            up = [[0, mp.inf], [0, mp.inf]]

            def ym(a1, a2):
                if a1 and a2:
                    return a1 * a2 * quad(lambda u, v: u ** (a1 - 1) * v ** (a2 - 1) * Sy(u, v), *up)
                if a1:
                    return a1 * quad(lambda u: u ** (a1 - 1) * Sy(u, 0), up[0])
                if a2:
                    return a2 * quad(lambda v: v ** (a2 - 1) * Sy(0, v), up[1])
                return mp.mpf(1)
            return sum(math.comb(r1, i) * math.comb(r2, j) * mp.mpf(l1) ** (r1 - i) * mp.mpf(l2) ** (r2 - j)
                       * ym(i, j) for i in range(r1 + 1) for j in range(r2 + 1))
        return quad(lambda u, v: u ** r1 * v ** r2 * dens(u, v), *box)
    X1, X2 = mp.mpmathify(x[0]), mp.mpmathify(x[1])
    if qty == "sf":
        v = S(X1, X2)
    elif qty == "cdf":
        v = 1 - S(X1, box[1][0]) - S(box[0][0], X2) + S(X1, X2)
    elif qty == "pdf":
        v = dens(X1, X2)
    elif qty == "marginal_pdf":
        v = m1(X1) if idx[0] == 0 else m2(X2)
    elif qty in ("mean", "var", "cov", "corr", "mixed_moment"):
        if qty == "mixed_moment":
            v = moment(*r)
        else:
            i = idx[0]
            j = idx[1] if len(idx) > 1 else i
            e1 = [moment(1, 0), moment(0, 1)]
            e2 = [moment(2, 0), moment(0, 2)]
            c = moment(1, 1) - e1[0] * e1[1] if i != j else e2[i] - e1[i] ** 2
            if qty == "mean":
                v = e1[i]
            elif qty == "var":
                v = e2[i] - e1[i] ** 2
            elif qty == "cov":
                v = c
            else:
                v = c / mp.sqrt((e2[0] - e1[0] ** 2) * (e2[1] - e1[1] ** 2))
    elif qty in ("cond_pdf", "cond_mean", "cond_var", "cond_moment"):
        i = idx[0]                         # the variable (0 or 1); the other component is given
        if i == 1:
            f, at = (lambda w: dens(X1, w)), X2
        else:
            f, at = (lambda w: dens(w, X2)), X1
        rng = box[i]
        if qty == "cond_pdf":              # f(x1, x2) / f1(x1): the marginal (a singular part counts)
            v = f(at) / (m1(X1) if i == 1 else m2(X2))
        else:
            norm = quad(f, rng)
        if qty != "cond_pdf":
            mom = lambda s: quad(lambda w: w ** s * f(w), rng) / norm               # noqa: E731
            if qty == "cond_mean":
                v = mom(1)
            elif qty == "cond_var":
                v = mom(2) - mom(1) ** 2
            else:
                v = mom(int(r[0] if isinstance(r, list) else r))
    elif qty == "mgf":
        T1, T2 = mp.mpmathify(t[0]), mp.mpmathify(t[1])
        if "sf" in ex and box[0][0] == 0 and box[1][0] == 0:   # 1 + t1 I1 + t2 I2 + t1 t2 I12 (support [0, inf)^2)
            t1, t2 = float(T1), float(T2)
            es = lambda e, sv: 0.0 if sv <= 0 else math.exp(e + math.log(sv))   # noqa: E731 - no overflow
            v = (1 + T1 * quad(lambda u: es(t1 * u, Sf(u, 0)), box[0])
                 + T2 * quad(lambda w: es(t2 * w, Sf(0, w)), box[1])
                 + T1 * T2 * quad(lambda u, w: es(t1 * u + t2 * w, Sf(u, w)), *box))
        else:
            t1, t2 = float(T1), float(T2)
            v = quad(lambda u, w: math.exp(t1 * u + t2 * w) * dens(u, w), *box)
    else:
        return {"error": f"no closed-form {qty}"}
    z = complex(v)
    out["value"], out["imag"] = z.real, z.imag or None
    out["tol_floor"] = float(err[0])
    return out


def answer(q):
    if q["dist"] == "closed_form":
        return _closed_mv(q) if q.get("multivariate") else _closed_uni(q)
    if q.get("multivariate"):
        return answer_mv(q)
    d = _dist(q["dist"], q.get("args", {}))
    discrete = hasattr(d.dist, "pmf")
    qty, x, r = q["qty"], q.get("x"), q.get("r")
    out = {"value": None, "imag": None, "tol_floor": 0.0, "how": f"scipy {q['dist']}.{qty}"}
    if qty in ("pdf", "pmf"):
        out["value"] = float(d.pmf(x) if discrete else d.pdf(x))
    elif qty == "cdf":
        out["value"] = float(d.cdf(x))
    elif qty == "sf":
        out["value"] = float(d.sf(x))
    elif qty == "hazard":                       # discrete: p(x) / Pr[X >= x]
        out["value"] = float(d.pmf(x) / d.sf(x - 1) if discrete else d.pdf(x) / d.sf(x))
    elif qty == "quantile":
        out["value"] = float(d.ppf(x))
    elif qty == "isf":                           # inverse survival: the x with Pr[X > x] = alpha
        out["value"] = float(d.isf(x))
    elif qty == "cum_hazard":                    # -log S(x)
        out["value"] = float(-d.logsf(x))
    elif qty == "laplace":                       # E[e^{-sX}]
        q2 = dict(q, qty="mgf", x=-x)
        out = answer(q2)
        out["how"] = "Laplace transform: " + out.get("how", "")
        return out
    elif qty == "log_cf":                        # log of the characteristic function
        c = answer(dict(q, qty="cf"))
        if "error" in c:
            return c
        z = complex(c["value"], c["imag"] or 0.0)
        lz = complex(math.log(abs(z)), math.atan2(z.imag, z.real))
        c["value"], c["imag"], c["how"] = lz.real, lz.imag, "log of " + c["how"]
        return c
    elif qty == "mean":
        out["value"] = float(d.mean())
    elif qty == "var":
        out["value"] = float(d.var())
    elif qty == "sd":
        out["value"] = float(d.std())
    elif qty == "cv":                            # coefficient of variation
        out["value"] = float(d.std() / d.mean())
    elif qty == "skew":
        out["value"] = float(d.stats(moments="s"))
    elif qty == "kurt":                          # beta_2 = excess + 3
        out["value"] = float(d.stats(moments="k")) + 3.0
    elif qty == "excess_kurt":
        out["value"] = float(d.stats(moments="k"))
    elif qty == "raw_moment":
        out["value"] = _raw(d, r)
    elif qty == "central_moment":               # sum C(r,k) (-mu)^(r-k) mu'_k
        m = float(d.mean())
        r = int(r)
        out["value"] = float(sum(math.comb(r, k) * (-m) ** (r - k) * (_raw(d, k) if k else 1.0)
                                 for k in range(r + 1)))
        out["how"] = "scipy raw moments, binomial expansion"
    elif qty == "cumulant":
        r = int(r)
        mom = [1.0] + [_raw(d, k) for k in range(1, r + 1)]
        kap = [0.0] * (r + 1)
        for n in range(1, r + 1):
            kap[n] = mom[n] - sum(math.comb(n - 1, m - 1) * kap[m] * mom[n - m] for m in range(1, n))
        out["value"] = float(kap[r])
        out["how"] = "scipy raw moments, cumulant recursion"
    elif qty in ("mgf", "cgf"):              # e^{ty + log f(y)}: no overflow far out
        if discrete:
            v, err = _discrete_sum(lambda k: np.exp(x * k + d.logpmf(k)), d)
        else:
            v, err = _quad(lambda y: float(np.exp(x * y + d.logpdf(y))), d)
        out["value"] = v if qty == "mgf" else math.log(v)
        out["tol_floor"] = _rel(err, v)
        out["how"] = "quadrature of e^{ty} f(y)" if not discrete else "sum of e^{tk} p(k)"
    elif qty == "cf" and not discrete and x != 0 and math.isinf(_support(d)[1]):
        # QAWF (quad with a cos/sin weight) on (0, inf): heavy tails converge
        lo = _support(d)[0]
        if math.isinf(lo):           # fold: cos even, sin odd
            re_, e1 = integrate.quad(lambda y: d.pdf(y) + d.pdf(-y), 0, np.inf, weight="cos",
                                     wvar=x, limlst=200)
            im_, e2 = integrate.quad(lambda y: d.pdf(y) - d.pdf(-y), 0, np.inf, weight="sin",
                                     wvar=x, limlst=200)
        else:                        # (lo, inf): shift to (0, inf)
            c, s = math.cos(x * lo), math.sin(x * lo)
            a, e1 = integrate.quad(lambda y: d.pdf(y + lo), 0, np.inf, weight="cos", wvar=x,
                                   limlst=200)
            b, e2 = integrate.quad(lambda y: d.pdf(y + lo), 0, np.inf, weight="sin", wvar=x,
                                   limlst=200)
            re_, im_ = a * c - b * s, a * s + b * c
        out["value"], out["imag"] = re_, im_
        out["tol_floor"] = _rel(math.hypot(e1, e2), math.hypot(re_, im_))
        out["how"] = "Fourier quadrature (QAWF) of f(y)"
    elif qty == "cf":
        re_, e1 = _expect(lambda y: np.cos(x * y), d, discrete)
        im_, e2 = _expect(lambda y: np.sin(x * y), d, discrete)
        out["value"], out["imag"] = re_, im_
        out["tol_floor"] = _rel(math.hypot(e1, e2), math.hypot(re_, im_))
        out["how"] = "quadrature of e^{ity} f(y)" if not discrete else "sum of e^{itk} p(k)"
    elif qty == "pgf":
        v, err = _expect(lambda k: np.power(float(x), k), d, discrete)
        out["value"], out["tol_floor"] = v, _rel(err, v)
        out["how"] = "sum of z^k p(k)"
    elif qty == "entropy":
        out["value"] = float(d.entropy())
    elif qty == "median":
        out["value"] = float(d.median())
    elif qty == "mode":
        lo, hi = float(d.ppf(1e-6)), float(d.ppf(1 - 1e-6))
        if discrete:
            ks = np.arange(int(lo), int(hi) + 1)
            out["value"] = float(ks[int(np.argmax(d.pmf(ks)))])
        else:
            grid = np.linspace(lo, hi, 4001)
            i = int(np.argmax(d.pdf(grid)))
            a, b = grid[max(i - 1, 0)], grid[min(i + 1, len(grid) - 1)]
            res = optimize.minimize_scalar(lambda y: -d.pdf(y), bounds=(a, b), method="bounded",
                                           options={"xatol": 1e-12})
            out["value"] = float(res.x)
            out["tol_floor"] = 1e-8              # an argmax is good to about sqrt(eps)
        out["how"] = "argmax of the density (numerical; about sqrt(machine eps))"
    else:
        return {"error": f"unknown quantity {qty}"}
    if out["value"] is None or (isinstance(out["value"], float) and math.isnan(out["value"])):
        return {"error": f"SciPy gives no {qty} here"}
    return out


def main():
    for line in sys.stdin:
        if not line.strip():
            continue
        q = {}
        try:
            q = json.loads(line)
            res = answer(q)
        except Exception as e:  # noqa: BLE001 - every failure is an answer, never a crash
            res = {"error": f"{type(e).__name__}: {e}"}
        if isinstance(q, dict) and "id" in q:
            res["id"] = q["id"]
        sys.stdout.write(json.dumps(res) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
