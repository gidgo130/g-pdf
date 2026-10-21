# SPDX-License-Identifier: AGPL-3.0-or-later
"""Halves keep the source's placement: orientation, residual (deskew) rotation, offsets."""

import pytest

np = pytest.importorskip("numpy")
Image = pytest.importorskip("PIL.Image")
import pymupdf  # noqa: E402

from gpdf.pipeline import images  # noqa: E402

from .synth import place, rotated_page  # noqa: E402


def render(page, dpi=50):
    pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
    return np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.stride)[:, : pix.width]


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


def decode(doc, xref):
    pix = pymupdf.Pixmap(doc, xref)
    a = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)
    return a[:, :, 0] if pix.n == 1 else a


@pytest.fixture(scope="module", params=[(1.5, "g4"), (-1.5, "jpeg")], ids=["g4+1.5", "jpeg-1.5"])
def deskewed(request, synth, tmp_path_factory):
    """A deskewed spread (pixels tilted, image matrix turning them back) and its split."""
    skew, encoding = request.param
    from gpdf.pipeline import split

    path = tmp_path_factory.mktemp("deskew") / "deskewed.pdf"
    doc = pymupdf.open()
    truth = synth.add_deskewed(doc, "deskewed", skew=skew, encoding=encoding)
    doc.save(str(path))
    with pymupdf.open(path) as d:
        (cut,) = split.analyze(d)
    rep = split.split_document(path, path.with_name("deskewed_split.pdf"))
    return {"src": path, "out": rep.output, "truth": truth, "cut": cut, "page": rep.pages[0]}


def test_deskewed_page_is_split_with_the_cut_in_the_gap(deskewed, synth):
    c, t, p = deskewed["cut"], deskewed["truth"], deskewed["page"]
    assert c.is_spread and not c.flagged
    assert not any("cannot split" in r for r in c.reasons)
    assert p.action == "split" and p.image_rotation_deg == -t.notes["skew"]
    assert 0.94 < p.image_cover < 0.96
    strip = t.notes["strip"]
    with pymupdf.open(deskewed["src"]) as src:
        r = src[0].rect
    gl, gr = (strip + g * synth.PT for g in t.gap)       # text is straight on the display
    for row in range(synth.TOP, synth.TOP + synth.BLOCK_H):
        y = row * synth.PT
        x = c.x_top + (c.x_bottom - c.x_top) * y / r.height
        assert gl < x < gr, (row, x, gl, gr)


def test_deskewed_halves_render_like_the_source(deskewed):
    from .test_split import corr

    p = deskewed["page"]
    strip_px = round((deskewed["truth"].notes["strip"] - 8) * 50 / 72)   # minus the tilt
    with pymupdf.open(deskewed["src"]) as src, pymupdf.open(deskewed["out"]) as out:
        full = render(src[0]).astype(np.float32)
        W = full.shape[1]
        assert (full[:, :strip_px] == 255).all()
        for o, (x0, x1) in zip(p.out_pages, ((0, max(p.cut) * W), (min(p.cut) * W, W))):
            h = render(out[o - 1]).astype(float)

            def clip(dx):
                c = full[:, max(0, round(x0) + dx): round(x1) + dx]
                return np.asarray(Image.fromarray(c, "F").resize((h.shape[1], h.shape[0])))

            here = corr(h, clip(0))
            assert here > 0.9
            assert here > max(corr(h, clip(-3)), corr(h, clip(3)))
            if p.encoding == "jpeg":
                assert np.abs(h - clip(0)).mean() < 4
        left = render(out[p.out_pages[0] - 1])
        assert (left[:, :strip_px] == 255).all()              # the blank strip stays blank


def test_deskewed_halves_keep_the_source_pixels(deskewed):
    p = deskewed["page"]
    with pymupdf.open(deskewed["src"]) as src_doc, pymupdf.open(deskewed["out"]) as out:
        page = src_doc[0]
        src, _ = images.examine(page)
        up = images.upright_pixels(page, src).pixels.astype(int)
        halves = images.split_halves(page, src, p.cut)[:2]
        diffs = 0
        for o, half in zip(p.out_pages, halves):
            x = out[o - 1].get_images()[0][0]
            got = decode(out, x).astype(int)
            c0, r0 = half.origin
            s = up[r0: r0 + got.shape[0], c0: c0 + got.shape[1]]
            assert got.shape == s.shape == half.pixels.shape
            if p.encoding == "g4":
                assert out.xref_get_key(x, "Filter")[1] == "/CCITTFaxDecode"
                d = got != s
                assert (got[d] == 255).all()
                diffs += int(d.sum())
            else:
                assert out.xref_get_key(x, "Filter")[1] == "/DCTDecode"
                assert np.abs(got - s).mean() < 4
        if p.encoding == "g4":
            assert diffs == p.whitened_px


def test_deskewed_page_beyond_three_degrees_is_refused(synth, tmp_path):
    from gpdf.pipeline import split

    path = tmp_path / "steep.pdf"
    doc = pymupdf.open()
    synth.add_deskewed(doc, "steep", skew=3.5)
    doc.save(str(path))
    rep = split.split_document(path, tmp_path / "steep_split.pdf", accept_flagged=True)
    (p,) = rep.pages
    assert p.action == "keep" and p.left_whole == "cannot split: image rotated 3.5 deg"
