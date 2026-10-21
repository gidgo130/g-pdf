# Handoff: merging the "book prep" pipeline into g-pdf

Written 2026-10-20 from a chat that (1) built and tested a Colab book-prep script and (2) then read the g-pdf repo.
Everything marked **[measured]** was run; **[read]** came from reading the repo; **[untested]** is a guess.

## 1. Goal
One tool, three front ends over the same core: GUI (existing g-pdf), CLI, and batch.
Pipeline: detect what a PDF needs -> OCR (only if scanned) -> bookmarks (auto-detected chapters) -> metadata -> optional compression -> page-marked text export.

## 2. What exists in g-pdf [read]
Read: README, pyproject, requirements, REVIEW_NOTES, `__main__`, `core/ocr.py`, `core/document.py` (outline), `core/commands.py`, `core/history.py` (outline), `core/speech_text.py` (outline), `ui/bookmarks.py`, `ui/properties_dialog.py`, `tests/conftest.py`, `tests/MANUAL_TESTS.md`.
**Not read:** `ui/main_window.py`, `doc_tab.py`, `page_panel.py`, `pdf_view.py`, `read_aloud.py`, `REVIEW_PROMPT.md`, most tests.
- Stack: PySide6 + PyMuPDF, Python >=3.10 (venv uses 3.14), AGPL-3.0-or-later, v0.2.0. Entry: `python -m gpdf`, script `g-pdf`.
- `core/` = PDF logic, but `PdfDocument` is a `QObject`; `__main__.py` imports PySide6 at top level -> a CLI cannot reuse it without Qt.
- Undoable commands (`commands.py`), history capped at 256 MB in memory.
- `core/ocr.py`: per-page OCR via PyMuPDF (`pdfocr_tobytes`), refuses pages that already have text, undoable via `SetPageBytesCommand`.
- `ui/bookmarks.py`: read-only tree from `raw.get_toc()`.
- REVIEW_NOTES says nothing has been hand-tested on Windows yet (drags, OCR, read-aloud). Its "Not built" list includes page labels and language auto-pick.

## 3. Findings that should shape the design
1. **g-pdf's current OCR bloats scans [measured].** Page 12 of the test scan: original ~116 KB/page (1-bit CCITT, 600 dpi). `ocr.py`'s method (render 300 dpi RGB + `pdfocr_tobytes`) = **949 KB**; gray render = 537 KB. That is 5-8x per page, roughly 130-230 MB for a 247-page book. `ocrmypdf` keeps the original image and only adds the text layer (45 pages: 5.2 -> 5.3 MB).
2. **Whole-book OCR must not go through per-page undo [read + reasoning].** 247 `SetPageBytesCommand`s would hold before+after bytes and blow the 256 MB cap, silently dropping undo. Treat book prep as one operation that writes a *new file* and opens it as a new tab.
3. **Don't use `--deskew` on 1-bit scans [measured]:** it re-encodes and doubled the file (1.2 -> 2.3 MB on 10 pages).
4. **`--clean` (unpaper) is slow [measured, rough]:** without it a 45-page slice took ~1-2 min; with it the full 247 pages took ~24 min (2 cores). Slightly noisier OCR on decorative chapter titles without it; the Contents-page repair covers that.
5. **GPU does nothing for Tesseract.** CPU only.
6. **Ghostscript 300 dpi downsample halves 1-bit scans [measured]:** 30.5 -> 15.0 MB on the full book; text layer and bookmarks survive; 200 dpi zoom still crisp. Only `/Subsample` is supported for 1-bit images. Only tested on 1-bit scans; colour/gray [untested].
7. **Detection on the test book [measured]:** 19/19 chapters + Foreword + Contents found; offset (printed = PDF - 10) found; born-digital PDF and already-OCR'd PDF both handled by the skip-OCR path.

## 4. The draft code
`book_pipeline_draft.py` (attached) is the tested Colab version: `process(src, ...)` plus helpers `has_text`, `find_marks`, `fix_title`, `find_offset`. Treat it as a spec/prototype, not final code. Known weaknesses:
- Regexes and thresholds (0.96 / 0.6 title match; 100 chars x 12 sampled pages for "has text"; 5-page offset agreement) were tuned on **one book**. Needs a varied test set before trusting.
- Chapter detection needs a heading line like `CHAPTER 3` / `Chapter III` / `Part 2` in the first 6 lines. Books with bare numbers/titles get only front-matter bookmarks.
- `overrides` only handles numeric chapters; everything is flat (no nesting levels).
- `process()` prints and writes next to the cwd; needs to return a structured report and take an output dir.
- Printed page shows negatives for front matter (e.g. -9); should show "front" or roman numerals.
- Not tested: non-English, two-page-spread scans, multi-column, equations (Tesseract mangles math), password-protected files.

## 5. Proposed architecture
```
gpdf/
  core/            (existing, unchanged)
  pipeline/        NEW. No Qt imports. Used by CLI, GUI and tests.
    analyze.py     per-page text/no-text, scanned|mixed|digital, dpi, rotation, near-blank/plate pages,
                   existing bookmarks/metadata -> Report dataclass (+ .to_json())
    tools.py       find tesseract / ghostscript / ocrmypdf (shutil.which + default Windows paths);
                   friendly, specific "install X" messages
    ocr.py         ocrmypdf wrapper with progress callback
    structure.py   find_marks / find_offset / fix_title from the draft -> Mark(title, page, level, confidence)
    compress.py    Ghostscript 300 dpi step; keep result only if smaller
    export.py      page-marked text; optional per-chapter files
    run.py         prepare(src, options, out_dir) -> Result; never overwrites the source
  cli.py           argparse; its own entry point (NOT __main__, which imports PySide6)
```
Add to `pyproject.toml`: `[project.scripts] gpdf-cli = "gpdf.cli:main"`; `ocrmypdf` and `pypdf` as an optional extra (`pip install g-pdf[prep]`) so the viewer still installs light.

**CLI sketch**
```
gpdf-cli analyze book.pdf [--json]               # what does this file need? (no changes)
gpdf-cli prepare book.pdf -o out/ [--lang eng] [--no-compress] [--no-text] [--dry-run]
gpdf-cli batch folder/ -o out/ [--recursive] [--jobs N] [--skip-done] [--report report.csv]
gpdf-cli bookmarks book.pdf --detect | --apply marks.json
gpdf-cli export-text book.pdf [--by-chapter]
```
**Batch rules:** files sequential, pages parallel (`ocrmypdf --jobs`); a failure on one file never stops the rest; per-file JSON report + final summary table; `--skip-done` via sidecar (source size+mtime hash); non-destructive outputs (`_processed.pdf`); `--dry-run` = analyze only.

**GUI hooks**
- "Prepare book..." dialog: shows the analyze report, checkboxes for steps, runs in a worker (QThread/QProcess) with a progress bar and cancel, opens the result in a new tab.
- Bookmarks tree becomes editable (rename/add/delete/reorder, "Detect chapters" button, offset field), saved through a new undoable `TocCommand` using `raw.set_toc`.
- Read-aloud and Find already depend on the text layer, so OCR directly unlocks them; chapter bookmarks enable "read from this chapter".
- Optional: page labels (printed numbering) via `set_page_labels`; low priority, mostly ignored by Notability/GoodNotes/AI tools.

## 6. Ideas beyond the draft (unverified)
- Presets: `book`, `textbook` (flag equation pages), `article`, `slides` (skip chapter detection).
- Confidence column in the bookmark list; low-confidence items highlighted for review.
- Contents-page parser that maps printed page numbers -> PDF pages as a second chapter source (cross-check against heading detection).
- Spread splitting and margin crop for two-page scans.
- Flag duplicate/blank/plate pages for review instead of deleting.
- Text export with `[[PDF p. N | printed p. M]]` markers and `===== Chapter =====` headings (implemented in the draft, works).
- Config file (TOML) for default language/steps.

## 7. Windows setup the app must handle (explain in README + detect at runtime)
- Tesseract (UB-Mannheim installer, tick needed languages), Ghostscript (Windows installer; 64-bit), then `pip install ocrmypdf pypdf`. `unpaper` is optional and awkward on Windows; skip.
- `shutil.which` first, then default install folders; never crash on a missing tool - report which one and how to install it.
- Licensing [reasoning, not legal advice]: repo is AGPL-3.0-or-later; PyMuPDF and Ghostscript are AGPL, ocrmypdf MPL-2.0, Tesseract Apache-2.0, pypdf BSD. Run the external ones as separate programs and don't vendor binaries/tessdata. Re-check before any distribution.

## 8. Testing plan
- **Do not commit the test book** (a 1948 book, probably still in copyright, and the repo is public). Generate synthetic fixtures instead: render pages of fake text to 1-bit TIFF/PNG, wrap with `img2pdf`/PyMuPDF, with `CHAPTER N` headings, running heads with page numbers, a Contents page, a plate page. Also a born-digital PDF (reportlab, as in the draft test) and a mixed one.
- Unit tests (headless, Linux CI): `has_text`, `find_marks`, `find_offset`, `fix_title`, analyze on each fixture.
- Integration (needs tesseract/ocrmypdf; mark `@pytest.mark.slow`): scanned fixture -> text layer exists, outline count right, output size <= ~1.1x input.
- Keep a private, uncommitted real-book test list for manual runs on Windows.

## 9. Suggested order
1. `pipeline/` + fixtures + tests, port the draft (no Qt, runs on Linux).
2. `cli.py`: `analyze`, `prepare`, then `batch`.
3. Windows verification of the tool detection and a real OCR run.
4. GUI: "Prepare book..." dialog, then editable bookmarks.
5. Replace or hide the per-page OCR menu entry for whole-document use (keep it for single-page fixes, but gray + size warning).

## 10. Decisions Gideon needs to make
1. Dependencies: require `ocrmypdf` + Ghostscript (best fidelity, more setup) vs. a PyMuPDF-only OCR that keeps the original image (no extra installs, **not built or tested**).
2. Keep `pipeline/` inside g-pdf, or a separate repo/package that g-pdf imports?
3. Should "Prepare book" overwrite in place (with backup) or always write a new file? (Draft: always new file.)
4. How much of the checklist in section 6 to take on vs. keeping the app focused on page manipulation (his stated top priority).
