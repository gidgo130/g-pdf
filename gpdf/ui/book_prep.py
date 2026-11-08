# SPDX-License-Identifier: AGPL-3.0-or-later
"""Book prep from the window: split spreads and OCR the current tab's file on disk.

The pipeline (gpdf.pipeline) runs in a worker thread on its OWN PyMuPDF document opened
from the path; the tab's PdfDocument is never touched off the GUI thread. A job reports
only through Qt signals. Cancel sets a flag that the job's progress callback turns into
``Cancelled``, raised inside the pipeline, which then cleans up its temporary files.

Outputs go in ``<stem>_prep`` next to the source (a file already inside a ``*_prep``
folder writes next to itself), with the file names gpdf-cli writes.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QStandardPaths, QThread, Signal
from PySide6.QtWidgets import QMessageBox, QWidget

from gpdf import APP_NAME

PREP_SUFFIX = "_prep"


# -- where things go ---------------------------------------------------------------

def prep_folder(src) -> Path:
    src = Path(src)
    if src.parent.name.endswith(PREP_SUFFIX):
        return src.parent
    return src.with_name(src.stem + PREP_SUFFIX)


@dataclass
class SplitFiles:
    folder: Path
    decisions: Path
    report: Path
    sheet: Path
    out: Path

    @property
    def dry_run_outputs(self) -> list[Path]:
        return [self.decisions, self.report, self.sheet]


def split_files(src) -> SplitFiles:
    folder, stem = prep_folder(src), Path(src).stem
    return SplitFiles(folder, folder / f"{stem}_split.decisions.json",
                      folder / f"{stem}_split.report.json",
                      folder / f"{stem}_split.review.html", folder / f"{stem}_split.pdf")


def ocr_files(src) -> tuple[Path, Path]:
    """(output PDF, report) of OCR Document."""
    folder, stem = prep_folder(src), Path(src).stem
    return folder / f"{stem}_ocr.pdf", folder / f"{stem}_ocr.report.json"


def find_ocr_report(path) -> Path | None:
    """The OCR report for an open file: <stem>_ocr.report.json next to it or in its prep
    folder, where <stem> drops a trailing "_ocr" (the report of x_ocr.pdf is
    x_ocr.report.json)."""
    path = Path(path)
    name = f"{path.stem.removesuffix('_ocr')}_ocr.report.json"
    for folder in (path.parent, prep_folder(path)):
        if (folder / name).is_file():
            return folder / name
    return None


def downloads_folder() -> Path:
    loc = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation)
    return Path(loc) if loc else Path.home() / "Downloads"


def find_decisions(src, downloads=None) -> Path | None:
    """The newest decisions file for ``src``: the prep folder's <stem>_split.decisions.json
    or, in Downloads, <stem>_split.decisions*.json (browsers save repeats as
    "... (1).json")."""
    files = split_files(src)
    found = [files.decisions] if files.decisions.is_file() else []
    folder = Path(downloads) if downloads is not None else downloads_folder()
    if folder.is_dir():
        found += [p for p in folder.glob(f"{Path(src).stem}_split.decisions*.json")
                  if p.is_file()]
    return max(found, key=lambda p: p.stat().st_mtime_ns, default=None)


# -- jobs ----------------------------------------------------------------------------

class Cancelled(Exception):
    """Raised inside a job when the user pressed Cancel."""


def error_text(exc: BaseException) -> str:
    """One line for the user: the message of the pipeline's own errors, else the type."""
    from gpdf.pipeline.report import DecisionsError

    known = (DecisionsError, OSError)
    try:
        from gpdf.pipeline import ocr, split, tesseract
        known += (split.SplitError, ocr.OcrError, tesseract.TesseractError)
    except ImportError:
        pass
    if isinstance(exc, known):
        return str(exc)
    return f"unexpected error: {type(exc).__name__}: {exc}"


class Job(QThread):
    """Runs ``fn(tick)`` in a worker thread. ``tick(label, done, total)`` reports progress
    and raises Cancelled after cancel(). Results arrive through the signals only."""

    progress = Signal(str, int, int)
    succeeded = Signal(object)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, fn, parent=None) -> None:
        super().__init__(parent)
        self._fn = fn
        self._stop = threading.Event()

    def cancel(self) -> None:
        self._stop.set()

    def _tick(self, label: str, done: int, total: int) -> None:
        if self._stop.is_set():
            raise Cancelled
        self.progress.emit(label, done, total)

    def run(self) -> None:
        try:
            result = self._fn(self._tick)
        except Cancelled:
            self.cancelled.emit()
        except Exception as exc:  # noqa: BLE001 - reported to the user, not raised
            self.failed.emit(error_text(exc))
        else:
            self.succeeded.emit(result)


@dataclass
class DryRun:
    report: object                 # SplitReport (dry run)
    files: SplitFiles
    backup: Path | None            # copy of a replaced decisions file that held a review


def run_dry_run(src, files: SplitFiles, tick) -> DryRun:
    """Detect, write the review sheet, then the decisions file and report (worker thread).
    A cancel before the end leaves no new files."""
    import pymupdf

    from gpdf.cli import _backup_review
    from gpdf.pipeline import review, split

    decisions, report = split.dry_run(src, progress=lambda d, t: tick("Finding spreads", d, t))
    made = not files.folder.exists()
    files.folder.mkdir(parents=True, exist_ok=True)
    try:
        with pymupdf.open(src) as doc:
            review.write_review_sheet(doc, decisions, files.sheet, Path(src).stem,
                                      lambda d, t: tick("Writing the review sheet", d, t))
    except Cancelled:
        if made and not any(files.folder.iterdir()):
            files.folder.rmdir()
        raise
    backup = _backup_review(files.decisions, decisions) if files.decisions.exists() else None
    decisions.save(files.decisions)
    report.save(files.report)
    return DryRun(report, files, backup)


def run_split(src, files: SplitFiles, decisions, accept_flagged, tick):
    """Split with a reviewed decisions file (worker thread). The caller has checked the
    decisions file's source stamp and confirmed replacing the output."""
    from gpdf.pipeline import split

    return split.split_document(
        src, files.out, decisions=decisions, accept_flagged=accept_flagged, force=True,
        progress=lambda d, t: tick("Finding spreads", d, t),
        split_progress=lambda d, t: tick("Splitting", d, t))


def run_ocr(src, out: Path, report_path: Path, langs: str, tess, tick):
    """OCR into ``out`` and write the report (worker thread). The caller has confirmed
    replacing existing outputs. A cancel waits for the pages Tesseract is reading, then
    leaves no output."""
    from gpdf.pipeline import ocr

    out.parent.mkdir(parents=True, exist_ok=True)
    report = ocr.ocr_document(src, out, langs, tess=tess, force=True,
                              progress=lambda d, t: tick("Reading pages", d, t))
    report.save(report_path)
    return report


def pipeline_missing() -> str | None:
    """Why book prep cannot run (the optional numpy/Pillow extra is missing), or None."""
    try:
        import numpy  # noqa: F401
        import PIL  # noqa: F401
    except ImportError:
        from gpdf.pipeline import PREP_HINT
        return f"Book prep {PREP_HINT}"
    return None


# -- dialogs (module-level so tests can replace them) --------------------------------

def ask_save_first(parent: QWidget, name: str) -> bool:
    """The tab has unsaved changes; True to save them and go on."""
    answer = QMessageBox.question(
        parent, APP_NAME,
        f"{name} has unsaved changes. Book prep works on the file on disk: save it first?",
        QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel,
        QMessageBox.StandardButton.Save)
    return answer == QMessageBox.StandardButton.Save


def ensure_saved(tab) -> Path | None:
    """The tab's file on disk, saved first if the user agrees; None to stop."""
    if tab.document.is_dirty:
        if not ask_save_first(tab, tab.document.name) or not tab.save():
            return None
    return tab.path
