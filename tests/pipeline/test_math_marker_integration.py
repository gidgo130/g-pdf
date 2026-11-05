# SPDX-License-Identifier: AGPL-3.0-or-later
"""Marker through the real llama-server on a one-page PDF rendered here from our own
formulas (PyMuPDF text). Slow (server start and model loads); skipped unless Marker is
configured ($GPDF_MARKER, llama-server via $GPDF_LLAMA_SERVER or PATH, and the Surya
GGUF files in the Hugging Face cache or $GPDF_MARKER_GGUF)."""

import os

import pymupdf
import pytest

from gpdf.pipeline.mathocr import marker
from gpdf.pipeline.mathocr.engine import EngineError

pytestmark = pytest.mark.slow


def _marker_or_skip():
    if not os.environ.get(marker.ENV_VAR):
        pytest.skip(f"Marker not configured (${marker.ENV_VAR} unset)")
    try:
        return marker.prepare(allow_unpinned=True)
    except EngineError as e:
        pytest.skip(f"Marker not configured: {e}")


def test_real_marker_reads_a_display_and_inline_math(tmp_path):
    m = _marker_or_skip()
    path = tmp_path / "law.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=480, height=640)
    page.insert_text((40, 100), "1 Exponential Law", fontname="tibo", fontsize=16)
    page.insert_text((40, 140), "The density with rate λ > 0 and mean 1/λ is", fontname="tiro",
                     fontsize=12)
    page.insert_text((80, 190), "f(x) = λ e^{-λx},   x > 0,          (1.1)", fontname="tiit",
                     fontsize=13)
    page.insert_text((40, 240), "which integrates to one over the positive half-line.",
                     fontname="tiro", fontsize=12)
    doc.save(str(path))
    doc.close()
    cache = marker.Cache(tmp_path / "cache", "e" * 64, m.version)
    assert marker.run(m, path, [1], cache, log_dir=tmp_path / "logs") == 1
    blocks = marker.blocks(cache.read(1))
    assert any(b["type"] == "text" for b in blocks)
    assert marker.has_math(blocks)
    assert marker.run(m, path, [1], cache) == 0          # cached: no second run
