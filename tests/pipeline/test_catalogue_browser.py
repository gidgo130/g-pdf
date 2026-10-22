# SPDX-License-Identifier: AGPL-3.0-or-later
"""The catalogue review sheet in a real browser (Playwright, headless Chromium): edit and
confirm decisions, save, restore and load them, and the next survey reads the saved file.

Skipped unless Playwright and its Chromium are installed (CI installs them on Linux)."""

import json
import shutil

import pytest

pytest.importorskip("numpy")
pytest.importorskip("PIL")
pytest.importorskip("wordfreq")
sync_api = pytest.importorskip("playwright.sync_api")

from gpdf.pipeline import catalogue  # noqa: E402

from .test_catalogue import base, by_rel, layout, load, survey_cli  # noqa: E402, F401

NO_PICKER = "delete Window.prototype.showSaveFilePicker; delete window.showSaveFilePicker;"


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
def sheet(layout):
    assert survey_cli(layout, "A") == 0
    return layout, layout["cat"] / catalogue.SHEET, by_rel(load(layout))


def open_sheet(browser, path, *init_scripts):
    ctx = browser.new_context(accept_downloads=True, viewport={"width": 1366, "height": 900})
    for script in init_scripts:
        ctx.add_init_script(script)
    page = ctx.new_page()
    page.errors = []
    page.on("pageerror", lambda e: page.errors.append(str(e)))
    page.on("console", lambda m: page.errors.append(m.text) if m.type == "error" else None)
    page.on("dialog", lambda d: d.accept())          # beforeunload on reload
    page.goto(path.as_uri())
    return page


def row(page, entry):
    return page.locator(f'tr.entry[data-id="{entry["id"]}"]')


def detail(page):
    return page.locator("tr.detail")


def decisions(page):
    return json.loads(page.evaluate("gpdfCatalogue.decisionsText()"))


def test_edit_confirm_save_and_the_next_survey_reads_it(browser, sheet, tmp_path, capsys):
    lay, html, files = sheet
    txt, pdf = files["obra.txt"], files["obra.pdf"]
    page = open_sheet(browser, html, NO_PICKER)
    assert page.inner_text("#progress") == "Confirmed 0 / 4"
    assert decisions(page)["files"] == {}

    row(page, txt).click()
    assert detail(page).locator("select").first.input_value() == "exclude"
    detail(page).locator("button.confirm").click()
    assert page.evaluate(f"gpdfCatalogue.status('{txt['id']}')") == "confirmed"

    row(page, txt).click()                                   # close it
    row(page, pdf).click()
    d = detail(page)
    d.locator("select").first.select_option("exclude")
    d.locator("input").nth(1).fill("off_topic")              # reason
    d.locator("input").nth(1).dispatch_event("change")
    d.locator("textarea").fill("fuera del tema")
    d.locator("textarea").dispatch_event("change")
    assert page.evaluate(f"gpdfCatalogue.status('{pdf['id']}')") == "pending"
    assert "-> exclude (off_topic)" in row(page, pdf).inner_text()
    detail(page).locator("button.confirm").click()
    assert page.inner_text("#progress") == "Confirmed 2 / 4"

    page.locator(".strip .bar").first.click()                # page line for a bar
    assert page.inner_text(".page-info").startswith("p. 1 · es · ")

    with page.expect_download() as dl:
        page.click("#save")
    assert dl.value.suggested_filename == catalogue.DECISIONS
    saved = tmp_path / "saved.json"
    dl.value.save_as(saved)
    assert not page.evaluate("gpdfCatalogue.dirty()")
    data = json.loads(saved.read_text("utf-8"))
    assert data["schema"] == 1 and set(data["files"]) == {txt["id"], pdf["id"]}
    t, p = data["files"][txt["id"]], data["files"][pdf["id"]]
    assert (t["action"], t["reason"], t["duplicate_of"], t["confirmed"],
            t["suggestion_action"]) == ("exclude", "duplicate", pdf["id"], True, "exclude")
    assert (p["action"], p["reason"], p["notes"], p["confirmed"], p["suggestion_action"]) == (
        "exclude", "off_topic", "fuera del tema", True, "keep_text")
    assert p["title"] == "Obra" and p["path"] == pdf["path"]
    assert page.errors == []

    shutil.copy(saved, lay["cat"] / catalogue.DECISIONS)     # the owner keeps it in DIR
    capsys.readouterr()
    assert survey_cli(lay, "A") == 0
    out = capsys.readouterr().out
    line = next(s for s in out.splitlines() if s.startswith("obra.pdf"))
    assert line.rstrip().endswith("confirmed")
    page = open_sheet(browser, html)                         # the rewritten sheet
    assert page.inner_text("#progress") == "Confirmed 2 / 4"
    assert page.errors == []


def test_restore_unsaved_work_and_load_a_file(browser, sheet, tmp_path):
    lay, html, files = sheet
    pdf = files["obra.pdf"]
    page = open_sheet(browser, html)
    row(page, pdf).click()
    detail(page).locator("button.confirm").click()
    edited = decisions(page)["files"]
    page.reload()
    assert page.locator("#restore").is_visible()
    assert page.inner_text("#restore-text").startswith("Restore your unsaved decisions (1 file")
    assert decisions(page)["files"] == {}
    page.click("#restore-yes")
    assert decisions(page)["files"] == edited
    page.reload()
    page.click("#restore-no")
    page.reload()
    assert not page.locator("#restore").is_visible()

    good = tmp_path / "good.json"
    good.write_text(json.dumps({"schema": 1, "files": {pdf["id"]: {
        "action": "keep_text", "confirmed": True, "suggestion_action": "keep_text"}}}), "utf-8")
    page.set_input_files("#load-input", str(good))
    page.wait_for_function(f"gpdfCatalogue.status('{pdf['id']}') === 'confirmed'")
    bad = tmp_path / "bad.json"
    bad.write_text('{"schema": 2, "files": {}}', "utf-8")
    page.set_input_files("#load-input", str(bad))
    page.wait_for_selector("#msg.error")
    assert "Nothing was changed" in page.inner_text("#msg")
    assert page.evaluate(f"gpdfCatalogue.status('{pdf['id']}')") == "confirmed"

    page.click('[data-filter="pending"]')
    assert page.locator("tr.entry").count() == 3
    page.click("#confirm-shown")
    assert page.inner_text("#progress") == "Confirmed 4 / 4"
    page.click('[data-filter="all"]')
    assert page.locator("tr.entry").count() == 4
    assert page.errors == []


def test_applied_decision_shows_applied_with_the_medians(browser, sheet):
    lay, html, files = sheet
    cat = load(lay)
    pdf = files["obra.pdf"]
    cat["files"][pdf["id"]]["applied"] = {"action": "rebuild_layer", "median_before": 6.02,
                                          "median_after": 0.51}
    decisions = {"schema": 1, "files": {pdf["id"]: {
        "path": pdf["path"], "action": "rebuild_layer", "pages": "all", "confirmed": True,
        "suggestion_action": pdf["suggestion"]["action"]}}}
    catalogue.write_sheet(cat, decisions, lay["cat"])
    page = open_sheet(browser, html)
    badge = row(page, pdf).locator(".badge.applied")
    assert badge.inner_text() == "applied"
    assert badge.get_attribute("title") == "rebuild_layer applied: median suspect 6.0% -> 0.5%"
    assert page.inner_text("#progress") == "Confirmed 1 / 4"
    assert page.errors == []
