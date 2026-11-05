# SPDX-License-Identifier: AGPL-3.0-or-later
"""math-apply-review's structure check: a display replace that loses row breaks, alignment
points, environments or \\left/\\right pairs, unbalances braces, or writes a stray `\\ `
where a `\\\\` row break was, is refused unless it says structure_change with a reason."""

import json

import pytest

from gpdf.pipeline.mathocr import latex as L
from gpdf.pipeline.mathocr import review

OLD = "\\begin{aligned} a &= b + c \\\\ d &= \\left( e \\right) \\end{aligned}"
PAGE = ('<!-- page printed="10" file="1" -->\n\nText.\n\n$$\n' + OLD
        + "\n\\tag{1.1}\n$$\n\nMore text.\n")
ITEMS = [
    {"id": "p001-d01", "file": 1, "printed": "10", "kind": "disagree", "reasons": ["x"],
     "tag": "1.1", "latex_primary": OLD, "latex_second": None, "crop": None},
    {"id": "p001-m01", "file": 1, "printed": "10", "kind": "missing_display", "reasons": ["x"],
     "tag": None, "latex_primary": None, "latex_second": "y", "crop": None},
]


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "book"
    (r / "pages").mkdir(parents=True)
    (r / "pages" / "p001.md").write_text(PAGE, "utf-8")
    (r / "pages" / "p001.flags.json").write_text("[]", "utf-8")
    (r / "review.jsonl").write_text("".join(json.dumps(i) + "\n" for i in ITEMS), "utf-8")
    return r


def run(root, tmp_path, *decisions):
    path = tmp_path / "decisions.jsonl"
    path.write_text("".join(json.dumps(d) + "\n" for d in decisions), "utf-8")
    return review.apply(root, path)


def replace(latex, **kw):
    return {"id": "p001-d01", "action": "replace", "latex": latex, **kw}


def page(root):
    return (root / "pages" / "p001.md").read_text("utf-8")


def test_row_breaks_ignore_a_trailing_break_and_substack_rows():
    assert L.row_breaks("\\begin{aligned} a \\\\ b \\\\ \\end{aligned}") == 1
    assert L.row_breaks("a \\\\[2pt] b \\\\") == 1
    assert L.row_breaks("\\sum_{\\substack{i < j \\\\ k}} x") == 0
    assert L.row_breaks("a \\ b") == 0
    # a break followed only by \hline rules and \end is trailing too
    assert L.row_breaks("\\begin{array}{l} a \\\\ \\hline b \\\\ \\hline \\end{array}") == 1


def test_a_backslash_space_where_a_row_break_was_is_refused(root, tmp_path):
    bad = OLD.replace("c \\\\ d", "c \\ d")
    rep = run(root, tmp_path, replace(bad))
    assert not rep.applied
    reason = rep.refused[0]["reason"]
    assert "row breaks" in reason and "stray" in reason
    assert page(root) == PAGE


@pytest.mark.parametrize("new, needle", [
    (OLD.replace("a &= b", "a = b"), "alignment"),
    (OLD.replace("\\left( e \\right)", "( e )"), "\\left"),
    (OLD.replace("b + c", "{b + c"), "braces"),
    ("a = b + c, \\quad d = (e)", "environment"),
])
def test_a_replace_that_loses_structure_is_refused(root, tmp_path, new, needle):
    rep = run(root, tmp_path, replace(new))
    assert not rep.applied and needle in rep.refused[0]["reason"]


@pytest.mark.parametrize("new", [
    OLD.replace("b + c", "b - c"),                                   # a value change only
    OLD.replace("{aligned}", "{array}{rl}", 1).replace("end{aligned}", "end{array}"),
    OLD.replace("\\end{aligned}", "\\\\ \\end{aligned}"),            # a trailing break
    OLD.replace("d &=", "d &= f \\\\ &="),                           # a row added
])
def test_a_replace_that_keeps_the_structure_goes_through(root, tmp_path, new):
    rep = run(root, tmp_path, replace(new))
    assert [a["id"] for a in rep.applied] == ["p001-d01"], rep.refused
    assert new in page(root)


def test_an_intended_structure_change_needs_a_reason(root, tmp_path):
    flat = "a = b + c, \\quad d = \\left( e \\right)"
    rep = run(root, tmp_path, replace(flat, structure_change=True))
    assert not rep.applied and "reason" in rep.refused[0]["reason"]
    rep = run(root, tmp_path, replace(flat, structure_change=True,
                                      reason="printed on one line"))
    assert rep.applied and flat in page(root)
    rec = [f for f in json.loads((root / "pages" / "p001.flags.json").read_text("utf-8"))
           if f.get("type") == "reviewed"][0]
    assert rec["structure_change"] is True and rec["reason"] == "printed on one line"
    assert not run(root, tmp_path, replace(flat, structure_change=True,
                                           reason="printed on one line")).applied


def test_a_missing_display_that_is_unbalanced_is_refused(root, tmp_path):
    rep = run(root, tmp_path, {"id": "p001-m01", "action": "replace", "latex": "x = {y",
                               "after": "More text."})
    assert not rep.applied and "braces" in rep.refused[0]["reason"]


def test_an_rcl_array_rewritten_as_aligned_keeps_its_alignment(root, tmp_path):
    rcl = "\\begin{array}{r c l} a & = & b + c \\\\ d & = & \\left( e \\right) \\end{array}"
    assert review.structure_problems(rcl, OLD) == []
    assert "alignment" in " ".join(review.structure_problems(rcl, OLD.replace("d &=", "d =")))
