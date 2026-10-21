# SPDX-License-Identifier: AGPL-3.0-or-later
"""Entry point: ``python -m gpdf [file.pdf ...]``."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication, QIcon
from PySide6.QtWidgets import QApplication

from gpdf import APP_NAME, __version__
from gpdf.ui.main_window import MainWindow


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)

    # Crisp rendering at fractional Windows scaling (125%, 150%, ...).
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    icon = Path(__file__).parent / "assets" / "g-pdf.png"
    if icon.is_file():
        app.setWindowIcon(QIcon(str(icon)))

    window = MainWindow()
    window.show()

    paths = [a for a in argv[1:] if not a.startswith("-")]
    if paths:
        # Let the window appear first so any error dialog has something to sit on.
        QTimer.singleShot(0, lambda: window.open_paths(paths))

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
