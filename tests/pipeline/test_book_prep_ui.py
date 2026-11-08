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

