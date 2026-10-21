# SPDX-License-Identifier: AGPL-3.0-or-later
import os
from pathlib import Path

import pymupdf
import pytest

from gpdf.core import ocr, speech_text, textedit
from gpdf.core.document import PdfDocument, PdfEditError


@pytest.fixture
def doc(make_pdf, qapp):
    d = PdfDocument(make_pdf("t.pdf", sizes=[(600, 800), (600, 800)]))
    yield d
    d.close()


def test_search_view_space_rotation(doc):
    hits = textedit.search(doc, "page 1")
    assert len(hits) == 1 and hits[0].page == 0
    x0, y0, x1, y1 = hits[0].rect
    doc.rotate_pages([0], 90)
    hit = textedit.search(doc, "page 1")[0]
    # 90° clockwise: page is now 800 wide x 600 tall; hit stays inside it,
    # and the old top-left text lands near the top-right corner.
    assert 0 <= hit.rect[0] and hit.rect[2] <= 800 and hit.rect[3] <= 600
    assert hit.rect[2] > 700 and hit.rect[1] < 200


def test_search_empty_and_nomatch(doc):
    assert textedit.search(doc, "  ") == []
    assert textedit.search(doc, "zzzz") == []


def test_replace_span_fixes_typo(doc):
    s = textedit.span_at(doc, 0, 80, 66)
    assert s is not None and "page 1" in s.text
    textedit.replace_span(doc, 0, s, "t.pdf paige 1")
    txt = doc.raw[0].get_text()
    assert "paige 1" in txt and "page 1" not in txt
    doc.undo()
    assert "page 1" in doc.raw[0].get_text()


def test_replace_text_counts(doc):
    n = textedit.replace_text(doc, 0, "page", "sheet")
    assert n == 1 and "sheet" in doc.raw[0].get_text() and "page" not in doc.raw[0].get_text()
    assert textedit.replace_text(doc, 0, "nothing-here", "x") == 0


def test_unsupported_characters_refused(doc):
    s = textedit.span_at(doc, 0, 80, 66)
    with pytest.raises(PdfEditError):
        textedit.replace_span(doc, 0, s, "日本語")
    assert "page 1" in doc.raw[0].get_text() and not doc.can_undo


def test_spanish_characters_ok(doc):
    textedit.add_text(doc, 1, 72, 200, "¿Qué pasó? Niño áéíóú")
    assert "Niño" in doc.raw[1].get_text()


def test_rotated_pages_refused(doc):
    doc.rotate_pages([0], 90)
    assert textedit.span_at(doc, 0, 10, 10) is None
    with pytest.raises(PdfEditError):
        textedit.add_text(doc, 0, 50, 50, "x")


def test_add_image(doc, tmp_path):
    img = tmp_path / "i.png"
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 40), False)
    pix.set_rect(pix.irect, (255, 0, 0))
    pix.save(str(img))
    textedit.add_image(doc, 0, (100, 300, 200, 400), img)
    assert len(doc.raw[0].get_images()) == 1
    doc.undo()
    assert len(doc.raw[0].get_images()) == 0
    with pytest.raises(PdfEditError):
        textedit.add_image(doc, 0, (0, 0, 1, 1), img)
    with pytest.raises(PdfEditError):
        textedit.add_image(doc, 0, (0, 0, 50, 50), tmp_path / "missing.png")


def test_edit_keeps_other_pages_and_links(make_pdf, qapp):
    d = PdfDocument(make_pdf("l.pdf", sizes=[(600, 800)] * 3))
    try:
        textedit.add_text(d, 1, 72, 200, "hello")
        assert d.page_count == 3
        assert "page 3" in d.raw[2].get_text() and "page 1" in d.raw[0].get_text()
    finally:
        d.close()


# -- speech text ---------------------------------------------------------------

def test_split_chunks_sentences_and_hyphens():
    t = "Hello there. This is a sam-\nple of text! Is it ok? Yes."
    assert speech_text.split_chunks(t) == [
        "Hello there.", "This is a sample of text!", "Is it ok?", "Yes."
    ]


def test_split_chunks_long_sentence_and_empty():
    long = "word " * 300
    chunks = speech_text.split_chunks(long, max_len=100)
    assert all(len(c) <= 100 for c in chunks) and len(chunks) > 10
    assert speech_text.split_chunks("   \n\n ") == []


def test_spanish_inverted_marks():
    assert speech_text.split_chunks("Hola. ¿Cómo estás? Bien.") == [
        "Hola.", "¿Cómo estás?", "Bien."
    ]


def test_page_chunks(doc):
    assert speech_text.page_chunks(doc, 0)


# -- OCR ---------------------------------------------------------------------------

def test_ocr_unavailable_message(doc, monkeypatch):
    monkeypatch.setattr(ocr, "find_tessdata", lambda: None)
    with pytest.raises(PdfEditError, match="Tesseract"):
        ocr.ocr_page(doc, 0)


def test_ocr_refuses_text_pages(doc, monkeypatch):
    monkeypatch.setattr(ocr, "find_tessdata", lambda: "/x")
    monkeypatch.setattr(ocr, "available_languages", lambda: ["eng"])
    with pytest.raises(PdfEditError, match="already contains text"):
        ocr.ocr_page(doc, 0)


@pytest.mark.skipif(ocr.find_tessdata() is None, reason="Tesseract data not installed")
def test_ocr_real(make_pdf, qapp, tmp_path):
    src = pymupdf.open()
    p = src.new_page(width=600, height=300)
    p.insert_text((50, 150), "Hello OCR World", fontsize=40)
    pix = p.get_pixmap(dpi=150)
    scan = pymupdf.open()
    sp = scan.new_page(width=600, height=300)
    sp.insert_image(sp.rect, pixmap=pix)
    path = tmp_path / "scan.pdf"
    scan.save(str(path))
    d = PdfDocument(path)
    try:
        assert not textedit.page_has_text(d, 0)
        ocr.ocr_page(d, 0)
        assert "Hello" in d.raw[0].get_text()
        assert d.page_sizes[0] == pytest.approx((600, 300), abs=1)
        d.undo()
        assert not textedit.page_has_text(d, 0)
    finally:
        d.close()
