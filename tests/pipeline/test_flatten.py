# SPDX-License-Identifier: AGPL-3.0-or-later
"""Flattening pieced-together black-and-white spreads (Unruh pp. 1-3): visible font text,
1-bit leftover images and a vector line, rendered and split as one 1-bit page."""

import copy

import pytest

np =pytest.importorskip("numpy")
Image = pytest.importorskip("PIL.Image")
import pymupdf  # noqa: E402

from gpdf.pipeline import images, review, split  # noqa: E402
from gpdf.pipeline.report import KEEP, SPLIT  # noqa: E402

from . import synth  # noqa: E402
from .test_split import corr, render  # noqa: E402

PIECED, RGB, SMASK, RED = range(4)
FLAT = {"dpi": 300, "images": 3, "visible_text_spans": 2 * synth.PIECED_LINES, "drawings": 1}


@pytest.fixture(scope="module")
def pieced(tmp_path_factory):
    path = tmp_path_factory.mktemp("flatten") / "pieced.pdf"
    doc = pymupdf.open()
    for variant in (None, "rgb", "smask", "red"):
        synth.add_pieced_spread(doc, variant)
    doc.save(str(path), garbage=3, deflate=True)
    doc.close()
    return path


@pytest.fixture(scope="module")
def dry(pieced):
    decisions, _ = split.dry_run(pieced)
    return decisions


def test_pieced_spread_is_flagged_to_be_flattened(pieced, dry):
    with pymupdf.open(pieced) as doc:
        src, why = images.examine(doc[PIECED])
        assert src.kind == images.G4 and src.flatten == FLAT
        assert why == ["will be flattened at 300 dpi: 3 images, visible text, 1 drawing"]
        d = dry.pages[PIECED]
        assert d.detected.flagged and why[0] in d.detected.reasons
        assert not any(r.startswith(review.CANNOT_SPLIT) for r in d.detected.reasons)
        assert review._page_view(doc[PIECED], d)["splittable"]


@pytest.mark.parametrize("pno, needle", [(RGB, "3 images"), (SMASK, "3 images"),
                                         (RED, "3 images")])
def test_spoiled_pieced_pages_are_still_refused(pieced, dry, pno, needle):
    with pymupdf.open(pieced) as doc:
        src, why = images.examine(doc[pno])
        assert src is None and why == [f"cannot split: {needle} on the page (need exactly one)"]
        assert why[0] in dry.pages[pno].detected.reasons
        assert not review._page_view(doc[pno], dry.pages[pno])["splittable"]


def test_single_scan_with_a_black_line_flattens_a_red_one_does_not():
    doc = pymupdf.open()
    for colour in ((0, 0, 0), (1, 0, 0)):
        page = synth.new_page(doc)
        synth.put_image(page, synth.g4(doc, np.zeros((synth.H, synth.W), bool)))
        page.draw_line((10, 10), (100, 10), color=colour)
    src, why = images.examine(doc[0])
    assert src.flatten == {"dpi": 200, "images": 1, "visible_text_spans": 0, "drawings": 1}
    assert why == ["will be flattened at 200 dpi: 1 image, 1 drawing"]
    assert images.examine(doc[1]) == (None, [images.DRAWINGS])


def reviewed(dry, cut=(0.5, 0.5)):
    """The dry run's decisions with the pieced page split at ``cut``, the others kept."""
    dec = copy.deepcopy(dry)
    for k, p in enumerate(dec.pages):
        p.action, p.cut = (SPLIT, list(cut)) if k == PIECED else (KEEP, None)
        p.reviewed = True
    return dec


def test_reviewed_split_flattens_into_g4_halves_without_text(pieced, dry, tmp_path):
    aa = pymupdf.TOOLS.show_aa_level()
    out = tmp_path / "out.pdf"
    rep = split.split_document(pieced, out, decisions=reviewed(dry))
    assert pymupdf.TOOLS.show_aa_level() == aa
    p = rep.pages[PIECED]
    assert (p.action, p.encoding, p.flattened, p.needs_ocr) == (SPLIT, "g4", FLAT, True)
    assert (p.image_rotation_deg, p.image_cover) == (0.0, 1.0)
    assert all(q.flattened is None for q in rep.pages[1:])
    assert rep.to_dict()["pages"][PIECED]["flattened"] == FLAT
    with pymupdf.open(pieced) as src, pymupdf.open(out) as o:
        sp = src[PIECED]
        assert sp.get_text().strip()
        full = render(sp).astype(np.float32)
        W = full.shape[1]
        for n, (x0, x1) in zip(p.out_pages, ((0, 0.5 * W), (0.5 * W, W))):
            half = o[n - 1]
            assert not half.get_text().strip() and not half.get_drawings()
            ((x, *_),) = half.get_images()
            assert o.xref_get_key(x, "Filter")[1] == "/CCITTFaxDecode"
            assert o.xref_get_key(x, "BitsPerComponent")[1] == "1"
            assert int(o.xref_get_key(x, "Height")[1]) == round(sp.rect.height * 300 / 72)
            h = render(half).astype(float)

            def clip(dx):
                c = full[:, max(0, round(x0) + dx): round(x1) + dx]
                return np.asarray(Image.fromarray(c, "F").resize((h.shape[1], h.shape[0])))

            here = corr(h, clip(0))
            assert here > 0.9
            assert here > max(corr(h, clip(-3)), corr(h, clip(3)))


def test_accept_flagged_flattens_too(pieced, dry, tmp_path):
    rep = split.split_document(pieced, tmp_path / "out.pdf", accept_flagged=True)
    p = rep.pages[PIECED]
    assert dry.pages[PIECED].detected.is_spread                 # the guess: a spread
    assert p.action == SPLIT and p.flattened == FLAT and p.cut == dry.pages[PIECED].detected.cut
    assert all(q.action == KEEP and "cannot split" in q.left_whole
               for q in rep.pages[1:] if dry.pages[q.page - 1].detected.is_spread)


def test_anti_aliasing_is_restored_even_when_the_render_fails(pieced, monkeypatch):
    before = pymupdf.TOOLS.show_aa_level()
    try:
        pymupdf.TOOLS.set_aa_level(4)
        pymupdf.mupdf.fz_set_text_aa_level(2)
        odd = pymupdf.TOOLS.show_aa_level()
        seen = []

        def broken(self, *a, **k):
            seen.append(pymupdf.TOOLS.show_aa_level())
            raise RuntimeError("render failed")

        with pymupdf.open(pieced) as doc:
            page = doc[PIECED]
            up = images.flatten_pixels(page, 72)                  # works: levels come back
            assert set(np.unique(up.pixels)) <= {0, 255}
            assert pymupdf.TOOLS.show_aa_level() == odd
            monkeypatch.setattr(pymupdf.Page, "get_pixmap", broken)
            with pytest.raises(RuntimeError):
                images.flatten_pixels(page, 72)
        assert seen[0]["graphics"] == seen[0]["text"] == 0
        assert pymupdf.TOOLS.show_aa_level() == odd
    finally:
        pymupdf.TOOLS.set_aa_level(before["graphics"])
        pymupdf.mupdf.fz_set_text_aa_level(before["text"])
