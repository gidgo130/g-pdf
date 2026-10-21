# SPDX-License-Identifier: AGPL-3.0-or-later
"""A tab widget whose tabs can be dragged out into new windows and back in.

How it works
------------
* Dragging a tab inside the bar reorders it (QTabBar's built-in behaviour).
* Dragging it more than ``DETACH_DISTANCE`` pixels away from the bar hands the
  gesture over to a real ``QDrag``. The dragged widget is parked in a registry
  under a random token, and only the token travels in the drag's MIME data.
* Any ``DetachableTabWidget`` (in any window) accepts that drag. On drop it
  takes the widget out of the registry and emits ``tab_dropped``; the owning
  window decides how to move it.
* If nobody took the widget when the drag ends (the drop landed outside every
  tab area), the source emits ``detach_requested`` and the window builds a new
  window for it.
  Dropping a tab on its *own* window's page area also detaches it.

This module only does gestures and signalling. Actually moving a tab between
windows lives in ``MainWindow`` so it can be unit-tested without a mouse.
Esc cancels the drag. Qt reports the same result for Esc and for a drop outside
every window, so we check whether the primary button (or Esc) is still held
when the drag ends: a real drop means the button was just released.
"""

from __future__ import annotations

import sys
import uuid

from PySide6.QtCore import QDir, QEvent, QMimeData, QObject, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QCursor, QDrag, QGuiApplication, QMouseEvent, QPixmap
from PySide6.QtWidgets import QTabBar, QTabWidget, QWidget

MIME_TYPE = "application/x-gpdf-tab"
DETACH_DISTANCE = 28  # px outside the tab bar before a tab starts to detach

# token -> tab widget currently being dragged
_pending: dict[str, QWidget] = {}


def register_drag(tab: QWidget) -> str:
    """Park a tab for dragging and return the token that identifies it."""
    token = uuid.uuid4().hex
    _pending[token] = tab
    return token


def discard_drag(token: str) -> None:
    _pending.pop(token, None)


def drag_is_pending(token: str) -> bool:
    return token in _pending


def drag_was_cancelled() -> bool:
    """True if the drag that just ended was cancelled with Esc, not dropped.

    Esc ends a drag while the primary button is still down; a drop means it was
    just released. Any failure falls back to False (treat it as a drop).
    """
    try:
        if sys.platform == "win32":
            import ctypes

            user32 = ctypes.windll.user32
            swapped = user32.GetSystemMetrics(23)  # SM_SWAPBUTTON
            button = 0x02 if swapped else 0x01  # VK_RBUTTON / VK_LBUTTON
            return bool(
                user32.GetAsyncKeyState(button) & 0x8000
                or user32.GetAsyncKeyState(0x1B) & 0x8000  # VK_ESCAPE
            )
        return bool(QGuiApplication.mouseButtons() & Qt.MouseButton.LeftButton)
    except Exception:
        return False


class _EscWatcher(QObject):
    """Records an Esc press during a tab drag (X11/macOS deliver it to Qt; on
    Windows the OLE drag loop doesn't, and drag_was_cancelled() covers that)."""

    def __init__(self) -> None:
        super().__init__()
        self.pressed = False
        self._done = False

    def install(self) -> None:
        if not self._done:
            QGuiApplication.instance().installEventFilter(self)

    def uninstall(self) -> None:
        self._done = True
        QGuiApplication.instance().removeEventFilter(self)

    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
            self.pressed = True
        return False


def make_tab_mime(token: str) -> QMimeData:
    mime = QMimeData()
    mime.setData(MIME_TYPE, token.encode("ascii"))
    return mime


def take_dragged_tab(mime: QMimeData) -> QWidget | None:
    """Claim the tab a drag is carrying, or None if it isn't one of ours."""
    if not mime.hasFormat(MIME_TYPE):
        return None
    token = bytes(mime.data(MIME_TYPE).data()).decode("ascii", "ignore")
    return _pending.pop(token, None)


def dropped_pdf_paths(mime: QMimeData) -> list[str]:
    """Local .pdf file paths carried by a file drag from the desktop."""
    if not mime.hasUrls():
        return []
    # toLocalFile() gives forward slashes; use the OS's own form (C:\a\b.pdf).
    paths = [QDir.toNativeSeparators(u.toLocalFile()) for u in mime.urls() if u.isLocalFile()]
    return [p for p in paths if p.lower().endswith(".pdf")]


def accepts_drag(mime: QMimeData) -> bool:
    return mime.hasFormat(MIME_TYPE) or bool(dropped_pdf_paths(mime))


class DetachableTabBar(QTabBar):
    tab_dropped = Signal(object, int)  # (tab widget, insertion index, -1 = end)
    detach_requested = Signal(object, QPoint)  # (tab widget, global cursor pos)
    files_dropped = Signal(list)  # list of .pdf paths

    def __init__(self, owner: QTabWidget) -> None:
        super().__init__(owner)
        self._owner = owner
        self._press_tab: QWidget | None = None
        self.setAcceptDrops(True)
        self.setMovable(True)
        self.setExpanding(False)
        self.setUsesScrollButtons(True)
        self.setElideMode(Qt.TextElideMode.ElideMiddle)

    # -- gesture: leaving the bar starts a real drag ------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            index = self.tabAt(event.position().toPoint())
            self._press_tab = self._owner.widget(index) if index >= 0 else None
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._press_tab = None
        super().mouseReleaseEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if (
            event.buttons() & Qt.MouseButton.LeftButton
            and self._press_tab is not None
            and self._outside_bar(event.position().toPoint())
        ):
            tab, self._press_tab = self._press_tab, None
            # Finish QTabBar's own reorder-drag so it doesn't fight QDrag.
            release = QMouseEvent(
                QEvent.Type.MouseButtonRelease,
                event.position(),
                event.globalPosition(),
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.NoButton,
                event.modifiers(),
            )
            super().mouseReleaseEvent(release)
            self.start_tab_drag(tab)
            return
        super().mouseMoveEvent(event)

    def _outside_bar(self, pos: QPoint) -> bool:
        reach = DETACH_DISTANCE
        return not self.rect().adjusted(-reach, -reach, reach, reach).contains(pos)

    def start_tab_drag(self, tab: QWidget) -> None:
        """Run the drag; detach into a new window if nothing accepted the drop."""
        token = register_drag(tab)
        drag = QDrag(self)
        drag.setMimeData(make_tab_mime(token))

        index = self._owner.indexOf(tab)
        if index >= 0:
            pixmap: QPixmap = self.grab(self.tabRect(index))
            if not pixmap.isNull():
                drag.setPixmap(pixmap)
                drag.setHotSpot(QPoint(pixmap.width() // 2, pixmap.height() // 2))

        esc = _EscWatcher()
        # Qt's own drag filter swallows Esc and the newest filter runs first, so
        # install ours once the drag's event loop is running.
        QTimer.singleShot(0, esc.install)
        try:
            drag.exec(Qt.DropAction.MoveAction)
        finally:
            esc.uninstall()

        if drag_is_pending(token):  # nobody took it: Esc, or dropped outside
            discard_drag(token)
            if esc.pressed or drag_was_cancelled():
                return
            self.detach_requested.emit(tab, QCursor.pos())

    # -- accepting drops ----------------------------------------------------

    def dragEnterEvent(self, event) -> None:
        if accepts_drag(event.mimeData()):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        if accepts_drag(event.mimeData()):
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:
        mime = event.mimeData()
        tab = take_dragged_tab(mime)
        if tab is not None:
            self.tab_dropped.emit(tab, self._insertion_index(event.position().toPoint()))
            event.acceptProposedAction()
            return
        paths = dropped_pdf_paths(mime)
        if paths:
            self.files_dropped.emit(paths)
            event.acceptProposedAction()
            return
        super().dropEvent(event)

    def _insertion_index(self, pos: QPoint) -> int:
        """Index to insert before, or -1 for the end."""
        index = self.tabAt(pos)
        if index < 0:
            return -1
        rect = self.tabRect(index)
        return index + 1 if pos.x() > rect.center().x() else index


class DetachableTabWidget(QTabWidget):
    tab_dropped = Signal(object, int)
    detach_requested = Signal(object, QPoint)
    files_dropped = Signal(list)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        bar = DetachableTabBar(self)
        self.setTabBar(bar)
        self.setTabsClosable(True)
        self.setDocumentMode(True)
        bar.tab_dropped.connect(self.tab_dropped)
        bar.detach_requested.connect(self.detach_requested)
        bar.files_dropped.connect(self.files_dropped)
        # Dropping on the page area (not just the strip) also adopts the tab.
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event) -> None:
        if accepts_drag(event.mimeData()):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        if accepts_drag(event.mimeData()):
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:
        mime = event.mimeData()
        tab = take_dragged_tab(mime)
        if tab is not None:
            if self.indexOf(tab) >= 0:
                # Own tab dropped on its own page area: detach, as browsers do.
                where = self.mapToGlobal(event.position().toPoint())
                self.detach_requested.emit(tab, where)
            else:
                self.tab_dropped.emit(tab, -1)
            event.acceptProposedAction()
            return
        paths = dropped_pdf_paths(mime)
        if paths:
            self.files_dropped.emit(paths)
            event.acceptProposedAction()
            return
        super().dropEvent(event)
