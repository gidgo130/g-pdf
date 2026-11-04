# SPDX-License-Identifier: AGPL-3.0-or-later
"""Numeric identity checks on our own formulas (skipped without the mathcheck extra):
correct densities pass, a wrong normalizing constant fails, a normal mgf with the wrong
sign fails the variance check but passes M(0) = 1, an F/f pair with a wrong exponent
fails F' = f, a matrix display is skipped, a parameter-constraint case is inconclusive."""

import pytest

pytest.importorskip("sympy")
pytest.importorskip("mpmath")
pytest.importorskip("antlr4")

from gpdf.pipeline.mathocr import identities as I  # noqa: E402


def check(latex, before="", ctx=None):
    return I.check_display(latex, before, ctx or I.Context())


# ---------------------------------------------------------------- preprocessing and classes

def test_preprocess_splits_aligned_rows_and_equality_chains():
    eqs = I.equations(r"\begin{aligned} f(x) &= \lambda e^{-\lambda x}, \\ &= \lambda \exp(-\lambda x), "
                      r"\qquad x > 0 \end{aligned} \tag{3.1}")
    assert [(l, r) for l, r in eqs] == [("f(x)", r"\lambda e^{-\lambda x}"),
                                        ("f(x)", r"\lambda \exp(-\lambda x)")]
    assert I.equations(r"F(x) = 1 - e^{-x} = 1 - \exp(-x), \quad x \ge 0") == [
        ("F(x)", "1 - e^{-x}"), ("F(x)", r"1 - \exp(-x)")]
    assert I.equations(r"\left( \frac{1}{2} \right)^{n}") == []          # no equation
    assert I.equations(r"f(x) \propto x^2") == []                        # not an identity


@pytest.mark.parametrize("lhs, before, kind, variables", [
    ("f(x)", "", "density", ["x"]),
    (r"f_X(x)", "", "density", ["x"]),
    (r"p_{X_1,X_2}(x_1, x_2)", "", "density", ["x_1", "x_2"]),
    (r"F(x)", "", "cdf", ["x"]),
    (r"\bar{F}_{X}(x)", "", "survival", ["x"]),
    (r"S(x)", "", "survival", ["x"]),
    (r"\Pr[X > x]", "", "survival", ["x"]),
    (r"\Pr[X_1 > x_1, X_2 > x_2]", "", "survival", ["x_1", "x_2"]),
    (r"\Pr[X \le x]", "", "cdf", ["x"]),
    (r"h(x)", "", "hazard", ["x"]),
    (r"M(t)", "", "mgf", ["t"]),
    (r"M_X(t)", "", "mgf", ["t"]),
    (r"E[e^{tX}]", "", "mgf", ["t"]),
    (r"E[e^{t_1 X_1 + t_2 X_2}]", "", "mgf", ["t_1", "t_2"]),
    (r"\varphi(t)", "", "cf", ["t"]),
    (r"G(z)", "", "pgf", ["z"]),
    (r"E[z^X]", "", "pgf", ["z"]),
    (r"E[X]", "", "mean", []),
    (r"\psi(x)", "the density function of X is", "density", ["x"]),
    (r"\psi(t)", "the moment generating function is", "mgf", ["t"]),
    (r"\psi(x)", "", None, []),
    (r"L_4(h; R)", "", None, []),
])
def test_classification_by_the_left_side_or_the_text_before(lhs, before, kind, variables):
    assert I.classify(lhs, before)[:2] == (kind, variables)


# ---------------------------------------------------------------- the checks

def test_correct_exponential_and_gamma_densities_pass():
    r = check(r"f(x) = \lambda e^{-\lambda x}, \qquad x > 0")
    assert (r.kind, r.status, r.support) == ("density", "passed", "(0,inf)")
    assert {c.identity for c in r.checks} == {"non-negative", "integrates to 1"}
    r = check(r"f(x) = \frac{1}{\Gamma(\alpha)} \beta^{\alpha} x^{\alpha - 1} e^{-\beta x}")
    assert (r.status, r.support) == ("passed", "(0,inf)")
    r = check(r"f(x) = \frac{1}{\sqrt{2\pi}\sigma} \exp\left\{ -\frac{(x-\mu)^2}{2\sigma^2} \right\}")
    assert (r.status, r.support) == ("passed", "(-inf,inf)")
    r = check(r"f(x) = \frac{\Gamma(a+b)}{\Gamma(a)\Gamma(b)} x^{a-1} (1-x)^{b-1} \tag{2.3}")
    assert (r.status, r.support) == ("passed", "(0,1)")


def test_a_density_with_a_wrong_normalizing_constant_fails():
    r = check(r"f(x) = \frac{1}{\Gamma(\alpha)} \beta^{\alpha - 1} x^{\alpha - 1} e^{-\beta x}")
    assert (r.kind, r.status) == ("density", "failed")
    bad = [c for c in r.checks if c.status == "failed"]
    assert bad[0].identity == "integrates to 1"
    assert set(bad[0].details) >= {"params", "integral", "support"}
    assert "printed error" in r.note


def test_a_normal_mgf_with_the_wrong_sign_fails_the_variance_check_but_not_m0():
    r = check(r"M(t) = \exp\left\{ \mu t - \frac{1}{2} \sigma^2 t^2 \right\}")
    assert (r.kind, r.status) == ("mgf", "failed")
    by = {c.identity: c.status for c in r.checks}
    assert by == {"M(0) = 1": "passed", "M''(0) - M'(0)^2 >= 0": "failed"}
    r = check(r"M(t) = \exp\left\{ \mu t + \frac{1}{2} \sigma^2 t^2 \right\}")
    assert r.status == "passed"


def test_a_two_dimensional_mgf_checks_the_hessian():
    good = r"M(t_1, t_2) = \exp\left\{ \frac{1}{2}(\sigma_1^2 t_1^2 + 2\rho\sigma_1\sigma_2 t_1 t_2 + \sigma_2^2 t_2^2) \right\}"
    r = check(good.replace(r"\rho", "(0.5)"))
    assert (r.kind, r.status) == ("mgf", "passed")
    r = check(good.replace(r"\rho", "(0.5)").replace(r"\exp\left\{ \frac{1}{2}",
                                                     r"\exp\left\{ -\frac{1}{2}"))
    assert r.status == "failed"
    assert [c.identity for c in r.checks if c.status == "failed"] == ["H(0) - g g^T is PSD"]


def test_cf_and_pgf():
    r = check(r"\varphi(t) = \exp\left\{ i\mu t - \frac{1}{2}\sigma^2 t^2 \right\}")
    assert (r.kind, r.status) == ("cf", "passed")
    r = check(r"\varphi(t) = \exp\left\{ i\mu t + \frac{1}{2}\sigma^2 t^2 \right\}")
    assert r.status == "failed"
    r = check(r"G(z) = e^{\lambda(z - 1)}")
    assert (r.kind, r.status) == ("pgf", "passed")
    r = check(r"G(z) = e^{\lambda(z - 2)}")
    assert r.status == "failed" and r.checks[0].identity == "G(1) = 1"


def test_cdf_and_survival_shape_and_limits():
    r = check(r"F(x) = 1 - e^{-\lambda x}, \qquad x \ge 0")
    assert (r.kind, r.status, r.support) == ("cdf", "passed", "(0,inf)")
    r = check(r"F(x) = 1 + e^{-\lambda x}")
    assert r.status == "failed"
    r = check(r"\bar{F}(x) = e^{-\lambda x}")
    assert (r.kind, r.status) == ("survival", "passed")
    r = check(r"S(x) = e^{\lambda x}")
    assert r.status == "failed"


def test_an_f_over_f_pair_with_a_wrong_exponent_fails_the_derivative_check():
    ctx = I.Context()
    r1 = check(r"F(x) = 1 - e^{-\lambda x}", ctx=ctx)
    r2 = check(r"f(x) = \lambda^2 e^{-\lambda x}", ctx=ctx)
    assert r1.status == "passed"
    assert r2.status == "failed"
    ids = [c.identity for c in r2.checks if c.status == "failed"]
    assert "F' = f" in ids and "integrates to 1" in ids
    ctx = I.Context()
    check(r"F(x) = 1 - e^{-\lambda x}", ctx=ctx)
    r = check(r"f(x) = \lambda e^{-\lambda x}", ctx=ctx)
    assert r.status == "passed" and "F' = f" in [c.identity for c in r.checks]


def test_other_pairs_in_a_section():
    ctx = I.Context()
    check(r"f(x) = \lambda e^{-\lambda x}", ctx=ctx)
    check(r"\bar{F}(x) = e^{-\lambda x}", ctx=ctx)
    r = check(r"h(x) = \lambda", ctx=ctx)
    assert r.status == "passed" and "h = f / S" in [c.identity for c in r.checks]
    r = check(r"h(x) = 2\lambda", ctx=ctx)
    assert r.status == "failed"
    ctx = I.Context()
    check(r"F(x) = 1 - e^{-\lambda x}", ctx=ctx)
    r = check(r"S(x) = 1 - e^{-\lambda x}", ctx=ctx)         # should be e^{-lambda x}
    assert r.status == "failed" and "S = 1 - F" in [c.identity for c in r.checks if c.status == "failed"]
    ctx = I.Context()
    check(r"\bar{F}_{X_1}(x_1) = e^{-\lambda_1 x_1}", ctx=ctx)
    r = check(r"\bar{F}_{X_1,X_2}(x_1, x_2) = e^{-\lambda_1 x_1 - \lambda_2 x_2 - \lambda_{12} x_1 x_2}", ctx=ctx)
    assert "joint survival at x_2 = 0 = marginal" in [c.identity for c in r.checks if c.status == "passed"]
    ctx = I.Context()
    check(r"M(t) = \exp\{\mu t + \frac{1}{2}\sigma^2 t^2\}", ctx=ctx)
    r = check(r"E[X] = \mu", ctx=ctx)
    assert r.kind == "mean" and "M'(0) = mean" in [c.identity for c in r.checks if c.status == "passed"]
    r = check(r"E[X] = 2\mu", ctx=ctx)
    assert r.status == "failed"


def test_a_new_section_forgets_the_pairs():
    ctx = I.Context()
    check(r"F(x) = 1 - e^{-\lambda x}", ctx=ctx)
    ctx.new_section("## 3 Another distribution")
    r = check(r"f(x) = \lambda^2 e^{-\lambda x}", ctx=ctx)
    assert "F' = f" not in [c.identity for c in r.checks]


def test_a_matrix_display_is_skipped_not_flagged():
    r = check(r"\boldsymbol{\xi} = \begin{pmatrix} 0 \\ 0 \end{pmatrix}")
    assert (r.kind, r.status) == (None, "skipped")
    r = check(r"f(x) = \begin{cases} 1 & \text{if } x > 0 \\ 0 & \text{otherwise} \end{cases}")
    assert r.status == "skipped" and not r.checks
    r = check(r"L_4(h; R) \doteq L_2(h_1, h_2)")
    assert r.status == "skipped"


def test_a_parameter_constraint_case_is_inconclusive():
    # a Pareto-type cdf needs alpha > 1 for one of the draws to reach the limit within
    # the sample range: some draws pass, some fail -> inconclusive, never a flag
    r = check(r"F(x) = 1 - (1 + x)^{-\alpha}, \qquad x > 0", "")
    assert r.status in ("inconclusive", "passed")
    # a correct density only for alpha > 1: the draws below 1 fail, the others pass
    r = check(r"f(x) = (\alpha - 1)(1 + x)^{-\alpha}, \qquad x > 0")
    assert r.status == "inconclusive"
    assert {c.status for c in r.checks} >= {"inconclusive"}


def test_timeout_skips_the_formula(monkeypatch):
    monkeypatch.setattr(I, "TIME_LIMIT", 0.001)
    monkeypatch.setattr(I, "_slow_start", lambda: __import__("time").sleep(0.05))
    r = check(r"f(x) = \lambda e^{-\lambda x}")
    assert (r.status, r.reason) == ("skipped", "timeout")


def test_counts_over_a_page():
    ctx = I.Context()
    results = [check(r"f(x) = \lambda e^{-\lambda x}", ctx=ctx),
               check(r"x = y", ctx=ctx),
               check(r"M(t) = \exp\{\mu t - \frac{1}{2}\sigma^2 t^2\}", ctx=ctx)]
    assert I.counts(results) == {"checked": 2, "passed": 1, "failed": 1, "inconclusive": 0,
                                 "skipped": 1}
