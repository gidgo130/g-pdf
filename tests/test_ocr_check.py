# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Check OCR panel (report loading, order, page links) and the view's text overlay."""

import json

import pymupdf
import pytest

from gpdf.core.document import PdfDocument
from gpdf.ui import ocr_check
from gpdf.ui.pdf_view import PdfView

PAGES = [
    {"page": 1, "status": "ocr", "words": 300, "mean_conf": 91.5, "low_conf_share": 0.02},
    {"page": 2, "status": "blank", "words": 0, "mean_conf": None, "low_conf_share": None},
    {"page": 3, "status": "ocr", "words": 120, "mean_conf": 55.0, "low_conf_share": 0.4},
    {"page": 4, "status": "failed", "words": 0, "mean_conf": None, "low_conf_share": None,
     "error": "exit code 1: boom"},
    {"page": 5, "status": "ocr", "words": 250, "mean_conf": 55.0, "low_conf_share": 0.3},
    {"page": 6, "status": "ocr", "words": 0, "mean_conf": None, "low_conf_share": None},
]


def write_report(path, pages=PAGES):
    path.write_text(json.dumps({
        "schema": 1, "langs": "eng", "pages": pages,
        "summary": {"counts": {"ocr": 4, "has_text": 0, "blank": 1, "no_image": 0, "failed": 1},
                    "mean_conf": 72.3}}), encoding="utf-8")
    return path


def test_report_rows_failed_first_then_lowest_confidence(tmp_path):
    check = ocr_check.load_report(write_report(tmp_path / "x_ocr.report.json"))
    assert [r.page for r in check.rows] == [4, 3, 5, 1]       # ties (55.0) by page
    assert check.langs == "eng" and check.mean_conf == 72.3
    assert check.rows[0].error == "exit code 1: boom"


def test_unreadable_report_says_why(tmp_path):
    bad = tmp_path / "x_ocr.report.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(ocr_check.ReportError, match="x_ocr.report.json"):
        ocr_check.load_report(bad)
    with pytest.raises(ocr_check.ReportError):
        ocr_check.load_report(tmp_path / "missing.json")


@pytest.fixture
def doc(qapp, tmp_path):
    """Two pages: one upright, one /Rotate 90, each with an invisible word."""
    path = tmp_path / "x_ocr.pdf"
    d = pymupdf.open()
    for rot in (0, 90):
        page = d.new_page(width=300, height=400)
        page.insert_text((40, 60), f"word{rot}", fontsize=20, render_mode=3)
        page.set_rotation(rot)
    d.save(str(path))
    document = PdfDocument(path)
    yield document
    document.close()


def test_panel_lists_pages_in_report_order_and_links_to_them(qapp, doc, tmp_path):
    check = ocr_check.load_report(write_report(tmp_path / "x_ocr.report.json"))
    panel = ocr_check.OcrCheckPanel(check, doc)
    assert panel.pages_in_order() == [4, 3, 5, 1]
    assert panel.table.item(0, 1).text() == "failed"
    assert panel.table.item(1, 3).text() == "40"               # low-confidence share, %
    asked = []
    panel.page_requested.connect(asked.append)
    panel.table.cellClicked.emit(1, 2)
    assert asked == [2]                                         # page 3, 0-based
    toggles = []
    panel.overlay_toggled.connect(toggles.append)
    panel.show_text.setChecked(True)
    assert toggles == [True]
    panel.set_page(1)
    assert panel.text.toPlainText() == "word90"
    panel.set_page(99)
    assert panel.text.toPlainText() == ""


@pytest.mark.parametrize("index", [0, 1])
def test_overlay_words_are_where_search_puts_them(qapp, doc, index):
    """On the upright and the rotated page the overlay box is the displayed word's box
    (as search highlights it), not the unrotated one."""
    from gpdf.core import textedit

    view = PdfView()
    view.set_document(doc)
    ((x0, y0, x1, y1, text),) = view.overlay_words(index)
    hit = next(h for h in textedit.search(doc, text) if h.page == index)
    assert (x0, y0, x1, y1) == pytest.approx(hit.rect, abs=0.5)
    w, h = doc.raw[index].rect.width, doc.raw[index].rect.height
    assert 0 <= x0 < x1 <= w and 0 <= y0 < y1 <= h
    if index == 1:
        assert (w, h) == (400, 300) and x0 > 300                # turned with the page
    assert not view.text_overlay
    view.set_text_overlay(True)
    view.resize(500, 700)
    view.grab()                                                 # paints without error
    assert not doc.is_dirty
    view.set_document(None)


def test_tab_shows_the_panel_in_the_sidebar_and_replaces_it(qapp, doc, tmp_path, monkeypatch):
    from gpdf.ui.doc_tab import DocumentTab

    report = write_report(tmp_path / "x_ocr.report.json")
    tab = DocumentTab(doc)
    assert tab.show_ocr_check(report)
    first = tab.ocr_check
    assert tab.sidebar.currentWidget() is first and tab.sidebar.tabText(2) == "OCR check"
    first.show_text.setChecked(True)
    assert tab.view.text_overlay
    first.table.cellClicked.emit(2, 0)                         # page 5: past the end
    tab.view.go_to_page(1)
    assert first.text.toPlainText() == "word90"
    assert tab.show_ocr_check(report)                          # again: one panel
    assert tab.sidebar.count() == 3 and tab.ocr_check is not first
    assert not tab.view.text_overlay

    shown = []
    monkeypatch.setattr(tab, "show_error", shown.append)
    (tmp_path / "bad.json").write_text("[]", encoding="utf-8")
    assert not tab.show_ocr_check(tmp_path / "bad.json") and "bad.json" in shown[0]
    tab._drop_ocr_check()
    assert tab.sidebar.count() == 2


def test_check_ocr_menu_finds_the_report_or_says_where_it_looked(qapp, tmp_path, monkeypatch):
    from gpdf.ui import prep_actions
    from gpdf.ui.main_window import MainWindow

    prep = tmp_path / "vt_prep"
    prep.mkdir()
    pdf = prep / "vt_ocr.pdf"
    d = pymupdf.open()
    d.new_page().insert_text((40, 60), "hello", render_mode=3)
    d.save(str(pdf))
    said = []
    monkeypatch.setattr(prep_actions, "show_message", lambda w, t: said.append(t))
    w = MainWindow()
    try:
        tab = w.open_path(pdf)
        w._a_prep_check.trigger()
        assert tab.ocr_check is None and "vt_ocr.report.json" in said[0]
        write_report(prep / "vt_ocr.report.json", PAGES[:1])
        w._a_prep_check.trigger()
        assert tab.ocr_check is not None and tab.ocr_check.pages_in_order() == [1]
    finally:
        w.close()
        qapp.processEvents()
