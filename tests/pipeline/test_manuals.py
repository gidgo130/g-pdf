# SPDX-License-Identifier: AGPL-3.0-or-later
"""gpdf-cli manual-pages: page-stamped text of born-digital equipment manuals, on synthetic
PDFs built here with PyMuPDF (two columns with figures, a ruled table, dot-leader specs,
a contents page, running heads with page numbers, InDesign page labels, a WARNING box,
steps with the number alone on its line, soft hyphens, control glyphs, a hidden span)."""

import json

import pymupdf
import pytest

from gpdf import cli
from gpdf.pipeline import manuals

W, H = 612, 792
BODY = 10


def _lines(page, x, y, lines, size=BODY, font="helv", pitch=None):
    pitch = pitch or size * 1.3
    for i, t in enumerate(lines):
        page.insert_text((x, y + i * pitch), t, fontname=font, fontsize=size)
    return y + len(lines) * pitch


def _figure(page, rect, labels, caption=None):
    """A vector drawing (box + diagonal) with callout labels inside and a caption below."""
    page.draw_rect(rect, color=(0, 0, 0), width=1)
    page.draw_line((rect.x0 + 5, rect.y0 + 5), (rect.x1 - 5, rect.y1 - 5), color=(0, 0, 0))
    for (lx, ly), text in labels:
        page.insert_text((lx, ly), text, fontname="helv", fontsize=9)
    if caption:
        page.insert_text((rect.x0 + 10, rect.y1 + 16), caption, fontname="helv", fontsize=BODY)


def _two_column_page(doc):
    page = doc.new_page(width=W, height=H)
    # left column: heading, tools list, steps, figure, step
    page.insert_text((48, 70), "Chuck Removal", fontname="hebo", fontsize=24)
    page.insert_text((48, 95), "Tools Needed:", fontname="hebo", fontsize=BODY)
    page.insert_text((265, 95), "Qty", fontname="hebo", fontsize=BODY)
    _lines(page, 48, 110, ["Spanner Wrench............................... 1",
                           "Chuck Key (3- or 4-Jaw)..................... 1"])
    _lines(page, 48, 150, ["1.  DISCONNECT LATHE FROM POWER!"])
    _lines(page, 48, 170, ["2.  Remove the cap screws and chuck locks"])
    _lines(page, 68, 183, ["(see Figure 28)."])
    _figure(page, pymupdf.Rect(48, 300, 290, 420),
            [((176, 330), "Cap Screw"), ((71, 400), "Chuck Lock")],
            "Figure 28. Location of chuck lock and cap screws.")
    _lines(page, 48, 470, ["3.  Support the chuck, unscrew it, and remove."])
    # right column: heading, paragraph, figure with labels, paragraph
    page.insert_text((324, 70), "Changing Jaw Set", fontname="hebo", fontsize=24)
    _lines(page, 324, 130, ["The 3-jaw scroll chuck included with the lathe fea-",
                            "tures inside and outside hardened steel jaw sets",
                            "(see Figure 30), which move in unison to center",
                            "a concentric workpiece."])
    _figure(page, pymupdf.Rect(325, 266, 565, 443),
            [((491, 310), "Inside Set"), ((336, 400), "Outside Set")],
            "Figure 30. Chuck and jaw selection.")
    _lines(page, 324, 480, ["Jaws are numbered from 1-3 (see Figure 31).",
                            "They are designed to be installed in numerical",
                            "order in the jaw guides."])
    return page


def _blocks(rec, kind=None):
    return [b for b in rec["blocks"] if kind is None or b["kind"] == kind]


@pytest.fixture
def out(tmp_path):
    d = tmp_path / "out"
    d.mkdir()
    return d


def _process(doc, tmp_path, out, slug="synth", **kw):
    path = tmp_path / f"{slug}.pdf"
    doc.save(str(path))
    doc.close()
    return manuals.process_pdf(path, slug, out / "figures", **kw)


# ------------------------------------------------------------------ reading order, figures
def test_two_columns_read_left_then_right_with_figures(tmp_path, out):
    doc = pymupdf.open()
    _two_column_page(doc)
    _, recs = _process(doc, tmp_path, out, dpi=72)
    rec = recs[0]
    kinds = [b["kind"] for b in rec["blocks"]]
    texts = [b.get("text", "") for b in rec["blocks"]]
    assert rec["columns"] == 2
    # left column first: heading, tools (specs), steps 1-2, figure 28, step 3; then the right
    i_left = texts.index("Chuck Removal")
    i_right = texts.index("Changing Jaw Set")
    assert i_left < i_right
    figs = _blocks(rec, "figure")
    assert [f["caption"] for f in figs] == ["Figure 28. Location of chuck lock and cap screws.",
                                            "Figure 30. Chuck and jaw selection."]
    assert figs[0]["labels"] == ["Cap Screw", "Chuck Lock"]
    assert figs[1]["labels"] == ["Inside Set", "Outside Set"]
    for f in figs:
        assert (out / f["file"]).is_file()
    steps = _blocks(rec, "step")
    assert [s["n"] for s in steps] == ["1", "2", "3"]
    assert steps[1]["text"] == "Remove the cap screws and chuck locks (see Figure 28)."
    # the figure sits between step 2 and step 3
    order = [b["kind"] for b in rec["blocks"][i_left:i_right]]
    assert order.index("figure") > order.index("step")
    assert rec["blocks"][i_right - 1]["kind"] == "step"
    assert rec["blocks"][i_right - 1]["n"] == "3"
    # labels and captions are not body text
    body = " ".join(b.get("text", "") for b in rec["blocks"] if b["kind"] not in ("figure",))
    for label in ("Cap Screw", "Chuck Lock", "Inside Set", "Outside Set", "Figure 28."):
        assert label not in body
    # tools list is a specs block; the hyphenated line joins without a space
    specs = _blocks(rec, "specs")
    assert specs[0]["items"][0] == {"label": "Spanner Wrench", "values": ["1"]}
    paras = [b["text"] for b in _blocks(rec, "para")]
    assert any("chuck included with the lathe features inside" in p for p in paras)
    assert "heading" in kinds


# ------------------------------------------------------------------------------ tables
def test_ruled_table_becomes_markdown_and_leaves_the_flow(tmp_path, out):
    doc = pymupdf.open()
    page = doc.new_page(width=W, height=H)
    page.insert_text((72, 60), "Beacon Light", fontname="hebo", fontsize=11)
    rows = [("Flashing Green", "The machine is stopped, but is in a ready state."),
            ("Flashing Red", "A fault has occurred."),
            ("Flashing Yellow", "A tool has expired.")]
    x0, x1, xm, y0 = 72, 540, 220, 70
    for i, (a, b) in enumerate(rows):
        y = y0 + i * 24
        page.draw_rect(pymupdf.Rect(x0, y, x1, y + 24), color=(0, 0, 0), width=0.7)
        page.draw_line((xm, y), (xm, y + 24), color=(0, 0, 0), width=0.7)
        page.insert_text((x0 + 4, y + 16), a, fontname="helv", fontsize=9)
        page.insert_text((xm + 4, y + 16), b, fontname="helv", fontsize=9)
    page.insert_text((72, 180), "2.3.3 Keyboard", fontname="hebo", fontsize=15)
    _lines(page, 72, 200, ["Keyboard keys are grouped into these functional areas:"])
    _, recs = _process(doc, tmp_path, out, dpi=72)
    rec = recs[0]
    tables = _blocks(rec, "table")
    assert len(tables) == 1
    md = tables[0]["text"]
    assert "Flashing Green" in md and "Flashing Yellow" in md and "|" in md
    assert tables[0]["rows"] >= 3 and tables[0]["cols"] == 2
    kinds = [b["kind"] for b in rec["blocks"]]
    assert kinds.index("table") < [b.get("text") for b in rec["blocks"]].index("2.3.3 Keyboard")
    body = " ".join(b.get("text", "") for b in rec["blocks"] if b["kind"] != "table")
    assert "Flashing Red" not in body
    assert not _blocks(rec, "figure")          # the table ruling is not a figure
    assert "merged" not in tables[0]
    assert tables[0]["cells"][0] == ["Flashing Green", "The machine is stopped, but is in a ready state."]


def test_merged_cells_stay_empty_and_are_kept_as_null(tmp_path, out):
    """A cell spanning two rows (column 0) and one spanning three columns (last row): the
    continuation cells are written empty, never filled from the left or from above."""
    doc = pymupdf.open()
    page = doc.new_page(width=W, height=H)
    x, y = [72, 172, 272, 372, 472], [100, 124, 148, 172, 196]

    def rule(a, b):
        page.draw_line(a, b, color=(0, 0, 0), width=0.7)

    page.draw_rect(pymupdf.Rect(x[0], y[0], x[-1], y[-1]), color=(0, 0, 0), width=0.7)
    rule((x[0], y[1]), (x[-1], y[1]))
    rule((x[1], y[2]), (x[-1], y[2]))         # column 0 spans rows 1-2
    rule((x[0], y[3]), (x[-1], y[3]))
    rule((x[1], y[0]), (x[1], y[-1]))
    for xi in (x[2], x[3]):                   # the last row spans columns 1-3
        rule((xi, y[0]), (xi, y[3]))
    printed = [["Thick", "Amps", "Speed", "Delay"], ["10", "85", "0.5", "1.0"],
               [None, "", "0.7", "1.2"], ["20", "Edge Start", None, None]]
    for r, row in enumerate(printed):
        for c, t in enumerate(row):
            if t:
                page.insert_text((x[c] + 4, y[r] + 16), t, fontname="helv", fontsize=9)
    _, recs = _process(doc, tmp_path, out, dpi=72)
    (table,) = _blocks(recs[0], "table")
    assert table["merged"] is True
    assert table["cells"] == printed
    assert table["text"].splitlines()[2:] == ["|10|85|0.5|1.0|", "|||0.7|1.2|", "|20|Edge Start|||"]


# ------------------------------------------------------------------------ specs, contents
def test_dot_leader_specs_with_two_values_and_empty_middle(tmp_path, out):
    doc = pymupdf.open()
    page = doc.new_page(width=W, height=H)
    page.insert_text((72, 50), "4.0  Specifications", fontname="hebo", fontsize=16)
    _lines(page, 72, 70, [
        "Model number ................................  ................... VBS-18MWEVS",
        "Stock Number ........................ 321357A ........................ 321359A",
        "Horsepower ............................................................... 2 HP",
        "Phase .................................................................... three",
    ])
    _, recs = _process(doc, tmp_path, out, dpi=72)
    specs = _blocks(recs[0], "specs")
    assert len(specs) == 1
    assert specs[0]["items"] == [
        {"label": "Model number", "values": ["VBS-18MWEVS"]},
        {"label": "Stock Number", "values": ["321357A", "321359A"]},
        {"label": "Horsepower", "values": ["2 HP"]},
        {"label": "Phase", "values": ["three"]},
    ]
    assert "| Horsepower | 2 HP |" in specs[0]["text"]
    assert recs[0].get("contents") is not True


def test_contents_page_is_marked(tmp_path, out):
    doc = pymupdf.open()
    page = doc.new_page(width=W, height=H)
    page.insert_text((72, 50), "Table of Contents", fontname="hebo", fontsize=16)
    _lines(page, 72, 70, ["Safety ........................................ 3",
                          "Specifications ................................ 5",
                          "Setup ......................................... 9",
                          "Operation .................................... 14"])
    _, recs = _process(doc, tmp_path, out, dpi=72)
    assert recs[0]["contents"] is True
    assert _blocks(recs[0], "contents") and not _blocks(recs[0], "specs")


# ------------------------------------------------------------- running heads, page labels
def _numbered_book(doc, n=8, skip=(), start=10):
    for i in range(n):
        page = doc.new_page(width=W, height=H)
        page.insert_text((450, 30), "96-8000  rev N June 2006", fontname="helv", fontsize=8)
        page.insert_text((300, 770), "Operation", fontname="hebo", fontsize=10)
        if i not in skip:
            page.insert_text((55, 770), str(start + i), fontname="helv", fontsize=9)
        _lines(page, 54, 100, [f"Body text of page {i + 1}, a paragraph about RS-232.",
                               "A second line of the paragraph on this page."])


def test_running_head_and_foot_stripped_and_labels_from_text(tmp_path, out):
    doc = pymupdf.open()
    _numbered_book(doc, skip=(3,))
    _, recs = _process(doc, tmp_path, out, dpi=72)
    assert [r["label"] for r in recs] == ["10", "11", "12", "13", "14", "15", "16", "17"]
    assert recs[2]["label_source"] == "text"
    assert recs[3]["label_source"] == "inferred"
    for rec in recs:
        stripped = " ".join(rec["stripped"])
        assert "96-8000" in stripped and "Operation" in stripped
        body = " ".join(b.get("text", "") for b in rec["blocks"])
        assert "96-8000" not in body and "Operation" not in body
        assert "RS-232" in body


def test_running_foot_up_to_ten_percent_from_the_edge_is_stripped(tmp_path, out):
    """Hypertherm's feet sit at y 724-730 of 792 (8% from the bottom); a body line at the
    same height that does not repeat stays in the flow."""
    doc = pymupdf.open()
    for i in range(6):
        page = doc.new_page(width=W, height=H)
        page.insert_text((54, 728), f"Powermax65/85 Operator Manual  3-{20 + i}", fontname="helv",
                         fontsize=8)
        _lines(page, 54, 100, [f"Body text of page {i + 1}."])
        if i == 2:
            page.insert_text((300, 724), "Continued on the next page.", fontname="helv", fontsize=9)
    _, recs = _process(doc, tmp_path, out, dpi=72)
    for rec in recs:
        assert any("Powermax65/85 Operator Manual" in s for s in rec["stripped"])
        assert not any("Powermax" in b.get("text", "") for b in rec["blocks"])
    assert "Continued on the next page." in [b.get("text") for b in recs[2]["blocks"]]


def test_pdf_page_labels_with_indesign_hex_prefix(tmp_path, out):
    doc = pymupdf.open()
    for i in range(4):
        page = doc.new_page(width=W, height=H)
        _lines(page, 72, 100, [f"Text {i}"])
    doc.set_page_labels([{"startpage": 0, "prefix": "", "style": "A", "firstpagenum": 1},
                         {"startpage": 2, "prefix": "<FEFF0053006500630031003A>", "style": "D",
                          "firstpagenum": 1}])
    assert doc[2].get_label() == "<FEFF0053006500630031003A>1"
    _, recs = _process(doc, tmp_path, out, dpi=72)
    assert [r["label"] for r in recs] == ["A", "B", "1", "2"]
    assert {r["label_source"] for r in recs} == {"pdf_label"}


def test_label_candidates_letter_spaced_digits():
    assert manuals.label_candidates("8 0") == [("a", 80, None)]
    assert manuals.label_candidates("-28-") == [("a", 28, None)]
    assert manuals.label_candidates("OM-232 384 Page 12") == [("a", 12, None)]
    assert manuals.label_candidates("4-1") == [("c", 1, 4)]
    assert manuals.label_candidates("iv") == [("r", 4, None)]


def test_pdf_label_prefixes_other_than_indesign_sections_are_kept(tmp_path, out):
    """Hypertherm 806650: chapter prefixes "SC-", "1-", "3-" are part of the printed label."""
    doc = pymupdf.open()
    for i in range(6):
        page = doc.new_page(width=W, height=H)
        _lines(page, 72, 100, [f"Text {i}"])
    doc.set_page_labels([{"startpage": 0, "prefix": "<FEFF00530043002D>", "style": "D", "firstpagenum": 1},
                         {"startpage": 2, "prefix": "<FEFF0031002D>", "style": "D", "firstpagenum": 7},
                         {"startpage": 4, "prefix": "<FEFF0053006500630032003A>", "style": "D",
                          "firstpagenum": 4}])
    _, recs = _process(doc, tmp_path, out, dpi=72)
    assert [r["label"] for r in recs] == ["SC-1", "SC-2", "1-7", "1-8", "4", "5"]


def test_decode_label():
    assert manuals.decode_label("<FEFF0053006500630031003A>1") == "1"
    assert manuals.decode_label("<FEFF00530043002D>2") == "SC-2"
    assert manuals.decode_label("<FEFF0033002D>5") == "3-5"
    assert manuals.decode_label("<FEFF00530065006300310032003A>3") == "3"     # Sec12:
    assert manuals.decode_label("A") == "A"
    assert manuals.decode_label("") is None


def test_chapter_page_labels(tmp_path, out):
    doc = pymupdf.open()
    for i in range(4):
        page = doc.new_page(width=W, height=H)
        page.insert_text((54, 760), "J500", fontname="helv", fontsize=10)
        page.insert_text((560, 760), f"4-{i + 1}", fontname="helv", fontsize=10)
        _lines(page, 72, 100, [f"Chapter four text {i}"])
    _, recs = _process(doc, tmp_path, out, dpi=72)
    assert [r["label"] for r in recs] == ["4-1", "4-2", "4-3", "4-4"]
    assert all("J500" in " ".join(r["stripped"]) for r in recs)


# ------------------------------------------------------------- warnings, steps, hyphens
def test_warning_box_and_steps_with_number_alone(tmp_path, out):
    doc = pymupdf.open()
    page = doc.new_page(width=W, height=H)
    page.insert_text((278, 100), "WARNING!", fontname="hebo", fontsize=10)
    _lines(page, 162, 113, ["One of the biggest causes of electronic damage is a lack of a good",
                            "ground on both the CNC mill and the computer."], size=8, font="hebo",
           pitch=9.6)
    page.insert_text((54, 160), "Cable length", fontname="hebo", fontsize=11)
    _lines(page, 54, 175, ["The following lists baud rate and the respective maximum cable length."])
    _lines(page, 325, 220, ["CAUTION: MAKE SURE THE BLADE IS NOT IN CON-",
                            "TACT WITH THE WORKPIECE WHEN THE MOTOR",
                            "IS STARTED."])
    # NGC style: "1." then the text on the same baseline, in its own span
    y = 300
    for n, t in (("1", "Function"), ("2", "Cursor"), ("3", "Display")):
        page.insert_text((81, y), f"{n}.", fontname="helv", fontsize=BODY)
        page.insert_text((108, y), t, fontname="helv", fontsize=BODY)
        y += 16
    # number alone on its line, text on the next line
    page.insert_text((48, 400), "4.", fontname="hebo", fontsize=BODY)
    page.insert_text((69, 413), "DISCONNECT LATHE FROM POWER!", fontname="helv", fontsize=BODY)
    _, recs = _process(doc, tmp_path, out, dpi=72)
    rec = recs[0]
    warns = _blocks(rec, "warning")
    assert [w["level"] for w in warns] == ["WARNING", "CAUTION"]
    assert warns[0]["text"].startswith("WARNING! One of the biggest causes")
    assert warns[0]["text"].endswith("the computer.")
    # a hyphen at a line end joins without a space only before a lowercase letter
    assert "IN CON- TACT WITH THE WORKPIECE" in warns[1]["text"]
    heads = [b["text"] for b in _blocks(rec, "heading")]
    assert "Cable length" in heads
    steps = _blocks(rec, "step")
    assert [(s["n"], s["text"]) for s in steps] == [
        ("1", "Function"), ("2", "Cursor"), ("3", "Display"), ("4", "DISCONNECT LATHE FROM POWER!")]


def test_soft_hyphen_controls_and_hidden_span(tmp_path, out):
    doc = pymupdf.open()
    page = doc.new_page(width=W, height=H)
    _lines(page, 72, 100, ["Pull the red release button\x07 on the electro-", "magnetic valve."])
    _lines(page, 72, 140, ["Press the \x01\x02 key to start."])
    page.insert_text((84, 200), "Figure 5: Securing workpiece", fontname="helv", fontsize=BODY)
    page.insert_text((84, 200), "The work stop is used", fontname="helv", fontsize=BODY)
    _lines(page, 72, 213, ["to set up the saw for making multiple cuts."])
    _, recs = _process(doc, tmp_path, out, dpi=72)
    rec = recs[0]
    paras = [b["text"] for b in _blocks(rec, "para")]
    assert "Pull the red release button on the electromagnetic valve." in paras
    assert "Press the key to start." in paras
    assert rec["dropped_chars"] == 3
    assert any(p.startswith("The work stop is used to set up") for p in paras)
    assert not any("Securing" in p for p in paras)
    over = [w for w in rec["warnings"] if w["kind"] == "overlap"]
    assert len(over) == 1
    assert "Figure 5: Securing workpiece" in over[0]["dropped"]
    assert "The work stop is used" in over[0]["kept"]


def _junk(o):
    """The junk glyphs in a string or in every string of a record."""
    if isinstance(o, dict):
        return [c for v in o.values() for c in _junk(v)]
    if isinstance(o, list):
        return [c for v in o for c in _junk(v)]
    return [c for c in o if c not in "\n\t" and manuals._dropped(c)] if isinstance(o, str) else []


def test_control_glyphs_dropped_from_table_cells_and_counted_once(tmp_path, out):
    doc = pymupdf.open()
    page = doc.new_page(width=W, height=H)
    rows = [("Key", "Function"), ("\x01\x02", "Cycle start"), ("\x1f", "Feed hold"),
            ("\x0e", "Reset")]
    x0, x1, xm, y0 = 72, 540, 220, 70
    for i, (a, b) in enumerate(rows):
        y = y0 + i * 24
        page.draw_rect(pymupdf.Rect(x0, y, x1, y + 24), color=(0, 0, 0), width=0.7)
        page.draw_line((xm, y), (xm, y + 24), color=(0, 0, 0), width=0.7)
        page.insert_text((x0 + 4, y + 16), a, fontname="helv", fontsize=9)
        page.insert_text((xm + 4, y + 16), b, fontname="helv", fontsize=9)
    # vertical text is not read into lines, but its glyphs are dropped and counted too
    page.insert_text((40, 400), "Side\x03 note", fontname="helv", fontsize=9, rotate=90)
    _, recs = _process(doc, tmp_path, out, dpi=72)
    rec = recs[0]
    tables = _blocks(rec, "table")
    assert len(tables) == 1
    assert "Cycle start" in tables[0]["text"] and "Feed hold" in tables[0]["text"]
    assert not _junk(rec)
    assert not _junk(manuals.render_md({}, {}, [rec]))
    assert rec["dropped_chars"] == 5


def _runs(page, x, y, runs, size=9):
    """Text runs set one after another on a baseline, each in its own font (a styled run)."""
    for text, font in runs:
        page.insert_text((x, y), text, fontname=font, fontsize=size)
        x += pymupdf.get_text_length(text, fontname=font, fontsize=size)


def test_table_cells_are_plain_text_with_spaces_between_runs_and_lines(tmp_path, out):
    """Styled runs in a cell (Haas NGC: "Shows EDIT:MDI in", "Press [SHIFT]") and a cell of
    two printed lines: plain text, a space where the page has a word gap or a line break,
    no Markdown emphasis and no <br>; the header row the same. Text overprinted for a bold
    look is read once."""
    doc = pymupdf.open()
    page = doc.new_page(width=W, height=H)
    x0, xm, x1, y = 72, 220, 540, [70, 94, 130, 154]
    for a, b in zip(y, y[1:]):
        page.draw_rect(pymupdf.Rect(x0, a, x1, b), color=(0, 0, 0), width=0.7)
        page.draw_line((xm, a), (xm, b), color=(0, 0, 0), width=0.7)
    _runs(page, x0 + 4, 86, [("Mode", "hebo")])
    _runs(page, xm + 4, 86, [("Shown", "hebo"), (" ", "helv"), ("on screen", "heit")])
    _runs(page, x0 + 4, 110, [("Manual Data Input", "helv")])
    _runs(page, xm + 4, 110, [("Shows ", "helv"), ("EDIT:MDI", "hebo"), (" in", "helv")])
    _runs(page, xm + 4, 124, [("upper left display.", "helv")])
    _runs(page, x0 + 4, 146, [("Setup", "helv")])
    _runs(page, xm + 4, 146, [("Press ", "helv"), ("[SHIFT]", "hebo"), (" after ", "helv"),
                              ("[POWER UP]", "hebo")])
    _runs(page, x0 + 4.3, 146, [("Setup", "helv")])        # overprinted for a bold look (Miller)
    _, recs = _process(doc, tmp_path, out, dpi=72)
    (table,) = _blocks(recs[0], "table")
    assert table["cells"] == [["Mode", "Shown on screen"],
                              ["Manual Data Input", "Shows EDIT:MDI in upper left display."],
                              ["Setup", "Press [SHIFT] after [POWER UP]"]]
    assert table["text"].splitlines() == ["|Mode|Shown on screen|", "|---|---|",
                                          "|Manual Data Input|Shows EDIT:MDI in upper left display.|",
                                          "|Setup|Press [SHIFT] after [POWER UP]|"]


def test_table_cell_keeps_the_letters_of_an_expanded_ligature():
    """MuPDF expands a ligature glyph into its letters and gives the added letters a zero-width
    box (Grizzly pdf 60: "ﬁ" in "finish", "ﬃ" in "difficult"): they belong to the cell too."""
    def lig(word, x):
        """``word`` with ``^`` before each letter a ligature adds (set at zero width)."""
        chars, zero = [], False
        for c in word:
            if c == "^":
                zero = True
                continue
            w = 0 if zero else 4.5
            chars.append({"c": c, "bbox": (x, 282.5, x + w, 289.0), "origin": (x, 289.0)})
            x += w
            zero = False
        return chars, x

    a, x = lig("Bad f^inish, dif^f^icult", 51.3)
    raw = [{"bbox": (51.3, 282.5, x, 289.0), "spans": [{"chars": a}]}]
    assert manuals.cell_text(raw, pymupdf.Rect(47, 278, 300, 358)) == "Bad finish, difficult"


def test_table_cell_soft_hyphens_dropped_and_joined_at_a_line_end():
    """Hypertherm pdf 52 sets "im\xadme\xaddi\xadate\xadly" with zero-width soft hyphens: the cell
    reads "immediately", as body text; a soft hyphen at the end of a cell's line joins the
    word with the next line."""
    def line(text, y, x=72.0):
        chars = []
        for c in text:
            w = 0 if c == "\xad" else 4.5
            chars.append({"c": c, "bbox": (x, y - 6.5, x + w, y), "origin": (x, y)})
            x += w
        return {"bbox": (72.0, y - 6.5, x, y), "spans": [{"chars": chars}]}

    raw = [line("comes on im\xadme\xaddi\xadate\xadly when the", 100), line("torch is ac\xad", 110),
           line("tivated.", 120)]
    assert manuals.cell_text(raw, pymupdf.Rect(70, 90, 300, 125)) == \
        "comes on immediately when the torch is activated."


class _FakePage:
    """A page whose rawdict is given: one span per text, glyphs 5 pt apart."""

    def __init__(self, texts):
        lines = []
        for i, t in enumerate(texts):
            y = 100 + 20 * i
            chars = [{"c": c, "bbox": (72 + 5 * k, y - 8, 77 + 5 * k, y + 2), "origin": (72 + 5 * k, y)}
                     for k, c in enumerate(t)]
            lines.append({"dir": (1, 0), "spans": [{"font": "Arial", "flags": 0, "size": 10,
                                                    "chars": chars}]})
        self.raw = {"blocks": [{"type": 0, "lines": lines}]}

    def get_text(self, *a, **kw):
        return self.raw


def test_c1_controls_read_as_cp1252():
    """mini_mill.pdf sets inch marks, quotes and bullets as cp1252 bytes in C1 (0x94 = ”):
    they are characters, not junk; a C1 code point that cp1252 leaves unmapped stays junk."""
    lines, dropped = manuals.read_lines(_FakePage(["Up to 17.75\x94 under spindle.",
                                                   "Haas Automation\x92s \x93Shipping\x94", "\x95 x\x81y"]))
    assert [l.text for l in lines] == ["Up to 17.75” under spindle.", "Haas Automation’s “Shipping”",
                                       "• xy"]
    assert dropped == 1
    assert manuals.clean_text("3/8\x94 hose\x8d") == ("3/8” hose", 1)


def test_clean_record_sweeps_every_string():
    rec = {"blocks": [{"kind": "figure", "caption": "Fig\x01ure 2", "labels": ["A\x02", "B"]},
                      {"kind": "specs", "items": [{"label": "X\x7f", "values": ["1\x00"]}],
                       "text": "| X\x7f | 1\x00 |\n|---|"}],
           "stripped": ["Head"], "dropped_chars": 1}
    manuals.clean_record(rec)
    assert rec["blocks"][0] == {"kind": "figure", "caption": "Figure 2", "labels": ["A", "B"]}
    assert rec["blocks"][1]["items"] == [{"label": "X", "values": ["1"]}]
    assert rec["blocks"][1]["text"] == "| X | 1 |\n|---|"
    assert rec["stripped"] == ["Head"]
    assert rec["dropped_chars"] == 1 + 7


def test_blocks_keep_printed_lines_and_nc_code_is_a_code_block(tmp_path, out):
    doc = pymupdf.open()
    page = doc.new_page(width=W, height=H)
    _lines(page, 72, 100, ["The following program drills four holes with a peck",
                           "drilling cycle on the part zero set in G54."])
    nc = ["O1234 (Canned Cycle Example Program)", "T1 M06", "G90 G54 G00 X.565 Y-1.875",
          "S1275 M03", "G43 H01 Z.1 M08", "G82 Z-.175 P.03 R.1 F10.", "repeat for each hole",
          "M30"]
    _lines(page, 72, 160, nc)
    _lines(page, 72, 300, ["1.  Load the program and press", "Cycle Start."])
    _, recs = _process(doc, tmp_path, out, dpi=72)
    rec = recs[0]
    paras = _blocks(rec, "para")
    assert paras[0]["lines"] == ["The following program drills four holes with a peck",
                                 "drilling cycle on the part zero set in G54."]
    assert paras[1]["lines"] == nc
    assert paras[1]["text"] == " ".join(nc)
    assert _blocks(rec, "step")[0]["lines"] == ["1. Load the program and press", "Cycle Start."]
    md = manuals.render_md({}, {}, [rec])
    assert "```\n" + "\n".join(nc) + "\n```" in md
    assert "The following program drills four holes with a peck drilling cycle" in md
    assert manuals.nc_code(nc) and not manuals.nc_code(paras[0]["lines"])
    # macro statements and spaced axis words are code too (Haas 2006 pdf 95, pdf 64)
    assert manuals.nc_code(["O0300 (Initialize an array of variables) ;", "#3000=1 (Base variable) ;",
                            "#[#2+#19]=#22 ;", "END1 ;", "M99 ;"])
    assert manuals.nc_code(["Sub Program O1000 (X,Y Locations)", "X 1.115 Y-2.750", "X[#105] Y0", "M99"])
    assert manuals.nc_code(["N10#104= [ #101 / COS[ 45. ] ]", "N11G98 G01 X#104 F100.", "N12M133 P1500"])
    assert manuals.nc_code(["(1) M08", "(2) G00 X1.1 Z0.5 (Initial Start Point)"])
    assert manuals.nc_code(["(Turn on Cutter Comp)", "(Move to I.D. of bored hole)"])
    # not listings: Grizzly model numbers, a Bridgeport caption and feed chart, variable lists
    assert not manuals.nc_code(["G0752/G0752Z Electrical Box Wiring Diagram.. 69",
                                "G0752/G0752Z Electrical Box Components... 70"])
    assert not manuals.nc_code(["Figure I.II - Spindle Guard Assembly", "(#30 Quick Change or Universal",
                                "#200 Quick Change Spindle)"])
    assert not manuals.nc_code(["(X) .750-35 IPM (X) .375-15 IPM", "(19-889mm/min) (9.5-381mm/min)"])
    assert not manuals.nc_code(["#0 Not a number (read only)", "#1-#33 Macro call arguments"])
    # one line of prose that starts with a G-code is not a listing
    assert not manuals.nc_code(["G28 returns all axes to machine zero."])
    assert not manuals.nc_code(["X axis travel is 30 inches; Y axis travel", "(see Figure 3) is 16."])


def test_symbol_font_bullets(tmp_path, out):
    """Mastercam sets its bullets as Wingdings "l"; here the Symbol font stands in."""
    doc = pymupdf.open()
    page = doc.new_page(width=W, height=H)
    for i, t in enumerate(["Select the chain.", "Click OK to close the dialog."]):
        page.insert_text((72, 100 + 30 * i), "l", fontname="symb", fontsize=BODY)
        page.insert_text((84, 100 + 30 * i), t, fontname="helv", fontsize=BODY)
    _lines(page, 72, 200, ["l is the letter, not a bullet, in a text font."])
    page.insert_text((72, 240), "l", fontname="symb", fontsize=BODY)    # no text after it
    _, recs = _process(doc, tmp_path, out, dpi=72)
    bullets = _blocks(recs[0], "bullet")
    assert [(b["marker"], b["text"]) for b in bullets] == [
        ("•", "Select the chain."), ("•", "Click OK to close the dialog.")]
    assert bullets[0]["lines"][0].endswith("Select the chain.")
    assert "- Select the chain." in manuals.render_md({}, {}, recs)
    assert any(b["text"].startswith("l is the letter") for b in _blocks(recs[0], "para"))


def test_bold_paragraph_is_not_a_heading(tmp_path, out):
    doc = pymupdf.open()
    page = doc.new_page(width=W, height=H)
    page.insert_text((72, 100), "Cleaning:", fontname="hebo", fontsize=BODY)
    _lines(page, 72, 130, ["Maintenance, cleaning and repair work may only be carried",
                           "out after the machine is protected against accidental",
                           "starting by pulling the mains plug."], font="hebo")
    _lines(page, 72, 190, ["Clean the machine regularly."])
    _, recs = _process(doc, tmp_path, out, dpi=72)
    kinds = [(b["kind"], b["text"][:20]) for b in recs[0]["blocks"]]
    assert kinds == [("heading", "Cleaning:"), ("para", "Maintenance, cleanin"), ("para", "Clean the machine re")]


def test_soft_hyphen_joins_the_word():
    words = manuals.join_lines(["Incorrect installa­", "tion will result"])
    assert words == "Incorrect installation will result"
    assert manuals.join_lines(["a soft­hyphen inside", "stays out"]) == "a softhyphen inside stays out"
    assert manuals.join_lines(["self-", "Contained"]) == "self- Contained"


# -------------------------------------------------------------------------------- CLI
def test_cli_dry_run_and_run_write_only_to_out(tmp_path, capsys):
    src = tmp_path / "src"
    src.mkdir()
    doc = pymupdf.open()
    _two_column_page(doc)
    _numbered_book(doc, n=3)
    doc.save(str(src / "synth manual.pdf"))
    doc.close()
    config = {"source": str(src),
              "manuals": [{"slug": "synth", "file": "synth manual.pdf", "maker": "Acme", "model": "X",
                           "doctype": "Manual", "doc_no": "1", "rev": "A", "date": "2020",
                           "control": None, "machines": ["lathe"], "pages": None, "notes": ""}],
              "excluded": [{"file": "old.pdf", "reason": "superseded"}]}
    cfg = tmp_path / "manuals.json"
    cfg.write_text(json.dumps(config), "utf-8")
    out = tmp_path / "pages"
    before = sorted(p.name for p in src.iterdir())
    assert cli.main(["manual-pages", "--config", str(cfg), "--out", str(out), "--dry-run"]) == 0
    assert "synth" in capsys.readouterr().out
    assert not out.exists()
    assert cli.main(["manual-pages", "--config", str(cfg), "--out", str(out), "--dpi", "72"]) == 0
    assert sorted(p.name for p in src.iterdir()) == before
    recs = [json.loads(l) for l in (out / "synth.pages.jsonl").read_text("utf-8").splitlines()]
    assert recs[0]["type"] == "file" and recs[0]["manual"]["slug"] == "synth"
    assert len(recs[0]["sha256"]) == 64 and recs[0]["pages"] == 4
    assert [r["pdf"] for r in recs[1:]] == [1, 2, 3, 4]
    md = (out / "synth.md").read_text("utf-8")
    assert "## pdf 1" in md and "### Chuck Removal" in md and "[Figure: Figure 28." in md
    summary = json.loads((out / "manual-pages.json").read_text("utf-8"))
    assert summary["manuals"]["synth"]["pages"] == 4
    assert summary["manuals"]["synth"]["figures"]["count"] == 2
    assert summary["excluded"][0]["file"] == "old.pdf"
    assert list((out / "figures").glob("synth_pdf001_f*.png"))
    # --pages limits the pages written; --only an unknown slug is an error
    assert cli.main(["manual-pages", "--config", str(cfg), "--out", str(out), "--only", "synth",
                     "--pages", "2-3", "--dpi", "72"]) == 0
    recs = [json.loads(l) for l in (out / "synth.pages.jsonl").read_text("utf-8").splitlines()]
    assert [r["pdf"] for r in recs[1:]] == [2, 3]
    assert cli.main(["manual-pages", "--config", str(cfg), "--out", str(out), "--only", "nope"]) == 1
