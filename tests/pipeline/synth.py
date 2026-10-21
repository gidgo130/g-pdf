# SPDX-License-Identifier: AGPL-3.0-or-later
"""Synthetic book scans for the pipeline tests, generated at test time.

Spanish text (accents, footnotes, running heads) is typeset with a base-14 font,
rasterised at DPI grey, and composed with numpy into spreads and single pages that mimic
the real corpus: 1-bit scans (CCITT G4 or Flate bpc 1) with solid or dithered binding
shadows, a 96 dpi RGB JPEG with a soft shadow, an Unruh-like rotated page with a crop box,
and pages the splitter must refuse. ``build_book`` writes the PDF and returns the ground
truth: per page, the band of columns (in DPI pixels of the unrotated composition) that
holds no text, and the affine map from display pixels back into the composition.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from functools import lru_cache

import numpy as np
import pymupdf
from PIL import Image

from gpdf.pipeline import images

DPI = 200
MM = DPI / 25.4
W, H = 1800, 1300                 # spread, pixels (9 x 6.5 in)
PW = W // 2                       # one page
BLOCK_W, BLOCK_H, TOP = 640, 1080, 110
PT = 72 / DPI                     # points per pixel

SENTENCES = [
    "La vanguardia latinoamericana nació en un período de intensas transformaciones sociales.",
    "¿Qué significaba ser moderno en una ciudad periférica, lejos de París y de Berlín?",
    "¡Abajo el academicismo!, proclamaban los jóvenes poetas reunidos en el café.",
    "El ultraísmo, el estridentismo y la antropofagia compartían una misma inquietud.",
    "Según la crítica, la ruptura fue también una búsqueda de identidad continental.",
    "Vicente Huidobro defendió el creacionismo con argumentos casi científicos.",
    "La revista Martín Fierro reunió a pintores, críticos y narradores de su generación.",
    "En Lima, José Carlos Mariátegui fundó Amauta en 1926 y abrió sus páginas al debate.",
    "La pequeña imprenta del barrio publicó cientos de folletos efímeros y manifiestos.",
    "Ningún lector pudo ignorar la vehemencia de aquellas páginas, escritas con urgencia.",
    "El pingüino, la cigüeña y el ñandú aparecen en los poemas más lúdicos del período.",
    "Oswald de Andrade publicó el Manifiesto antropófago en São Paulo en 1928.",
]
FOOTNOTES = [
    "¹ Véase el capítulo III, donde se discuten las polémicas de la época.",
    "² Cf. P. Bürger, Teoría de la vanguardia, trad. de J. García, p. 45.",
]


def _paragraphs(seed: int, n: int = 40) -> str:
    rng = np.random.default_rng(seed)
    words = [SENTENCES[i] for i in rng.integers(0, len(SENTENCES), n)]
    paras = [" ".join(words[i: i + 5]) for i in range(0, n, 5)]
    return "\n".join("    " + p for p in paras)


def _render(page) -> np.ndarray:
    pix = page.get_pixmap(matrix=pymupdf.Matrix(DPI / 72, DPI / 72), colorspace=pymupdf.csGRAY)
    g = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.stride)[:, : pix.width]
    return 1 - g.astype(np.float32) / 255


@lru_cache(maxsize=None)
def text_block(seed: int, columns: int = 1, w: int = BLOCK_W, h: int = BLOCK_H) -> np.ndarray:
    """Darkness (0 paper .. 1 ink) of a typeset text block: running head, justified body,
    footnote rule and footnotes."""
    doc = pymupdf.open()
    wp, hp = w * PT, h * PT
    page = doc.new_page(width=wp, height=hp)
    page.insert_text((0, 8), "LAS VANGUARDIAS LATINOAMERICANAS", fontname="tiit", fontsize=7.5)
    page.insert_text((wp - 14, 8), str(10 + seed), fontname="tiro", fontsize=7.5)
    gutter = 14
    cw = (wp - gutter * (columns - 1)) / columns
    for c in range(columns):
        x0 = c * (cw + gutter)
        body = pymupdf.Rect(x0, 18, x0 + cw, hp - 56)
        n = 40                       # insert_textbox writes nothing if the text overflows
        while page.insert_textbox(body, _paragraphs(seed * 7 + c, n), fontname="tiro",
                                  fontsize=10, align=pymupdf.TEXT_ALIGN_JUSTIFY) < 0:
            n -= 1
    page.draw_line((0, hp - 50), (wp / 3, hp - 50), width=0.5)
    page.insert_textbox(pymupdf.Rect(0, hp - 46, wp, hp), "\n".join(FOOTNOTES),
                        fontname="tiro", fontsize=7, align=pymupdf.TEXT_ALIGN_JUSTIFY)
    d = _render(page)
    out = np.zeros((h, w), np.float32)
    out[: min(h, d.shape[0]), : min(w, d.shape[1])] = d[:h, :w]
    return out


def _paste(canvas, block, x0, y0):
    """Darker-wins paste, clipped to the canvas."""
    h, w = block.shape
    cx0, cy0 = max(0, x0), max(0, y0)
    cx1, cy1 = min(canvas.shape[1], x0 + w), min(canvas.shape[0], y0 + h)
    if cx1 > cx0 and cy1 > cy0:
        region = canvas[cy0:cy1, cx0:cx1]
        np.maximum(region, block[cy0 - y0: cy1 - y0, cx0 - x0: cx1 - x0], out=region)


def _ink_cols(dark, x0, x1):
    cols = np.where((dark[:, x0:x1] > 0.5).any(0))[0]
    return (x0 + cols[0], x0 + cols[-1]) if len(cols) else None


@dataclass
class Truth:
    name: str
    spread: bool                       # a two-page spread the splitter should cut
    gap: tuple[float, float] | None    # text-free columns (composition pixels)
    flagged: bool | None = False       # expected detector verdict; None = either
    affine: tuple | None = None        # display px -> composition px (a, b, c, d, e, f)
    encoding: str = "g4"
    notes: dict = field(default_factory=dict)


def text_spread(seeds=(1, 2), fold=0.5, gap_mm=30.0, left=True, right=True):
    """Darkness of a two-page text spread and its text-free band (gl, gr)."""
    dark = np.zeros((H, W), np.float32)
    fx = fold * W
    lx1, rx0 = round(fx - gap_mm * MM / 2), round(fx + gap_mm * MM / 2)
    if left:
        _paste(dark, text_block(seeds[0]), lx1 - BLOCK_W, TOP)
    if right:
        _paste(dark, text_block(seeds[1]), rx0, TOP)
    lc = _ink_cols(dark, 0, round(fx))
    rc = _ink_cols(dark, round(fx), W)
    return dark, (lc[1] + 1 if lc else 0, rc[0] if rc else W)


def solid_shadow(fx, width_mm=5.0, ramp_mm=1.0):
    x = np.arange(W)
    d = np.clip((width_mm / 2 * MM + ramp_mm * MM - np.abs(x - fx)) / (ramp_mm * MM), 0, 1)
    return np.broadcast_to(d[None, :], (H, W)).astype(np.float32)


def soft_shadow(fx, peak, sigma_mm):
    x = np.arange(W)
    d = peak * np.exp(-0.5 * ((x - fx) / (sigma_mm * MM)) ** 2)
    return np.broadcast_to(d[None, :], (H, W)).astype(np.float32)


def rotate(a, deg):
    """Rotate a darkness map about its centre; returns (rotated, affine display->source)."""
    t = np.radians(deg)
    c, s = np.cos(t), np.sin(t)
    cx, cy = a.shape[1] / 2, a.shape[0] / 2
    # source = R(-t) * (out - centre) + centre, as PIL's affine (out -> source) data
    aff = (c, s, cx - c * cx - s * cy, -s, c, cy + s * cx - c * cy)
    im = Image.fromarray(a.astype(np.float32), "F")
    r = im.transform(im.size, Image.AFFINE, aff, resample=Image.BILINEAR, fillcolor=0)
    return np.asarray(r, np.float32), aff


def to_black(dark, shadow=None, seed=0):
    """1-bit page (True = black): text thresholded, shadow dithered."""
    black = dark > 0.5
    if shadow is not None:
        black |= np.random.default_rng(seed).random(dark.shape) < shadow
    return black


# ---------------------------------------------------------------- encodings
def put_image(page, x, rect=None):
    page.insert_image(rect or page.rect, xref=x, keep_proportion=False)


def g4(doc, black):
    return images.g4_xobject(doc, Image.fromarray(~black))


def flate1(doc, black):
    h, w = black.shape
    x = images.image_xobject(doc, w, h, 1, 1, np.packbits(~black, axis=1).tobytes())
    assert doc.xref_get_key(x, "Filter")[1] == "/FlateDecode"
    return x


def grey8(doc, grey):
    h, w = grey.shape
    return images.image_xobject(doc, w, h, 1, 8, np.ascontiguousarray(grey).tobytes())


def jpeg_rgb(doc, rgb, quality=75):
    b = io.BytesIO()
    Image.fromarray(rgb).save(b, "JPEG", quality=quality)
    h, w = rgb.shape[:2]
    return images.image_xobject(doc, w, h, 3, 8, b.getvalue(), "/DCTDecode")


def new_page(doc, w_px=W, h_px=H):
    return doc.new_page(width=w_px * PT, height=h_px * PT)


# ---------------------------------------------------------------- placement
FLIP = pymupdf.Matrix(1, 0, 0, -1, 0, 1)       # PDF image space has row 0 at v = 1
ROT90_UNIT = pymupdf.Matrix(0, 1, -1, 0, 1, 0)  # stored unit -> upright unit for np.rot90(u, 1)


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


# ---------------------------------------------------------------- pages
def add_one_bit_spread(doc, name, encode=g4, shadow=None, skew=0.0, fold=0.5, gap_mm=30.0,
                       left=True, right=True, seeds=(1, 2), flagged=False, text_layer=False):
    dark, gap = text_spread(seeds, fold, gap_mm, left, right)
    sh = None
    if shadow == "solid":
        sh = solid_shadow(fold * W)
    elif shadow == "dithered":
        sh = soft_shadow(fold * W, 0.75, 4.0)
    aff = None
    if skew:
        dark, aff = rotate(dark, skew)
        if sh is not None:
            sh, _ = rotate(np.ascontiguousarray(sh), skew)
    page = new_page(doc)
    put_image(page, encode(doc, to_black(dark, sh, seed=len(doc))))
    if text_layer:
        for i, line in enumerate(SENTENCES[:6]):
            page.insert_text((60, 60 + 14 * i), line[:40], fontname="tiro", fontsize=9, render_mode=3)
            page.insert_text((360, 60 + 14 * i), line[40:], fontname="tiro", fontsize=9, render_mode=3)
    return Truth(name, True, gap, flagged, aff, "g4" if encode is g4 else "flate1")


def add_cover(doc):
    page = doc.new_page(width=PW * PT, height=H * PT)
    tmp = pymupdf.open()
    t = tmp.new_page(width=PW * PT, height=H * PT)
    t.insert_textbox(pymupdf.Rect(30, 120, PW * PT - 30, 260),
                     "Las vanguardias\nlatinoamericanas\n\nTextos programáticos y críticos",
                     fontname="tibo", fontsize=20, align=pymupdf.TEXT_ALIGN_CENTER)
    dark = _render(t)[:H, :PW]
    yy = np.linspace(0, 1, H)[:, None]
    grey = (230 - 60 * yy * (np.arange(PW)[None, :] % 40 < 20) - 200 * dark).clip(0, 255)
    put_image(page, grey8(doc, grey.astype(np.uint8)))
    return Truth("cover", False, None, False, encoding="grey8")


def add_two_column(doc):
    page = doc.new_page(width=PW * PT, height=H * PT)
    dark = np.zeros((H, PW), np.float32)
    _paste(dark, text_block(5, columns=2, w=PW - 200), 100, TOP)
    put_image(page, g4(doc, to_black(dark)))
    return Truth("two_column", False, None, False)


def add_landscape_picture(doc):
    rng = np.random.default_rng(11)
    low = Image.fromarray((rng.random((13, 18)) * 255).astype(np.uint8)).resize((W, H), Image.BICUBIC)
    grey = np.asarray(low, np.float32) * 0.6 + 60 + rng.normal(0, 18, (H, W))
    page = new_page(doc)
    put_image(page, grey8(doc, grey.clip(0, 255).astype(np.uint8)))
    return Truth("landscape_picture", False, None, True, encoding="grey8")


def add_plate(doc):
    """Thick printed frame line at ~0.43 W plus the real fold shadow at 0.5."""
    dark = np.zeros((H, W), np.float32)
    x0, x1, y0, y1, t = int(0.08 * W), int(0.43 * W), int(0.1 * H), int(0.9 * H), round(2.5 * MM)
    dark[y0:y1, x0:x0 + t] = dark[y0:y1, x1 - t:x1] = 1
    dark[y0:y0 + t, x0:x1] = dark[y1 - t:y1, x0:x1] = 1
    yy, xx = np.mgrid[y0 + 30:y1 - 30, x0 + 30:x1 - 30]
    dark[y0 + 30:y1 - 30, x0 + 30:x1 - 30] = ((xx + yy) % 14 < 3) * 0.9
    rx0 = round(W / 2 + 15 * MM)
    _paste(dark, text_block(6), rx0, TOP)
    page = new_page(doc)
    put_image(page, g4(doc, to_black(dark, soft_shadow(W / 2, 0.45, 3.0), seed=len(doc))))
    return Truth("plate", True, (x1, _ink_cols(dark, W // 2, W)[0]), None)


def add_photo_spread(doc):
    """Bürger-like: 96 dpi RGB JPEG with a soft grey binding shadow."""
    dark, gap = text_spread((3, 4))
    s = soft_shadow(W / 2, 0.4, 7.0)
    paper = np.array([238, 230, 214], np.float32)
    ink = np.array([35, 30, 28], np.float32)
    rgb = paper * (1 - s[..., None]) * (1 - dark[..., None]) + ink * dark[..., None]
    small = Image.fromarray(rgb.clip(0, 255).astype(np.uint8)).resize(
        (round(W * 96 / DPI), round(H * 96 / DPI)), Image.LANCZOS)
    page = new_page(doc)
    put_image(page, jpeg_rgb(doc, np.asarray(small)))
    return Truth("photo_jpeg", True, gap, False, encoding="jpeg")


def add_rotated(doc):
    """Unruh-like: /Rotate 90, image stored upside down and placed with a rotated matrix,
    crop box smaller than the media box, image starting a few points inside the page."""
    dark, gap = text_spread((7, 8))
    black = to_black(dark, soft_shadow(W / 2, 0.75, 4.0), seed=len(doc))
    wd, hd = W * PT, H * PT                                     # display size
    page = doc.new_page(width=hd + 40, height=wd + 40)
    page.set_cropbox(pymupdf.Rect(20, 20, 20 + hd, 20 + wd))
    page.set_rotation(90)
    # insert_image(xref=...) ignores rotate=, so compose the placement matrix ourselves:
    # pixels stored upside down, unit square turned 270 deg, page /Rotate 90.
    x = g4(doc, np.ascontiguousarray(np.rot90(black, 2)))
    put_image(page, x, pymupdf.Rect(20, -20 - 10 * PT, 20 + hd, -20 - 10 * PT + wd))
    cx = page.get_contents()[0]
    content = doc.xref_stream(cx).decode()
    m = re.search(r"(\S+) (\S+) (\S+) (\S+) (\S+) (\S+) cm", content)
    M = pymupdf.Matrix(0, -1, 1, 0, 0, 1) * pymupdf.Matrix(*map(float, m.groups()))
    doc.update_stream(cx, content.replace(m.group(0), " ".join(f"{v:.6f}" for v in M) + " cm").encode())
    # Where did the composition land on the displayed page? Measure from a render.
    shown = _render(page) > 0.5
    best = max(range(-40, 41), key=lambda dx: (shown[:, max(0, dx):W + min(0, dx)]
                                              == black[:, max(0, -dx):W - max(0, dx)]).mean())
    assert (shown[:, max(0, best):W + min(0, best)]
            == black[:, max(0, -best):W - max(0, best)]).mean() > 0.99
    assert best > 0, "image must start inside the page"
    return Truth("rotated", True, gap, False, (1, 0, -best, 0, 1, 0), notes={"dx": best})


def add_two_images(doc):
    """A spread made of two separate images, one of them 8-bit grey: cannot be split (nor
    flattened, which takes 1-bit pieces only)."""
    dark, gap = text_spread((9, 10))
    black = to_black(dark, soft_shadow(W / 2, 0.75, 4.0), seed=len(doc))
    page = new_page(doc)
    put_image(page, g4(doc, np.ascontiguousarray(black[:, :PW])), pymupdf.Rect(0, 0, PW * PT, H * PT))
    put_image(page, grey8(doc, np.where(black[:, PW:], 0, 255).astype(np.uint8)),
              pymupdf.Rect(PW * PT, 0, W * PT, H * PT))
    return Truth("two_images", True, gap, True)


PIECE_DPI = 300
PIECED_LINES = 24                 # visible text lines per page of a pieced spread


def add_pieced_spread(doc, variant=None):
    """Unruh pp. 1-3: the scanned text replaced by visible font text, with leftovers kept
    as 1-bit images at 300 dpi (a gutter-shadow strip in G4, a page-edge strip in Flate
    bpc 1, a 3x3 speck) and a black vector line. ``variant`` spoils it for flattening:
    "rgb" (an RGB piece), "smask" (a piece with an SMask) or "red" (a red line)."""
    page = new_page(doc)
    r = page.rect
    k = PIECE_DPI / 72
    rows = round(r.height * k)
    rng = np.random.default_rng(len(doc))
    shadow = rng.random((rows, 30)) < np.linspace(0.2, 0.9, 30)[None, :]   # dithered ramp
    edge = rng.random((rows, 20)) < 0.6
    speck = np.ones((3, 3), bool)
    gx = r.width / 2 - 15 / k
    pieces = [(g4(doc, shadow), pymupdf.Rect(gx, 0, gx + 30 / k, r.height)),
              (flate1(doc, edge), pymupdf.Rect(0, 0, 20 / k, r.height)),
              (g4(doc, speck), pymupdf.Rect(500, 400, 500 + 3 / k, 400 + 3 / k))]
    if variant == "rgb":
        rgb = np.zeros((3, 3, 3), np.uint8)
        pieces[2] = (images.image_xobject(doc, 3, 3, 3, 8, rgb.tobytes()), pieces[2][1])
    if variant == "smask":
        sm = images.image_xobject(doc, 3, 3, 1, 8, bytes([255] * 9))
        doc.xref_set_key(pieces[2][0], "SMask", f"{sm} 0 R")
    for x, rect in pieces:
        put_image(page, x, rect)
    for i in range(PIECED_LINES):
        line = SENTENCES[i % len(SENTENCES)]
        page.insert_text((40, 50 + 15 * i), line[:44], fontname="tiro", fontsize=9)
        page.insert_text((r.width / 2 + 40, 50 + 15 * i), line[44:88], fontname="tiro", fontsize=9)
    page.draw_line((40, r.height - 40), (r.width / 2 - 40, r.height - 40),
                   color=(1, 0, 0) if variant == "red" else (0, 0, 0), width=1)
    return page


def add_deskewed(doc, name, skew=1.5, encoding="g4", strip=40.0, rotation=90, seeds=(25, 26)):
    """Unruh-like deskewed scan: the stored pixels are tilted by ``skew`` degrees and the
    image matrix turns them back, so the text displays straight. /Rotate ``rotation`` with
    a crop box; the page is ``strip`` points wider than the image on the left (blank there)
    and the tilted image overhangs the other edges a little. ``gap`` is in composition
    pixels; composition pixel (x, y) displays at (strip + x * PT, y * PT)."""
    dark, gap = text_spread(seeds)
    dark[:, :4] = 1                                   # scanner edge at the left
    tilted, aff = rotate(dark, skew)
    shadow, _ = rotate(np.ascontiguousarray(soft_shadow(W / 2, 0.75, 4.0)), skew)
    if encoding == "g4":
        x = g4(doc, to_black(tilted, shadow, seed=len(doc)))
    else:
        paper = np.array([238, 230, 214], np.float32)
        ink = np.array([35, 30, 28], np.float32)
        s = 0.5 * shadow[..., None]
        rgb = paper * (1 - s) * (1 - tilted[..., None]) + ink * tilted[..., None]
        x = jpeg_rgb(doc, rgb.clip(0, 255).astype(np.uint8))
    page = rotated_page(doc, W * PT + strip, H * PT, rotation)
    # stored pixel -> composition pixel is PIL's affine (output -> source) data
    to_comp = pymupdf.Matrix(aff[0], aff[3], aff[1], aff[4], aff[2], aff[5])
    place(page, x, pymupdf.Matrix(W, 0, 0, H, 0, 0) * to_comp
          * pymupdf.Matrix(PT, 0, 0, PT, strip, 0))
    return Truth(name, True, gap, False, encoding=encoding, notes={"strip": strip, "skew": skew})


def build_book(path) -> list[Truth]:
    """The main fixture book: every case once, in a book of centred spreads."""
    doc = pymupdf.open()
    truth = [
        add_cover(doc),
        add_one_bit_spread(doc, "solid", shadow="solid", seeds=(1, 2)),
        add_one_bit_spread(doc, "dithered", shadow="dithered", seeds=(3, 4)),
        add_one_bit_spread(doc, "plain_flate1", encode=flate1, seeds=(5, 6)),
        add_one_bit_spread(doc, "skew_pos", encode=flate1, shadow="dithered", skew=1.5, seeds=(7, 8)),
        add_one_bit_spread(doc, "skew_neg", shadow="dithered", skew=-1.5, seeds=(9, 10)),
        add_one_bit_spread(doc, "off044", fold=0.44, seeds=(11, 12), flagged=True),
        add_one_bit_spread(doc, "off056", encode=flate1, fold=0.56, seeds=(13, 14), flagged=True),
        add_one_bit_spread(doc, "blank_side", right=False, seeds=(15, 16), flagged=True),
        add_one_bit_spread(doc, "narrow4mm", gap_mm=4, seeds=(17, 18)),
        add_two_column(doc),
        add_landscape_picture(doc),
        add_plate(doc),
        add_photo_spread(doc),
        add_rotated(doc),
        add_two_images(doc),
        add_one_bit_spread(doc, "text_layer", shadow="dithered", seeds=(19, 20), text_layer=True),
        add_one_bit_spread(doc, "plain_g4", seeds=(21, 22)),
    ]
    names = [t.name for t in truth]
    p = names.index
    doc.set_toc([
        [1, "Cubierta", p("cover") + 1],
        [1, "Capítulo 1. Orígenes", p("solid") + 1],
        [2, "1.1 Los manifiestos", p("dithered") + 1],
        [1, "Capítulo 2. Polémicas", p("skew_pos") + 1],
        [1, "Lámina", p("plate") + 1],
        [1, "Índice", p("two_column") + 1],
    ])
    doc.set_page_labels([{"startpage": 0, "prefix": "", "style": "r", "firstpagenum": 1}])
    doc.set_metadata({"title": "Las vanguardias latinoamericanas (prueba)",
                      "author": "José Vasconcelos", "subject": "Prueba sintética"})
    sp = doc[p("solid")]                                  # links/annots on a split page
    sp.insert_link({"kind": pymupdf.LINK_GOTO, "page": p("two_column"),
                    "from": pymupdf.Rect(60, 60, 200, 80)})
    sp.add_text_annot((300, 100), "nota del lector")
    doc[p("two_column")].insert_link({"kind": pymupdf.LINK_GOTO, "page": p("dithered"),
                                      "from": pymupdf.Rect(40, 40, 120, 60)})
    doc.save(str(path), garbage=3, deflate=True)
    return truth


def build_mini(path) -> list[Truth]:
    """A short book for CLI and decisions tests (fast to detect)."""
    doc = pymupdf.open()
    truth = [
        add_cover(doc),
        add_one_bit_spread(doc, "solid", shadow="solid", seeds=(1, 2)),
        add_one_bit_spread(doc, "dithered", shadow="dithered", seeds=(3, 4)),
        add_one_bit_spread(doc, "plain", seeds=(5, 6)),
        add_one_bit_spread(doc, "blank_side", right=False, seeds=(15, 16), flagged=True),
        add_one_bit_spread(doc, "very_narrow", gap_mm=0.5, seeds=(23, 24), flagged=True),
        add_landscape_picture(doc),
    ]
    doc.save(str(path), garbage=3, deflate=True)
    return truth
