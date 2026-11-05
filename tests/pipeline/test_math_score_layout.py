# SPDX-License-Identifier: AGPL-3.0-or-later
"""math-score's layout errors: a multi-line display whose row breaks were lost or added,
and a display the candidate set as inline math, counted apart from value and style
errors (they are not cosmetic)."""

import json

from gpdf.pipeline.mathocr import score

ROWS = "\\begin{aligned} a &= b + c \\\\ d &= e \\end{aligned}"
REF = ('<!-- page printed="1" file="1" -->\n\nFirst.\n\n$$\n' + ROWS + "\n\\tag{1.1}\n$$\n\n"
       "Then\n\n$$\ng(t) = e^{t} - 1\n\\tag{1.2}\n$$\n\nand\n\n$$\nu = v\n$$\n\nLast.\n")


def write(folder, md):
    (folder / "pages").mkdir(parents=True)
    (folder / "pages" / "p001.md").write_text(md, "utf-8")
    (folder / "pages" / "p001.flags.json").write_text("[]", "utf-8")


def run(tmp_path, cand_md):
    write(tmp_path / "ref", REF)
    write(tmp_path / "cand", cand_md)
    summary, _ = score.run(tmp_path / "ref", tmp_path / "cand", tmp_path / "out")
    return summary, json.loads((tmp_path / "out" / "score.json").read_text("utf-8"))


def test_the_same_layout_has_no_layout_error(tmp_path):
    cand = REF.replace("\\end{aligned}", "\\\\ \\end{aligned}")       # a trailing break
    s, _ = run(tmp_path, cand)
    assert s["layout_errors"] == 0 and s["layout_row_breaks"] == 0
    assert s["layout_display_as_inline"] == 0


def test_a_collapsed_row_break_is_a_layout_error_not_a_cosmetic_pass(tmp_path):
    s, res = run(tmp_path, REF.replace("c \\\\ d", "c \\ d"))
    assert s["layout_row_breaks"] == 1 and s["layout_errors"] == 1
    assert res["layout"][0]["kind"] == "row_breaks" and res["layout"][0]["row_breaks"] == [1, 0]
    assert "## Layout errors: 1" in (tmp_path / "out" / "REPORT.md").read_text("utf-8")


def test_an_added_row_break_is_a_layout_error(tmp_path):
    s, _ = run(tmp_path, REF.replace("$$\nu = v\n$$", "$$\n\\begin{aligned} u \\\\ = v \\end{aligned}\n$$"))
    assert s["layout_row_breaks"] == 1


def test_a_display_set_inline_is_a_layout_error(tmp_path):
    cand = REF.replace("Then\n\n$$\ng(t) = e^{t} - 1\n\\tag{1.2}\n$$\n\nand",
                       "Then \\(g(t) = e^{t} - 1\\) and")
    s, res = run(tmp_path, cand)
    assert s["missed"] == 1
    assert s["layout_display_as_inline"] == 1 and s["layout_errors"] == 1
    assert res["layout"][0]["kind"] == "display_as_inline"
