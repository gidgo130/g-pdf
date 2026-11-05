# SPDX-License-Identifier: AGPL-3.0-or-later
"""gpdf-cli math-check-pages on hand-written pages: unbalanced $$ and \\( \\), a lone `\\`
where a row break was meant, a multi-line environment without row breaks, braces,
\\begin/\\end and \\left/\\right, flags files that do not parse; numbers only on screen."""

import json

from gpdf import cli
from gpdf.pipeline.mathocr import check

GOOD = ('<!-- page printed="1" file="10" -->\n\nLet \\(x_1\\) be real.\n\n'
        "$$\n\\begin{aligned}\na &= b \\\\\nc &= d\n\\end{aligned}\n\\tag{1.1}\n$$\n\n"
        "$$\n\\left( a \\right) = \\{ b \\}\n$$\n")


def write(folder, pages, flags=None):
    folder.mkdir(parents=True, exist_ok=True)
    for stem, md in pages.items():
        (folder / f"{stem}.md").write_text(md, "utf-8")
        if flags is None or stem in flags:
            content = (flags or {}).get(stem, "[]")
            (folder / f"{stem}.flags.json").write_text(content, "utf-8")
    return folder


def kinds(res):
    return sorted((p["page"], p["kind"]) for p in res["problems"])


def test_a_clean_page_has_no_problems(tmp_path):
    res = check.check_pages(write(tmp_path / "p", {"p010": GOOD}))
    assert res["pages"] == 1 and res["problems"] == []


def test_each_structural_problem_is_found(tmp_path):
    pages = {
        "p011": GOOD + "\n$$\nx = 1\n",                                     # odd $$
        "p012": GOOD + "\nand \\(y + 1 and more prose.\n",                  # \( without \)
        "p013": ("$$\n\\begin{aligned}\na &= b \\\nc &= d\n\\end{aligned}\n$$\n"
                 "$$\n\\begin{array}{ll}\nx & y \\[2ex]\nz & w\n\\end{array}\n$$\n"),
        "p014": "$$\n\\begin{cases}\n1 & x > 0\n0 & x \\le 0\n\\end{cases}\n$$\n",
        "p015": "$$\n\\frac{a}{b\n$$\n\n$$\n\\left( a\n$$\n\n$$\n\\begin{aligned} a \\end{array}\n$$\n",
        "p016": "Inline \\(\\left( x\\) and \\(\\frac{1}{2\\).\n",
    }
    flags = {s: "[]" for s in pages}
    flags["p012"] = '[{"type": "page_anomaly", "note": "a \\sigma here"}]'    # not JSON (\s)
    flags["p013"] = '{"type": "dropped"}'                                   # not a list
    del flags["p016"]
    res = check.check_pages(write(tmp_path / "p", pages, flags))
    got = kinds(res)
    assert ("p011", "display_delimiters") in got
    assert ("p012", "inline_delimiters") in got
    assert got.count(("p013", "single_backslash_break")) == 2
    assert ("p014", "no_row_breaks") in got
    assert {("p015", "braces"), ("p015", "left_right"), ("p015", "begin_end")} <= set(got)
    assert {("p016", "left_right"), ("p016", "braces")} <= set(got)
    assert ("p012", "flags_json") in got and ("p013", "flags_json") in got
    assert ("p016", "flags_missing") in got
    assert res["counts"]["single_backslash_break"] == 2


def test_the_trailing_double_backslash_and_escaped_braces_are_fine(tmp_path):
    md = ("$$\n\\begin{array}{l}\na \\\\\nb \\\\[1ex]\nc \\\\\n\\end{array}\n$$\n\n"
          "Set \\(\\{x\\}\\) and \\(\\left\\{ y \\right.\\).\n")
    assert check.check_pages(write(tmp_path / "p", {"p020": md}))["problems"] == []


def test_cli_counts_on_screen_without_formulas_and_json_out(tmp_path, capsys):
    folder = write(tmp_path / "p", {"p030": "$$\n\\frac{secret}{x\n$$\n", "p031": GOOD})
    out = tmp_path / "check.json"
    assert cli.main(["math-check-pages", str(folder), "--out", str(out)]) == 2
    screen = capsys.readouterr().out
    assert "braces: 1" in screen and "secret" not in screen and "p030" in screen
    assert json.loads(out.read_text("utf-8"))["counts"]["braces"] == 1
    assert cli.main(["math-check-pages", str(write(tmp_path / "q", {"p031": GOOD}))]) == 0
    assert cli.main(["math-check-pages", str(tmp_path / "missing")]) == 1


def test_an_export_folder_is_read_through_its_pages_subfolder(tmp_path):
    write(tmp_path / "exp" / "pages", {"p040": GOOD})
    assert check.check_pages(tmp_path / "exp")["pages"] == 1


def test_a_text_md_is_split_by_its_page_markers(tmp_path):
    text = tmp_path / "text.md"
    text.write_text(GOOD + '\n<!-- page printed="2" file="11" -->\n\n$$\n\\frac{a}{b\n$$\n', "utf-8")
    res = check.check_pages(text)
    assert res["pages"] == 2 and kinds(res) == [("p011", "braces")]
