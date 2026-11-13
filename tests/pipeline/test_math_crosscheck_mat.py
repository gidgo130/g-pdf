# SPDX-License-Identifier: AGPL-3.0-or-later
"""gpdf-cli math-crosscheck, multivariate step 2 (matrix variate), on hand-written formulas of our
own: the matrix pass (transpose, inverse, determinant bars against an absolute value, trace,
etr, square roots, the identity, Kronecker products, Gamma_p) and one planted error per
quantity and distribution (the normal in matrix form, the p = 2 Wishart, the Dirichlet) against
the fake reference, and the real SciPy worker for every SciPy family when GPDF_SCIPY_PYTHON is
set."""

import json
import math
import os
import sys
from pathlib import Path

import pytest

pytest.importorskip("sympy")
pytest.importorskip("antlr4")

from gpdf.pipeline.mathocr import crosscheck as C  # noqa: E402
from gpdf.pipeline.mathocr import crosscheck_mat as MX  # noqa: E402
from gpdf.pipeline.mathocr import crosscheck_mv as MV  # noqa: E402

FAKE = Path(__file__).with_name("fake_scipy_ref.py")
ROW = {"matrix": True, "matrices": ["Sigma", "A", "B"], "vectors": ["a", "b", "mu"]}
VALS = {"Sigma": [[2.0, 0.3], [0.3, 1.0]], "A": [[1.0, 2.0], [0.5, -1.0]], "B": [[0.4, 0.0], [1.0, 3.0]],
        "a": [1.0, 2.0], "b": [3.0, 4.0], "p": 2, "x": 0.5}
DET_S = 2.0 * 1.0 - 0.3 * 0.3


def value(tex, vals=VALS, row=ROW):
    text, mat = MX.reduce(MV.expand(MX.gamma_p(tex), vals, None), row, vals)
    assert mat is None, "expected a number"
    v, why = C.evaluate(C.book_expr(MV.for_parser(text, vals)),
                        {k: v for k, v in vals.items() if not isinstance(v, list)})
    assert v is not None, (text, why)
    return v.real


def test_matrix_pass_products_transposes_inverses():
    assert value(r"\mathbf{a}' \mathbf{b}") == pytest.approx(11)
    assert value(r"\mathbf{a}^{T} \mathbf{A} \mathbf{b}") == pytest.approx(1 * (3 + 8) + 2 * (1.5 - 4))
    inv = [[1.0 / DET_S, -0.3 / DET_S], [-0.3 / DET_S, 2.0 / DET_S]]
    want = sum(VALS["a"][i] * inv[i][j] * VALS["a"][j] for i in range(2) for j in range(2))
    assert value(r"\exp\left\{-\frac{1}{2} \mathbf{a}' \boldsymbol{\Sigma}^{-1} \mathbf{a}\right\}") == \
        pytest.approx(math.exp(-want / 2))
    assert value(r"2 (\mathbf{a} - \mathbf{b})' (\mathbf{a} - \mathbf{b}) + 1") == pytest.approx(17)


def test_determinant_bars_and_absolute_values():
    assert value(r"|\boldsymbol{\Sigma}|^{-1/2}") == pytest.approx(DET_S ** -0.5)
    assert value(r"\det(\mathbf{A} \mathbf{B})") == pytest.approx(-2.0 * 1.2)
    text, _ = MX.reduce(r"|x - 2|", ROW, VALS)          # a scalar between bars: an absolute value
    assert "|" in text and MX.OPEN not in text
    assert value(r"|x - 2| + |\Sigma|") == pytest.approx(1.5 + DET_S)


def test_trace_etr_roots_identity_kronecker():
    assert value(r"\operatorname{tr}(\boldsymbol{\Sigma} \mathbf{A})") == pytest.approx(2 + 0.15 + 0.6 - 1)
    assert value(r"\operatorname{etr}\left(-\frac{1}{2} \Sigma\right)") == pytest.approx(math.exp(-1.5))
    assert value(r"|\Sigma^{1/2}|^{2}") == pytest.approx(DET_S)
    assert value(r"\mathbf{a}' \Sigma^{-1/2} \Sigma^{-1/2} \mathbf{a}") == pytest.approx(
        value(r"\mathbf{a}' \Sigma^{-1} \mathbf{a}"))
    assert value(r"|\mathbf{I}_p + \Sigma|") == pytest.approx(3 * 2 - 0.09)
    assert value(r"|I + \Sigma|") == pytest.approx(3 * 2 - 0.09)
    assert value(r"\operatorname{tr}(\mathbf{A} \otimes \mathbf{B})") == pytest.approx(0 * 3.4)
    assert value(r"|\mathbf{A} \otimes \mathbf{B}|") == pytest.approx((-2.0) ** 2 * 1.2 ** 2)


def test_gamma_p_and_errors():
    vals = {**VALS, "p": 3, "a": 2.5}
    want = math.pi ** 1.5 * math.gamma(2.5) * math.gamma(2.0) * math.gamma(1.5)
    assert value(r"\Gamma_p(a)", vals, {"matrix": True}) == pytest.approx(want)
    b = value(r"B_p(a, 3)", vals, {"matrix": True})
    g = lambda t: math.pi ** 1.5 * math.gamma(t) * math.gamma(t - 0.5) * math.gamma(t - 1)  # noqa: E731
    assert b == pytest.approx(g(2.5) * g(3) / g(5.5))
    for bad in (r"\mathbf{A} \mathbf{a} \mathbf{a}", r"\mathbf{A} + \mathbf{a}", r"\mathbf{A} / 2",
                r"\frac{1}{\mathbf{A}}", r"\operatorname{tr}(\mathbf{C})"):
        with pytest.raises(MX.MatrixError):
            MX.reduce(bad, ROW, VALS)
    _, m = MX.reduce(r"n \boldsymbol{\Sigma}", ROW, {**VALS, "n": 3})   # a whole matrix
    assert m is not None and float(m[0, 1]) == pytest.approx(0.9)


# ---------------------------------------------------------------- planted errors

SIG = [[2.0, 0.3], [0.3, 1.0]]
MVN = (r"(2 \pi)^{-p/2} |\boldsymbol{\Sigma}|^{-1/2} \exp\left\{ {S}\frac{1}{2} (\mathbf{x} - \boldsymbol{\mu})' "
       r"\boldsymbol{\Sigma}^{-1} (\mathbf{x} - \boldsymbol{\mu}) \right\}")
WIS = (r"\frac{|\mathbf{W}|^{(n - p {S} 1)/2} \operatorname{etr}\left(-\frac{1}{2} \boldsymbol{\Sigma}^{-1} "
       r"\mathbf{W}\right)}{2^{np/2} \Gamma_p\left(\frac{n}{2}\right) |\boldsymbol{\Sigma}|^{n/2}}")
DIR = (r"\frac{\Gamma(\alpha_1 + \alpha_2 + \alpha_3)}{\prod_{i=1}^{k} \Gamma(\alpha_i)} "
       r"\prod_{i=1}^{k} x_i^{\alpha_i {S} 1}")
# (id, dist, qty, right, planted, entry fields)
CASES = [
    ("mvn pdf", "mvn", "pdf", MVN.replace("{S}", "-"), MVN.replace("{S}", "+"), {}),
    ("mvn mean", "mvn", "mean", r"E(\mathbf{x}) = \boldsymbol{\mu}", r"E(\mathbf{x}) = 2 \boldsymbol{\mu}", {}),
    ("mvn cov", "mvn", "cov", r"\boldsymbol{\Sigma}", r"\boldsymbol{\Sigma}^{-1}", {}),
    ("wishart pdf", "wis", "pdf", WIS.replace("{S}", "-"), WIS.replace("{S}", "+"), {}),
    ("wishart mean", "wis", "mean", r"E(\mathbf{W}) = n \boldsymbol{\Sigma}", r"(n - 1) \boldsymbol{\Sigma}", {}),
    ("wishart cov", "wis", "cov", r"n (\sigma_{11} \sigma_{12} + \sigma_{12} \sigma_{11})",
     r"(\sigma_{11} \sigma_{12} + \sigma_{12} \sigma_{11})", {"index": [[1, 1], [1, 2]]}),
    ("wishart mgf", "wis", "mgf", r"|\mathbf{I}_p - 2 \boldsymbol{\Theta} \boldsymbol{\Sigma}|^{-n/2}",
     r"|\mathbf{I}_p + 2 \boldsymbol{\Theta} \boldsymbol{\Sigma}|^{-n/2}", {}),
    ("dirichlet pdf", "dir", "pdf", DIR.replace("{S}", "-"), DIR.replace("{S}", "+"), {}),
    ("dirichlet cov", "dir", "cov", r"-\frac{\alpha_i \alpha_j}{\alpha_0^2 (\alpha_0 + 1)}",
     r"-\frac{\alpha_i \alpha_j}{\alpha_0^2 (\alpha_0 - 1)}", {"index": [1, 3]}),
    ("dirichlet mixed moment", "dir", "mixed_moment",
     r"\frac{\Gamma(\alpha_0)}{\Gamma(\alpha_0 + r_1 + r_2 + r_3)} \prod_{i=1}^{k} \frac{\Gamma(\alpha_i + r_i)}{\Gamma(\alpha_i)}",
     r"\frac{\Gamma(\alpha_0)}{\Gamma(\alpha_0 + r_1 + r_2)} \prod_{i=1}^{k} \frac{\Gamma(\alpha_i + r_i)}{\Gamma(\alpha_i)}",
     {"r": [1, 0, 2]}),
]
DISTS = {
    "mvn": {"scipy": "multivariate_normal", "matrix": True, "matrices": ["Sigma"], "vectors": ["x", "mu"],
            "var": "x", "args": {"mean": "mu", "cov": "Sigma"},
            "points": [{"p": 2, "mu": [0.5, -1.0], "Sigma": SIG, "x": [1.0, 0.2]},
                       {"p": 2, "mu": [0.0, 1.0], "Sigma": [[1.0, -0.4], [-0.4, 0.8]], "x": [-0.5, 0.3]},
                       {"p": 2, "mu": [2.0, 0.0], "Sigma": [[0.5, 0.1], [0.1, 0.3]], "x": [1.5, 0.4]}]},
    "wis": {"scipy": "wishart", "matrix": True, "matrices": ["W", "Sigma", "Theta"], "var": "W",
            "args": {"df": "n", "scale": "Sigma"},
            "derived": {"sigma_11": "Sigma[0][0]", "sigma_12": "Sigma[0][1]"},
            "points": [{"p": 2, "n": 5, "Sigma": SIG, "W": [[3.0, 0.5], [0.5, 2.0]],
                        "Theta": [[0.05, 0.02], [0.02, -0.03]], "@t": "Theta"},
                       {"p": 2, "n": 7, "Sigma": [[1.0, -0.4], [-0.4, 0.8]], "W": [[6.0, -1.0], [-1.0, 4.0]],
                        "Theta": [[0.1, 0.0], [0.0, 0.1]], "@t": "Theta"},
                       {"p": 2, "n": 4.5, "Sigma": [[0.5, 0.1], [0.1, 0.3]], "W": [[1.2, 0.2], [0.2, 0.9]],
                        "Theta": [[-0.2, 0.1], [0.1, 0.05]], "@t": "Theta"}]},
    "dir": {"scipy": "dirichlet", "k": 3, "args": {"alpha": "[alpha_1, alpha_2, alpha_3]"},
            "derived": {"alpha_0": "alpha_1 + alpha_2 + alpha_3"},
            "points": [{"alpha_1": 1.5, "alpha_2": 2.5, "alpha_3": 3.0, "@x": [0.2, 0.3, 0.5]},
                       {"alpha_1": 0.8, "alpha_2": 1.2, "alpha_3": 2.0, "@x": [0.1, 0.6, 0.3]},
                       {"alpha_1": 4.0, "alpha_2": 2.0, "alpha_3": 1.5, "@x": [0.5, 0.25, 0.25]}]},
}


def run(tmp_path, cases, dists, python=None, script=None):
    md, entries = '<!-- page printed="1" file="1" -->\n\n', []
    for cid, dist, q, right, wrong, extra in cases:
        for ok, tex in (("ok", right), ("bad", wrong)):
            tag = f"7.{len(entries) + 1}"
            md += f"$$\n{tex}\n\\tag{{{tag}}}\n$$\n\n"
            entries.append({"id": f"{cid} {ok}", "where": {"tag": tag}, "qty": q, "dist": dist, **extra})
    (tmp_path / "text.md").write_text(md, "utf-8")
    (tmp_path / "spec.json").write_text(json.dumps({"pages": "text.md", "dists": dists, "entries": entries}), "utf-8")
    ref = C.RefWorker(python or sys.executable, script)
    try:
        return {r["id"]: r for r in C.run(tmp_path / "spec.json", tmp_path / "out", ref=ref)["results"]}
    finally:
        ref.close()


def test_one_planted_error_per_matrix_quantity(tmp_path):
    res = run(tmp_path, CASES, DISTS, script=FAKE)
    for r in res.values():
        want = "failed" if r["id"].endswith("bad") else "passed"
        assert r["status"] == want, (r["id"], r["status"], r.get("reason"), r.get("reduced"))
    assert len(res["wishart pdf ok"]["points"]) == 3


def test_an_unread_matrix_expression_is_inconclusive(tmp_path):
    cases = [("odd", "mvn", "pdf", r"\boldsymbol{\Sigma} / 2", r"\boldsymbol{\Sigma} / 3", {})]
    res = run(tmp_path, cases, DISTS, script=FAKE)
    assert res["odd ok"]["status"] == "inconclusive"
    assert res["odd ok"]["reason"].startswith("matrix expression not read")


REAL_DISTS = {
    "mvt": {"scipy": "multivariate_t", "matrix": True, "matrices": ["Sigma"], "vectors": ["x", "mu"], "var": "x",
            "args": {"loc": "mu", "shape": "Sigma", "df": "nu"},
            "points": [{"p": 2, "nu": 5, "mu": [0.5, -1.0], "Sigma": SIG, "x": [1.0, 0.2]},
                       {"p": 2, "nu": 3.5, "mu": [0.0, 1.0], "Sigma": [[1.0, -0.4], [-0.4, 0.8]], "x": [-0.5, 0.3]},
                       {"p": 2, "nu": 9, "mu": [2.0, 0.0], "Sigma": [[0.5, 0.1], [0.1, 0.3]], "x": [1.5, 0.4]}]},
    "iw": {"scipy": "invwishart", "matrix": True, "matrices": ["V", "Psi"], "var": "V",
           "args": {"df": "n", "scale": "Psi"},
           "points": [{"p": 2, "n": 6, "Psi": SIG, "V": [[0.6, 0.1], [0.1, 0.4]]},
                      {"p": 2, "n": 8, "Psi": [[1.0, -0.4], [-0.4, 0.8]], "V": [[0.3, -0.1], [-0.1, 0.2]]},
                      {"p": 2, "n": 5.5, "Psi": [[0.5, 0.1], [0.1, 0.3]], "V": [[0.2, 0.05], [0.05, 0.15]]}]},
    "mn": {"scipy": "matrix_normal", "matrix": True, "matrices": ["X", "M", "U", "V"], "var": "X",
           "args": {"mean": "M", "rowcov": "U", "colcov": "V"},
           "points": [{"M": [[1.0, 0.0], [0.5, 2.0]], "U": SIG, "V": [[1.0, 0.2], [0.2, 0.5]], "X": [[1.4, -0.3], [0.2, 2.5]]},
                      {"M": [[0.0, 1.0], [1.0, 0.0]], "U": [[1.0, -0.4], [-0.4, 0.8]], "V": [[0.7, 0.0], [0.0, 1.1]],
                       "X": [[0.5, 0.5], [0.8, -0.2]]},
                      {"M": [[2.0, 1.0], [0.0, 1.0]], "U": [[0.5, 0.1], [0.1, 0.3]], "V": [[2.0, 0.5], [0.5, 1.0]],
                       "X": [[1.0, 1.5], [0.3, 0.6]]}]},
    "vmf": {"scipy": "vonmises_fisher", "matrix": True, "vectors": ["x", "mu"], "var": "x",
            "args": {"mu": "mu", "kappa": "kappa"},
            "points": [{"p": 3, "kappa": 2.0, "mu": [0.0, 0.6, 0.8], "x": [0.48, 0.6, 0.64]},
                       {"p": 3, "kappa": 0.7, "mu": [1.0, 0.0, 0.0], "x": [0.0, 0.6, 0.8]},
                       {"p": 3, "kappa": 5.0, "mu": [0.0, 0.0, 1.0], "x": [0.6, 0.0, 0.8]}]},
    **{k: v for k, v in DISTS.items()},
}
MVT = (r"\frac{\Gamma\left(\frac{\nu + p}{2}\right)}{\Gamma\left(\frac{\nu}{2}\right) (\nu \pi)^{p/2} "
       r"|\boldsymbol{\Sigma}|^{1/2}} \left[1 + \frac{1}{\nu} (\mathbf{x} - \boldsymbol{\mu})' \boldsymbol{\Sigma}^{-1} "
       r"(\mathbf{x} - \boldsymbol{\mu})\right]^{-(\nu {S} p)/2}")
IW = (r"\frac{|\boldsymbol{\Psi}|^{n/2} |\mathbf{V}|^{-(n {S} p + 1)/2} \operatorname{etr}\left(-\frac{1}{2} "
      r"\boldsymbol{\Psi} \mathbf{V}^{-1}\right)}{2^{np/2} \Gamma_p\left(\frac{n}{2}\right)}")
MN = (r"(2 \pi)^{-2} |\mathbf{U}|^{-1} |\mathbf{V}|^{-1} \operatorname{etr}\left\{-\frac{1}{2} \mathbf{V}^{-1} "
      r"(\mathbf{X} - \mathbf{M})' \mathbf{U}^{-1} (\mathbf{X} {S} \mathbf{M})\right\}")
VMF = (r"\frac{\kappa^{p/2 - 1}}{(2 \pi)^{p/2} I_{p/2 - 1}(\kappa)} \exp(\kappa \boldsymbol{\mu}' \mathbf{x})")
REAL_CASES = CASES + [
    ("mvt pdf", "mvt", "pdf", MVT.replace("{S}", "+"), MVT.replace("{S}", "-"), {}),
    ("invwishart pdf", "iw", "pdf", IW.replace("{S}", "+"), IW.replace("{S}", "-"), {}),
    ("invwishart mean", "iw", "mean", r"\frac{\boldsymbol{\Psi}}{n - p - 1}", r"\frac{\boldsymbol{\Psi}}{n - p + 1}", {}),
    ("matrix normal pdf", "mn", "pdf", MN.replace("{S}", "-"), MN.replace("{S}", "+"), {}),
    ("matrix normal cov", "mn", "cov", r"u_{11} v_{12}", r"u_{12} v_{11}", {"index": [[1, 1], [1, 2]]}),
]


def test_real_scipy_matrix_families_when_configured(tmp_path):
    py = os.environ.get(C.ENV_VAR)
    if not py:
        pytest.skip("set GPDF_SCIPY_PYTHON to a Python with SciPy")
    dists = json.loads(json.dumps(REAL_DISTS))
    dists["mn"]["derived"] = {"u_11": "U[0][0]", "u_12": "U[0][1]", "v_11": "V[0][0]", "v_12": "V[0][1]"}
    res = run(tmp_path, REAL_CASES, dists, python=py)
    for r in res.values():
        want = "failed" if r["id"].endswith("bad") else "passed"
        assert r["status"] == want, (r["id"], r["status"], r.get("reason"), r.get("reduced"))
    # the von Mises-Fisher density needs the Bessel I: not in the scalar parser, so inconclusive
    vmf = run(tmp_path / "v" if (tmp_path / "v").mkdir() is None else None,
              [("vmf pdf", "vmf", "pdf", VMF, VMF.replace("\\kappa \\boldsymbol", "2 \\kappa \\boldsymbol"), {})],
              dists, python=py)
    assert vmf["vmf pdf ok"]["status"] in ("passed", "inconclusive")


# ---------------------------------------------------------------- closed forms from a book's definitions

TRI = {"scipy": "closed_form", "internal_consistency": True, "args": {"c": "c"},
       "expr": {"pdf": "2 * x / c if x < c else 2 * (1 - x) / (1 - c)", "support": [0, 1], "breaks": ["c"]},
       "points": [{"c": 0.3, "@x": 0.2}, {"c": 0.6, "@x": 0.7}, {"c": 0.5, "@x": 0.45}]}
FGM = {"scipy": "closed_form", "k": 2, "internal_consistency": True, "args": {"al": "alpha"},
       "expr": {"sf": "exp(-x_1) + exp(-x_2) - 1 + (1 - exp(-x_1)) * (1 - exp(-x_2)) * (1 + al * exp(-x_1 - x_2))",
                "cdf": "(1 - exp(-x_1)) * (1 - exp(-x_2)) * (1 + al * exp(-x_1 - x_2))"},
       "points": [{"alpha": 0.5, "@x": [0.7, 1.3]}, {"alpha": -0.6, "@x": [2.0, 0.4]},
                  {"alpha": 0.9, "@x": [0.25, 0.9]}]}
CLOSED = [("fgm cdf (given)", "fgm", "cdf", r"(1 - e^{-x_1})(1 - e^{-x_2})(1 + \alpha e^{-x_1 - x_2})",
           r"(1 - e^{-x_1})(1 - e^{-x_2})(1 - \alpha e^{-x_1 - x_2})", {})]
CLOSED_REAL = CLOSED + [
    ("tri cdf", "tri", "cdf", r"\frac{x^2}{c}", r"\frac{x^3}{c}", {}),
    ("tri mean", "tri", "mean", r"\frac{1 + c}{3}", r"\frac{1 + c}{2}", {}),
    ("fgm pdf", "fgm", "pdf", r"e^{-x_1 - x_2}\{1 + \alpha(2 e^{-x_1} - 1)(2 e^{-x_2} - 1)\}",
     r"e^{-x_1 - x_2}\{1 + \alpha(2 e^{-x_1} + 1)(2 e^{-x_2} - 1)\}", {}),
    ("fgm corr", "fgm", "corr", r"\frac{\alpha}{4}", r"\frac{\alpha}{2}", {"index": [1, 2]}),
    ("fgm cond mean", "fgm", "cond_mean", r"1 + \frac{1}{2} \alpha - \alpha e^{-x_1}",
     r"1 + \frac{1}{2} \alpha + \alpha e^{-x_1}", {"index": [2], "given": [1]}),
]


def test_closed_form_quantity_given_as_an_expression(tmp_path):
    res = run(tmp_path, CLOSED, {"fgm": FGM}, script=FAKE)
    assert res["fgm cdf (given) ok"]["status"] == "passed"
    assert res["fgm cdf (given) bad"]["status"] == "failed"


def test_real_closed_forms_from_a_pdf_or_survival_function(tmp_path):
    py = os.environ.get(C.ENV_VAR)
    if not py:
        pytest.skip("set GPDF_SCIPY_PYTHON to a Python with SciPy")
    tri = {**TRI, "points": [{"c": 0.3, "@x": 0.2}, {"c": 0.6, "@x": 0.5}, {"c": 0.5, "@x": 0.45}]}
    res = run(tmp_path, CLOSED_REAL, {"fgm": FGM, "tri": tri}, python=py)
    for r in res.values():
        want = "failed" if r["id"].endswith("bad") else "passed"
        assert r["status"] == want, (r["id"], r["status"], r.get("reason"))
    assert "internal consistency" in res["fgm pdf ok"]["points"][0].get("how", "internal consistency")


LOGIS = {"scipy": "closed_form", "k": 2, "internal_consistency": True, "args": {},
         "expr": {"sf": "1 - 1 / (1 + exp(-x_1)) - 1 / (1 + exp(-x_2)) + 1 / (1 + exp(-x_1) + exp(-x_2))",
                  "support": [["-inf", "inf"], ["-inf", "inf"]]},
         "points": [{"@x": [1.1, -0.4]}, {"@x": [0.3, 1.9]}, {"@x": [-1.5, 0.2]}]}
PARETO = {"scipy": "closed_form", "k": 2, "internal_consistency": True, "args": {"a": "a"},
          "expr": {"sf": "(1 + (x_1 - 1) + (x_2 - 2)) ** (-a)", "support": [[1, "inf"], [2, "inf"]]},
          "points": [{"a": 4.5, "@x": [1.1, 2.4]}, {"a": 6.0, "@x": [1.3, 2.9]}, {"a": 5.2, "@x": [2.5, 2.2]}]}
SUPPORT_CASES = [
    ("logistic cond pdf", "logis", "cond_pdf",
     r"\frac{2 e^{-x_1} (1 + e^{-x_2})^2}{(1 + e^{-x_1} + e^{-x_2})^3}",
     r"\frac{2 e^{-x_1} (1 + e^{-x_2})}{(1 + e^{-x_1} + e^{-x_2})^3}", {"index": [1, 2], "given": [2]}),
    ("pareto mean", "pareto", "mean", r"1 + \frac{1}{a - 1}", r"1 + \frac{1}{a}", {"index": [1]}),
]


def test_real_survival_closed_forms_on_another_support(tmp_path):
    py = os.environ.get(C.ENV_VAR)
    if not py:
        pytest.skip("set GPDF_SCIPY_PYTHON to a Python with SciPy")
    res = run(tmp_path, SUPPORT_CASES, {"logis": LOGIS, "pareto": PARETO}, python=py)
    for r in res.values():
        want = "failed" if r["id"].endswith("bad") else "passed"
        assert r["status"] == want, (r["id"], r["status"], r.get("reason"))


def test_real_closed_form_outside_its_support_and_at_a_pole(tmp_path):
    # a density with a pole at an end (n < 1): the cdf past the support is 1 and before it 0; the quantile and
    # median are found without evaluating the pole
    py = os.environ.get(C.ENV_VAR)
    if not py:
        pytest.skip("set GPDF_SCIPY_PYTHON to a Python with SciPy")
    stsp = {"scipy": "closed_form", "internal_consistency": True, "args": {"theta": "theta", "n": "n"},
            "expr": {"pdf": "n * (x / theta) ** (n - 1) if x <= theta else n * ((1 - x) / (1 - theta)) ** (n - 1)",
                     "support": [0, 1], "breaks": ["theta"]},
            "points": [{"theta": 0.3, "n": 0.6, "@x": 1.4, "@p": 0.25}, {"theta": 0.6, "n": 0.7, "@x": 1.0, "@p": 0.8},
                       {"theta": 0.45, "n": 0.5, "@x": 2.0, "@p": 0.5}]}
    cases = [("cdf past the support", "s", "cdf", r"1", r"0.9", {}),
             ("quantile at a pole", "s", "quantile", r"\theta \left(\frac{p}{\theta}\right)^{1/n}", r"\theta p^{1/n}", {"fixed": {"@p": 0.2}})]
    res = run(tmp_path, cases, {"s": stsp}, python=py)
    for r in res.values():
        want = "failed" if r["id"].endswith("bad") else "passed"
        assert r["status"] == want, (r["id"], r["status"], r.get("reason"))
