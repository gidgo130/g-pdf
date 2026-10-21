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

    def _replace_page(self, index: int, data: bytes) -> None:
        raw = self.raw
        src = pymupdf.open(stream=data, filetype="pdf")
        try:
            raw.insert_pdf(src, start_at=index, join_duplicates=1)
        finally:
            src.close()
        raw.delete_page(index + 1)
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
        """Write the document, replacing the target atomically."""
        target = Path(path)
        data = self.raw.tobytes(garbage=1, deflate=True)
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
