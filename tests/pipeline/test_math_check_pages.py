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


def test_control_characters_and_orphaned_escape_fragments(tmp_path):
    # a helper that wrote "\b" or "\t" through a string escape leaves a control character
    # (U+0008, U+0009) or, once that is dropped, the rest of the command: "oldsymbol", "heta"
    pages = {
        "p020": GOOD + "\n$$\n\x08oldsymbol{x} = 1\n$$\n",              # backspace control character
        "p021": GOOD + "\nThe angle \\(heta\\) and \\(\\alpha\\).\n",    # \theta lost its \t
        "p022": GOOD + "\n$$\n\\frac{1}{2} imes rac{a}{b} + x_ilde{y}\n$$\n",
        "p023": GOOD + "\nTab\there.\n",
        "p024": GOOD + "\nThe theta, beta, \\mathbf{x}, \\vartheta, \\boldsymbol{y}, the rights; "
                       "a weight; \\(\\tilde{x} \\times \\varepsilon \\rightarrow 0\\).\n",
    }
    res = check.check_pages(write(tmp_path / "p", pages))
    assert kinds(res) == [("p020", "control_character"), ("p021", "escape_fragment"),
                          ("p022", "escape_fragment"), ("p022", "escape_fragment"),
                          ("p022", "escape_fragment"), ("p023", "control_character")]
    assert "U+0008" in res["problems"][0]["detail"]
    assert res["counts"]["escape_fragment"] == 4
    assert check.check_md("x \x0c y")[0][0] == "control_character"
    assert check.check_md(GOOD) == []


# ---------------------------------------------------------------- pages without a file number
# A page marker with a `src` attribute names a supplementary image (file="") or a sub-PDF (file=
# the page within that PDF); such a marker starts its own page, keyed by (src, file).

SRC_TEXT = (GOOD
            + '\n<!-- page printed="64" file="" src="book-p064-hi.png" -->\n\n$$\n\\frac{a}{b\n$$\n'
            + '\n<!-- page printed="65" file="" src="book-p065-hi.png" -->\n\nText \\(y\\).\n'
            + '\n<!-- page printed="" file="2" src="chart-a.pdf" -->\n\n$$\n\\left( a\n$$\n'
            + '\n<!-- page printed="" file="2" src="chart-b.pdf" -->\n\nPlain.\n'
            + '\n<!-- page printed="11" file="11" -->\n\nNo problem here.\n')


def test_a_src_marker_starts_its_own_page_keyed_by_src_and_file(tmp_path):
    text = tmp_path / "text.md"
    text.write_text(SRC_TEXT, "utf-8")
    res = check.check_pages(text)
    assert res["pages"] == 6
    pages = {p["page"] for p in res["problems"]}
    # the problem on the image page and on the sub-PDF page are theirs, not file 10's
    assert kinds(res) == [("book-p064-hi.png#", "braces"), ("chart-a.pdf#p002", "left_right")]
    assert "p010" not in pages


def test_markers_without_src_behave_as_before(tmp_path):
    from gpdf.pipeline.mathocr.score import _pages_from_text

    # a marker with an empty file and no src is not a page marker (its text stays in the page before)
    old = GOOD + '\n<!-- page printed="64" file="" image="x.png" -->\n\n$$\n\\frac{a}{b\n$$\n'
    mds = _pages_from_text(old)
    assert list(mds) == ["p010"] and "x.png" in mds["p010"]
    text = tmp_path / "text.md"
    text.write_text(old, "utf-8")
    res = check.check_pages(text)
    assert res["pages"] == 1 and kinds(res) == [("p010", "braces")]


def test_src_pages_can_be_scored_and_cross_checked_without_error(tmp_path):
    from gpdf.pipeline.mathocr import score

    text = tmp_path / "text.md"
    text.write_text(SRC_TEXT, "utf-8")
    mds, flags, _ = score.read_pages(text)
    assert set(mds) == {"p010", "book-p064-hi.png#", "book-p065-hi.png#", "chart-a.pdf#p002",
                        "chart-b.pdf#p002", "p011"}
    units = score.load_units(mds, flags)
    assert units["book-p064-hi.png#"]["printed"] == "64"
    from gpdf.pipeline.mathocr import crosscheck

    # cross-check entries address file pages: the supplementary pages are left out, the file pages kept
    assert sorted(crosscheck.load_pages(text)) == [10, 11]
