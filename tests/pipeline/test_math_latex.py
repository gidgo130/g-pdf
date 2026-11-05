# SPDX-License-Identifier: AGPL-3.0-or-later
"""LaTeX normalization, token distance and markdown extraction (the trial scorer's rules)."""

import random

import pytest

from gpdf.pipeline.mathocr import latex as L


def norm(s):
    return L.norm_str(L.tokens(s))


@pytest.mark.parametrize("a, b", [
    (r"\left( x \right)", "(x)"),
    (r"x \leq y \geq z \neq w", r"x \le y \ge z \ne w"),
    (r"x^a_b", r"x_b^a"),
    (r"x^{a}_{b}", r"x_{b}^{a}"),
    (r"{\bf X}", r"\mathbf{X}"),
    (r"\boldsymbol{x} \pmb{y} \bm{z}", r"\mathbf{x} \mathbf{y} \mathbf{z}"),
    (r"\text{if } x", r"\mathrm{if} x"),
    (r"\operatorname{var}(X)", r"\mathrm{var}(X)"),
    (r"\exp\{x\}", r"\mathrm{exp}\{x\}"),
    (r"\begin{aligned} a &= b \\ &\quad c \end{aligned}", r"a = b \\ c"),
    (r"\begin{array}{r c l} a & = & b \end{array}", r"a = b"),
    (r"\begin{cases} a & b \\ c & d \end{cases}", r"\{ a b \\ c d"),
    (r"\begin{pmatrix} 1 \\ 0 \end{pmatrix}", r"( 1 \\ 0 )"),
    (r"\underset{i}{\min} f", r"\min_{i} f"),
    (r"f'(x)", r"f^{\prime}(x)"),
    (r"x^{2}", r"x^2"),
    (r"\frac{1}{2}", r"\dfrac{1}{2}"),
    (r"a \, b \; c \quad d \qquad e ~ f", "abcdef"),
    (r"\displaystyle \sum\limits_{i} x", r"\sum_i x"),
    (r"a \\[2ex] b", r"a \\ b"),
    (r"\ldots", r"\dots"),
    (r"\widehat{x} \overline{y} \widetilde{z}", r"\hat{x} \bar{y} \tilde{z}"),
    (r"\mbox{\boldmath$\xi$}", r"\mathbf{\xi}"),
    (r"a \left. b", "a b"),
    (r"\sum_{\substack{i<j}}", r"\sum_{i<j}"),
    (r"x \mid y \vert z", "x | y | z"),
    (r"\lbrace x \rbrace", r"\{ x \}"),
    (r"\textstyle a \nonumber", "a"),
    # notation only (MATH_3): prime and script order, double primes, a prime on a star
    (r"\hat{\mu}'_{20}", r"\hat{\mu}_{20}^{\prime}"),
    (r"\mu'^{2}_{10}", r"\mu_{10}^{\prime 2}"),
    (r"q''(v)", r"q^{\prime\prime}(v)"),
    (r"\alpha_i'^2", r"\alpha_{i}^{\prime 2}"),
    (r"a^{*\prime}", r"a^{*^{\prime}}"),
    (r"e^{-\alpha'_2 x_2}", r"e ^ {- \alpha_ {2} ^ {\prime} x _ {2}}"),
    (r"X \overset{d}{=} Y", r"X \stackrel{{d}}{{=}} Y"),
    (r"s = \phantom{-}1", "s = 1"),
    (r"\binom{m_1}{m_2}", r"\begin{pmatrix} m_1 \\ m_2 \end{pmatrix}"),
    (r"0, \text{otherwise,}", r"0, \text{otherwise},"),
    (r"\prod_{\substack{i \\ J_i=1}} h", r"\prod_{\stackrel{i}{J_i=1}} h"),
])
def test_normalization_makes_these_equal(a, b):
    assert norm(a) == norm(b)


@pytest.mark.parametrize("a, b", [
    (r"x^2", r"x^3"),
    (r"e^{-x}", r"e^{x}"),
    (r"\cdots", r"\dots"),                 # kept distinct (cosmetic, but not equal)
    (r"\mathbf{x}", "x"),
    (r"2^{-(1/2)\sum_j v_j}", r"2^{-(1/2)} \sum_j v_j"),
    (r"\sum\sum_{i<j}", r"\sum_{i<j}"),
    (r"x'", "x"),
    (r"x''", "x'"),
    (r"x'^2", "x'_2"),
    (r"a^{*\prime}", r"a^{*}"),
    (r"\binom{n}{k}", r"(n k)"),
    (r"\overset{d}{=}", r"\overset{=}{d}"),
])
def test_normalization_keeps_these_apart(a, b):
    assert norm(a) != norm(b)


def test_token_list_shape():
    assert L.tokens(r"\frac{a+b}{2}") == [r"\frac", "{", "a", "+", "b", "}", "2"]
    assert L.tokens(r"\Pr[X > x]") == [r"\mathrm", "{", "P", "r", "}", "[", "X", ">", "x", "]"]
    assert L.tokens(r"a \\ \\") == ["a"]              # trailing line breaks dropped


# ---------------------------------------------------------------- tags

@pytest.mark.parametrize("latex, trailing, body, tag", [
    (r"x = y \tag{47.12}", None, "x = y", "47.12"),
    (r"x = y \tag*{47.12a}", None, "x = y", "47.12a"),
    (r"x = y", "47.12", "x = y", "47.12"),
    (r"x = y \qquad (47.12)", None, "x = y", "47.12"),
    (r"\begin{aligned} x &= y, \\ &\quad (47 . 13) \end{aligned}", None, None, "47.13"),
    (r"f(x) = (1 - p)", None, r"f(x) = (1 - p)", None),
    (r"x = y", None, "x = y", None),
])
def test_split_tag(latex, trailing, body, tag):
    got_body, got_tag = L.split_tag(latex, trailing)
    assert got_tag == tag
    if body is not None:
        assert norm(got_body) == norm(body)


# ---------------------------------------------------------------- value-only (loose) comparison

def test_value_only_ignores_markup_breaks_dots_and_trailing_punctuation():
    a = norm(r"\mathbf{x} \cdots \mathrm{y} \\ z.")
    assert L.value_only(a) == L.value_only(norm(r"x \dots y z"))
    assert L.value_only(norm(r"x, y")) != L.value_only(norm(r"x y"))   # only trailing ones
    assert L.value_only(norm(r"x^2")) != L.value_only(norm(r"x^3"))


# ---------------------------------------------------------------- distance

def _dp(a, b):
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]


def test_distance_matches_dynamic_programming_on_random_input():
    rng = random.Random(7)
    for _ in range(300):
        a = "".join(rng.choice("abcd") for _ in range(rng.randint(0, 70)))
        b = "".join(rng.choice("abcd") for _ in range(rng.randint(0, 70)))
        assert L.distance(a, b) == _dp(a, b)
    toks_a = [r"\sum", "_", "i", "x", "^", "2"]
    toks_b = [r"\sum", "_", "j", "x", "^", "2", "+", "1"]
    assert L.distance(toks_a, toks_b) == _dp(toks_a, toks_b) == 3
    assert L.distance("", "") == 0 and L.distance("abc", "") == 3


def test_distance_on_long_strings_is_fast_enough():
    a = "the quick brown fox " * 200
    b = "the quick brown fix " * 200
    assert L.distance(a, b) == 200
    assert L.ned(a, b) == pytest.approx(200 / len(a))


def test_editops_and_cosmetic_classification():
    ops = L.editops(["a", "b", "c"], ["a", "x", "c"])
    assert ops == [("replace", "b", "x")]
    assert L.editops(["a"], ["a", "b"]) == [("insert", "", "b")]
    assert L.editops(["a", "b"], ["a"]) == [("delete", "b", "")]
    assert L.is_cosmetic(L.editops(L.tokens(r"a + \cdots"), L.tokens(r"a + \dots")))
    assert L.is_cosmetic(L.editops(L.tokens(r"\mathbf{x} + y,"), L.tokens(r"x + y")))
    assert not L.is_cosmetic(L.editops(L.tokens(r"a - b"), L.tokens(r"a + b")))
    assert not L.is_cosmetic([])          # an exact match is not a near-miss


# ---------------------------------------------------------------- markdown extraction

PAGE = r"""<!-- page printed="54" file="73" -->
Some prose with \(x_1\) and $y^2$ inline, then a display

$$
a = b
\tag{44.74}
$$

and one with the number after it

$$
c = d
$$ (44.75)

<table><tr><td>1</td></tr></table>

Hyphen-
ated words and *emphasis*.
"""


def test_extract_displays_inline_and_prose():
    displays, inline, prose = L.extract(PAGE)
    assert [(d.strip(), t) for d, t in displays] == [(r"a = b" + "\n" + r"\tag{44.74}", None),
                                                     ("c = d", "44.75")]
    assert inline == ["x_1", "y^2"]
    assert "a = b" not in prose and "Some prose with" in prose
    p = L.prose_norm(prose)
    assert "<table>" not in p and "Hyphenated words" in p and "emphasis" in p
    assert "page printed" not in p


def test_display_bodies_split_tag_after_extract():
    displays, _, _ = L.extract(PAGE)
    assert L.split_tag(*displays[0])[1] == "44.74"
    assert L.split_tag(*displays[1])[1] == "44.75"


# ---------------------------------------------------------------- syntax helpers

def test_commands_and_balance():
    assert L.commands(r"\sum_{i} \frac{a}{b} \operatorname*{max}") == {
        "sum", "frac", "operatorname*"}
    assert L.balance_problems(r"\left( x \right)") == []
    assert L.balance_problems(r"\left( x") == ["\\left without \\right"]
    assert L.balance_problems(r"x \right)") == ["\\right without \\left"]
    assert L.balance_problems(r"{x") == ["unbalanced braces"]
    assert L.balance_problems(r"x}") == ["unbalanced braces"]
    assert L.balance_problems(r"\begin{array}{l} x") == ["\\begin{array} without \\end"]
    assert L.balance_problems(r"x \end{aligned}") == ["\\end{aligned} without \\begin"]
    assert L.balance_problems(r"\{ x \}") == []
    assert L.balance_problems(r"\begin{cases} a \\ b \end{cases} {") == ["unbalanced braces"]
    assert L.balance_problems(r"\left\{ x \right.") == []        # a lone \{ is legal
