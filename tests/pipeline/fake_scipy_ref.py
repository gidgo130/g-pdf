# SPDX-License-Identifier: AGPL-3.0-or-later
"""A stand-in for crosscheck_ref.py without SciPy: the exponential distribution (scale) and
the normal (loc, scale) in closed form. FAKE_REF_SLEEP=SECONDS makes it hang on "hang"."""
import json
import math
import os
import sys
import time


def answer(q):
    d, a, qty, x, r = q["dist"], q.get("args", {}), q["qty"], q.get("x"), q.get("r")
    if d == "hang":
        time.sleep(float(os.environ.get("FAKE_REF_SLEEP", "60")))
    if d == "expon":
        s = a["scale"]
        table = {"pdf": lambda: math.exp(-x / s) / s, "cdf": lambda: 1 - math.exp(-x / s),
                 "sf": lambda: math.exp(-x / s), "mean": lambda: s, "var": lambda: s * s,
                 "mgf": lambda: 1 / (1 - s * x), "raw_moment": lambda: math.factorial(r) * s ** r,
                 "kurt": lambda: 9.0, "skew": lambda: 2.0}
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
