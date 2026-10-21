# SPDX-License-Identifier: AGPL-3.0-or-later
import pymupdf
import pytest

from gpdf.core.document import PdfDocument, PdfOpenError


def test_basic_info(sample_pdf):
    doc = PdfDocument(sample_pdf)
    try:
        assert doc.name == "sample.pdf"
        assert doc.page_count == 3
        assert doc.page_sizes[0] == pytest.approx((595, 842))
        assert doc.page_sizes[1] == pytest.approx((842, 595))
        assert doc.is_open
    finally:
        doc.close()


def test_rotation_is_reflected_in_page_size(tmp_path):
    path = tmp_path / "rot.pdf"
    raw = pymupdf.open()
    page = raw.new_page(width=200, height=400)
    page.set_rotation(90)
    raw.save(str(path))
    raw.close()

    doc = PdfDocument(path)
    try:
        assert doc.page_sizes[0] == pytest.approx((400, 200))
    finally:
        doc.close()


def test_render_matches_requested_size_and_has_ink(sample_pdf):
    doc = PdfDocument(sample_pdf)
    try:
        image = doc.render(0, 300, 424)
        assert not image.isNull()
        assert abs(image.width() - 300) <= 1
        assert abs(image.height() - 424) <= 1
        # The page is white with some black text, so not every pixel is white.
        colors = {image.pixel(x, y) for x in range(0, image.width(), 7)
                  for y in range(0, image.height(), 7)}
        assert len(colors) > 1
    finally:
        doc.close()


def test_render_out_of_range(sample_pdf):
    doc = PdfDocument(sample_pdf)
    try:
        with pytest.raises(IndexError):
            doc.render(3, 100, 100)
    finally:
        doc.close()


def test_close_is_idempotent_and_blocks_render(sample_pdf):
    doc = PdfDocument(sample_pdf)
    doc.close()
    doc.close()
    assert not doc.is_open
    with pytest.raises(RuntimeError):
        doc.render(0, 100, 100)


def test_missing_file(tmp_path):
    with pytest.raises(PdfOpenError):
        PdfDocument(tmp_path / "nope.pdf")


def test_not_a_pdf(tmp_path):
    path = tmp_path / "notes.txt"
    path.write_text("hello")
    with pytest.raises(PdfOpenError, match="not a PDF"):
        PdfDocument(path)


def test_corrupt_pdf(tmp_path):
    path = tmp_path / "bad.pdf"
    path.write_bytes(b"%PDF-1.4\nthis is not really a pdf")
    with pytest.raises(PdfOpenError):
        PdfDocument(path)


def test_password_protected(tmp_path):
    path = tmp_path / "locked.pdf"
    raw = pymupdf.open()
    raw.new_page()
    raw.save(
        str(path),
        encryption=pymupdf.PDF_ENCRYPT_AES_256,
        user_pw="secret",
        owner_pw="secret",
    )
    raw.close()
    with pytest.raises(PdfOpenError, match="password"):
        PdfDocument(path)
