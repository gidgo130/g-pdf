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
| 1 | Data loss | `core/document.py` `_replace_page` (232-241); `commands.py` `DeleteCommand.undo` | OCR, undo/redo of any text/image edit, a failed edit, and undo of a page delete all swap in a new page object. PyMuPDF then turns bookmarks to that page into dead entries (page −1), and deletes inbound links and the page's own outbound links. Not restored by redo; permanent on save. REVIEW_NOTES understated it ("links restored", "inbound links break"). | Reproduced in sandbox (1.28.2 and 2.0); fix in area A | No |
| 2 | Data loss | `core/document.py` `__init__` | A truncated/damaged PDF opens silently in MuPDF's repaired form (`is_repaired`): test file kept page 1, pages 2–4 came up blank. Save would overwrite the original with the damaged version. Needs a warning and Save → Save As. | Reproduced in sandbox; confirmed on Windows (manual test 10) | Yes |
| 3 | Wrong result / size | `core/ocr.py` 66-70 | OCR renders 300 dpi RGB and replaces the page: 5.6× (RGB) / 3× (gray) on a synthetic 600 dpi gray page; also halves 600 dpi scans, bakes annotations into the image, drops /Rotate, and triggers #1. | Confirmed by code + synthetic probe | No |
| 4 | UX / failure | `core/history.py` + `EditPageCommand.do` | If a mutate fails, the page is restored via `_replace_page` (so #1 applies) but nothing is recorded in history, so the document reads "clean" though it changed. | Code reading | No |
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

## Notes by area
- Area F: page drag/drop tests called `dropEvent` directly and passed while real drops were impossible (#13). Added tests/realinput (real X11 mouse via xdotool, CI only) covering manual tests 1, 7, 16, 17; Esc-cancel on X11 needed _EscWatcher.
