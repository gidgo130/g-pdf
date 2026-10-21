# SPDX-License-Identifier: AGPL-3.0-or-later
"""split_document on the synthetic book: pixels, encodings, structure, safety."""

import hashlib
import io
import os
import shutil
import sys

import pytest

np = pytest.importorskip("numpy")
Image = pytest.importorskip("PIL.Image")
import pymupdf  # noqa: E402

from gpdf.pipeline import split  # noqa: E402
from gpdf.pipeline.report import SPLIT, UNREVIEWED  # noqa: E402


def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def decode(doc, xref):
    pix = pymupdf.Pixmap(doc, xref)
    a = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)
    return a[:, :, 0] if pix.n == 1 else a


def render(page, dpi=50):
    pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
    return np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.stride)[:, : pix.width]


@pytest.fixture(scope="module")
def result(book, tmp_path_factory):
    path, truth = book
    before = sha(path)
    out = tmp_path_factory.mktemp("split") / "book_split.pdf"
    report = split.split_document(path, out)
    return {"src": path, "out": out, "truth": truth, "report": report,
            "hash_before": before, "names": [t.name for t in truth]}


def page_of(result, name):
    return result["report"].pages[result["names"].index(name)]


def test_analyze_flags_pages_that_cannot_be_split(book):
    path, truth = book
    with pymupdf.open(path) as doc:
        cuts = split.analyze(doc)
    flagged = {t.name for c, t in zip(cuts, truth) if c.flagged}
    assert flagged == {t.name for t in truth if t.flagged} | (
        {"plate"} if cuts[[t.name for t in truth].index("plate")].flagged else set())
    two = cuts[[t.name for t in truth].index("two_images")]
    assert any("2 images" in r for r in two.reasons)


def test_source_unchanged_and_output_reopens(result):
    assert sha(result["src"]) == result["hash_before"]
    rep = result["report"]
    with pymupdf.open(result["out"]) as doc:
        assert not doc.is_repaired
        assert len(doc) == rep.pages_out == rep.pages_in + len(rep.spreads)
        for page in doc:
            render(page, 10)
    assert not [f for f in os.listdir(result["out"].parent) if f.endswith(".tmp")]


def test_unflagged_spreads_split_flagged_stay_whole(result):
    rep = result["report"]
    for t, p in zip(result["truth"], rep.pages):
        if t.flagged:
            assert p.action != SPLIT and p.left_whole == UNREVIEWED, t.name
        elif t.spread and t.name != "plate":
            assert p.action == SPLIT, t.name
    assert rep.exit_code == 2


def test_g4_halves_are_bit_exact_except_whitened_wedge(result):
    rep = result["report"]
    checked = 0
    with pymupdf.open(result["src"]) as src, pymupdf.open(result["out"]) as out:
        for t, p in zip(result["truth"], rep.pages):
            if p.action != SPLIT or t.encoding not in ("g4", "flate1") or t.name == "rotated":
                continue
            s = decode(src, src[p.page - 1].get_images()[0][0])
            halves = []
            for o in p.out_pages:
                x = out[o - 1].get_images()[0][0]
                assert out.xref_get_key(x, "Filter")[1] == "/CCITTFaxDecode", t.name
                assert out.xref_get_key(x, "BitsPerComponent")[1] == "1"
                halves.append(decode(out, x))
            left, right = halves
            L, R = left.shape[1], right.shape[1]
            assert left.shape[0] == right.shape[0] == s.shape[0]
            dl = left != s[:, :L]
            dr = right != s[:, s.shape[1] - R:]
            assert (left[dl] == 255).all() and (right[dr] == 255).all(), t.name
            assert int(dl.sum() + dr.sum()) == p.whitened_px, t.name
            checked += 1
    assert checked >= 8
    assert page_of(result, "skew_pos").whitened_px > 0


def test_jpeg_halves_keep_tables_and_size(result):
    p = page_of(result, "photo_jpeg")
    assert p.encoding == "jpeg"
    with pymupdf.open(result["src"]) as src, pymupdf.open(result["out"]) as out:
        sx = src[p.page - 1].get_images()[0][0]
        s_tables = Image.open(io.BytesIO(src.xref_stream_raw(sx))).quantization
        s = decode(src, sx).astype(int)
        size = 0
        x0 = 0
        for o in p.out_pages:
            x = out[o - 1].get_images()[0][0]
            assert out.xref_get_key(x, "Filter")[1] == "/DCTDecode"
            raw = out.xref_stream_raw(x)
            size += len(raw)
            assert Image.open(io.BytesIO(raw)).quantization == s_tables
            h = decode(out, x).astype(int)
            crop = s[:, x0: x0 + h.shape[1]] if x0 == 0 else s[:, s.shape[1] - h.shape[1]:]
            assert np.abs(h - crop).mean() < 4
            x0 = h.shape[1]
        assert size <= 1.1 * len(src.xref_stream_raw(sx))


def blur3(a):
    """3x3 box blur: removes the aliasing of dithered pixels rendered on different grids."""
    from numpy.lib.stride_tricks import sliding_window_view

    return sliding_window_view(np.pad(a, 1, mode="edge"), (3, 3)).mean((-1, -2))


def corr(a, b):
    return np.corrcoef(blur3(a).ravel(), blur3(b).ravel())[0, 1]


def test_rotated_cropped_page_halves_match_the_source_render(result):
    p = page_of(result, "rotated")
    assert p.action == SPLIT
    with pymupdf.open(result["src"]) as src, pymupdf.open(result["out"]) as out:
        sp = src[p.page - 1]
        assert sp.rotation == 90 and sp.cropbox != sp.mediabox
        full = render(sp).astype(np.float32)
        W = full.shape[1]
        for o, (x0, x1) in zip(p.out_pages, ((0, max(p.cut) * W), (min(p.cut) * W, W))):
            half = out[o - 1]
            assert half.rotation == 0
            h = render(half).astype(float)

            def clip(dx):
                c = full[:, max(0, round(x0) + dx): round(x1) + dx]
                return np.asarray(Image.fromarray(c, "F").resize((h.shape[1], h.shape[0])))

            here = corr(h, clip(0))
            assert here > 0.9
            assert here > max(corr(h, clip(-3)), corr(h, clip(3)))


def test_half_sizes_are_physical(result):
    with pymupdf.open(result["src"]) as src, pymupdf.open(result["out"]) as out:
        for p in result["report"].pages:
            if p.action != SPLIT:
                continue
            r = src[p.page - 1].rect
            a, b = (out[o - 1].rect for o in p.out_pages)
            assert a.height == pytest.approx(r.height, abs=0.5)
            assert a.width == pytest.approx(max(p.cut) * r.width, abs=0.5)
            assert b.width == pytest.approx((1 - min(p.cut)) * r.width, abs=0.5)


def test_text_layer_dropped_and_reported(result):
    p = page_of(result, "text_layer")
    assert p.needs_ocr and p.page in result["report"].needs_ocr
    with pymupdf.open(result["src"]) as src, pymupdf.open(result["out"]) as out:
        assert src[p.page - 1].get_text().strip()
        assert all(not out[o - 1].get_text().strip() for o in p.out_pages)


def test_outline_links_labels_and_metadata(result):
    rep = result["report"]
    first = {t.name: rep.pages[i].out_pages[0] for i, t in enumerate(result["truth"])}
    with pymupdf.open(result["src"]) as src, pymupdf.open(result["out"]) as out:
        assert [e[:2] for e in out.get_toc()] == [e[:2] for e in src.get_toc()]
        assert [e[2] for e in out.get_toc()] == [
            first[n] for n in ("cover", "solid", "dithered", "skew_pos", "plate", "two_column")]
        assert out.metadata["title"] == src.metadata["title"]
        assert out.metadata["author"] == "José Vasconcelos"
        assert not out.get_page_labels()
        assert rep.page_labels_dropped
        solid = page_of(result, "solid")
        assert (solid.links_dropped, solid.annots_dropped) == (1, 1)
        assert all(not out[o - 1].get_links() and not list(out[o - 1].annots())
                   for o in solid.out_pages)
        links = out[first["two_column"] - 1].get_links()
        assert [(lk["kind"], lk["page"] + 1) for lk in links] == [
            (pymupdf.LINK_GOTO, first["dithered"])]
        assert rep.links_retargeted == 1


def test_accept_flagged_leaves_unsplittable_pages_whole(result, tmp_path):
    rep = split.split_document(result["src"], tmp_path / "all.pdf", accept_flagged=True)
    two = rep.pages[result["names"].index("two_images")]
    assert two.action != SPLIT and "cannot split" in two.left_whole
    assert rep.pages[result["names"].index("off044")].action == SPLIT
    assert not rep.unreviewed and rep.unsplittable == [two] and rep.exit_code == 2


def test_refuses_to_write_over_the_source(book, tmp_path):
    src = tmp_path / "Libro.pdf"
    shutil.copy(book[0], src)
    with pytest.raises(split.SplitError, match="different file"):
        split.split_document(src, src)
    if sys.platform == "win32":
        with pytest.raises(split.SplitError, match="different file"):
            split.split_document(src, tmp_path / "LIBRO.PDF")


def test_refuses_existing_output_without_force(book, tmp_path):
    out = tmp_path / "x_split.pdf"
    out.write_bytes(b"keep me")
    with pytest.raises(split.SplitError, match="already exists"):
        split.split_document(book[0], out)
    assert out.read_bytes() == b"keep me"
    split.split_document(book[0], out, force=True)
    assert pymupdf.open(out).page_count > 18


def test_failure_leaves_no_output_and_no_temp_file(book, tmp_path, monkeypatch):
    def broken(path, pages):
        raise split.SplitError("simulated")

    monkeypatch.setattr(split, "_verify", broken)
    with pytest.raises(split.SplitError, match="simulated"):
        split.split_document(book[0], tmp_path / "out.pdf")
    assert os.listdir(tmp_path) == []
