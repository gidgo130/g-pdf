# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Check OCR panel: pages of an OCR report, lowest mean confidence first.

Clicking a row goes to that page; "Show OCR text" asks the view to draw the invisible
text layer visibly (render only, never saved); the pane below shows the current page's
extracted text, so misreads are easy to spot.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QHeaderView, QLabel, QPlainTextEdit, QSplitter,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

OCR, FAILED = "ocr", "failed"     # statuses of gpdf.pipeline.ocr (no numpy import here)


class ReportError(Exception):
    """The report cannot be read; the message is meant for the user."""


@dataclass
class Row:
    page: int                      # 1-based
    status: str
    words: int
    mean_conf: float | None
    low_conf_share: float | None
    error: str = ""


@dataclass
class OcrCheck:
    path: Path
    langs: str
    mean_conf: float | None
    counts: dict
    rows: list[Row]                # failed pages first, then OCR'd pages, lowest first


def sort_rows(rows) -> list[Row]:
    """Failed pages first, then OCR'd pages with words by mean confidence (lowest first,
    ties by page); pages that were not read (blank, has text, ...) are left out."""
    failed = sorted((r for r in rows if r.status == FAILED), key=lambda r: r.page)
    done = sorted((r for r in rows if r.status == OCR and r.mean_conf is not None),
                  key=lambda r: (r.mean_conf, r.page))
    return failed + done


def load_report(path) -> OcrCheck:
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        rows = [Row(int(p["page"]), p["status"], int(p.get("words") or 0), p.get("mean_conf"),
                    p.get("low_conf_share"), p.get("error") or "") for p in data["pages"]]
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise ReportError(f"cannot read the OCR report {path.name}: {e}") from None
    summary = data.get("summary") or {}
    return OcrCheck(path, data.get("langs", ""), summary.get("mean_conf"),
                    summary.get("counts") or {}, sort_rows(rows))


class _SortItem(QTableWidgetItem):
    """Sorts by the number in UserRole (failed pages, with None, sort first)."""

    def __lt__(self, other):
        a, b = self.data(Qt.ItemDataRole.UserRole), other.data(Qt.ItemDataRole.UserRole)
        return (a is not None, a or 0) < (b is not None, b or 0)


def _item(value, text) -> QTableWidgetItem:
    item = _SortItem(text)
    item.setData(Qt.ItemDataRole.UserRole, value)
    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    return item


class OcrCheckPanel(QWidget):
    page_requested = Signal(int)          # 0-based
    overlay_toggled = Signal(bool)

    COLUMNS = ("Page", "Conf.", "Words", "Low %")

    def __init__(self, check: OcrCheck, document, parent=None) -> None:
        super().__init__(parent)
        self.check, self.document = check, document
        c = check.counts
        mean = f"{check.mean_conf:.1f}" if check.mean_conf is not None else "-"
        others = ", ".join(f"{n} {k.replace('_', ' ')}" for k, n in c.items()
                           if n and k != OCR)
        self.summary = QLabel(f"{check.path.name}\n{c.get(OCR, 0)} pages read ({check.langs}), "
                              f"mean confidence {mean}" + (f"; {others}" if others else ""))
        self.summary.setWordWrap(True)

        self.table = QTableWidget(len(check.rows), len(self.COLUMNS), self)
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        for i, r in enumerate(check.rows):
            conf = "failed" if r.status == FAILED else f"{r.mean_conf:.0f}"
            low = "" if r.low_conf_share is None else f"{100 * r.low_conf_share:.0f}"
            for j, item in enumerate((_item(r.page, str(r.page)), _item(r.mean_conf, conf),
                                      _item(r.words, str(r.words)),
                                      _item(r.low_conf_share, low))):
                if r.error:
                    item.setToolTip(r.error)
                self.table.setItem(i, j, item)
        # keep the report's order (lowest first) until a header is clicked
        self.table.horizontalHeader().setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
        self.table.setSortingEnabled(True)
        self.table.cellClicked.connect(self._on_clicked)

        self.show_text = QCheckBox("Show OCR text")
        self.show_text.toggled.connect(self.overlay_toggled)
        self.text = QPlainTextEdit(self)
        self.text.setReadOnly(True)
        self.text.setPlaceholderText("This page has no text layer.")

        split = QSplitter(Qt.Orientation.Vertical, self)
        split.addWidget(self.table)
        split.addWidget(self.text)
        split.setSizes([300, 200])
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(self.summary)
        layout.addWidget(self.show_text)
        layout.addWidget(split, 1)

    def pages_in_order(self) -> list[int]:
        """1-based pages as the table lists them now."""
        return [self.table.item(i, 0).data(Qt.ItemDataRole.UserRole)
                for i in range(self.table.rowCount())]

    def _on_clicked(self, row: int, _col: int) -> None:
        self.page_requested.emit(self.table.item(row, 0).data(Qt.ItemDataRole.UserRole) - 1)

    def set_page(self, index: int) -> None:
        """Show the extracted text of page ``index`` (0-based) of the open document."""
        if self.document is None or not 0 <= index < self.document.page_count:
            self.text.clear()
            return
        self.text.setPlainText(self.document.raw[index].get_text("text").strip())
