# g-pdf

A lean, local PDF workstation for Windows (and anywhere Python and Qt run): the
things you actually use in Acrobat, without the bloat, accounts, or cloud.

> **Status: first full draft (v0.2).** Everything below is implemented and
> unit-tested headlessly, but the interactive parts (real mouse drags, speech,
> OCR on Windows) have not been hand-tested yet. See
> [`REVIEW_NOTES.md`](REVIEW_NOTES.md) for the open questions and risks, and
> [`tests/MANUAL_TESTS.md`](tests/MANUAL_TESTS.md) for the checklist.

## What works

- **Tabs and windows**: open several PDFs as tabs; drag a tab out to its own
  window, drag it back (like browser tabs); drop `.pdf` files onto a window.
- **Page manipulation** (thumbnail panel on the left, or the Pages menu):
  drag thumbnails to reorder, multi-select, rotate, delete, duplicate, insert a
  blank page or another PDF, extract pages to a new file. Drag pages into
  another open window to copy them there. Drop a `.pdf` file onto the panel to
  insert it at that spot.
- **Undo/redo** for every change (Ctrl+Z / Ctrl+Y), unsaved-changes marker (●),
  and safe saving (written to a temp file, then swapped in).
- **Read aloud**, fully offline, using the voices installed on your system.
- **Light content editing**: Edit Text (click a line to fix a typo), Add Text,
  Add Image. Currently for pages that are not rotated.
- **Find** (Ctrl+F) with highlights; **bookmarks** tree.
- **Document properties** in plain language, with a one-click "Remove personal
  info".
- **OCR** for scanned pages (needs Tesseract installed; see below).
- Zoom, fit width, go to page; high-DPI aware.

## Planned

- Split view, restructuring existing page content (move/delete elements),
  password-protected PDFs, background rendering for very large files.

## Optional: OCR on Windows

Install Tesseract from <https://github.com/UB-Mannheim/tesseract/wiki> (tick the
languages you need, e.g. Spanish). g-pdf finds it in the default install folder,
or set the `GPDF_TESSDATA` environment variable to the `tessdata` folder.

## Optional: read-aloud voices

Uses the Windows voices under Settings > Time & language > Speech. To read
Spanish, add a Spanish voice pack there.

## Install and run (Windows)

You need Python 3.10 or newer. Check with `python --version`.

```powershell
git clone https://github.com/<your-username>/g-pdf.git
cd g-pdf

# A virtual environment keeps this project's packages separate from the rest
# of your Python install.
python -m venv .venv
.venv\Scripts\Activate.ps1

pip install -r requirements.txt
python -m gpdf                    # or: python -m gpdf some.pdf other.pdf
```

If PowerShell refuses to run `Activate.ps1`, allow local scripts once with
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, then try again.
In `cmd.exe`, use `.venv\Scripts\activate.bat` instead.

## Book prep (gpdf-cli)

`gpdf-cli split` turns two-page spread scans into single pages. It writes a new
file and never changes the original. It needs the `prep` extra (numpy, Pillow):

```powershell
pip install -e ".[prep]"          # from a clone; or: pip install "g-pdf[prep]"
```

The extra also installs [wordfreq](https://github.com/rspeer/wordfreq) for the
survey's text-quality score. Its code is Apache-2.0 and its word lists are
CC BY-SA 4.0; together they take about 60 MB.

1. **Dry run.** Nothing is split yet:

   ```powershell
   gpdf-cli split book.pdf -o out --dry-run
   ```

   This writes `out\book_split.report.json`, `out\book_split.decisions.json`
   and the review sheet `out\book_split.review.html`, and lists the pages the
   detector is unsure about ("flagged"), with reasons. `--no-sheet` skips the
   sheet.
2. **Review** in the sheet: open `book_split.review.html` in Edge, Chrome or
   Firefox (it works offline, straight from the file). It starts at the first
   flagged page. The dashed line is the detector's guess; the red line is the
   cut. To move it:
   - click near the top or bottom edge of the page (or the close-up) to put
     that end of the cut there;
   - drag a round handle to move one end (Shift-drag moves the whole line), or
     drag the line itself to move all of it; while you hold it, the mouse wheel
     turns it 0.1° per notch about the point under the cursor;
   - **Ctrl+←/→** move it by 0.1% of the width (with Shift: 1%), **Ctrl+↑/↓**
     turn it 0.1° clockwise / counter-clockwise about its middle (with Shift:
     1°), and **V** makes it vertical.

   Press **S** to split, **K** to keep the page whole, **Enter** to accept what
   is shown, **R** to reset the cut, the arrow keys to move between pages, and
   **?** for the list of keys. Click a thumbnail
   to change a page the detector was sure about. Then click **Save decisions**
   and save `book_split.decisions.json`, preferably in the `out` folder (Firefox
   puts it in Downloads). The sheet keeps unsaved work in the browser and offers
   to restore it when reopened; **Load decisions...** reopens a saved file. The
   sheet shows the command for the next step, with a Copy button.
3. **Real run:**

   ```powershell
   gpdf-cli split book.pdf -o out --decisions out\book_split.decisions.json
   ```

   This writes `out\book_split.pdf`. Reviewed pages are applied exactly as you
   set them. Flagged pages you did not review stay whole, and the command
   exits with code 2 and lists them. `--accept-flagged` takes the detector's
   guess for those pages instead. Without `--decisions`, the detector's guesses
   are used and flagged pages stay whole.

Pages pieced together from black-and-white images, text and lines are first
drawn into one black-and-white image, which is then split.

Split pages lose their links, annotations and any text layer. The report lists
those pages, and the text pages need OCR again. Bookmarks to a split page now
point to its first half. Page labels are dropped. Existing output files are only
replaced with `--force`, except the review sheet, which holds no state and is
always rewritten. A forced dry run first copies a decisions file that holds a
review to `book_split.decisions.<date-time>.bak.json`. If the PDF changed after
the dry run, the decisions file is refused; run the dry run again or pass
`--force`.

To rebuild only the review sheet (for example after a g-pdf update) without
touching the decisions file or the report:

```powershell
gpdf-cli split book.pdf -o out --sheet-only --decisions out\book_split.decisions.json
```

The sheet then opens showing that saved review. Without `--decisions` it shows
the detector's guesses.

## Book prep: OCR

`gpdf-cli ocr` adds an invisible, searchable text layer over each scanned page.
The scan image itself is not changed. It needs Tesseract 5 with the book's
languages. On Windows:

1. Download the installer from <https://github.com/UB-Mannheim/tesseract/wiki>.
2. In the installer, under **Additional language data**, tick **Spanish**.
   English is included.
3. Add `C:\Program Files\Tesseract-OCR` to `PATH`, or let gpdf-cli find it
   there. You can also use `--tesseract PATH` or the `GPDF_TESSERACT` variable.

Then:

```powershell
gpdf-cli ocr out\book_split.pdf -o out --lang spa        # eng+spa for English with Spanish quotes
```

This writes `out\book_split_ocr.pdf` and `out\book_split_ocr.report.json`, and
lists the pages with the lowest confidence. Only pages that have a scan image
and no text yet are read. Pages that already have a text layer, blank pages and
pages without an image are listed and left as they are. `--pages 40-59` limits
the run to those pages, and `--jobs N` sets how many Tesseract processes run at
once (the default is one fewer than the number of CPUs). If Tesseract fails on
a page, that page is left without text and the command exits with code 2.

## Book prep: survey

`gpdf-cli survey` is a read-only look at a whole corpus. It never changes
anything under the folders it reads, and it applies nothing:

```powershell
gpdf-cli survey "C:\fuentes" "C:\lecturas" --catalogue out\catalogue --derived out
```

It walks each folder for `*.pdf` and `*.txt` and writes three things into
`out\catalogue`:

- `catalogue.json`, with one entry per file. Each entry holds page facts,
  scan or born-digital, the language, a text-quality score, duplicates and a
  suggested action.
- `catalogue.review.html`, the review sheet.
- `cache\`, word shingles used to find duplicates.

A file is identified by its contents, so later runs keep your decisions when
a file moves, and flag them for re-confirming when a file changes.

- **Text quality** is the share of suspect lowercase words: unknown to the
  word lists, mixed case like `opouyIy`, or a digit in a word like `Jap6n`
  or `e1`. Each page is graded good (< 3%), fair, or poor (>= 8%). Checked
  against ground truth, suspect % is roughly half the word error rate; it
  does not see real-word substitutions, dropped accents or letter-spaced
  words. Two rates are shown apart: unknown capitalised and short foreign
  words (names, or Tesseract reading an ornament), and words missing an
  accent (`tambien`).
- **Working files.** With `--derived`, a source's `<name>_split_ocr.pdf`
  (or `_rebuilt.pdf`, `_ocr.pdf`, `_split.pdf`) is measured instead of the
  source. The source's own layer is kept for comparison.
- **Duplicates.** Files that share most of their text are the same work.
  One is kept: a PDF before a `.txt`, then the more complete one, then the
  cleaner text, then the one with more bookmarks.
- **Suggestions.** Each file gets `keep_text`, `ocr_missing` (pages),
  `rebuild_layer`, `exclude` (duplicate, other language) or `review`, with
  its reasons. For a scan with a weak text layer, Tesseract re-reads up to 8
  pages as a trial, and `rebuild_layer` is suggested only if that is clearly
  better (by suspect % plus the capitalised/foreign rate, on both sides; the
  audit found this does not favour re-OCR). Pages with ink but no text are
  tried too: text or picture.
  `--no-trial` skips the trial.

Open the sheet in a browser. Check each file, change the action or add a
title, author, year, page offset or notes, and press **Confirm** (or
**Confirm all shown**). Then press **Save decisions** and keep
`catalogue.decisions.json` in the catalogue folder. The survey reads that
file but never writes it. A re-run only hashes unchanged files; use
`--remeasure` to measure everything again, and `--sheet-only` to rewrite
just the sheet. The command exits with code 2 if a file could not be read or
a trial page failed.

### Applying the decisions

```powershell
gpdf-cli apply --catalogue out\catalogue --derived out --dry-run
gpdf-cli apply --catalogue out\catalogue --derived out
```

`apply` acts only on confirmed decisions, and only after checking that each
file is still the one the survey measured. For `rebuild_layer` (and
`ocr_missing`) it writes `out\<name>_rebuilt.pdf` and a report. Tesseract
reads each page as the OCR step does. Only once it has read a page is the
old invisible text removed and the new text laid over the untouched scan.
A page with visible text, a page whose text cannot be removed and a page
Tesseract failed on keep their old layer. Links, notes and bookmarks stay.
`keep_text` and `exclude` write nothing.

`--dry-run` prints the plan. `--only TEXT` limits it to files whose path
contains TEXT. An existing output needs `--force`. Sources, `catalogue.json`
and the decisions file are never changed. Each output is recorded in
`applied.json` in the catalogue folder. The next survey (with `--derived`)
measures the `_rebuilt.pdf` and shows the decision as **applied**. Exit
code 2 means a file was refused or a page failed.

### Exporting the text

```powershell
gpdf-cli export --catalogue out\catalogue --out out\text --dry-run
gpdf-cli export --catalogue out\catalogue --out out\text
```

`export` writes clean, citable text for every confirmed (or applied) entry
that is not excluded. It reads the applied output if there is one, else the
working file, else the source, and checks each file's hash first. Pending,
re-confirm, missing and changed files are refused (exit code 2). For each
entry it writes, in `out\text`:

- `<name>.jsonl`: one record per heading, paragraph, verse, quote or note,
  in reading order, with its PDF pages and printed page labels (or, for a
  `.txt`, its line numbers), its language and flags such as `low_quality`.
- `<name>.pages.jsonl`: per page, the label, the page kind (body, contents,
  index, blank, front), the running head that was removed, and every removed
  line with its reason.
- `<name>.apparatus.jsonl`: the contents and index pages, kept out of the text.
- `<name>.txt`: a readable version with `[p. 74 · pdf 74]` at each page.

`export.json` records the inputs, their hashes and the counts.

Line breaks, headings, page numbers, running heads, footnotes and
letter-spaced words are decided from where the characters sit on the page.
Word lists are used only where the layout cannot decide (hyphens, OCR's "."
for a hyphen, letter-spacing without measurable gaps). Tick **No dictionary
fixes** for an entry in the review sheet to turn the word lists off for it,
for a poet's invented words. `--only TEXT` and `--pages 74-75` (with
`--only`) are for quick checks; an existing output needs `--force`.

## Keyboard shortcuts

| Action | Shortcut |
| --- | --- |
| Open / New window | Ctrl+O / Ctrl+N |
| Close tab / Close window | Ctrl+W / Ctrl+Shift+W |
| Next / previous tab | Ctrl+Tab / Ctrl+Shift+Tab |
| Zoom in / out | Ctrl+`+` (or Ctrl+`=`) / Ctrl+`-` |
| Fit width / actual size | Ctrl+0 / Ctrl+1 |
| Go to page | Ctrl+G |
| Save / Save as | Ctrl+S / Ctrl+Shift+S |
| Undo / Redo | Ctrl+Z / Ctrl+Y |
| Find | Ctrl+F |
| Rotate left / right | Ctrl+[ / Ctrl+] |
| Delete / duplicate pages | Ctrl+Del / Ctrl+D |
| Select all pages | Ctrl+Shift+A |
| Read aloud panel | Ctrl+Shift+R |
| Toggle sidebar | F4 |
| Scroll | Mouse wheel, arrow keys, PgUp/PgDn, Home/End |

## Development

```powershell
pip install -e ".[dev]"
pytest
```

Tests run without a display. Real mouse drags can't be simulated headlessly, so
the tab drag-out gesture has a manual checklist in
[`tests/MANUAL_TESTS.md`](tests/MANUAL_TESTS.md).

Layout: `gpdf/core` is Qt-light PDF logic, `gpdf/ui` is the interface. The UI
never talks to PyMuPDF directly; everything goes through `gpdf/core/document.py`.

## License

g-pdf is licensed under the **GNU Affero General Public License v3.0 or later**
(see [`LICENSE`](LICENSE)). It builds on [PyMuPDF](https://github.com/pymupdf/PyMuPDF),
which is AGPL-licensed, so this project is too. If you distribute a modified
version, you must make its source available under the same license.

## Credits

- Inspired by [PyAcro](https://github.com/samjanjua6/PyAcro) by samjanjua6, a
  PySide6 + PyMuPDF PDF workstation. g-pdf is an independent implementation with
  a different scope and architecture; no PyAcro code was copied.
- [PyMuPDF](https://github.com/pymupdf/PyMuPDF) (Artifex Software) does the PDF
  parsing and rendering.
- [Qt for Python (PySide6)](https://doc.qt.io/qtforpython-6/) provides the
  interface.
