# SPDX-License-Identifier: AGPL-3.0-or-later
"""Outline (bookmarks) tree. Read-only for now."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem

from gpdf.core.document import PdfDocument


class BookmarksTree(QTreeWidget):
    page_requested = Signal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setHeaderHidden(True)
        self._doc: PdfDocument | None = None
        self.itemClicked.connect(self._clicked)

    def set_document(self, doc: PdfDocument | None) -> None:
        if self._doc is not None and self._doc.is_open:
            try:
                self._doc.structure_changed.disconnect(self.rebuild)
            except (RuntimeError, TypeError):
                pass
        self._doc = doc
        if doc is not None:
            doc.structure_changed.connect(self.rebuild)
        self.rebuild()

    def has_entries(self) -> bool:
        return self.topLevelItemCount() > 0

    def rebuild(self) -> None:
        self.clear()
        if self._doc is None or not self._doc.is_open:
            return
        stack: list[tuple[int, QTreeWidgetItem | None]] = [(0, None)]
        for level, title, page, *_ in self._doc.raw.get_toc(simple=True):
            while stack and stack[-1][0] >= level:
                stack.pop()
            parent = stack[-1][1] if stack else None
            item = QTreeWidgetItem([title])
            item.setData(0, Qt.ItemDataRole.UserRole, max(0, page - 1))
            (parent.addChild(item) if parent else self.addTopLevelItem(item))
            stack.append((level, item))
        self.expandToDepth(0)

    def _clicked(self, item: QTreeWidgetItem) -> None:
        page = item.data(0, Qt.ItemDataRole.UserRole)
        if isinstance(page, int) and page >= 0:
            self.page_requested.emit(min(page, self._doc.page_count - 1) if self._doc else 0)
