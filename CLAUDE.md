# CLAUDE.md: working rules for g-pdf

g-pdf is a local PDF workstation: PySide6 (Qt) + PyMuPDF, AGPL-3.0-or-later.
`gpdf/core` holds the PDF logic, `gpdf/ui` the interface, `tests/` pytest.

## Environment (Windows, PowerShell)
- Activate: `.\.venv\Scripts\Activate.ps1` (Python 3.14). Install: `python -m pip install -e ".[dev]"`.
- Test: `python -m pytest -q` (Qt runs offscreen via `tests/conftest.py`). Run it before every commit.
- Launch: `python -m gpdf [files]`. Hand-test files live in `private/manual/`.
- `tests/realinput/` drives a real X11 mouse (xdotool under Xvfb). It runs only in CI and is
  skipped on Windows; add a test there for any new drag/drop or mouse gesture.
- PyMuPDF is pinned `<2` (2.x bundles onnxruntime + ML models). Don't add or upgrade
  dependencies without asking.

## Git
- Small commits: one fix plus its test. Commit on your own once the full suite passes;
  end with a list of commits (hash, what changed, tests added). Ask first only for:
  changes over ~30 lines without an approved plan, changes to the deliberate design
  choices below, dependency changes, deleting files. Don't push; the owner pushes and
  reviews CI (GitHub Actions: Windows suite + Linux real-mouse tests).
- Never stage `private/`, PDFs (except `tests/fixtures/`), tessdata, `*.traineddata` or
  binaries. Check `git status` before committing.
- `HANDOFF.md` and `REVIEW_PROMPT.md` are gitignored personal notes; don't commit them.

## Code
- New `.py` files start with `# SPDX-License-Identifier: AGPL-3.0-or-later`.
- For bugs, write a failing test first. Change only what the task asks; propose
  anything else instead of doing it. For changes over ~30 lines, state a 2–3 line plan
  and wait for approval.
- Keep `REVIEW_REPORT.md` tables up to date when a finding is fixed or a manual test runs.

## Things that bite
- `PdfDocument` is a QObject and not thread-safe: use it only from the GUI thread.
- Anything that changes a page's appearance must call `_touch` / `_page_changed` so
  `page_token` (the render cache key) changes.
- Views connect to document signals in `set_document` and must disconnect there.
- Page objects must keep their xref (bookmarks, links, named destinations and structure
  tags point at them). Restore page content with `_restore_page`; `DeleteCommand` unlinks
  and relinks the same objects. This uses PyMuPDF privates (`_delete_page`,
  `_remove_toc_item`, `_reset_page_refs`, `mupdf.pdf_insert_page`); covered by tests,
  re-check if the PyMuPDF pin changes.
- Tests monkeypatch `QMessageBox`; a new modal dialog on a tested code path will hang the
  suite. Keep a Python reference to any `QMimeData` passed to a `QDropEvent`, or it segfaults.
- Read-aloud must stay fully offline: no network or AI in the speech path.

## Deliberate design choices (challenge in REVIEW_REPORT.md, don't change silently)
Memory-backed open (no file lock), cross-document page drag = copy, per-document undo,
text editing refused on rotated pages.
