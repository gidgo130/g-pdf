# SPDX-License-Identifier: AGPL-3.0-or-later
"""DjVu input for math-export: DjVuLibre's ddjvu (an external program, never a Python
dependency) renders the selected pages into a proxy PDF with the DjVu's page count, so
page markers keep the DjVu page index; renders are cached; a missing ddjvu is a clean
error; the real tools, when installed, are exercised on a DjVu made by c44."""

import json
import shutil
import subprocess

import pymupdf
import pytest

from gpdf import cli
from gpdf.pipeline.mathocr import djvu, engine

from .fake_exe import make_exe
from .fake_mineru import make_fake_mineru, make_home

FAKE_DDJVU = r'''
import json, os, sys
import pymupdf
args = sys.argv[1:]
opt = {a.split("=")[0]: a.split("=", 1)[1] for a in args if a.startswith("-") and "=" in a}
src, out = [a for a in args if not a.startswith("-")]
page, dpi = int(opt["-page"]), int(opt["-scale"])
with open(os.environ["FAKE_DDJVU_LOG"], "a") as f:
    f.write(json.dumps({"page": page, "dpi": dpi, "format": opt["-format"]}) + "\n")
w, h = 2 * dpi, 3 * dpi                     # a 2 x 3 inch page
pix = pymupdf.Pixmap(pymupdf.csGRAY, pymupdf.IRect(0, 0, w, h), False)
pix.clear_with(255)
pix.set_rect(pymupdf.IRect(w // 10, h // 10 + page, w // 2, h // 5 + page), (0,))
pix.save(out, output="pnm")
'''
FAKE_DJVUSED = r'''
import os, sys
assert sys.argv[1:3] == ["-e", "n"], sys.argv
print(os.environ.get("FAKE_DJVU_PAGES", "6"))
'''


@pytest.fixture
def tools(tmp_path, monkeypatch):
    folder = tmp_path / "djvulibre"
    folder.mkdir()
    make_exe(folder, "ddjvu", FAKE_DDJVU)
    make_exe(folder, "djvused", FAKE_DJVUSED)
    log = tmp_path / "ddjvu.jsonl"
    monkeypatch.setenv("FAKE_DDJVU_LOG", str(log))
    monkeypatch.setenv(djvu.ENV_VAR, str(folder))
    book = tmp_path / "book.djvu"
    book.write_bytes(b"AT&TFORM fake djvu bytes")
    return folder, log, book


def calls(log):
    return [json.loads(s) for s in log.read_text().splitlines()] if log.is_file() else []


def test_the_proxy_has_the_djvu_page_count_and_renders_only_the_selected_pages(tools, tmp_path):
    folder, log, book = tools
    tool = djvu.prepare()
    assert tool.page_count(book) == 6
    proxy = djvu.proxy_pdf(tool, book, [2, 4], 400, tmp_path / "cache", tmp_path / "proxy.pdf")
    doc = pymupdf.open(str(proxy))
    assert doc.page_count == 6
    assert doc[1].rect.width == pytest.approx(144) and doc[1].rect.height == pytest.approx(216)
    assert len(doc[1].get_images()) == 1 and len(doc[3].get_images()) == 1
    assert doc[0].get_images() == [] and doc[5].get_images() == []
    pix = doc[1].get_pixmap(dpi=400, colorspace=pymupdf.csGRAY)
    assert (pix.width, pix.height) == (800, 1200) and min(pix.samples) < 128
    doc.close()
    assert [(c["page"], c["dpi"]) for c in calls(log)] == [(2, 400), (4, 400)]
    djvu.proxy_pdf(tool, book, [2, 4, 5], 400, tmp_path / "cache", tmp_path / "proxy.pdf")
    assert [c["page"] for c in calls(log)] == [2, 4, 5]          # 2 and 4 from the cache


def test_a_missing_ddjvu_is_an_error_with_install_steps(tmp_path, monkeypatch):
    monkeypatch.setenv(djvu.ENV_VAR, str(tmp_path / "nowhere"))
    with pytest.raises(djvu.DjvuError, match="winget install DjVuLibre.DjView"):
        djvu.prepare()


@pytest.fixture
def mineru(tmp_path, monkeypatch):
    exe = make_fake_mineru(tmp_path)
    home, pins = make_home(tmp_path)
    monkeypatch.setattr(engine, "PINNED_MODELS", pins)
    blocks = lambda n: [{"type": "page_number", "bbox": [0.1, 0.05, 0.15, 0.07],  # noqa: E731
                         "content": [{"type": "text", "content": str(n)}]},
                        {"type": "text", "bbox": [0.1, 0.3, 0.9, 0.35],
                         "content": [{"type": "text", "content": "Some prose."}]}]
    script = {f"p{p:03d}": {t: {"markdown": "Some prose.\n", "blocks": blocks(p + 20)}
                            for t in ("standard", "basic")} for p in range(1, 7)}
    (tmp_path / "pages.json").write_text(json.dumps(script), "utf-8")
    monkeypatch.setenv("FAKE_MINERU_PAGES", str(tmp_path / "pages.json"))
    monkeypatch.setenv("FAKE_MINERU_LOG", str(tmp_path / "mineru.jsonl"))
    monkeypatch.setenv("GPDF_TESSERACT", str(tmp_path / "no-tesseract"))
    return exe, home


def test_math_export_reads_a_djvu_with_djvu_page_numbers(tools, mineru, tmp_path):
    folder, log, book = tools
    exe, home = mineru
    out = tmp_path / "out"
    assert cli.main(["math-export", str(book), "--out", str(out), "--mineru", exe,
                     "--mineru-home", str(home), "--no-check-identities", "--pages", "3-4",
                     "--dpi", "400"]) == 0
    root = out / "book"
    p3 = (root / "pages" / "p003.md").read_text("utf-8")
    assert p3.startswith('<!-- page printed="23" file="3" -->')
    meta = json.loads((root / "meta.json").read_text("utf-8"))
    assert meta["source"]["path"] == str(book) and meta["source"]["page_count"] == 6
    assert meta["source"]["sha256"] == engine.sha256_of(book)
    assert meta["source"]["djvu"]["dpi"] == 400
    assert pymupdf.open(meta["source"]["render_pdf"]).page_count == 6
    assert [c["page"] for c in calls(log)] == [3, 4]
    assert cli.main(["math-export", str(book), "--out", str(out), "--mineru", exe,
                     "--mineru-home", str(home), "--no-check-identities", "--pages", "3",
                     "--ocr-mode", "txt"]) == 1


def test_the_review_pack_renders_from_the_proxy(tools, mineru, tmp_path):
    from gpdf.pipeline.mathocr import pack

    folder, log, book = tools
    exe, home = mineru
    out = tmp_path / "out"
    assert cli.main(["math-export", str(book), "--out", str(out), "--mineru", exe,
                     "--mineru-home", str(home), "--no-check-identities", "--pages", "3"]) == 0
    pack.pack(out / "book", tmp_path / "rev")
    render = (tmp_path / "rev" / "render.py").read_text("utf-8")
    assert repr(str(out / "book" / "source" / "book.pdf")) in render and "book.djvu" not in render


needs_djvulibre = pytest.mark.skipif(not (shutil.which("ddjvu") and shutil.which("djvused")
                                          and shutil.which("c44")),
                                     reason="DjVuLibre (ddjvu, djvused, c44) not on PATH")


@needs_djvulibre
def test_real_ddjvu_renders_a_djvu_made_by_c44(tmp_path, monkeypatch):
    monkeypatch.delenv(djvu.ENV_VAR, raising=False)
    pix = pymupdf.Pixmap(pymupdf.csGRAY, pymupdf.IRect(0, 0, 600, 900), False)
    pix.clear_with(255)
    pix.set_rect(pymupdf.IRect(100, 100, 300, 200), (0,))
    pix.save(str(tmp_path / "page.pgm"), output="pnm")
    book = tmp_path / "real.djvu"
    subprocess.run(["c44", "-dpi", "300", str(tmp_path / "page.pgm"), str(book)], check=True)
    tool = djvu.prepare()
    assert tool.page_count(book) == 1
    proxy = djvu.proxy_pdf(tool, book, [1], 150, tmp_path / "cache", tmp_path / "proxy.pdf")
    doc = pymupdf.open(str(proxy))
    assert doc[0].rect.width == pytest.approx(144, abs=1)        # 2 inches at 300 dpi
    out = doc[0].get_pixmap(dpi=150, colorspace=pymupdf.csGRAY)
    assert (out.width, out.height) == pytest.approx((300, 450), abs=2) and min(out.samples) < 128
