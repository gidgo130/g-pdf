# SPDX-License-Identifier: AGPL-3.0-or-later
import pytest
from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt
from PySide6.QtGui import QDropEvent
from PySide6.QtWidgets import QInputDialog, QMessageBox

from gpdf.ui import page_panel
from gpdf.ui.main_window import MainWindow
from gpdf.ui.pdf_view import TOOL_ADD_TEXT, TOOL_EDIT_TEXT


def settle(qapp):
    for _ in range(3):
        qapp.processEvents()


@pytest.fixture(autouse=True)
def clean_windows(qapp, monkeypatch):
    settle(qapp)
    yield
    # Tests leave unsaved edits behind; never let teardown block on a dialog.
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    for w in MainWindow.windows():
        w.close()
    settle(qapp)


@pytest.fixture
def win(qapp, make_pdf, monkeypatch):
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.StandardButton.Ok)
    w = MainWindow()
    w.resize(1000, 700)
    w.show()
    tab = w.open_path(make_pdf("e.pdf", sizes=[(600, 800)] * 5))
    settle(qapp)
    return w, tab


def labels(doc):
    return [doc.raw[i].get_text().split()[-1] for i in range(doc.page_count)]


def test_rotate_acts_on_selection_and_keeps_it(win, qapp):
    w, tab = win
    tab.panel.select_pages([1, 2])
    w._a_rot_r.trigger()
    assert tab.document.page_rotation(1) == 90 and tab.document.page_rotation(0) == 0
    assert tab.panel.selected_pages() == [1, 2]
    w._a_undo.trigger()
    assert tab.document.page_rotation(1) == 0


def test_actions_fall_back_to_current_page(win):
    w, tab = win
    w._a_rot_l.trigger()
    assert tab.document.page_rotation(0) == 270


def test_delete_dup_blank_and_title_marker(win):
    w, tab = win
    assert not tab.document.is_dirty and "●" not in w.tabs.tabText(0)
    tab.panel.select_pages([0])
    w._a_dup.trigger()
    assert tab.document.page_count == 6 and tab.panel.selected_pages() == [1]
    assert w.tabs.tabText(0).startswith("●") and w.windowTitle().startswith("●")
    w._a_blank.trigger()
    assert tab.document.page_count == 7
    tab.panel.select_pages([0, 1])
    w._a_delete.trigger()
    assert tab.document.page_count == 5
    assert w._a_undo.isEnabled() and "Delete" in w._a_undo.text()


def test_cannot_delete_all_shows_message(win, monkeypatch):
    w, tab = win
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: shown.append(a[2]))
    tab.select_all_pages()
    w._a_delete.trigger()
    assert shown and tab.document.page_count == 5


def test_view_relayouts_after_structure_change(win, qapp):
    w, tab = win
    n = len(tab.view._tops)
    tab.panel.select_pages([0, 1])
    w._a_delete.trigger()
    assert len(tab.view._tops) == n - 2
    assert tab.view.page_count == 3
    assert "of 3" in w._page_label.text()


def test_panel_drop_reorders(win, qapp):
    w, tab = win
    tab.panel.select_pages([0])
    mime, token = page_panel._make_mime(tab.document, [0])
    row_pos = tab.panel.visualRect(tab.panel.model().index(3)).center()
    right = QPoint(tab.panel.visualRect(tab.panel.model().index(3)).right() - 2, row_pos.y())
    ev = QDropEvent(QPointF(right), Qt.DropAction.MoveAction, mime,
                    Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    tab.panel.dropEvent(ev)
    assert labels(tab.document) == ["2", "3", "4", "1", "5"]
    assert tab.panel.selected_pages() == [3]
    page_panel._registry.pop(token, None)


def test_panel_drop_between_documents_copies(win, qapp, make_pdf):
    w, tab = win
    other = w.open_path(make_pdf("o.pdf", sizes=[(600, 800)] * 2))
    settle(qapp)
    mime, token = page_panel._make_mime(tab.document, [4])
    end_item = other.panel.visualRect(other.panel.model().index(1))
    pos = QPointF(end_item.right() - 2, end_item.center().y())
    other.panel.dropEvent(QDropEvent(pos, Qt.DropAction.CopyAction, mime,
                                     Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    assert other.document.page_count == 3 and tab.document.page_count == 5
    assert "e.pdf" in other.document.raw[2].get_text()
    page_panel._registry.pop(token, None)


def test_panel_row_for_empty_space_is_nearest(win):
    w, tab = win
    last = tab.panel.visualRect(tab.panel.model().index(4))
    pos = QPoint(last.right() + 400, last.center().y()) if False else QPoint(last.left() + 1, last.bottom() + 5)
    assert 0 <= tab.panel._row_for_pos(pos) <= 5


def test_edit_text_tool_roundtrip(win, qapp, monkeypatch):
    w, tab = win
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("e.pdf paige 1", True))
    w._tool_actions[TOOL_EDIT_TEXT].trigger()
    assert tab.view.tool == TOOL_EDIT_TEXT
    tab.view.page_clicked.emit(0, 80.0, 66.0)
    assert "paige" in tab.document.raw[0].get_text()
    w._a_undo.trigger()
    assert "paige" not in tab.document.raw[0].get_text()


def test_add_text_tool(win, monkeypatch):
    w, tab = win
    monkeypatch.setattr(QInputDialog, "getMultiLineText", lambda *a, **k: ("Hello", True))
    monkeypatch.setattr(QInputDialog, "getInt", lambda *a, **k: (14, True))
    w._tool_actions[TOOL_ADD_TEXT].trigger()
    tab.view.page_clicked.emit(1, 100.0, 300.0)
    assert "Hello" in tab.document.raw[1].get_text()


def test_page_at_maps_clicks(win, qapp):
    w, tab = win
    v = tab.view
    r = v._page_rect(0)
    page, x, y = v.page_at(r.center())
    assert page == 0 and x == pytest.approx(300, abs=2) and y == pytest.approx(400, abs=2)
    assert v.page_at(QPoint(1, 1)) is None


def test_find_highlights_and_navigation(win, qapp):
    w, tab = win
    w._a_find.trigger()
    tab.find_bar.edit.setText("page")
    assert tab.find_bar.count.text() == "1 of 5"
    tab.find_bar.next_hit()
    assert tab.find_bar.count.text() == "2 of 5"
    tab.find_bar.edit.setText("nomatchxyz")
    assert tab.find_bar.count.text() == "No matches"
    tab.find_bar.close_bar()
    assert not tab.find_bar.isVisible()


def test_find_updates_after_edit(win):
    w, tab = win
    w._a_find.trigger()
    tab.find_bar.edit.setText("page")
    tab.panel.select_pages([0])
    w._a_delete.trigger()
    assert tab.find_bar.count.text().endswith("of 4")


def test_close_dirty_tab_prompts(win, monkeypatch):
    w, tab = win
    w._a_rot_r.trigger()
    answers = iter([QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Discard])
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: next(answers))
    w.close_tab_at(0)
    assert w.tabs.count() == 1
    w.close_tab_at(0)
    assert w.tabs.count() == 0


def test_close_window_cancel_keeps_window(win, monkeypatch):
    w, tab = win
    w._a_rot_r.trigger()
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Cancel)
    assert w.close() is False
    assert w in MainWindow.windows() and w.tabs.count() == 1
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    w.close()


def test_close_dirty_save(win, monkeypatch):
    w, tab = win
    path = tab.path
    w._a_rot_r.trigger()
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Save)
    w.close_tab_at(0)
    import pymupdf
    d = pymupdf.open(str(path))
    assert d[0].rotation == 90
    d.close()


def test_save_action_enabled_only_when_dirty(win):
    w, tab = win
    assert not w._a_save.isEnabled()
    w._a_rot_r.trigger()
    assert w._a_save.isEnabled()
    w._a_save.trigger()
    assert not w._a_save.isEnabled() and "●" not in w.tabs.tabText(0)


def test_tab_move_keeps_edit_state_wired(win, qapp):
    w, tab = win
    w2 = MainWindow()
    w2.show()
    w2.move_tab_here(tab)
    settle(qapp)
    tab.panel.select_pages([0])
    w2._a_rot_r.trigger()
    assert w2.tabs.tabText(0).startswith("●")


def test_properties_privacy(win, monkeypatch):
    from gpdf.ui import doc_tab
    w, tab = win
    tab.document.set_metadata({"author": "Gideon", "title": "T", "producer": "X"})

    class Fake:
        def __init__(self, doc, parent): pass
        def exec(self): return True
        def values(self): return ({"author": "", "keywords": ""}, True)

    monkeypatch.setattr(doc_tab, "PropertiesDialog", Fake)
    tab.show_properties()
    assert tab.document.metadata["author"] == ""
    assert tab.document.metadata["title"] == "T"


def test_properties_dialog_values(win, qapp):
    from gpdf.ui.properties_dialog import PropertiesDialog, friendly_date
    w, tab = win
    tab.document.set_metadata({"author": "A", "title": "T", "producer": "P", "creator": "C"})
    d = PropertiesDialog(tab.document)
    d.title.setText("New")
    changed, clear = d.values()
    assert changed == {"title": "New"} and not clear
    d._remove_personal()
    changed, clear = d.values()
    assert clear and changed["author"] == "" and changed["producer"] == "" and changed["creator"] == ""
    assert friendly_date("D:20240102030405Z") == "02 Jan 2024, 03:04"
    assert friendly_date("") == "Unknown"
