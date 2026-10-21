# SPDX-License-Identifier: AGPL-3.0-or-later
"""The review sheet in a real browser (Playwright, headless Chromium): review, save,
restore and load decisions, then run the split with the saved file.

Skipped unless Playwright and its Chromium are installed (CI installs them on Linux)."""

import json
import shutil

import pytest

pytest.importorskip("numpy")
pytest.importorskip("PIL")
sync_api = pytest.importorskip("playwright.sync_api")

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
