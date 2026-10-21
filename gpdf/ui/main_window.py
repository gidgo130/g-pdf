# SPDX-License-Identifier: AGPL-3.0-or-later
"""The application window: a tab strip of open PDFs plus menus and status bar.

Any number of ``MainWindow`` instances can exist at once. Tabs move freely
between them (see ``move_tab_here`` / ``_on_detach_requested``); a window whose
last tab leaves closes itself, unless it is the only window left.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QFont, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from gpdf import APP_NAME
from gpdf.core.document import PdfDocument, PdfOpenError
from gpdf.ui.detachable_tabs import DetachableTabWidget, accepts_drag, dropped_pdf_paths
from gpdf.ui.doc_tab import DocumentTab
from gpdf.ui.pdf_view import TOOL_ADD_IMAGE, TOOL_ADD_TEXT, TOOL_BROWSE, TOOL_EDIT_TEXT


class _EmptyState(QWidget):
    """Shown when a window has no tabs."""

    def __init__(self, on_open) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(12)

        title = QLabel(APP_NAME)
        font = QFont(title.font())
        font.setPointSize(font.pointSize() + 12)
        font.setBold(True)
        title.setFont(font)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        hint = QLabel("Open a PDF to get started, or drop one here.")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)

        button = QPushButton("Open PDF…")
        button.clicked.connect(lambda _=False: on_open())

        layout.addWidget(title)
        layout.addWidget(hint)
        layout.addWidget(button, alignment=Qt.AlignmentFlag.AlignCenter)


class MainWindow(QMainWindow):
    _windows: list[MainWindow] = []

    @classmethod
    def windows(cls) -> list[MainWindow]:
        """All open windows."""
        return list(cls._windows)

    def __init__(self) -> None:
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setAcceptDrops(True)
        self.setWindowTitle(APP_NAME)
        self.resize(1100, 820)
        self._closing = False

        self.tabs = DetachableTabWidget()
        self._stack = QStackedWidget()
        self._stack.addWidget(_EmptyState(self.open_dialog))  # page 0
        self._stack.addWidget(self.tabs)  # page 1
        self.setCentralWidget(self._stack)

        self._page_label = QLabel()
        self._zoom_label = QLabel()
        self.statusBar().addPermanentWidget(self._page_label)
        self.statusBar().addPermanentWidget(self._zoom_label)

        self._build_actions()
        self.tabs.currentChanged.connect(self._refresh)
        self.tabs.tabCloseRequested.connect(self.close_tab_at)
        self.tabs.tab_dropped.connect(self.move_tab_here)
        self.tabs.detach_requested.connect(self._on_detach_requested)
        self.tabs.files_dropped.connect(self.open_paths)

        MainWindow._windows.append(self)
        self._refresh()

    # -- actions and menus --------------------------------------------------

    def _action(self, text: str, slot, *shortcuts) -> QAction:
        action = QAction(text, self)
        if shortcuts:
            action.setShortcuts([QKeySequence(s) for s in shortcuts])
        action.triggered.connect(lambda _checked=False, s=slot: s())
        self.addAction(action)  # keeps shortcuts live even if hidden
        return action

    def _build_actions(self) -> None:
        std = QKeySequence.StandardKey
        a_new = self._action("&New Window", self.new_window, "Ctrl+N")
        a_open = self._action("&Open…", self.open_dialog, std.Open)
        self._a_close_tab = self._action("&Close Tab", self.close_current_tab, "Ctrl+W")
        a_close_win = self._action("Close &Window", self.close, "Ctrl+Shift+W")
        a_quit = self._action("&Quit", QApplication.closeAllWindows, "Ctrl+Q")

        self._a_zoom_in = self._action(
            "Zoom &In", lambda: self._with_view("zoom_in"), std.ZoomIn, "Ctrl+="
        )
        self._a_zoom_out = self._action(
            "Zoom &Out", lambda: self._with_view("zoom_out"), std.ZoomOut
        )
        self._a_fit = self._action(
            "Fit &Width", lambda: self._with_view("fit_width"), "Ctrl+0"
        )
        self._a_actual = self._action(
            "&Actual Size", lambda: self._with_view("reset_zoom"), "Ctrl+1"
        )
        self._a_goto = self._action("&Go to Page…", self.go_to_page_dialog, "Ctrl+G")
        self._a_next = self._action("&Next Tab", lambda: self._cycle_tab(1), "Ctrl+Tab")
        self._a_prev = self._action(
            "&Previous Tab", lambda: self._cycle_tab(-1), "Ctrl+Shift+Tab"
        )

        # -- file / edit / pages / tools actions (all act on the current tab)
        def on_tab(method, *args):
            def run():
                tab = self.current_tab()
                if tab is not None:
                    getattr(tab, method)(*args)
            return run

        self._a_save = self._action("&Save", on_tab("save"), std.Save)
        self._a_save_as = self._action("Save &As…", on_tab("save_as"), "Ctrl+Shift+S")
        self._a_props = self._action("Document &Properties…", on_tab("show_properties"))
        self._a_undo = self._action("&Undo", self._undo, std.Undo)
        self._a_redo = self._action("&Redo", self._redo, "Ctrl+Y", "Ctrl+Shift+Z")
        self._a_find = self._action("&Find…", on_tab("open_find"), std.Find)
        self._a_select_all = self._action("Select &All Pages", on_tab("select_all_pages"), "Ctrl+Shift+A")
        self._a_rot_l = self._action("Rotate &Left", on_tab("rotate", -90), "Ctrl+[")
        self._a_rot_r = self._action("Rotate &Right", on_tab("rotate", 90), "Ctrl+]")
        self._a_delete = self._action("&Delete Pages", on_tab("delete_pages"), "Ctrl+Del")
        self._a_dup = self._action("D&uplicate Pages", on_tab("duplicate_pages"), "Ctrl+D")
        self._a_blank = self._action("Insert &Blank Page", on_tab("insert_blank"))
        self._a_insert = self._action("Insert Pages from &File…", on_tab("insert_from_file"))
        self._a_extract = self._action("&Extract Pages…", on_tab("extract_pages"))
        self._a_sidebar = self._action("Toggle &Sidebar", on_tab("toggle_sidebar"), "F4")
        self._a_read = self._action("&Read Aloud", on_tab("toggle_reader"), "Ctrl+Shift+R")
        self._a_ocr = self._action("&OCR This Page…", on_tab("ocr_current_page"))

        self._tool_group = QActionGroup(self)
        self._tool_actions: dict[str, QAction] = {}
        for tool, text in (
            (TOOL_BROWSE, "&Browse"),
            (TOOL_EDIT_TEXT, "&Edit Text (click a line)"),
            (TOOL_ADD_TEXT, "Add &Text (click a spot)"),
            (TOOL_ADD_IMAGE, "Add &Image (drag an area)"),
        ):
            action = self._action(text, lambda t=tool: self._set_tool(t))
            action.setCheckable(True)
            self._tool_group.addAction(action)
            self._tool_actions[tool] = action
        self._tool_actions[TOOL_BROWSE].setChecked(True)

        file_menu = self.menuBar().addMenu("&File")
        for a in (a_new, a_open):
            file_menu.addAction(a)
        file_menu.addSeparator()
        for a in (self._a_save, self._a_save_as, self._a_props):
            file_menu.addAction(a)
        file_menu.addSeparator()
        for a in (self._a_close_tab, a_close_win):
            file_menu.addAction(a)
        file_menu.addSeparator()
        file_menu.addAction(a_quit)

        edit_menu = self.menuBar().addMenu("&Edit")
        for a in (self._a_undo, self._a_redo, self._a_find):
            edit_menu.addAction(a)

        pages_menu = self.menuBar().addMenu("&Pages")
        for a in (self._a_select_all, self._a_rot_l, self._a_rot_r, self._a_delete,
                  self._a_dup, self._a_blank, self._a_insert, self._a_extract):
            pages_menu.addAction(a)

        tools_menu = self.menuBar().addMenu("&Tools")
        for a in self._tool_actions.values():
            tools_menu.addAction(a)
        tools_menu.addSeparator()
        for a in (self._a_ocr, self._a_read):
            tools_menu.addAction(a)

        view_menu = self.menuBar().addMenu("&View")
        for a in (self._a_zoom_in, self._a_zoom_out, self._a_fit, self._a_actual):
            view_menu.addAction(a)
        view_menu.addSeparator()
        for a in (self._a_sidebar, self._a_goto, self._a_next, self._a_prev):
            view_menu.addAction(a)

        toolbar = self.addToolBar("Main")
        toolbar.setMovable(False)
        for a in (self._a_save, self._a_undo, self._a_redo):
            toolbar.addAction(a)
        toolbar.addSeparator()
        for a in (self._a_rot_l, self._a_rot_r, self._a_delete, self._a_dup):
            toolbar.addAction(a)
        toolbar.addSeparator()
        for a in self._tool_actions.values():
            toolbar.addAction(a)
        toolbar.addSeparator()
        for a in (self._a_find, self._a_read):
            toolbar.addAction(a)

        self._tab_actions = [
            self._a_close_tab, self._a_zoom_in, self._a_zoom_out, self._a_fit,
            self._a_actual, self._a_goto, self._a_next, self._a_prev,
            self._a_save, self._a_save_as, self._a_props, self._a_find,
            self._a_select_all, self._a_rot_l, self._a_rot_r, self._a_delete,
            self._a_dup, self._a_blank, self._a_insert, self._a_extract,
            self._a_sidebar, self._a_read, self._a_ocr, *self._tool_actions.values(),
        ]

    def _undo(self) -> None:
        tab = self.current_tab()
        if tab is not None:
            tab.document.undo()

    def _redo(self) -> None:
        tab = self.current_tab()
        if tab is not None:
            tab.document.redo()

    def _set_tool(self, tool: str) -> None:
        tab = self.current_tab()
        if tab is not None:
            tab.set_tool(tool)

    # -- queries ------------------------------------------------------------

    def current_tab(self) -> DocumentTab | None:
        widget = self.tabs.currentWidget()
        return widget if isinstance(widget, DocumentTab) else None

    def _with_view(self, method: str) -> None:
        tab = self.current_tab()
        if tab is not None:
            getattr(tab.view, method)()

    # -- opening files ------------------------------------------------------

    def open_dialog(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Open PDF", "", "PDF files (*.pdf);;All files (*)"
        )
        self.open_paths(paths)

    def open_paths(self, paths) -> None:
        errors = []
        for path in paths:
            try:
                self.open_path(path)
            except PdfOpenError as exc:
                errors.append(str(exc))
        if errors:
            QMessageBox.warning(self, APP_NAME, "\n\n".join(errors))

    def open_path(self, path: str | Path) -> DocumentTab:
        """Open a PDF in a new tab here; if it's already open, select it here,
        moving its tab over first when it lives in another window."""
        resolved = Path(path).expanduser().resolve()
        for window in MainWindow.windows():
            for i in range(window.tabs.count()):
                tab = window.tabs.widget(i)
                if isinstance(tab, DocumentTab) and tab.path.resolve() == resolved:
                    if window is self:
                        self.tabs.setCurrentIndex(i)
                        self.raise_()
                        self.activateWindow()
                    else:
                        self.move_tab_here(tab)  # selects, raises and activates
                    return tab

        tab = DocumentTab(PdfDocument(resolved))  # may raise PdfOpenError
        self.adopt_tab(tab)
        return tab

    # -- tab bookkeeping ----------------------------------------------------

    def adopt_tab(self, tab: DocumentTab, index: int = -1) -> None:
        """Put a tab into this window and wire its signals to this window."""
        if index < 0 or index > self.tabs.count():
            i = self.tabs.addTab(tab, tab.document.name)
        else:
            i = self.tabs.insertTab(index, tab, tab.document.name)
        self.tabs.setTabToolTip(i, str(tab.path))
        tab.view.current_page_changed.connect(self._refresh_status)
        tab.view.zoom_changed.connect(self._refresh_status)
        tab.document.state_changed.connect(self._refresh)
        self.tabs.setCurrentIndex(i)
        self._after_tabs_changed()

    def release_tab(self, tab: DocumentTab) -> None:
        """Take a tab out of this window (without closing its document)."""
        i = self.tabs.indexOf(tab)
        if i < 0:
            return
        self.tabs.removeTab(i)
        self._disconnect_tab(tab)
        self._after_tabs_changed()

    def _disconnect_tab(self, tab: DocumentTab) -> None:
        for signal, slot in (
            (tab.view.current_page_changed, self._refresh_status),
            (tab.view.zoom_changed, self._refresh_status),
            (tab.document.state_changed, self._refresh),
        ):
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass

    def _after_tabs_changed(self) -> None:
        self._refresh()
        if self.tabs.count() == 0 and len(MainWindow._windows) > 1 and not self._closing:
            QTimer.singleShot(0, self._close_if_empty)

    def _close_if_empty(self) -> None:
        if self.tabs.count() == 0 and len(MainWindow._windows) > 1:
            self.close()

    def close_tab_at(self, index: int) -> None:
        tab = self.tabs.widget(index)
        if isinstance(tab, DocumentTab):
            if not tab.confirm_close():
                return
            self.release_tab(tab)
            tab.dispose()

    def close_current_tab(self) -> None:
        if self.tabs.count():
            self.close_tab_at(self.tabs.currentIndex())

    def _cycle_tab(self, step: int) -> None:
        n = self.tabs.count()
        if n:
            self.tabs.setCurrentIndex((self.tabs.currentIndex() + step) % n)

    # -- moving tabs between windows ---------------------------------------

    def move_tab_here(self, tab: DocumentTab, index: int = -1) -> None:
        """Move ``tab`` (from this or any other window) to ``index`` here.

        ``index`` means "insert before this position"; -1 means the end.
        """
        source = tab.window()
        if source is self:
            self._reorder(tab, index)
            return
        if isinstance(source, MainWindow):
            source.release_tab(tab)
        self.adopt_tab(tab, index)
        self.raise_()
        self.activateWindow()

    def _reorder(self, tab: DocumentTab, index: int) -> None:
        count = self.tabs.count()
        current = self.tabs.indexOf(tab)
        target = count - 1 if index < 0 else (index - 1 if current < index else index)
        target = max(0, min(target, count - 1))
        if current != target:
            self.tabs.tabBar().moveTab(current, target)
        self.tabs.setCurrentIndex(target)

    def _on_detach_requested(self, tab: DocumentTab, global_pos: QPoint) -> None:
        """A tab was dropped outside every tab area: give it its own window."""
        new_window = MainWindow()  # create first so the source sees >1 windows
        new_window.resize(self.size())
        new_window.move(global_pos - QPoint(60, 18))  # cursor lands near the tab strip
        self.release_tab(tab)
        new_window.adopt_tab(tab)
        new_window.show()
        new_window.raise_()
        new_window.activateWindow()

    def new_window(self) -> MainWindow:
        window = MainWindow()
        window.move(self.pos() + QPoint(32, 32))
        window.show()
        return window

    # -- view helpers -------------------------------------------------------

    def go_to_page_dialog(self) -> None:
        tab = self.current_tab()
        if tab is None:
            return
        view = tab.view
        page, ok = QInputDialog.getInt(
            self, "Go to page", f"Page (1–{view.page_count}):",
            view.current_page + 1, 1, view.page_count,
        )
        if ok:
            view.go_to_page(page - 1)

    # -- refreshing UI state ------------------------------------------------

    def _refresh(self, *_) -> None:
        tab = self.current_tab()
        self._stack.setCurrentIndex(1 if self.tabs.count() else 0)
        for action in self._tab_actions:
            action.setEnabled(tab is not None)
        for i in range(self.tabs.count()):
            t = self.tabs.widget(i)
            if isinstance(t, DocumentTab):
                label = ("● " if t.document.is_dirty else "") + t.document.name
                if self.tabs.tabText(i) != label:
                    self.tabs.setTabText(i, label)
                self.tabs.setTabToolTip(i, str(t.path))
        if tab is not None:
            doc = tab.document
            self.setWindowTitle(
                f"{'● ' if doc.is_dirty else ''}{doc.name} — {APP_NAME}")
            self._a_undo.setEnabled(doc.can_undo)
            self._a_redo.setEnabled(doc.can_redo)
            self._a_undo.setText(f"&Undo {doc.undo_label}".strip())
            self._a_redo.setText(f"&Redo {doc.redo_label}".strip())
            self._a_save.setEnabled(doc.is_dirty)
            self._tool_actions[tab.view.tool].setChecked(True)
        else:
            self.setWindowTitle(APP_NAME)
            self._a_undo.setEnabled(False)
            self._a_redo.setEnabled(False)
        self._refresh_status()

    def _refresh_status(self, *_) -> None:
        tab = self.current_tab()
        if tab is None:
            self._page_label.clear()
            self._zoom_label.clear()
            return
        view = tab.view
        self._page_label.setText(f"Page {view.current_page + 1} of {view.page_count}")
        self._zoom_label.setText(f"{round(view.zoom * 100)}%")

    # -- dropping PDF files onto the window --------------------------------

    def dragEnterEvent(self, event) -> None:
        if dropped_pdf_paths(event.mimeData()):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dropEvent(self, event) -> None:
        paths = dropped_pdf_paths(event.mimeData())
        if paths:
            event.acceptProposedAction()
            self.open_paths(paths)
        else:
            super().dropEvent(event)

    # -- closing ------------------------------------------------------------

    def closeEvent(self, event) -> None:
        for i in range(self.tabs.count()):
            tab = self.tabs.widget(i)
            if isinstance(tab, DocumentTab) and not tab.confirm_close():
                event.ignore()
                return
        self._closing = True
        for i in reversed(range(self.tabs.count())):
            tab = self.tabs.widget(i)
            self.tabs.removeTab(i)
            if isinstance(tab, DocumentTab):
                self._disconnect_tab(tab)
                tab.dispose()
        if self in MainWindow._windows:
            MainWindow._windows.remove(self)
        event.accept()
