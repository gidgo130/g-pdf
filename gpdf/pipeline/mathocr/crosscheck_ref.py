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


def _el(arr, i):
    return float(arr[tuple(i)] if isinstance(i, (list, tuple)) else arr[i])


def _mat(q):
    name, qty = q["dist"], q["qty"]
    a = _np(q.get("args", {}))
    x, idx = q.get("x"), q.get("index") or []
    d, mean, mode, cov = _family(name, a)
    out = {"value": None, "imag": None, "tol_floor": 0.0, "how": f"{name} {qty} (scipy / closed form)"}
    vec = name in VECTOR_FAMILIES
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


def answer(q):
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
