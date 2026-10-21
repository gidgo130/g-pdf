# SPDX-License-Identifier: AGPL-3.0-or-later
"""Stand-in executables for tests: a Python script behind a .cmd (Windows) or a shell
script (POSIX), so subprocess can run it like a real program."""

import os
import sys
from pathlib import Path


def make_exe(folder, name, code) -> str:
    """An executable ``folder/name`` that runs the Python ``code`` (sys.argv as usual)."""
    folder = Path(folder)
    py = folder / f"{name}.py"
    py.write_text(code, encoding="utf-8")
    if sys.platform == "win32":
        exe = folder / f"{name}.cmd"
        exe.write_text(f'@"{sys.executable}" "{py}" %*\r\n@exit /b %errorlevel%\r\n')
    else:
        exe = folder / name
        exe.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{py}" "$@"\n')
        os.chmod(exe, 0o755)
    return str(exe)
