# SPDX-License-Identifier: AGPL-3.0-or-later
"""Two-page spread detection (spec: docs/prototypes/spread_split_proto.py, detection v3).

Each page is rendered at ADPI grey and searched for two gutter cues:
  1. "seam": the binding shadow, the darkest column band near the middle of a blurred
     darkness map (solid black, dithered and grey shadows alike); the cut follows the
     darkest column per strip.
  2. "valley": a band with little text texture (vertical light/dark transitions; white
     space and flat shadows have none); the cut runs through the middle of the gap per strip.
A robust straight line (at most MAX_SKEW_DEG) is fitted through the per-strip points. Each
option is scored by the text marks the cut crosses (shadow band masked for the seam);
the seam wins ties. A book pass then re-detects gutters far from the book median near it,
or flags them.

The thresholds were calibrated on three real books and reviewed independently: keep them
as they are unless the spec is re-measured.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pymupdf
from numpy.lib.stride_tricks import sliding_window_view as swv

ADPI = 100                  # detection render resolution
SPREAD_ASPECT = 1.15        # width / height at or above this looks like a spread
MAX_SKEW_DEG = 3.0
ZONE = (0.33, 0.67)         # gutter must lie in this part of the width
SEAM_PEAK = 0.25            # mean blurred darkness of the seam column (0 = paper, 1 = black)
SEAM_BAND = 0.4             # shadow band = columns at least this fraction of the seam peak
SEAM_MAX_W = 0.10           # wider dark areas are pictures, not seams
FLAG_BELOW = 0.65           # confidence below this: the owner reviews the page
VALLEY_MAX_SLANT = 1.5      # deg; valley fits follow word spaces, so prefer vertical
REDETECT_ACCEPT = 0.03      # a re-detected cut must land this close to the median
MEDIAN_TOL = 0.04           # gutter further than this from the book median: re-detect near it, else flag
EDGE_ROWS = 0.03            # ignore this fraction of rows at top and bottom (scanner borders)

BLANK_SIDE = "one side (almost) blank"


@dataclass
class PageCut:
    """Detector verdict for one page. ``page`` is 0-based; x_top/x_bottom are display points
    (page.rect coordinates) of the cut at the top and bottom edge."""

    page: int
    is_spread: bool
    confidence: float
    x_top: float = 0.0
    x_bottom: float = 0.0
    clearance_mm: float = 0.0
    method: str = ""
    reasons: list[str] = field(default_factory=list)

    @property
    def flagged(self) -> bool:
        return self.confidence < FLAG_BELOW

    def frac(self, width: float) -> float:
        return (self.x_top + self.x_bottom) / 2 / width


def _runs(mask):
    d = np.diff(np.r_[0, np.asarray(mask, np.int8), 0])
    return list(zip(np.where(d == 1)[0], np.where(d == -1)[0]))


def _maxfilter(g, k):
    p = k // 2
    g = swv(np.pad(g, ((p, p), (0, 0)), mode="edge"), k, axis=0).max(-1)
    return swv(np.pad(g, ((0, 0), (p, p)), mode="edge"), k, axis=1).max(-1)


def _boxblur(a, k):
    c = np.cumsum(np.pad(a, ((k, k), (0, 0)), mode="edge"), 0)
    a = (c[2 * k:] - c[: -2 * k]) / (2 * k)
    c = np.cumsum(np.pad(a, ((0, 0), (k, k)), mode="edge"), 1)
    return (c[:, 2 * k:] - c[:, : -2 * k]) / (2 * k)


def features(page):
    """Grey render, text-transition map T and blurred darkness map gb of a page."""
    pix = page.get_pixmap(dpi=ADPI, colorspace=pymupdf.csGRAY)
    g = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.stride)[:, : pix.width]
    g = g.astype(np.float32)
    paper = np.percentile(g, 90)
    bg = _maxfilter(g, max(3, int(0.15 * ADPI) | 1))
    ink = (g < 0.72 * bg) & (bg - g > 25)
    T = np.zeros(ink.shape, np.int32)
    T[1:] = ink[1:] != ink[:-1]
    e = int(EDGE_ROWS * T.shape[0])                     # scanner borders at top/bottom
    T[:e] = 0
    T[T.shape[0] - e:] = 0
    gb = _boxblur(np.clip(1 - g / paper, 0, 1), max(2, int(0.03 * ADPI)))
    return g, T, gb


def _fit(pts, H):
    """Robust straight line x = c0 + k*y through (y, x) points; None if too few or too steep."""
    if len(pts) < 4:
        return None
    P = np.array(pts, float)
    for _ in range(2):
        k, c0 = np.polyfit(P[:, 0], P[:, 1], 1)
        res = np.abs(P[:, 1] - (k * P[:, 0] + c0))
        keep = res <= max(2.0, 2.5 * np.median(res))
        if keep.sum() >= 4:
            P = P[keep]
    k, c0 = np.polyfit(P[:, 0], P[:, 1], 1)
    if abs(np.degrees(np.arctan(k))) > MAX_SKEW_DEG:
        return None
    return c0, c0 + k * (H - 1)


def seam_line(gb, W, H, zone=ZONE):
    """Binding shadow: the darkest column band near the middle (solid, dithered or grey
    shadows alike). Returns ((x_top, x_bottom), (left, right) shadow band widths) or None."""
    r0, r1 = int(0.08 * H), int(0.92 * H)
    Dm = gb[r0:r1].mean(0)
    lo, hi = int(zone[0] * W), int(zone[1] * W)
    pk = lo + int(np.argmax(Dm[lo:hi]))
    peak = Dm[pk]
    base = np.median(Dm[int(0.1 * W): int(0.9 * W)])
    if peak < SEAM_PEAK or peak < 2.0 * base:
        return None
    half = Dm > 0.5 * peak
    left = right = pk
    sh = Dm > SEAM_BAND * peak                          # shadow band (for masking text checks)
    sl_ = sr_ = pk
    while sl_ > 0 and sh[sl_ - 1] and pk - sl_ < 0.06 * W:
        sl_ -= 1
    while sr_ < W - 1 and sh[sr_ + 1] and sr_ - pk < 0.06 * W:
        sr_ += 1
    while left > 0 and half[left - 1]:
        left -= 1
    while right < W - 1 and half[right + 1]:
        right += 1
    if right - left > SEAM_MAX_W * W:                   # a picture, not a seam
        return None
    m = int(0.03 * W)
    w0, w1 = max(0, pk - m), min(W, pk + m)
    step = (r1 - r0) // 16
    pts = []
    for s0 in range(r0, r1 - step + 1, step):
        prof = gb[s0: s0 + step, w0:w1].mean(0)
        if prof.max() >= 0.6 * peak:
            pts.append((s0 + step / 2, w0 + int(np.argmax(prof))))
    line = _fit(pts, H)
    return (line if line else (float(pk), float(pk))), (pk - sl_, sr_ - pk)


def valley_line(T, W, H, zone=ZONE):
    """Low text-texture band near the middle. Returns ((x_top, x_bottom), (a, b) band) or (None, None)."""
    P = np.convolve(T.sum(0).astype(float), np.ones(5) / 5, "same")
    level = np.percentile(P[int(0.05 * W): int(0.95 * W)], 75)
    if level <= 0:
        return None, None
    lo, hi = int(zone[0] * W), int(zone[1] * W)
    runs = [(max(a, lo), min(b, hi)) for a, b in _runs(P < 0.08 * level) if b > lo and a < hi]
    runs = [(a, b) for a, b in runs if b - a >= 0.004 * W]
    if not runs:
        return None, None
    a, b = max(runs, key=lambda ab: (ab[1] - ab[0]) - 0.5 * abs((ab[0] + ab[1]) / 2 - W / 2))
    m = int(0.03 * W)
    w0, w1 = max(0, a - m), min(W, b + m)
    pts = []
    for s0 in range(0, H - H // 16 + 1, H // 16):
        p = T[s0: s0 + H // 16, w0:w1].sum(0)
        if p.sum() == 0:
            continue
        z = [(u, v) for u, v in _runs(p == 0) if u > 0 and v < len(p)]
        if z:
            u, v = min(z, key=lambda uv: abs((uv[0] + uv[1]) / 2 + w0 - (a + b) / 2))
            pts.append((s0 + H / 32, w0 + (u + v) / 2))
    line = _fit(pts, H)
    vert = (float(np.median([p[1] for p in pts])) if pts else (a + b) / 2,) * 2
    if line is None or abs(np.degrees(np.arctan((line[1] - line[0]) / H))) > VALLEY_MAX_SLANT:
        return vert, (a, b)
    mv, cv = evaluate(T, vert, H, W)
    ml, cl = evaluate(T, line, H, W)
    return (line if (ml < mv or (ml == mv and cl >= cv + 1.0)) else vert), (a, b)


def text_outside_seam(T, line, band, H, W):
    """Text transitions outside the binding-shadow band around the cut line (shadow speckle is not text)."""
    xt, xb = line
    left, right = band
    pad = max(1, int(0.01 * ADPI))
    X = xt + (xb - xt) * np.arange(H)[:, None] / (H - 1)
    xs = np.arange(W)[None, :]
    return np.where((xs >= X - left - pad) & (xs <= X + right + pad), 0, T)


def evaluate(T, line, H, W):
    """(text marks the cut crosses, clearance in mm before a wider cut crosses more)."""
    C = np.cumsum(np.pad(T, ((0, 0), (1, 0))), axis=1)
    ys = np.arange(H)
    xt, xb = line

    def rows(h):
        X = xt + (xb - xt) * ys / (H - 1)
        X0 = np.clip(np.round(X - h).astype(int), 0, W)
        X1 = np.clip(np.round(X + h + 1).astype(int), 0, W)
        return (C[ys, X1] - C[ys, X0]) > 0

    def marks(h):
        merged = []
        for u, v in _runs(rows(h)):
            if merged and u - merged[-1][1] < 0.1 * ADPI:
                merged[-1] = (merged[-1][0], v)
            else:
                merged.append((u, v))
        return len(merged)

    m0 = marks(0)
    clear = 0
    for hh in range(1, int(0.8 * ADPI)):
        if marks(hh) > max(m0, 2):
            break
        clear = hh
    return m0, clear / ADPI * 25.4


def detect_page(page, idx=0, zone=ZONE) -> PageCut:
    r = page.rect
    aspect = r.width / r.height
    if aspect < 1.0:
        return PageCut(idx, False, 0.95, method="aspect", reasons=["portrait"])
    g, T, gb = features(page)
    H, W = g.shape
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
        wide = aspect >= SPREAD_ASPECT
        return PageCut(idx, False, 0.6 if wide else 0.8, method="none",
                       reasons=["landscape page: no seam or gutter found" if wide else "no gutter"])
    # prefer the seam unless it crosses clearly more text
    options.sort(key=lambda o: (min(o[2], 3), 0 if o[0] == "seam" else 1, -o[3]))
    method, (xt, xb), crossed, clear_mm, Tused = options[0]
    reasons, conf = [], 1.0
    if aspect < SPREAD_ASPECT:
        conf *= 0.6
        reasons.append(f"aspect {aspect:.2f} below spread range")
    if crossed > 2:
        conf *= 0.3
        reasons.append(f"cut crosses text ({crossed} lines/marks)")
    elif crossed:
        conf *= 0.9
        reasons.append(f"cut touches {crossed} small mark(s)")
    if method == "valley":
        if clear_mm < 1:
            conf *= 0.5
            reasons.append(f"gutter clearance {clear_mm:.1f} mm")
        elif clear_mm < 3:
            conf *= 0.85
            reasons.append(f"gutter clearance {clear_mm:.1f} mm")
        if band and band[1] - band[0] > 0.25 * W:
            conf *= 0.7
            reasons.append("very wide low-text band")
    xm = (xt + xb) / 2
    li, ri = Tused[:, : int(xm)].sum(), Tused[:, int(xm):].sum()
    if min(li, ri) / max(li, ri, 1) < 0.05:
        if method == "seam" and clear_mm >= 3:
            conf *= 0.9
            reasons.append(BLANK_SIDE + " (clear seam)")
        else:
            conf *= 0.6
            reasons.append(BLANK_SIDE)
    off = abs(xm / W - 0.5)
    if off > 0.15:
        conf *= 0.4
        reasons.append(f"gutter far from centre ({xm / W:.3f})")
    elif off > 0.08:
        conf *= 0.8
        reasons.append(f"gutter off centre ({xm / W:.3f})")
    sk = np.degrees(np.arctan((xb - xt) / H))
    if abs(sk) > 0.2:
        reasons.append(f"slant {sk:+.2f} deg")
    s = r.width / W
    return PageCut(idx, True, round(conf, 3), float(xt * s), float(xb * s), round(clear_mm, 1),
                   method, reasons)


def detect(doc, progress=None) -> list[PageCut]:
    """Detect every page, then the book pass: gutters far from the median of the confident
    spreads are re-detected near it, or flagged. ``progress(done, total)`` is optional."""
    cuts = []
    for i, p in enumerate(doc):
        cuts.append(detect_page(p, i))
        if progress:
            progress(i + 1, len(doc))
    sp = [c for c in cuts if c.is_spread and c.confidence >= FLAG_BELOW]
    if len(sp) < 3:
        return cuts
    med = float(np.median([c.frac(doc[c.page].rect.width) for c in sp]))
    for k, c in enumerate(cuts):
        if not c.is_spread:
            continue
        w = doc[c.page].rect.width
        if BLANK_SIDE in c.reasons and c.method == "valley":
            c.x_top = c.x_bottom = med * w
            c.reasons.append(f"cut placed at book median {med:.3f}")
            continue
        if abs(c.frac(w) - med) <= MEDIAN_TOL:
            continue
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
