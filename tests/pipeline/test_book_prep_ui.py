# SPDX-License-Identifier: AGPL-3.0-or-later
"""Book Prep in the window: prep folder, decisions lookup, worker jobs, unsaved guard."""

import os
import time

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("PIL")
import pymupdf  # noqa: E402

from gpdf.ui import book_prep  # noqa: E402

from . import synth  # noqa: E402


@pytest.fixture(scope="module")
def spreads_pdf(tmp_path_factory):
    """Page 1: a clean 1-bit spread; page 2: a masked spread with visible text (flagged)."""
    path = tmp_path_factory.mktemp("prep") / "book.pdf"
    doc = pymupdf.open()
    synth.add_one_bit_spread(doc, "clean", shadow="solid")
    synth.add_masked_spread(doc)
    doc[1].insert_text((60, 60), "visible", fontname="tiro", fontsize=9)
    doc.save(str(path))
    return path


@pytest.fixture
def src(spreads_pdf, tmp_path):
    """A private copy of the spreads file."""
    p = tmp_path / "book.pdf"
    p.write_bytes(spreads_pdf.read_bytes())
    return p


def wait(qapp, cond, timeout=120):
    end = time.monotonic() + timeout
    while not cond():
        assert time.monotonic() < end, "timed out"
        qapp.processEvents()
        time.sleep(0.005)
    for _ in range(3):
        qapp.processEvents()


def run_job(qapp, fn):
    """Run fn(tick) in a Job; returns (signal name, value)."""
    job = book_prep.Job(fn)
    got = []
    job.succeeded.connect(lambda r: got.append(("succeeded", r)))
    job.failed.connect(lambda m: got.append(("failed", m)))
    job.cancelled.connect(lambda: got.append(("cancelled", None)))
    job.start()
    wait(qapp, lambda: got)
    job.wait()
    return got[0], job


# -- paths ---------------------------------------------------------------------------

def test_prep_folder_is_next_to_the_source_or_the_prep_folder_itself(tmp_path):
    assert book_prep.prep_folder(tmp_path / "vt.pdf") == tmp_path / "vt_prep"
    inside = tmp_path / "vt_prep" / "vt_split.pdf"
    assert book_prep.prep_folder(inside) == tmp_path / "vt_prep"
    f = book_prep.split_files(tmp_path / "vt.pdf")
    assert [p.name for p in (f.decisions, f.report, f.sheet, f.out)] == [
        "vt_split.decisions.json", "vt_split.report.json", "vt_split.review.html",
        "vt_split.pdf"]
    assert [p.name for p in book_prep.ocr_files(inside)] == [
        "vt_split_ocr.pdf", "vt_split_ocr.report.json"]


def test_ocr_report_is_found_next_to_the_file_or_in_its_prep_folder(tmp_path):
    prep = tmp_path / "vt_prep"
    prep.mkdir()
    assert book_prep.find_ocr_report(tmp_path / "vt.pdf") is None
    (prep / "vt_ocr.report.json").write_text("{}")
    assert book_prep.find_ocr_report(tmp_path / "vt.pdf") == prep / "vt_ocr.report.json"
    assert book_prep.find_ocr_report(prep / "vt_ocr.pdf") == prep / "vt_ocr.report.json"


def test_newest_decisions_file_wins_including_numbered_downloads(tmp_path):
    src, downloads = tmp_path / "vt.pdf", tmp_path / "Downloads"
    downloads.mkdir()
    assert book_prep.find_decisions(src, downloads) is None
    prep = book_prep.split_files(src).decisions
    prep.parent.mkdir()
    files = [prep, downloads / "vt_split.decisions.json",
             downloads / "vt_split.decisions (1).json", downloads / "vt_split.decisions (2).json",
             downloads / "other_split.decisions (3).json"]
    for k, p in enumerate(files):
        p.write_text("{}")
        os.utime(p, ns=(10**18 + k * 10**9,) * 2)
    os.utime(files[2], ns=(10**18 + 9 * 10**9,) * 2)      # (1) saved last
    assert book_prep.find_decisions(src, downloads) == files[2]
    os.utime(prep, ns=(10**18 + 20 * 10**9,) * 2)         # saved over the dry run's file
    assert book_prep.find_decisions(src, downloads) == prep


# -- jobs ----------------------------------------------------------------------------

def test_job_reports_result_failure_and_cancel_through_signals(qapp):
    (kind, value), _ = run_job(qapp, lambda tick: (tick("x", 1, 1), 42)[1])
    assert (kind, value) == ("succeeded", 42)

    def boom(tick):
        raise OSError("disk full")
    assert run_job(qapp, boom)[0] == ("failed", "disk full")

    def slow(tick):
        for k in range(1000):
            tick("x", k, 1000)
            time.sleep(0.01)
    job = book_prep.Job(slow)
    got = []
    job.cancelled.connect(lambda: got.append("cancelled"))
    job.start()
    job.cancel()
    wait(qapp, lambda: got)
    job.wait()
    assert got == ["cancelled"]


def test_dry_run_job_writes_the_cli_files(qapp, src):
    files = book_prep.split_files(src)
    (kind, res), _ = run_job(qapp, lambda tick: book_prep.run_dry_run(src, files, tick))
    assert kind == "succeeded", res
    assert all(p.is_file() for p in files.dry_run_outputs)
    assert res.backup is None and len(res.report.flagged) == 1
    assert res.report.flagged[0].page == 2


def test_cancelled_dry_run_leaves_no_output(qapp, src):
    files = book_prep.split_files(src)

    def cancel_in_sheet(tick):
        def t(label, d, n):
            if label.startswith("Writing"):
                raise book_prep.Cancelled
            tick(label, d, n)
        return book_prep.run_dry_run(src, files, t)
    assert run_job(qapp, cancel_in_sheet)[0][0] == "cancelled"
    assert not files.folder.exists()


# -- unsaved changes -------------------------------------------------------------------

class FakeTab:
    def __init__(self, path, dirty, saves=True):
        self.path, self.saved = path, []
        self.document = type("D", (), {"is_dirty": dirty, "name": path.name})()
        self._saves = saves

    def save(self):
        self.saved.append(True)
        return self._saves


def test_unsaved_tab_is_saved_first_or_book_prep_stops(monkeypatch, tmp_path):
    p = tmp_path / "a.pdf"
    assert book_prep.ensure_saved(FakeTab(p, dirty=False)) == p
    asked = []
    monkeypatch.setattr(book_prep, "ask_save_first", lambda w, n: asked.append(n) or False)
    tab = FakeTab(p, dirty=True)
    assert book_prep.ensure_saved(tab) is None and asked == ["a.pdf"] and not tab.saved
    monkeypatch.setattr(book_prep, "ask_save_first", lambda w, n: True)
    tab = FakeTab(p, dirty=True)
    assert book_prep.ensure_saved(tab) == p and tab.saved
    assert book_prep.ensure_saved(FakeTab(p, dirty=True, saves=False)) is None


# -- Split Spreads from the window -------------------------------------------------------

@pytest.fixture
def window(qapp, monkeypatch, tmp_path):
    """A window with the browser and Downloads replaced; yields (window, opened, downloads)."""
    from gpdf.ui import prep_actions
    from gpdf.ui.main_window import MainWindow

    opened = []
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    monkeypatch.setattr(prep_actions, "open_in_browser", opened.append)
    monkeypatch.setattr(book_prep, "downloads_folder", lambda: downloads)
    w = MainWindow()
    yield w, opened, downloads
    for win in MainWindow.windows():
        win.close()
    for _ in range(3):
        qapp.processEvents()


def dry_run_in(qapp, w, src):
    w.open_path(src)
    w.book_prep.split_spreads()
    assert w.book_prep.busy
    wait(qapp, lambda: not w.book_prep.busy)
    return w.book_prep.panels[src.resolve()]


def review(files, dest, mtime_s=None):
    """The dry run's decisions with every flagged page reviewed (split), saved at dest."""
    from gpdf.pipeline.report import Decisions

    dec = Decisions.load(files.decisions)
    for d in dec.pages:
        d.reviewed = True
    dec.save(dest)
    if mtime_s:
        os.utime(dest, (mtime_s, mtime_s))
    return dest


def test_split_spreads_dry_run_sheet_review_apply_opens_the_result(qapp, window, src):
    w, opened, downloads = window
    panel = dry_run_in(qapp, w, src)
    files = book_prep.split_files(src)
    assert opened == [files.sheet] and all(p.is_file() for p in files.dry_run_outputs)
    assert "0 of 1 flagged pages reviewed" in panel.found.text()

    # the browser saved a repeat download; it is newer than the dry run's file
    saved = review(files, downloads / "book_split.decisions (1).json",
                   files.decisions.stat().st_mtime + 60)
    panel.refresh()
    assert str(saved) in panel.found.text() and "1 of 1 flagged" in panel.found.text()
    panel.b_sheet.click()
    assert opened == [files.sheet] * 2

    panel.apply()
    wait(qapp, lambda: not w.book_prep.busy)
    tab = w.current_tab()
    assert tab.path == files.out.resolve() and tab.document.page_count == 4
    assert src.resolve() not in w.book_prep.panels


@pytest.mark.parametrize("accept, pages", [(True, 4), (False, 3)])
def test_unreviewed_flagged_page_stays_whole_unless_the_guess_is_accepted(
        qapp, window, src, monkeypatch, accept, pages):
    from gpdf.ui import prep_actions

    w, _, _ = window
    panel = dry_run_in(qapp, w, src)
    asked = []
    monkeypatch.setattr(prep_actions, "ask_yes_no", lambda p, t: asked.append(t) or accept)
    panel.apply()                                   # the dry run's own, unreviewed file
    wait(qapp, lambda: not w.book_prep.busy)
    assert len(asked) == 1 and "p. 2" in asked[0] and "detector's guess" in asked[0]
    assert w.current_tab().document.page_count == pages


def test_changed_source_is_refused_plainly_with_a_rerun_offer(qapp, window, src, monkeypatch):
    from gpdf.ui import prep_actions

    w, _, _ = window
    panel = dry_run_in(qapp, w, src)
    st = src.stat()
    os.utime(src, ns=(st.st_atime_ns, st.st_mtime_ns + 5 * 10**9))
    asked = []
    monkeypatch.setattr(prep_actions, "ask_yes_no", lambda p, t: asked.append(t) or False)
    panel.apply()
    assert not w.book_prep.busy and len(asked) == 1
    assert "has changed since the review sheet was made" in asked[0]
    assert not book_prep.split_files(src).out.exists()


def test_existing_outputs_are_replaced_only_when_confirmed(qapp, window, src, monkeypatch):
    from gpdf.ui import prep_actions

    w, _, _ = window
    dry_run_in(qapp, w, src)
    asked = []
    monkeypatch.setattr(prep_actions, "ask_replace", lambda p, paths: asked.append(paths) or False)
    w.book_prep.split_spreads()
    assert not w.book_prep.busy
    assert [p.name for p in asked[0]] == [
        "book_split.decisions.json", "book_split.report.json", "book_split.review.html"]


def test_no_decisions_file_asks_for_one(qapp, window, src, monkeypatch):
    from gpdf.ui import prep_actions

    w, _, _ = window
    panel = dry_run_in(qapp, w, src)
    book_prep.split_files(src).decisions.unlink()
    panel.refresh()
    assert "No decisions file found yet" in panel.found.text()
    asked = []
    monkeypatch.setattr(prep_actions, "choose_decisions_file",
                        lambda p, start: asked.append(start))
    panel.apply()
    assert asked and not w.book_prep.busy


# -- OCR Document from the window ----------------------------------------------------------

TSV_HEAD = ("level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\t"
            "height\tconf\ttext\n")


@pytest.fixture
def fake_tesseract(monkeypatch, tmp_path):
    """Tesseract replaced: probe lists eng/spa/osd; a page reads as one invisible word
    "fakeN" with confidence 90 - 20 * N (page N, 1-based)."""
    from PIL import Image

    from gpdf.pipeline import tesseract

    tess = tesseract.Tesseract("tesseract", "5.4.0", str(tmp_path / "tessdata"),
                               ["eng", "osd", "spa"])
    monkeypatch.setattr(tesseract, "find", lambda explicit=None: "tesseract")
    monkeypatch.setattr(tesseract, "probe", lambda exe: tess)
    monkeypatch.setattr(tesseract, "check", lambda t, langs: None)
    seen = []

    def recognize(t, image, outbase, langs, dpi, timeout=0):
        n = int(str(outbase)[-5:])
        seen.append((n, langs))
        with Image.open(image) as im:
            w, h = im.width * 72 / dpi, im.height * 72 / dpi
        d = pymupdf.open()
        d.new_page(width=w, height=h).insert_text((20, 40), f"fake{n}", render_mode=3)
        d.save(f"{outbase}.pdf")
        with open(f"{outbase}.tsv", "w", encoding="utf-8") as f:
            f.write(TSV_HEAD + f"5\t1\t1\t1\t1\t1\t20\t30\t40\t12\t{90 - 20 * n}\tfake{n}\n")
        return ""

    monkeypatch.setattr(tesseract, "recognize", recognize)
    return seen


def test_ocr_document_opens_the_result_with_the_check_panel(
        qapp, window, src, fake_tesseract, monkeypatch):
    from gpdf.ui import prep_actions

    w, _, _ = window
    asked = []
    monkeypatch.setattr(prep_actions, "ask_languages",
                        lambda p, langs, chosen: asked.append((langs, chosen)) or "eng+spa")
    w.open_path(src)
    w.book_prep.ocr_document()
    wait(qapp, lambda: not w.book_prep.busy)
    assert asked == [(["eng", "spa"], ["eng"])]               # osd is not a language
    out, report = book_prep.ocr_files(src)
    assert report.is_file() and fake_tesseract == [(1, "eng+spa")]   # p. 2 has text
    tab = w.current_tab()
    assert tab.path == out.resolve() and tab.ocr_check is not None
    assert tab.sidebar.currentWidget() is tab.ocr_check
    assert tab.ocr_check.pages_in_order() == [1]
    assert "1 has text" in tab.ocr_check.summary.text()
    assert tab.ocr_check.text.toPlainText() == "fake1"

    # again: the outputs exist, the last languages are offered, replacing is asked
    replaced = []
    monkeypatch.setattr(prep_actions, "ask_replace",
                        lambda p, paths: replaced.append(paths) or False)
    w.tabs.setCurrentIndex(0)
    w.book_prep.ocr_document()
    assert asked[-1] == (["eng", "spa"], ["eng", "spa"])
    assert [p.name for p in replaced[0]] == ["book_ocr.pdf", "book_ocr.report.json"]
    assert not w.book_prep.busy


def test_ocr_document_without_tesseract_shows_the_install_hint(qapp, window, src, monkeypatch):
    from gpdf.core.ocr import INSTALL_HINT
    from gpdf.pipeline import tesseract
    from gpdf.ui import prep_actions

    def missing(explicit=None):
        raise tesseract.TesseractError("Tesseract not found")
    monkeypatch.setattr(tesseract, "find", missing)
    shown = []
    monkeypatch.setattr(prep_actions, "show_error", lambda p, t: shown.append(t))
    w, _, _ = window
    w.open_path(src)
    w.book_prep.ocr_document()
    assert shown == [INSTALL_HINT] and not w.book_prep.busy


def test_cancelled_ocr_leaves_no_output(qapp, src, fake_tesseract):
    from gpdf.pipeline import tesseract

    out, report = book_prep.ocr_files(src)
    tess = tesseract.probe("tesseract")

    def cancel_after_first(tick):
        def t(label, d, n):
            if d >= 1:
                raise book_prep.Cancelled
            tick(label, d, n)
        return book_prep.run_ocr(src, out, report, "eng", tess, t)
    assert run_job(qapp, cancel_after_first)[0][0] == "cancelled"
    assert not out.exists() and not report.exists()
    assert not list(out.parent.glob(".gpdf-*"))
