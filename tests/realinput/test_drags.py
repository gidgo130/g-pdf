# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tab and thumbnail drags with a real (X11) mouse. See conftest.py."""

import pytest
from PySide6.QtCore import QPoint
from PySide6.QtTest import QTest

from gpdf.ui.main_window import MainWindow

from .conftest import settle


@pytest.fixture(autouse=True)
def clean_windows(qapp, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    settle(qapp, 0.1)
    yield
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard
    )
    for w in MainWindow.windows():
        w.close()
    settle(qapp, 0.2)


def show(qapp, x=0, y=0, w=900, h=650) -> MainWindow:
    win = MainWindow()
    win.setGeometry(x, y, w, h)
    win.show()
    QTest.qWaitForWindowExposed(win)
    settle(qapp)
    return win


def names(win):
    return [win.tabs.tabText(i) for i in range(win.tabs.count())]


def tab_center(win, i) -> QPoint:
    bar = win.tabs.tabBar()
    return bar.mapToGlobal(bar.tabRect(i).center())


def thumb(tab, row, fy=0.5) -> QPoint:
    panel = tab.panel
    r = panel.visualRect(panel.model().index(row))
    return panel.viewport().mapToGlobal(QPoint(r.center().x(), int(r.top() + r.height() * fy)))


def labels(doc):
    return [doc.raw[i].get_text().split()[-1] for i in range(doc.page_count)]


def test_tab_dropped_on_its_own_page_area_detaches(qapp, robot, make_pdf):
    win = show(qapp)
    for n in "abc":
        win.open_path(make_pdf(f"{n}.pdf"))
    settle(qapp)
    start = tab_center(win, 1)
    robot.drag(start, start + QPoint(0, 250)).run()
    assert names(win) == ["a.pdf", "c.pdf"]
    others = [w for w in MainWindow.windows() if w is not win]
    assert len(others) == 1 and names(others[0]) == ["b.pdf"]


def test_tab_dropped_outside_every_window_detaches(qapp, robot, make_pdf):
    win = show(qapp)
    for n in "ab":
        win.open_path(make_pdf(f"{n}.pdf"))
    settle(qapp)
    robot.drag(tab_center(win, 0), QPoint(1300, 820)).run()
    assert names(win) == ["b.pdf"]
    assert len(MainWindow.windows()) == 2


def test_esc_cancels_a_tab_drag(qapp, robot, make_pdf):
    win = show(qapp)
    for n in "ab":
        win.open_path(make_pdf(f"{n}.pdf"))
    settle(qapp)
    robot.drag(tab_center(win, 0), QPoint(1300, 820), release=False).key("Escape").up().run()
    assert names(win) == ["a.pdf", "b.pdf"]
    assert MainWindow.windows() == [win]


def test_thumbnail_drag_reorders_in_a_single_column(qapp, robot, make_pdf):
    win = show(qapp)
    tab = win.open_path(make_pdf("p.pdf", sizes=[(600, 800)] * 4))
    settle(qapp)
    # Press on an unselected page and drag it to the lower half of page 3.
    robot.drag(thumb(tab, 0), thumb(tab, 2, 0.8)).run()
    assert labels(tab.document) == ["2", "3", "1", "4"]
    assert tab.panel.selected_pages() == [2]


def test_thumbnail_drag_copies_to_another_window(qapp, robot, make_pdf):
    a = show(qapp, 0, 0, 700, 650)
    b = show(qapp, 760, 0, 700, 650)
    ta = a.open_path(make_pdf("src.pdf", sizes=[(600, 800)] * 3))
    tb = b.open_path(make_pdf("dst.pdf", sizes=[(600, 800)] * 2))
    settle(qapp)
    robot.drag(thumb(ta, 1), thumb(tb, 1, 0.8)).run()
    assert ta.document.page_count == 3          # source unchanged
    assert tb.document.page_count == 3
    assert tb.document.raw[2].get_text().split()[0] == "src.pdf"
