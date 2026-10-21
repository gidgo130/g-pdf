# SPDX-License-Identifier: AGPL-3.0-or-later
"""One open document: the widget that lives inside a tab."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFileDialog, QInputDialog, QMessageBox, QSplitter, QTabWidget, QVBoxLayout, QWidget,
)

from gpdf import APP_NAME
from gpdf.core import ocr, textedit
from gpdf.core.document import FileChangedError, PdfDocument, PdfEditError
from gpdf.ui.bookmarks import BookmarksTree
from gpdf.ui.find_bar import FindBar
from gpdf.ui.page_panel import PagePanel
from gpdf.ui.pdf_view import (
    TOOL_ADD_IMAGE, TOOL_ADD_TEXT, TOOL_BROWSE, TOOL_EDIT_TEXT, PdfView,
)
from gpdf.ui.properties_dialog import PropertiesDialog
from gpdf.ui.read_aloud import ReadAloudBar, ReadAloudController

IMAGE_FILTER = "Images (*.png *.jpg *.jpeg *.bmp *.gif *.tif *.tiff);;All files (*)"


# Dialogs with custom buttons; module-level so tests can replace them.

def ask_file_changed(parent: QWidget, name: str) -> str:
    """Save hit a file another program changed (#15). Returns "save_as",
    "overwrite", "discard" or "cancel"."""
    box = QMessageBox(QMessageBox.Icon.Warning, APP_NAME,
                      f"{name} was changed by another program since you opened it.",
                      parent=parent)
    box.setInformativeText(
        "Save As keeps both versions. Overwrite replaces the other program's "
        "changes with yours. Discard my changes opens the file as the other "
        "program saved it; your edits are lost.")
    save_as = box.addButton("Save As…", QMessageBox.ButtonRole.AcceptRole)
    overwrite = box.addButton("Overwrite", QMessageBox.ButtonRole.DestructiveRole)
    discard = box.addButton("Discard my changes", QMessageBox.ButtonRole.DestructiveRole)
    box.addButton(QMessageBox.StandardButton.Cancel)
    box.setDefaultButton(save_as)
    box.exec()
    return {save_as: "save_as", overwrite: "overwrite", discard: "discard"}.get(
        box.clickedButton(), "cancel")


class DocumentTab(QWidget):
    """A PDF plus its view, page panel and tools. Owns the document: call
    :meth:`dispose` to close it.

    Tabs can move between windows, so nothing here may refer to a particular
    window; the window that currently holds the tab wires up its own signals.
    """

    # "Discard my changes": the window replaces this tab with a fresh one.
    reload_requested = Signal(object)

    def __init__(self, document: PdfDocument, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.document = document
        self.view = PdfView(self)
        self.panel = PagePanel(self)
        self.bookmarks = BookmarksTree(self)
        self.find_bar = FindBar(self.view, self)
        self.reader = ReadAloudController(parent=self)
        self.reader_bar = ReadAloudBar(
            self.reader, lambda: self.view.current_page, self.view.go_to_page, self)
        self.reader_bar.hide()

        self.sidebar = QTabWidget(self)
        self.sidebar.addTab(self.panel, "Pages")
        self.sidebar.addTab(self.bookmarks, "Bookmarks")
        self.sidebar.setTabPosition(QTabWidget.TabPosition.South)

        right = QWidget(self)
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)
        rl.addWidget(self.find_bar)
        rl.addWidget(self.view, 1)
        rl.addWidget(self.reader_bar)

        self.splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.splitter.addWidget(self.sidebar)
        self.splitter.addWidget(right)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([210, 800])
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.splitter)

        self.view.set_document(document)
        self.panel.set_document(document)
        self.bookmarks.set_document(document)
        self.find_bar.set_document(document)
        self.reader.set_document(document)
        self.sidebar.setTabVisible(1, True)

        self.panel.page_activated.connect(self.view.go_to_page)
        self.panel.error.connect(self.show_error)
        self.bookmarks.page_requested.connect(self.view.go_to_page)
        self.view.current_page_changed.connect(self.panel.mark_current)
        self.view.page_clicked.connect(self._on_page_clicked)
        self.view.area_selected.connect(self._on_area_selected)

    @property
    def path(self) -> Path:
        return self.document.path

    # -- messages ------------------------------------------------------------------

    def show_error(self, message: str) -> None:
        QMessageBox.warning(self, APP_NAME, message)

    def _guard(self, fn, *args):
        """Run an operation; show PdfEditError messages instead of crashing."""
        try:
            return fn(*args)
        except PdfEditError as exc:
            self.show_error(str(exc))
        return None

    # -- sidebar / panes -----------------------------------------------------------

    def toggle_sidebar(self) -> None:
        self.sidebar.setVisible(not self.sidebar.isVisible())

    def open_find(self) -> None:
        self.find_bar.open_bar()

    def toggle_reader(self) -> None:
        visible = not self.reader_bar.isVisible()
        self.reader_bar.setVisible(visible)
        if not visible:
            self.reader.stop()

    # -- page operations (act on thumbnail selection, else the current page) -------

    def target_pages(self) -> list[int]:
        sel = self.panel.selected_pages()
        return sel if sel else [self.view.current_page]

    def select_all_pages(self) -> None:
        self.panel.select_pages(list(range(self.document.page_count)))

    def rotate(self, degrees: int) -> None:
        pages = self.target_pages()
        self.panel.select_pages(pages) if self.panel.selected_pages() else None
        self._guard(self.document.rotate_pages, pages, degrees)

    def delete_pages(self) -> None:
        pages = self.target_pages()
        self._guard(self.document.delete_pages, pages)

    def duplicate_pages(self) -> None:
        pages = self.target_pages()
        new = self._guard(self.document.duplicate_pages, pages)
        if new:
            self.panel.select_pages(new)

    def insert_blank(self) -> None:
        pages = self.target_pages()
        new = self._guard(self.document.insert_blank, pages[-1] + 1)
        if new is not None:
            self.panel.select_pages([new])

    def insert_from_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Insert pages from PDF", "", "PDF files (*.pdf)")
        if path:
            at = self.target_pages()[-1] + 1
            new = self._guard(self.document.insert_file, path, at)
            if new:
                self.panel.select_pages(new)

    def extract_pages(self) -> None:
        pages = self.target_pages()
        default = str(self.document.path.with_name(self.document.path.stem + "-extract.pdf"))
        path, _ = QFileDialog.getSaveFileName(
            self, "Save selected pages as", default, "PDF files (*.pdf)")
        if path:
            self._guard(self.document.extract_pages, pages, path)

    # -- content tools -------------------------------------------------------------

    def set_tool(self, tool: str) -> None:
        self.view.set_tool(tool)

    def _on_page_clicked(self, page: int, x: float, y: float) -> None:
        tool = self.view.tool
        if tool == TOOL_EDIT_TEXT:
            span = textedit.span_at(self.document, page, x, y)
            if span is None:
                if self.document.page_rotation(page) != 0:
                    self.show_error("Editing text on rotated pages isn't supported yet.")
                elif not textedit.page_has_text(self.document, page):
                    self.show_error(
                        "This page has no editable text (it may be a scan). "
                        "Try Tools > OCR page first.")
                return
            text, ok = QInputDialog.getText(
                self, "Edit text", "Change this piece of text to:", text=span.text)
            if ok and text != span.text:
                self._guard(textedit.replace_span, self.document, page, span, text)
        elif tool == TOOL_ADD_TEXT:
            text, ok = QInputDialog.getMultiLineText(self, "Add text", "Text to add here:")
            if ok and text.strip():
                size, ok2 = QInputDialog.getInt(self, "Add text", "Font size (points):", 12, 4, 200)
                if ok2:
                    self._guard(textedit.add_text, self.document, page, x, y, text, size)

    def _on_area_selected(self, page: int, x0: float, y0: float, x1: float, y1: float) -> None:
        if self.view.tool != TOOL_ADD_IMAGE:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Choose image", "", IMAGE_FILTER)
        if path:
            self._guard(textedit.add_image, self.document, page, (x0, y0, x1, y1), path)

    def ocr_current_page(self) -> None:
        page = self.view.current_page
        langs = ocr.available_languages()
        if not langs:
            self.show_error(ocr.INSTALL_HINT)
            return
        lang = langs[0]
        if len(langs) > 1:
            lang, ok = QInputDialog.getItem(
                self, "OCR", "Language of the text on this page:", langs,
                langs.index("eng") if "eng" in langs else 0, False)
            if not ok:
                return
        self._guard(ocr.ocr_page, self.document, page, lang)

    def show_properties(self) -> None:
        dialog = PropertiesDialog(self.document, self)
        if dialog.exec():
            changed, clear = dialog.values()
            if changed or clear:
                self.document.set_metadata(changed, clear_xmp=clear)

    # -- saving --------------------------------------------------------------------

    def save(self) -> bool:
        if self.document.is_damaged:  # keep the damaged original (#2)
            path = self.document.path
            return self.save_as(str(path.with_name(f"{path.stem} (recovered).pdf")))
        if not self._confirm_signed():
            return False
        return self._save_to(None)

    def save_as(self, default: str | None = None) -> bool:
        path, _ = QFileDialog.getSaveFileName(
            self, "Save as", default or str(self.document.path), "PDF files (*.pdf)")
        if not path:
            return False
        if not path.lower().endswith(".pdf"):
            path += ".pdf"
        if not self._confirm_signed():
            return False
        return self._save_to(path)

    def _confirm_signed(self) -> bool:
        if not self.document.has_signatures:
            return True
        answer = QMessageBox.warning(
            self, APP_NAME,
            "This PDF has a digital signature. Saving here rewrites the file and "
            "the signature will no longer be valid. Save anyway?",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel)
        return answer == QMessageBox.StandardButton.Save

    def _save_to(self, path: str | None, force: bool = False) -> bool:
        try:
            if path is None:
                self.document.save(force=force)
            else:
                self.document.save_as(path)
            return True
        except FileChangedError:
            return self._on_file_changed()
        except OSError as exc:
            self.show_error(f"Could not save: {exc}")
            return False

    def _on_file_changed(self) -> bool:
        choice = ask_file_changed(self, self.document.name)
        if choice == "save_as":
            return self.save_as()
        if choice == "overwrite":
            return self._save_to(None, force=True)
        if choice == "discard":
            self.reload_requested.emit(self)  # may dispose of this tab
        return False

    def confirm_close(self) -> bool:
        """Ask about unsaved changes. True if it is OK to close the tab."""
        if not self.document.is_dirty:
            return True
        box = QMessageBox.question(
            self, APP_NAME,
            f"Save changes to {self.document.name} before closing?",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save)
        if box == QMessageBox.StandardButton.Save:
            return self.save()
        return box == QMessageBox.StandardButton.Discard

    def dispose(self) -> None:
        """Release the file and schedule this widget for deletion."""
        self.reader.stop()
        self.view.set_document(None)
        self.panel.set_document(None)
        self.bookmarks.set_document(None)
        self.find_bar.set_document(None)
        self.document.close()
        self.deleteLater()
