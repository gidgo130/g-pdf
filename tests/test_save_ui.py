# SPDX-License-Identifier: AGPL-3.0-or-later
"""Saving from the UI: damaged files (#2)."""

import pymupdf
import pytest
from PySide6.QtWidgets import QFileDialog, QMessageBox

from gpdf.ui.main_window import MainWindow
from tests.test_data_safety import truncated_pdf


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
