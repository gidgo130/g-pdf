# SPDX-License-Identifier: AGPL-3.0-or-later
"""The pipeline and CLI must run without Qt: they may not import PySide6, gpdf.core or gpdf.ui."""

import pkgutil
import subprocess
import sys

import gpdf.pipeline

MODULES = ["gpdf.cli", "gpdf.pipeline"] + [
    f"gpdf.pipeline.{m.name}" for m in pkgutil.iter_modules(gpdf.pipeline.__path__)
]


def test_pipeline_imports_no_qt():
    code = (
        "import importlib, sys\n"
        f"for m in {MODULES!r}:\n"
        "    importlib.import_module(m)\n"
        "bad = sorted(m for m in sys.modules if m.split('.')[0] == 'PySide6'"
        " or m.startswith(('gpdf.core', 'gpdf.ui')))\n"
        "print(','.join(bad))\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == ""
