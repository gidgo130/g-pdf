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
    pg = page([EQ(r"\mathbf{x} = \mathbf{0}", 0.2), EQ(r"x_1 = 0", 0.3)])
    assert flags.bold_mixed(pg) == []                 # the zero vector against a scalar 0


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
        {"paragraph": 1, "latex_primary": "x", "latex_second": "x'", "bbox": [0.1, 0.2, 0.9, 0.25],
         "at": 0.357},
        {"paragraph": 2, "latex_primary": "z", "latex_second": "z'",
         "bbox": [0.1, 0.5, 0.9, 0.55], "at": 0.833}]
    assert it.record()["spans"] == it.spans


def test_grouped_inline_items_get_a_crop_per_text_line(tmp_path):
    path = tmp_path / "lines.pdf"
    doc = pymupdf.open()
    pg = doc.new_page(width=300, height=400)
    for k in range(3):
        pg.insert_text((40, 100 + 16 * k), "a line of text with words", fontsize=11)
    doc.save(str(path))
    doc.close()
    doc = pymupdf.open(str(path))
    bbox = [0.1, 85 / 400, 0.9, 155 / 400]
    boxes = flags.line_boxes(doc, 1, bbox)
    assert len(boxes) == 3 and all(b[1] < b[3] for b in boxes)
    assert boxes[0][3] <= boxes[1][1] <= boxes[1][3] <= boxes[2][1]
    assert boxes[0][0] == 0.1 and boxes[0][2] == 0.9
    it = flags.Item("p001-inline", 1, "1", "inline", ["x"], None, None, None, bbox=bbox,
                    bboxes=[bbox], spans=[])
    flags.write_crops(doc, [it], tmp_path / "crops", dpi=100)
    doc.close()
    assert it.crops == ["crops/p001-inline-1.png", "crops/p001-inline-2.png",
                        "crops/p001-inline-3.png"] and it.crop == it.crops[0]
    assert all((tmp_path / c).stat().st_size > 0 for c in it.crops)
    assert flags.line_boxes(pymupdf.open(str(path)), 1, [0.1, 0.5, 0.9, 0.6]) == [
        [0.1, 0.5, 0.9, 0.6]]                        # blank region: the box itself


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


# ---------------------------------------------------------------- MATH_3: style opt-in

def test_the_page_consistency_style_rule_is_opt_in():
    # bold X for a vector and X_1 for its component is normal multivariate notation
    pg = page([EQ(r"\mathbf{x}^T \mathbf{y} = 1", 0.2), EQ(r"f(x) = 2", 0.3)], file=5)
    same = {"displays": [r"\mathbf{x}^T \mathbf{y} = 1", r"f(x) = 2"], "paragraphs": []}
    assert flags.review_items(pg, same) == []
    items = flags.review_items(pg, same, style_page=True)
    assert [(it.id, it.kind) for it in items] == [("p005-d01", "style"), ("p005-d02", "style")]
    # engine disagreement on style still flags without the opt-in
    other = {"displays": [r"x^T y = 1", r"f(x) = 2"], "paragraphs": []}
    assert [(it.id, it.kind) for it in flags.review_items(pg, other)] == [("p005-d01", "style")]


# ---------------------------------------------------------------- MATH_3: Marker

@pytest.mark.parametrize("a, b, any_, value, small", [
    (r"\mathbf{x} + y", r"x + y", True, False, False),          # style only
    (r"a + \cdots + b", r"a + \dots + b", True, False, False),  # dot style only
    (r"x^2 + y", r"x^3 + y", True, True, True),                 # one token
    (r"a + b + c + d", r"p - q \cdot r / s + t", True, True, False),   # many tokens
    (r"x + y", r"x+y", False, False, False),
    (r"x", None, False, False, False),                          # Marker has no reading
])
def test_marker_rule_variants(a, b, any_, value, small):
    assert [flags.marker_disagrees(a, b, r) for r in flags.MARKER_RULES] == [any_, value, small]


def test_marker_disagreement_makes_or_joins_an_item():
    pg = page([EQ(r"e^{(1/2) t'Rt} \tag{2.1}", 0.3), EQ(r"x^2 = 1", 0.5), EQ(r"y = 2", 0.7)],
              file=6)
    basic = {"displays": [r"e^{(1/2) t'Rt} \tag{2.1}", r"x^3 = 1", r"y = 2"], "paragraphs": []}
    marker = {"displays": [r"e^{(1/2)} t'Rt \tag{2.1}", r"x^4 = 1", r"y = 2"], "paragraphs": []}
    stats = {}
    items = flags.review_items(pg, basic, third=marker, stats=stats)
    by_id = {it.id: it for it in items}
    assert list(by_id) == ["p006-d01", "p006-d02"]
    d1, d2 = by_id["p006-d01"], by_id["p006-d02"]
    assert (d1.kind, d1.reasons) == ("marker", ["marker disagrees"])    # the tiers agree
    assert d1.record()["latex_marker"] == r"e^{(1/2)} t'Rt"
    assert (d2.kind, d2.reasons) == ("disagree", ["tiers disagree", "marker disagrees"])
    assert stats == {"no_marker_reading": [], "inline_flagged": 0}
    assert flags.review_items(pg, basic, third=marker, marker_rule="small")[0].id == "p006-d01"


def test_marker_rule_value_is_the_default():
    # a many-token difference (a dropped side-condition line) flags by default
    pg = page([EQ(r"f(x) = e^{-x} 	ag{3.1}", 0.3)], file=7)
    basic = {"displays": [r"f(x) = e^{-x} 	ag{3.1}"], "paragraphs": []}
    marker = {"displays": [r"f(x) = e^{-x}, \quad 0 < x < y < z 	ag{3.1}"], "paragraphs": []}
    assert [it.kind for it in flags.review_items(pg, basic, third=marker)] == ["marker"]
    assert flags.review_items(pg, basic, third=marker, marker_rule="small") == []


def test_missing_display_from_marker_the_basic_tier_and_the_layout():
    blocks = [EQ(r"a = b \tag{4.1}", 0.2), EQ("", 0.4), EQ(r"c = d \tag{4.3}", 0.8)]
    pg = page(blocks, file=8)
    assert len(pg.displays) == 2                     # the empty block gave no display
    marker = {"displays": [r"a = b \tag{4.1}", r"\int_0^1 f(x)\,dx = F(1) - F(0) \tag{4.2}",
                           r"c = d \tag{4.3}"],
              "boxes": [[0.2, 0.2, 0.8, 0.26], [0.2, 0.5, 0.8, 0.56], [0.2, 0.8, 0.8, 0.86]],
              "paragraphs": []}
    basic = {"displays": [r"a = b \tag{4.1}", r"\int_0^1 f(x) dx = F(1) - F(0)",
                          r"c = d \tag{4.3}"], "paragraphs": []}
    items = flags.review_items(pg, basic, third=marker, std_blocks=blocks)
    miss = [it for it in items if it.kind == "missing_display"]
    assert [(it.id, it.reasons[0]) for it in miss] == [
        ("p008-m01", "Marker has a display the standard tier lacks"),
        ("p008-m02", "equation block in the layout gave no display")]
    assert miss[0].latex_primary is None and miss[0].tag == "4.2"
    assert miss[0].latex_second.startswith(r"\int_0^1") and miss[0].bbox == [0.2, 0.5, 0.8, 0.56]
    assert miss[1].bbox == [0.2, 0.4, 0.8, 0.46]
    # without Marker the basic tier's extra display is the one reported
    items = flags.review_items(pg, basic)
    assert [(it.id, it.reasons[0]) for it in items if it.kind == "missing_display"] == [
        ("p008-m01", "the basic tier has a display the standard tier lacks")]


@pytest.mark.parametrize("mode, flagged", [
    ("basic", ["x", "z"]), ("marker", ["y", "z"]), ("either", ["x", "y", "z"]), ("both", ["z"]),
])
def test_inline_flags_against_basic_marker_either_or_both(mode, flagged):
    pg = page([P("a ", ("eq", "x"), " b ", ("eq", "y"), " c ", ("eq", "z"), y=0.2)], file=9)
    basic = [P("a ", ("eq", "x'"), " b ", ("eq", "y"), " c ", ("eq", "z'"), y=0.2)]
    marker = [P("a ", ("eq", "x"), " b ", ("eq", "y'"), " c ", ("eq", "z''"), y=0.2)]
    spans = flags.inline_flagged(pg, basic, marker, mode)
    assert [s["latex_primary"] for s in spans] == flagged
    assert all(set(s) == {"paragraph", "latex_primary", "latex_second", "latex_marker", "bbox",
                          "at"} for s in spans)
    # a page Marker did not read falls back to the basic tier
    assert [s["latex_primary"] for s in flags.inline_flagged(pg, basic, None, mode)] == ["x", "z"]
    items = flags.review_items(pg, {"displays": [], "paragraphs": basic},
                               third={"displays": [], "paragraphs": marker}, group_inline=True,
                               inline_with=mode)
    assert items[0].reasons == [f"{len(flagged)} inline spans differ ({mode})"]


def test_each_flagged_span_gets_a_crop_of_its_own_line_and_the_lines_around_it(tmp_path):
    path = tmp_path / "lines.pdf"
    doc = pymupdf.open()
    pg = doc.new_page(width=300, height=400)
    for k in range(6):
        pg.insert_text((40, 100 + 16 * k), "a line of text with words", fontsize=11)
    doc.save(str(path))
    doc.close()
    doc = pymupdf.open(str(path))
    bbox = [0.1, 85 / 400, 0.9, 200 / 400]
    lines = flags.line_boxes(doc, 1, bbox)
    assert len(lines) == 6
    spans = [{"paragraph": 1, "latex_primary": "a", "bbox": bbox, "at": 0.0},
             {"paragraph": 1, "latex_primary": "b", "bbox": bbox, "at": 0.55},
             {"paragraph": 1, "latex_primary": "c", "bbox": None, "at": 0.5}]
    it = flags.Item("p001-inline", 1, "1", "inline", ["x"], None, None, None, bbox=bbox,
                    bboxes=[bbox], spans=spans)
    flags.write_crops(doc, [it], tmp_path / "crops", dpi=100)
    doc.close()
    first, middle, nobox = it.spans
    assert first["crop"] == "crops/p001-inline-s1.png" and (tmp_path / first["crop"]).is_file()
    assert first["line_box"] == [0.1, lines[0][1], 0.9, lines[1][3]]      # no line above
    assert middle["line_box"] == [0.1, lines[2][1], 0.9, lines[4][3]]     # line 4 of 6, +-1
    assert "crop" not in nobox and "line_box" not in nobox
    assert len(it.crops) == 6                       # the per-line crops are still there
