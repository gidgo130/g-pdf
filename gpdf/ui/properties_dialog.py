# SPDX-License-Identifier: AGPL-3.0-or-later
"""Plain-language document properties (PDF "metadata")."""

from __future__ import annotations

import re
from datetime import datetime

from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout,
)

from gpdf.core.document import PdfDocument

INTRO = (
    "Every PDF carries a few hidden labels about itself. They don't show on the "
    "pages, but they travel with the file, and anyone you send it to can read them."
)
PRIVACY_NOTE = (
    "Clears author, keywords, and the names of the programs that made the file, "
    "plus any extra hidden details stored with them. It does not change what is "
    "written on the pages, the names on comments or highlights, or attached files."
)

_DATE = re.compile(r"D:(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?")


def friendly_date(pdf_date: str) -> str:
    m = _DATE.match(pdf_date or "")
    if not m:
        return pdf_date or "Unknown"
    parts = [int(g) if g else d for g, d in zip(m.groups(), (0, 1, 1, 0, 0, 0))]
    try:
        return datetime(*parts).strftime("%d %b %Y, %H:%M")
    except ValueError:
        return pdf_date


class PropertiesDialog(QDialog):
    def __init__(self, doc: PdfDocument, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Document properties")
        self.setMinimumWidth(480)
        self._clear_hidden = False
        meta = doc.metadata

        layout = QVBoxLayout(self)
        intro = QLabel(INTRO)
        intro.setWordWrap(True)
        layout.addWidget(intro)

        form = QFormLayout()
        self.title = QLineEdit(meta["title"])
        self.title.setPlaceholderText("Shown in the window title of some viewers")
        self.author = QLineEdit(meta["author"])
        self.author.setPlaceholderText("Who wrote it (this can reveal your name)")
        self.subject = QLineEdit(meta["subject"])
        self.subject.setPlaceholderText("A short description")
        self.keywords = QLineEdit(meta["keywords"])
        self.keywords.setPlaceholderText("Words that help find this file")
        form.addRow("Title", self.title)
        form.addRow("Author", self.author)
        form.addRow("Subject", self.subject)
        form.addRow("Keywords", self.keywords)
        layout.addLayout(form)

        info = QFormLayout()
        info.addRow("Made with", QLabel(meta["creator"] or "Unknown"))
        info.addRow("Converted by", QLabel(meta["producer"] or "Unknown"))
        info.addRow("Created", QLabel(friendly_date(meta["creationDate"])))
        info.addRow("Last changed", QLabel(friendly_date(meta["modDate"])))
        info.addRow("Pages", QLabel(str(doc.page_count)))
        info.addRow("File", QLabel(str(doc.path)))
        layout.addLayout(info)

        self.remove_btn = QPushButton("Remove personal info…")
        self.remove_btn.setToolTip(PRIVACY_NOTE)
        self.remove_btn.clicked.connect(self._remove_personal)
        layout.addWidget(self.remove_btn)
        note = QLabel(PRIVACY_NOTE)
        note.setWordWrap(True)
        layout.addWidget(note)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._meta = meta
        self._creator_cleared = False

    def _remove_personal(self) -> None:
        self.author.clear()
        self.keywords.clear()
        self._clear_hidden = True
        self.remove_btn.setText("Personal info will be removed when you press OK")
        self.remove_btn.setEnabled(False)

    def values(self) -> tuple[dict[str, str], bool]:
        """(changed metadata fields, clear XMP?)"""
        new = {
            "title": self.title.text(), "author": self.author.text(),
            "subject": self.subject.text(), "keywords": self.keywords.text(),
        }
        if self._clear_hidden:
            new["creator"] = ""
            new["producer"] = ""
        changed = {k: v for k, v in new.items() if v != self._meta.get(k, "")}
        return changed, self._clear_hidden
