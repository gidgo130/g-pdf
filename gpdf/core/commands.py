# SPDX-License-Identifier: AGPL-3.0-or-later
"""Undoable operations on a :class:`~gpdf.core.document.PdfDocument`.

Each command reaches into the document's private helpers (``owner._raw``,
``owner._insert_bytes`` ...) on purpose: commands are part of the document
implementation, split out only to keep ``document.py`` readable.

Undo strategies, cheapest first:
* reorder / rotate / metadata: remember the inverse, no page data kept;
* delete: unlink the page objects from the page tree, relink them on undo;
* edit / OCR: keep a small standalone PDF of the page and restore its content
  into the same page object on undo;
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
    """Unlink pages from the page tree and keep the page objects alive, so
    undo puts the very same objects back (bookmarks, links, named
    destinations and structure tags to them stay valid).

    While a page is out of the tree, MuPDF resolves anything still pointing
    at it to the wrong page, so ``do`` neutralises bookmarks to deleted pages
    and removes GoTo links to them from the remaining pages, as PyMuPDF's
    ``delete_pages`` does, recording what it changed for ``undo``.

    Uses PyMuPDF privates (``_delete_page``, ``_remove_toc_item``,
    ``_reset_page_refs``) and ``mupdf.pdf_insert_page``; re-check if the
    PyMuPDF pin changes.
    """

    def __init__(self, owner: PdfDocument, indices: Sequence[int]) -> None:
        self.owner = owner
        self.indices = list(indices)  # sorted, unique
        self.label = "Delete pages"
        self.xrefs: list[int] = []
        self.outline: list[tuple[int, str]] = []  # (item xref, its source)
        self.annots: list[tuple[int, str]] = []  # (page xref, /Annots as written)

    def do(self) -> None:
        from gpdf.core.document import annot_xrefs

        raw = self.owner._raw
        dead = set(self.indices)
        self.xrefs = [raw.page_xref(i) for i in self.indices]
        self.outline = []
        toc = raw.get_toc(simple=False)
        for i, ox in enumerate(raw.get_outline_xrefs()):
            if toc[i][2] - 1 in dead:
                self.outline.append((ox, raw.xref_object(ox)))
                raw._remove_toc_item(ox)
        self.annots = []
        for i in range(raw.page_count):
            if i in dead:
                continue  # links ON deleted pages come back with the page
            gone = {
                link["xref"] for link in raw[i].get_links()
                if link["kind"] == pymupdf.LINK_GOTO and link.get("page") in dead
            }
            if not gone:
                continue
            pxref = raw.page_xref(i)
            kind, value = raw.xref_get_key(pxref, "Annots")
            self.annots.append((pxref, value))
            keep = [x for x in annot_xrefs(raw, pxref) if x not in gone]
            raw.xref_set_key(
                pxref, "Annots",
                "[" + " ".join(f"{x} 0 R" for x in keep) + "]" if keep else "null",
            )
        for i in reversed(self.indices):
            raw._delete_page(i)
        raw._reset_page_refs()
        self.byte_cost = sum(len(src) for _, src in self.outline) + sum(
            len(value) for _, value in self.annots
        )
        self.owner._structure_changed()

    def undo(self) -> None:
        raw = self.owner._raw
        pdf = pymupdf._as_pdf_document(raw)
        # Ascending order: each page goes back to its original position
        # because every earlier page is already restored.
        for index, xref in zip(self.indices, self.xrefs):
            pymupdf.mupdf.pdf_insert_page(
                pdf, index, pymupdf.mupdf.pdf_new_indirect(pdf, xref, 0)
            )
        raw._reset_page_refs()
        for ox, src in self.outline:
            raw.update_object(ox, src)
        for pxref, value in self.annots:
            raw.xref_set_key(pxref, "Annots", value)
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
    """Swap one page's content for another version of itself (used by OCR).

    The page object is kept. OCR bakes annotations into the page image, so
    only the page's links are kept on ``do``; ``undo`` brings all back."""

    def __init__(
        self, owner: PdfDocument, index: int, label: str, before: bytes, after: bytes
    ) -> None:
        from gpdf.core.document import annot_xrefs

        self.owner = owner
        self.index = index
        self.label = label
        self.before = before
        self.after = after
        self.byte_cost = len(before) + len(after)
        raw = owner._raw
        self.before_annots = annot_xrefs(raw, raw.page_xref(index))
        self.links = [
            x for x in self.before_annots
            if raw.xref_get_key(x, "Subtype")[1] == "/Link"
        ]

    def do(self) -> None:
        self.owner._restore_page(self.index, self.after, self.links)

    def undo(self) -> None:
        self.owner._restore_page(self.index, self.before, self.before_annots)


class EditPageCommand(Command):
    """Mutate one page in place; undo/redo restore whole-page snapshots into
    the same page object.

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
        from gpdf.core.document import annot_xrefs

        self.owner = owner
        self.index = index
        self.label = label
        self.before = before
        self.after: bytes | None = None
        self.before_annots = annot_xrefs(owner._raw, owner._raw.page_xref(index))
        self.after_annots: list[int] = []
        self._mutate = mutate
        self.byte_cost = len(before)

    def do(self) -> None:
        from gpdf.core.document import annot_xrefs

        owner = self.owner
        if self.after is None:
            try:
                self._mutate(owner._raw[self.index])
            except Exception:
                owner._restore_page(self.index, self.before, self.before_annots)
                raise
            self.after_annots = annot_xrefs(owner._raw, owner._raw.page_xref(self.index))
            self.after = owner._extract_bytes([self.index])
            self.byte_cost = len(self.before) + len(self.after)
            owner._page_changed(self.index)
        else:
            owner._restore_page(self.index, self.after, self.after_annots)

    def undo(self) -> None:
        self.owner._restore_page(self.index, self.before, self.before_annots)


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
