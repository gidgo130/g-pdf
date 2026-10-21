# Review notes (for the Claude Code pass)

First full draft: built fast, tested headlessly only (101 tests, offscreen Qt,
Linux, PySide6 6.12 / PyMuPDF 1.28 / Python 3.13). Nothing here has run on
Windows or with a real mouse. Items are ordered roughly by how likely they are
to bite. **[V]** = needs verification on a real machine; **[K]** = knock-on risk
when changing related code.

## 1. Verify on Windows first

- **[V] Real-mouse drags**: tab tear-off/re-dock (known: Esc detaches) and the new
  thumbnail drag (reorder, cross-window copy, file drop). Only the code paths
  *after* the gesture are tested. See `tests/MANUAL_TESTS.md` 1-7, 15-18.
- **[V] OCR**: needs Tesseract + language data on the machine. Untested on Windows;
  `ocr.find_tessdata()` checks `GPDF_TESSDATA`, `TESSDATA_PREFIX`, PyMuPDF's
  discovery, then the default UB-Mannheim install folders. PyMuPDF's OCR may need
  the `tesseract` DLLs found at runtime. Real OCR test only ran with eng data.
- **[V] Read-aloud**: only tested with Qt's *mock* engine. Real Windows (SAPI/OneCore)
  behaviour of `stateChanged -> Ready` after each sentence, pause/resume, and rate
  is unverified; some engines report Ready late or twice. The advance logic is in
  `ReadAloudController._on_tts_state`. Spanish needs an installed voice; there is
  no language-matching of voice to text yet (user picks the voice).
- **[V] Thumbnails/rendering** on 125-150% scaling; scrolling a 500-page PDF.

## 2. Data-safety decisions to confirm

- **Saving rewrites the whole file** (`tobytes(garbage=1, deflate=True)` to a temp
  file, `os.replace`). Digital signatures are invalidated (we warn once on save,
  based on `get_sigflags()` at open). Tagged-PDF structure, some optional content
  and unusual features could be altered by the rewrite; not audited.
- **Memory-backed open**: the file is read fully into RAM, so there's no file lock
  (good on Windows) but (a) huge PDFs cost RAM and (b) **external changes to the
  file while open are not detected**; saving overwrites them. Consider an mtime
  check before save.
- **Atomic save on Windows**: `os.replace` fails if the target is open in another
  program with no share-delete (e.g. some viewers). The error is shown ("Could not
  save"); the original is untouched. A fallback (save-as prompt) isn't built.
- **Undo is per-document, in memory** (256 MB cap, oldest steps dropped; the saved
  state may become unreachable by undo, then the doc stays "dirty"). Page
  delete/replace snapshots are small standalone PDFs.
- **[K] Undoing a page delete re-inserts the page via `insert_pdf(join_duplicates=1)`**.
  Verified that form-field names are preserved (otherwise they become `name [12]`),
  and that rotation survives. Annotations/links restored; **cross-page links from
  *other* pages to a deleted-then-restored page are not re-pointed** (select()
  keeps xrefs for reorders, but delete/restore creates a new page object).
  Table of contents entries pointing at a deleted page are dropped by PyMuPDF and
  not restored on undo. **[V]** with a real bookmarked PDF.
- **Cross-document drag = copy** (source unchanged, no cross-doc undo link). The
  target's undo removes the inserted pages.
- **Replace-page flow** (`_replace_page`, used by text edits, add text/image and
  OCR) swaps the page for a snapshot with `insert_pdf` and deletes the old one:
  the page's xref changes, so inbound links from other pages break after
  edit+undo/redo. Fine for now; revisit if link fidelity matters.

## 3. Editing limits (by design, make sure you accept them)

- **Typo fix = redact + redraw** in a base-14 font (Helvetica/Times/Courier family,
  guessed from flags/name). The original font isn't reused, so edited text looks
  slightly different. Characters outside Latin-1-ish glyph coverage (CJK, many
  symbols) are refused with a message. Spanish accents/ñ/¿ are fine (tested).
- **Span-level editing**: the edit dialog replaces one text span (PyMuPDF's unit,
  usually a word-run in one font). A longer replacement may overlap the next span;
  there's no reflow. Text drawn as vector outlines or images isn't editable (use
  OCR for scans).
- **Redaction side effects**: `apply_redactions` is called with images and line art
  left alone, but anything else under the span rectangle (e.g. clipped content or
  overlapping annotations) is cleared. Background fill is sampled from one pixel
  next to the box, so it's wrong on gradients/photos.
- **Rotated pages (/Rotate != 0) are refused for editing** (not a bug: view-space vs
  page-space mapping for inserted text was left unproven). Searches *are*
  rotation-correct (`rotation_matrix`, tested).
- "Restructure existing content" (move/delete objects) is not started.

## 4. Metadata

- "Remove personal info" blanks author, creator, producer, keywords and deletes XMP
  (tested via save/reload at probe time). It does **not** touch: annotation/comment
  authors, embedded files, text on pages, page-level metadata, or document
  info of embedded sub-PDFs. The dialog says so; check the wording.
- Title/subject are kept on purpose. PyMuPDF may re-add `producer` on some saves
  (verified not at probe time; re-check after `save_as`).
- Dates are shown but not editable.

## 5. Performance / scaling

- Rendering is synchronous on the GUI thread (view + thumbnails). Thumbnails are
  rendered when first painted (LRU 400). Large scanned PDFs may stutter.
- Find searches the whole document synchronously on every keystroke; no debounce.
- `PdfDocument._recompute_sizes` is O(pages) per structure change; thumbnail drop
  nearest-item search is O(pages) when dropping onto empty space.
- Panel model does a full reset on each structure change (selection is restored
  heuristically: same page count -> same rows; after a move/dup/insert the
  operation sets its own selection). Undo/redo of a reorder leaves the *old*
  rows selected rather than the moved pages.

## 6. Code structure to know before refactoring

- `core/commands.py` reaches into `PdfDocument` privates (`_raw`, `_extract_bytes`,
  `_insert_bytes`, `_replace_page`, `_structure_changed`, `_touch`) on purpose.
- `page_token(i) = (xref, generation)` is the cache key for views and
  thumbnails; anything that changes a page's look **must** call `_touch` /
  `_page_changed` (rotate does; metadata doesn't need to).
- `PdfView._on_structure_changed` resets `_current = -1` to force a
  `current_page_changed` emit; status bar depends on that.
- `PdfDocument` is now a `QObject` (signals). All views connect in `set_document`
  and must disconnect there; `DocumentTab.dispose()` does it. Add new listeners
  following the same pattern or they'll touch a closed document.
- Drag registries (tabs, pages) are process-local dicts keyed by token: a drag from
  another process does nothing. Entries are removed after `QDrag.exec`.
- Tests monkeypatch `QMessageBox.warning/question`; any new modal dialog in a code
  path that tests reach will hang them (the UI tests' teardown patches
  `question` to Discard for that reason).

## 7. Licensing reminders

- AGPL-3.0-or-later (PyMuPDF). PySide6 is LGPL. Tesseract (Apache-2.0) is used as
  an external install, not bundled. Don't vendor `tessdata` into the repo
  (`/home/claude/probe/tessdata` was a test copy, never committed).
- PyAcro is credited as inspiration only; no code copied (it has no license).

## 8. Not built / ideas

Split view; password-protected files; recent files; page labels; drag pages to
reorder *across* tabs inside one window (needs a second visible panel); background
render threads; highlight of the sentence being read; merge dialog for several
files at once (currently: insert file at a position); remember window/splitter
layout; language auto-pick for TTS/OCR.
