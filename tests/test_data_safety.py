# SPDX-License-Identifier: AGPL-3.0-or-later
"""Characterization tests for data safety (area A, step 1).

These pin down what currently happens when pages are replaced, files are
damaged or changed on disk, saves fail, and history is trimmed. Tests that
fail because of a real bug are ``xfail(strict=True)`` and name the finding in
REVIEW_REPORT.md, so fixing the bug turns the xfail into a failure that must
be un-marked.
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pymupdf
import pytest

from gpdf.core import textedit
from gpdf.core.document import PdfDocument, PdfEditError
from gpdf.core.history import Command, History

FIXTURES = Path(__file__).parent / "fixtures"


# -- helpers ------------------------------------------------------------------


def _save_pdf(doc: pymupdf.Document, path: Path) -> Path:
    doc.save(str(path))
    doc.close()
    return path


def _toc_links_pdf(path: Path) -> Path:
    """3 pages, TOC One/Two/Three -> 1/2/3, GOTO links p1->p3 and p3->p1."""
    doc = pymupdf.open()
    for i in range(3):
        page = doc.new_page()
        page.insert_text((72, 72), f"Page {i + 1}", fontsize=24)
    doc.set_toc([[1, "One", 1], [1, "Two", 2], [1, "Three", 3]])
    doc[0].insert_link({
        "kind": pymupdf.LINK_GOTO, "from": pymupdf.Rect(72, 200, 172, 220),
        "page": 2, "to": pymupdf.Point(0, 0),
    })
    doc[2].insert_link({
        "kind": pymupdf.LINK_GOTO, "from": pymupdf.Rect(72, 200, 172, 220),
        "page": 0, "to": pymupdf.Point(0, 0),
    })
    return _save_pdf(doc, path)


def _nav_state(raw: pymupdf.Document):
    toc = [(lvl, title, pg) for lvl, title, pg in raw.get_toc(simple=True)]
    links = [
        [(link["kind"], link.get("page")) for link in raw[i].get_links()]
        for i in range(raw.page_count)
    ]
    return toc, links


_EXPECTED_NAV = (
    [(1, "One", 1), (1, "Two", 2), (1, "Three", 3)],
    [[(pymupdf.LINK_GOTO, 2)], [], [(pymupdf.LINK_GOTO, 0)]],
)


def _texts(raw: pymupdf.Document) -> list[str]:
    return [raw[i].get_text().strip() for i in range(raw.page_count)]


class _Boom(Exception):
    pass


def _raise(page):
    raise _Boom("mutate failed")


# -- A. bookmarks and links survive page replacement (#1) ------------------------


def _path_add_text_undo_redo(doc, check):
    textedit.add_text(doc, 2, 72, 150, "added")
    check("add_text")
    assert doc.undo()
    check("undo")
    assert doc.redo()
    check("redo")


def _path_ocr_replace(doc, check):
    doc.replace_page_bytes(2, "OCR page", doc._extract_bytes([2]))
    check("replace_page_bytes")


def _path_delete_undo(doc, check):
    doc.delete_pages([2])
    assert doc.undo()
    check("delete+undo")


def _path_failed_edit(doc, check):
    with pytest.raises(_Boom):
        doc.edit_page(2, "Boom", _raise)
    check("failed edit")


@pytest.mark.parametrize("path", [
    pytest.param(_path_add_text_undo_redo, id="add_text-undo-redo",
                 marks=pytest.mark.xfail(strict=True, reason="#1 page swap kills TOC/links")),
    pytest.param(_path_ocr_replace, id="ocr-replace-page-bytes",
                 marks=pytest.mark.xfail(strict=True, reason="#1 page swap kills TOC/links")),
    pytest.param(_path_delete_undo, id="delete-undo",
                 marks=pytest.mark.xfail(strict=True, reason="#1 page swap kills TOC/links")),
    pytest.param(_path_failed_edit, id="failed-edit",
                 marks=pytest.mark.xfail(strict=True, reason="#1 page swap kills TOC/links")),
])
def test_bookmarks_and_links_survive(qapp, tmp_path, path):
    src = _toc_links_pdf(tmp_path / "nav.pdf")
    doc = PdfDocument(src)
    assert _nav_state(doc.raw) == _EXPECTED_NAV  # fixture sanity

    saves = 0

    def check(stage: str) -> None:
        nonlocal saves
        assert _nav_state(doc.raw) == _EXPECTED_NAV, f"live, after {stage}"
        saves += 1
        out = tmp_path / f"saved{saves}.pdf"
        doc.save_as(out)
        reopened = pymupdf.open(out)
        try:
            assert _nav_state(reopened) == _EXPECTED_NAV, f"saved, after {stage}"
        finally:
            reopened.close()

    try:
        path(doc, check)
    finally:
        doc.close()


# -- B. damaged file (#2) --------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="#2 damaged PDFs open silently, no was_repaired")
def test_truncated_file_reports_repair(qapp, tmp_path):
    full = tmp_path / "full.pdf"
    doc = pymupdf.open()
    for i in range(4):
        page = doc.new_page()
        for j in range(40):  # enough content that page 1 survives truncation
            page.insert_text((72, 72 + j * 15), f"Page {i + 1} line {j}", fontsize=8)
    _save_pdf(doc, full)
    data = full.read_bytes()
    damaged = tmp_path / "damaged.pdf"
    damaged.write_bytes(data[: len(data) // 3])

    pdf = PdfDocument(damaged)  # opens (that part is current behaviour)
    try:
        assert pdf.was_repaired is True
    finally:
        pdf.close()


# -- C. failed edit (#4) -----------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="#4 failed edit swaps the page object (new xref)")
def test_failed_edit_keeps_page_xref(qapp, make_pdf):
    doc = PdfDocument(make_pdf())
    try:
        xref = doc.raw.page_xref(1)
        with pytest.raises(_Boom):
            doc.edit_page(1, "Boom", _raise)
        assert doc.raw.page_xref(1) == xref
    finally:
        doc.close()


def test_failed_edit_leaves_content_and_history_alone(qapp, make_pdf):
    """What does hold today: text intact, nothing pushed on the undo stack."""
    doc = PdfDocument(make_pdf())
    try:
        before = _texts(doc.raw)
        with pytest.raises(_Boom):
            doc.edit_page(1, "Boom", _raise)
        assert _texts(doc.raw) == before
        assert not doc.can_undo
    finally:
        doc.close()


# -- D. file changed on disk ---------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="#15 save overwrites a file changed by another program")
def test_save_refuses_when_file_changed_on_disk(qapp, make_pdf, tmp_path):
    path = make_pdf("mine.pdf")
    doc = PdfDocument(path)
    try:
        other = pymupdf.open()
        other.new_page().insert_text((72, 72), "THEIRS", fontsize=24)
        theirs = tmp_path / "theirs.pdf"
        _save_pdf(other, theirs)
        theirs_bytes = theirs.read_bytes()
        path.write_bytes(theirs_bytes)
        st = path.stat()
        os.utime(path, (st.st_atime + 10, st.st_mtime + 10))

        doc.rotate_pages([0], 90)
        with pytest.raises(PdfEditError):
            doc.save()
        assert path.read_bytes() == theirs_bytes
    finally:
        doc.close()


# -- E. atomic save ---------------------------------------------------------------------


def _leftovers(folder: Path) -> list[Path]:
    return list(folder.glob(".gpdf-*.tmp"))


def test_save_leaves_no_temp_file(qapp, make_pdf):
    path = make_pdf()
    doc = PdfDocument(path)
    try:
        doc.rotate_pages([0], 90)
        doc.save()
        assert _leftovers(path.parent) == []
        assert pymupdf.open(path)[0].rotation == 90
    finally:
        doc.close()


def test_failed_replace_cleans_temp_and_keeps_original(qapp, make_pdf, monkeypatch):
    path = make_pdf()
    original = path.read_bytes()
    doc = PdfDocument(path)
    try:
        doc.rotate_pages([0], 90)

        def fail(src, dst):
            raise OSError("simulated replace failure")

        monkeypatch.setattr(os, "replace", fail)
        with pytest.raises(OSError):
            doc.save()
        monkeypatch.undo()
        assert _leftovers(path.parent) == []
        assert path.read_bytes() == original
        assert doc.is_dirty
    finally:
        doc.close()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows file locking semantics")
def test_save_onto_open_file_fails_cleanly(qapp, make_pdf):
    path = make_pdf()
    original = path.read_bytes()
    doc = PdfDocument(path)
    try:
        doc.rotate_pages([0], 90)
        with open(path, "rb"):
            with pytest.raises(OSError):
                doc.save()
        assert _leftovers(path.parent) == []
        assert path.read_bytes() == original
        assert doc.is_dirty
    finally:
        doc.close()


@pytest.mark.skipif(sys.platform != "win32", reason="read-only replace semantics differ")
def test_save_onto_read_only_file_fails_cleanly(qapp, make_pdf):
    path = make_pdf()
    original = path.read_bytes()
    doc = PdfDocument(path)
    try:
        doc.rotate_pages([0], 90)
        os.chmod(path, stat.S_IREAD)
        try:
            with pytest.raises(OSError):
                doc.save()
            assert _leftovers(path.parent) == []
            assert path.read_bytes() == original
            assert doc.is_dirty
        finally:
            os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
    finally:
        doc.close()


# -- F. signatures ----------------------------------------------------------------------


def test_signed_file_flag_clears_after_save_as(qapp, tmp_path):
    doc = PdfDocument(FIXTURES / "signed.pdf")
    try:
        assert doc.has_signatures is True
        doc.save_as(tmp_path / "copy.pdf")
        assert doc.has_signatures is False
    finally:
        doc.close()


# -- G. undo fidelity ---------------------------------------------------------------------


def _numbered_pdf(path: Path, n: int = 5) -> Path:
    doc = pymupdf.open()
    for i in range(n):
        doc.new_page().insert_text((72, 72), f"Page {i + 1}", fontsize=24)
    return _save_pdf(doc, path)


@pytest.mark.parametrize("indices", [
    pytest.param([0], id="first"),
    pytest.param([4], id="last"),
    pytest.param([0, 2], id="non-contiguous"),
    pytest.param([1, 2, 3, 4], id="all-but-one"),
])
def test_delete_undo_restores_order(qapp, tmp_path, indices):
    doc = PdfDocument(_numbered_pdf(tmp_path / "n.pdf"))
    try:
        original = _texts(doc.raw)
        doc.delete_pages(indices)
        assert doc.page_count == 5 - len(indices)
        assert doc.undo()
        assert doc.page_count == 5
        assert _texts(doc.raw) == original
        doc.save_as(tmp_path / "out.pdf")
        reopened = pymupdf.open(tmp_path / "out.pdf")
        try:
            assert reopened.page_count == 5
            assert _texts(reopened) == original
        finally:
            reopened.close()
    finally:
        doc.close()


def _page_props(raw: pymupdf.Document, index: int) -> dict:
    page = raw[index]
    return {
        "rotation": page.rotation,
        "cropbox": tuple(round(v, 2) for v in page.cropbox),
        "annots": [
            (a.type[1], tuple(round(v, 2) for v in a.rect)) for a in page.annots()
        ],
        "fields": [w.field_name for w in page.widgets()],
        "text": page.get_text().strip(),
    }


def _rich_pdf(path: Path) -> Path:
    doc = pymupdf.open()
    for i in range(4):
        doc.new_page().insert_text((72, 72), f"Page {i + 1}", fontsize=24)
    page = doc[1]
    page.set_cropbox(pymupdf.Rect(20, 20, 500, 700))
    page.set_rotation(90)
    page.add_highlight_annot(page.search_for("Page 2")[0])
    widget = pymupdf.Widget()
    widget.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
    widget.field_name = "customer_name"
    widget.rect = pymupdf.Rect(72, 200, 272, 224)
    page.add_widget(widget)
    doc.set_page_labels([
        {"startpage": 0, "prefix": "A-", "style": "D", "firstpagenum": 1},
        {"startpage": 2, "prefix": "", "style": "r", "firstpagenum": 1},
    ])
    return _save_pdf(doc, path)


def test_delete_undo_restores_page_properties(qapp, tmp_path):
    doc = PdfDocument(_rich_pdf(tmp_path / "rich.pdf"))
    try:
        before = _page_props(doc.raw, 1)
        assert before["rotation"] == 90 and before["fields"] == ["customer_name"]
        assert len(before["annots"]) == 1
        doc.delete_pages([1])
        assert doc.undo()
        assert _page_props(doc.raw, 1) == before
        doc.save_as(tmp_path / "out.pdf")
        reopened = pymupdf.open(tmp_path / "out.pdf")
        try:
            assert _page_props(reopened, 1) == before
        finally:
            reopened.close()
    finally:
        doc.close()


def test_delete_undo_restores_page_labels(qapp, tmp_path):
    doc = PdfDocument(_rich_pdf(tmp_path / "rich.pdf"))
    try:
        labels = doc.raw.get_page_labels()
        assert labels  # fixture sanity
        text_labels = [doc.raw[i].get_label() for i in range(doc.page_count)]
        doc.delete_pages([1])
        assert doc.undo()
        assert doc.raw.get_page_labels() == labels
        assert [doc.raw[i].get_label() for i in range(doc.page_count)] == text_labels
        doc.save_as(tmp_path / "out.pdf")
        reopened = pymupdf.open(tmp_path / "out.pdf")
        try:
            assert reopened.get_page_labels() == labels
        finally:
            reopened.close()
    finally:
        doc.close()


# -- H. history ---------------------------------------------------------------------------


class _Cmd(Command):
    def __init__(self, name: str, cost: int, log: list[str]) -> None:
        self.label = name
        self.byte_cost = cost
        self._log = log

    def do(self) -> None:
        self._log.append(f"do {self.label}")

    def undo(self) -> None:
        self._log.append(f"undo {self.label}")


def test_history_trim_drops_oldest():
    log: list[str] = []
    history = History(max_bytes=100)
    for name in "abcd":
        history.push(_Cmd(name, 40, log))
    assert history.total_bytes <= 100
    undone = []
    while (cmd := history.undo()) is not None:
        undone.append(cmd.label)
    assert undone == ["d", "c"]  # a and b were dropped


def test_history_trim_makes_saved_state_unreachable():
    log: list[str] = []
    history = History(max_bytes=100)
    assert history.is_clean  # saved state == fresh open
    history.push(_Cmd("a", 60, log))
    history.push(_Cmd("b", 60, log))  # drops "a": the opened state is gone
    assert not history.is_clean
    while history.undo() is not None:
        pass
    assert not history.can_undo
    assert not history.is_clean


def test_history_trim_keeps_save_point_when_still_reachable():
    log: list[str] = []
    history = History(max_bytes=100)
    history.push(_Cmd("a", 60, log))
    history.mark_clean()
    history.push(_Cmd("b", 60, log))  # drops "a", save point = state before "b"
    assert not history.is_clean
    history.undo()
    assert history.is_clean


def test_undo_after_save_is_dirty_and_redo_is_clean(qapp, tmp_path):
    doc = PdfDocument(_numbered_pdf(tmp_path / "n.pdf"))
    try:
        doc.rotate_pages([0], 90)
        doc.save()
        assert not doc.is_dirty
        assert doc.undo()
        assert doc.is_dirty
        assert doc.redo()
        assert not doc.is_dirty
    finally:
        doc.close()


# -- I. redaction side effects ------------------------------------------------------------


def test_replace_span_leaves_neighbouring_line_and_highlight(qapp, tmp_path):
    src = tmp_path / "lines.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "First line here", fontsize=12)
    page.insert_text((72, 113), "Second line here", fontsize=12)  # 13 pt below
    page.add_highlight_annot(page.search_for("Second line here")[0])
    _save_pdf(doc, src)

    pdf = PdfDocument(src)
    try:
        page = pdf.raw[0]

        def line2(p):
            return [w[4] for w in p.get_text("words") if w[3] > 105]

        annots_before = [(a.type[1], tuple(round(v, 1) for v in a.rect)) for a in page.annots()]
        line2_before = line2(page)
        assert line2_before == ["Second", "line", "here"]
        assert len(annots_before) == 1

        span = textedit.span_at(pdf, 0, 80, 96)
        assert span is not None and span.text.startswith("First")
        textedit.replace_span(pdf, 0, span, "Changed line here")

        page = pdf.raw[0]
        assert "Changed" in page.get_text()
        assert line2(page) == line2_before
        annots_after = [(a.type[1], tuple(round(v, 1) for v in a.rect)) for a in page.annots()]
        assert annots_after == annots_before
    finally:
        pdf.close()


def _words_below(page, y):
    return [w[4] for w in page.get_text("words") if w[3] > y]


def test_replace_span_tight_line_spacing(qapp, tmp_path):
    src = tmp_path / "tight.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "First line here", fontsize=12)
    page.insert_text((72, 109), "Second line here", fontsize=12)  # 9 pt below
    _save_pdf(doc, src)

    pdf = PdfDocument(src)
    try:
        span = textedit.span_at(pdf, 0, 80, 98)
        assert span is not None and span.text.startswith("First")
        textedit.replace_span(pdf, 0, span, "Changed line here")
        page = pdf.raw[0]
        assert _words_below(page, 105) == ["Second", "line", "here"]
        assert "Changed" in page.get_text()
    finally:
        pdf.close()


def test_replace_text_leaves_next_line_alone(qapp, tmp_path):
    src = tmp_path / "rt.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "First line here", fontsize=12)
    page.insert_text((72, 112), "Second lineup here", fontsize=12)  # 12 pt below
    _save_pdf(doc, src)

    pdf = PdfDocument(src)
    try:
        assert textedit.replace_text(pdf, 0, "First", "Primo") == 1
        page = pdf.raw[0]
        line1 = [w[4] for w in page.get_text("words") if w[3] <= 105]
        assert sorted(line1) == ["Primo", "here", "line"]
        assert _words_below(page, 105) == ["Second", "lineup", "here"]
    finally:
        pdf.close()


def test_replace_span_keeps_adjacent_bold_span(qapp, tmp_path):
    src = tmp_path / "bold.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Head", fontsize=12)
    x = 72 + pymupdf.get_text_length("Head", fontname="helv", fontsize=12)
    page.insert_text((x, 100), "TAIL", fontsize=12, fontname="hebo")
    _save_pdf(doc, src)

    pdf = PdfDocument(src)
    try:
        span = textedit.span_at(pdf, 0, 75, 97)
        assert span is not None and span.text.strip() == "Head"
        textedit.replace_span(pdf, 0, span, "Changed")
        assert "TAIL" in pdf.raw[0].get_text()
    finally:
        pdf.close()


def test_replace_span_keeps_line_art_under_text(qapp, tmp_path):
    src = tmp_path / "art.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.draw_rect(pymupdf.Rect(60, 85, 300, 106), color=None, fill=(0.9, 0.9, 0.5))
    page.insert_text((72, 100), "First line here", fontsize=12)
    _save_pdf(doc, src)

    pdf = PdfDocument(src)
    try:
        before = len(pdf.raw[0].get_drawings())
        assert before >= 1
        span = textedit.span_at(pdf, 0, 80, 97)
        textedit.replace_span(pdf, 0, span, "Changed line here")
        assert len(pdf.raw[0].get_drawings()) == before
    finally:
        pdf.close()


def _link_state(page):
    return [(l["kind"], l.get("page"), tuple(round(v, 1) for v in l["from"])) for l in page.get_links()]


def test_replace_span_keeps_link_over_text(qapp, tmp_path):
    src = tmp_path / "link.pdf"
    doc = pymupdf.open()
    doc.new_page()
    doc.new_page()
    page = doc[0]  # a page object taken before the next new_page() goes stale
    page.insert_text((72, 100), "Click here now", fontsize=12)
    page.insert_link({
        "kind": pymupdf.LINK_GOTO, "from": pymupdf.Rect(72, 88, 160, 104),
        "page": 1, "to": pymupdf.Point(0, 0),
    })
    _save_pdf(doc, src)

    pdf = PdfDocument(src)
    try:
        before = _link_state(pdf.raw[0])
        assert len(before) == 1
        span = textedit.span_at(pdf, 0, 80, 97)
        textedit.replace_span(pdf, 0, span, "Tap here now")
        assert "Tap" in pdf.raw[0].get_text()
        assert _link_state(pdf.raw[0]) == before
        out = tmp_path / "out.pdf"
        pdf.save_as(out)
    finally:
        pdf.close()
    reopened = pymupdf.open(str(out))
    try:
        assert _link_state(reopened[0]) == before
    finally:
        reopened.close()
