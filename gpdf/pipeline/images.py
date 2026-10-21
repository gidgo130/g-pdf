# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pixels of a scanned page, upright, and writers for the two halves of a split spread.

Spec: docs/prototypes/spread_split_proto.py (physical split helpers). A page can be split
only when it is exactly one image covering the page that this module can re-encode
without changing it: 1-bit (any filter) -> CCITT G4; DCT (JPEG) -> JPEG with the
source's quantisation tables; other 8-bit grey/RGB -> lossless Flate.
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
    """Page pixels re-oriented to the displayed page, covering exactly page.rect."""

    pixels: np.ndarray         # (H, W) grey or (H, W, 3) RGB, uint8
    sx: float                  # pixels per point, horizontally
    sy: float                  # pixels per point, vertically


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
    """The page image re-oriented (transpose/flip) to the displayed page, cropped to
    page.rect and padded with white where the image falls short of it. Residual image
    rotation (<= MAX_IMAGE_ROT_DEG) is ignored."""
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
    if abs(ex.x) >= abs(ex.y):
        if ex.x < 0:
            a = a[:, ::-1]
        if ey.y < 0:
            a = a[::-1]
    else:
        a = a.transpose(1, 0, 2)
        if ex.y < 0:
            a = a[::-1]
        if ey.x < 0:
            a = a[:, ::-1]
    disp = pymupdf.Rect(pymupdf.Point(0, 0) * M, pymupdf.Point(1, 1) * M).normalize()
    sx, sy = a.shape[1] / disp.width, a.shape[0] / disp.height
    r = page.rect
    W, H = round(r.width * sx), round(r.height * sy)
    ox, oy = round((disp.x0 - r.x0) * sx), round((disp.y0 - r.y0) * sy)
    out = np.full((H, W, a.shape[2]), WHITE, np.uint8)
    x0, y0 = max(0, ox), max(0, oy)
    x1, y1 = min(W, ox + a.shape[1]), min(H, oy + a.shape[0])
    if x1 > x0 and y1 > y0:
        out[y0:y1, x0:x1] = a[y0 - oy: y1 - oy, x0 - ox: x1 - ox]
    pixels = out[:, :, 0] if out.shape[2] == 1 else out
    return Upright(pixels, W / r.width, H / r.height)


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


def write_half(page, pixels: np.ndarray, src: SourceImage) -> None:
    """Fill a new page (sized to the half) with the half's pixels, encoded as for src."""
    from PIL import Image

    doc = page.parent
    pixels = np.ascontiguousarray(pixels)
    h, w = pixels.shape[:2]
    if src.kind == G4:
        x = g4_xobject(doc, Image.fromarray(pixels > 127))
    elif src.kind == JPEG:
        x = image_xobject(doc, w, h, src.n, 8, jpeg_half(src.jpeg, Image.fromarray(pixels)),
                          "/DCTDecode")
    else:
        x = image_xobject(doc, w, h, src.n, 8, pixels.tobytes())
    page.insert_image(page.rect, xref=x, keep_proportion=False)
