# SPDX-License-Identifier: AGPL-3.0-or-later
"""A stand-in for crosscheck_ref.py without SciPy: the exponential distribution (scale) and
the normal (loc, scale) in closed form. FAKE_REF_SLEEP=SECONDS makes it hang on "hang"."""
import json
import math
import os
import sys
import time


def _comps(n, k):
    if k == 1:
        yield (n,)
        return
    for a in range(n + 1):
        for rest in _comps(n - a, k - 1):
            yield (a,) + rest


def _det2(m):
    return m[0][0] * m[1][1] - m[0][1] * m[1][0]


def matrix_variate(q):
    """Step 2 in closed form: the Dirichlet (math.gamma), the 2-D normal's whole mean and
    covariance, and the p = 2 Wishart (density, mean, element covariance, mgf)."""
    a, qty, x, idx = q["args"], q["qty"], q.get("x"), q.get("index") or []
    out = {"value": None, "imag": None, "tol_floor": 0.0, "how": "fake " + q["dist"]}
    if q["dist"] == "multivariate_normal":
        m = [[v] for v in a["mean"]] if qty == "mean" else a["cov"] if qty == "cov" else None
        if m is None:
            return {"error": f"no whole {qty}"}
        out["matrix"] = m
        return out
    if q["dist"] == "dirichlet":
        al = a["alpha"]
        a0 = sum(al)
        if qty == "pdf":
            xs = list(x) + ([1 - sum(x)] if len(x) < len(al) else [])
            out["value"] = math.gamma(a0) / math.prod(math.gamma(v) for v in al) * math.prod(
                xi ** (v - 1) for xi, v in zip(xs, al))
        elif qty == "mean":
            out["value"] = al[idx[0]] / a0
        elif qty == "cov":
            i, j = idx
            out["value"] = ((i == j) * al[i] * a0 - al[i] * al[j]) / (a0 * a0 * (a0 + 1))
        elif qty == "mixed_moment":
            r = q["r"]
            out["value"] = math.gamma(a0) / math.gamma(a0 + sum(r)) * math.prod(
                math.gamma(v + ri) / math.gamma(v) for v, ri in zip(al, r))
        else:
            return {"error": f"no {qty}"}
        return out
    n, S = a["df"], a["scale"]                    # wishart, p = 2
    if qty == "mean" and q.get("whole"):
        out["matrix"] = [[n * v for v in row] for row in S]
    elif qty == "pdf":
        dS, dW = _det2(S), _det2(x)
        inv = [[S[1][1] / dS, -S[0][1] / dS], [-S[1][0] / dS, S[0][0] / dS]]
        tr = sum(inv[i][k] * x[k][i] for i in range(2) for k in range(2))
        g2 = math.sqrt(math.pi) * math.gamma(n / 2) * math.gamma(n / 2 - 0.5)
        out["value"] = dW ** ((n - 3) / 2) * math.exp(-tr / 2) / (2 ** n * g2 * dS ** (n / 2))
    elif qty == "cov":
        (i, j), (k, m) = idx
        out["value"] = n * (S[i][k] * S[j][m] + S[i][m] * S[j][k])
    elif qty == "mgf":
        T = q["t"]
        TS = [[sum(T[i][k] * S[k][j] for k in range(2)) for j in range(2)] for i in range(2)]
        out["value"] = _det2([[1 - 2 * TS[0][0], -2 * TS[0][1]], [-2 * TS[1][0], 1 - 2 * TS[1][1]]]) ** (-n / 2)
    else:
        return {"error": f"no {qty}"}
    return out


def multivariate(q):
    """The multinomial (n, p) by sums over its support, and the bivariate normal."""
    a, qty, x, idx, given = q["args"], q["qty"], q.get("x"), q.get("index") or [], q.get("given") or []
    if q["dist"] in ("dirichlet", "wishart") or q.get("whole"):
        return matrix_variate(q)
    if q["dist"] == "multivariate_normal":
        (m1, m2), ((s11, s12), (_, s22)) = a["mean"], a["cov"]
        if qty == "pdf":
            r = s12 / math.sqrt(s11 * s22)
            z1, z2 = (x[0] - m1) / math.sqrt(s11), (x[1] - m2) / math.sqrt(s22)
            v = math.exp(-(z1 * z1 - 2 * r * z1 * z2 + z2 * z2) / (2 * (1 - r * r))) / (
                2 * math.pi * math.sqrt(s11 * s22 * (1 - r * r)))
        elif qty == "cond_mean":
            v = m1 + s12 / s22 * (x[1] - m2)
        else:
            return {"error": f"no {qty}"}
        return {"value": v, "imag": None, "tol_floor": 0.0, "how": "fake mvn"}
    if q["dist"] == "negative_multinomial":       # pmf and marginals in closed form
        n, p = a["n"], a["p"]
        p0 = 1 - sum(p)
        if qty == "pmf":
            v = math.exp(math.lgamma(n + sum(x)) - math.lgamma(n) - sum(math.lgamma(y + 1) for y in x)
                         + n * math.log(p0) + sum(y * math.log(pi) for y, pi in zip(x, p)))
        elif qty == "marginal_pmf":
            i, y = idx[0], x[idx[0]]
            s = p0 / (p0 + p[i])
            v = math.exp(math.lgamma(n + y) - math.lgamma(n) - math.lgamma(y + 1)) * s ** n * (1 - s) ** y
        else:
            return {"error": f"no {qty}"}
        return {"value": v, "imag": None, "tol_floor": 0.0, "how": "fake negative multinomial"}
    n, p = a["n"], a["p"]

    def pmf(y):
        return math.factorial(n) * math.prod(pi ** yi / math.factorial(yi) for pi, yi in zip(p, y))
    S = [(y, pmf(y)) for y in _comps(n, len(p))]

    def E(f, sel=lambda y: True):
        tot = sum(w for y, w in S if sel(y))
        return sum(f(y) * w for y, w in S if sel(y)) / tot
    i = idx[0] if idx else 0
    j = idx[1] if len(idx) > 1 else i
    cov = E(lambda y: y[i] * y[j]) - E(lambda y: y[i]) * E(lambda y: y[j])
    var = lambda k: E(lambda y: y[k] ** 2) - E(lambda y: y[k]) ** 2  # noqa: E731
    cond = lambda y: all(y[g] == x[g] for g in given)  # noqa: E731

    def falling(y):
        out = 1
        for yi, ri in zip(y, q.get("r") or []):
            out *= math.prod(yi - m for m in range(ri))
        return out
    table = {"pmf": lambda: pmf(x), "marginal_pmf": lambda: E(lambda y: float(y[i] == x[i])),
             "mean": lambda: E(lambda y: y[i]), "var": lambda: var(i), "cov": lambda: cov,
             "corr": lambda: cov / math.sqrt(var(i) * var(j)), "fact_moment": lambda: E(falling),
             "pgf": lambda: E(lambda y: math.prod(t ** yi for t, yi in zip(q["t"], y))),
             "cond_pmf": lambda: E(lambda y: float(y[i] == x[i]), cond),
             "cond_mean": lambda: E(lambda y: y[i], cond),
             "cond_var": lambda: E(lambda y: y[i] ** 2, cond) - E(lambda y: y[i], cond) ** 2}
    return {"value": table[qty](), "imag": None, "tol_floor": 0.0, "how": "fake multinomial"}


def answer(q):
    if q.get("multivariate"):
        return multivariate(q)
    d, a, qty, x, r = q["dist"], q.get("args", {}), q["qty"], q.get("x"), q.get("r")
    if d == "hang":
        time.sleep(float(os.environ.get("FAKE_REF_SLEEP", "60")))
    if d == "expon":
        s = a["scale"]
        neg = x is not None and qty in ("pdf", "cdf", "sf") and x < 0     # below the support
        table = {"pdf": lambda: 0.0 if neg else math.exp(-x / s) / s,
                 "cdf": lambda: 0.0 if neg else 1 - math.exp(-x / s),
                 "sf": lambda: 1.0 if neg else math.exp(-x / s), "mean": lambda: s, "var": lambda: s * s,
                 "mgf": lambda: 1 / (1 - s * x), "raw_moment": lambda: math.factorial(r) * s ** r,
                 "kurt": lambda: 9.0, "skew": lambda: 2.0, "cv": lambda: 1.0}
    elif d == "branch":         # a log cf answered on another branch of the complex log
        if qty != "log_cf":
            return {"error": "only log_cf"}
        return {"value": -0.5 * x * x, "imag": x + 2 * math.pi, "tol_floor": 0.0, "how": "fake"}
    elif d == "norm":
        m, s = a.get("loc", 0.0), a["scale"]
        table = {"pdf": lambda: math.exp(-((x - m) / s) ** 2 / 2) / (s * math.sqrt(2 * math.pi)),
                 "mean": lambda: m, "var": lambda: s * s}
    else:
        return {"error": f"no {d}"}
    if qty not in table:
        return {"error": f"no {qty}"}
    return {"value": table[qty](), "imag": None, "tol_floor": 0.0, "how": f"fake {d}.{qty}"}


for line in sys.stdin:
    if line.strip():
        try:
            out = answer(json.loads(line))
        except Exception as e:  # noqa: BLE001
            out = {"error": str(e)}
        sys.stdout.write(json.dumps(out) + "\n")
        sys.stdout.flush()
