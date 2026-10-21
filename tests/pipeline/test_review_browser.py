# SPDX-License-Identifier: AGPL-3.0-or-later
"""The review sheet in a real browser (Playwright, headless Chromium): review, save,
restore and load decisions, then run the split with the saved file.

Skipped unless Playwright and its Chromium are installed (CI installs them on Linux)."""

import json
import math
import shutil

import pytest

pytest.importorskip("numpy")
pytest.importorskip("PIL")
sync_api = pytest.importorskip("playwright.sync_api")
import pymupdf  # noqa: E402

from gpdf import cli  # noqa: E402
from gpdf.pipeline import split  # noqa: E402
from gpdf.pipeline.report import KEEP, SPLIT, UNREVIEWED, Decisions  # noqa: E402

# mini book: flagged pages 5 (spread guess), 6 and 7 (single guesses)
NO_PICKER = "delete Window.prototype.showSaveFilePicker; delete window.showSaveFilePicker;"
NO_STORAGE = ("Object.defineProperty(window, 'localStorage', {configurable: true, "
              "get() { throw new DOMException('blocked', 'SecurityError'); }});")


@pytest.fixture(scope="module")
def browser():
    pw = sync_api.sync_playwright().start()
    try:
        b = pw.chromium.launch()
    except Exception as e:  # noqa: BLE001 - no browser binary, missing system libraries
        pw.stop()
        pytest.skip(f"Chromium does not launch: {e}")
    yield b
    b.close()
    pw.stop()


@pytest.fixture
def sheet(mini, tmp_path):
    """(source, outdir, sheet path) after a dry run of a copy of the mini book."""
    src = tmp_path / "mini.pdf"
    shutil.copy(mini[0], src)
    outdir = tmp_path / "out"
    assert cli.main(["split", str(src), "-o", str(outdir), "--dry-run"]) == 0
    return src, outdir, outdir / "mini_split.review.html"


def open_sheet(browser, path, *init_scripts):
    ctx = browser.new_context(accept_downloads=True, viewport={"width": 1366, "height": 768})
    for script in init_scripts:
        ctx.add_init_script(script)
    page = ctx.new_page()
    page.errors = []
    page.on("pageerror", lambda e: page.errors.append(str(e)))
    page.on("console", lambda m: page.errors.append(m.text) if m.type == "error" else None)
    page.on("dialog", lambda d: d.accept())          # beforeunload on reload
    page.goto(path.as_uri())
    return page


def state(page):
    return json.loads(page.evaluate("gpdfSheet.decisionsText()"))


def save(page, path):
    with page.expect_download() as dl:
        page.click("#save")
    assert dl.value.suggested_filename == "mini_split.decisions.json"
    dl.value.save_as(path)
    return path


def test_review_save_and_split(browser, sheet, tmp_path):
    src, outdir, html = sheet
    page = open_sheet(browser, html, NO_PICKER)
    original = json.loads((outdir / "mini_split.decisions.json").read_text("utf-8"))
    assert page.evaluate("gpdfSheet.current()") == 5           # first unreviewed flagged
    page.focus("#command")
    page.keyboard.press("k")                                   # typing in a field: ignored
    assert state(page) == original
    page.locator("#command").blur()
    page.keyboard.press("k")
    assert page.evaluate("gpdfSheet.current()") == 6
    img = page.locator("#main-img").bounding_box()
    h = page.locator("#main-frame .handle.bottom").bounding_box()
    x, y = h["x"] + h["width"] / 2, h["y"] + h["height"] / 2
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x + 40, y, steps=5)
    page.mouse.up()
    page.keyboard.press("s")
    assert page.locator("#progress").inner_text() == "Flagged reviewed: 2 / 3"
    path = save(page, tmp_path / "reviewed.json")
    assert "<saved decisions file>" not in page.input_value("#command")
    assert not page.evaluate("gpdfSheet.dirty()")

    dec = Decisions.load(path)
    saved = json.loads(path.read_text("utf-8"))
    for k in (0, 1, 2, 3, 6):                                  # untouched pages unchanged
        assert saved["pages"][k] == original["pages"][k]
    assert {k: saved[k] for k in ("schema", "source", "source_size", "source_mtime_ns")} == {
        k: original[k] for k in ("schema", "source", "source_size", "source_mtime_ns")}
    rep = split.split_document(src, outdir / "mini_split.pdf", decisions=dec)
    p5, p6, p7 = rep.pages[4:7]
    assert (p5.action, p5.reviewed, p5.left_whole) == (KEEP, True, "")
    assert p6.action == SPLIT and p6.reviewed
    assert p6.cut[0] == pytest.approx(0.5, abs=1e-6)
    assert p6.cut[1] == pytest.approx(0.5 + 40 / img["width"], abs=0.01)
    assert p7.left_whole == UNREVIEWED and rep.exit_code == 2
    assert page.errors == []


def test_reload_offers_to_restore_unsaved_work(browser, sheet):
    page = open_sheet(browser, sheet[2])
    original = state(page)
    page.keyboard.press("k")
    page.keyboard.press("s")
    reviewed = state(page)
    assert reviewed != original
    page.reload()
    assert page.locator("#restore").is_visible()
    assert page.inner_text("#restore-text") == "Restore your unsaved review (2 pages changed)?"
    assert state(page) == original                             # nothing applied yet
    page.click("#restore-yes")
    assert state(page) == reviewed and not page.locator("#restore").is_visible()
    page.reload()
    page.click("#restore-no")
    assert state(page) == original
    page.reload()
    assert not page.locator("#restore").is_visible()
    assert page.errors == []


def test_works_without_storage(browser, sheet, tmp_path):
    page = open_sheet(browser, sheet[2], NO_PICKER, NO_STORAGE)
    assert page.evaluate("gpdfSheet.current()") == 5
    page.keyboard.press("k")
    saved = json.loads(save(page, tmp_path / "d.json").read_text("utf-8"))
    assert (saved["pages"][4]["action"], saved["pages"][4]["reviewed"]) == ("keep", True)
    assert not page.locator("#restore").is_visible()
    assert page.errors == []


def test_load_decisions_checks_the_stamp(browser, sheet, tmp_path):
    src, outdir, html = sheet
    page = open_sheet(browser, html)
    before = state(page)
    text = (outdir / "mini_split.decisions.json").read_text("utf-8")
    dec = json.loads(text)
    stale = tmp_path / "stale.json"
    stale.write_text(text.replace(f'"source_size": {dec["source_size"]}',
                                  f'"source_size": {dec["source_size"] + 1}'), "utf-8")
    page.set_input_files("#load-input", str(stale))
    page.wait_for_selector("#msg.error")
    assert "source size differs" in page.inner_text("#msg")
    assert state(page) == before

    dec["pages"][6].update(action="split", cut=[0.4, 0.45], reviewed=True)
    good = tmp_path / "good.json"
    good.write_text(json.dumps(dec), "utf-8")
    page.set_input_files("#load-input", str(good))
    page.wait_for_selector("#msg:not(.error)")
    after = state(page)
    assert after["pages"][6] == dec["pages"][6]
    assert after["source_mtime_ns"] == dec["source_mtime_ns"]
    assert page.locator("#progress").inner_text() == "Flagged reviewed: 1 / 3"
    assert page.errors == []


def badges(page, pno):
    return page.locator("#tiles .tile").nth(pno - 1).locator(".badge").all_inner_texts()


def test_kept_spread_shows_kept_whole_not_single(browser, sheet):
    page = open_sheet(browser, sheet[2])
    assert "single" not in badges(page, 5)                     # spread guess, split
    page.keyboard.press("k")                                   # p. 5: a spread kept whole
    assert page.evaluate("gpdfSheet.current()") == 6
    page.keyboard.press("k")                                   # p. 6: called single, kept
    assert badges(page, 5) == ["reviewed", "changed", "kept whole"]
    assert badges(page, 6) == ["reviewed", "single"]
    assert page.errors == []


# cut controls, all on p. 5 (flagged, split by the detector, so the saved file has its cut)
def cut5(page):
    return state(page)["pages"][4]["cut"]


def slant(cut, size):
    w, h = size
    return math.degrees(math.atan((cut[1] - cut[0]) * w / h))


@pytest.fixture
def size5(sheet):
    with pymupdf.open(sheet[0]) as doc:
        r = doc[4].rect
    return r.width, r.height


def test_ctrl_arrows_move_the_cut_and_plain_arrows_navigate(browser, sheet, tmp_path):
    src, outdir, html = sheet
    page = open_sheet(browser, html, NO_PICKER)
    c0 = cut5(page)
    page.keyboard.press("Control+ArrowRight")
    assert cut5(page) == pytest.approx([c0[0] + 0.001, c0[1] + 0.001], abs=1e-6)
    page.keyboard.press("Control+Shift+ArrowRight")
    assert cut5(page) == pytest.approx([c0[0] + 0.011, c0[1] + 0.011], abs=1e-6)
    page.keyboard.press("Control+ArrowLeft")
    assert cut5(page) == pytest.approx([c0[0] + 0.010, c0[1] + 0.010], abs=1e-6)
    assert f"{c0[0] + 0.010:.3f} / {c0[1] + 0.010:.3f}" in page.inner_text("#facts")
    assert page.evaluate("gpdfSheet.current()") == 5 and page.evaluate("window.scrollY") == 0
    page.keyboard.press("ArrowRight")
    assert page.evaluate("gpdfSheet.current()") == 6
    page.keyboard.press("ArrowLeft")
    page.keyboard.press("Enter")                               # p. 5: split as shown

    path = save(page, tmp_path / "d.json")                     # still a valid decisions file
    rep = split.split_document(src, outdir / "mini_split.pdf", decisions=Decisions.load(path))
    assert rep.pages[4].action == SPLIT
    assert rep.pages[4].cut == pytest.approx([c0[0] + 0.010, c0[1] + 0.010], abs=1e-6)
    assert page.errors == []


def test_ctrl_up_down_turn_about_the_midpoint_and_v_straightens(browser, sheet, size5):
    page = open_sheet(browser, sheet[2])
    c0 = cut5(page)
    page.keyboard.press("Control+ArrowUp")                     # clockwise: slant decreases
    c1 = cut5(page)
    assert slant(c1, size5) == pytest.approx(slant(c0, size5) - 0.1, abs=1e-3)
    assert sum(c1) / 2 == pytest.approx(sum(c0) / 2, abs=1e-6)
    page.keyboard.press("Control+Shift+ArrowDown")
    c2 = cut5(page)
    assert slant(c2, size5) == pytest.approx(slant(c0, size5) + 0.9, abs=1e-3)
    assert sum(c2) / 2 == pytest.approx(sum(c0) / 2, abs=1e-6)
    page.keyboard.press("v")
    m = sum(c0) / 2
    assert cut5(page) == pytest.approx([m, m], abs=1e-6)
    assert "+0.00 deg" in page.inner_text("#facts")
    page.focus("#command")                                     # typing in a field: ignored
    page.keyboard.press("Control+ArrowRight")
    assert cut5(page) == pytest.approx([m, m], abs=1e-6)
    page.locator("#command").blur()
    assert not page.locator("#keys").is_visible()
    page.keyboard.press("?")
    assert page.locator("#keys").is_visible()
    page.keyboard.press("?")
    assert not page.locator("#keys").is_visible()
    assert page.errors == []


def test_moves_stop_at_the_clamp_without_bending_the_line(browser, sheet, size5):
    page = open_sheet(browser, sheet[2])
    page.keyboard.press("Control+ArrowUp")                     # give it some slant
    c0 = cut5(page)
    for _ in range(80):
        page.keyboard.press("Control+Shift+ArrowRight")
    c = cut5(page)
    assert max(c) == pytest.approx(0.95, abs=1e-6)
    assert c[1] - c[0] == pytest.approx(c0[1] - c0[0], abs=1e-6)
    page.keyboard.press("r")
    for _ in range(120):                                       # turn until an end meets it
        page.keyboard.press("Control+Shift+ArrowUp")
    c = cut5(page)
    assert max(c) <= 0.95 and min(c) >= 0.05 and abs(slant(c, size5)) > 30
    page.keyboard.press("Control+Shift+ArrowUp")               # one more step is refused
    assert cut5(page) == c
    assert page.errors == []


def main_img(page):
    b = page.locator("#main-img").bounding_box()
    return b["x"], b["y"], b["width"], b["height"]


def test_click_in_the_top_or_bottom_band_moves_that_end(browser, sheet):
    page = open_sheet(browser, sheet[2])
    c0 = cut5(page)
    x, y, w, h = main_img(page)
    page.mouse.click(x + 0.3 * w, y + 0.05 * h)                # top band
    c = cut5(page)
    assert c[0] == pytest.approx(0.3, abs=0.003) and c[1] == c0[1]
    page.mouse.click(x + 0.7 * w, y + h + 6)                   # below the image, by the handle
    c = cut5(page)
    assert c[0] == pytest.approx(0.3, abs=0.003) and c[1] == pytest.approx(0.7, abs=0.003)
    page.mouse.click(x + 0.2 * w, y + 0.5 * h)                 # middle of the image: nothing
    page.mouse.click(x + 0.5 * w, y + 0.5 * h)                 # on the line, no drag: nothing
    assert cut5(page) == c
    page.mouse.move(x + 0.4 * w, y + 0.5 * h)                  # a drag in the middle: nothing
    page.mouse.down()
    page.mouse.move(x + 0.45 * w, y + 0.5 * h, steps=4)
    page.mouse.up()
    assert cut5(page) == c
    assert page.errors == []


def test_drag_the_line_and_turn_it_with_the_wheel_while_held(browser, sheet, size5):
    page = open_sheet(browser, sheet[2])
    c0 = cut5(page)
    x, y, w, h = main_img(page)
    lx = x + (c0[0] + 0.5 * (c0[1] - c0[0])) * w
    page.mouse.move(lx + 4, y + 0.5 * h)                       # within the hit zone
    page.mouse.down()
    page.mouse.move(lx + 4 + 30, y + 0.5 * h, steps=5)
    page.mouse.up()
    c1 = cut5(page)
    assert c1[0] - c0[0] == pytest.approx(30 / w, abs=0.002)
    assert c1[1] - c1[0] == pytest.approx(c0[1] - c0[0], abs=1e-6)

    py = round(y + 0.25 * h)                                   # hold the line a quarter down
    t = (py - y) / h                                           # (events carry whole pixels)
    px = x + (c1[0] + t * (c1[1] - c1[0])) * w
    pivot = c1[0] + t * (c1[1] - c1[0])
    page.mouse.move(px, py)
    page.mouse.down()
    page.mouse.wheel(0, -100)                                  # one notch up: clockwise
    page.mouse.wheel(0, -100)
    c2 = cut5(page)
    assert slant(c2, size5) == pytest.approx(slant(c1, size5) - 0.2, abs=1e-3)
    assert c2[0] + t * (c2[1] - c2[0]) == pytest.approx(pivot, abs=1e-6)
    page.mouse.wheel(0, 100)                                   # one notch down
    c3 = cut5(page)
    assert slant(c3, size5) == pytest.approx(slant(c1, size5) - 0.1, abs=1e-3)
    page.mouse.up()
    assert cut5(page) == c3                                    # release: not a click
    assert page.evaluate("window.scrollY") == 0                # held: the page did not scroll

    page.mouse.wheel(0, 300)                                   # not held: scrolls, no turn
    page.wait_for_function("window.scrollY > 0")
    assert cut5(page) == c3
    assert page.errors == []


def test_handle_drags_still_move_one_end_or_with_shift_the_whole_line(browser, sheet):
    page = open_sheet(browser, sheet[2])
    c0 = cut5(page)
    _, _, w, _ = main_img(page)
    hb = page.locator("#main-frame .handle.top").bounding_box()
    hx, hy = hb["x"] + hb["width"] / 2, hb["y"] + hb["height"] / 2
    page.mouse.move(hx, hy)
    page.mouse.down()
    page.mouse.move(hx - 20, hy, steps=4)
    page.mouse.up()
    c1 = cut5(page)
    assert c1[0] == pytest.approx(c0[0] - 20 / w, abs=0.002) and c1[1] == c0[1]
    page.keyboard.down("Shift")
    page.mouse.move(hx - 20, hy)
    page.mouse.down()
    page.mouse.move(hx, hy, steps=4)
    page.mouse.up()
    page.keyboard.up("Shift")
    c2 = cut5(page)
    assert c2[0] - c1[0] == pytest.approx(20 / w, abs=0.002)
    assert c2[1] - c2[0] == pytest.approx(c1[1] - c1[0], abs=1e-6)
    assert page.errors == []


def test_close_up_controls_the_same_cut(browser, sheet):
    page = open_sheet(browser, sheet[2])
    c0 = cut5(page)
    x0, x1 = page.evaluate("JSON.parse(document.getElementById('gpdf-data').textContent)"
                           ".pages[4].close.x")
    b = page.locator("#close-img").bounding_box()              # tall: only its top is in view
    page.mouse.click(b["x"] + 0.25 * b["width"], b["y"] + 4)   # top band
    want = x0 + 0.25 * (x1 - x0)
    assert cut5(page) == pytest.approx([want, c0[1]], abs=0.003)
    x, _, w, _ = main_img(page)                                # the main view follows
    hb = page.locator("#main-frame .handle.top").bounding_box()
    assert (hb["x"] + hb["width"] / 2 - x) / w == pytest.approx(want, abs=0.003)
    assert page.errors == []


def test_saving_a_sheet_made_from_stale_decisions_writes_fresh_detection(browser, pieced,
                                                                        tmp_path):
    src = tmp_path / "pieced.pdf"
    shutil.copy(pieced, src)
    outdir = tmp_path / "out"
    assert cli.main(["split", str(src), "-o", str(outdir), "--dry-run", "--no-sheet"]) == 0
    path = outdir / "pieced_split.decisions.json"
    dec = json.loads(path.read_text("utf-8"))
    fresh = dec["pages"][0]["detected"]
    dec["pages"][0]["detected"] = dict(fresh, reasons=["cannot split: 3 images on the page "
                                                       "(need exactly one)"])
    path.write_text(json.dumps(dec), "utf-8")
    assert cli.main(["split", str(src), "-o", str(outdir), "--sheet-only",
                     "--decisions", str(path)]) == 0
    page = open_sheet(browser, outdir / "pieced_split.review.html", NO_PICKER)
    assert page.evaluate("gpdfSheet.current()") == 1
    assert not page.locator("#act-split").is_disabled()          # splittable now
    page.keyboard.press("s")
    with page.expect_download() as dl:
        page.click("#save")
    dl.value.save_as(tmp_path / "saved.json")
    saved = json.loads((tmp_path / "saved.json").read_text("utf-8"))
    assert saved["pages"][0]["detected"] == fresh
    assert (saved["pages"][0]["action"], saved["pages"][0]["reviewed"]) == ("split", True)
    Decisions.load(tmp_path / "saved.json")
    assert page.errors == []
