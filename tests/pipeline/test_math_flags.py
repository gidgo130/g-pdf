# SPDX-License-Identifier: AGPL-3.0-or-later
"""The review-queue flag rules, each with positive and negative cases: tier disagreement,
style, stacked operators, syntax, tag sequence, inline alignment; the review items and
their crops."""

import pymupdf
import pytest

from gpdf.pipeline.mathocr import flags
from gpdf.pipeline.mathocr import latex as L
from gpdf.pipeline.mathocr.assemble import assemble
from gpdf.pipeline.mathocr.labels import Label


def EQ(tex, y=0.5):
    return {"type": "equation", "bbox": [0.2, y, 0.8, y + 0.06], "content": tex}


def P(*parts, y=0.3):
    content = [{"type": "equation_inline", "content": p[1]} if isinstance(p, tuple)
               else {"type": "text", "content": p} for p in parts]
    return {"type": "text", "bbox": [0.1, y, 0.9, y + 0.05], "content": content}


def page(blocks, file=73, printed="54"):
    return assemble(blocks, file, Label(printed, None))


# ---------------------------------------------------------------- 1. disagreement

@pytest.mark.parametrize("a, b, flagged", [
    (r"x^2", r"x^3", True),
    (r"a - b", r"a + b", True),
    (r"\sum\sum_{i<j}", r"\sum_{i<j}", True),
    (r"2^{-(1/2)\sum_j v_j}", r"2^{-(1/2)} \sum_j v_j", True),
    (r"\mathbf{x} + y", r"x + y", False),             # markup only
    (r"a \\ b", r"a b", False),                       # line break only
    (r"a + \cdots + b,", r"a + \dots + b", False),    # dots and trailing punctuation
    (r"\left( x \right)", r"(x)", False),
    (r"x", None, True),                               # no second reading
])
def test_display_disagreement(a, b, flagged):
    assert flags.disagree(a, b) == flagged


# ---------------------------------------------------------------- 2. style

def test_style_only_difference():
    assert flags.style_only(r"\mathbf{x} + y", r"x + y")
    assert flags.style_only(r"\mathcal{F}(t)", r"F(t)")
    assert not flags.style_only(r"x^2", r"x^3")
    assert not flags.style_only(r"x", r"x")
    assert not flags.style_only(r"x", None)


def test_a_symbol_both_bold_and_not_on_one_page():
    pg = page([EQ(r"S_{k\mathbf{1}_r} = 1", 0.2), EQ(r"Q_{k1_r}(\pi) = 2", 0.3),
               P("with ", ("eq", r"\mathbf{X}"), " and ", ("eq", r"X_1"), y=0.4)])
    assert flags.bold_mixed(pg) == ["1_r"]            # X vs X_1 differ in their scripts
    pg = page([EQ(r"\mathbf{x}^T \mathbf{y}", 0.2), EQ(r"f(x)", 0.3)])
    assert flags.bold_mixed(pg) == ["x"]
    pg = page([EQ(r"\boldsymbol{\xi} + \xi_1", 0.2)])
    assert flags.bold_mixed(pg) == []


# ---------------------------------------------------------------- 3. stacked operators

@pytest.mark.parametrize("tex, reasons", [
    (r"\sum_{1 \le i_1 < i_2 \le k} a_{i_1 i_2}", ["relation in the limits",
                                                   "two index variables (i_1, i_2)"]),
    (r"\sum_{i,j} x_{ij}", ["two index variables (i, j)"]),
    (r"\sum_{j_1 + \dots + j_v = n} c", ["a sum of indices"]),
    (r"\prod_{i \ne j} (1 - x_i)", ["relation in the limits", "two index variables (i, j)"]),
    (r"\sum_{i_1 \neq \cdots \neq i_k = 1}^{k} y", ["relation in the limits",
                                                   "two index variables (i_1, i_k)"]),
    (r"\int_{x < y} f", ["relation in the limits", "two index variables (x, y)"]),
    (r"\sum_{i=1}^{n} x_i", []),
    (r"\sum_{j=0}^{m} v_j \sum_{r=1}^{k} \lambda_{i_r}", []),
    (r"\int_0^\infty e^{-x} dx", []),
    (r"\sum_{i \in S} x_i", []),
    (r"\sum_{\substack{i<j}} x", ["relation in the limits", "two index variables (i, j)"]),
    (r"a_{i,j} + b_{1 < 2}", []),                     # not under an operator
])
def test_stacked_operator_limits(tex, reasons):
    assert flags.stacked(tex) == reasons


# ---------------------------------------------------------------- 4. syntax

@pytest.mark.parametrize("tex, reasons", [
    (r"\frac{a}{b", ["unbalanced braces"]),
    (r"\left( x", ["\\left without \\right"]),
    (r"\begin{array}{l} x", ["\\begin{array} without \\end"]),
    (r"\foo{x} + \barx", ["unknown command \\foo", "unknown command \\barx"]),
    (r"\left\{ \begin{array}{ll} 0 & \text{if } x < 0 \\ 1 & \text{otherwise} \end{array} \right.", []),
    (r"\begin{pmatrix} 1 & 0 \\ 0 & 1 \end{pmatrix}", []),
    (r"\operatorname*{max}_{i} \boldsymbol{\xi}^{\langle 2 \rangle} \pmb{x} \dot{a}", []),
    (r"\bar{F}_{X_1,X_2}(x_1, x_2) \doteq \Pr[X > x] \quad \text{and} \quad \ell", []),
])
def test_syntax_rule(tex, reasons):
    assert flags.syntax(tex) == reasons


# ---------------------------------------------------------------- 5. tags

def test_tag_sequence_within_a_run():
    seq = [("p1", 0, "47.12"), ("p1", 1, "47.13"), ("p2", 0, "47.13"), ("p2", 1, "47.15"),
           ("p3", 0, "47.14"), ("p3", 1, "47.16a"), ("p3", 2, "47.16b"), ("p3", 3, "48.1"),
           ("p4", 0, "48.2"), ("p4", 1, None), ("p4", 2, "48.3")]
    assert flags.tag_problems(seq) == {
        ("p2", 0): "tag 47.13 repeats", ("p2", 1): "tag 47.15 skips 47.14",
        ("p3", 0): "tag 47.14 goes backwards after 47.15"}
    assert flags.tag_problems([("p1", 0, "A.1"), ("p1", 1, "A.1")]) == {("p1", 1): "tag A.1 repeats"}
    assert flags.tag_problems([("p1", 0, "47.19"), ("p2", 0, "48.1")]) == {}


# ---------------------------------------------------------------- 6. inline math

def test_inline_spans_are_aligned_paragraph_by_paragraph():
    prim = page([P("Let ", ("eq", r"x_1"), " and ", ("eq", r"\sigma^2"), " be", y=0.2),
                 P("Then ", ("eq", r"E[X] = \mu"), ".", y=0.4)])
    basic = [P("Let ", ("eq", r"x_{1}"), " and ", ("eq", r"\sigma^{2}"), " be", y=0.2),
             P("Then ", ("eq", r"E[X] = \mu,"), ".", y=0.4)]
    pairs = flags.inline_pairs(prim, basic)
    assert [(m.paragraph, m.span, m.ok) for m in pairs] == [(0, 0, True), (0, 1, True),
                                                           (1, 0, True)]
    assert pairs[2].second == r"E[X] = \mu,"
    basic = [P("Let ", ("eq", r"x_{2}"), " and ", ("eq", r"\sigma^{2}"), " be", y=0.2),
             P("Then nothing.", y=0.4)]
    pairs = flags.inline_pairs(prim, basic)
    assert [(m.paragraph, m.span, m.ok) for m in pairs] == [(0, 0, False), (0, 1, True),
                                                           (1, 0, False)]
    assert pairs[0].second == "x_{2}" and pairs[2].second is None


def test_paragraphs_are_paired_by_position_not_order():
    prim = page([P("A ", ("eq", "a"), y=0.2), P("B ", ("eq", "b"), y=0.6)])
    basic = [P("B ", ("eq", "b"), y=0.6), P("A ", ("eq", "a"), y=0.2)]
    assert [m.ok for m in flags.inline_pairs(prim, basic)] == [True, True]


# ---------------------------------------------------------------- the queue

@pytest.fixture
def pdf(tmp_path):
    path = tmp_path / "book.pdf"
    doc = pymupdf.open()
    for i in range(2):
        pg = doc.new_page(width=300, height=400)
        pg.insert_text((40, 60), f"page {i + 1}", fontsize=14)
        pg.draw_rect(pymupdf.Rect(60, 200, 240, 224), color=(0, 0, 0), width=2)
    doc.save(str(path))
    doc.close()
    return path


def test_review_items_carry_ids_reasons_both_readings_and_crops(pdf, tmp_path):
    pg = page([EQ(r"x^2 = 1 \tag{3.1}", 0.5), EQ(r"\left( y \right) = 2", 0.6),
               EQ(r"\sum_{i<j} z \tag{3.3}", 0.7),
               P("inline ", ("eq", r"a_1"), y=0.2)], file=1, printed="7")
    basic = {"displays": [r"x^3 = 1 \tag{3.1}", r"(y) = 2"],
             "paragraphs": [P("inline ", ("eq", r"a_2"), y=0.2)]}
    items = flags.review_items(pg, basic, runs_tags={("p001", 2): "tag 3.3 skips 3.2"})
    by_id = {it.id: it for it in items}
    assert list(by_id) == ["p001-d01", "p001-d03", "p001-i01"]
    d1 = by_id["p001-d01"]
    assert (d1.kind, d1.reasons, d1.tag) == ("disagree", ["tiers disagree"], "3.1")
    assert d1.latex_primary == "x^2 = 1" and d1.latex_second == "x^3 = 1"
    assert d1.file == 1 and d1.printed == "7"
    d3 = by_id["p001-d03"]
    assert d3.kind == "disagree"
    assert d3.reasons == ["no second reading", "relation in the limits",
                          "two index variables (i, j)", "tag 3.3 skips 3.2"]
    assert d3.latex_second is None
    i1 = by_id["p001-i01"]
    assert (i1.kind, i1.latex_primary, i1.latex_second) == ("inline", "a_1", "a_2")
    doc = pymupdf.open(str(pdf))
    flags.write_crops(doc, items, tmp_path / "crops", dpi=200)
    doc.close()
    assert d1.crop == "crops/p001-d01.png" and (tmp_path / "crops" / "p001-d01.png").stat().st_size > 0
    assert i1.crop == "crops/p001-i01.png"
    rec = d1.record()
    assert rec["id"] == "p001-d01" and rec["crop"] == "crops/p001-d01.png"
    assert set(rec) >= {"id", "file", "printed", "kind", "reasons", "tag", "latex_primary",
                        "latex_second", "crop"}


def test_grouped_inline_items_make_one_item_per_page():
    pg = page([P("a ", ("eq", "x"), " b ", ("eq", "y"), y=0.2), P("c ", ("eq", "z"), y=0.5)],
              file=2)
    basic = {"displays": [], "paragraphs": [P("a ", ("eq", "x'"), " b ", ("eq", "y"), y=0.2),
                                            P("c ", ("eq", "z'"), y=0.5)]}
    items = flags.review_items(pg, basic, group_inline=True)
    assert [it.id for it in items] == ["p002-inline"]
    it = items[0]
    assert it.kind == "inline" and it.spans == [
        {"paragraph": 1, "latex_primary": "x", "latex_second": "x'"},
        {"paragraph": 2, "latex_primary": "z", "latex_second": "z'"}]
    assert it.record()["spans"] == it.spans


def test_the_syntax_whitelist_covers_standard_math():
    missing = {c for c in ("alpha", "varepsilon", "Gamma", "infty", "partial", "cdots", "ldots",
                           "left", "right", "frac", "sqrt", "binom", "sum", "prod", "int",
                           "bigcup", "mathbf", "mathrm", "boldsymbol", "pmb", "operatorname",
                           "operatorname*", "text", "tag", "begin", "end", "hat", "bar", "tilde",
                           "dot", "ell", "langle", "rangle", "le", "leq", "ge", "geq", "ne", "neq",
                           "to", "quad", "qquad", "exp", "log", "max", "min", "lim", "Pr",
                           "substack", "underset", "stackrel", "atop", "doteq", "otimes")
               if c not in flags.WHITELIST}
    assert missing == set()
    assert L.commands(r"\foo") - flags.WHITELIST == {"foo"}
