# SPDX-License-Identifier: AGPL-3.0-or-later
"""Continuous-scroll PDF viewer.

Pages are laid out top to bottom. Only pages that are on screen get rendered,
and rendered pages are kept in a size-capped LRU cache so scrolling back is
instant. Rendering is synchronous for now (fast for ordinary pages); moving it
to a worker thread is a later optimisation.
"""

from __future__ import annotations

import bisect
from collections import OrderedDict

from PySide6.QtCore import QPoint, QRect, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor, QCursor, QImage, QKeyEvent, QMouseEvent, QPainter, QPalette, QPen, QWheelEvent,
)
from PySide6.QtWidgets import QAbstractScrollArea

from gpdf.core.document import PdfDocument

TOOL_BROWSE = "browse"
TOOL_EDIT_TEXT = "edit_text"
TOOL_ADD_TEXT = "add_text"
TOOL_ADD_IMAGE = "add_image"

# At 100% zoom a PDF point is drawn as 96/72 logical pixels, like most viewers.
POINTS_TO_PX = 96.0 / 72.0
MIN_ZOOM = 0.1
MAX_ZOOM = 4.0
ZOOM_STEP = 1.1  # factor per zoom-in / zoom-out step
PAGE_GAP = 12  # logical px between pages
MARGIN = 16  # logical px around the page stack
CACHE_BYTES = 384 * 1024 * 1024


class PdfView(QAbstractScrollArea):
    """Scrollable view of every page of one :class:`PdfDocument`."""

    current_page_changed = Signal(int)  # 0-based index of the most visible page
    zoom_changed = Signal(float)  # 1.0 == 100%
    # Editing tools: page index plus view-space points on that page.
    page_clicked = Signal(int, float, float)
    area_selected = Signal(int, float, float, float, float)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._doc: PdfDocument | None = None
        self._zoom = 1.0
        self._fit_width = True
        self._tops: list[int] = []  # page top edges, logical px
        self._sizes: list[tuple[int, int]] = []  # page (w, h), logical px
        self._content_w = 0
        self._content_h = 0
        self._current = 0
        self._cache: OrderedDict[tuple, QImage] = OrderedDict()
        self._cache_bytes = 0
        self._tool = TOOL_BROWSE
        self._drag_start: tuple[int, QPoint] | None = None
        self._drag_now: QPoint | None = None
        self._hits: list = []
        self._hit_current = -1

        # Always show the vertical bar so fit-to-width doesn't oscillate when
        # the bar appears and steals viewport width.
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.verticalScrollBar().setSingleStep(32)
        self.horizontalScrollBar().setSingleStep(32)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    # -- public API ---------------------------------------------------------

    @property
    def document(self) -> PdfDocument | None:
        return self._doc

    @property
    def zoom(self) -> float:
        return self._zoom

    @property
    def page_count(self) -> int:
        return self._doc.page_count if self._doc else 0

    @property
    def current_page(self) -> int:
        return self._current

    def set_document(self, doc: PdfDocument | None) -> None:
        if self._doc is not None and self._doc.is_open:
            for sig, slot in (
                (self._doc.structure_changed, self._on_structure_changed),
                (self._doc.page_changed, self._on_page_changed),
            ):
                try:
                    sig.disconnect(slot)
                except (RuntimeError, TypeError):
                    pass
        self._doc = doc
        self._hits = []
        self._hit_current = -1
        if doc is not None:
            doc.structure_changed.connect(self._on_structure_changed)
            doc.page_changed.connect(self._on_page_changed)
        self._clear_cache()
        self._current = 0
        self._fit_width = True
        if doc is not None:
            self._zoom = self._zoom_to_fit_width()
        self._relayout()
        self.verticalScrollBar().setValue(0)
        self.horizontalScrollBar().setValue(0)
        self.viewport().update()
        self.zoom_changed.emit(self._zoom)
        self.current_page_changed.emit(0)

    def set_zoom(self, zoom: float, *, fit_width: bool = False) -> None:
        """Set the zoom, keeping the page at the top of the view in place."""
        zoom = max(MIN_ZOOM, min(MAX_ZOOM, zoom))
        if self._doc is None:
            self._zoom = zoom
            self._fit_width = fit_width
            return

        anchor_page, anchor_frac = self._top_anchor()
        h_center_frac = self._h_center_fraction()
        self._zoom = zoom
        self._fit_width = fit_width
        self._relayout()
        self._restore_anchor(anchor_page, anchor_frac, h_center_frac)
        self.viewport().update()
        self.zoom_changed.emit(self._zoom)

    def zoom_in(self) -> None:
        self.set_zoom(self._zoom * ZOOM_STEP)

    def zoom_out(self) -> None:
        self.set_zoom(self._zoom / ZOOM_STEP)

    def reset_zoom(self) -> None:
        self.set_zoom(1.0)

    def fit_width(self) -> None:
        self.set_zoom(self._zoom_to_fit_width(), fit_width=True)

    def go_to_page(self, index: int) -> None:
        """Scroll so page ``index`` (0-based) is at the top of the view."""
        if not self._tops:
            return
        index = max(0, min(index, len(self._tops) - 1))
        self.verticalScrollBar().setValue(self._tops[index] - MARGIN)

    def _on_structure_changed(self) -> None:
        """Pages were added/removed/reordered/rotated: re-lay out, keep place."""
        if self._doc is None or not self._doc.is_open:
            return
        anchor_page, anchor_frac = self._top_anchor()
        h_frac = self._h_center_fraction()
        if self._fit_width:
            self._zoom = self._zoom_to_fit_width()
        self._relayout()
        self._restore_anchor(anchor_page, anchor_frac, h_frac)
        self.viewport().update()
        self.zoom_changed.emit(self._zoom)
        self._current = -1  # force re-emit so page counters refresh
        self._update_current_page()

    def _on_page_changed(self, index: int) -> None:
        self.viewport().update()  # stale images miss the cache via page_token

    # -- tools, highlights --------------------------------------------------

    @property
    def tool(self) -> str:
        return self._tool

    def set_tool(self, tool: str) -> None:
        self._tool = tool
        self._drag_start = self._drag_now = None
        cursor = {
            TOOL_BROWSE: Qt.CursorShape.ArrowCursor,
            TOOL_EDIT_TEXT: Qt.CursorShape.IBeamCursor,
            TOOL_ADD_TEXT: Qt.CursorShape.IBeamCursor,
            TOOL_ADD_IMAGE: Qt.CursorShape.CrossCursor,
        }[tool]
        self.viewport().setCursor(QCursor(cursor))
        self.viewport().update()

    def set_highlights(self, hits, current: int = -1) -> None:
        """Show search hits (objects with ``.page`` and ``.rect``)."""
        self._hits = list(hits)
        self._hit_current = current if 0 <= current < len(self._hits) else -1
        self.viewport().update()

    def show_hit(self, i: int) -> None:
        """Make hit ``i`` the current one and scroll it into view."""
        if not 0 <= i < len(self._hits):
            return
        self._hit_current = i
        hit = self._hits[i]
        scale = self._zoom * POINTS_TO_PX
        y = self._tops[hit.page] + hit.rect[1] * scale
        vbar = self.verticalScrollBar()
        vp_h = self.viewport().height()
        if not (vbar.value() + 40 < y < vbar.value() + vp_h - 80):
            vbar.setValue(round(y - vp_h / 3))
        self.viewport().update()

    def _page_rect(self, i: int) -> QRect:
        vx = self.horizontalScrollBar().value()
        vy = self.verticalScrollBar().value()
        stack_w = max(self._content_w, self.viewport().width())
        w, h = self._sizes[i]
        return QRect((stack_w - w) // 2 - vx, self._tops[i] - vy, w, h)

    def page_at(self, pos: QPoint) -> tuple[int, float, float] | None:
        """Page under a viewport position, with view-space point coordinates."""
        if not self._tops:
            return None
        vy = self.verticalScrollBar().value()
        i = max(0, bisect.bisect_right(self._tops, vy + pos.y()) - 1)
        rect = self._page_rect(i)
        if not rect.contains(pos):
            return None
        scale = self._zoom * POINTS_TO_PX
        return i, (pos.x() - rect.x()) / scale, (pos.y() - rect.y()) / scale

    # -- layout -------------------------------------------------------------

    def _zoom_to_fit_width(self) -> float:
        if self._doc is None:
            return self._zoom
        widest_pt = max(w for w, _ in self._doc.page_sizes)
        available = max(1, self.viewport().width() - 2 * MARGIN)
        zoom = available / (widest_pt * POINTS_TO_PX)
        return max(MIN_ZOOM, min(MAX_ZOOM, zoom))

    def _relayout(self) -> None:
        """Recompute page positions and scrollbar ranges for the current zoom."""
        self._tops.clear()
        self._sizes.clear()
        if self._doc is None:
            self._content_w = self._content_h = 0
        else:
            scale = self._zoom * POINTS_TO_PX
            y = MARGIN
            widest = 0
            for w_pt, h_pt in self._doc.page_sizes:
                w, h = max(1, round(w_pt * scale)), max(1, round(h_pt * scale))
                self._tops.append(y)
                self._sizes.append((w, h))
                widest = max(widest, w)
                y += h + PAGE_GAP
            self._content_w = widest + 2 * MARGIN
            self._content_h = y - PAGE_GAP + MARGIN

        vp = self.viewport().size()
        vbar, hbar = self.verticalScrollBar(), self.horizontalScrollBar()
        vbar.setRange(0, max(0, self._content_h - vp.height()))
        vbar.setPageStep(max(1, vp.height()))
        hbar.setRange(0, max(0, self._content_w - vp.width()))
        hbar.setPageStep(max(1, vp.width()))

    def _top_anchor(self) -> tuple[int, float]:
        """(page index, fraction of that page) currently at the top edge."""
        if not self._tops:
            return 0, 0.0
        vy = self.verticalScrollBar().value()
        i = max(0, bisect.bisect_right(self._tops, vy) - 1)
        frac = (vy - self._tops[i]) / self._sizes[i][1]
        return i, frac

    def _h_center_fraction(self) -> float:
        if self._content_w <= 0:
            return 0.5
        hx = self.horizontalScrollBar().value()
        return (hx + self.viewport().width() / 2) / self._content_w

    def _restore_anchor(self, page: int, frac: float, h_center_frac: float) -> None:
        if self._tops:
            page = min(page, len(self._tops) - 1)
            self.verticalScrollBar().setValue(
                round(self._tops[page] + frac * self._sizes[page][1])
            )
        hbar = self.horizontalScrollBar()
        hbar.setValue(
            round(h_center_frac * self._content_w - self.viewport().width() / 2)
        )

    def _compute_current_page(self) -> int:
        """The page with the most area visible (first wins ties)."""
        if not self._tops:
            return 0
        vy = self.verticalScrollBar().value()
        vh = self.viewport().height()
        best, best_overlap = 0, -1
        i = max(0, bisect.bisect_right(self._tops, vy) - 1)
        while i < len(self._tops) and self._tops[i] < vy + vh:
            top, height = self._tops[i], self._sizes[i][1]
            overlap = min(top + height, vy + vh) - max(top, vy)
            if overlap > best_overlap:
                best, best_overlap = i, overlap
            i += 1
        return best

    def _update_current_page(self) -> None:
        page = self._compute_current_page()
        if page != self._current:
            self._current = page
            self.current_page_changed.emit(page)

    # -- cache --------------------------------------------------------------

    def _clear_cache(self) -> None:
        self._cache.clear()
        self._cache_bytes = 0

    def _page_image(self, index: int, w: int, h: int, dpr: float) -> QImage:
        key = (self._doc.page_token(index), w, h, round(dpr, 3))
        assert self._doc is not None
        image = self._cache.get(key)
        if image is not None:
            self._cache.move_to_end(key)
            return image
        assert self._doc is not None
        image = self._doc.render(index, round(w * dpr), round(h * dpr))
        image.setDevicePixelRatio(dpr)
        self._cache[key] = image
        self._cache_bytes += image.sizeInBytes()
        return image

    def _evict_cache(self) -> None:
        while self._cache_bytes > CACHE_BYTES and len(self._cache) > 1:
            _, old = self._cache.popitem(last=False)
            self._cache_bytes -= old.sizeInBytes()

    # -- Qt events ----------------------------------------------------------

    def paintEvent(self, event) -> None:
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.fillRect(event.rect(), self.palette().color(QPalette.ColorRole.Dark))
        if self._doc is None or not self._tops:
            return

        vy = self.verticalScrollBar().value()
        vx = self.horizontalScrollBar().value()
        vp_w, vp_h = self.viewport().width(), self.viewport().height()
        stack_w = max(self._content_w, vp_w)
        dpr = self.devicePixelRatioF()
        border = QColor(0, 0, 0, 70)

        i = max(0, bisect.bisect_right(self._tops, vy) - 1)
        while i < len(self._tops) and self._tops[i] - vy < vp_h:
            w, h = self._sizes[i]
            rect = self._page_rect(i)
            if rect.intersects(event.rect()):
                painter.fillRect(rect, Qt.GlobalColor.white)
                painter.drawImage(rect, self._page_image(i, w, h, dpr))
                painter.setPen(border)
                painter.drawRect(rect.adjusted(0, 0, -1, -1))
            i += 1
        self._paint_overlays(painter, vy, vp_h)
        painter.end()
        self._evict_cache()

    def _paint_overlays(self, painter: QPainter, vy: int, vp_h: int) -> None:
        scale = self._zoom * POINTS_TO_PX
        for n, hit in enumerate(self._hits):
            if hit.page >= len(self._tops):
                continue
            top = self._tops[hit.page]
            if top + self._sizes[hit.page][1] < vy or top > vy + vp_h:
                continue
            page = self._page_rect(hit.page)
            x0, y0, x1, y1 = hit.rect
            r = QRectF(page.x() + x0 * scale, page.y() + y0 * scale,
                       (x1 - x0) * scale, (y1 - y0) * scale)
            color = QColor(255, 140, 0, 110) if n == self._hit_current else QColor(255, 235, 0, 110)
            painter.fillRect(r, color)
        if self._drag_start is not None and self._drag_now is not None:
            painter.setPen(QPen(QColor(30, 110, 230), 1, Qt.PenStyle.DashLine))
            painter.setBrush(QColor(30, 110, 230, 40))
            painter.drawRect(QRect(self._drag_start[1], self._drag_now).normalized())

    # -- mouse (editing tools) ------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._tool != TOOL_BROWSE:
            hit = self.page_at(event.position().toPoint())
            if hit is not None:
                if self._tool == TOOL_ADD_IMAGE:
                    self._drag_start = (hit[0], event.position().toPoint())
                    self._drag_now = self._drag_start[1]
                else:
                    self.page_clicked.emit(*hit)
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_start is not None:
            self._drag_now = event.position().toPoint()
            self.viewport().update()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._drag_start is not None and event.button() == Qt.MouseButton.LeftButton:
            page, start = self._drag_start
            end = event.position().toPoint()
            self._drag_start = self._drag_now = None
            self.viewport().update()
            rect = self._page_rect(page)
            scale = self._zoom * POINTS_TO_PX
            box = QRect(start, end).normalized().intersected(rect)
            if box.width() > 4 and box.height() > 4:
                self.area_selected.emit(
                    page,
                    (box.left() - rect.x()) / scale, (box.top() - rect.y()) / scale,
                    (box.right() - rect.x()) / scale, (box.bottom() - rect.y()) / scale,
                )
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._doc is None:
            return
        if self._fit_width:
            new_zoom = self._zoom_to_fit_width()
            if abs(new_zoom - self._zoom) > 1e-6:
                self.set_zoom(new_zoom, fit_width=True)
                return
        self._relayout()
        self._update_current_page()

    def scrollContentsBy(self, dx: int, dy: int) -> None:
        self.viewport().update()
        self._update_current_page()

    def wheelEvent(self, event: QWheelEvent) -> None:
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            steps = event.angleDelta().y() / 120.0
            if steps:
                self.set_zoom(self._zoom * ZOOM_STEP**steps)
            event.accept()
            return
        super().wheelEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        vbar = self.verticalScrollBar()
        page = round(self.viewport().height() * 0.9)
        key = event.key()
        if key == Qt.Key.Key_PageDown:
            vbar.setValue(vbar.value() + page)
        elif key == Qt.Key.Key_PageUp:
            vbar.setValue(vbar.value() - page)
        elif key == Qt.Key.Key_Home:
            vbar.setValue(vbar.minimum())
        elif key == Qt.Key.Key_End:
            vbar.setValue(vbar.maximum())
        elif key == Qt.Key.Key_Down:
            vbar.setValue(vbar.value() + vbar.singleStep())
        elif key == Qt.Key.Key_Up:
            vbar.setValue(vbar.value() - vbar.singleStep())
        else:
            super().keyPressEvent(event)
