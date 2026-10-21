# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tab / window behaviour. Real mouse drags can't be simulated offscreen, so the
gesture is tested up to the point where it would start a QDrag, and everything
after (drop handling, moving, detaching) is tested by calling the same code
paths directly."""

import pytest
from PySide6.QtCore import QEvent, QMimeData, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QDropEvent, QMouseEvent
from PySide6.QtWidgets import QMessageBox

from gpdf.ui import detachable_tabs as dt
from gpdf.ui.main_window import MainWindow


def settle(qapp):
    for _ in range(3):
        qapp.processEvents()


@pytest.fixture(autouse=True)
def clean_windows(qapp):
    settle(qapp)
    assert MainWindow.windows() == []
    yield
    for window in MainWindow.windows():
        window.close()
    settle(qapp)


def names(window):
    return [window.tabs.tabText(i) for i in range(window.tabs.count())]


def mouse_event(kind, pos, *, buttons=Qt.MouseButton.LeftButton):
    p = QPointF(pos)
    return QMouseEvent(
        kind, p, p, Qt.MouseButton.LeftButton, buttons, Qt.KeyboardModifier.NoModifier
    )


def drop_event(mime, pos=QPointF(5, 5)):
    return QDropEvent(
        pos,
        Qt.DropAction.MoveAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )


# -- opening --------------------------------------------------------------


def test_open_adds_tab_and_updates_chrome(qapp, make_pdf):
    win = MainWindow()
    assert win._stack.currentIndex() == 0  # empty state
    assert not win._a_zoom_in.isEnabled()

    win.open_path(make_pdf("a.pdf"))
    assert names(win) == ["a.pdf"]
    assert win._stack.currentIndex() == 1
    assert win.windowTitle().startswith("a.pdf")
    assert win._page_label.text() == "Page 1 of 3"
    assert win._zoom_label.text().endswith("%")
    assert win._a_zoom_in.isEnabled()


def test_opening_an_already_open_file_focuses_it_even_across_windows(qapp, make_pdf):
    path = make_pdf("a.pdf")
    first, second = MainWindow(), MainWindow()
    tab = first.open_path(path)
    again = second.open_path(path)
    assert again is tab
    assert second.tabs.count() == 0
    assert first.tabs.count() == 1


def test_failed_open_warns_and_adds_nothing(qapp, tmp_path, monkeypatch):
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: shown.append(a))
    bad = tmp_path / "bad.txt"
    bad.write_text("nope")
    win = MainWindow()
    win.open_paths([bad])
    assert len(shown) == 1
    assert win.tabs.count() == 0


def test_view_actions_change_the_view(qapp, make_pdf):
    win = MainWindow()
    win.show()
    tab = win.open_path(make_pdf("a.pdf"))
    qapp.processEvents()
    before = tab.view.zoom
    win._a_zoom_in.trigger()
    assert tab.view.zoom > before
    win._a_actual.trigger()
    assert tab.view.zoom == pytest.approx(1.0)
    win._a_fit.trigger()
    assert tab.view.zoom != pytest.approx(1.0)


# -- moving tabs between windows -----------------------------------------


def test_move_tab_between_windows_keeps_document_alive(qapp, make_pdf):
    a, b = MainWindow(), MainWindow()
    t1 = a.open_path(make_pdf("1.pdf"))
    t2 = a.open_path(make_pdf("2.pdf"))

    b.move_tab_here(t2)

    assert names(a) == ["1.pdf"]
    assert names(b) == ["2.pdf"]
    assert t2.window() is b
    assert b.current_tab() is t2
    assert not t2.document.render(0, 100, 100).isNull()
    assert t1.window() is a


def test_moving_the_last_tab_out_closes_the_source_window(qapp, make_pdf):
    a, b = MainWindow(), MainWindow()
    t1 = a.open_path(make_pdf("1.pdf"))
    b.move_tab_here(t1)
    settle(qapp)
    assert MainWindow.windows() == [b]
    assert names(b) == ["1.pdf"]


def test_move_inserts_at_the_requested_position(qapp, make_pdf):
    a, b = MainWindow(), MainWindow()
    t1 = a.open_path(make_pdf("1.pdf"))
    a.open_path(make_pdf("2.pdf"))
    b.open_path(make_pdf("3.pdf"))
    b.open_path(make_pdf("4.pdf"))

    b.move_tab_here(t1, 1)
    assert names(b) == ["3.pdf", "1.pdf", "4.pdf"]


def test_reorder_within_a_window(qapp, make_pdf):
    win = MainWindow()
    t1 = win.open_path(make_pdf("1.pdf"))
    t2 = win.open_path(make_pdf("2.pdf"))
    t3 = win.open_path(make_pdf("3.pdf"))

    win.move_tab_here(t1, -1)  # to the end
    assert names(win) == ["2.pdf", "3.pdf", "1.pdf"]

    win.move_tab_here(t3, 0)  # to the front
    assert names(win) == ["3.pdf", "2.pdf", "1.pdf"]

    win.move_tab_here(t1, 1)  # before the item at index 1
    assert names(win) == ["3.pdf", "1.pdf", "2.pdf"]
    assert win.current_tab() is t1
    assert t2.window() is win


def test_status_signals_follow_the_tab_to_its_new_window(qapp, make_pdf, monkeypatch):
    calls = []
    original = MainWindow._refresh_status

    def spy(self, *args):
        calls.append(self)
        original(self, *args)

    monkeypatch.setattr(MainWindow, "_refresh_status", spy)

    a, b = MainWindow(), MainWindow()
    a.show()
    b.show()
    a.open_path(make_pdf("1.pdf"))
    t2 = a.open_path(make_pdf("2.pdf"))
    qapp.processEvents()
    b.move_tab_here(t2)
    qapp.processEvents()

    calls.clear()
    t2.view.go_to_page(2)
    qapp.processEvents()

    assert a not in calls  # old window no longer listens to this tab
    assert b in calls
    assert b._page_label.text() == "Page 3 of 3"

    # Close while the spy is still installed so disconnects match the connects.
    for window in MainWindow.windows():
        window.close()
    settle(qapp)


# -- detaching ------------------------------------------------------------


def test_detach_with_other_tabs_leaves_source_open(qapp, make_pdf):
    a = MainWindow()
    a.open_path(make_pdf("1.pdf"))
    t2 = a.open_path(make_pdf("2.pdf"))

    a._on_detach_requested(t2, QPoint(300, 300))
    settle(qapp)

    assert len(MainWindow.windows()) == 2
    assert names(a) == ["1.pdf"]
    new = t2.window()
    assert isinstance(new, MainWindow) and new is not a
    assert names(new) == ["2.pdf"]


def test_detaching_a_single_tab_moves_it_to_a_fresh_window(qapp, make_pdf):
    a = MainWindow()
    t1 = a.open_path(make_pdf("1.pdf"))

    a._on_detach_requested(t1, QPoint(300, 300))
    settle(qapp)

    windows = MainWindow.windows()
    assert len(windows) == 1
    assert windows[0] is not a
    assert names(windows[0]) == ["1.pdf"]
    assert not t1.document.render(0, 50, 50).isNull()


# -- closing --------------------------------------------------------------


def test_closing_the_only_tab_in_the_only_window_shows_empty_state(qapp, make_pdf):
    win = MainWindow()
    tab = win.open_path(make_pdf("1.pdf"))
    doc = tab.document
    win.close_tab_at(0)
    settle(qapp)
    assert not doc.is_open
    assert MainWindow.windows() == [win]
    assert win._stack.currentIndex() == 0


def test_closing_the_last_tab_closes_the_window_when_others_exist(qapp, make_pdf):
    a, b = MainWindow(), MainWindow()
    a.open_path(make_pdf("1.pdf"))
    a.close_tab_at(0)
    settle(qapp)
    assert MainWindow.windows() == [b]


def test_closing_a_window_closes_its_documents(qapp, make_pdf):
    win = MainWindow()
    doc = win.open_path(make_pdf("1.pdf")).document
    win.close()
    settle(qapp)
    assert not doc.is_open
    assert MainWindow.windows() == []


# -- drag gesture and drops ----------------------------------------------


def test_leaving_the_bar_starts_a_tab_drag_but_moving_inside_does_not(
    qapp, make_pdf, monkeypatch
):
    win = MainWindow()
    win.show()
    t1 = win.open_path(make_pdf("1.pdf"))
    win.open_path(make_pdf("2.pdf"))
    qapp.processEvents()

    bar = win.tabs.tabBar()
    dragged = []
    monkeypatch.setattr(bar, "start_tab_drag", lambda tab: dragged.append(tab))

    start = bar.tabRect(0).center()
    bar.mousePressEvent(mouse_event(QEvent.Type.MouseButtonPress, start))
    bar.mouseMoveEvent(mouse_event(QEvent.Type.MouseMove, start + QPoint(6, 1)))
    assert dragged == []

    far = QPoint(start.x(), bar.height() + dt.DETACH_DISTANCE + 40)
    bar.mouseMoveEvent(mouse_event(QEvent.Type.MouseMove, far))
    assert dragged == [t1]

    # Further movement must not start a second drag.
    bar.mouseMoveEvent(mouse_event(QEvent.Type.MouseMove, far + QPoint(5, 5)))
    assert dragged == [t1]


def test_dropping_a_dragged_tab_on_another_windows_bar_moves_it(qapp, make_pdf):
    a, b = MainWindow(), MainWindow()
    t1 = a.open_path(make_pdf("1.pdf"))
    a.open_path(make_pdf("2.pdf"))
    b.show()
    qapp.processEvents()

    token = dt.register_drag(t1)
    mime = dt.make_tab_mime(token)
    event = drop_event(mime)
    b.tabs.tabBar().dropEvent(event)

    assert event.isAccepted()
    assert not dt.drag_is_pending(token)
    assert t1.window() is b
    assert names(a) == ["2.pdf"]
    assert names(b) == ["1.pdf"]


def test_dropping_on_the_page_area_appends_the_tab(qapp, make_pdf):
    a, b = MainWindow(), MainWindow()
    t1 = a.open_path(make_pdf("1.pdf"))
    a.open_path(make_pdf("2.pdf"))
    b.open_path(make_pdf("3.pdf"))

    mime = dt.make_tab_mime(dt.register_drag(t1))
    b.tabs.dropEvent(drop_event(mime))
    assert names(b) == ["3.pdf", "1.pdf"]


def test_drop_with_unknown_token_is_ignored(qapp, make_pdf):
    win = MainWindow()
    win.open_path(make_pdf("1.pdf"))
    mime = QMimeData()
    mime.setData(dt.MIME_TYPE, b"deadbeef")
    event = drop_event(mime)
    win.tabs.tabBar().dropEvent(event)
    assert names(win) == ["1.pdf"]
    assert not event.isAccepted()


def test_pdf_files_dropped_on_the_tab_bar_are_reported(qapp, make_pdf, tmp_path):
    pdf = make_pdf("a.pdf")
    txt = tmp_path / "notes.txt"
    txt.write_text("x")
    win = MainWindow()
    bar = win.tabs.tabBar()
    got = []
    bar.files_dropped.connect(got.append)

    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(txt)), QUrl.fromLocalFile(str(pdf))])
    bar.dropEvent(drop_event(mime))
    assert got == [[str(pdf)]]


def test_pdf_files_dropped_on_the_window_open_in_tabs(qapp, make_pdf):
    win = MainWindow()
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(make_pdf("a.pdf")))])
    win.dropEvent(drop_event(mime))
    assert names(win) == ["a.pdf"]
