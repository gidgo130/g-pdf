# SPDX-License-Identifier: AGPL-3.0-or-later
"""An open PDF: view data, undoable page operations, safe saving.

Everything the UI needs from a PDF goes through this class, so the rest of the
app never touches PyMuPDF directly (the text/OCR helpers in sibling modules
take ``PdfDocument.raw`` for read access only).

The file is read fully into memory and opened from those bytes, so g-pdf never
holds a lock on the file on disk (Windows would otherwise refuse to let other
programs, or us, replace it). The flip side: huge PDFs cost RAM, and changes
made to the file by another program while it is open are not noticed.

Not thread-safe: PyMuPDF documents must only be used from one thread at a time.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Callable, Iterable, Sequence

import pymupdf
from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QImage

from gpdf.core.commands import (
    DeleteCommand,
    EditPageCommand,
    InsertCommand,
    MetadataCommand,
    ReorderCommand,
    RotateCommand,
    SetPageBytesCommand,
)
from gpdf.core.history import Command, CompositeCommand, History


class PdfOpenError(Exception):
    """Raised when a file can't be opened as a usable PDF."""


class PdfEditError(Exception):
    """Raised when an operation can't be applied (message is user-readable)."""


# Page dict keys that describe what a page looks like. Restoring a snapshot
# copies these onto the existing page object; every other key (/Parent,
# /StructParents, /Tabs ...) stays as it is.
CONTENT_KEYS = (
    "Contents", "Resources", "MediaBox", "CropBox", "BleedBox", "TrimBox",
    "ArtBox", "Rotate", "UserUnit", "Group",
)


def annot_xrefs(raw: pymupdf.Document, page_xref: int) -> list[int]:
    """The xrefs in a page's /Annots, read through the xref API (works when
    the Page wrapper is stale). Handles a direct or an indirect array."""
    kind, value = raw.xref_get_key(page_xref, "Annots")
    if kind == "null":
        return []
    if kind == "xref":  # indirect array: "n 0 R"
        value = raw.xref_object(int(value.split()[0]), compressed=True)
    return [int(n) for n in re.findall(r"(\d+) 0 R", value)]


# -- save-time cleanup (#18) ----------------------------------------------------
# Deleted pages stay in the open document (undo relinks the same objects), so
# Save cleans a scratch copy instead: page objects that fell out of the page
# tree are blanked, and their form widgets are pruned from /AcroForm.


def _get_key(raw: pymupdf.Document, xref: int, key: str) -> tuple[str, str]:
    """``xref_get_key``, but ("null", "null") for an xref entry that can't be
    loaded (damaged files have them; MuPDF raises)."""
    try:
        return raw.xref_get_key(xref, key)
    except Exception:
        return ("null", "null")


def _ref_array(raw: pymupdf.Document, xref: int, key: str) -> list[int]:
    """The xrefs in an array of references (/Kids, /Fields ...), direct or
    indirect; [] if there is none. ``key`` may be a path ("AcroForm/Fields")."""
    kind, value = _get_key(raw, xref, key)
    if kind == "xref":  # indirect array: "n 0 R"
        value = raw.xref_object(int(value.split()[0]), compressed=True)
    elif kind != "array":
        return []
    return [int(n) for n in re.findall(r"(\d+) 0 R", value)]


def _set_ref_array(raw: pymupdf.Document, xref: int, key: str, refs: list[int]) -> None:
    """Write ``refs`` where ``_ref_array(raw, xref, key)`` read them from."""
    text = "[" + " ".join(f"{x} 0 R" for x in refs) + "]"
    kind, value = raw.xref_get_key(xref, key)
    if kind == "xref":
        raw.update_object(int(value.split()[0]), text)
    else:
        raw.xref_set_key(xref, key, text)


def _fields_holder(raw: pymupdf.Document, name: str = "Fields") -> tuple[int, str] | None:
    """(xref, key) of the /AcroForm array ``name`` (/Fields, /CO); /AcroForm
    may be inline in the catalog or indirect."""
    cat = raw.pdf_catalog()
    kind, value = raw.xref_get_key(cat, "AcroForm")
    if kind == "xref":
        return int(value.split()[0]), name
    if kind == "dict":
        return cat, f"AcroForm/{name}"
    return None


def _stray_pages(raw: pymupdf.Document) -> set[int]:
    """/Type /Page objects that are not in the page tree."""
    tree = {raw.page_xref(i) for i in range(raw.page_count)}
    return {
        x for x in range(1, raw.xref_length())
        if x not in tree and _get_key(raw, x, "Type") == ("name", "/Page")
    }


def _unplaced_widgets(raw: pymupdf.Document) -> set[int]:
    """Widget annotations in the field tree that no tree page's /Annots holds."""
    holder = _fields_holder(raw)
    if holder is None:
        return set()
    widgets: set[int] = set()
    seen: set[int] = set()
    stack = _ref_array(raw, *holder)
    while stack:
        x = stack.pop()
        if x in seen:
            continue
        seen.add(x)
        if _get_key(raw, x, "Subtype") == ("name", "/Widget"):
            widgets.add(x)
        stack.extend(_ref_array(raw, x, "Kids"))
    if not widgets:
        return set()
    for i in range(raw.page_count):
        widgets.difference_update(annot_xrefs(raw, raw.page_xref(i)))
    return widgets


def _prune_fields(raw: pymupdf.Document, dead: set[int]) -> set[int]:
    """Remove the ``dead`` widgets from the field tree, then every field whose
    /Kids that left empty. Fields that never had kids (data-only) stay.
    Returns the xrefs removed."""
    seen: set[int] = set()
    removed: set[int] = set()

    def prune(xref: int, key: str) -> list[int]:
        refs = _ref_array(raw, xref, key)
        keep = []
        for x in refs:
            if x in dead:
                removed.add(x)
                continue
            if x not in seen:
                seen.add(x)
                if _ref_array(raw, x, "Kids") and not prune(x, "Kids"):
                    removed.add(x)
                    continue
            keep.append(x)
        if keep != refs:
            _set_ref_array(raw, xref, key, keep)
        return keep

    holder = _fields_holder(raw)
    if holder is not None:
        prune(*holder)
    return removed


def _scrubbed_bytes(
    raw: pymupdf.Document, dead_pages: set[int], dead_widgets: set[int]
) -> bytes:
    """``raw`` saved with ``dead_pages`` blanked and ``dead_widgets`` pruned,
    done on a scratch copy so ``raw`` (and with it undo) is untouched.
    ``tobytes(garbage=0)`` keeps object numbers, so the xrefs still match."""
    scratch = pymupdf.open(stream=raw.tobytes(garbage=0), filetype="pdf")
    try:
        for x in dead_pages:
            scratch.update_object(x, "null")
        if dead_widgets:
            removed = _prune_fields(scratch, dead_widgets)
            # /CO (calculation order) would otherwise keep pruned fields alive.
            co = _fields_holder(scratch, "CO")
            if co is not None:
                refs = _ref_array(scratch, *co)
                keep = [x for x in refs if x not in removed]
                if keep != refs:
                    _set_ref_array(scratch, *co, keep)
        return scratch.tobytes(garbage=1, deflate=True)
    finally:
        scratch.close()


def _ranges(indices: Sequence[int]) -> list[tuple[int, int]]:
    """Collapse sorted unique indices into inclusive (from, to) runs."""
    runs: list[tuple[int, int]] = []
    for i in indices:
        if runs and runs[-1][1] == i - 1:
            runs[-1] = (runs[-1][0], i)
        else:
            runs.append((i, i))
    return runs


class PdfDocument(QObject):
    """An open PDF file."""

    structure_changed = Signal()      # page count / order / sizes changed
    page_changed = Signal(int)        # one page's content changed (same position)
    state_changed = Signal()          # dirty flag, undo/redo availability, path

    def __init__(self, path: str | Path, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.path = Path(path)
        try:
            data = self.path.read_bytes()
        except Exception as exc:
            raise PdfOpenError(f"Could not open {self.path.name}: {exc}") from exc
        if b"%PDF" not in data[:1024]:
            raise PdfOpenError(f"{self.path.name} is not a PDF file.")
        try:
            doc = pymupdf.open(stream=data, filetype="pdf")
        except Exception as exc:  # PyMuPDF raises several unrelated types here
            raise PdfOpenError(f"Could not open {self.path.name}: {exc}") from exc

        if not doc.is_pdf:
            doc.close()
            raise PdfOpenError(f"{self.path.name} is not a PDF file.")
        if doc.needs_pass:
            doc.close()
            raise PdfOpenError(
                f"{self.path.name} is password-protected, which isn't supported yet."
            )
        if doc.page_count == 0:
            doc.close()
            raise PdfOpenError(f"{self.path.name} has no pages.")

        self._raw: pymupdf.Document | None = doc
        self._history = History()
        self._gen: dict[int, int] = {}  # page xref -> content generation
        self._page_sizes: tuple[tuple[float, float], ...] = ()
        self._signed = self._detect_signatures()
        self._recompute_sizes()
        # Objects the file already kept outside the page tree / off every page
        # (e.g. form templates) are the file's business: Save never cleans them.
        self._orphans_at_open = _stray_pages(doc)
        self._unplaced_widgets_at_open = _unplaced_widgets(doc)

    # -- basic info ---------------------------------------------------------

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def raw(self) -> pymupdf.Document:
        """The underlying document. Read-only use outside gpdf.core, please."""
        if self._raw is None:
            raise RuntimeError("Document is closed")
        return self._raw

    @property
    def page_count(self) -> int:
        return len(self._page_sizes)

    @property
    def page_sizes(self) -> tuple[tuple[float, float], ...]:
        """Per-page (width, height) in points, page rotation applied."""
        return self._page_sizes

    @property
    def is_open(self) -> bool:
        return self._raw is not None

    @property
    def is_dirty(self) -> bool:
        return not self._history.is_clean

    @property
    def has_signatures(self) -> bool:
        """True if the file had digital signatures when opened. Saving rewrites
        the file, which invalidates them."""
        return self._signed

    @property
    def can_undo(self) -> bool:
        return self._history.can_undo

    @property
    def can_redo(self) -> bool:
        return self._history.can_redo

    @property
    def undo_label(self) -> str:
        return self._history.undo_label

    @property
    def redo_label(self) -> str:
        return self._history.redo_label

    def page_token(self, index: int) -> tuple[int, int]:
        """Changes whenever the page's rendered appearance may have changed."""
        xref = self.raw.page_xref(index)
        return (xref, self._gen.get(xref, 0))

    def page_rotation(self, index: int) -> int:
        return self.raw[index].rotation

    # -- rendering ----------------------------------------------------------

    def render(self, index: int, width_px: int, height_px: int) -> QImage:
        """Render one page to an RGB image of (about) the given pixel size."""
        raw = self.raw
        if not 0 <= index < self.page_count:
            raise IndexError(f"Page {index} out of range (0..{self.page_count - 1})")
        width_pt, height_pt = self._page_sizes[index]
        matrix = pymupdf.Matrix(
            max(1, width_px) / width_pt, max(1, height_px) / height_pt
        )
        pix = raw[index].get_pixmap(matrix=matrix, alpha=False)
        image = QImage(
            pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888
        )
        return image.copy()  # detach from the pixmap's buffer

    def render_thumbnail(self, index: int, max_side_px: int) -> QImage:
        w, h = self._page_sizes[index]
        scale = max_side_px / max(w, h)
        return self.render(index, round(w * scale), round(h * scale))

    # -- internal helpers used by commands ------------------------------------

    def _recompute_sizes(self) -> None:
        raw = self.raw
        self._page_sizes = tuple(
            (raw[i].rect.width, raw[i].rect.height) for i in range(raw.page_count)
        )

    def _detect_signatures(self) -> bool:
        try:
            return bool(self.raw.get_sigflags() > 0)
        except Exception:
            return False

    def _structure_changed(self) -> None:
        self._recompute_sizes()
        self.structure_changed.emit()
        self.state_changed.emit()

    def _touch(self, indices: Iterable[int]) -> None:
        for i in indices:
            xref = self.raw.page_xref(i)
            self._gen[xref] = self._gen.get(xref, 0) + 1

    def _page_changed(self, index: int) -> None:
        self._touch([index])
        self._recompute_sizes()
        self.page_changed.emit(index)
        self.state_changed.emit()

    def _extract_bytes(self, indices: Sequence[int]) -> bytes:
        """A standalone PDF holding just these pages (ascending index order)."""
        out = pymupdf.open()
        try:
            for a, b in _ranges(sorted(indices)):
                out.insert_pdf(self.raw, from_page=a, to_page=b)
            return out.tobytes(garbage=0)
        finally:
            out.close()

    def _insert_bytes(self, data: bytes, at: int, join: bool) -> int:
        src = pymupdf.open(stream=data, filetype="pdf")
        try:
            n = src.page_count
            self.raw.insert_pdf(
                src, start_at=-1 if at >= self.raw.page_count else at,
                join_duplicates=1 if join else 0,
            )
            return n
        finally:
            src.close()

    def _restore_page(self, index: int, data: bytes, annots: list[int]) -> None:
        """Put a page snapshot (``data``, one page) back INTO the existing page
        object, so its xref, and with it every bookmark, link, named
        destination and structure tag pointing at it, stays valid. ``annots``
        becomes the page's /Annots (the snapshot's own annotations are not
        used)."""
        raw = self.raw
        o = raw.page_xref(index)
        n = raw.page_count
        src = pymupdf.open(stream=data, filetype="pdf")
        try:
            src.xref_set_key(src.page_xref(0), "Annots", "null")
            raw.insert_pdf(src, start_at=-1, join_duplicates=1)
        finally:
            src.close()
        t = raw.page_xref(n)
        for key in CONTENT_KEYS:
            kind, value = raw.xref_get_key(t, key)
            raw.xref_set_key(o, key, "null" if kind == "null" else value)
        raw.xref_set_key(
            o, "Annots",
            "[" + " ".join(f"{x} 0 R" for x in annots) + "]" if annots else "null",
        )
        raw._delete_page(n)  # unlink the temporary page only
        raw._reset_page_refs()
        # Blank it too, or every later save takes the scrub path. Its contents
        # and resources live on in page ``o``.
        raw.update_object(t, "null")
        self._page_changed(index)
        self._structure_changed()

    # -- history --------------------------------------------------------------

    def _push(self, command: Command) -> None:
        self._history.push(command)
        self.state_changed.emit()

    def undo(self) -> bool:
        done = self._history.undo() is not None
        if done:
            self.state_changed.emit()
        return done

    def redo(self) -> bool:
        done = self._history.redo() is not None
        if done:
            self.state_changed.emit()
        return done

    # -- page operations --------------------------------------------------------

    def _clean_indices(self, indices: Iterable[int]) -> list[int]:
        out = sorted(set(int(i) for i in indices))
        if not out:
            raise PdfEditError("No pages selected.")
        if out[0] < 0 or out[-1] >= self.page_count:
            raise PdfEditError("Page number out of range.")
        return out

    def move_pages(self, indices: Iterable[int], dest: int) -> list[int]:
        """Move pages so they sit before original page ``dest`` (``page_count``
        = the end). Returns their new indices (empty if nothing moved)."""
        sel = self._clean_indices(indices)
        dest = max(0, min(dest, self.page_count))
        rest = [i for i in range(self.page_count) if i not in set(sel)]
        pos = dest - sum(1 for i in sel if i < dest)
        order = rest[:pos] + sel + rest[pos:]
        if order == list(range(self.page_count)):
            return []
        label = "Move pages" if len(sel) > 1 else "Move page"
        self._push(ReorderCommand(self, order, label))
        return list(range(pos, pos + len(sel)))

    def rotate_pages(self, indices: Iterable[int], degrees: int) -> None:
        if degrees % 90:
            raise PdfEditError("Rotation must be a multiple of 90 degrees.")
        self._push(RotateCommand(self, self._clean_indices(indices), degrees))

    def delete_pages(self, indices: Iterable[int]) -> None:
        sel = self._clean_indices(indices)
        if len(sel) >= self.page_count:
            raise PdfEditError("A PDF needs at least one page; can't delete them all.")
        self._push(DeleteCommand(self, sel))

    def duplicate_pages(self, indices: Iterable[int]) -> list[int]:
        """Copy pages, each run placed right after the original run.
        Returns indices of the new pages in the final numbering."""
        sel = self._clean_indices(indices)
        data = self._extract_bytes(sel)
        end = sel[-1] + 1
        self._push(InsertCommand(self, end, data, len(sel), "Duplicate pages"))
        return list(range(end, end + len(sel)))

    def insert_blank(self, at: int, like: int | None = None) -> int:
        """Insert a blank page before ``at``. Size follows page ``like`` (or the
        neighbour, or A4)."""
        at = max(0, min(at, self.page_count))
        ref = like if like is not None else (at - 1 if at > 0 else 0)
        w, h = self.raw[ref].mediabox.width, self.raw[ref].mediabox.height
        blank = pymupdf.open()
        try:
            blank.new_page(width=w, height=h)
            data = blank.tobytes()
        finally:
            blank.close()
        self._push(InsertCommand(self, at, data, 1, "Insert blank page"))
        return at

    def insert_pages_from(
        self, other: "PdfDocument", indices: Iterable[int], at: int
    ) -> list[int]:
        """Copy pages from another open document (cross-document drag/paste)."""
        sel = other._clean_indices(indices)
        data = other._extract_bytes(sel)
        at = max(0, min(at, self.page_count))
        self._push(InsertCommand(self, at, data, len(sel), "Insert pages"))
        return list(range(at, at + len(sel)))

    def insert_file(self, path: str | Path, at: int) -> list[int]:
        """Insert every page of another PDF file."""
        try:
            src = pymupdf.open(str(path))
        except Exception as exc:
            raise PdfEditError(f"Could not open {Path(path).name}: {exc}") from exc
        try:
            if not src.is_pdf or src.needs_pass or src.page_count == 0:
                raise PdfEditError(
                    f"{Path(path).name} isn't a usable PDF (not a PDF, encrypted, or empty)."
                )
            data = src.tobytes()
            n = src.page_count
        finally:
            src.close()
        at = max(0, min(at, self.page_count))
        self._push(InsertCommand(self, at, data, n, f"Insert {Path(path).name}"))
        return list(range(at, at + n))

    def extract_pages(self, indices: Iterable[int], path: str | Path) -> None:
        """Write the given pages to a new PDF file. Not an undoable change to
        this document."""
        sel = self._clean_indices(indices)
        _atomic_write(Path(path), self._extract_bytes(sel))

    # -- content edits --------------------------------------------------------

    def edit_page(
        self, index: int, label: str, mutate: Callable[[pymupdf.Page], None]
    ) -> None:
        """Apply ``mutate(page)`` as one undoable step. On error, the page is
        restored and the error re-raised."""
        before = self._extract_bytes([index])
        self._push(EditPageCommand(self, index, label, before, mutate))

    def replace_page_bytes(self, index: int, label: str, after: bytes) -> None:
        before = self._extract_bytes([index])
        self._push(SetPageBytesCommand(self, index, label, before, after))

    # -- metadata -------------------------------------------------------------

    @property
    def metadata(self) -> dict[str, str]:
        meta = self.raw.metadata or {}
        return {k: meta.get(k) or "" for k in (
            "title", "author", "subject", "keywords",
            "creator", "producer", "creationDate", "modDate",
        )}

    def set_metadata(self, values: dict[str, str], clear_xmp: bool = False) -> None:
        self._push(MetadataCommand(self, values, clear_xmp))

    def remove_personal_info(self) -> None:
        """Blank author/creator/producer/keywords and drop XMP metadata. Title
        and subject are kept (they're usually what the file is *about*)."""
        self.set_metadata(
            {"author": "", "creator": "", "producer": "", "keywords": ""},
            clear_xmp=True,
        )

    # -- saving -----------------------------------------------------------------

    def save(self) -> None:
        self.save_as(self.path)

    def save_as(self, path: str | Path) -> None:
        """Write the document, replacing the target atomically. Pages deleted
        (or left over by edits) and their form fields are not written."""
        target = Path(path)
        raw = self.raw
        dead_pages = _stray_pages(raw) - self._orphans_at_open
        dead_widgets = _unplaced_widgets(raw) - self._unplaced_widgets_at_open
        if dead_pages or dead_widgets:
            data = _scrubbed_bytes(raw, dead_pages, dead_widgets)
        else:
            data = raw.tobytes(garbage=1, deflate=True)
        _atomic_write(target, data)
        self.path = target
        self._history.mark_clean()
        self._signed = False  # whatever signatures existed no longer validate
        self.state_changed.emit()

    # -- lifecycle ----------------------------------------------------------

    def close(self) -> None:
        """Release the document. Safe to call more than once."""
        if self._raw is not None:
            self._raw.close()
            self._raw = None


def _atomic_write(target: Path, data: bytes) -> None:
    """Write to a temp file beside the target, then swap it in. A crash or full
    disk leaves the original untouched."""
    fd, tmp = tempfile.mkstemp(prefix=".gpdf-", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
