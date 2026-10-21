# SPDX-License-Identifier: AGPL-3.0-or-later
"""Saving from the UI: damaged files (#2), files changed on disk (#15)."""

import pymupdf
import pytest
from PySide6.QtWidgets import QFileDialog, QMessageBox

from gpdf.ui import doc_tab
from gpdf.ui.main_window import MainWindow
from tests.test_data_safety import change_on_disk, truncated_pdf


def settle(qapp):
    for _ in range(3):
        qapp.processEvents()


@pytest.fixture(autouse=True)
def clean_windows(qapp, monkeypatch):
    settle(qapp)
    yield
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    for w in MainWindow.windows():
        w.close()
    settle(qapp)


def test_opening_damaged_file_warns(qapp, tmp_path, monkeypatch):
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: shown.append(a[2]))
    win = MainWindow()
    win.open_path(truncated_pdf(tmp_path / "broken.pdf"))
    assert len(shown) == 1
    assert shown[0].startswith("broken.pdf is damaged.")
    assert "Save will ask for a new file name" in shown[0]


def test_opening_intact_file_does_not_warn(qapp, make_pdf):
    MainWindow().open_path(make_pdf())  # the conftest guard fails on any dialog


def test_save_on_damaged_file_asks_for_recovered_name(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.StandardButton.Ok)
    path = truncated_pdf(tmp_path / "broken.pdf")
    original = path.read_bytes()
    tab = MainWindow().open_path(path)
    tab.document.rotate_pages([0], 90)
    asked = []

    def fake_dialog(parent, caption, default, filt):
        asked.append(default)
        return default, filt

    monkeypatch.setattr(QFileDialog, "getSaveFileName", fake_dialog)
    assert tab.save() is True
    recovered = tmp_path / "broken (recovered).pdf"
    assert asked == [str(recovered)]
    assert path.read_bytes() == original
    with pymupdf.open(str(recovered)) as check:
        assert check[0].rotation == 90
    assert tab.path == recovered and not tab.document.is_dirty


# -- #15 file changed by another program -------------------------------------------


@pytest.fixture
def changed(qapp, make_pdf, monkeypatch):
    """Window with two tabs; the first one is edited, then its file is replaced
    on disk. Returns (window, tab, path, the other program's bytes)."""
    win = MainWindow()
    path = make_pdf("mine.pdf")
    tab = win.open_path(path)
    win.open_path(make_pdf("other.pdf"))
    tab.document.rotate_pages([0], 90)
    theirs = change_on_disk(path)
    return win, tab, path, theirs


def answer(monkeypatch, choice):
    asked = []
    monkeypatch.setattr(doc_tab, "ask_file_changed",
                        lambda parent, name: asked.append(name) or choice)
    return asked


def test_file_changed_save_as(changed, tmp_path, monkeypatch):
    win, tab, path, theirs = changed
    asked = answer(monkeypatch, "save_as")
    copy = tmp_path / "copy.pdf"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(copy), ""))
    assert tab.save() is True
    assert asked == ["mine.pdf"]
    assert path.read_bytes() == theirs
    with pymupdf.open(str(copy)) as check:
        assert check[0].rotation == 90
    assert not tab.document.is_dirty


def test_file_changed_overwrite(changed, monkeypatch):
    win, tab, path, theirs = changed
    answer(monkeypatch, "overwrite")
    assert tab.save() is True
    with pymupdf.open(str(path)) as check:
        assert check.page_count == 3 and check[0].rotation == 90
    assert not tab.document.is_dirty


def test_file_changed_discard_reopens_in_place(changed, qapp, monkeypatch):
    win, tab, path, theirs = changed
    answer(monkeypatch, "discard")
    assert tab.save() is False
    settle(qapp)
    fresh = win.tabs.widget(0)
    assert fresh is not tab and fresh.path == path
    assert win.tabs.count() == 2 and win.tabs.tabText(0).startswith("mine.pdf")
    assert fresh.document.page_count == 1 and "THEIRS" in fresh.document.raw[0].get_text()
    assert not fresh.document.is_dirty
    assert path.read_bytes() == theirs
    assert not tab.document.is_open  # the old tab was disposed


def test_file_changed_discard_keeps_tab_if_reopen_fails(changed, monkeypatch):
    win, tab, path, theirs = changed
    path.write_bytes(b"not a pdf any more")
    answer(monkeypatch, "discard")
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: shown.append(a[2]))
    assert tab.save() is False
    assert shown and "not a PDF" in shown[0]
    assert win.tabs.widget(0) is tab and tab.document.is_dirty


def test_file_changed_cancel(changed, monkeypatch):
    win, tab, path, theirs = changed
    answer(monkeypatch, "cancel")
    assert tab.save() is False
    assert tab.document.is_dirty and win.tabs.widget(0) is tab
    assert path.read_bytes() == theirs


def click_on_exec(monkeypatch, label):
    """Make QMessageBox.exec click the button labelled ``label`` (or nothing)."""
    def fake_exec(box):
        for button in box.buttons():
            if button.text() == label:
                button.click()
    monkeypatch.setattr(QMessageBox, "exec", fake_exec)


@pytest.mark.parametrize("label, choice", [
    ("Save As…", "save_as"), ("Overwrite", "overwrite"),
    ("Discard my changes", "discard"), ("Cancel", "cancel"), (None, "cancel"),
])
def test_ask_file_changed_maps_buttons(qapp, monkeypatch, label, choice):
    click_on_exec(monkeypatch, label)
    assert doc_tab.ask_file_changed(None, "mine.pdf") == choice
