# SPDX-License-Identifier: AGPL-3.0-or-later
"""Real-input tests: genuine X11 mouse/keyboard events via xdotool.

They catch what the headless suite can't (e.g. a widget that never receives a
real drop). They only run when GPDF_REALINPUT=1, an X11 DISPLAY and xdotool are
available, i.e. in CI under Xvfb; everywhere else they are skipped.

    QT_QPA_PLATFORM=xcb GPDF_REALINPUT=1 xvfb-run -s "-screen 0 1600x1000x24" \
        python -m pytest tests/realinput
"""

import os
import shutil
import subprocess
import time

import pytest
from PySide6.QtCore import QPoint
from PySide6.QtGui import QGuiApplication

ENABLED = bool(
    os.environ.get("GPDF_REALINPUT") == "1"
    and os.environ.get("DISPLAY")
    and shutil.which("xdotool")
)


def pytest_collection_modifyitems(config, items):
    if ENABLED and QGuiApplication.platformName() in ("", "xcb"):
        return
    skip = pytest.mark.skip(
        reason="real-input tests need GPDF_REALINPUT=1, QT_QPA_PLATFORM=xcb, X11 and xdotool"
    )
    for item in items:
        if "realinput" in item.nodeid:
            item.add_marker(skip)


class Robot:
    """Queue xdotool steps, then run them while the Qt event loop keeps going."""

    def __init__(self, qapp) -> None:
        self.qapp = qapp
        self.steps: list[str] = []

    def move(self, p: QPoint) -> "Robot":
        self.steps.append(f"xdotool mousemove {p.x()} {p.y()}; sleep 0.12")
        return self

    def down(self) -> "Robot":
        self.steps.append("xdotool mousedown 1; sleep 0.2")
        return self

    def up(self) -> "Robot":
        self.steps.append("xdotool mouseup 1; sleep 0.3")
        return self

    def click(self, p: QPoint) -> "Robot":
        self.move(p)
        self.steps.append("xdotool click 1; sleep 0.4")
        return self

    def key(self, name: str) -> "Robot":
        self.steps.append(f"xdotool key {name}; sleep 0.3")
        return self

    def drag(self, start: QPoint, end: QPoint, *, n: int = 8, release: bool = True) -> "Robot":
        self.move(start).down()
        for k in range(1, n + 1):
            self.move(start + (end - start) * (k / n))
        self.steps.append("sleep 0.3")
        return self.up() if release else self

    def run(self, timeout: float = 30) -> None:
        proc = subprocess.Popen(["bash", "-c", "\n".join(self.steps)])
        self.steps = []
        deadline = time.monotonic() + timeout
        while proc.poll() is None:
            self.qapp.processEvents()
            time.sleep(0.01)
            if time.monotonic() > deadline:
                proc.kill()
                raise TimeoutError("xdotool script did not finish")
        settle(self.qapp, 0.5)


def settle(qapp, seconds: float = 0.3) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        qapp.processEvents()
        time.sleep(0.01)


@pytest.fixture
def robot(qapp):
    return Robot(qapp)
