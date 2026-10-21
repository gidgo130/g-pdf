# SPDX-License-Identifier: AGPL-3.0-or-later
import pymupdf
import pytest

from gpdf.core.document import PdfDocument, PdfEditError
from gpdf.core.history import CompositeCommand, History, Command


def texts(doc):
    return [doc.raw[i].get_text().strip() for i in range(doc.page_count)]


@pytest.fixture
def five(make_pdf, qapp):
    d = PdfDocument(make_pdf("five.pdf", sizes=[(600 + i, 200) for i in range(5)]))
    yield d
    d.close()


def labels(doc):
    return [t.split()[-1] for t in texts(doc)]


def test_move_and_undo(five):
    assert five.move_pages([0, 1], 4) == [2, 3]
    assert labels(five) == ["3", "4", "1", "2", "5"]
    assert five.is_dirty
    five.undo()
    assert labels(five) == ["1", "2", "3", "4", "5"]
    assert not five.is_dirty
    five.redo()
    assert labels(five) == ["3", "4", "1", "2", "5"]


def test_move_noop(five):
    assert five.move_pages([1], 1) == []
    assert five.move_pages([1], 2) == []
    assert not five.can_undo


def test_rotate_undo(five):
    five.rotate_pages([0, 2], 90)
    assert five.page_rotation(0) == 90 and five.page_sizes[0] == pytest.approx((200, 600))
    five.rotate_pages([0], -180)
    assert five.page_rotation(0) == 270
    five.undo(); five.undo()
    assert five.page_rotation(0) == 0 and five.page_rotation(2) == 0


def test_delete_undo_positions(five):
    five.delete_pages([0, 2, 4])
    assert labels(five) == ["2", "4"]
    five.undo()
    assert labels(five) == ["1", "2", "3", "4", "5"]
    assert five.page_sizes[4][0] == pytest.approx(604)
    five.redo()
    assert labels(five) == ["2", "4"]


def test_cannot_delete_all(five):
    with pytest.raises(PdfEditError):
        five.delete_pages(range(5))
    assert five.page_count == 5 and not five.can_undo


def test_delete_preserves_rotation_on_undo(five):
    five.rotate_pages([1], 90)
    five.delete_pages([1])
    five.undo()
    assert five.page_rotation(1) == 90


def test_duplicate(five):
    assert five.duplicate_pages([1, 2]) == [3, 4]
    assert labels(five) == ["1", "2", "3", "2", "3", "4", "5"]
    five.undo()
    assert labels(five) == ["1", "2", "3", "4", "5"]


def test_insert_blank_matches_neighbour(five):
    five.insert_blank(3)
    assert five.page_count == 6
    assert five.page_sizes[3] == pytest.approx(five.page_sizes[2])
    assert texts(five)[3] == ""
    five.undo()
    assert five.page_count == 5


def test_insert_from_other_doc_and_file(five, make_pdf, qapp):
    other = PdfDocument(make_pdf("other.pdf", sizes=[(600, 100)] * 2))
    try:
        five.insert_pages_from(other, [1], 0)
        assert texts(five)[0].endswith("2") and "other.pdf" in texts(five)[0]
        five.insert_file(other.path, 5)
        assert five.page_count == 8
        five.undo(); five.undo()
        assert five.page_count == 5
    finally:
        other.close()


def test_insert_bad_file(five, tmp_path):
    bad = tmp_path / "x.pdf"
    bad.write_text("nope")
    with pytest.raises(PdfEditError):
        five.insert_file(bad, 0)


def test_extract(five, tmp_path):
    out = tmp_path / "ex.pdf"
    five.extract_pages([4, 1], out)
    d = pymupdf.open(str(out))
    assert d.page_count == 2 and d[0].get_text().strip().endswith("2")
    d.close()


def test_save_roundtrip_and_clean(five, tmp_path):
    five.move_pages([4], 0)
    five.save()
    assert not five.is_dirty
    d = pymupdf.open(str(five.path))
    assert d[0].get_text().strip().endswith("5")
    d.close()
    assert not list(tmp_path.glob(".gpdf-*"))
    five.undo()
    assert five.is_dirty
    five.redo()
    assert not five.is_dirty


def test_save_as_changes_path(five, tmp_path):
    five.save_as(tmp_path / "new.pdf")
    assert five.name == "new.pdf" and (tmp_path / "new.pdf").exists()


def test_file_not_locked_while_open(five):
    five.path.unlink()  # would fail on Windows if we held a handle
    assert five.page_count == 5


def test_edit_page_undo_and_failure(five):
    five.edit_page(0, "Add text", lambda p: p.insert_text((20, 100), "HELLO"))
    assert "HELLO" in five.raw[0].get_text()
    five.undo()
    assert "HELLO" not in five.raw[0].get_text()
    five.redo()
    assert "HELLO" in five.raw[0].get_text()

    def boom(p):
        p.insert_text((20, 150), "PARTIAL")
        raise ValueError("x")

    with pytest.raises(ValueError):
        five.edit_page(1, "bad", boom)
    assert "PARTIAL" not in five.raw[1].get_text()
    assert five.page_count == 5
    assert five.undo_label == "Add text"


def test_page_token_changes_on_edit(five):
    t = five.page_token(0)
    five.edit_page(0, "x", lambda p: p.insert_text((20, 100), "A"))
    assert five.page_token(0) != t


def test_metadata_and_privacy(five):
    five.set_metadata({"title": "T", "author": "Me", "producer": "P"})
    assert five.metadata["author"] == "Me"
    five.remove_personal_info()
    assert five.metadata["author"] == "" and five.metadata["title"] == "T"
    five.undo()
    assert five.metadata["author"] == "Me"
    five.undo()
    assert five.metadata["title"] == ""


def test_signals(five, qapp):
    hits = {"s": 0, "st": 0}
    five.structure_changed.connect(lambda: hits.__setitem__("s", hits["s"] + 1))
    five.state_changed.connect(lambda: hits.__setitem__("st", hits["st"] + 1))
    five.rotate_pages([0], 90)
    assert hits["s"] == 1 and hits["st"] >= 1


def test_form_page_delete_undo_keeps_field_name(tmp_path, qapp):
    p = tmp_path / "f.pdf"
    d = pymupdf.open()
    d.new_page(); d.new_page()
    w = pymupdf.Widget()
    w.field_name = "f1"; w.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
    w.rect = pymupdf.Rect(50, 50, 200, 80); w.field_value = "typed"
    d[1].add_widget(w)
    d.save(str(p)); d.close()
    doc = PdfDocument(p)
    try:
        doc.delete_pages([1]); doc.undo()
        assert [w.field_name for w in doc.raw[1].widgets()] == ["f1"]
    finally:
        doc.close()


# -- history unit tests ---------------------------------------------------------

class Dummy(Command):
    def __init__(self, cost=0): self.byte_cost = cost; self.label = "d"; self.n = 0
    def do(self): self.n += 1
    def undo(self): self.n -= 1


def test_history_clean_tracking():
    h = History()
    h.push(Dummy()); h.mark_clean(); assert h.is_clean
    h.push(Dummy()); assert not h.is_clean
    h.undo(); assert h.is_clean
    h.undo(); assert not h.is_clean
    h.redo(); assert h.is_clean


def test_history_clean_unreachable_after_branch():
    h = History()
    h.push(Dummy()); h.push(Dummy()); h.mark_clean()
    h.undo(); h.push(Dummy())
    assert not h.is_clean
    h.undo(); assert not h.is_clean


def test_history_trim_by_bytes():
    h = History(max_bytes=100)
    for _ in range(5):
        h.push(Dummy(60))
    assert h.total_bytes <= 100 or len(h._commands) == 1
    assert h.can_undo


def test_history_failed_push_not_recorded():
    class Bad(Command):
        def do(self): raise RuntimeError
    h = History()
    with pytest.raises(RuntimeError):
        h.push(Bad())
    assert not h.can_undo


def test_composite_rolls_back():
    a, b = Dummy(), Dummy()
    class Bad(Command):
        def do(self): raise RuntimeError
    c = CompositeCommand("c", [a, b, Bad()])
    with pytest.raises(RuntimeError):
        c.do()
    assert a.n == 0 and b.n == 0
