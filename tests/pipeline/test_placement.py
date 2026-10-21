# SPDX-License-Identifier: AGPL-3.0-or-later
"""Halves keep the source's placement: orientation, residual (deskew) rotation, offsets."""

import pytest

np = pytest.importorskip("numpy")
Image = pytest.importorskip("PIL.Image")
import pymupdf  # noqa: E402

from gpdf.pipeline import images  # noqa: E402

FLIP = pymupdf.Matrix(1, 0, 0, -1, 0, 1)       # PDF image space has row 0 at v = 1
ROT90_UNIT = pymupdf.Matrix(0, 1, -1, 0, 1, 0)  # stored unit -> upright unit for np.rot90(u, 1)


def render(page, dpi=50):
    pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
    return np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.stride)[:, : pix.width]


def rotated_page(doc, width, height, rotation, margin=20):
    """A page displaying width x height points, with a crop box inside a larger media box."""
    w, h = (height, width) if rotation % 180 else (width, height)
    page = doc.new_page(width=w + 2 * margin, height=h + 2 * margin)
    page.set_cropbox(pymupdf.Rect(margin, margin, margin + w, margin + h))
    page.set_rotation(rotation)
    assert page.rect == pymupdf.Rect(0, 0, width, height)
    return page


def place(page, xref, display, k=0):
    """Draw image ``xref`` so that its unit square maps to ``display`` points (row 0 at the
    top, y down) as if stored upright, when it is stored as np.rot90(upright, k). The
    placement is checked against what PyMuPDF reports back."""
    stored = pymupdf.Matrix(1, 0, 0, 1, 0, 0)
    for _ in range(k % 4):
        stored = stored * ROT90_UNIT
    M = stored * display
    cb, mb = page.cropbox, page.mediabox
    to_pdf = pymupdf.Matrix(1, 0, 0, -1, cb.x0, mb.height - cb.y0)   # unrotated page -> PDF
    cm = FLIP * M * ~page.rotation_matrix * to_pdf
    doc = page.parent
    doc.xref_set_key(page.xref, "Resources", f"<< /XObject << /Im0 {xref} 0 R >> >>")
    c = doc.get_new_xref()
    doc.update_object(c, "<<>>")
    doc.update_stream(c, f"q {' '.join(f'{v:.6f}' for v in cm)} cm /Im0 Do Q".encode())
    doc.xref_set_key(page.xref, "Contents", f"{c} 0 R")
    (info,) = page.get_image_info(xrefs=True)
    got = pymupdf.Matrix(info["transform"]) * page.rotation_matrix
    assert all(abs(u - v) < 1e-3 for u, v in zip(got, M)), (got, M)


def blobs(shown, x_split):
    """Bounding boxes (x0, y0, x1, y1) of the dark pixels left and right of column x_split
    (None where there are none)."""
    out = []
    for part, dx in ((shown[:, :x_split], 0), (shown[:, x_split:], x_split)):
        ys, xs = np.nonzero(part < 128)
        out.append((xs.min() + dx, ys.min(), xs.max() + dx, ys.max()) if len(xs) else None)
    return out


@pytest.mark.parametrize("rotation, k", [(0, 0), (0, 1), (90, 2), (90, 1), (270, 3), (270, 0)])
def test_asymmetric_marker_lands_where_it_was(rotation, k):
    """A square near the top-left of the left page and a bar near the bottom-right of the
    right page render in the same place on the halves as on the source page."""
    up = np.zeros((400, 600), bool)                  # 100 dpi: 432 x 288 pt
    up[40:80, 40:80] = True                          # square, left page
    up[340:360, 440:560] = True                      # bar, right page
    doc = pymupdf.open()
    page = rotated_page(doc, 432, 288, rotation)
    x = images.g4_xobject(doc, Image.fromarray(np.ascontiguousarray(~np.rot90(up, k))))
    place(page, x, pymupdf.Matrix(432, 0, 0, 288, 0, 0), k)
    shown = render(page, 100)
    assert blobs(shown, 300) == [(40, 40, 79, 79), (440, 340, 559, 359)]   # source upright

    src, why = images.examine(page)
    assert why == []
    left, right, whitened = images.split_halves(page, src, (0.5, 0.5))
    assert whitened == 0
    out = pymupdf.open()
    for half in (left, right):
        p = out.new_page(width=half.width, height=half.height)
        images.write_half(p, half, src)
    assert blobs(render(out[0], 100), 150) == [(40, 40, 79, 79), None]
    assert blobs(render(out[1], 100), 100) == [None, (440 - 300, 340, 559 - 300, 359)]
