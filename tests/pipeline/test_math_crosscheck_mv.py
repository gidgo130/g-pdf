# SPDX-License-Identifier: AGPL-3.0-or-later
"""gpdf-cli math-crosscheck, multivariate step 1, on hand-written pages of our own formulas:
fixed-k sums, products and dotted lists written out, multinomial coefficients, free indices,
and one planted error per quantity (joint pmf and pdf, marginal, mean, var, cov, corr,
factorial moment, pgf, conditional pmf, mean and variance; a marginal summed from the joint)
against a fake reference (tests/pipeline/fake_scipy_ref.py), and the real SciPy worker when
GPDF_SCIPY_PYTHON is set."""

import json
import os
import sys
from pathlib import Path

import pytest

pytest.importorskip("sympy")
pytest.importorskip("antlr4")

from gpdf.pipeline.mathocr import crosscheck as C  # noqa: E402
from gpdf.pipeline.mathocr import crosscheck_mv as MV  # noqa: E402

FAKE = Path(__file__).with_name("fake_scipy_ref.py")

MN = "P(x_1, x_2, \\dots, x_k) = "
# (qty, right / planted formula, entry fields)
CASES = [
    ("pmf", MN + "\\binom{n}{x_1, x_2, \\dots, x_k} \\prod_{i=1}^{k} p_i^{x_i}",
     MN + "\\frac{n!}{x_1! x_2! \\cdots x_k!} \\prod_{i=1}^{k} p_i^{x_i + 1}", {}),
    ("marginal_pmf", "\\Pr[X_i = x_i] = \\binom{n}{x_i} p_i^{x_i} (1 - p_i)^{n - x_i}",
     "\\Pr[X_i = x_i] = \\binom{n}{x_i} p_i^{x_i} (1 - p_i)^{n + x_i}", {"index": [2]}),
    ("mean", "E[X_i] = n p_i", "E[X_i] = n p_i (1 - p_i)", {"index": [1]}),
    ("var", "\\operatorname{var}(X_i) = n p_i (1 - p_i)", "\\operatorname{var}(X_i) = n p_i",
     {"index": [3]}),
    ("cov", "\\operatorname{cov}(X_i, X_j) = -n p_i p_j", "\\operatorname{cov}(X_i, X_j) = n p_i p_j",
     {"index": [1, 3]}),
    ("corr", "\\rho_{ij} = -\\sqrt{\\frac{p_i p_j}{(1 - p_i)(1 - p_j)}}",
     "\\rho_{ij} = \\sqrt{\\frac{p_i p_j}{(1 - p_i)(1 - p_j)}}", {"index": [1, 2]}),
    ("fact_moment", "\\mu'_{(r)} = \\frac{n!}{(n - r_1 - r_2 - \\cdots - r_k)!} \\prod_{i=1}^{k} p_i^{r_i}",
     "\\mu'_{(r)} = \\frac{n!}{(n - r_1 - r_2 - \\cdots - r_k)!} \\prod_{i=1}^{k} p_i^{r_i + 1}",
     {"r": [2, 1, 0]}),
    ("pgf", "G(t) = \\left(\\sum_{i=1}^{k} p_i t_i\\right)^{n}",
     "G(t) = \\left(\\sum_{i=1}^{k} p_i t_i\\right)^{n - 1}", {}),
    ("cond_pmf", "\\binom{n - x_2}{x_1} \\left(\\frac{p_1}{1 - p_2}\\right)^{x_1} "
                 "\\left(\\frac{p_3}{1 - p_2}\\right)^{n - x_2 - x_1}",
     "\\binom{n - x_2}{x_1} \\left(\\frac{p_1}{1 - p_2}\\right)^{x_1} "
     "\\left(\\frac{p_2}{1 - p_2}\\right)^{n - x_2 - x_1}", {"index": [1], "given": [2]}),
    ("cond_mean", "(n - x_2) \\frac{p_1}{1 - p_2}", "n \\frac{p_1}{1 - p_2}",
     {"index": [1], "given": [2]}),
    ("cond_var", "(n - x_2) \\frac{p_1 p_3}{(1 - p_2)^{2}}", "(n - x_2) \\frac{p_1}{1 - p_2}",
     {"index": [1], "given": [2]}),
]
BVN = ("\\frac{1}{2 \\pi \\sigma_1 \\sigma_2 \\sqrt{1 - \\rho^2}} \\exp\\left\\{-\\frac{1}{2(1 - \\rho^2)}"
       "\\left[\\left(\\frac{x_1 - \\mu_1}{\\sigma_1}\\right)^2 {S} 2 \\rho \\frac{(x_1 - \\mu_1)(x_2 - \\mu_2)}"
       "{\\sigma_1 \\sigma_2} + \\left(\\frac{x_2 - \\mu_2}{\\sigma_2}\\right)^2\\right]\\right\\}")
NM = ("P(n_1, n_2) = \\frac{\\Gamma(n + n_1 + n_2)}{\\Gamma(n) n_1! n_2!} Q^{-n} "
      "\\prod_{i=1}^{k} \\left(\\frac{P_i}{Q}\\right)^{n_i}")
DISTS = {
    "mult": {"scipy": "multinomial", "k": 3, "args": {"n": "n", "p": "[p_1, p_2, p_3]"},
             "derived": {"p_3": "1 - p_1 - p_2"},
             "points": [{"n": 6, "p_1": 0.2, "p_2": 0.5, "@x": [2, 3, 1], "@t": [0.3, 0.6, 0.9]},
                        {"n": 5, "p_1": 0.1, "p_2": 0.3, "@x": [1, 1, 3], "@t": [0.5, 0.2, 0.7]},
                        {"n": 4, "p_1": 0.45, "p_2": 0.25, "@x": [0, 2, 2], "@t": [0.8, 0.4, 0.1]}]},
    "bvn": {"scipy": "multivariate_normal", "k": 2,
            "args": {"mean": "[mu_1, mu_2]", "cov": "[[sigma_1 ** 2, rho * sigma_1 * sigma_2], "
                                                   "[rho * sigma_1 * sigma_2, sigma_2 ** 2]]"},
            "points": [{"mu_1": 0.5, "mu_2": -1.0, "sigma_1": 1.2, "sigma_2": 0.7, "rho": 0.3, "@x": [1.1, -0.4]},
                       {"mu_1": -0.2, "mu_2": 0.4, "sigma_1": 0.8, "sigma_2": 1.5, "rho": -0.6, "@x": [0.3, 1.9]},
                       {"mu_1": 1.0, "mu_2": 2.0, "sigma_1": 2.0, "sigma_2": 1.0, "rho": 0.8, "@x": [2.5, 2.2]}]},
    "nm": {"scipy": "negative_multinomial", "k": 2, "var": "n",
           "args": {"n": "n", "p": "[P_1 / Q, P_2 / Q]"}, "derived": {"Q": "1 + P_1 + P_2"},
           "points": [{"n": 2.5, "P_1": 0.7, "P_2": 1.3, "@x": [2, 3]},
                      {"n": 1.2, "P_1": 0.4, "P_2": 0.9, "@x": [0, 4]},
                      {"n": 4.0, "P_1": 1.1, "P_2": 0.5, "@x": [5, 1]}]},
}


def page_and_entries():
    disp, entries = [], []
    for q, right, wrong, extra in CASES:
        for ok, tex in (("ok", right), ("bad", wrong)):
            disp.append(tex)
            entries.append({"id": f"{q} {ok}", "where": {"tag": f"9.{len(disp)}"}, "qty": q,
                            "dist": "mult", **extra})
    for ok, sign in (("ok", "-"), ("bad", "+")):
        disp.append("f(x_1, x_2) = " + BVN.replace("{S}", sign))
        entries.append({"id": f"pdf {ok}", "where": {"tag": f"9.{len(disp)}"}, "qty": "pdf", "dist": "bvn"})
    for ok, s in (("ok", "+"), ("bad", "-")):
        disp.append(f"E[X_1 \\mid X_2 = x_2] = \\mu_1 {s} \\rho \\frac{{\\sigma_1}}{{\\sigma_2}} (x_2 - \\mu_2)")
        entries.append({"id": f"bvn cond_mean {ok}", "where": {"tag": f"9.{len(disp)}"}, "qty": "cond_mean",
                        "dist": "bvn", "index": [1], "given": [2]})
    disp.append(NM)
    entries.append({"id": "nm pmf", "where": {"tag": f"9.{len(disp)}"}, "qty": "pmf", "dist": "nm"})
    entries.append({"id": "nm marginal by summing the joint", "where": {"tag": f"9.{len(disp)}"},
                    "qty": "marginal_pmf", "dist": "nm", "index": [1], "sum_out": {"over": "n_2", "top": 400}})
    md = '<!-- page printed="1" file="1" -->\n\n' + "".join(
        f"$$\n{t}\n\\tag{{9.{n}}}\n$$\n\n" for n, t in enumerate(disp, 1))
    return md, entries


def run_spec(tmp_path, python=None, script=None):
    md, entries = page_and_entries()
    (tmp_path / "text.md").write_text(md, "utf-8")
    p = tmp_path / "spec.json"
    p.write_text(json.dumps({"pages": "text.md", "dists": DISTS, "entries": entries}), "utf-8")
    ref = C.RefWorker(python or sys.executable, script)
    try:
        return C.run(p, tmp_path / "out", ref=ref)
    finally:
        ref.close()


def test_one_planted_error_per_quantity(tmp_path):
    res = {r["id"]: r for r in run_spec(tmp_path, script=FAKE)["results"]}
    for r in res.values():
        want = "failed" if r["id"].endswith("bad") else "passed"
        assert r["status"] == want, (r["id"], r["status"], r.get("reason"), r.get("expanded"))
    assert len(res["pmf ok"]["points"]) == 3 and res["pmf ok"]["multivariate"]


def test_expansion_of_sums_products_lists_and_coefficients():
    v = {"x_1": 2, "x_2": 3}
    assert MV.expand("x_1 + x_2 + \\cdots + x_k", v, 4) == "x_{1} + x_{2} + x_{3} + x_{4}"
    assert MV.expand("n_1! n_2! \\cdots n_k!", v, 3) == "n_{1}! n_{2}! n_{3}!"
    assert MV.expand("x_1, \\dots, x_k", v, 2) == "x_{1}, x_{2}"
    assert MV.expand("\\prod_{i=0}^{k} p_i^{x_i}", v, 1) == "((p_{0}^{x_{0}}) \\cdot (p_{1}^{x_{1}}))"
    s = MV.expand("\\sum_{i = 0}^{\\min(x_1, x_2)} \\frac{a^{i}}{i!} + c", v, 3)
    assert s == "((\\frac{a^{0}}{0!}) + (\\frac{a^{1}}{1!}) + (\\frac{a^{2}}{2!}))+ c"
    assert MV.expand("\\binom{n}{a, b}", v, 2) == "\\frac{(n)!}{(a)!(b)!}"
    assert MV.expand("E[X_i] = n p_i", v, 3, {"i": 2}) == "E[X_{2}] = n p_{2}"
    assert MV.expand("\\boldsymbol{x}_1 + \\mathbf{t}_{2}", v, 2) == "x_{1} + t_{2}"
    assert MV.expand("p_{0 1} + p_{1 1}", v, 2) == "p_{01}+ p_{11}"
    # for the parser: a product where it would see a call, a leading-zero subscript's value
    assert MV.for_parser("p_{01}^{a} p_{1}(1 - p_{1}) B(a, b)", {"p_01": 0.3}) == \
        "(0.3)^{a} p_{1} \\cdot (1 - p_{1}) B(a, b)"


REAL = {
    "bp": ({"scipy": "bivariate_poisson", "k": 2, "args": {"theta1": "t_1", "theta2": "t_2", "theta12": "t_3"},
            "points": [{"t_1": 1.2, "t_2": 0.7, "t_3": 0.5, "@x": [2, 3]}, {"t_1": 0.4, "t_2": 2.1, "t_3": 1.3, "@x": [1, 0]},
                       {"t_1": 3.0, "t_2": 1.5, "t_3": 0.9, "@x": [4, 5]}]},
           "e^{-(t_1 + t_2 + t_3)} \\sum_{i=0}^{\\min(x_1, x_2)} \\frac{t_1^{x_1 - i} t_2^{x_2 - i} t_3^{i}}"
           "{(x_1 - i)! (x_2 - i)! i!}", "pmf", {}),
    "bb": ({"scipy": "bivariate_binomial", "k": 2, "args": {"n": "n", "p11": "p_11", "p10": "p_10", "p01": "p_01"},
            "derived": {"p_00": "1 - p_11 - p_10 - p_01"},
            "points": [{"n": 6, "p_11": 0.2, "p_10": 0.3, "p_01": 0.1, "@x": [3, 2]},
                       {"n": 5, "p_11": 0.1, "p_10": 0.25, "p_01": 0.35, "@x": [2, 3]},
                       {"n": 8, "p_11": 0.3, "p_10": 0.2, "p_01": 0.2, "@x": [4, 5]}]},
           "\\sum_{i=0}^{\\min(x_1, x_2)} \\frac{n!}{i! (x_1 - i)! (x_2 - i)! (n + i - x_1 - x_2)!} "
           "p_{1 1}^{i} p_{1 0}^{x_1 - i} p_{0 1}^{x_2 - i} p_{0 0}^{n - x_1 - x_2 + i}", "pmf", {}),
    "mh": ({"scipy": "multivariate_hypergeom", "k": 3, "var": "n", "args": {"m": "[m_1, m_2, m_3]", "n": "n"},
            "derived": {"m": "m_1 + m_2 + m_3"},
            "points": [{"m_1": 5, "m_2": 7, "m_3": 4, "n": 6, "@x": [2, 3, 1]},
                       {"m_1": 10, "m_2": 3, "m_3": 6, "n": 8, "@x": [4, 1, 3]},
                       {"m_1": 4, "m_2": 4, "m_3": 9, "n": 5, "@x": [0, 2, 3]}]},
           "-\\frac{n (m - n)}{m^2 (m - 1)} m_i m_j", "cov", {"index": [1, 3]}),
    "dm": ({"scipy": "dirichlet_multinomial", "k": 2, "first": 0, "args": {"alpha": "[a_0, a_1, a_2]", "n": "n"},
            "derived": {"a": "a_0 + a_1 + a_2"},
            "points": [{"a_0": 1.5, "a_1": 2.5, "a_2": 0.8, "n": 6, "@x": [2, 3, 1]},
                       {"a_0": 0.6, "a_1": 1.1, "a_2": 3.2, "n": 5, "@x": [0, 1, 4]},
                       {"a_0": 2.0, "a_1": 2.0, "a_2": 2.0, "n": 7, "@x": [3, 2, 2]}]},
           "\\frac{n! \\Gamma(a)}{\\Gamma(n + a)} \\prod_{i=0}^{k} \\frac{\\Gamma(a_i + x_i)}{\\Gamma(a_i) x_i!}", "pmf", {}),
}


def test_real_scipy_multivariate_when_configured(tmp_path):
    py = os.environ.get(C.ENV_VAR)
    if not py:
        pytest.skip("set GPDF_SCIPY_PYTHON to a Python with SciPy")
    res = {r["id"]: r for r in run_spec(tmp_path, python=py)["results"]}
    for r in res.values():
        want = "failed" if r["id"].endswith("bad") else "passed"
        assert r["status"] == want, (r["id"], r["status"], r.get("reason"))
    # the other references (exact sums over their supports), each right and planted wrong
    md, entries, dists = '<!-- page printed="1" file="1" -->\n\n', [], {}
    for n, (name, (dist, tex, q, extra)) in enumerate(REAL.items()):
        dists[name] = dist
        for ok, t in (("ok", tex), ("bad", tex.replace("n!", "2 n!").replace("e^{-", "2 e^{-")
                                       .replace("-\\frac", "\\frac"))):
            tag = f"8.{len(entries) + 1}"
            md += f"$$\n{t}\n\\tag{{{tag}}}\n$$\n\n"
            entries.append({"id": f"{name} {ok}", "where": {"tag": tag}, "qty": q, "dist": name, **extra})
    d = tmp_path / "real"
    d.mkdir()
    (d / "text.md").write_text(md, "utf-8")
    (d / "spec.json").write_text(json.dumps({"pages": "text.md", "dists": dists, "entries": entries}), "utf-8")
    res = {r["id"]: r for r in C.run(d / "spec.json", d / "out", python=py)["results"]}
    for r in res.values():
        want = "failed" if r["id"].endswith("bad") else "passed"
        assert r["status"] == want, (r["id"], r["status"], r.get("reason"), r.get("expanded"))
