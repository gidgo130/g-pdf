# SPDX-License-Identifier: AGPL-3.0-or-later
"""Undoable operations on a :class:`~gpdf.core.document.PdfDocument`.

Each command reaches into the document's private helpers (``owner._raw``,
``owner._insert_bytes`` ...) on purpose: commands are part of the document
implementation, split out only to keep ``document.py`` readable.

Undo strategies, cheapest first:
* reorder / rotate / metadata: remember the inverse, no page data kept;
* delete / replace / OCR: keep a small standalone PDF of just the affected
  pages and re-insert it on undo;
* insert: undo is "delete those pages again".
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Sequence

import pymupdf

from gpdf.core.history import Command, CompositeCommand  # noqa: F401 (re-export)

if TYPE_CHECKING:
    from gpdf.core.document import PdfDocument

METADATA_KEYS = (
    "title", "author", "subject", "keywords",
    "creator", "producer", "creationDate", "modDate",
)


class ReorderCommand(Command):
    def __init__(self, owner: PdfDocument, order: Sequence[int], label: str) -> None:
        self.owner = owner
        self.label = label
        self.order = list(order)  # new page i == old page order[i]
        self.inverse = [0] * len(self.order)
        for new, old in enumerate(self.order):
            self.inverse[old] = new

    def do(self) -> None:
        self.owner._raw.select(self.order)
        self.owner._structure_changed()

    def undo(self) -> None:
        self.owner._raw.select(self.inverse)
        self.owner._structure_changed()


class RotateCommand(Command):
    def __init__(self, owner: PdfDocument, indices: Sequence[int], delta: int) -> None:
        self.owner = owner
        self.indices = list(indices)
        self.delta = delta
        self.label = "Rotate pages"
        self.old = {i: owner._raw[i].rotation for i in self.indices}

    def _apply(self, rotations: dict[int, int]) -> None:
        for index, rotation in rotations.items():
            self.owner._raw[index].set_rotation(rotation % 360)
        self.owner._touch(self.indices)
        self.owner._structure_changed()

    def do(self) -> None:
        self._apply({i: r + self.delta for i, r in self.old.items()})

    def undo(self) -> None:
        self._apply(self.old)


class DeleteCommand(Command):
    def __init__(self, owner: PdfDocument, indices: Sequence[int]) -> None:
        self.owner = owner
        self.indices = list(indices)  # sorted, unique
        self.label = "Delete pages"
        self.saved = owner._extract_bytes(self.indices)
        self.byte_cost = len(self.saved)

    def do(self) -> None:
        self.owner._raw.delete_pages(self.indices)
        self.owner._structure_changed()

    def undo(self) -> None:
        src = pymupdf.open(stream=self.saved, filetype="pdf")
        try:
            # Ascending order: each page goes back to its original position
            # because every earlier page is already restored.
            for k, index in enumerate(self.indices):
                at = -1 if index >= self.owner._raw.page_count else index
                # join_duplicates keeps form-field names unchanged instead of
                # renaming them ("name [12]") because the deleted page's field
                # is still registered in the document's form.
                self.owner._raw.insert_pdf(
                    src, from_page=k, to_page=k, start_at=at, join_duplicates=1
                )
        finally:
            src.close()
        self.owner._structure_changed()


class InsertCommand(Command):
    """Insert the pages of a small standalone PDF (``data``) at ``at``."""

    def __init__(
        self, owner: PdfDocument, at: int, data: bytes, count: int, label: str
    ) -> None:
        self.owner = owner
        self.at = at
        self.data = data
        self.count = count
        self.label = label
        self.byte_cost = len(data)

    def do(self) -> None:
        self.owner._insert_bytes(self.data, self.at, join=False)
        self.owner._structure_changed()

    def undo(self) -> None:
        self.owner._raw.delete_pages(list(range(self.at, self.at + self.count)))
        self.owner._structure_changed()


class SetPageBytesCommand(Command):
    """Swap one page for another version of itself (used by edits and OCR)."""

    def __init__(
        self, owner: PdfDocument, index: int, label: str, before: bytes, after: bytes
    ) -> None:
        self.owner = owner
        self.index = index
        self.label = label
        self.before = before
        self.after = after
        self.byte_cost = len(before) + len(after)

    def do(self) -> None:
        self.owner._replace_page(self.index, self.after)

    def undo(self) -> None:
        self.owner._replace_page(self.index, self.before)


class EditPageCommand(Command):
    """Mutate one page in place; undo/redo swap whole-page snapshots.

    The first ``do`` runs ``mutate`` on the live page, then snapshots the
    result so redo doesn't have to trust ``mutate`` to be repeatable. If
    ``mutate`` raises, the page is put back exactly as it was.
    """

    def __init__(
        self,
        owner: PdfDocument,
        index: int,
        label: str,
        before: bytes,
        mutate: Callable[[pymupdf.Page], None],
    ) -> None:
        self.owner = owner
        self.index = index
        self.label = label
        self.before = before
        self.after: bytes | None = None
        self._mutate = mutate
        self.byte_cost = len(before)

    def do(self) -> None:
        owner = self.owner
        if self.after is None:
            try:
                self._mutate(owner._raw[self.index])
            except Exception:
                owner._replace_page(self.index, self.before)
                raise
            self.after = owner._extract_bytes([self.index])
            self.byte_cost = len(self.before) + len(self.after)
            owner._page_changed(self.index)
        else:
            owner._replace_page(self.index, self.after)

    def undo(self) -> None:
        self.owner._replace_page(self.index, self.before)


class MetadataCommand(Command):
    def __init__(
        self, owner: PdfDocument, new: dict[str, str], clear_xmp: bool
    ) -> None:
        self.owner = owner
        self.label = "Edit document properties"
        raw = owner._raw
        current = raw.metadata or {}
        self.old = {k: current.get(k) or "" for k in METADATA_KEYS}
        self.new = {**self.old, **new}
        self.clear_xmp = clear_xmp
        self.old_xmp = raw.get_xml_metadata() if clear_xmp else ""
        self.byte_cost = len(self.old_xmp)

    def do(self) -> None:
        raw = self.owner._raw
        raw.set_metadata(self.new)
        if self.clear_xmp:
            raw.del_xml_metadata()

    def undo(self) -> None:
        raw = self.owner._raw
        raw.set_metadata(self.old)
        if self.clear_xmp and self.old_xmp.strip():
            raw.set_xml_metadata(self.old_xmp)
