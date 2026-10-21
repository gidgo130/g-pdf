# SPDX-License-Identifier: AGPL-3.0-or-later
"""Spread detection on the synthetic book."""

import pytest

np = pytest.importorskip("numpy")
import pymupdf  # noqa: E402

from gpdf.pipeline import spreads  # noqa: E402


@pytest.fixture(scope="module")
def detected(book):
    path, truth = book
    with pymupdf.open(path) as doc:
        cuts = spreads.detect(doc)
        widths = [p.rect.width for p in doc]
    return cuts, truth, widths


def cut_in_gap(synth, cut, truth, width_pt):
    """Composition column of the cut at every text row, and whether all lie in the gap."""
    rows = np.arange(int(0.1 * synth.H), int(0.9 * synth.H))
    x_pt = cut.x_top + (cut.x_bottom - cut.x_top) * rows / (synth.H - 1)
    X = x_pt * (synth.W / width_pt)                    # display pixels at DPI
    a, b, c, d, e, f = truth.affine or (1, 0, 0, 0, 1, 0)
    xs = a * X + b * rows + c
    gl, gr = truth.gap
    return xs, bool(((xs >= gl) & (xs <= gr)).all())


def test_spread_verdicts(detected):
    cuts, truth, _ = detected
    for c, t in zip(cuts, truth):
        expect = t.spread and t.name != "very_narrow"
        assert c.is_spread == expect, (t.name, c)


def test_flagged_set_is_exactly_the_doubtful_pages(detected):
    """two_images is a confident spread here; the split stage flags it (test_split)."""
    cuts, truth, _ = detected
    flagged = {t.name for c, t in zip(cuts, truth) if c.flagged}
    assert flagged == {"off044", "off056", "blank_side", "landscape_picture"}
    assert all(c.flagged == (c.confidence < spreads.FLAG_BELOW) for c in cuts)


def test_unflagged_cuts_lie_in_the_gap_at_every_row(synth, detected):
    cuts, truth, widths = detected
    checked = []
    for c, t, w in zip(cuts, truth, widths):
        if c.is_spread and not c.flagged:
            xs, ok = cut_in_gap(synth, c, t, w)
            assert ok, (t.name, xs.min(), xs.max(), t.gap)
            checked.append(t.name)
    assert {"solid", "dithered", "skew_pos", "skew_neg", "narrow4mm", "photo_jpeg",
            "rotated", "plain_flate1"} <= set(checked)


def test_skewed_cuts_follow_the_slant(detected):
    cuts, truth, _ = detected
    by = {t.name: c for c, t in zip(cuts, truth)}
    assert by["skew_pos"].x_bottom < by["skew_pos"].x_top
    assert by["skew_neg"].x_bottom > by["skew_neg"].x_top


def test_blank_side_cut_is_at_the_book_median_and_in_the_gap(synth, detected):
    cuts, truth, widths = detected
    k = next(i for i, t in enumerate(truth) if t.name == "blank_side")
    assert any("book median" in r for r in cuts[k].reasons)
    assert cut_in_gap(synth, cuts[k], truth[k], widths[k])[1]


def test_plate_cut_is_on_the_fold_or_flagged(synth, detected):
    """Regression: a thick printed frame line at 0.43 W must not win over the fold."""
    cuts, truth, widths = detected
    k = next(i for i, t in enumerate(truth) if t.name == "plate")
    c = cuts[k]
    assert c.flagged or (abs(c.frac(widths[k]) - 0.5) < 0.01
                         and cut_in_gap(synth, c, truth[k], widths[k])[1])


def test_very_narrow_gutter_is_called_single_but_flagged(mini):
    path, truth = mini
    k = next(i for i, t in enumerate(truth) if t.name == "very_narrow")
    with pymupdf.open(path) as doc:
        c = spreads.detect(doc)[k]
    assert not c.is_spread and c.flagged


def test_gap_check_rejects_a_cut_on_the_frame_line(synth, detected):
    _, truth, widths = detected
    k = next(i for i, t in enumerate(truth) if t.name == "plate")
    x = 0.422 * widths[k]
    assert not cut_in_gap(synth, spreads.PageCut(k, True, 1.0, x, x), truth[k], widths[k])[1]
