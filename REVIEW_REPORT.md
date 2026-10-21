# g-pdf v0.2 review report

Work in progress. Findings are verified on Windows only where the
"Windows" column says so.

## Environment
- Windows 11 (x64), Python 3.14.7 venv, PySide6 6.12.0, PyMuPDF 1.28.2
  (pinned `<2`), pytest 9.1.1.
- Sandbox cross-checks (Linux): suite passes on Python 3.13 and 3.14, PyMuPDF
  1.28.2 and 2.0.

## Automated tests on Windows
| Run | Result |
|---|---|
| First run | 9 passed, 1 skipped, 91 errors: pytest's own temp folder `%TEMP%\pytest-of-gidgo` was access-denied (created by another identity; Python ≥3.13 applies a real ACL for mode 0o700). Environment issue; fixed by deleting the folder. |
| Second run | 99 passed, 1 failed, 1 skipped: dropped-file paths used `/` on Windows (`QUrl.toLocalFile`). Fixed in `detachable_tabs.dropped_pdf_paths` (commit 2e2bca9). |
| Third run | **100 passed, 1 skipped** (OCR test: no Tesseract data yet). |

## Findings (ranked)
| # | Sev. | Where | Finding | Status | Windows |
|---|---|---|---|---|---|
| 1 | Data loss | `core/document.py` `_replace_page` (232-241); `commands.py` `DeleteCommand.undo` | OCR, undo/redo of any text/image edit, a failed edit, and undo of a page delete all swap in a new page object. PyMuPDF then turns bookmarks to that page into dead entries (page −1), and deletes inbound links and the page's own outbound links. Not restored by redo; permanent on save. REVIEW_NOTES understated it ("links restored", "inbound links break"). Named destinations broke too (resolve to page −1). | Fixed: `_restore_page` restores edit/OCR/failed-edit snapshots INTO the existing page object (xref kept); `DeleteCommand` unlinks the page objects from the page tree (neutralising bookmarks and inbound GoTo links meanwhile) and relinks the same objects on undo. Tests in `tests/test_data_safety.py` section A (TOC, GoTo + named links, named dests; live and saved; non-contiguous delete, delete+save+undo, undo/redo/undo, edit+delete). | Suite pass |
| 2 | Data loss | `core/document.py` `__init__` | A truncated/damaged PDF opens silently in MuPDF's repaired form (`is_repaired`): test file kept page 1, pages 2–4 came up blank. Save would overwrite the original with the damaged version. Needs a warning and Save → Save As. | Fixed: `was_repaired` (MuPDF `is_repaired`) at open; only then `is_damaged` reads every page's content and checks MuPDF's warnings (a harmless repair such as a shifted startxref has none). Opening warns once; `save()` refuses a damaged file and the UI's Save goes to Save As with "<stem> (recovered).pdf"; a successful Save As clears both flags. Also found: garbage collection (`tobytes(garbage>=1)`) on a damaged file fails with "cycle in page tree" (its /Kids point at objects that are gone) and breaks the open document, so damaged files are always written with `garbage=0`. Cost: unreferenced objects, including content of pages deleted in g-pdf, stay in the saved file for these documents. Tests: `tests/test_data_safety.py` section B, `tests/test_save_ui.py`. | Suite pass; manual test 30 pending |
| 3 | Wrong result / size | `core/ocr.py` 66-70 | OCR renders 300 dpi RGB and replaces the page: 5.6× (RGB) / 3× (gray) on a synthetic 600 dpi gray page; also halves 600 dpi scans, bakes annotations into the image, drops /Rotate, and triggers #1. | Confirmed by code + synthetic probe. #1 part fixed: the page object is kept and its links stay (other annotations are still dropped on OCR, back on undo). | No |
| 4 | UX / failure | `core/history.py` + `EditPageCommand.do` | If a mutate fails, the page is restored via `_replace_page` (so #1 applies) but nothing is recorded in history, so the document reads "clean" though it changed. | Fixed: the failed edit restores the snapshot into the same page object (same xref, same /Annots), so the page really is unchanged. Test `test_failed_edit_keeps_page_xref`. | Suite pass |
| 5 | Deps | `pyproject.toml` | Unbounded `PyMuPDF>=1.24.3` installed 2.0, which bundles onnxruntime + numpy + networkx (~130 MB) and ML layout models, imported on `import pymupdf` (0.15 s → ~0.9 s). | Pinned `<2` (f33e8be) | Yes |
| 6 | Packaging | `pyproject.toml` | `PySide6` meta-package also installs WebEngine + Pdf (~130 MB unused). `PySide6-Essentials` + `PySide6-Addons` (TTS) would do. | Open | n/a |
| 7 | Test | `tests/test_tabs.py` 339 | Path-string comparison failed on Windows (see run 2). | Fixed via code | Yes |
| 8 | UX | `ui/detachable_tabs.py` `DetachableTabWidget.dropEvent` | Dropping a dragged tab on its own window's page area moved it to the end instead of detaching (browsers detach). Owner chose: detach. | Fixed + test | Yes |
| 10 | UX | `ui/detachable_tabs.py` `start_tab_drag` | Esc during a tab drag detaches instead of cancelling: `QDrag.exec` returns IgnoreAction for both. | Fixed (Windows: GetAsyncKeyState; X11/macOS: Esc watcher) | Yes |
| 11 | UX | `ui/main_window.py` `open_path` | Opening a file already open in another window focuses it there; owner wants the tab moved to the window where it was dropped/opened. | Fixed + 4 tests | Yes |
| 12 | UX / feature | `ui/pdf_view.py` | Middle-button navigation: click (press+release without moving) toggles browser-style autoscroll; press-and-drag pans. | Planned (after area A) | n/a |
| 13 | Broken feature | `ui/page_panel.py` `PagePanel.__init__` | `setAcceptDrops(True)` on a `QListView` is not enough: drops arrive on its viewport, which never accepted them. Real thumbnail drags (reorder, cross-window copy) and PDF file drops on the panel never reached `dropEvent`. Tests missed it because they called `dropEvent` directly. Verified with a simulated X11 mouse drag: after `viewport().setAcceptDrops(True)` reorder and drop work. | Fixed + 2 tests (events sent to the viewport) | Pending retest |
| 14 | Wrong result | `ui/page_panel.py` `_row_for_pos`, `paintEvent` | Insertion row and marker assumed a horizontal grid (left/right half, vertical line). In the normal one-column sidebar a drop on the lower half of a page inserted *before* it. | Fixed: orientation taken from the layout (`_row_is_horizontal`: a neighbour on the same line means grid, else use y and a horizontal marker) + 3 tests | Pending retest |
| 9 | UX | `ui/page_panel.py` | Thumbnails are white pages on a white panel with no border; only faint text visible, large empty cells. | Fixed: custom item delegate in `page_panel.py` (grey page border, Highlight tint + 2 px Highlight border on selection, light grey panel) | Pending retest |
| 15 | Data loss | `core/document.py` `save` / `_atomic_write` | Memory-backed open means a file changed on disk by another program while open is silently overwritten by Save (no mtime/size check). Should raise `PdfEditError` and leave the other file alone. | Fixed: `(st_mtime_ns, st_size)` recorded right after reading at open and after every successful `save_as`; `save()` raises `FileChangedError` (a `PdfEditError`) when the file exists with a different stamp, unless `force=True`; a deleted file is simply recreated; `save_as` never checks. UI asks: Save As… (default) / Overwrite / Discard my changes (the window replaces the tab at the same index with a fresh one; if the file no longer opens, the error is shown and the tab and its edits stay) / Cancel. Tests: `tests/test_data_safety.py` section D, `tests/test_save_ui.py`. | Suite pass; manual test 31 pending |
| 16 | Data loss | `core/textedit.py` `replace_span` | The redaction rect is the span bbox (72.0, 87.1, 144.7, 103.6), which overlaps the next line 13 pt below at 12 pt (its glyphs start near y 102): after editing line 1, line 2 "Second line here" is reduced to "ere". The highlight annotation survives. `replace_text` had the same flaw. | Fixed: redaction is now a thin x-height band (`_redact_band`), no fill. Tests in `tests/test_data_safety.py` section I. Windows: suite pass | Fixed |
| 17 | Data loss | `core/textedit.py` `replace_span` / `replace_text` | `apply_redactions` deletes every link annotation the redaction rect overlaps, so editing linked text silently drops the link. | Fixed: link xrefs recorded before redaction and re-appended to `/Annots` afterwards. Test in `tests/test_data_safety.py` section I (live and after save_as; undo not covered, see #1). Windows: suite pass | Fixed |
| 18 | Data leak | `core/document.py` `save_as` | After a page is deleted, Save still writes (a) the page object and its content if anything still references it (named destinations, structure tags), and (b) its form fields, values included, in /AcroForm. Same with the old PyMuPDF `delete_pages`. Present since v0.2. | Fixed: `save_as` cleans a scratch copy (`_scrubbed_bytes`), never the open document, so undo (also after a save) keeps working: /Type /Page objects outside the page tree are blanked, and widgets in the field tree that are on no page are pruned from /AcroForm (plus fields left with empty /Kids; data-only fields kept), and from /AcroForm/CO (calculation order), which would otherwise keep their values in the file. Pages/widgets already outside the tree at open are left alone. Nothing to clean: saves as before. `_restore_page` blanks its temporary page, so undo/redo of edits, failed edits and OCR no longer leave stray pages that trigger the cleanup. Cost on a 300-page file: ~0.74 s → ~1.26 s (delete) / ~1.48 s (delete page with a field); edit + undo + redo, then save: ~1.4 s before the `_restore_page` change, ~0.8 s after (same as a plain save; timings noisy ±30%). Tests in `tests/test_data_safety.py` section J (named dest, filled field, radio group with indirect /AcroForm + /Kids, data-only field, calculated field in /CO, OCR path, orphan at open, no scratch copy, no stray page after edit undo/redo). | Suite pass |
| 19 | UX / failure | `ui/doc_tab.py` `_save_to` | A failed save (file locked by another program, read-only, disk full) showed only "Could not save: [WinError 5] …" with no way forward; closing a dirty tab whose save failed was safe but unexplained. | Fixed: the dialog says "Couldn't save <name>: <reason>. Your changes are still here." with Save As… / Cancel; for `PermissionError` the reason is "it's open in another program or read-only", otherwise the error text. Same when Save As itself fails. A close only goes ahead after a save that really succeeded. Tests in `tests/test_save_ui.py`. | Suite pass; manual test 32 pending |

## Handoff check (Step 1)
- HANDOFF.md: "repo is public" was stale at the time (now public); no local git
  repo existed; PyMuPDF 2.0 drift not anticipated. Its known issues 1, 2 and 4
  are correct.
- docs/book-pipeline-handoff.md: correct that `PdfDocument` is a QObject, but
  `document.py` also imports `QImage`, so every `gpdf.core` module needs Qt and
  `pipeline/` can't import any of them. Its plan treats `core/` as finished;
  editable bookmarks are pointless until #1 is fixed. GUI worker must run on the
  file on disk (separate process), never on the open, single-threaded document.
  Draft calls `ocrmypdf` by bare name (use `sys.executable -m ocrmypdf`) and `gs`
  (`gswin64c.exe` on Windows). Ghostscript gray/colour downsampling probably
  re-encodes as JPEG by default: untested, check before enabling.

## Manual tests (tests/MANUAL_TESTS.md)
Files: `private/manual/` (synthetic, gitignored).

| # | Test | Result | Notes |
|---|---|---|---|
| 1 | Tab detach to desktop | Pass | Also detaches over its own page area after #8 |
| 2 | Re-dock onto tab strip | Pass | |
| 3 | Drop tab on page area | Pass | |
| 4 | Reorder within strip | Pass | |
| 5 | Drag out the only tab | Pass | |
| 6 | Drop on menu/status bar | Pass | |
| 7 | Esc mid-drag | Pass | After #10; real drops still detach (also checked on Linux/X11) |
| 8 | Drop .pdf from Explorer (strip, page, start screen; non-PDF ignored) | Pass | |
| 9 | Same file twice | Pass | Moves the tab to the window it was dropped/opened in (#11) |
| 10 | Corrupt / password PDF | Pass | Garbage, password and non-PDF refused with a message; truncated file opens silently with blank pages (#2) |
| 11 | Zoom keys and Ctrl+wheel | Pass | |
| 12 | Resize on fit-width | Pass | |
| 13 | Scroll 150-page PDF | Pass | |
| 14 | 125/150% sharpness, mixed-DPI monitors | Pass | text sharp; mixed-DPI not tested |
| 15 | Thumbnail click / multi-select | Pass | |
| 16 | Thumbnail drag reorder + undo | Fail | Fixed by #13/#14, retest |
| 17 | Thumbnail drag to other window (copy) | Fail | Fixed by #13/#14, retest |
| 18 | Drop .pdf on panel | | |
| 19 | Rotate/delete/dup/blank/insert/extract; delete-all refused | | |
| 20 | Close dirty: Save/Discard/Cancel | | |
| 21 | Save while open elsewhere | | |
| 22 | Edit Text | | |
| 23 | Edit on rotated page / scan | | |
| 24 | Add Text / Add Image + undo | | |
| 25 | Find | | |
| 26 | Read Aloud | | |
| 27 | Properties / remove personal info / undo | | |
| 28 | OCR (Tesseract) | | |
| 29 | Signed PDF save warning | | |
| 30 | Damaged file: warning, Save → "(recovered)" | | |
| 31 | File changed by another program: each button | | |
| 32 | Save to a locked / read-only file | | |

## Notes by area
- Area F: page drag/drop tests called `dropEvent` directly and passed while real drops were impossible (#13). Added tests/realinput (real X11 mouse via xdotool, CI only) covering manual tests 1, 7, 16, 17; Esc-cancel on X11 needed _EscWatcher.
