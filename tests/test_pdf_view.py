# SPDX-License-Identifier: AGPL-3.0-or-later
import pytest
from PySide6.QtGui import QColor

from gpdf.core.document import PdfDocument
from gpdf.ui.pdf_view import MARGIN, MAX_ZOOM, MIN_ZOOM, POINTS_TO_PX, PdfView


@pytest.fixture
def view(qapp, sample_pdf):
    doc = PdfDocument(sample_pdf)
    v = PdfView()
    v.resize(800, 600)
    v.show()
    qapp.processEvents()
    v.set_document(doc)
    qapp.processEvents()
    yield v
    v.set_document(None)
    doc.close()
    v.close()


def test_opens_fit_to_width(view):
    widest_pt = 842  # page 2 is landscape
    expected = (view.viewport().width() - 2 * MARGIN) / (widest_pt * POINTS_TO_PX)
    assert view.zoom == pytest.approx(expected, rel=1e-3)
    assert view.page_count == 3
    assert view.current_page == 0


def test_go_to_page_updates_current_page(view, qapp):
    seen = []
    view.current_page_changed.connect(seen.append)
    view.go_to_page(2)
    qapp.processEvents()
    assert view.current_page == 2
    assert seen and seen[-1] == 2
    view.go_to_page(0)
    assert view.current_page == 0


def test_go_to_page_clamps(view):
    view.go_to_page(99)
    assert view.current_page == 2
    view.go_to_page(-5)
    assert view.current_page == 0


def test_zoom_scales_content_and_clamps(view):
    view.set_zoom(1.0)
    h1 = view._content_h
    view.set_zoom(2.0)
    assert view._content_h == pytest.approx(2 * h1, rel=0.1)
    view.set_zoom(100)
    assert view.zoom == MAX_ZOOM
    view.set_zoom(0.0001)
    assert view.zoom == MIN_ZOOM


def test_zoom_keeps_the_top_page_in_place(view):
    view.set_zoom(1.0)
    view.go_to_page(1)
    view.verticalScrollBar().setValue(view.verticalScrollBar().value() + 50)
    page_before, frac_before = view._top_anchor()
    view.set_zoom(1.8)
    page_after, frac_after = view._top_anchor()
    assert page_after == page_before
    assert frac_after == pytest.approx(frac_before, abs=0.02)


def test_fit_width_follows_resize_but_manual_zoom_does_not(view, qapp):
    view.fit_width()
    view.resize(500, 600)
    qapp.processEvents()
    expected = (view.viewport().width() - 2 * MARGIN) / (842 * POINTS_TO_PX)
    assert view.zoom == pytest.approx(expected, rel=1e-3)

    view.set_zoom(1.25)
    view.resize(900, 600)
    qapp.processEvents()
    assert view.zoom == pytest.approx(1.25)


def test_paints_a_white_page_and_fills_cache(view):
    view.set_zoom(1.0)
    view.go_to_page(0)
    image = view.viewport().grab().toImage()
    assert not image.isNull()
    # Somewhere in the viewport there must be a white page pixel.
    white = QColor("white").rgb()
    found = any(
        image.pixel(x, y) == white
        for x in range(0, image.width(), 9)
        for y in range(0, image.height(), 9)
    )
    assert found
    assert view._cache_bytes > 0


def test_cache_is_capped(view, monkeypatch):
    import gpdf.ui.pdf_view as mod

    monkeypatch.setattr(mod, "CACHE_BYTES", 1)
    view.set_zoom(0.5)
    view.viewport().grab()  # paints, then evicts
    assert len(view._cache) <= 1


def test_empty_view_is_safe(qapp):
    v = PdfView()
    v.resize(400, 300)
    v.show()
    qapp.processEvents()
    v.zoom_in()
    v.go_to_page(3)
    v.viewport().grab()
    assert v.page_count == 0
    v.close()
