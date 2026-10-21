# SPDX-License-Identifier: AGPL-3.0-or-later
import os

# Must be set before any Qt widget is created so tests run without a display.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf
import pytest
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def make_pdf(tmp_path):
    """Factory: make_pdf("name.pdf", sizes=[(w, h), ...]) -> Path."""

    def _make(name="sample.pdf", sizes=((595, 842), (842, 595), (300, 400))):
        path = tmp_path / name
        doc = pymupdf.open()
        for i, (w, h) in enumerate(sizes):
            page = doc.new_page(width=w, height=h)
            page.insert_text((72, 72), f"{name} page {i + 1}", fontsize=24)
        doc.save(str(path))
        doc.close()
        return path

    return _make


@pytest.fixture
def sample_pdf(make_pdf):
    return make_pdf()
