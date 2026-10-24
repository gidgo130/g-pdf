# SPDX-License-Identifier: AGPL-3.0-or-later
"""gpdf-cli manual-check: page-cited extracts checked against the layer-1 page records and the
PDF. A tiny manual is built here with PyMuPDF (a cover without a page number, four numbered
pages with a warning, steps, an NC listing, a ruled table with a merged cell and a figure),
run through manual-pages, then extracts that pass and ones that fail are checked."""

import json
import shutil

import pymupdf
import pytest

from gpdf import cli
from gpdf.pipeline import manual_check, manuals

W, H = 612, 792
PDF_NAME = "Acme Mill Operator’s 2020.pdf"
SLUG = "acme-mill"


def _text(page, x, y, lines, size=10, font="helv"):
    for i, t in enumerate(lines):
        page.insert_text((x, y + i * size * 1.3), t, fontname=font, fontsize=size)


def _manual(path):
    doc = pymupdf.open()
    cover = doc.new_page(width=W, height=H)
    cover.insert_text((72, 300), "Acme Mill Operator's Manual", fontname="hebo", fontsize=24)
    for n in range(1, 5):
        page = doc.new_page(width=W, height=H)
        page.insert_text((300, 770), "Operation", fontname="hebo", fontsize=10)
        page.insert_text((55, 770), str(n), fontname="helv", fontsize=9)
    p = doc[1]
    p.insert_text((72, 110), "Safety", fontname="hebo", fontsize=18)
    _text(p, 72, 140, ["Always wear safety glasses. Keep hands clear of the",
                       "spindle while it turns."])
    _text(p, 72, 190, ["WARNING: Never open the doors while the spindle is turning."])
    p = doc[2]
    _text(p, 72, 110, ["1. Press POWER UP.", "2. Jog the table to the center."])
    _text(p, 72, 170, ["The spindle runs up to 6,000 rpm. The table holds 350 lb."])
    _text(p, 72, 220, ["G54 G00 X0 Y0", "M30"], font="cour")
    p = doc[3]
    x, y = [72, 172, 272, 372], [100, 124, 148, 172, 196]

    def rule(a, b):
        p.draw_line(a, b, color=(0, 0, 0), width=0.7)

    p.draw_rect(pymupdf.Rect(x[0], y[0], x[-1], y[-1]), color=(0, 0, 0), width=0.7)
    rule((x[0], y[1]), (x[-1], y[1]))
    rule((x[0], y[2]), (x[1], y[2]))            # column 1 spans rows 1-2 ("85")
    rule((x[2], y[2]), (x[3], y[2]))
    rule((x[0], y[3]), (x[-1], y[3]))
    rule((x[1], y[0]), (x[1], y[-1]))
    rule((x[2], y[0]), (x[2], y[-1]))
    printed = [["Thick", "Amps", "Speed"], ["10", "85", "0.5"], ["12", None, "0.7"],
               ["16", "45", "1.0"]]
    for r, row in enumerate(printed):
        for c, t in enumerate(row):
            if t:
                p.insert_text((x[c] + 4, y[r] + 16), t, fontname="helv", fontsize=9)
    p = doc[4]
    rect = pymupdf.Rect(72, 200, 300, 350)
    p.draw_rect(rect, color=(0, 0, 0), width=1)
    p.draw_line((77, 205), (295, 345), color=(0, 0, 0))
    p.insert_text((150, 260), "Spindle", fontname="helv", fontsize=9)
    p.insert_text((82, 366), "Figure 1. The spindle head.", fontname="helv", fontsize=10)
    doc.save(str(path))
    doc.close()


@pytest.fixture(scope="module")
def setup(tmp_path_factory):
    root = tmp_path_factory.mktemp("mc")
    src = root / "src"
    src.mkdir()
    _manual(src / PDF_NAME)
    config = {"source": str(src),
              "manuals": [{"slug": SLUG, "file": PDF_NAME, "maker": "Acme", "model": "Mill",
                           "doctype": "Operator's Manual", "doc_no": "AM-1", "rev": "B",
                           "date": "2020", "control": None, "machines": ["mill"], "pages": None,
                           "notes": ""}]}
    cfg = root / "manuals.json"
    cfg.write_text(json.dumps(config), "utf-8")
    pages = root / "pages"
    manuals.run(cfg, pages, dpi=72, log=lambda *a: None)
    return root, cfg, pages


@pytest.fixture
def ex(setup, tmp_path):
    root, cfg, pages = setup
    d = tmp_path / "extracts"
    (d / "figures").mkdir(parents=True)
    shutil.copy(pages / "figures" / f"{SLUG}_pdf005_f1.png", d / "figures")
    return d


HEADER = """# Acme Mill: safety

Title: Acme Mill Operator's Manual
Manufacturer: Acme
Document number / revision / date: AM-1 / B / 2020
Control generation (Haas): n/a
Models and years covered: Mill
Source PDF filename: Acme Mill Operator’s 2020.pdf
Pages extracted (printed page numbers): 1-4
Notes on applicability to our machines: all
"""

GOOD = HEADER + """
[Figure: cover, no text to quote — figures/acme-mill_pdf005_f1.png] [p. 4 / pdf 5]

Acme Mill Operator's Manual [pdf 1]

## Safety

Always wear safety glasses. Keep hands clear of the spindle while it turns. [p. 1 / pdf 2]

Always wear safety glasses. … while it turns. [NOTE: a remark of my own] [p. 1 / pdf 2]

> **WARNING:** Never open the doors while the spindle is turning. [p. 1 / pdf 2]

[p. 2 / pdf 3]

1. Press POWER UP.
2. Jog the table to the center. [GAP: none]

*Summary:* The spindle turns at up to 6000 rpm, and the table takes “350 lb.” [p. 2 / pdf 3]

[p. 2 / pdf 3]
```
G54 G00 X0 Y0
M30
```

### Cut chart [p. 3 / pdf 4]

| Thick | Amps | Speed |
|---|---|---|
| 10 | 85 | 0.5 |
| 12 | 85 | 0.7 |
| 12 |  | 0.7 |
| 16 | 45 | 1.0 |

spindle while it turns. … The table holds 350 lb. [p. 1–2 / pdf 2–3]
"""


def _check(ex, setup, name, text):
    root, cfg, pages = setup
    (ex / name).write_text(text, "utf-8")
    checker = manual_check.Checker(ex, pages, cfg)
    try:
        return checker.check_file(ex / name)
    finally:
        checker.close()


NAME = "ACME_MILL_OPERATOR_AM-1_RevB_safety.md"


def _good():
    return GOOD


def _rules(res):
    return [(e["rule"], e["message"]) for e in res.errors]


def test_a_faithful_extract_passes(ex, setup):
    res = _check(ex, setup, NAME, _good())
    assert res.errors == [], res.errors
    assert res.source == SLUG


def test_a_changed_word_fails(ex, setup):
    res = _check(ex, setup, NAME, _good().replace("Always wear safety glasses. Keep",
                                                  "Always wear safety goggles. Keep"))
    assert [e["rule"] for e in res.errors] == ["verbatim"]
    assert "goggles" in res.errors[0]["quote"] and res.errors[0]["line"]
    res = _check(ex, setup, NAME, _good().replace("the center.", "the centre."))
    assert [e["rule"] for e in res.errors] == ["verbatim"]


def test_a_wrong_pdf_page_fails(ex, setup):
    text = _good().replace("while it turns. [p. 1 / pdf 2]\n\nAlways",
                           "while it turns. [p. 2 / pdf 3]\n\nAlways")
    res = _check(ex, setup, NAME, text)
    assert [e["rule"] for e in res.errors] == ["verbatim"]
    assert res.errors[0]["cite"] == "[p. 2 / pdf 3]"


def test_a_wrong_printed_label_fails(ex, setup):
    res = _check(ex, setup, NAME, _good().replace("turning. [p. 1 / pdf 2]", "turning. [p. 5 / pdf 2]"))
    assert _rules(res) == [("tag", "pdf 2 is printed p. 1, not p. 5")]
    res = _check(ex, setup, NAME, _good().replace("Manual [pdf 1]", "Manual [p. 1 / pdf 1]"))
    assert _rules(res) == [("tag", "pdf 1 has no printed label: tag it [pdf 1]")]
    res = _check(ex, setup, NAME, _good().replace("turning. [p. 1 / pdf 2]", "turning. [pdf 2]"))
    assert _rules(res) == [("tag", "pdf 2 has the printed label 1: tag it [p. 1 / pdf 2]")]


def test_an_untagged_block_fails(ex, setup):
    res = _check(ex, setup, NAME, _good().replace(" [p. 1 / pdf 2]\n\n> ", "\n\n> ", 1))
    assert _rules(res) == [("tag", "no page tag")]


def test_a_made_up_number_in_a_summary_fails(ex, setup):
    res = _check(ex, setup, NAME, _good().replace("up to 6000 rpm", "up to 8,000 rpm"))
    assert _rules(res) == [("number", "number 8,000 not on the cited page")]
    res = _check(ex, setup, NAME, _good().replace("“350 lb.”", "“350 pounds”"))
    assert [e["rule"] for e in res.errors] == ["verbatim"]


def test_a_table_value_in_the_wrong_row_fails_and_a_merged_cell_passes(ex, setup):
    res = _check(ex, setup, NAME, _good().replace("| 16 | 45 | 1.0 |", "| 16 | 45 | 0.7 |"))
    assert _rules(res) == [("table", "row is not a row of a table on the cited page")]
    assert res.errors[0]["quote"] == "16 | 45 | 0.7"
    # the merged cell accepts the value above it ("85"), but not one from another row
    res = _check(ex, setup, NAME, _good().replace("| 12 | 85 | 0.7 |", "| 12 | 45 | 0.7 |"))
    assert [e["rule"] for e in res.errors] == ["table"]
    # a column left out must be empty in the row
    res = _check(ex, setup, NAME, _good().replace("| 16 | 45 | 1.0 |", "| 16 | 1.0 |"))
    assert [e["rule"] for e in res.errors] == ["table"]
    res = _check(ex, setup, NAME, _good().replace("| Thick | Amps |", "| Thickness | Amps |"))
    assert _rules(res) == [("table", "header word 'Thickness' not on the cited page")]


def test_a_missing_figure_file_fails(ex, setup):
    (ex / "figures" / f"{SLUG}_pdf005_f1.png").unlink()
    res = _check(ex, setup, NAME, _good())
    assert _rules(res) == [("figure", "figures/acme-mill_pdf005_f1.png not found")]


def test_a_figure_from_another_page_fails(ex, setup):
    res = _check(ex, setup, NAME, _good().replace("[p. 4 / pdf 5]", "[p. 3 / pdf 4]", 1))
    assert _rules(res) == [("figure", "figures/acme-mill_pdf005_f1.png is not from the cited pdf page")]


def test_a_bad_file_name_fails(ex, setup):
    for bad in ("acme_MILL_OPERATOR_AM-1_RevB_safety.md", "ACME_MILL_OPERATOR_AM-1_Rev B_safety.md",
                "ACME_MILL_OPERATOR_AM-1_RevB_Safety.md", "ACME_MILL_AM-1_RevB_safety.md"):
        res = _check(ex, setup, bad, _good())
        assert [e["rule"] for e in res.errors] == ["name"], bad
    assert _check(ex, setup, "HAAS_MINI-MILL_OPERATOR-ADDENDUM_ES0205l_RevNA_safety-and-power.md",
                  _good()).errors == []


def test_a_missing_header_line_fails(ex, setup):
    res = _check(ex, setup, NAME, _good().replace("Manufacturer: Acme\n", ""))
    assert _rules(res) == [("header", "header line 'Manufacturer:' missing or out of order")]
    res = _check(ex, setup, NAME, _good().replace("Control generation (Haas): n/a", "Control generation (Haas): new"))
    assert [e["rule"] for e in res.errors] == ["header"]
    res = _check(ex, setup, NAME, _good().replace("Operator’s 2020.pdf", "Operator's 2020.pdf"))
    assert _rules(res) == [("header", "source PDF 'Acme Mill Operator's 2020.pdf' is not a file in the config")]


def test_ellipsis_fragments_must_keep_their_order(ex, setup):
    res = _check(ex, setup, NAME, _good().replace("Always wear safety glasses. … while it turns.",
                                                  "while it turns. … Always wear safety glasses."))
    assert [e["rule"] for e in res.errors] == ["verbatim"]


def test_a_code_line_not_on_the_page_fails(ex, setup):
    res = _check(ex, setup, NAME, _good().replace("G54 G00 X0 Y0", "G55 G00 X0 Y0"))
    assert [(e["rule"], e["line"]) for e in res.errors] == [("verbatim", _good().splitlines().index("G54 G00 X0 Y0") + 1)]


def test_an_unruled_table_on_a_page_without_a_table_is_checked_cell_by_cell(ex, setup):
    extra = "\n[p. 2 / pdf 3]\n\n| spindle | table |\n|---|---|\n| 1 | Press POWER UP. |\n"
    res = _check(ex, setup, NAME, _good() + extra)
    assert res.errors == [] and [w["rule"] for w in res.warnings] == ["table"]
    res = _check(ex, setup, NAME, _good() + extra.replace("Press POWER UP.", "Press POWER DOWN."))
    assert [e["rule"] for e in res.errors] == ["table"]
    # an omission inside a cell is an omission, as in a paragraph
    res = _check(ex, setup, NAME, _good() + extra.replace("Press POWER UP.", "Press … UP."))
    assert res.errors == []
    res = _check(ex, setup, NAME, _good() + extra.replace("Press POWER UP.", "UP. … Press"))
    assert [e["rule"] for e in res.errors] == ["table"]


def test_markup_and_glued_words_in_table_cells_warn(ex, setup):
    """Leftovers of PyMuPDF's table Markdown: <br>, emphasis markup, a lowercase letter glued
    to "[" or to an upper-case run that the page shows as two words."""
    extra = "\n[p. 2 / pdf 3]\n\n| spindle | table |\n|---|---|\n| 1 | Press POWER UP. |\n"
    assert _check(ex, setup, NAME, _good() + extra).warnings[-1]["message"].startswith("unanchored")
    for cell, what in (("Press<br>POWER UP.", "<br>"), ("Press **POWER** UP.", "emphasis"),
                       ("Press _POWER_ UP.", "emphasis"), ("Press[POWER UP.", "'['"),
                       ("PressPOWER UP.", "two words")):
        res = _check(ex, setup, NAME, _good() + extra.replace("Press POWER UP.", cell))
        glitch = [w for w in res.warnings if not w["message"].startswith("unanchored")]
        assert len(glitch) == 1 and what in glitch[0]["message"], (cell, res.warnings)
        assert glitch[0]["line"] == len((_good() + extra).splitlines())
    # footnote asterisks printed on the page are not markup
    res = _check(ex, setup, NAME, _good() + extra.replace("Press POWER UP.", "Press POWER UP.**"))
    assert not [w for w in res.warnings if "emphasis" in w["message"]]
    # an anchored table (pdf 4) is looked at too
    res = _check(ex, setup, NAME, _good().replace("| 16 | 45 | 1.0 |", "| 16 | 45<br> | 1.0 |"))
    assert res.errors == [] and ["<br>" in w["message"] for w in res.warnings] == [True]


def test_findings_name_the_manual_in_every_tag(ex, setup):
    good = ("# Findings\n\n## 1. Spindle speed\n\n- *Summary:* 6,000 rpm "
            f"[{SLUG} p. 2 / pdf 3]\n- [GAP: not found in {SLUG}]\n")
    assert _check(ex, setup, "01_FINDINGS.md", good).errors == []
    res = _check(ex, setup, "01_FINDINGS.md", good.replace(f"[{SLUG} p. 2", "[p. 2"))
    assert [e["rule"] for e in res.errors] == ["tag"]


def test_cli_reports_and_exits_1_on_a_failing_file(ex, setup, tmp_path, capsys):
    root, cfg, pages = setup
    (ex / NAME).write_text(_good(), "utf-8")
    (ex / "00_MANIFEST.md").write_text(f"| {NAME} |\n| MISSING_FILE_X_Y_Z_RevA_gone.md |\n", "utf-8")
    report = tmp_path / "check"
    args = ["manual-check", "--extracts", str(ex), "--pages", str(pages), "--config", str(cfg),
            "--report", str(report)]
    assert cli.main(args) == 0
    data = json.loads((report / "check.json").read_text("utf-8"))
    files = {f["file"]: f for f in data["files"]}
    assert files[NAME]["status"] == "pass"
    assert [w["rule"] for w in files["00_MANIFEST.md"]["warnings"]] == ["manifest"]
    bad = "ACME_MILL_OPERATOR_AM-1_RevB_bad.md"
    (ex / bad).write_text(_good().replace("goggles", "x").replace("glasses. Keep", "goggles. Keep"), "utf-8")
    assert cli.main(args) == 1
    md = (report / "check.md").read_text("utf-8")
    assert f"## {bad}: fail" in md and "goggles" in md
    assert "1 fail" in capsys.readouterr().out
    assert sorted(p.name for p in ex.iterdir()) == sorted([NAME, bad, "00_MANIFEST.md", "figures"])
    # only the files named; the report must not go into the extracts folder
    assert cli.main(args + [NAME]) == 0
    assert cli.main(args[:-1] + [str(ex / "check")]) == 1
