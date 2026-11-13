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


def counting_clock(step):
    """A clock that moves ``step`` seconds per reading: the deadline becomes a fixed
    number of evaluations (TIME_LIMIT / step), whatever the machine's load."""
    n = [0]

    def clock():
        n[0] += 1
        return n[0] * step
    return clock


@pytest.fixture
def steady_clock(monkeypatch):
    monkeypatch.setattr(I, "_clock", counting_clock(1e-5))     # 500 000 evaluations


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
    # the engine's spacing
    (r"\bar {F} _ {X _ {1}, X _ {2}} (x _ {1}, x _ {2})", "", "survival", ["x_1", "x_2"]),
    (r"M _ {X _ {1}, X _ {2}} (t _ {1}, t _ {2})", "", "mgf", ["t_1", "t_2"]),
    (r"\operatorname * {P r} [ X > x ]", "", "survival", ["x"]),
    (r"p _ {\Theta} (\theta)", "", "density", ["theta"]),
])
def test_classification_by_the_left_side_or_the_text_before(lhs, before, kind, variables):
    assert I.classify(lhs, before)[:2] == (kind, variables)


def test_greek_variables_and_spaced_latex_are_checked():
    r = check(r"p _ {\Theta} (\theta) = \lambda e ^ {- \lambda \theta}, \qquad \theta > 0")
    assert (r.kind, r.status, r.support) == ("density", "passed", "(0,inf)")
    r = check(r"M _ {X _ {1}, X _ {2}} (t _ {1}, t _ {2}) = \{1 - 2 t _ {1} - 2 t _ {2} - 2 t _ {1} t _ {2} \} ^ {- \alpha}")
    assert r.kind == "mgf" and r.status in ("passed", "failed", "inconclusive")
    assert [c.identity for c in r.checks] == ["M(0) = 1", "H(0) - g g^T is PSD"]


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
    # (the condition x > 0 is printed: without one the support is unknown and the check inconclusive)
    r = check(r"f(x) = \frac{1}{\Gamma(\alpha)} \beta^{\alpha - 1} x^{\alpha - 1} e^{-\beta x}, \quad x > 0")
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
    r2 = check(r"f(x) = \lambda^2 e^{-\lambda x}, \quad x > 0", ctx=ctx)
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


def test_vector_arguments_integrals_and_series_are_skipped():
    # seen on KBJ file 124/125: a multivariate normal density with bold vectors must not be
    # checked as a one-variable density, and an mgf defined by a multiple integral must not be
    # evaluated numerically at all (one evaluation ran for 40 minutes)
    r = check(r"p _ {\boldsymbol {Z}} (\boldsymbol {z}) = (2 \pi) ^ {- k / 2} | \boldsymbol {A} | ^ {1 / 2} "
              r"\exp \left\{- \frac {1}{2} (\boldsymbol {z} - \boldsymbol {\zeta}) ^ {T} \boldsymbol {A} "
              r"(\boldsymbol {z} - \boldsymbol {\zeta}) \right\}")
    assert (r.kind, r.status, r.reason) == (None, "skipped", "unclassified")
    r = check(r"M _ {\boldsymbol {X}} (\boldsymbol {t}) = C \int_ {- \infty} ^ {\infty} \dots "
              r"\int_ {- \infty} ^ {\infty} \exp \left\{ \boldsymbol{t}^T \boldsymbol{x} \right\} dx")
    assert r.status == "skipped" and not r.checks
    r = check(r"M _ {\boldsymbol {X}} (t _ {1}, \dots , t _ {k}) = E \left[ e ^ {\boldsymbol {t} ^ {T} \boldsymbol {X}} \right]")
    assert (r.kind, r.status) == (None, "skipped")
    assert I.classify(r"E[e^{\boldsymbol{t}^T \boldsymbol{X}}]")[0] is None
    r = check(r"G(z) = \sum_{n=0}^{\infty} b_n z^n")
    assert r.status == "skipped" and not r.checks
    r = check(r"f(x) = \prod_{i=1}^{k} (1 + x_i)^{-1}")
    assert r.status == "skipped" and not r.checks


def test_normal_density_notation_and_unknown_functions_from_kbj(steady_clock):
    # phi(.) is the standard normal density, Phi(.) its cdf: the skew-normal density passes
    r = check(r"p(z; \lambda) = 2 \varphi(z) \Phi(\lambda z)".replace(r"\varphi", r"\phi"))
    assert (r.kind, r.status, r.support) == ("density", "passed", "(-inf,inf)")
    r = check(r"p(x_1, x_2) = \phi(x_1) \phi(x_2)")
    assert (r.kind, r.status, r.support) == ("density", "passed", "(-inf,inf)")
    # a Latin letter before a bracket is an unknown function, not a product: skipped
    r = check(r"p(x_1, x_2) = e^{-(x_1^2 + x_2^2)/2} g(\tan^{-1}(x_2 / x_1))")
    assert (r.status, r.reason) == ("skipped", "does not parse")
    r = check(r"\bar{F}(t) = p(-\log B(t))")
    assert r.status == "skipped"
    # an unnamed normalizing constant cannot be checked
    r = check(r"p(x_1, x_2) = c e^{-(x_1^2 x_2^2 + x_1^2 + x_2^2)/2}")
    assert (r.status, r.reason) == ("skipped", "does not parse")
    # an interval probability or an event is no distribution function
    assert I.classify(r"\Pr[a_1 < \chi_4^2 < a_2]")[0] is None
    assert I.classify(r"\Pr[(T > t) \cap (I = i)]")[0] is None
    assert I.classify(r"\Pr[X_1 > x_1, X_2 \le x_2]")[0] is None
    assert I.classify(r"\Pr[X_1 > x_1, X_2 > x_2]")[0] == "survival"
    # a bivariate normal density passes the cubature at its coarser tolerance
    r = check(r"p(x_1, x_2) = [2 \pi \sqrt{1 - (0.5)^2}]^{-1} \exp\left\{-\frac{1}{2(1 - (0.5)^2)}"
              r"(x_1^2 - 2 (0.5) x_1 x_2 + x_2^2)\right\}")
    assert (r.kind, r.status, r.support) == ("density", "passed", "(-inf,inf)")


BIVARIATE_NORMAL = (r"p(x_1, x_2) = [2 \pi \sqrt{1 - (0.5)^2}]^{-1} \exp\left\{"
                    r"-\frac{1}{2(1 - (0.5)^2)}(x_1^2 - 2 (0.5) x_1 x_2 + x_2^2)\right\}")


def test_the_deadline_counts_clock_readings(monkeypatch):
    # the 2-D cubature needs about 77 000 evaluations: within 500 000 it passes, within
    # 50 000 it times out, every time
    monkeypatch.setattr(I, "_clock", counting_clock(1e-5))
    assert check(BIVARIATE_NORMAL).status == "passed"
    monkeypatch.setattr(I, "_clock", counting_clock(1e-4))
    assert (check(BIVARIATE_NORMAL).status, check(BIVARIATE_NORMAL).reason) == ("skipped", "timeout")


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


def test_the_deadline_stops_a_formula_inside_its_integrals(monkeypatch):
    monkeypatch.setattr(I, "TIME_LIMIT", 0.002)
    t0 = __import__("time").monotonic()
    r = check(r"f(x) = \frac{1}{\Gamma(\alpha)} \beta^{\alpha} x^{\alpha - 1} e^{-\beta x}")
    assert (r.status, r.reason) == ("skipped", "timeout")
    assert __import__("time").monotonic() - t0 < 1.0        # it did not finish the work


def test_a_double_exponential_density_finishes():
    # JKB p706: the quadrature reaches y far below 0, where e^{-y} has a binary exponent
    # near 10^20; mpmath's exp of that would compute ln 2 to as many bits and never
    # return, and the deadline is only read between evaluations. A subprocess, so a
    # regression fails the test instead of hanging the suite.
    import subprocess
    import sys
    code = ("from gpdf.pipeline.mathocr import identities as I\n"
            "r = I.check_display(r'p_{Y}(y) = e^{-e^{-y}} e^{-y}, \\quad -\\infty < y < \\infty,')\n"
            "print(r.kind, r.status, r.support)\n")
    try:
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                             timeout=60).stdout.split()
    except subprocess.TimeoutExpired:
        out = ["did", "not", "finish"]
    assert out == ["density", "passed", "(-inf,inf)"]


def test_putting_e_in_does_not_collapse_an_unevaluated_product():
    # sympy 1.14: subs(e, E) on this parse_latex tree gives 1 (the unevaluated x^{-3/2}
    # exponent); the standard inverse Gaussian density then "failed" to integrate to 1
    import math
    s = (r"\sqrt{\frac{\phi}{2 \pi}}e^{\phi}x^{- 3 / 2}"
         r"\exp ( - \frac{1}{2}\phi \cdot (x + x^{- 1}) )")
    expr = I._parse(s)
    assert expr != 1
    phi, x = 2.0, 1.1
    want = math.sqrt(phi / (2 * math.pi)) * math.exp(phi) * x ** -1.5 * math.exp(-phi * (x + 1 / x) / 2)
    got = float(expr.subs({s_: (phi if s_.name == "phi" else x) for s_ in expr.free_symbols}))
    assert abs(got - want) < 1e-12
    r = check("f(x) = " + s, "")
    assert (r.kind, r.status) == ("density", "passed"), [(c.identity, c.status) for c in r.checks]


def test_a_check_stuck_between_deadline_reads_is_stopped_by_the_item_timeout():
    # The deadline is only read between function evaluations; a check stuck inside one
    # (j21b hung 7.5 h that way) must still end, at ITEM_TIMEOUT. A subprocess, so a
    # regression fails the test instead of hanging the suite.
    import subprocess
    import sys
    code = ("import time\n"
            "from gpdf.pipeline.mathocr import identities as I\n"
            "I.ITEM_TIMEOUT = 0.5\n"
            "def stuck():\n"
            "    while True:\n"
            "        try:\n"
            "            sum(range(1000))\n"
            "        except Exception:\n"     # a broad handler must not swallow it
            "            pass\n"
            "I._slow_start = stuck\n"
            "t = time.monotonic()\n"
            "r = I.check_display(r'f(x) = \\lambda e^{-\\lambda x}')\n"
            "print(r.status, r.reason, time.monotonic() - t < 5)\n")
    try:
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                             timeout=60).stdout.split()
    except subprocess.TimeoutExpired:
        out = ["did", "not", "finish"]
    assert out == ["skipped", "timeout", "True"]


def test_the_item_timeout_does_not_fire_after_the_check():
    import time
    from gpdf.pipeline.mathocr import watch
    with watch.hard_timeout(0.2):
        pass
    time.sleep(0.5)                       # a late exception would surface here


def test_counts_over_a_page():
    ctx = I.Context()
    results = [check(r"f(x) = \lambda e^{-\lambda x}", ctx=ctx),
               check(r"x = y", ctx=ctx),
               check(r"M(t) = \exp\{\mu t - \frac{1}{2}\sigma^2 t^2\}", ctx=ctx)]
    assert I.counts(results) == {"checked": 2, "passed": 1, "failed": 1, "inconclusive": 0,
                                 "unsure": 0, "skipped": 1}


# ---------------------------------------------------------------- discrete distributions

@pytest.mark.parametrize("latex", [
    r"\Pr[X = x] = \binom{n}{x} p^{x} q^{n - x}, \quad x = 0, 1, \ldots, n",
    r"\Pr [ X = x ] = \frac{e^{-\theta} \theta^{x}}{x!}, \qquad x = 0, 1, 2, \ldots",
    r"p_x = \frac{\Gamma(k + x)}{x! \, \Gamma(k)} \left(\frac{P}{Q}\right)^{x} \left(1 - \frac{P}{Q}\right)^{k}",
    r"\Pr[X = x] = \frac{\theta^{x}}{-x \log(1 - \theta)}, \quad x = 1, 2, \ldots",
    r"\Pr[X = x] = {n \choose x} p^{x} (1 - p)^{n - x}",
])
def test_correct_pmfs_sum_to_one(latex):
    r = check(latex)
    assert (r.kind, r.status) == ("pmf", "passed"), [(c.identity, c.status, c.details) for c in r.checks]
    assert {c.identity for c in r.checks} >= {"non-negative", "sums to 1"}


def test_a_pmf_with_a_wrong_exponent_fails_and_carries_its_numbers():
    r = check(r"\Pr[X = x] = \frac{e^{-\theta} \theta^{x + 1}}{x!}")
    assert (r.kind, r.status) == ("pmf", "failed")
    bad = [c for c in r.checks if c.status == "failed"]
    assert bad[0].identity == "sums to 1" and {"params", "support", "sum"} <= set(bad[0].details)


def test_a_discrete_density_written_as_f_is_not_a_false_alarm():
    r = check(r"f(x) = \frac{e^{-\lambda} \lambda^{x}}{x!}")
    assert r.status == "passed"


def test_printed_mean_and_variance_against_the_pgf_mgf_and_pmf():
    ctx = I.Context()
    check(r"G(z) = (q + p z)^{n}", ctx=ctx)
    r = check(r"\mu = n p, \qquad \operatorname{Var}(X) = n p q", ctx=ctx)
    ok = {c.identity for c in r.checks if c.status == "passed"}
    assert {"G'(1) = mean", "G''(1) + G'(1) - G'(1)^2 = variance"} <= ok
    r = check(r"\operatorname{Var}(X) = n p^{2} q", ctx=ctx)            # printed wrong
    assert r.status == "failed" and r.kind == "variance"
    ctx = I.Context()
    check(r"\Pr[X = x] = \frac{e^{-\theta} \theta^{x}}{x!}", ctx=ctx)
    r = check(r"E[X] = \theta", ctx=ctx)
    assert "sum x p(x) = mean" in [c.identity for c in r.checks if c.status == "passed"]
    r = check(r"\mu_2 = \theta^{2}", ctx=ctx)
    assert r.status == "failed"
    ctx = I.Context()
    check(r"M(t) = (q + p e^{t})^{n}", ctx=ctx)
    r = check(r"\sigma^{2} = n p q", ctx=ctx)
    assert "M''(0) - M'(0)^2 = variance" in [c.identity for c in r.checks if c.status == "passed"]


def test_recurrences_and_point_values_against_the_pmf():
    ctx = I.Context()
    check(r"\Pr[X = x] = \binom{n}{x} p^{x} q^{n - x}", ctx=ctx)
    r = check(r"\frac{\Pr[X = x + 1]}{\Pr[X = x]} = \frac{(n - x) p}{(x + 1) q}", ctx=ctx)
    assert (r.kind, r.status) == ("recurrence", "passed")
    r = check(r"p_{x + 1} = \frac{(n - x) p}{(x + 1) q} p_{x}", ctx=ctx)
    assert r.status == "passed"
    r = check(r"(x + 1) p_{x + 1} = (n - x) \frac{p}{q} p_{x}", ctx=ctx)
    assert r.status == "passed"
    r = check(r"\frac{\Pr[X = x + 1]}{\Pr[X = x]} = \frac{(n - x) p}{x q}", ctx=ctx)
    assert r.status == "failed" and r.checks[0].identity == "recurrence"
    r = check(r"\Pr[X = 0] = q^{n}", ctx=ctx)
    assert (r.kind, r.status) == ("pmf_point", "passed")
    r = check(r"\Pr[X = 0] = p^{n}", ctx=ctx)
    assert r.status == "failed"
    # mixture weights p_1, p_2 are no pmf values
    r = check(r"\Pr[X = x] = p_1 \frac{e^{-\theta_1}\theta_1^x}{x!} + p_2 \frac{e^{-\theta_2}\theta_2^x}{x!}",
              ctx=I.Context())
    assert r.kind == "pmf" and r.status in ("inconclusive", "failed", "passed")


def test_a_constant_printed_in_the_section_is_used():
    ctx = I.Context()
    ctx.new_section("## 7.1 Logarithmic", defs={"C": r"-1 / \log(1 - \theta)"})
    r = check(r"\Pr[X = x] = C \frac{\theta^{x}}{x}", ctx=ctx)
    assert (r.kind, r.status) == ("pmf", "passed")
    assert I.definitions(r"C = \frac{1}{\Gamma(\alpha)}, \qquad q = 1 - p") == {
        "C": r"\frac{1}{\Gamma(\alpha)}", "q": "1 - p"}


def test_side_conditions_with_dots_or_text_no_longer_hide_the_formula():
    assert I.equations(r"\Pr[X = x] = \theta^{x} (1 - \theta), \quad x = 0, 1, \ldots") == [
        (r"\Pr[X = x]", r"\theta^{x}(1 - \theta)"), ("x", "0")]
    assert I.equations(r"\mu = n p \text{ and } \mu_2 = n p q") == [(r"\mu", "n p"), (r"\mu_2", "n p q")]
    assert I.equations(r"G(z) = \sum_{n=0}^{\infty} b_n z^n") == []


def test_no_pair_check_across_parametrizations():
    # JKK 5.1 prints the negative binomial in P, Q and in p, q: a mean in P is not checked
    # against a PGF in q (no shared parameters), nor a recurrence against a pmf without its
    # parameters
    ctx = I.Context()
    check(r"G(z) = \left(\frac{1 - q}{1 - q z}\right)^{k}", ctx=ctx)
    r = check(r"\mu = k P", ctx=ctx)
    assert r.status == "skipped" or not [c for c in r.checks if c.identity.startswith("G'")]
    ctx = I.Context()
    check(r"\Pr[X = x] = \frac{e^{-\theta}\theta^{x}}{x!}", ctx=ctx)
    r = check(r"\frac{p_{x+1}}{p_x} = \frac{\lambda}{x + 1}", ctx=ctx)
    assert not r.checks


def test_jkk_hypergeometric_chapter_false_alarms_are_gone():
    # a hypergeometric pmf in a, b (counts): integer draws, so binomials are non-negative
    r = check(r"\Pr [ X = x ] = \binom{a}{x} \binom{b}{n - x} / \binom{a + b}{n}")
    assert (r.kind, r.status) == ("pmf", "passed")
    # a Gauss hypergeometric function {}_2F_1[...] or a derivative is not evaluated
    assert check(r"G (z) = K _ {2} F _ {1} [ - n, - a; b - n + 1; z ]").status == "skipped"
    assert check(r"G(z) = \frac{\partial^{n}}{\partial y^{n}} (1 + y z)^{a}").status == "skipped"
    # a left-hand side whose argument is not a variable is not classified
    assert I.classify(r"G (e ^ {t})")[0] is None
    assert I.classify(r"G (1 + t)")[0] is None
    assert I.classify(r"G _ {\beta b} (k | m, \ell + m, n)")[0] is None
    # a pmf in x whose right side has no x (another index) is not checked
    r = check(r"\Pr [ X = x ] = \binom {2 N - r} {N - r} 2 ^ {r - 2 n}")
    assert r.status == "skipped"


def test_a_greek_parameter_before_a_bracket_binds_as_a_product_not_a_power_base():
    # Consul's generalized Poisson: theta (theta + x lambda)^{x-1} is theta times the power
    r = check(r"\Pr [ X = x ] = \frac {\theta (\theta + x \lambda) ^ {x - 1} e ^ {- \theta - x \lambda}}"
              r"{x !}, \qquad x = 0, 1, 2, \dots")
    # lambda < 1 is needed: the draws above 1 diverge, so inconclusive (never failed)
    assert (r.kind, r.status) == ("pmf", "inconclusive"), [(c.identity, c.details) for c in r.checks]
    sums = [c for c in r.checks if c.identity == "sums to 1"][0]
    assert sums.details["params"]["lambda"] > 1
    r = check(r"G(z) = e^{\lambda_1 (z - 1)}")
    assert r.status == "passed"


def test_pi_before_a_bracket_is_a_product_not_a_function_call():
    # Gilchrist's Cauchy quantile: tan[pi (p - 0.5)]; parse_latex read pi(...) as an unknown call
    e = I._parse(I._clean(I._pre(r"\lambda + \eta \tan(\pi (p - 0.5))")))
    assert e is not None
    s = {x.name: x for x in e.free_symbols}
    v = e.subs({s["lambda"]: 1, s["eta"]: 2, s["p"]: 0.75})
    assert abs(float(v) - 3.0) < 1e-12                  # 1 + 2 tan(pi/4)


def test_a_derivative_in_q_is_skipped_not_raised():
    # Ross 11e: q is replaced by 1 - p, which sympy cannot do inside d/dq (ValueError)
    r = I.check_display(r"M(t) = \frac{d}{dq} \left( q^{t} \right)")
    assert r.status in ("skipped", "inconclusive", "passed", "failed")


def test_erfc_and_the_inverse_normal_parse():
    # erfc, and the normal quantile written Phi^{-1}(p) or N(p): sqrt(2) erfinv(2p - 1)
    r = check(r"F(x) = \frac{1}{2} \operatorname{erfc}\left(-\frac{x - \mu}{\sigma \sqrt{2}}\right)")
    assert r.status == "passed", (r.status, r.reason)
    for tex in (r"\mu + \sigma \Phi^{-1}(p)", r"\mu + \sigma N(p)", r"\mu + \sigma \Phi^{-1}\left(p\right)"):
        e = I._parse(I._clean(tex))
        assert e is not None, tex
        names = {s.name: s for s in e.free_symbols}
        v = complex(e.subs({names["mu"]: 1, names["sigma"]: 2, names["p"]: 0.975}).evalf())
        assert abs(v.real - (1 + 2 * 1.959963984540054)) < 1e-9, (tex, v)


def test_the_digamma_function_parses_by_name():
    # \operatorname{digamma}(z) (a spec writes a book's psi this way when psi is the digamma function) is sympy's
    # digamma; a bare \psi stays a letter (a potential, a density)
    for tex in (r"\operatorname{digamma}(n/2) + n", r"\mathrm{digamma}\left(\frac{n}{2}\right) + n"):
        e = I._parse(I._clean(tex))
        assert e is not None, tex
        n = {s.name: s for s in e.free_symbols}["n"]
        assert abs(complex(e.subs({n: 3}).evalf()).real - (0.0364899739785765 + 3)) < 1e-12, tex  # 2 - gamma - 2 ln 2


def test_derivation_steps_are_not_checked():
    # three or more equalities: a derivation, skipped; a row that starts with "=" continues the first
    r = check(r"f(x) = 2 x e^{-x^2} = 3 x^2 = x")
    assert r.status == "skipped", (r.status, [c.identity for c in r.checks])
    r = check(r"\begin{aligned} F(x) &= 1 - e^{-x} \\ &= 2 - e^{-x} \end{aligned}")
    assert r.status == "passed", (r.status, [(c.identity, c.status) for c in r.checks])
    # a footnote mark after a closing bracket and trailing punctuation are not part of the formula
    assert I.equations(r"F(x) = (1 - e^{-x})^{*}.") == [("F(x)", "(1 - e^{-x})")]


def test_the_books_letters_decide_the_quantity():
    # Gilchrist: S(p) and Q(p) are quantile functions (monotone increasing on (0, 1))
    ctx = I.Context(letters={"S": "quantile", "Q": "quantile"})
    r = check(r"S(p) = \ln\frac{p}{1 - p}", ctx=ctx)
    assert (r.kind, r.status) == ("quantile", "passed"), (r.kind, r.status)
    r = check(r"Q(p) = \lambda + \eta \ln\frac{1 - p}{p}", ctx=I.Context(letters={"Q": "quantile"}))
    assert (r.kind, r.status) == ("quantile", "failed")
    # undeclared, a "survival function" of a probability variable cannot be confirmed: skipped
    r = check(r"S(p) = \ln\frac{p}{1 - p}")
    assert r.status == "skipped", (r.kind, r.status)
    # HMC: G is a cdf, not a pgf
    r = check(r"G(y) = 1 - e^{-y}", ctx=I.Context(letters={"G": "cdf"}))
    assert (r.kind, r.status) == ("cdf", "passed")
    r = check(r"G(y) = 1 - e^{-y}", ctx=I.Context(letters={"G": "skip"}))
    assert r.status == "skipped"


def test_identity_letters_parse():
    assert I.parse_letters("S=quantile, Q=quantile,G=cdf") == {"S": "quantile", "Q": "quantile", "G": "cdf"}
    with pytest.raises(ValueError):
        I.parse_letters("S=quantity")


def test_the_support_comes_from_the_displays_own_condition():
    r = check(r"f(x) = \frac{2 x}{\theta^2}, \quad 0 < x < \theta")
    assert r.status == "passed", (r.status, [(c.identity, c.status, c.details) for c in r.checks])
    r = check(r"f(x) = \frac{1}{n}, \quad x = 1, 2, \ldots, n")
    assert r.status == "passed", (r.status, [(c.identity, c.status, c.details) for c in r.checks])
    # no condition and no standard support integrates to 1: inconclusive (no support), not failed
    r = check(r"f(x) = 3 x")
    integ = next(c for c in r.checks if c.identity == "integrates to 1")
    assert integ.status == "inconclusive" and integ.details.get("support") == "no support", integ
    # with a condition, a wrong constant still fails
    r = check(r"f(x) = \frac{3 x}{\theta^2}, \quad 0 < x < \theta")
    assert r.status == "failed"


def test_a_hypergeometric_pmf_draws_integer_counts():
    # D is a count like N: a fractional D made binom(D, x) negative and failed "non-negative"
    r = check(r"p(x) = \frac{\binom{N - D}{n - x} \binom{D}{x}}{\binom{N}{n}}, \quad x = 0, 1, \dots, n")
    assert r.status == "passed", [(c.identity, c.status, c.details) for c in r.checks]


def test_no_derivative_pair_for_a_pmf_on_an_integer_support():
    # an earlier continuous cdf in the section; the pmf's list may lack the comma before the dots
    for dots in (r"3, \ldots", r"3 \ldots ."):
        ctx = I.Context()
        check(r"F(y) = \frac{27 y^{3}}{8}, \quad 0 \leq y < \frac{2}{3}", ctx=ctx)
        r = check(r"p(x) = \frac{2}{3} \left(\frac{1}{3}\right)^{x - 1}, \quad x = 1, 2, " + dots, ctx=ctx)
        assert r.kind == "density" and r.status == "passed", (dots, [(c.identity, c.status) for c in r.checks])
        assert "F' = f" not in [c.identity for c in r.checks]
        assert r.checks and any(c.details.get("support", "").endswith("(condition)") for c in r.checks), dots


def test_a_failed_check_on_a_guessed_quantity_is_unsure():
    # a pgf letter with a density-like variable, or a quantity guessed from the prose before it
    r = check(r"G(x) = x^2 + 1")
    assert r.status == "unsure", (r.kind, r.status)
    r = check(r"q(x) = 3 x^{2} + 1, \quad 0 < x < 1", before="whose density is")
    assert r.status == "unsure", (r.kind, r.status)
    r = check(r"f(x) = 3 x^{2} + 1, \quad 0 < x < 1")       # typed by its own left side: failed
    assert r.status == "failed"
    assert I.counts([r, check(r"G(x) = x^2 + 1")])["unsure"] == 1
