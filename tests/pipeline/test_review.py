# SPDX-License-Identifier: AGPL-3.0-or-later
"""The offline review sheet: embedded data, images, and that it is self-contained."""

import base64
import io
import json
import re

import pytest

pytest.importorskip("numpy")
Image = pytest.importorskip("PIL.Image")
import pymupdf  # noqa: E402

from gpdf.pipeline import review, split  # noqa: E402
from gpdf.pipeline.report import Decisions, Detected, PageDecision  # noqa: E402

DATA_RE = re.compile(r'<script type="application/json" id="gpdf-data">(.*?)</script>', re.S)


def sheet_data(text):
    (block,) = DATA_RE.findall(text)
    return block, json.loads(block)


def decode(uri):
    head, b64 = uri.split(",", 1)
    assert head == "data:image/jpeg;base64"
    return Image.open(io.BytesIO(base64.b64decode(b64)))


@pytest.fixture(scope="module")
def book_sheet(book, tmp_path_factory):
    """Dry run of the main synthetic book (flagged, unsplittable and rotated pages) and
    its sheet: (path, decisions, html text)."""
    path, _ = book
    decisions, _ = split.dry_run(path)
    sheet = tmp_path_factory.mktemp("sheet") / "book_split.review.html"
    with pymupdf.open(path) as doc:
        size = review.write_review_sheet(doc, decisions, sheet, "book")
    assert size == sheet.stat().st_size
    return path, decisions, sheet.read_text("utf-8")


def test_sheet_data_matches_the_decisions(book_sheet):
    path, decisions, text = book_sheet
    _, data = sheet_data(text)
    assert data["decisions"] == json.loads(json.dumps(decisions.to_dict()))
    assert data["source_size_text"] == str(decisions.source_size)
    assert data["source_mtime_ns_text"] == str(decisions.source_mtime_ns)
    assert data["stem"] == "book" and data["source_name"] == "book.pdf"
    assert [p["page"] for p in data["pages"]] == [d.page for d in decisions.pages]


def test_images_thumbnails_everywhere_detail_on_flagged_pages(book_sheet):
    path, decisions, text = book_sheet
    _, data = sheet_data(text)
    flagged = {d.page for d in decisions.pages if d.detected.flagged}
    assert flagged
    with pymupdf.open(path) as doc:
        for d, v in zip(decisions.pages, data["pages"]):
            r = doc[d.page - 1].rect
            assert (v["width"], v["height"]) == pytest.approx((r.width, r.height), abs=0.01)
            expect = {"thumb": (r.width, r.height, review.THUMB_DPI)}
            assert (v["full"] is not None) == (v["close"] is not None) == (d.page in flagged)
            if d.page in flagged:
                x0, x1 = v["close"]["x"]
                assert (x0, x1) == review.close_range(d.cut or d.detected.cut)
                expect["full"] = (r.width, r.height, review.FULL_DPI)
                expect["close"] = ((x1 - x0) * r.width, r.height, review.CLOSE_DPI)
            for name, (w, h, dpi) in expect.items():
                im = decode(v[name]["src"])
                im.load()
                assert im.mode == "L" and im.size == (v[name]["w"], v[name]["h"])
                assert im.size[0] == pytest.approx(w * dpi / 72, abs=2), (d.page, name)
                assert im.size[1] == pytest.approx(h * dpi / 72, abs=2), (d.page, name)


def test_unsplittable_pages_are_marked(book_sheet):
    _, decisions, text = book_sheet
    _, data = sheet_data(text)
    marks = {v["page"]: v["splittable"] for v in data["pages"]}
    cannot = {d.page for d in decisions.pages
              if any(r.startswith("cannot split:") for r in d.detected.reasons)}
    assert cannot                                    # the two_images page
    assert {p for p, ok in marks.items() if not ok} == cannot


def test_sheet_is_self_contained(book_sheet):
    text = book_sheet[2]
    assert "http://" not in text and "https://" not in text
    assert not re.search(r"""(src|href)\s*=\s*["']?//""", text, re.I)
    assert "@import" not in text
    assert not re.search(r"""url\(\s*["']?[a-z][a-z0-9+.-]*:""", text, re.I)
    block, _ = sheet_data(text)
    assert "</" not in block


def test_script_end_in_the_data_is_escaped(tmp_path):
    """A reason (or file name) containing </script> cannot end the data block."""
    doc = pymupdf.open()
    doc.new_page(width=200, height=100)
    nasty = "cannot split: </script><script>alert(1)</script>"
    dec = Decisions("C:/libros/a <b> & c.pdf", 10, 2**62 + 1, [PageDecision(
        1, Detected(True, [0.5, 0.5], 0.3, True, [nasty]), "split", [0.5, 0.5], False)])
    path = tmp_path / "x.review.html"
    review.write_review_sheet(doc, dec, path, "x </script>")
    text = path.read_text("utf-8")
    block, data = sheet_data(text)
    assert "</" not in block
    assert data["decisions"]["pages"][0]["detected"]["reasons"] == [nasty]
    assert data["source_mtime_ns_text"] == str(2**62 + 1)       # exact, beyond JS numbers
    assert data["pages"][0]["splittable"] is False
    assert text.count("</script>") == 2                         # data block + code block
    assert "<title>Review: a &lt;b&gt; &amp; c.pdf</title>" in text
