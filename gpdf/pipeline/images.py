# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pixels of a scanned page, upright, and writers for the two halves of a split spread.

Spec: docs/prototypes/spread_split_proto.py (physical split helpers). A page can be split
only when it is exactly one image covering the page that this module can re-encode
without changing it: 1-bit (any filter) -> CCITT G4; DCT (JPEG) -> JPEG with the
source's quantisation tables; other 8-bit grey/RGB -> lossless Flate.

Each half keeps the source's own pixels (after a lossless 90-degree transpose/flip) and
is drawn with the source's placement, shifted to the half page: a residual (deskew)
rotation is kept, not resampled, and where the image falls short of the page the page
background shows, as on the source.
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass

import numpy as np
import pymupdf

FULL_COVER = 0.98          # the image must cover at least this fraction of the page
MAX_IMAGE_ROT_DEG = 0.5    # residual rotation of the image matrix we ignore
WHITE = 255

G4, JPEG, FLATE = "g4", "jpeg", "flate"


@dataclass
class SourceImage:
    """The one image of a splittable page."""

    xref: int
    kind: str                  # G4, JPEG or FLATE: how the halves are written
    n: int                     # colour components (1 grey, 3 RGB)
    filters: list[str]
    jpeg: bytes | None = None  # the source JPEG (kind JPEG), for its tables


@dataclass
class Upright:
    """The page image re-oriented to the displayed page (no resampling)."""

    pixels: np.ndarray         # (H, W) grey or (H, W, 3) RGB, uint8
    matrix: pymupdf.Matrix     # upright pixel coordinates -> display points of page.rect
    sx: float                  # pixels per point, horizontally
    sy: float                  # pixels per point, vertically


@dataclass
class Half:
    """One page of a split spread: a crop of the upright pixels and where to draw it."""

    pixels: np.ndarray         # may be empty if the half page holds no image
    origin: tuple[int, int]    # (column, row) of pixels[0, 0] in Upright.pixels
    width: float               # half page size, points
    height: float
    matrix: pymupdf.Matrix     # image unit square (row 0 at the top) -> half page points


def _image_matrix(page, info) -> pymupdf.Matrix:
    # get_image_info's transform is in unrotated page space relative to the crop box;
    # the page rotation matrix takes it to display coordinates of page.rect. Do NOT
    # subtract the crop box again.
    return pymupdf.Matrix(info["transform"]) * page.rotation_matrix


def _axes(M):
    o = pymupdf.Point(0, 0) * M
    return pymupdf.Point(1, 0) * M - o, pymupdf.Point(0, 1) * M - o


def _residual_rotation_deg(M) -> float:
    ex, _ = _axes(M)
    a = math.degrees(math.atan2(ex.y, ex.x)) % 90
    return min(a, 90 - a)


def _resolve(doc, xref, key):
    """(type, value) of a dictionary key, following one indirect reference."""
    t, v = doc.xref_get_key(xref, key)
    if t != "xref":
        return t, v
    v = doc.xref_object(int(v.split()[0]), compressed=True).strip()
    if v.startswith("/"):
        return "name", v
    if v.startswith("["):
        return "array", v
    if v.lstrip("-").isdigit():
        return "int", v
    return "other", v


def _filters(doc, xref) -> list[str]:
    t, v = _resolve(doc, xref, "Filter")
    if t == "null":
        return []
    return [f for f in v.strip("[]").replace("/", " /").split() if f.startswith("/")]


def _trivial_decode(value: str, n: int) -> bool:
    try:
        nums = [float(x) for x in value.strip("[]").split()]
    except ValueError:
        return False
    return nums == [0.0, 1.0] * n


def examine(page) -> tuple[SourceImage | None, list[str]]:
    """The page's single full-page image if this module can split it, else (None, reasons)."""
    doc = page.parent
    infos = page.get_image_info(xrefs=True)
    if len(infos) != 1:
        return None, [f"cannot split: {len(infos)} images on the page (need exactly one)"]
    info = infos[0]
    x = info["xref"]
    if not x:
        return None, ["cannot split: inline image"]
    M = _image_matrix(page, info)
    reasons = []
    disp = pymupdf.Rect(pymupdf.Point(0, 0) * M, pymupdf.Point(1, 1) * M).normalize()
    area = page.rect.get_area()
    cover = (disp & page.rect).get_area() / area if area else 0
    if cover < FULL_COVER:
        reasons.append(f"cannot split: image covers {cover:.0%} of the page")
    rot = _residual_rotation_deg(M)
    if rot > MAX_IMAGE_ROT_DEG:
        reasons.append(f"cannot split: image rotated {rot:.1f} deg")
    if doc.xref_get_key(x, "ImageMask")[1] == "true":
        reasons.append("cannot split: image mask")
    if doc.xref_get_key(x, "SMask")[0] != "null" or doc.xref_get_key(x, "Mask")[0] != "null":
        reasons.append("cannot split: image has transparency (SMask/Mask)")
    bpc_t, bpc = _resolve(doc, x, "BitsPerComponent")
    bpc = int(bpc) if bpc_t == "int" else 0
    cs = _resolve(doc, x, "ColorSpace")[1]
    n = {"/DeviceGray": 1, "/DeviceRGB": 3}.get(cs)
    if bpc not in (1, 8):
        reasons.append(f"cannot split: {bpc} bits per component")
    if n is None or (bpc == 1 and n != 1):
        reasons.append(f"cannot split: colour space {cs} not supported")
    dt, dv = _resolve(doc, x, "Decode")
    if dt != "null" and not _trivial_decode(dv, n or 1):
        reasons.append(f"cannot split: non-trivial Decode array {dv}")
    if reasons:
        return None, reasons
    filters = _filters(doc, x)
    if bpc == 1:
        return SourceImage(x, G4, 1, filters), []
    if filters == ["/DCTDecode"]:
        ext = doc.extract_image(x)
        if ext.get("ext") in ("jpg", "jpeg"):
            return SourceImage(x, JPEG, n, filters, ext["image"]), []
    return SourceImage(x, FLATE, n, filters), []


def upright_pixels(page, src: SourceImage) -> Upright:
    """The page image re-oriented (transpose/flip) to the displayed page, and the matrix
    placing those pixels on the page exactly as the source does (residual rotation,
    offset and overhang included)."""
    info = next(i for i in page.get_image_info(xrefs=True) if i["xref"] == src.xref)
    M = _image_matrix(page, info)
    ex, ey = _axes(M)
    pix = pymupdf.Pixmap(page.parent, src.xref)
    if pix.alpha:
        pix = pymupdf.Pixmap(pix, 0)
    if pix.n != src.n:
        raise ValueError(f"image {src.xref} decodes to {pix.n} components, expected {src.n}")
    a = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.stride)[:, : pix.width * pix.n]
    a = a.reshape(pix.height, pix.width, pix.n)
    # U maps coordinates in the re-oriented array back to stored pixel coordinates.
    U = pymupdf.Matrix(1, 0, 0, 1, 0, 0)

    def flip_cols(a):
        nonlocal U
        U = pymupdf.Matrix(-1, 0, 0, 1, a.shape[1], 0) * U
        return a[:, ::-1]

    def flip_rows(a):
        nonlocal U
        U = pymupdf.Matrix(1, 0, 0, -1, 0, a.shape[0]) * U
        return a[::-1]

    if abs(ex.x) >= abs(ex.y):
        if ex.x < 0:
            a = flip_cols(a)
        if ey.y < 0:
            a = flip_rows(a)
    else:
        a = a.transpose(1, 0, 2)
        U = pymupdf.Matrix(0, 1, 1, 0, 0, 0) * U
        if ex.y < 0:
            a = flip_rows(a)
        if ey.x < 0:
            a = flip_cols(a)
    A = U * pymupdf.Matrix(1 / pix.width, 0, 0, 1 / pix.height, 0, 0) * M
    pixels = a[:, :, 0] if a.shape[2] == 1 else a
    return Upright(pixels, A, 1 / math.hypot(A.a, A.b), 1 / math.hypot(A.c, A.d))


def split_halves(page, src: SourceImage, cut) -> tuple[Half, Half, int]:
    """(left, right, whitened) for a cut from (cut[0], top) to (cut[1], bottom), as
    fractions of the displayed width. The left page is display x in [0, max(cut)], the
    right one [min(cut), width], both the source's height. The cut is solved in pixel
    space through the image placement; each half stores only the pixels inside its page
    (no off-page overhang), with the other page's pixels beyond the cut whitened.
    ``whitened`` counts the stored pixels that were whitened and not already white."""
    up = upright_pixels(page, src)
    a, A = up.pixels, up.matrix
    H, W = a.shape[:2]
    r = page.rect
    xt, xb = cut[0] * r.width, cut[1] * r.width
    inv = ~A
    pt, pb = pymupdf.Point(r.x0 + xt, r.y0) * inv, pymupdf.Point(r.x0 + xb, r.y1) * inv

    def column(y):                           # the cut line in pixel space, at row y
        return pt.x + (pb.x - pt.x) * (y - pt.y) / (pb.y - pt.y)

    b = cut_boundary(H, W, column(0.5), column(H - 0.5))     # at the row centres
    left, right, _ = halves(a, b)
    out, whitened = [], 0
    for half, off, x0, x1 in ((left, 0, 0.0, max(xt, xb)),
                              (right, int(b.min()), min(xt, xb), r.width)):
        box = pymupdf.Rect(r.x0 + x0, r.y0, r.x0 + x1, r.y1) * inv
        c0, r0 = max(off, math.floor(box.x0)), max(0, math.floor(box.y0))
        c1, r1 = min(off + half.shape[1], math.ceil(box.x1)), min(H, math.ceil(box.y1))
        c1, r1 = max(c0, c1), max(r0, r1)
        px = np.ascontiguousarray(half[r0:r1, c0 - off: c1 - off])
        changed = px != a[r0:r1, c0:c1]
        whitened += int((changed if changed.ndim == 2 else changed.any(-1)).sum())
        m = (pymupdf.Matrix(c1 - c0, 0, 0, r1 - r0, c0, r0) * A
             * pymupdf.Matrix(1, 0, 0, 1, -(r.x0 + x0), -r.y0))
        out.append(Half(px, (c0, r0), x1 - x0, r.height, m))
    return out[0], out[1], whitened


def cut_boundary(H: int, W: int, x_top_px: float, x_bottom_px: float) -> np.ndarray:
    """Per row, the first pixel column of the right page (a straight, possibly slanted cut)."""
    ys = np.arange(H)
    X = x_top_px + (x_bottom_px - x_top_px) * ys / max(H - 1, 1)
    return np.clip(np.round(X).astype(int), 1, W - 1)


def halves(pixels: np.ndarray, boundary: np.ndarray):
    """(left, right, whitened): rectangular crops around a slanted cut. Pixels of the other
    page inside the wedge beyond the cut line are whitened; ``whitened`` counts the pixels
    that were not already white."""
    lo, hi = int(boundary.min()), int(boundary.max())
    cols = np.arange(pixels.shape[1])[None, :]
    b = boundary[:, None]
    left, right = pixels[:, :hi].copy(), pixels[:, lo:].copy()
    lmask = cols[:, :hi] >= b                   # right-page pixels inside the left crop
    rmask = cols[:, lo:] < b                    # left-page pixels inside the right crop
    whitened = 0
    for half, mask in ((left, lmask), (right, rmask)):
        notwhite = half < WHITE if half.ndim == 2 else (half < WHITE).any(-1)
        whitened += int((notwhite & mask).sum())
        half[mask] = WHITE
    return left, right, whitened


def image_xobject(doc, w, h, n, bpc, data, filt=None, parms=None) -> int:
    """New image XObject in a Device colour space. ``data`` is already encoded with
    ``filt`` (/CCITTFaxDecode, /DCTDecode), or raw samples Flate-compressed here when
    ``filt`` is None. (insert_image would write an ICCBased colour space instead.)
    GOTCHA: doc.update_stream(..., compress=False) REMOVES /Filter, so set it after."""
    cs = "/DeviceGray" if n == 1 else "/DeviceRGB"
    x = doc.get_new_xref()
    doc.update_object(x, f"<< /Type /XObject /Subtype /Image /Width {w} /Height {h} "
                         f"/ColorSpace {cs} /BitsPerComponent {bpc} >>")
    if filt is None:
        doc.update_stream(x, data, compress=True)
        return x
    doc.update_stream(x, data, compress=False)
    doc.xref_set_key(x, "Filter", filt)
    if parms:
        doc.xref_set_key(x, "DecodeParms", parms)
    return x


def g4_xobject(doc, im1) -> int:
    """Raw CCITT G4 image object from a PIL mode-'1' image (one strip).
    GOTCHA: page.insert_image(stream=<G4 TIFF>) silently decodes to raw (60x larger)."""
    from PIL import Image, TiffImagePlugin

    old = TiffImagePlugin.STRIP_SIZE
    TiffImagePlugin.STRIP_SIZE = 1 << 30
    try:
        b = io.BytesIO()
        im1.save(b, "TIFF", compression="group4")
    finally:
        TiffImagePlugin.STRIP_SIZE = old
    t = Image.open(io.BytesIO(b.getvalue()))
    (off,), (cnt,) = t.tag_v2[273], t.tag_v2[279]
    data = b.getvalue()[off: off + cnt]
    w, h = im1.size
    blackis1 = "true" if t.tag_v2.get(262, 0) == 1 else "false"
    parms = f"<< /K -1 /Columns {w} /Rows {h} /BlackIs1 {blackis1} >>"
    return image_xobject(doc, w, h, 1, 1, data, "/CCITTFaxDecode", parms)


def jpeg_half(src_jpeg: bytes, half_pil) -> bytes:
    """Re-encode a JPEG half with the SOURCE quantisation tables and sampling (about 1.01x
    the source size, mean error ~3/255; a q90 re-encode is ~1.9x)."""
    from PIL import Image, JpegImagePlugin

    src = Image.open(io.BytesIO(src_jpeg))
    b = io.BytesIO()
    half_pil.save(b, "JPEG", qtables=src.quantization,
                  subsampling=JpegImagePlugin.get_sampling(src))
    return b.getvalue()


def draw_image(page, xref, matrix) -> None:
    """Make ``xref`` the page's only content, drawn through ``matrix`` (image unit square,
    row 0 at the top -> page points, y down). PDF image space has row 0 at v = 1 and PDF
    user space is y-up, hence the flip and the inverse transformation matrix."""
    doc = page.parent
    cm = pymupdf.Matrix(1, 0, 0, -1, 0, 1) * matrix * ~page.transformation_matrix
    doc.xref_set_key(page.xref, "Resources", f"<< /XObject << /Im0 {xref} 0 R >> >>")
    c = doc.get_new_xref()
    doc.update_object(c, "<<>>")
    doc.update_stream(c, f"q {' '.join(f'{v:.6f}' for v in cm)} cm /Im0 Do Q".encode())
    doc.xref_set_key(page.xref, "Contents", f"{c} 0 R")


def write_half(page, half: Half, src: SourceImage) -> None:
    """Fill a new page (sized to the half) with the half's pixels, encoded as for src and
    placed as on the source page. A half without pixels stays blank."""
    from PIL import Image

    pixels = half.pixels
    if pixels.size == 0:
        return
    doc = page.parent
    h, w = pixels.shape[:2]
    if src.kind == G4:
        x = g4_xobject(doc, Image.fromarray(pixels > 127))
    elif src.kind == JPEG:
        x = image_xobject(doc, w, h, src.n, 8, jpeg_half(src.jpeg, Image.fromarray(pixels)),
                          "/DCTDecode")
    else:
        x = image_xobject(doc, w, h, src.n, 8, pixels.tobytes())
    draw_image(page, x, half.matrix)
