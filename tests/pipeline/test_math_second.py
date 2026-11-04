# SPDX-License-Identifier: AGPL-3.0-or-later
"""The basic tier as a second opinion: pairing the two tiers' displays, and stripping
|...| bars that wrap a whole display only when the second tier has no such bars."""

from gpdf.pipeline.mathocr import align, second
from gpdf.pipeline.mathocr.labels import Label
from gpdf.pipeline.mathocr.assemble import assemble


def EQ(tex, y=0.5):
    return {"type": "equation", "bbox": [0.2, y, 0.8, y + 0.06], "content": tex}


def page(*texs, file=508):
    return assemble([EQ(t, 0.1 + 0.1 * i) for i, t in enumerate(texs)], file, Label("490", None))


# ---------------------------------------------------------------- pairing

def test_pairs_by_tag_first_then_by_reading_order_and_similarity():
    pg = page(r"a = b \tag{1.1}", r"x^2 + y^2 = z^2", r"c = d \tag{1.2}", r"q = r")
    basic = [r"c = d \tag{1.2}", r"x^{2} + y^{2} = z^{2}", r"a = b \tag{1.1}", r"totally other"]
    pairs = second.pair_displays(pg.displays, basic)
    assert [p for _, p in pairs] == [r"a = b \tag{1.1}", r"x^{2} + y^{2} = z^{2}",
                                     r"c = d \tag{1.2}", None]


def test_a_display_the_second_tier_split_in_two_is_joined():
    pg = page(r"F(x) = 1 - e^{-x} + \frac{x^2}{2} - \frac{x^3}{6}")
    basic = [r"F(x) = 1 - e^{-x}", r"+ \frac{x^2}{2} - \frac{x^3}{6}"]
    pairs = second.pair_displays(pg.displays, basic)
    assert pairs[0][1] == "F(x) = 1 - e^{-x}\n+ \\frac{x^2}{2} - \\frac{x^3}{6}"


def test_align_matches_the_trial_scorer_shape():
    ref = [{"toks": ["a", "=", "b"], "tag": "1"}, {"toks": ["x"], "tag": None}]
    cand = [{"toks": ["x"], "tag": None}, {"toks": ["a", "=", "b"], "tag": "1"}]
    pairs, missed, extra = align.align(ref, cand)
    assert sorted(pairs) == [([0], [1]), ([1], [0])] and missed == [] and extra == []


# ---------------------------------------------------------------- bars

def test_bars_round_a_whole_display_are_stripped_when_the_second_tier_has_none():
    pg = page(r"\left| Y _ {j} = \frac {X _ {j}}{X _ {0}} \quad (j = 1, \dots , k) \right|",
              r"\left| \sum_ {j} d _ {j} = 1. \right.\tag{49.43}")
    basic = [r"Y_{j} = \frac{X_{j}}{X_{0}} \quad (j = 1, \dots, k)", r"\sum_{j} d_{j} = 1.\tag{49.43}"]
    changed = second.strip_bars(pg, second.pair_displays(pg.displays, basic))
    assert changed == 2
    assert [d.latex for d in pg.displays] == [
        r"Y _ {j} = \frac {X _ {j}}{X _ {0}} \quad (j = 1, \dots , k)", r"\sum_ {j} d _ {j} = 1."]
    assert pg.displays[1].tag == "49.43"
    assert pg.render().count("$$\n\\sum_ {j} d _ {j} = 1.\n\\tag{49.43}\n$$") == 1
    fixes = [f for f in pg.flags if f["type"] == "auto_fix"]
    assert fixes[0] == {"type": "auto_fix", "what": "bars", "eq": "display 1",
                        "before": r"\left| Y _ {j} = \frac {X _ {j}}{X _ {0}} \quad (j = 1, \dots , k) \right|",
                        "after": r"Y _ {j} = \frac {X _ {j}}{X _ {0}} \quad (j = 1, \dots , k)"}
    assert fixes[1]["eq"] == "display 2 (49.43)"


def test_bars_stay_when_the_second_tier_has_them_too_or_is_missing():
    pg = page(r"\left| Y = 1 \right|", r"\left| Z = 2 \right|", r"\left| W = 3 \right|")
    basic = [r"\left| Y = 1 \right|", r"| Z = 2 |"]           # third has no counterpart
    assert second.strip_bars(pg, second.pair_displays(pg.displays, basic)) == 0
    assert [d.latex for d in pg.displays] == [r"\left| Y = 1 \right|", r"\left| Z = 2 \right|",
                                              r"\left| W = 3 \right|"]
    assert not [f for f in pg.flags if f["type"] == "auto_fix"]


def test_real_absolute_values_are_never_touched():
    pg = page(r"x = \left| y \right|", r"\left| y \right| = \left| z \right|",
              r"\left| a \right| + b")
    basic = ["x = y", "y = z", "a + b"]
    assert second.strip_bars(pg, second.pair_displays(pg.displays, basic)) == 0
    assert pg.displays[0].latex == r"x = \left| y \right|"
