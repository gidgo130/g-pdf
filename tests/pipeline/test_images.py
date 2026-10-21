# SPDX-License-Identifier: AGPL-3.0-or-later
"""Page pixels, splittability checks and half writers (small inline pages)."""

import io
import math

import pytest

np = pytest.importorskip("numpy")
Image = pytest.importorskip("PIL.Image")
import pymupdf  # noqa: E402

from gpdf.pipeline import images  # noqa: E402


def marked(h=300, w=500, seed=1):
    """1-bit test pattern (True = black) with an orientation marker at top-left."""
    a = np.random.default_rng(seed).random((h, w)) < 0.3
    a[:, :50] = True
    a[:40] = False
    a[:20, 100:200] = True
    return a


def g4_page(doc, black, rect=None, page_size=None):
    h, w = black.shape
    page = doc.new_page(width=page_size[0] if page_size else w * 72 / 200,
                        height=page_size[1] if page_size else h * 72 / 200)
    x = images.g4_xobject(doc, Image.fromarray(~black))
    page.insert_image(rect or page.rect, xref=x, keep_proportion=False)
    return page, x


def set_cm(page, matrix):
    """Replace the page's single image placement matrix."""
    doc = page.parent
    cx = page.get_contents()[0]
    old = doc.xref_stream(cx).decode()
    name = old.split("/")[1].split()[0]
    doc.update_stream(cx, f"q {' '.join(f'{v:.6f}' for v in matrix)} cm /{name} Do Q".encode())


def test_g4_round_trip_is_bit_exact():
    doc = pymupdf.open()
    a = marked()
    page, x = g4_page(doc, a)
    assert doc.xref_get_key(x, "Filter")[1] == "/CCITTFaxDecode"
    src, why = images.examine(page)
    assert why == [] and src.kind == images.G4
    up = images.upright_pixels(page, src)
    assert np.array_equal(up.pixels < 128, a)
    assert up.sx * 72 == pytest.approx(200)


@pytest.mark.parametrize("filt", ["/CCITTFaxDecode", "[/CCITTFaxDecode]"])
def test_any_one_bit_filter_spelling_is_g4(filt):
    doc = pymupdf.open()
    page, x = g4_page(doc, marked())
    doc.xref_set_key(x, "Filter", filt)
    src, _ = images.examine(page)
    assert src.kind == images.G4 and src.filters == ["/CCITTFaxDecode"]


@pytest.mark.parametrize("k, rot", [(0, 90), (1, 0), (2, 270), (3, 180)])
def test_rotated_placement_comes_out_upright(k, rot):
    """Stored pixels rotated by k*90, placed with rotate=rot on a /Rotate 90 page: the
    display is upright in every case, and so must be upright_pixels."""
    up = np.where(marked(), 0, 255).astype(np.uint8)
    doc = pymupdf.open()
    page = doc.new_page(width=300 * 72 / 200, height=500 * 72 / 200)
    page.set_rotation(90)
    st = Image.fromarray(np.ascontiguousarray(np.rot90(up, k)) > 127)
    page.insert_image(page.cropbox, xref=images.g4_xobject(doc, st), rotate=rot,
                      keep_proportion=False)
    src, why = images.examine(page)
    assert why == []
    assert np.array_equal(images.upright_pixels(page, src).pixels, up)


def test_crop_box_crops_and_short_image_leaves_page_background():
    doc = pymupdf.open()
    a = marked()
    # page 500x300 px at 200 dpi = 180x108 pt; the image starts 1.8 pt (5 px) right of the
    # page's left edge and overhangs its right edge by as much (99% coverage).
    page, _ = g4_page(doc, a, rect=pymupdf.Rect(1.8, 0, 181.8, 108), page_size=(180, 108))
    src, why = images.examine(page)
    assert why == []
    up = images.upright_pixels(page, src)
    assert np.array_equal(up.pixels < 128, a)                 # stored pixels, not padded
    left, right, whitened = images.split_halves(page, src, (0.5, 0.5))
    assert whitened == 0 and (left.width, right.width) == (90, 90)
    assert left.origin == (0, 0) and np.array_equal(left.pixels < 128, a[:, :245])
    assert right.origin == (245, 0) and np.array_equal(right.pixels < 128, a[:, 245:495])
    out = pymupdf.open()
    for half in (left, right):
        images.write_half(out.new_page(width=half.width, height=half.height), half, src)
    shown = [np.frombuffer((p := pg.get_pixmap(dpi=200, colorspace=pymupdf.csGRAY)).samples,
                           np.uint8).reshape(p.height, p.stride)[:, : p.width] for pg in out]
    assert (shown[0][:, :5] == 255).all()                     # page background, not the image
    assert (np.array_equal(shown[0][:, 5:] < 128, a[:, :245])
            and np.array_equal(shown[1] < 128, a[:, 245:495]))


def test_small_image_is_refused_for_coverage():
    doc = pymupdf.open()
    page, _ = g4_page(doc, marked(), rect=pymupdf.Rect(0, 0, 150, 108), page_size=(180, 108))
    src, why = images.examine(page)
    assert src is None and "covers 83%" in why[0]


def test_unsupported_images_are_refused_with_reasons():
    doc = pymupdf.open()
    cases = {}
    # two images
    page, _ = g4_page(doc, marked())
    page.insert_image(pymupdf.Rect(0, 0, 10, 10), xref=images.g4_xobject(
        doc, Image.fromarray(~marked(20, 20))))
    cases["2 images"] = page.number
    # non-trivial Decode
    page, x = g4_page(doc, marked())
    doc.xref_set_key(x, "Decode", "[1 0]")
    cases["Decode"] = page.number
    # image mask
    page, x = g4_page(doc, marked())
    doc.xref_set_key(x, "ImageMask", "true")
    cases["image mask"] = page.number
    # 16 bits per component
    page = doc.new_page(width=72, height=72)
    x = images.image_xobject(doc, 10, 10, 1, 16, bytes(200))
    page.insert_image(page.rect, xref=x)
    cases["16 bits"] = page.number
    # SMask (alpha) and an ICCBased colour space (PyMuPDF's default for pixmaps)
    page = doc.new_page(width=72, height=72)
    page.insert_image(page.rect, pixmap=pymupdf.Pixmap(pymupdf.csRGB, 10, 10, bytes(400), True))
    cases["SMask"] = page.number
    page = doc.new_page(width=72, height=72)
    page.insert_image(page.rect, pixmap=pymupdf.Pixmap(pymupdf.csRGB, 10, 10, bytes(300), False))
    cases["ICCBased"] = page.number
    # residual image rotation of 2 degrees
    page, _ = g4_page(doc, marked())
    r, t = page.rect, math.radians(2)
    set_cm(page, (r.width * math.cos(t), r.width * math.sin(t), -r.height * math.sin(t),
                  r.height * math.cos(t), 0, 0))
    cases["rotated 2.0 deg"] = page.number
    for needle, pno in cases.items():
        src, why = images.examine(doc[pno])
        assert src is None, needle
        assert any(needle in w for w in why), (needle, why)


def test_halves_whiten_the_wedge_and_count_changed_pixels():
    px = np.zeros((4, 10), np.uint8)          # all black
    b = images.cut_boundary(4, 10, 4, 7)      # rows: 4, 5, 6, 7
    left, right, whitened = images.halves(px, b)
    assert left.shape == (4, 7) and right.shape == (4, 6)
    assert [(row == 255).sum() for row in left] == [3, 2, 1, 0]
    assert [(row == 255).sum() for row in right] == [0, 1, 2, 3]
    assert whitened == 12
    px[:] = 255
    assert images.halves(px, b)[2] == 0       # already-white pixels are not counted


def test_jpeg_half_keeps_source_tables_and_flate_half_is_lossless():
    rng = np.random.default_rng(3)
    rgb = (rng.random((64, 96, 3)) * 255).astype(np.uint8)
    b = io.BytesIO()
    Image.fromarray(rgb).save(b, "JPEG", quality=60)
    src_tables = Image.open(io.BytesIO(b.getvalue())).quantization
    doc = pymupdf.open()
    page = doc.new_page(width=96, height=64)
    src = images.SourceImage(0, images.JPEG, 3, ["/DCTDecode"], b.getvalue())
    images.write_half(page, images.Half(rgb[:, :40], (0, 0), 40, 64,
                                        pymupdf.Matrix(40, 0, 0, 64, 0, 0)), src)
    x = page.get_images()[0][0]
    assert doc.xref_get_key(x, "Filter")[1] == "/DCTDecode"
    assert doc.xref_get_key(x, "ColorSpace")[1] == "/DeviceRGB"
    assert Image.open(io.BytesIO(doc.xref_stream_raw(x))).quantization == src_tables

    grad = np.broadcast_to(np.arange(40, dtype=np.uint8)[None, :, None] * 6, (64, 40, 3))
    page = doc.new_page(width=96, height=64)
    images.write_half(page, images.Half(grad, (0, 0), 40, 64, pymupdf.Matrix(40, 0, 0, 64, 0, 0)),
                      images.SourceImage(0, images.FLATE, 3, []))
    x = page.get_images()[0][0]
    assert doc.xref_get_key(x, "Filter")[1] == "/FlateDecode"
    assert doc.xref_get_key(x, "ColorSpace")[1] == "/DeviceRGB"
    pix = pymupdf.Pixmap(doc, x)
    assert np.array_equal(np.frombuffer(pix.samples, np.uint8).reshape(64, 40, 3), grad)
