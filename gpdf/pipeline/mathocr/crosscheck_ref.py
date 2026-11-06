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


def answer(q):
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
