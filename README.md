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
   flagged page. Drag the round handles to move the cut (Shift-drag moves the
   whole line); the dashed line is the detector's guess. Press **S** to split,
   **K** to keep the page whole, **Enter** to accept what is shown, **R** to
   reset the cut, and the arrow keys to move between pages. Click a thumbnail
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

Split pages lose their links, annotations and any text layer. The report lists
those pages, and the text pages need OCR again. Bookmarks to a split page now
point to its first half. Page labels are dropped. Existing output files are only
replaced with `--force`, except the review sheet, which holds no state and is
always rewritten. If the PDF changed after the dry run, the decisions file
is refused; run the dry run again or pass `--force`.

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
