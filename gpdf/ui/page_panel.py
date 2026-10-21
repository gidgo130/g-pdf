# SPDX-License-Identifier: AGPL-3.0-or-later
"""Thumbnail strip: select, drag to reorder, drag between documents.

Drag and drop uses a private registry (like the tab dragging does) instead of
serialising pages into the mime data: the mime payload is just a token that
points at (source document, page indices). That keeps cross-document copies
simple, but means page drags only work within one g-pdf process.
"""

from __future__ import annotations

import itertools
from collections import OrderedDict

from PySide6.QtCore import (
    QAbstractListModel, QByteArray, QMimeData, QModelIndex, QPoint, QRect, QSize, Qt, Signal,
)
from PySide6.QtGui import QColor, QDrag, QPainter, QPalette, QPen, QPixmap
from PySide6.QtWidgets import QAbstractItemView, QListView, QStyle, QStyledItemDelegate

from gpdf.core.document import PdfDocument, PdfEditError
from gpdf.ui.detachable_tabs import dropped_pdf_paths

PAGES_MIME = "application/x-gpdf-pages"
THUMB_SIDE = 140
THUMB_CACHE_ITEMS = 400
PANEL_BACKGROUND = QColor(222, 222, 226)
PAGE_BORDER = QColor(140, 140, 146)

_registry: dict[int, tuple[PdfDocument, list[int]]] = {}
_tokens = itertools.count(1)


def _make_mime(doc: PdfDocument, indices: list[int]) -> tuple[QMimeData, int]:
    token = next(_tokens)
    _registry[token] = (doc, indices)
    mime = QMimeData()
    mime.setData(PAGES_MIME, QByteArray(str(token).encode()))
    return mime, token


def _read_mime(mime: QMimeData) -> tuple[PdfDocument, list[int]] | None:
    if not mime.hasFormat(PAGES_MIME):
        return None
    try:
        token = int(bytes(mime.data(PAGES_MIME)).decode())
    except ValueError:
        return None
    entry = _registry.get(token)
    if entry is None or not entry[0].is_open:
        return None
    return entry


class PageModel(QAbstractListModel):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._doc: PdfDocument | None = None
        self._thumbs: OrderedDict[tuple, QPixmap] = OrderedDict()

    def set_document(self, doc: PdfDocument | None) -> None:
        self.beginResetModel()
        self._doc = doc
        self._thumbs.clear()
        self.endResetModel()

    def refresh(self) -> None:
        self.beginResetModel()
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:
        return self._doc.page_count if (self._doc and not parent.isValid()) else 0

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or self._doc is None or not self._doc.is_open:
            return None
        row = index.row()
        if role == Qt.ItemDataRole.DisplayRole:
            return str(row + 1)
        if role == Qt.ItemDataRole.DecorationRole:
            return self._thumbnail(row)
        if role == Qt.ItemDataRole.ToolTipRole:
            return f"Page {row + 1}"
        return None

    def _thumbnail(self, row: int) -> QPixmap:
        assert self._doc is not None
        key = (self._doc.page_token(row), self._doc.page_rotation(row))
        pm = self._thumbs.get(key)
        if pm is None:
            pm = QPixmap.fromImage(self._doc.render_thumbnail(row, THUMB_SIDE))
            self._thumbs[key] = pm
            while len(self._thumbs) > THUMB_CACHE_ITEMS:
                self._thumbs.popitem(last=False)
        else:
            self._thumbs.move_to_end(key)
        return pm


class ThumbnailDelegate(QStyledItemDelegate):
    """Draws a bordered thumbnail with its number; selected items get a Highlight tint and border."""

    def sizeHint(self, option, index) -> QSize:
        return QSize(THUMB_SIDE + 24, THUMB_SIDE + 40)

    def paint(self, painter: QPainter, option, index) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        cell = option.rect.adjusted(3, 3, -3, -3)
        highlight = option.palette.color(QPalette.ColorRole.Highlight)
        if option.state & QStyle.StateFlag.State_Selected:
            fill = QColor(highlight)
            fill.setAlpha(110)
            painter.setPen(QPen(highlight, 2))
            painter.setBrush(fill)
            painter.drawRoundedRect(cell.adjusted(1, 1, -1, -1), 4, 4)
        pm = index.data(Qt.ItemDataRole.DecorationRole)
        if isinstance(pm, QPixmap) and not pm.isNull():
            size = pm.deviceIndependentSize().toSize()
            target = QRect(
                cell.left() + (cell.width() - size.width()) // 2, cell.top() + 8,
                size.width(), size.height())
            painter.drawPixmap(target, pm)
            painter.setPen(QPen(PAGE_BORDER, 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(target.adjusted(-1, -1, 0, 0))
        painter.setPen(option.palette.color(QPalette.ColorRole.Text))
        label = QRect(cell.left(), cell.bottom() - 22, cell.width(), 20)
        painter.drawText(label, Qt.AlignmentFlag.AlignCenter, str(index.data(Qt.ItemDataRole.DisplayRole)))
        painter.restore()


class PagePanel(QListView):
    """Thumbnails of one document's pages."""

    page_activated = Signal(int)
    error = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._doc: PdfDocument | None = None
        self._model = PageModel(self)
        self.setModel(self._model)
        self.setItemDelegate(ThumbnailDelegate(self))
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Base, PANEL_BACKGROUND)
        self.setPalette(palette)
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setFlow(QListView.Flow.LeftToRight)
        self.setWrapping(True)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Static)
        self.setUniformItemSizes(True)
        self.setIconSize(QSize(THUMB_SIDE, THUMB_SIDE))
        self.setGridSize(QSize(THUMB_SIDE + 24, THUMB_SIDE + 40))
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragEnabled(False)  # we run our own QDrag
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)  # QListView gets drops on its viewport
        self.setDropIndicatorShown(False)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setMinimumWidth(THUMB_SIDE + 60)
        self._press_pos: QPoint | None = None
        self._drop_row: int | None = None
        self._pending_select: list[int] | None = None
        self.clicked.connect(lambda i: self.page_activated.emit(i.row()))

    # -- document ---------------------------------------------------------------

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
        self._model.set_document(doc)
        if doc is not None:
            doc.structure_changed.connect(self._on_structure_changed)
            doc.page_changed.connect(self._on_page_changed)

    def _on_structure_changed(self) -> None:
        before = self.selected_pages()
        count_before = self._model.rowCount()
        keep = self._pending_select
        self._pending_select = None
        self._model.refresh()
        if keep:
            self.select_pages(keep)
        elif before and self._model.rowCount() == count_before:
            self.select_pages(before)  # e.g. rotate: same pages, keep selected

    def _on_page_changed(self, index: int) -> None:
        i = self._model.index(index)
        self._model.dataChanged.emit(i, i)

    # -- selection API ------------------------------------------------------------

    def selected_pages(self) -> list[int]:
        return sorted(i.row() for i in self.selectionModel().selectedIndexes())

    def select_pages(self, pages: list[int]) -> None:
        sm = self.selectionModel()
        sm.clearSelection()
        n = self._model.rowCount()
        for p in pages:
            if 0 <= p < n:
                sm.select(self._model.index(p), sm.SelectionFlag.Select)
        if pages and 0 <= pages[0] < n:
            self.scrollTo(self._model.index(pages[0]))

    def keep_selection_after_next_change(self, pages: list[int]) -> None:
        """Select ``pages`` once the next structure change has been applied."""
        self._pending_select = list(pages)

    def mark_current(self, page: int) -> None:
        """Highlight the page the main view is on, without touching selection."""
        if 0 <= page < self._model.rowCount():
            sm = self.selectionModel()
            sm.setCurrentIndex(self._model.index(page), sm.SelectionFlag.NoUpdate)

    # -- painting the drop marker ---------------------------------------------------

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if self._drop_row is None:
            return
        n = self._model.rowCount()
        if n == 0:
            return
        at_end = self._drop_row >= n
        row = n - 1 if at_end else self._drop_row
        r = self.visualRect(self._model.index(row))
        painter = QPainter(self.viewport())
        painter.setPen(QPen(QColor(30, 110, 230), 3))
        if self._row_is_horizontal(row):
            x = r.right() if at_end else r.left()
            painter.drawLine(x, r.top() + 4, x, r.bottom() - 4)
        else:
            y = r.bottom() if at_end else r.top()
            painter.drawLine(r.left() + 4, y, r.right() - 4, y)
        painter.end()

    # -- starting a drag ----------------------------------------------------------------

    def mousePressEvent(self, event) -> None:
        self._press_pos = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if (
            self._press_pos is not None
            and event.buttons() & Qt.MouseButton.LeftButton
            and (event.position().toPoint() - self._press_pos).manhattanLength() > 12
            and self._doc is not None
        ):
            pressed = self.indexAt(self._press_pos)
            if pressed.isValid() and pressed.row() in set(self.selected_pages()):
                self._press_pos = None
                self._start_drag()
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._press_pos = None
        super().mouseReleaseEvent(event)

    def _start_drag(self) -> None:
        assert self._doc is not None
        pages = self.selected_pages()
        mime, token = _make_mime(self._doc, pages)
        drag = QDrag(self)
        drag.setMimeData(mime)
        first = self._model.data(self._model.index(pages[0]), Qt.ItemDataRole.DecorationRole)
        if first is not None:
            drag.setPixmap(first.scaled(
                70, 70, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        try:
            drag.exec(Qt.DropAction.CopyAction | Qt.DropAction.MoveAction, Qt.DropAction.MoveAction)
        finally:
            _registry.pop(token, None)
            self._drop_row = None
            self.viewport().update()

    # -- dropping -----------------------------------------------------------------------

    def _row_for_pos(self, pos: QPoint) -> int:
        """Insertion row (0..page_count) nearest the cursor."""
        n = self._model.rowCount()
        idx = self.indexAt(pos)
        if idx.isValid():
            return idx.row() + self._after(idx.row(), self.visualRect(idx).center(), pos)
        # Between/after items: nearest by visual centre. O(n); fine for normal docs.
        best, best_d = n, None
        for row in range(n):
            c = self.visualRect(self._model.index(row)).center()
            d = (c.x() - pos.x()) ** 2 + (c.y() - pos.y()) ** 2 * 4
            if best_d is None or d < best_d:
                best_d = d
                best = row + self._after(row, c, pos)
        return best

    def _row_is_horizontal(self, row: int) -> bool:
        """True if ``row`` shares a line with a neighbour (grid), False in a single column."""
        n = self._model.rowCount()
        top = self.visualRect(self._model.index(row)).top()
        for other in (row + 1, row - 1):
            if 0 <= other < n and self.visualRect(self._model.index(other)).top() == top:
                return True
        if n == 1:  # no neighbour to compare: a wide panel would lay a second page beside it
            return self.viewport().width() >= 2 * self.gridSize().width()
        return False

    def _after(self, row: int, centre: QPoint, pos: QPoint) -> int:
        """1 if ``pos`` is past ``centre`` along the layout direction of ``row``, else 0."""
        past = pos.x() > centre.x() if self._row_is_horizontal(row) else pos.y() > centre.y()
        return 1 if past else 0

    def _accepts(self, mime: QMimeData) -> bool:
        return _read_mime(mime) is not None or bool(dropped_pdf_paths(mime))

    def dragEnterEvent(self, event) -> None:
        if self._accepts(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        if not self._accepts(event.mimeData()):
            event.ignore()
            return
        self._drop_row = self._row_for_pos(event.position().toPoint())
        self.viewport().update()
        event.acceptProposedAction()

    def dragLeaveEvent(self, event) -> None:
        self._drop_row = None
        self.viewport().update()

    def dropEvent(self, event) -> None:
        row = self._row_for_pos(event.position().toPoint())
        self._drop_row = None
        self.viewport().update()
        doc = self._doc
        if doc is None:
            event.ignore()
            return
        try:
            entry = _read_mime(event.mimeData())
            if entry is not None:
                src, pages = entry
                if src is doc:
                    new = doc.move_pages(pages, row)
                    if new:
                        self.select_pages(new)
                else:
                    new = doc.insert_pages_from(src, pages, row)
                    self.select_pages(new)
                event.acceptProposedAction()
                return
            paths = dropped_pdf_paths(event.mimeData())
            if paths:
                at = row
                new = []
                for path in paths:
                    got = doc.insert_file(path, at)
                    at += len(got)
                    new += got
                self.select_pages(new)
                event.acceptProposedAction()
                return
        except PdfEditError as exc:
            self.error.emit(str(exc))
        event.ignore()
