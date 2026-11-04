# SPDX-License-Identifier: AGPL-3.0-or-later
"""math-export through the real engine on a two-page PDF rendered here from our own
formulas (PyMuPDF text). Slow (two model loads); skipped unless MinerU is configured
($GPDF_MINERU or mineru-kit on PATH) and its model files are present."""

import json
import os

import pymupdf
import pytest

from gpdf import cli
from gpdf.pipeline.mathocr import engine

pytestmark = pytest.mark.slow


def _engine_or_skip():
    try:
        return engine.prepare(os.environ.get("GPDF_MINERU"), allow_unpinned=True)
    except engine.EngineError as e:
        pytest.skip(f"MinerU not configured: {e}")


@pytest.fixture(scope="module")
def book(tmp_path_factory):
    path = tmp_path_factory.mktemp("mathbook") / "laws.pdf"
    doc = pymupdf.open()
    for n, (num, title, lines, display) in enumerate([
            ("12", "1 Exponential Law", ["The density function of the exponential law with",
                                         "rate parameter λ > 0 is"],
             "f(x) = λ e^{-λx},   x > 0,          (1.1)"),
            ("13", "2 Gamma Law", ["Its generalization, the gamma law with shape α,",
                                   "has the density"],
             "f(x) = β^α x^{α-1} e^{-βx} / Γ(α),   x > 0.   (1.2)")]):
        page = doc.new_page(width=480, height=640)
        page.insert_text((40 if n % 2 == 0 else 400, 40), num, fontname="tiro", fontsize=11)
        page.insert_text((200, 40), "THE LAWS", fontname="tiro", fontsize=10)
        page.insert_text((40, 100), title, fontname="tibo", fontsize=16)
        y = 140
        for s in lines:
            page.insert_text((40, y), s, fontname="tiro", fontsize=12)
            y += 18
        page.insert_text((80, y + 30), display, fontname="tiit", fontsize=13)
        page.insert_text((40, y + 80), "which integrates to one over the positive half-line.",
                         fontname="tiro", fontsize=12)
    doc.save(str(path))
    doc.close()
    return path


def test_real_engine_end_to_end(book, tmp_path):
    eng = _engine_or_skip()
    out = tmp_path / "out"
    code = cli.main(["math-export", str(book), "--out", str(out), "--mineru", eng.exe,
                     "--allow-unpinned", "--no-check-identities"])
    assert code == 0
    root = out / "laws"
    for name in ("pages/p001.md", "pages/p002.md", "pages/p001.flags.json", "text.md",
                 "review.jsonl", "meta.json", "run.json"):
        assert (root / name).is_file(), name
    for n in (1, 2):
        md = (root / "pages" / f"p{n:03d}.md").read_text("utf-8")
        assert md.startswith(f'<!-- page printed="{n + 11}" file="{n}" -->')
        assert md.count("$$") % 2 == 0 and md.count("\\(") == md.count("\\)")
        assert "THE LAWS" not in md
    meta = json.loads((root / "meta.json").read_text("utf-8"))
    assert meta["engine"]["version"] == eng.version
    assert set(meta["engine"]["models"]) == set(engine.PINNED_MODELS)
    rec = json.loads((root / "run.json").read_text("utf-8"))
    assert rec["totals"]["pages"] == 2 and rec["totals"]["displays"] >= 1
    assert rec["engine_seconds"]["parsed"] == {"standard": 2, "basic": 2}
    # the cache makes a second run engine-free
    assert cli.main(["math-export", str(book), "--out", str(out), "--mineru", eng.exe,
                     "--allow-unpinned", "--no-check-identities"]) == 0
    rec = json.loads((root / "run.json").read_text("utf-8"))
    assert rec["engine_seconds"]["parsed"] == {"standard": 0, "basic": 0}
