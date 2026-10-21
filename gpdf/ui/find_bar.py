# SPDX-License-Identifier: AGPL-3.0-or-later
"""Find-in-document bar."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QPushButton, QWidget

from gpdf.core import textedit
from gpdf.core.document import PdfDocument
from gpdf.ui.pdf_view import PdfView


class FindBar(QWidget):
    def __init__(self, view: PdfView, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.view = view
        self._doc: PdfDocument | None = None
        self._hits: list = []
        self._current = -1

        row = QHBoxLayout(self)
        row.setContentsMargins(6, 2, 6, 2)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText("Find in document")
        self.edit.setClearButtonEnabled(True)
        self.prev_btn = QPushButton("▲")
        self.next_btn = QPushButton("▼")
        self.close_btn = QPushButton("✕")
        for b in (self.prev_btn, self.next_btn, self.close_btn):
            b.setFixedWidth(32)
        self.count = QLabel()
        row.addWidget(self.edit, 1)
        row.addWidget(self.prev_btn)
        row.addWidget(self.next_btn)
        row.addWidget(self.count)
        row.addWidget(self.close_btn)

        self.edit.textChanged.connect(self.run_search)
        self.edit.returnPressed.connect(self.next_hit)
        self.next_btn.clicked.connect(self.next_hit)
        self.prev_btn.clicked.connect(self.prev_hit)
        self.close_btn.clicked.connect(self.close_bar)
        self.hide()

    def set_document(self, doc: PdfDocument | None) -> None:
        if self._doc is not None and self._doc.is_open:
            for sig in (self._doc.structure_changed, self._doc.page_changed):
                try:
                    sig.disconnect(self._on_doc_changed)
                except (RuntimeError, TypeError):
                    pass
        self._doc = doc
        if doc is not None:
            doc.structure_changed.connect(self._on_doc_changed)
            doc.page_changed.connect(self._on_doc_changed)
        self._clear()

    def _on_doc_changed(self, *_) -> None:
        if self.isVisible() and self.edit.text().strip():
            self.run_search(self.edit.text(), keep_position=True)

    def open_bar(self) -> None:
        self.show()
        self.edit.setFocus()
        self.edit.selectAll()

    def close_bar(self) -> None:
        self.hide()
        self._clear()
        self.view.setFocus()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.close_bar()
        else:
            super().keyPressEvent(event)

    def _clear(self) -> None:
        self._hits = []
        self._current = -1
        self.count.clear()
        self.view.set_highlights([], -1)

    def run_search(self, text: str, keep_position: bool = False) -> None:
        if self._doc is None or not self._doc.is_open or not text.strip():
            self._clear()
            return
        previous = self._current
        self._hits = textedit.search(self._doc, text)
        if not self._hits:
            self._current = -1
            self.count.setText("No matches")
            self.view.set_highlights([], -1)
            return
        if keep_position and 0 <= previous < len(self._hits):
            self._current = previous
        else:
            # first hit at or after the page being viewed
            page = self.view.current_page
            self._current = next((i for i, h in enumerate(self._hits) if h.page >= page), 0)
        self._show()

    def _show(self) -> None:
        self.count.setText(f"{self._current + 1} of {len(self._hits)}")
        self.view.set_highlights(self._hits, self._current)
        self.view.show_hit(self._current)

    def next_hit(self) -> None:
        if self._hits:
            self._current = (self._current + 1) % len(self._hits)
            self._show()

    def prev_hit(self) -> None:
        if self._hits:
            self._current = (self._current - 1) % len(self._hits)
            self._show()
