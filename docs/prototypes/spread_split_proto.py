# SPDX-License-Identifier: AGPL-3.0-or-later
# PROTOTYPE / SPEC ONLY (not imported by gpdf). Spread detection + physical split, written in the
# 2026-10-20/21 design chat. Measurements and rationale: claude.ai Project note BOOK_PREP_MEASUREMENTS.md.
#
# Detection (numpy + PyMuPDF): render at ADPI grey; two gutter cues per page:
#   1. "seam": binding shadow = darkest column band near the middle of a blurred darkness map
#      (works for solid black, dithered and grey shadows); cut follows the darkest column per strip.
#   2. "valley": low text-texture band (vertical light/dark transitions; white space and flat shadows
#      have none); cut through the middle of the gap per strip.
#   Robust straight-line fit through per-strip points (max 3 deg). Score each option by text marks the
#   cut crosses (shadow band masked for the seam option), prefer seam, then confidence factors.
# Calibrated on the private corpus (read-only): Schwartz 388 pp (1-bit CCITT, dithered shadow), Burger
# 97 pp (96 dpi JPEG, soft shadow), Unruh 166 pp (JBIG2, /Rotate 90, crop box). Flagged (< 0.65):
# v3 (after an independent Fable review that found 5 wrong unflagged cuts on Schwartz plates/back
# matter): gutters far from the book median are re-detected near it or flagged; valley slant capped.
# Flagged now: Schwartz 14/388, Burger 9/97, Unruh 5/166. Held-out fake spreads made from real
# single-page scans (Vasconcelos, Historia; 62 spreads, reviewer): 0 wrong unflagged cuts (v2).
# Thresholds below are still first guesses: keep them as named constants.
#
# Physical split (see bottom): 1-bit -> CCITT G4 halves (Pillow); JPEG -> re-encode with the source's
# quantisation tables; mapping page -> pixels must go through image matrix x page rotation x crop box.
from dataclasses import dataclass, field
import io
import numpy as np, pymupdf
from numpy.lib.stride_tricks import sliding_window_view as swv

ADPI = 100
SPREAD_ASPECT = 1.15
MAX_SKEW_DEG = 3.0
ZONE = (0.33, 0.67)         # gutter must lie in this part of the width
SEAM_PEAK = 0.25            # mean blurred darkness of the seam column (0 = paper, 1 = black)
SEAM_BAND = 0.4             # shadow band = columns at least this fraction of the seam peak
SEAM_MAX_W = 0.10           # wider dark areas are pictures, not seams
FLAG_BELOW = 0.65
VALLEY_MAX_SLANT = 1.5     # deg; valley fits follow word spaces, so prefer vertical
REDETECT_ACCEPT = 0.03     # a re-detected cut must land this close to the median
MEDIAN_TOL = 0.04          # gutter further than this from the book median: re-detect near it, else flag
EDGE_ROWS = 0.03          # ignore this fraction of rows at top and bottom (scanner borders)

@dataclass
class Cut:
    page: int
    is_spread: bool
    confidence: float
    x_top: float = 0.0
    x_bottom: float = 0.0
    clearance_mm: float = 0.0
    method: str = ""
    reasons: list = field(default_factory=list)
    def frac(self, w): return (self.x_top + self.x_bottom) / 2 / w

def _runs(mask):
    d = np.diff(np.r_[0, np.asarray(mask, np.int8), 0]); return list(zip(np.where(d == 1)[0], np.where(d == -1)[0]))

def _close(mask, k):            # fill gaps shorter than k in a 1-D boolean mask
    m = mask.copy()
    for a, b in _runs(~mask):
        if b - a < k and a > 0 and b < len(mask): m[a:b] = True
    return m

def _maxfilter(g, k):
    p = k // 2
    g = swv(np.pad(g, ((p, p), (0, 0)), mode="edge"), k, axis=0).max(-1)
    return swv(np.pad(g, ((0, 0), (p, p)), mode="edge"), k, axis=1).max(-1)

def features(page):
    pix = page.get_pixmap(dpi=ADPI, colorspace=pymupdf.csGRAY)
    g = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.stride)[:, : pix.width].astype(np.float32)
    paper = np.percentile(g, 90)
    bg = _maxfilter(g, max(3, int(0.15 * ADPI) | 1))
    ink = (g < 0.72 * bg) & (bg - g > 25)
    T = np.zeros(ink.shape, np.int32); T[1:] = ink[1:] != ink[:-1]
    e = int(EDGE_ROWS * T.shape[0]); T[:e] = 0; T[T.shape[0] - e:] = 0   # scanner borders at top/bottom
    gb = _boxblur(np.clip(1 - g / paper, 0, 1), max(2, int(0.03 * ADPI)))
    return g, T, gb

def _fit(pts, H):
    """Robust straight line x = c0 + k*y through (y, x) points; None if too few or too steep."""
    if len(pts) < 4: return None
    P = np.array(pts, float)
    for _ in range(2):
        k, c0 = np.polyfit(P[:, 0], P[:, 1], 1)
        res = np.abs(P[:, 1] - (k * P[:, 0] + c0))
        keep = res <= max(2.0, 2.5 * np.median(res))
        if keep.sum() >= 4: P = P[keep]
    k, c0 = np.polyfit(P[:, 0], P[:, 1], 1)
    if abs(np.degrees(np.arctan(k))) > MAX_SKEW_DEG: return None
    return c0, c0 + k * (H - 1)

def _boxblur(a, k):
    c = np.cumsum(np.pad(a, ((k, k), (0, 0)), mode="edge"), 0); a = (c[2 * k:] - c[:-2 * k]) / (2 * k)
    c = np.cumsum(np.pad(a, ((0, 0), (k, k)), mode="edge"), 1); return (c[:, 2 * k:] - c[:, :-2 * k]) / (2 * k)

def seam_line(gb, W, H, zone=ZONE):
    """Binding shadow: the darkest column band near the middle (solid, dithered or grey shadows alike)."""
    r0, r1 = int(0.08 * H), int(0.92 * H)
    Dm = gb[r0:r1].mean(0)
    lo, hi = int(zone[0] * W), int(zone[1] * W)
    pk = lo + int(np.argmax(Dm[lo:hi])); peak = Dm[pk]
    base = np.median(Dm[int(.1 * W): int(.9 * W)])
    if peak < SEAM_PEAK or peak < 2.0 * base: return None
    half = Dm > 0.5 * peak; l = pk; r = pk
    sh = Dm > SEAM_BAND * peak; sl_, sr_ = pk, pk                # shadow band (for masking text checks)
    while sl_ > 0 and sh[sl_ - 1] and pk - sl_ < 0.06 * W: sl_ -= 1
    while sr_ < W - 1 and sh[sr_ + 1] and sr_ - pk < 0.06 * W: sr_ += 1
    while l > 0 and half[l - 1]: l -= 1
    while r < W - 1 and half[r + 1]: r += 1
    if r - l > SEAM_MAX_W * W: return None                      # a picture, not a seam
    m = int(0.03 * W); w0, w1 = max(0, pk - m), min(W, pk + m)
    step = (r1 - r0) // 16; pts = []
    for s0 in range(r0, r1 - step + 1, step):
        prof = gb[s0: s0 + step, w0:w1].mean(0)
        if prof.max() >= 0.6 * peak: pts.append((s0 + step / 2, w0 + int(np.argmax(prof))))
    line = _fit(pts, H)
    return (line if line else (float(pk), float(pk))), (pk - sl_, sr_ - pk)

def valley_line(T, W, H, zone=ZONE):
    P = np.convolve(T.sum(0).astype(float), np.ones(5) / 5, "same")
    level = np.percentile(P[int(.05 * W): int(.95 * W)], 75)
    if level <= 0: return None, None
    lo, hi = int(zone[0] * W), int(zone[1] * W)
    runs = [(max(a, lo), min(b, hi)) for a, b in _runs(P < 0.08 * level) if b > lo and a < hi]
    runs = [(a, b) for a, b in runs if b - a >= 0.004 * W]
    if not runs: return None, None
    a, b = max(runs, key=lambda ab: (ab[1] - ab[0]) - 0.5 * abs((ab[0] + ab[1]) / 2 - W / 2))
    m = int(0.03 * W); w0, w1 = max(0, a - m), min(W, b + m)
    pts = []
    for s0 in range(0, H - H // 16 + 1, H // 16):
        p = T[s0: s0 + H // 16, w0:w1].sum(0)
        if p.sum() == 0: continue
        z = [(u, v) for u, v in _runs(p == 0) if u > 0 and v < len(p)]
        if z:
            u, v = min(z, key=lambda uv: abs((uv[0] + uv[1]) / 2 + w0 - (a + b) / 2))
            pts.append((s0 + H / 32, w0 + (u + v) / 2))
    line = _fit(pts, H)
    vert = (float(np.median([p[1] for p in pts])) if pts else (a + b) / 2,) * 2
    if line is None or abs(np.degrees(np.arctan((line[1] - line[0]) / H))) > VALLEY_MAX_SLANT:
        return vert, (a, b)
    mv, cv = evaluate(T, vert, H, W); ml, cl = evaluate(T, line, H, W)
    return (line if (ml < mv or (ml == mv and cl >= cv + 1.0)) else vert), (a, b)

def text_outside_seam(T, line, band, H, W):
    """Text transitions outside the binding-shadow band around the cut line (shadow speckle is not text)."""
    xt, xb = line; left, right = band; pad = max(1, int(0.01 * ADPI))
    X = xt + (xb - xt) * np.arange(H)[:, None] / (H - 1)
    xs = np.arange(W)[None, :]
    return np.where((xs >= X - left - pad) & (xs <= X + right + pad), 0, T)

def evaluate(T, line, H, W):
    C = np.cumsum(np.pad(T, ((0, 0), (1, 0))), axis=1); ys = np.arange(H); xt, xb = line
    def rows(h):
        X = xt + (xb - xt) * ys / (H - 1)
        X0 = np.clip(np.round(X - h).astype(int), 0, W); X1 = np.clip(np.round(X + h + 1).astype(int), 0, W)
        return (C[ys, X1] - C[ys, X0]) > 0
    def marks(h):
        merged = []
        for u, v in _runs(rows(h)):
            if merged and u - merged[-1][1] < 0.1 * ADPI: merged[-1] = (merged[-1][0], v)
            else: merged.append((u, v))
        return len(merged)
    m0 = marks(0); clear = 0
    for hh in range(1, int(0.8 * ADPI)):
        if marks(hh) > max(m0, 2): break
        clear = hh
    return m0, clear / ADPI * 25.4

def detect_page(page, idx=0, zone=ZONE):
    r = page.rect; aspect = r.width / r.height
    if aspect < 1.0: return Cut(idx, False, 0.95, method="aspect", reasons=["portrait"])
    g, T, gb = features(page); H, W = g.shape
    options = []
    seam = seam_line(gb, W, H, zone)
    if seam:
        sl, band = seam
        Tx = text_outside_seam(T, sl, band, H, W)
        options.append(("seam", sl, *evaluate(Tx, sl, H, W), Tx))
    vl, band = valley_line(T, W, H, zone)
    if vl:
        options.append(("valley", vl, *evaluate(T, vl, H, W), T))
    if not options:
        return Cut(idx, False, 0.6 if aspect >= SPREAD_ASPECT else 0.8, method="none",
                   reasons=["landscape page: no seam or gutter found" if aspect >= SPREAD_ASPECT else "no gutter"])
    # prefer the seam unless it crosses clearly more text
    options.sort(key=lambda o: (min(o[2], 3), 0 if o[0] == "seam" else 1, -o[3]))
    method, (xt, xb), crossed, clear_mm, Tused = options[0]
    reasons, conf = [], 1.0
    if aspect < SPREAD_ASPECT: conf *= 0.6; reasons.append(f"aspect {aspect:.2f} below spread range")
    if crossed > 2: conf *= 0.3; reasons.append(f"cut crosses text ({crossed} lines/marks)")
    elif crossed: conf *= 0.9; reasons.append(f"cut touches {crossed} small mark(s)")
    if method == "valley":
        if clear_mm < 1: conf *= 0.5; reasons.append(f"gutter clearance {clear_mm:.1f} mm")
        elif clear_mm < 3: conf *= 0.85; reasons.append(f"gutter clearance {clear_mm:.1f} mm")
        if band and band[1] - band[0] > 0.25 * W: conf *= 0.7; reasons.append("very wide low-text band")
    xm = (xt + xb) / 2
    li, ri = Tused[:, : int(xm)].sum(), Tused[:, int(xm):].sum()
    if min(li, ri) / max(li, ri, 1) < 0.05:
        if method == "seam" and clear_mm >= 3: conf *= 0.9; reasons.append("one side (almost) blank (clear seam)")
        else: conf *= 0.6; reasons.append("one side (almost) blank")
    off = abs(xm / W - 0.5)
    if off > 0.15: conf *= 0.4; reasons.append(f"gutter far from centre ({xm / W:.3f})")
    elif off > 0.08: conf *= 0.8; reasons.append(f"gutter off centre ({xm / W:.3f})")
    sk = np.degrees(np.arctan((xb - xt) / H))
    if abs(sk) > 0.2: reasons.append(f"slant {sk:+.2f} deg")
    s = r.width / W
    return Cut(idx, True, round(conf, 3), xt * s, xb * s, round(clear_mm, 1), method, reasons)

def detect(doc):
    cuts = [detect_page(p, i) for i, p in enumerate(doc)]
    sp = [c for c in cuts if c.is_spread and c.confidence >= FLAG_BELOW]
    if len(sp) < 3:
        return cuts
    med = float(np.median([c.frac(doc[c.page].rect.width) for c in sp]))
    for k, c in enumerate(cuts):
        if not c.is_spread: continue
        w = doc[c.page].rect.width
        if "one side (almost) blank" in c.reasons and c.method == "valley":
            c.x_top = c.x_bottom = med * w; c.reasons.append(f"cut placed at book median {med:.3f}")
            continue
        if abs(c.frac(w) - med) <= MEDIAN_TOL: continue
        alt = detect_page(doc[c.page], c.page, zone=(med - MEDIAN_TOL, med + MEDIAN_TOL))
        if (alt.is_spread and not any("crosses text" in r for r in alt.reasons)
                and abs(alt.frac(w) - med) <= REDETECT_ACCEPT):
            alt.confidence = round(min(alt.confidence, 0.85) * 0.85, 3)
            alt.reasons.append(f"re-detected near book median {med:.3f} (first guess {c.frac(w):.3f})")
            cuts[k] = alt
        else:
            c.confidence = round(min(c.confidence, FLAG_BELOW - 0.05), 3)
            c.reasons.append(f"gutter {c.frac(w):.3f} differs from book median {med:.3f}")
    return cuts


# ---------------------------------------------------------------- physical split helpers (spec)
def upright_pixels(page):
    """Single page image as a numpy array re-oriented to the DISPLAYED page, plus the display rect it
    covers. Real case: Unruh = JBIG2 image placed rotated, page /Rotate 90, crop box smaller than the
    image. Cut x (display points) -> pixel column via that display rect. Residual skew (~0.1 deg) ignored."""
    info = [i for i in page.get_image_info(xrefs=True) if i["xref"]]
    if len(info) != 1:
        return None                                   # not a single-scan page: copy unchanged, report it
    i = info[0]
    M = pymupdf.Matrix(i["transform"]) * page.rotation_matrix
    o = pymupdf.Point(0, 0) * M; ex = pymupdf.Point(1, 0) * M - o; ey = pymupdf.Point(0, 1) * M - o
    pix = pymupdf.Pixmap(page.parent, i["xref"])
    if pix.alpha: pix = pymupdf.Pixmap(pix, 0)
    a = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)
    if abs(ex.x) >= abs(ex.y):
        if ex.x < 0: a = a[:, ::-1]
        if ey.y < 0: a = a[::-1]
    else:
        a = a.transpose(1, 0, 2)
        if ex.y < 0: a = a[::-1]
        if ey.x < 0: a = a[:, ::-1]
    disp = pymupdf.Rect(pymupdf.Point(0, 0) * M, pymupdf.Point(1, 1) * M).normalize()
    # disp is already in DISPLAY coordinates of page.rect (get_image_info's transform is relative to the
    # crop box; do NOT subtract the crop box again; checked on Unruh). The image may overhang the page
    # (crop it to page.rect) or fall short of it (Unruh p20 starts 20 pt inside: pad with white).
    # Unruh's image matrix also carries a ~0.11 deg rotation; ignoring it shifts content <= ~0.5 mm.
    return np.ascontiguousarray(a), disp

def g4_xobject(doc, im1):
    """Raw CCITT G4 image object from a PIL mode-'1' image.
    GOTCHAS: page.insert_image(stream=<G4 TIFF>) silently decodes to raw (60x larger);
    doc.update_stream(..., compress=False) REMOVES /Filter, so set Filter/DecodeParms after it."""
    from PIL import Image, TiffImagePlugin
    old = TiffImagePlugin.STRIP_SIZE; TiffImagePlugin.STRIP_SIZE = 1 << 30      # one strip
    try:
        b = io.BytesIO(); im1.save(b, "TIFF", compression="group4")
    finally:
        TiffImagePlugin.STRIP_SIZE = old
    t = Image.open(io.BytesIO(b.getvalue()))
    (off,), (cnt,) = t.tag_v2[273], t.tag_v2[279]
    data = b.getvalue()[off: off + cnt]
    w, h = im1.size
    blackis1 = "true" if t.tag_v2.get(262, 0) == 1 else "false"
    parms = f"<< /K -1 /Columns {w} /Rows {h} /BlackIs1 {blackis1} >>"
    x = doc.get_new_xref()
    doc.update_object(x, f"<< /Type /XObject /Subtype /Image /Width {w} /Height {h} /ColorSpace /DeviceGray "
                         f"/BitsPerComponent 1 /Filter /CCITTFaxDecode /DecodeParms {parms} >>")
    doc.update_stream(x, data, compress=False)
    doc.xref_set_key(x, "Filter", "/CCITTFaxDecode")
    doc.xref_set_key(x, "DecodeParms", parms)
    return x            # then: page.insert_image(page.rect, xref=x)

def jpeg_half(src_jpeg_bytes, half_pil):
    """Re-encode a JPEG half with the SOURCE quantisation tables and sampling: 1.01-1.03x, mean error
    ~3/255. (q90/q95 re-encode = 1.9x/2.4x; jpegtran lossless crop rounds both cut edges out to
    16-px blocks and needs another binary.) Insert with page.insert_image(stream=...) (keeps DCT)."""
    from PIL import Image, JpegImagePlugin
    src = Image.open(io.BytesIO(src_jpeg_bytes))
    b = io.BytesIO()
    half_pil.save(b, "JPEG", qtables=src.quantization, subsampling=JpegImagePlugin.get_sampling(src))
    return b.getvalue()

# Measured sizes of split output vs source images: Schwartz CCITT -> G4 halves 1.11x; Burger JPEG
# (source tables) 1.01x; Unruh JBIG2 -> G4 halves 3.6x (no JBIG2 encoder; alternative: keep the
# JBIG2 object shared by both halves and clip each half; 1.00x, but each half still holds the spread).
# Slanted cut + rectangular crop: whiten the other page's pixels in the wedge; report the count.

# Notes from the independent review (2026-10-21):
# - Pages that are not a single full-page image (Unruh pp. 1-3: several images, the gutter shadow is
#   its own strip) cannot be split by this method: report them as flagged, never skip silently.
# - Re-OCR of a page that ALREADY has a text layer must rebuild the page from its image (as the split
#   does); overlaying onto the original page duplicates every word in get_text()/pdftotext.
# - Tesseract textonly_pdf needs the `pdf` config and `pdf.ttf` under TESSDATA_PREFIX ("Can't open
#   pdf" otherwise); a tessdata folder with only .traineddata files fails.
# - Text-quality scoring must join U+00AD soft hyphens at line ends first (ABBYY writes them): Ariel's
#   ABBYY layer scores 1.2% WER after that, 10.6% before; period orthography also inflates misses.
# - JPX sources have no quantisation tables: jpeg_half needs a fallback (e.g. lossless Flate) if a JPX
#   spread ever appears; none in the corpus today.
