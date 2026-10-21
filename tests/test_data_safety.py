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
import re
import stat
import sys
from pathlib import Path

import pymupdf
import pytest

from gpdf.core import document as document_mod
from gpdf.core import textedit
from gpdf.core.document import (
    FileChangedError, PdfDocument, PdfEditError, _ref_array, _stray_pages,
)
from gpdf.core.history import Command, History

FIXTURES = Path(__file__).parent / "fixtures"


# -- helpers ------------------------------------------------------------------


def _save_pdf(doc: pymupdf.Document, path: Path) -> Path:
    doc.save(str(path))
    doc.close()
    return path


def _toc_links_pdf(path: Path) -> Path:
    """3 pages, TOC One/Two/Three -> 1/2/3, GOTO links p1->p3 and p3->p1,
    named destination "sec3" -> p3 with a NAMED link to it on p2."""
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
    p3 = doc[2].xref
    names = doc.get_new_xref()
    doc.update_object(names, f"<</Names[(sec3)[{p3} 0 R/XYZ 0 800 0]]>>")
    doc.xref_set_key(doc.pdf_catalog(), "Names", f"<</Dests {names} 0 R>>")
    doc[1].insert_link({
        "kind": pymupdf.LINK_NAMED, "from": pymupdf.Rect(72, 300, 172, 320),
        "name": "sec3", "nameddest": "sec3",
    })
    return _save_pdf(doc, path)


def _nav_state(raw: pymupdf.Document):
    toc = [(lvl, title, pg) for lvl, title, pg in raw.get_toc(simple=True)]
    links = [
        [(link["kind"], link.get("page")) for link in raw[i].get_links()]
        for i in range(raw.page_count)
    ]
    if hasattr(raw, "_resolved_names"):  # resolve_names() caches its result
        del raw._resolved_names
    names = {name: dest["page"] for name, dest in raw.resolve_names().items()}
    return toc, links, names


_EXPECTED_NAV = (
    [(1, "One", 1), (1, "Two", 2), (1, "Three", 3)],
    [[(pymupdf.LINK_GOTO, 2)], [(pymupdf.LINK_NAMED, 2)], [(pymupdf.LINK_GOTO, 0)]],
    {"sec3": 2},
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
    pytest.param(_path_add_text_undo_redo, id="add_text-undo-redo"),
    pytest.param(_path_ocr_replace, id="ocr-replace-page-bytes"),
    pytest.param(_path_delete_undo, id="delete-undo"),
    pytest.param(_path_failed_edit, id="failed-edit"),
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


def _assert_nav(doc: PdfDocument, out: Path, stage: str) -> None:
    """Full _EXPECTED_NAV, live and in a saved copy."""
    assert _nav_state(doc.raw) == _EXPECTED_NAV, f"live, after {stage}"
    doc.save_as(out)
    reopened = pymupdf.open(out)
    try:
        assert _nav_state(reopened) == _EXPECTED_NAV, f"saved, after {stage}"
    finally:
        reopened.close()


def test_delete_non_contiguous_neutralises_then_restores_nav(qapp, tmp_path):
    doc = PdfDocument(_toc_links_pdf(tmp_path / "nav.pdf"))
    try:
        original = _texts(doc.raw)
        doc.delete_pages([0, 2])  # the p1<->p3 links run between deleted pages
        raw = doc.raw
        assert _texts(raw) == [original[1]]
        assert raw.get_toc(simple=True) == [[1, "One", -1], [1, "Two", 1], [1, "Three", -1]]
        for i in range(raw.page_count):
            for link in raw[i].get_links():
                if link["kind"] == pymupdf.LINK_GOTO:
                    assert raw[link["page"]].get_text().strip() != original[1]
        assert doc.undo()
        assert _texts(doc.raw) == original
        _assert_nav(doc, tmp_path / "out.pdf", "undo")
    finally:
        doc.close()


def test_delete_save_undo_restores_nav(qapp, tmp_path):
    doc = PdfDocument(_toc_links_pdf(tmp_path / "nav.pdf"))
    try:
        doc.delete_pages([2])
        doc.save_as(tmp_path / "deleted.pdf")  # page objects must survive a save
        assert doc.undo()
        _assert_nav(doc, tmp_path / "out.pdf", "delete, save, undo")
    finally:
        doc.close()


def test_delete_undo_redo_undo_restores_nav(qapp, tmp_path):
    doc = PdfDocument(_toc_links_pdf(tmp_path / "nav.pdf"))
    try:
        doc.delete_pages([0, 2])
        assert doc.undo()
        assert doc.redo()
        assert doc.page_count == 1
        assert doc.undo()
        _assert_nav(doc, tmp_path / "out.pdf", "delete, undo, redo, undo")
    finally:
        doc.close()


def test_edit_then_delete_undo_both(qapp, tmp_path):
    doc = PdfDocument(_toc_links_pdf(tmp_path / "nav.pdf"))
    try:
        original = _texts(doc.raw)
        xref = doc.raw.page_xref(2)
        textedit.add_text(doc, 2, 72, 150, "added")
        doc.delete_pages([2])
        assert doc.undo()
        assert "added" in doc.raw[2].get_text()
        assert doc.undo()
        assert _texts(doc.raw) == original
        assert doc.raw.page_xref(2) == xref
        _assert_nav(doc, tmp_path / "out.pdf", "edit, delete, undo both")
    finally:
        doc.close()


def test_add_text_undo_redo_keeps_page_xref(qapp, tmp_path):
    doc = PdfDocument(_toc_links_pdf(tmp_path / "nav.pdf"))
    try:
        xref = doc.raw.page_xref(2)
        textedit.add_text(doc, 2, 72, 150, "added")
        assert doc.raw.page_xref(2) == xref
        assert doc.undo()
        assert doc.raw.page_xref(2) == xref
        assert doc.redo()
        assert doc.raw.page_xref(2) == xref
        assert "added" in doc.raw[2].get_text()
    finally:
        doc.close()


def _field_pdf(path: Path) -> Path:
    """2 unrotated pages; page 1 has text field "customer_name" and a highlight."""
    doc = pymupdf.open()
    for i in range(2):
        doc.new_page().insert_text((72, 72), f"Page {i + 1}", fontsize=24)
    page = doc[0]
    page.add_highlight_annot(page.search_for("Page 1")[0])
    widget = pymupdf.Widget()
    widget.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
    widget.field_name = "customer_name"
    widget.rect = pymupdf.Rect(72, 200, 272, 224)
    page.add_widget(widget)
    return _save_pdf(doc, path)


def test_add_text_undo_redo_keeps_one_field_and_highlight(qapp, tmp_path):
    doc = PdfDocument(_field_pdf(tmp_path / "field.pdf"))
    try:
        textedit.add_text(doc, 0, 72, 300, "added")
        assert doc.undo()
        assert doc.redo()
        out = tmp_path / "out.pdf"
        doc.save_as(out)
    finally:
        doc.close()
    reopened = pymupdf.open(out)
    try:
        page = reopened[0]
        assert [w.field_name for w in page.widgets()] == ["customer_name"]
        assert [a.type[1] for a in page.annots()] == ["Highlight"]
        assert "added" in page.get_text()
        fields = [
            w.field_name for p in reopened for w in p.widgets()
            if w.field_name == "customer_name"
        ]
        assert fields == ["customer_name"]
    finally:
        reopened.close()


# -- B. damaged file (#2) --------------------------------------------------------


def _four_page_bytes() -> bytes:
    doc = pymupdf.open()
    for i in range(4):
        page = doc.new_page()
        for j in range(40):  # enough content that page 1 survives truncation
            page.insert_text((72, 72 + j * 15), f"Page {i + 1} line {j}", fontsize=8)
    data = doc.tobytes()
    doc.close()
    return data


def truncated_pdf(path: Path) -> Path:
    """Cut to a third: pages 2-4 come back empty after repair."""
    data = _four_page_bytes()
    path.write_bytes(data[: len(data) // 3])
    return path


def shifted_xref_pdf(path: Path) -> Path:
    """startxref off by 7 bytes: MuPDF repairs it, every page intact."""
    data = _four_page_bytes()
    m = re.search(rb"startxref\s+(\d+)", data)
    shifted = str(int(m.group(1)) + 7).encode()
    path.write_bytes(data[: m.start(1)] + shifted + data[m.end(1):])
    return path


def test_truncated_file_is_damaged_and_save_refuses(qapp, tmp_path):
    damaged = truncated_pdf(tmp_path / "damaged.pdf")
    original = damaged.read_bytes()
    pdf = PdfDocument(damaged)
    try:
        assert pdf.was_repaired is True
        assert pdf.is_damaged is True
        pdf.rotate_pages([0], 90)
        with pytest.raises(PdfEditError, match="damaged"):
            pdf.save()
        assert damaged.read_bytes() == original

        recovered = tmp_path / "damaged (recovered).pdf"
        pdf.save_as(recovered)
        assert pdf.was_repaired is False and pdf.is_damaged is False
        assert damaged.read_bytes() == original
        pdf.rotate_pages([0], 90)  # the open document still works
        pdf.save()  # now saves to the recovered copy
        with pymupdf.open(str(recovered)) as check:
            assert check.page_count == 4 and check[0].rotation == 180
            assert "Page 1 line 0" in check[0].get_text()
    finally:
        pdf.close()


def test_damaged_file_still_works_after_save_as(qapp, tmp_path):
    """Was: save_as garbage-collected the dangling page-tree refs, then the
    next page load raised "cycle in page tree", in the open document and in
    the file written."""
    pdf = PdfDocument(truncated_pdf(tmp_path / "damaged.pdf"))
    try:
        pdf.save_as(tmp_path / "damaged (recovered).pdf")
        pdf.rotate_pages([0], 90)  # reloads every page
        PdfDocument(tmp_path / "damaged (recovered).pdf").close()
    finally:
        pdf.close()


def test_recovered_file_is_whole_with_the_same_text(qapp, tmp_path):
    pdf = PdfDocument(truncated_pdf(tmp_path / "damaged.pdf"))
    try:
        assert pdf.is_damaged and not pdf.is_dirty  # the clean-up isn't an edit
        shown = [page.get_text() for page in pdf.raw]
        assert "Page 1 line 0" in shown[0]
        recovered = tmp_path / "damaged (recovered).pdf"
        pdf.save_as(recovered)
    finally:
        pdf.close()
    with pymupdf.open(str(recovered)) as check:
        assert check.is_repaired is False
        assert [page.get_text() for page in check] == shown


def test_deleted_page_of_damaged_file_is_not_saved(qapp, tmp_path):
    pdf = PdfDocument(truncated_pdf(tmp_path / "damaged.pdf"))
    try:
        pdf.delete_pages([0])
        out = tmp_path / "out.pdf"
        pdf.save_as(out)
    finally:
        pdf.close()
    with pymupdf.open(str(out)) as check:
        assert check.page_count == 3
        assert _stray_pages(check) == set()
        assert all("Page 1 line" not in page.get_text() for page in check)


def _page_objects(raw: pymupdf.Document) -> list[tuple[int, tuple[str, str]]]:
    return [(raw.page_xref(i), raw.xref_get_key(raw.page_xref(i), "Contents"))
            for i in range(raw.page_count)]


def test_cleanup_leaves_intact_files_alone(qapp, make_pdf):
    path = make_pdf()
    with pymupdf.open(str(path)) as plain:
        expected = _page_objects(plain)
        document_mod._mend_damaged(plain)  # even when called on it directly
        assert _page_objects(plain) == expected
    pdf = PdfDocument(path)
    try:
        assert _page_objects(pdf.raw) == expected
    finally:
        pdf.close()


def test_harmless_repair_is_not_damaged(qapp, tmp_path):
    path = shifted_xref_pdf(tmp_path / "shifted.pdf")
    pdf = PdfDocument(path)
    try:
        assert pdf.was_repaired is True
        assert pdf.is_damaged is False
        pdf.rotate_pages([0], 90)
        pdf.save()
        with pymupdf.open(str(path)) as check:
            assert check[0].rotation == 90 and check.page_count == 4
    finally:
        pdf.close()


def test_intact_file_is_not_repaired(qapp, make_pdf):
    pdf = PdfDocument(make_pdf())
    try:
        assert pdf.was_repaired is False and pdf.is_damaged is False
    finally:
        pdf.close()


# -- C. failed edit (#4) -----------------------------------------------------------


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


def change_on_disk(path: Path) -> bytes:
    """Replace ``path`` with a one-page "THEIRS" PDF, as another program
    would (new content, later mtime). Returns the bytes written."""
    other = pymupdf.open()
    other.new_page().insert_text((72, 72), "THEIRS", fontsize=24)
    theirs = other.tobytes()
    other.close()
    path.write_bytes(theirs)
    st = path.stat()
    os.utime(path, (st.st_atime + 10, st.st_mtime + 10))
    return theirs


def test_save_refuses_when_file_changed_on_disk(qapp, make_pdf):
    path = make_pdf("mine.pdf")
    doc = PdfDocument(path)
    try:
        theirs_bytes = change_on_disk(path)
        doc.rotate_pages([0], 90)
        with pytest.raises(FileChangedError, match="changed by another program"):
            doc.save()
        assert path.read_bytes() == theirs_bytes
        assert doc.is_dirty
    finally:
        doc.close()


def test_forced_save_overwrites_changed_file(qapp, make_pdf):
    path = make_pdf("mine.pdf")
    doc = PdfDocument(path)
    try:
        change_on_disk(path)
        doc.rotate_pages([0], 90)
        doc.save(force=True)
        with pymupdf.open(str(path)) as check:
            assert check.page_count == 3 and check[0].rotation == 90
        doc.rotate_pages([0], 90)
        doc.save()  # the stamp is now our own write
    finally:
        doc.close()


def test_saving_twice_does_not_trip_the_check(qapp, make_pdf):
    path = make_pdf()
    doc = PdfDocument(path)
    try:
        for _ in range(2):
            doc.rotate_pages([0], 90)
            doc.save()
        with pymupdf.open(str(path)) as check:
            assert check[0].rotation == 180
    finally:
        doc.close()


def test_save_recreates_a_deleted_file(qapp, make_pdf):
    path = make_pdf()
    doc = PdfDocument(path)
    try:
        path.unlink()
        doc.rotate_pages([0], 90)
        doc.save()
        with pymupdf.open(str(path)) as check:
            assert check[0].rotation == 90
    finally:
        doc.close()


def test_save_as_does_not_check_its_target(qapp, make_pdf):
    path, other = make_pdf("a.pdf"), make_pdf("b.pdf")
    doc = PdfDocument(path)
    try:
        change_on_disk(path)
        doc.save_as(other)  # the user picked it in a dialog
        doc.rotate_pages([0], 90)
        doc.save()  # stamp follows the new path
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


def test_replace_span_keeps_link_in_indirect_annots(qapp, tmp_path):
    src = tmp_path / "indirect.pdf"
    doc = pymupdf.open()
    doc.new_page()
    doc.new_page()
    page = doc[0]
    page.insert_text((72, 100), "Click here now", fontsize=12)
    page.insert_link({
        "kind": pymupdf.LINK_GOTO, "from": pymupdf.Rect(72, 88, 160, 104),
        "page": 1, "to": pymupdf.Point(0, 0),
    })
    kind, value = doc.xref_get_key(page.xref, "Annots")
    assert kind == "array"
    x = doc.get_new_xref()
    doc.update_object(x, value)
    doc.xref_set_key(page.xref, "Annots", f"{x} 0 R")
    assert doc.xref_get_key(page.xref, "Annots")[0] == "xref"
    _save_pdf(doc, src)

    pdf = PdfDocument(src)
    try:
        before = _link_state(pdf.raw[0])
        assert len(before) == 1
        span = textedit.span_at(pdf, 0, 80, 97)
        textedit.replace_span(pdf, 0, span, "Tap here now")
        assert "Tap" in pdf.raw[0].get_text()
        assert _link_state(pdf.raw[0]) == before
    finally:
        pdf.close()


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


# -- J. deleted pages leave nothing behind in the saved file (#18) ------------------


def _form_pdf(path: Path, hidden: bool = False) -> Path:
    """2 pages: text field "name" = "Gideon" on page 1, "ssn" = "123-45-6789"
    on page 2; with ``hidden``, also a data-only field (no widget) in /Fields."""
    doc = pymupdf.open()
    for i in range(2):
        doc.new_page().insert_text((72, 72), f"Page {i + 1}", fontsize=24)
    for pno, name, value in ((0, "name", "Gideon"), (1, "ssn", "123-45-6789")):
        widget = pymupdf.Widget()
        widget.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
        widget.field_name = name
        widget.field_value = value
        widget.rect = pymupdf.Rect(72, 200, 272, 224)
        doc[pno].add_widget(widget)
    if hidden:
        x = doc.get_new_xref()
        doc.update_object(x, "<</FT/Tx/T(hidden)/V(keep)>>")
        cat = doc.pdf_catalog()
        fields = _ref_array(doc, cat, "AcroForm/Fields") + [x]
        doc.xref_set_key(
            cat, "AcroForm/Fields", "[" + " ".join(f"{f} 0 R" for f in fields) + "]"
        )
    return _save_pdf(doc, path)


def _fields(raw: pymupdf.Document) -> list[int]:
    """Top-level entries of /AcroForm/Fields."""
    holder = document_mod._fields_holder(raw)
    assert holder is not None
    return _ref_array(raw, *holder)


def _field_names(raw: pymupdf.Document) -> list[str]:
    return [raw.xref_get_key(x, "T")[1] for x in _fields(raw)]


def _widgets(raw: pymupdf.Document, index: int) -> list[tuple[str, str]]:
    return [(w.field_name, w.field_value) for w in raw[index].widgets()]


def test_scratch_copy_keeps_object_numbers(qapp, tmp_path):
    doc = PdfDocument(_toc_links_pdf(tmp_path / "nav.pdf"))
    try:
        textedit.add_text(doc, 0, 72, 150, "added")
        doc.delete_pages([2])
        raw = doc.raw
        scratch = pymupdf.open(stream=raw.tobytes(garbage=0), filetype="pdf")
        try:
            assert scratch.xref_length() == raw.xref_length()
            for x in range(1, raw.xref_length()):
                assert scratch.xref_object(x, compressed=True) == raw.xref_object(
                    x, compressed=True
                ), x
        finally:
            scratch.close()
    finally:
        doc.close()


def test_deleted_page_not_saved_and_undo_restores_it(qapp, tmp_path):
    doc = PdfDocument(_toc_links_pdf(tmp_path / "nav.pdf"))
    try:
        contents = doc.raw[2].get_contents()
        doc.delete_pages([2])  # the named destination "sec3" still points at it
        out = tmp_path / "deleted.pdf"
        doc.save_as(out)
        reopened = pymupdf.open(out)
        try:
            assert reopened.page_count == 2
            assert _stray_pages(reopened) == set()
            for c in contents:
                assert c >= reopened.xref_length() or not reopened.xref_is_stream(c)
        finally:
            reopened.close()
        assert doc.undo()
        assert doc.page_count == 3
        _assert_nav(doc, tmp_path / "out.pdf", "delete, save, undo")
    finally:
        doc.close()


def test_deleted_page_form_field_not_saved(qapp, tmp_path):
    doc = PdfDocument(_form_pdf(tmp_path / "form.pdf"))
    try:
        doc.delete_pages([1])
        out = tmp_path / "deleted.pdf"
        doc.save_as(out)
        reopened = pymupdf.open(out)
        try:
            assert b"123-45-6789" not in reopened.tobytes(expand=255)
            assert _widgets(reopened, 0) == [("name", "Gideon")]
            assert _field_names(reopened) == ["name"]
        finally:
            reopened.close()
        assert doc.undo()
        assert _widgets(doc.raw, 1) == [("ssn", "123-45-6789")]
        out = tmp_path / "undone.pdf"
        doc.save_as(out)
        reopened = pymupdf.open(out)
        try:
            assert _widgets(reopened, 1) == [("ssn", "123-45-6789")]
            assert sorted(_field_names(reopened)) == ["name", "ssn"]
        finally:
            reopened.close()
    finally:
        doc.close()


def test_radio_group_keeps_button_on_kept_page(qapp, tmp_path):
    """One radio group, a button on each page; indirect /AcroForm and /Kids."""
    src = tmp_path / "radio.pdf"
    doc = pymupdf.open()
    for i in range(2):
        doc.new_page().insert_text((72, 72), f"Page {i + 1}", fontsize=24)
    group, kids, af = doc.get_new_xref(), doc.get_new_xref(), doc.get_new_xref()
    buttons = []
    for i in range(2):
        x = doc.get_new_xref()
        doc.update_object(
            x, f"<</Type/Annot/Subtype/Widget/Parent {group} 0 R/P {doc[i].xref} 0 R"
            "/Rect[72 200 92 220]/AS/Off>>",
        )
        doc.xref_set_key(doc[i].xref, "Annots", f"[{x} 0 R]")
        buttons.append(x)
    doc.update_object(kids, f"[{buttons[0]} 0 R {buttons[1]} 0 R]")
    doc.update_object(group, f"<</FT/Btn/Ff 49152/T(choice)/V/Off/Kids {kids} 0 R>>")
    doc.update_object(af, f"<</Fields[{group} 0 R]>>")
    doc.xref_set_key(doc.pdf_catalog(), "AcroForm", f"{af} 0 R")
    _save_pdf(doc, src)

    pdf = PdfDocument(src)
    try:
        pdf.delete_pages([1])
        out = tmp_path / "out.pdf"
        pdf.save_as(out)
    finally:
        pdf.close()
    reopened = pymupdf.open(out)
    try:
        assert reopened.xref_get_key(reopened.pdf_catalog(), "AcroForm")[0] == "xref"
        assert _field_names(reopened) == ["choice"]
        (group,) = _fields(reopened)
        (button,) = _ref_array(reopened, group, "Kids")
        assert reopened.xref_get_key(button, "P")[1] == f"{reopened[0].xref} 0 R"
    finally:
        reopened.close()


def test_data_only_field_survives_delete(qapp, tmp_path):
    doc = PdfDocument(_form_pdf(tmp_path / "form.pdf", hidden=True))
    try:
        doc.delete_pages([1])
        out = tmp_path / "out.pdf"
        doc.save_as(out)
    finally:
        doc.close()
    reopened = pymupdf.open(out)
    try:
        assert _field_names(reopened) == ["name", "hidden"]
        assert reopened.xref_get_key(_fields(reopened)[1], "V")[1] == "keep"
        assert b"123-45-6789" not in reopened.tobytes(expand=255)
    finally:
        reopened.close()


def test_calculated_field_on_deleted_page_leaves_calc_order(qapp, tmp_path):
    """Both fields calculated and listed in /AcroForm/CO; "ssn" is on page 2."""
    raw = pymupdf.open(_form_pdf(tmp_path / "form.pdf"))
    calc = [next(raw[i].widgets()).xref for i in range(2)]
    for x in calc:
        raw.xref_set_key(x, "AA", "<</C<</S/JavaScript/JS(event.value=event.value;)>>>>")
    raw.xref_set_key(
        raw.pdf_catalog(), "AcroForm/CO", "[" + " ".join(f"{x} 0 R" for x in calc) + "]"
    )
    src = _save_pdf(raw, tmp_path / "calc.pdf")

    doc = PdfDocument(src)
    try:
        doc.delete_pages([1])
        out = tmp_path / "out.pdf"
        doc.save_as(out)
    finally:
        doc.close()
    reopened = pymupdf.open(out)
    try:
        assert b"123-45-6789" not in reopened.tobytes(expand=255)
        co = _ref_array(reopened, *document_mod._fields_holder(reopened, "CO"))
        assert [reopened.xref_get_key(x, "T")[1] for x in co] == ["name"]
        assert _widgets(reopened, 0) == [("name", "Gideon")]
    finally:
        reopened.close()


def test_ocr_replaced_page_drops_field_until_undo(qapp, tmp_path):
    doc = PdfDocument(_form_pdf(tmp_path / "form.pdf"))
    blank = pymupdf.open()
    blank.new_page().insert_text((72, 72), "Page 2", fontsize=24)
    after = blank.tobytes()
    blank.close()
    try:
        doc.replace_page_bytes(1, "OCR page", after)
        out = tmp_path / "ocr.pdf"
        doc.save_as(out)
        reopened = pymupdf.open(out)
        try:
            assert b"123-45-6789" not in reopened.tobytes(expand=255)
            assert _field_names(reopened) == ["name"]
        finally:
            reopened.close()
        assert doc.undo()
        out = tmp_path / "undone.pdf"
        doc.save_as(out)
        reopened = pymupdf.open(out)
        try:
            assert _widgets(reopened, 1) == [("ssn", "123-45-6789")]
            assert sorted(_field_names(reopened)) == ["name", "ssn"]
        finally:
            reopened.close()
    finally:
        doc.close()


def test_page_outside_tree_at_open_is_kept(qapp, tmp_path):
    """A file that already keeps a page object outside the page tree (here:
    only a named destination points at it) keeps it through our saves."""
    src = _toc_links_pdf(tmp_path / "nav.pdf")
    raw = pymupdf.open(src)
    raw.delete_pages([2])  # PyMuPDF's delete leaves the object behind
    stray = tmp_path / "stray.pdf"
    raw.save(str(stray), garbage=1)
    raw.close()

    doc = PdfDocument(stray)
    try:
        (orphan,) = doc._orphans_at_open
        doc.rotate_pages([0], 90)
        out = tmp_path / "rotated.pdf"
        doc.save_as(out)
        reopened = pymupdf.open(out)
        try:
            assert _stray_pages(reopened) == {orphan}
        finally:
            reopened.close()
        doc.delete_pages([1])  # now Save does clean, but not the old orphan
        out = tmp_path / "deleted.pdf"
        doc.save_as(out)
        reopened = pymupdf.open(out)
        try:
            assert len(_stray_pages(doc.raw)) == 2
            assert _stray_pages(reopened) == {orphan}
        finally:
            reopened.close()
    finally:
        doc.close()


def test_save_without_dead_objects_skips_scratch_copy(qapp, tmp_path, monkeypatch):
    doc = PdfDocument(_form_pdf(tmp_path / "form.pdf"))
    try:
        doc.rotate_pages([0], 90)
        opened = []
        real_open = pymupdf.open

        def spy(*args, **kwargs):
            opened.append((args, kwargs))
            return real_open(*args, **kwargs)

        monkeypatch.setattr(document_mod.pymupdf, "open", spy)
        doc.save_as(tmp_path / "out.pdf")
        assert opened == []
    finally:
        doc.close()


def test_edit_undo_redo_leaves_no_stray_page(qapp, tmp_path, monkeypatch):
    doc = PdfDocument(_toc_links_pdf(tmp_path / "nav.pdf"))
    try:
        textedit.add_text(doc, 2, 72, 150, "added")
        assert doc.undo()
        assert doc.redo()  # undo and redo both restore via a temporary page
        assert _stray_pages(doc.raw) == set()
        opened = []
        real_open = pymupdf.open

        def spy(*args, **kwargs):
            opened.append((args, kwargs))
            return real_open(*args, **kwargs)

        monkeypatch.setattr(document_mod.pymupdf, "open", spy)
        doc.save_as(tmp_path / "out.pdf")
        assert opened == []
    finally:
        doc.close()
