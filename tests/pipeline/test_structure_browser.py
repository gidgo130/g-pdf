# SPDX-License-Identifier: AGPL-3.0-or-later
"""The structure review sheet in a real browser (Playwright, headless Chromium): edit,
move, merge, split, confirm the metadata, save; the next structure run applies the saved
file to the same tree the sheet showed; stale decisions are shown; refused splits say why.

Skipped unless Playwright and its Chromium are installed (CI installs them on Linux)."""

import json
import shutil

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

from gpdf import cli  # noqa: E402
from gpdf.pipeline import structure_decisions as sd  # noqa: E402

from .test_sections import anthology, text_dir  # noqa: E402, F401
from .test_structure_decisions import DEEP_STATUS, deep_ops  # noqa: E402

NO_PICKER = "delete Window.prototype.showSaveFilePicker; delete window.showSaveFilePicker;"
ID = "e" * 64


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
def out(text_dir, tmp_path, capsys):
    out = tmp_path / "structure"
    assert cli.main(["structure", "--text", str(text_dir), "--out", str(out)]) == 0
    capsys.readouterr()
    return out


def open_sheet(browser, path, *init_scripts):
    ctx = browser.new_context(accept_downloads=True, viewport={"width": 1500, "height": 900})
    for script in init_scripts:
        ctx.add_init_script(script)
    page = ctx.new_page()
    page.errors = []
    page.on("pageerror", lambda e: page.errors.append(str(e)))
    page.on("console", lambda m: page.errors.append(m.text) if m.type == "error" else None)
    page.on("dialog", lambda d: d.accept())          # beforeunload on reload
    page.goto(path.as_uri())
    return page


def sec(page, start, level):
    return page.locator(f'tr.sec[data-key="{start - 1}:{level}"]')


def fill(locator, value):
    locator.fill(value)
    locator.dispatch_event("change")


def js_tree(page):
    return [tuple(x) for x in page.evaluate(f"gpdfStructure.tree('{ID}')")]


def py_tree(out):
    data = json.loads((out / "libro.sections.json").read_text("utf-8"))
    return [(x["first"], x["last"], x["level"], x["title"], x["author"], x["role"])
            for x in data["sections"]], data


def test_edit_move_merge_split_and_metadata_saved_and_applied_by_the_next_run(
        browser, out, text_dir, tmp_path, capsys):
    page = open_sheet(browser, out / sd.SHEET, NO_PICKER)
    assert page.inner_text("#progress") == "Reviewed 0 / 1"
    page.locator(f'tr.entry[data-id="{ID}"]').click()
    assert page.locator("tr.sec").count() == 11

    fill(sec(page, 16, 2).locator("input.a"), "Autora Una")
    sec(page, 21, 2).locator("button.move").click()
    sec(page, 21, 2).locator("select.move-to").select_option("20")
    sec(page, 12, 2).locator("button.merge").click()
    sec(page, 20, 2).locator("button.split").click()
    form = page.locator("tr.splitrow")
    fill(form.locator("input.seq"), "23")
    form.locator("input.seq").dispatch_event("input")
    assert "unit 23" in form.locator(".preview").inner_text()
    form.locator("select.split-level").select_option("3")
    form.locator("input.split-title").fill("Primera parte")
    form.locator("input.split-author").fill("")
    form.locator("select.split-role").select_option("criticism")
    form.locator("button.add-split").click()
    fill(page.locator('input[data-field="year"]'), "1999")
    page.locator("button.confirm-meta").click()

    shown = js_tree(page)
    assert (16, 19, 2, "Ensayo uno sobre la vanguardia", "Autora Una", "criticism") in shown
    assert (20, 27, 2, "Ensayo dos sobre el choteo", "Autor Dos", "criticism") in shown
    assert (23, 24, 3, "Primera parte", None, "criticism") in shown
    assert not any(t[3] == "Puerto Rico" for t in shown)
    assert page.evaluate(f"gpdfStructure.results('{ID}')") == ["applied"] * 5
    assert page.evaluate(f"gpdfStructure.status('{ID}')") == "reviewed"
    assert page.inner_text("#progress") == "Reviewed 1 / 1"
    assert page.locator("ul.ops li").count() == 4

    with page.expect_download() as dl:
        page.click("#save")
    assert dl.value.suggested_filename == sd.DECISIONS
    saved = tmp_path / "saved.json"
    dl.value.save_as(saved)
    assert not page.evaluate("gpdfStructure.dirty()")
    data = json.loads(saved.read_text("utf-8"))
    rec = data["entries"][ID]
    assert data["schema"] == 1 and [op["op"] for op in rec["ops"]] == [
        "edit", "move", "merge", "split"]
    assert rec["metadata"]["confirmed"] is True and rec["metadata"]["year"] == "1999"
    assert rec["metadata"]["title"] == "Las vanguardias en la isla"
    assert page.errors == []

    shutil.copy(saved, out / sd.DECISIONS)               # the owner keeps it in OUTDIR
    assert cli.main(["structure", "--text", str(text_dir), "--out", str(out)]) == 0
    assert "decisions: 5 applied, 0 stale, 0 unmatched" in capsys.readouterr().out
    tree, data = py_tree(out)
    assert tree == shown
    assert data["metadata"]["year"] == "1999" and data["metadata"]["confirmed"] is True

    page = open_sheet(browser, out / sd.SHEET)               # the rewritten sheet
    assert js_tree(page) == shown
    assert page.inner_text("#progress") == "Reviewed 1 / 1"
    assert page.errors == []


def test_deep_sections_replay_as_in_python(browser, out, text_dir, capsys):
    e = anthology()
    dec = {"schema": 1, "entries": {ID: {"ops": deep_ops(e)}}}
    (out / sd.DECISIONS).write_text(json.dumps(dec), "utf-8")
    assert cli.main(["structure", "--text", str(text_dir), "--out", str(out)]) == 0
    assert "decisions: 7 applied, 0 stale, 5 unmatched" in capsys.readouterr().out
    tree, data = py_tree(out)
    page = open_sheet(browser, out / sd.SHEET)
    assert js_tree(page) == tree
    assert (27, 27, 5, "Madrid", "Otro", "primary") in tree
    assert page.evaluate(f"gpdfStructure.results('{ID}')") == DEEP_STATUS
    page.locator(f'tr.entry[data-id="{ID}"]').click()
    shown = page.locator("ul.ops li").all_inner_texts()
    for line, r in zip(shown, data["decisions"]["results"], strict=True):
        assert r["why"] in line
    assert page.errors == []


def test_stale_decisions_are_shown_refused_ops_say_why_and_unsaved_work_restores(
        browser, out, text_dir):
    dec = {"schema": 1, "entries": {ID: {"ops": [
        {"op": "edit", "seq": 16, "level": 2, "hash": "0" * 10, "title": "Viejo"}]}}}
    (out / sd.DECISIONS).write_text(json.dumps(dec), "utf-8")
    assert cli.main(["structure", "--text", str(text_dir), "--out", str(out)]) == 0
    page = open_sheet(browser, out / sd.SHEET)
    assert page.evaluate(f"gpdfStructure.status('{ID}')") == "problems"
    assert "1 with stale" in page.inner_text("#progress")
    page.click('[data-filter="problems"]')
    assert page.locator("tr.entry").count() == 1
    page.locator(f'tr.entry[data-id="{ID}"]').click()
    li = page.locator("ul.ops li")
    assert li.locator(".badge.stale").count() == 1 and "export changed" in li.inner_text()

    sec(page, 8, 2).locator("button.split").click()          # Cuba starts at unit 8
    form = page.locator("tr.splitrow")
    fill(form.locator("input.seq"), "8")
    form.locator("select.split-level").select_option("2")
    form.locator("button.add-split").click()
    page.wait_for_selector("#msg.error")
    assert "a level-2 section already starts at unit 8" in page.inner_text("#msg")
    assert page.locator("ul.ops li").count() == 1

    li.locator("button.remove").click()
    assert page.locator("tr.entry").count() == 0              # no problems left to show
    page.click('[data-filter="all"]')
    page.locator("input.reviewed").check()
    assert page.evaluate(f"gpdfStructure.status('{ID}')") == "reviewed"
    page.reload()
    assert page.locator("#restore").is_visible()
    page.click("#restore-yes")
    assert json.loads(page.evaluate("gpdfStructure.decisionsText()"))["entries"] == {
        ID: {"reviewed": True}}
    assert page.errors == []
