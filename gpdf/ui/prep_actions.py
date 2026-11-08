# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Book Prep menu's actions: progress dialogs, the split panel and the hand-off to
the browser review sheet. The work itself runs in book_prep jobs (worker threads)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QEvent, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog, QFileDialog, QHBoxLayout, QLabel, QMessageBox, QProgressDialog, QPushButton,
    QVBoxLayout, QWidget,
)

from gpdf import APP_NAME
from gpdf.ui import book_prep


# -- dialogs (module-level so tests can replace them) --------------------------------

def ask_yes_no(parent: QWidget, text: str) -> bool:
    answer = QMessageBox.question(parent, APP_NAME, text,
                                  QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                  QMessageBox.StandardButton.No)
    return answer == QMessageBox.StandardButton.Yes


def ask_replace(parent: QWidget, paths) -> bool:
    names = "\n".join(f"  {p.name}" for p in paths)
    return ask_yes_no(parent, f"These files already exist in {paths[0].parent}:\n{names}\n\n"
                              "Replace them?")


def show_message(parent: QWidget, text: str) -> None:
    QMessageBox.information(parent, APP_NAME, text)


def show_error(parent: QWidget, text: str) -> None:
    QMessageBox.warning(parent, APP_NAME, text)


def choose_decisions_file(parent: QWidget, start: Path) -> Path | None:
    path, _ = QFileDialog.getOpenFileName(parent, "Choose the decisions file", str(start),
                                          "Decisions files (*.json);;All files (*)")
    return Path(path) if path else None


def open_in_browser(path: Path) -> None:
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


def _pages(nums) -> str:
    return ", ".join(str(n) for n in nums)


class BookPrep:
    """The Book Prep actions of one window. At most one job runs at a time."""

    def __init__(self, window) -> None:
        self.window = window
        self.job: book_prep.Job | None = None
        self._progress: QProgressDialog | None = None
        self.panels: dict[Path, SplitPanel] = {}

    @property
    def busy(self) -> bool:
        return self.job is not None

    def shutdown(self) -> None:
        """Cancel a running job and wait for it (the window is closing)."""
        if self.job is not None:
            self.job.cancel()
            self.job.wait()
        for panel in list(self.panels.values()):
            panel.close()

    # -- running jobs --------------------------------------------------------------

    def start(self, title: str, fn, on_done) -> None:
        """Run ``fn(tick)`` in a worker with a progress dialog; ``on_done(result)`` runs on
        the GUI thread when it succeeds. Failures are shown; Cancel just stops."""
        if self.busy:
            show_error(self.window, "Book prep is already running.")
            return
        job = book_prep.Job(fn, self.window)
        dlg = QProgressDialog(title, "Cancel", 0, 0, self.window)
        dlg.setWindowTitle(APP_NAME)
        dlg.setWindowModality(Qt.WindowModality.WindowModal)
        dlg.setMinimumDuration(0)
        dlg.setAutoClose(False)
        dlg.setAutoReset(False)
        dlg.canceled.connect(job.cancel)

        def progress(label, done, total):
            dlg.setLabelText(f"{title}\n{label}: {done} of {total}")
            dlg.setMaximum(total)
            dlg.setValue(done)

        def finish():
            dlg.canceled.disconnect(job.cancel)
            dlg.close()
            dlg.deleteLater()
            job.wait()
            job.deleteLater()
            self.job = self._progress = None

        def succeeded(result):
            finish()
            on_done(result)

        def failed(message):
            finish()
            show_error(self.window, f"{title} failed: {message}")

        job.progress.connect(progress)
        job.succeeded.connect(succeeded)
        job.failed.connect(failed)
        job.cancelled.connect(finish)
        self.job, self._progress = job, dlg
        dlg.show()
        job.start()

    def open_result(self, path: Path):
        """Show ``path`` in a tab: a new one, or the open one reloaded from disk."""
        from gpdf.ui.doc_tab import DocumentTab
        from gpdf.ui.main_window import MainWindow

        resolved = Path(path).resolve()
        for window in MainWindow.windows():
            for i in range(window.tabs.count()):
                tab = window.tabs.widget(i)
                if isinstance(tab, DocumentTab) and tab.path.resolve() == resolved:
                    if tab.document.is_dirty:
                        show_error(self.window, f"{resolved.name} is open with unsaved changes, "
                                                "so it was not reloaded from disk.")
                        return window.open_path(resolved)
                    window.reload_tab(tab)
                    return window.open_path(resolved)
        return self.window.open_path(resolved)

    # -- Check OCR ---------------------------------------------------------------------

    def check_ocr(self) -> None:
        tab = self.window.current_tab()
        if tab is None:
            return
        report = book_prep.find_ocr_report(tab.path)
        if report is None:
            name = f"{tab.path.stem.removesuffix('_ocr')}_ocr.report.json"
            show_message(self.window, f"No OCR report found for {tab.path.name}: looked for "
                                      f"{name} next to it and in "
                                      f"{book_prep.prep_folder(tab.path).name}.")
            return
        tab.show_ocr_check(report)

    # -- Split Spreads ---------------------------------------------------------------

    def split_spreads(self) -> None:
        tab = self.window.current_tab()
        if tab is None or self.busy:
            return
        src = book_prep.ensure_saved(tab)
        if src is None:
            return
        self.dry_run(src)

    def dry_run(self, src: Path) -> None:
        files = book_prep.split_files(src)
        existing = [p for p in files.dry_run_outputs if p.exists()]
        if existing and not ask_replace(self.window, existing):
            return
        old = self.panels.pop(src.resolve(), None)
        if old is not None:
            old.close()
        self.start(f"Split spreads: {src.name}",
                   lambda tick: book_prep.run_dry_run(src, files, tick),
                   lambda res: self._after_dry_run(src, res))

    def _after_dry_run(self, src: Path, res: book_prep.DryRun) -> None:
        open_in_browser(res.files.sheet)
        panel = SplitPanel(self, src, res)
        self.panels[src.resolve()] = panel
        panel.show()

    def apply_split(self, panel: SplitPanel, decisions: Path, accept_flagged=False) -> None:
        src, files = panel.src, panel.files
        if not accept_flagged and files.out.exists() and not ask_replace(panel, [files.out]):
            return
        self.start(f"Split spreads: {src.name}",
                   lambda tick: book_prep.run_split(src, files, decisions, accept_flagged, tick),
                   lambda report: self._after_split(panel, decisions, report, accept_flagged))

    def _after_split(self, panel, decisions, report, accepted) -> None:
        if report.unreviewed and not accepted:
            pages = [p.page for p in report.unreviewed]
            if ask_yes_no(panel, f"{len(pages)} flagged page(s) were not reviewed and stayed "
                                 f"whole: p. {_pages(pages)}.\n\nSplit them by the detector's "
                                 "guess instead?"):
                self.apply_split(panel, decisions, accept_flagged=True)
                return
        kept = report.pages_in - len(report.spreads)
        self.window.statusBar().showMessage(
            f"Split {Path(report.source).name}: {report.pages_in} -> {report.pages_out} pages, "
            f"{kept} kept whole", 15000)
        self.panels.pop(panel.src.resolve(), None)
        panel.close()
        self.open_result(panel.files.out)


class SplitPanel(QDialog):
    """Non-modal: after the dry run, while the owner reviews in the browser."""

    def __init__(self, prep: BookPrep, src: Path, res: book_prep.DryRun) -> None:
        super().__init__(prep.window)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setModal(False)
        self.prep, self.src, self.files = prep, Path(src), res.files
        self.chosen: Path | None = None            # picked with Choose another...
        self.setWindowTitle(f"Split spreads: {self.src.name}")
        rep = res.report
        flagged = len(rep.flagged)
        summary = (f"{rep.pages_in} pages: {len(rep.spreads)} spreads, "
                   f"{rep.pages_in - len(rep.spreads)} singles, {flagged} flagged for review.")
        if res.backup:
            summary += f"\nThe old decisions file held a review: copied to {res.backup.name}."
        how = (f"The review sheet is open in your browser. Review the flagged pages, then press "
               f"Save decisions and save {self.files.decisions.name} in\n{self.files.folder}\n"
               "(or leave it in your Downloads folder). Then come back and press Apply split.")
        self.summary = QLabel(summary)
        self.how = QLabel(how)
        self.how.setWordWrap(True)
        self.how.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.found = QLabel()
        self.found.setWordWrap(True)
        self.found.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        self.b_sheet = QPushButton("Open sheet again")
        self.b_choose = QPushButton("Choose another…")
        self.b_apply = QPushButton("Apply split")
        self.b_cancel = QPushButton("Cancel")
        self.b_apply.setDefault(True)
        self.b_sheet.clicked.connect(lambda: open_in_browser(self.files.sheet))
        self.b_choose.clicked.connect(self.choose)
        self.b_apply.clicked.connect(self.apply)
        self.b_cancel.clicked.connect(self.close)

        buttons = QHBoxLayout()
        for b in (self.b_sheet, self.b_choose):
            buttons.addWidget(b)
        buttons.addStretch(1)
        for b in (self.b_apply, self.b_cancel):
            buttons.addWidget(b)
        layout = QVBoxLayout(self)
        for w in (self.summary, self.how, self.found):
            layout.addWidget(w)
        layout.addLayout(buttons)
        self.resize(560, 0)
        self.refresh()

    def event(self, ev) -> bool:
        if ev.type() == QEvent.Type.WindowActivate:   # back from the browser: look again
            self.refresh()
        return super().event(ev)

    def closeEvent(self, ev) -> None:
        if self.prep.panels.get(self.src.resolve()) is self:
            del self.prep.panels[self.src.resolve()]
        super().closeEvent(ev)

    def decisions_path(self) -> Path | None:
        if self.chosen is not None and self.chosen.is_file():
            return self.chosen
        return book_prep.find_decisions(self.src)

    def refresh(self) -> None:
        from gpdf.pipeline.report import Decisions, DecisionsError

        path = self.decisions_path()
        if path is None:
            self.found.setText("No decisions file found yet (looked in the prep folder and "
                               "Downloads).")
            return
        when = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        text = f"Decisions file: {path}\nsaved {when}"
        try:
            dec = Decisions.load(path)
        except DecisionsError as e:
            text += f"\ncannot be read: {e}"
        else:
            flagged = [d for d in dec.pages if d.detected.flagged]
            text += (f", {sum(d.reviewed for d in flagged)} of {len(flagged)} flagged pages "
                     "reviewed")
        self.found.setText(text)

    def choose(self) -> None:
        path = choose_decisions_file(self, self.files.folder)
        if path is not None:
            self.chosen = path
            self.refresh()

    def apply(self) -> None:
        from gpdf.pipeline import split
        from gpdf.pipeline.report import Decisions, DecisionsError

        path = self.decisions_path()
        if path is None:
            path = choose_decisions_file(self, book_prep.downloads_folder())
            if path is None:
                return
            self.chosen = path
            self.refresh()
        try:
            dec = Decisions.load(path)
        except DecisionsError as e:
            show_error(self, f"{path.name} cannot be used: {e}")
            return
        if (dec.source_size, dec.source_mtime_ns) != split.stamp(self.src):
            if ask_yes_no(self, f"{self.src.name} has changed since the review sheet was made "
                                f"(its size or modification time differ), so {path.name} may "
                                "not match its pages.\n\nRun the dry run again? You will review "
                                "the flagged pages again in a new sheet."):
                self.close()
                self.prep.dry_run(self.src)
            return
        self.prep.apply_split(self, path)
