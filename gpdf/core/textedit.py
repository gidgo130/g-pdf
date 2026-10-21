# SPDX-License-Identifier: AGPL-3.0-or-later
"""Search and light content editing (typo fixes, add text, add images).

Coordinates at this module's boundary are *view space*: points on the page as
displayed (rotation applied, origin top-left). PyMuPDF's text functions return
unrotated page space, so searches are mapped through ``page.rotation_matrix``.

Editing is deliberately limited to unrotated pages for now (see
REVIEW_NOTES.md): typo fixes redact the old text and draw the new text in a
standard font (Helvetica/Times/Courier family, chosen to resemble the original).
The original font file is NOT reused, so a fixed word can look slightly
different from its neighbours.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pymupdf

from gpdf.core.document import PdfDocument, PdfEditError, annot_xrefs

_FAMILIES = {
    # (monospace, serif) -> font names for (regular, bold, italic, bold-italic)
    (False, False): ("helv", "hebo", "heit", "hebi"),
    (False, True): ("tiro", "tibo", "tiit", "tibi"),
    (True, False): ("cour", "cobo", "coit", "cobi"),
    (True, True): ("cour", "cobo", "coit", "cobi"),
}


@dataclass(frozen=True)
class SearchHit:
    page: int
    rect: tuple[float, float, float, float]  # view space, points


@dataclass(frozen=True)
class TextSpan:
    text: str
    bbox: tuple[float, float, float, float]  # page space (== view space at rot 0)
    font: str       # standard font code used for re-drawing, e.g. "helv"
    size: float
    color: tuple[float, float, float]
    origin: tuple[float, float]


def search(doc: PdfDocument, query: str, pages: range | None = None) -> list[SearchHit]:
    """Case-insensitive search. Returns hits in reading order of pages."""
    query = query.strip()
    if not query:
        return []
    hits: list[SearchHit] = []
    for i in (pages if pages is not None else range(doc.page_count)):
        page = doc.raw[i]
        m = page.rotation_matrix
        for r in page.search_for(query):
            r = r * m
            hits.append(SearchHit(i, (r.x0, r.y0, r.x1, r.y1)))
    return hits


def page_has_text(doc: PdfDocument, index: int) -> bool:
    return bool(doc.raw[index].get_text("text").strip())


def _check_editable(page: pymupdf.Page) -> None:
    if page.rotation != 0:
        raise PdfEditError(
            "Editing text on rotated pages isn't supported yet. Rotate the page "
            "back to 0°, edit, then rotate again."
        )


def _font_code(font_name: str, flags: int) -> str:
    name = font_name.lower()
    mono = bool(flags & 8) or "courier" in name or "mono" in name
    serif = bool(flags & 4) or any(k in name for k in ("times", "serif", "georgia", "garamond"))
    if "sans" in name or "arial" in name or "helvet" in name or "calibri" in name:
        serif = False
    bold = bool(flags & 16) or "bold" in name
    italic = bool(flags & 2) or "italic" in name or "oblique" in name
    return _FAMILIES[(mono, serif)][(1 if bold else 0) + (2 if italic else 0)]


def _rgb(srgb: int) -> tuple[float, float, float]:
    return ((srgb >> 16 & 255) / 255, (srgb >> 8 & 255) / 255, (srgb & 255) / 255)


def span_at(doc: PdfDocument, index: int, x: float, y: float) -> TextSpan | None:
    """The text span under a view-space point (rotation 0 pages only), or None."""
    page = doc.raw[index]
    if page.rotation != 0:
        return None
    pt = pymupdf.Point(x, y)
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                if span["text"].strip() and pymupdf.Rect(span["bbox"]).contains(pt):
                    return TextSpan(
                        text=span["text"],
                        bbox=tuple(span["bbox"]),
                        font=_font_code(span["font"], span["flags"]),
                        size=span["size"],
                        color=_rgb(span["color"]),
                        origin=tuple(span["origin"]),
                    )
    return None


def _unsupported_chars(font: str, text: str) -> str:
    f = pymupdf.Font(font)
    return "".join(sorted({c for c in text if c not in "\n\t" and not f.has_glyph(ord(c))}))


def _redact_band(x0: float, x1: float, baseline: float, size: float) -> pymupdf.Rect:
    """Thin strip in one line's x-height zone: every glyph of that line touches it;
    glyphs of neighbouring lines and of adjacent spans on the same line don't."""
    inset = 0.1 * size
    return pymupdf.Rect(x0 + inset, baseline - 0.45 * size, x1 - inset, baseline - 0.30 * size)


def _link_xrefs(page: pymupdf.Page) -> list[int]:
    return [link["xref"] for link in page.get_links()]


def _restore_links(page: pymupdf.Page, keep: list[int]) -> None:
    """apply_redactions drops overlapped links from /Annots (the link objects
    survive); re-append any that went missing. get_links() is stale here, so
    /Annots is read and written through the xref API."""
    doc = page.parent
    present = annot_xrefs(doc, page.xref)
    missing = [x for x in keep if x not in present]
    if missing:
        refs = [*present, *missing]
        doc.xref_set_key(page.xref, "Annots", "[" + " ".join(f"{x} 0 R" for x in refs) + "]")


def replace_span(doc: PdfDocument, index: int, span: TextSpan, new_text: str) -> None:
    """Replace one span's text (undoable). Raises PdfEditError with a readable
    message if it can't be done; the page is left unchanged in that case."""
    if not new_text:
        raise PdfEditError("Replacement text is empty. Use delete for removing text.")
    bad = _unsupported_chars(span.font, new_text)
    if bad:
        raise PdfEditError(
            f"The standard fonts can't draw these characters: {bad!r}. "
            "Editing text with them isn't supported yet."
        )

    def mutate(page: pymupdf.Page) -> None:
        _check_editable(page)
        keep = _link_xrefs(page)
        rect = _redact_band(span.bbox[0], span.bbox[2], span.origin[1], span.size)
        page.add_redact_annot(rect, fill=False)
        page.apply_redactions(
            images=pymupdf.PDF_REDACT_IMAGE_NONE,
            graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
        )
        page.insert_text(
            pymupdf.Point(span.origin), new_text,
            fontname=span.font, fontsize=span.size, color=span.color,
        )
        _restore_links(page, keep)

    _check_editable(doc.raw[index])
    doc.edit_page(index, "Edit text", mutate)


def replace_text(doc: PdfDocument, index: int, old: str, new: str) -> int:
    """Replace every occurrence of ``old`` on one page. Returns the count."""
    page = doc.raw[index]
    _check_editable(page)
    spans = []
    for rect in page.search_for(old):
        s = span_at(doc, index, (rect.x0 + rect.x1) / 2, (rect.y0 + rect.y1) / 2)
        if s is not None:
            spans.append((rect, s))
    if not spans:
        return 0
    bad = _unsupported_chars(spans[0][1].font, new)
    if bad:
        raise PdfEditError(f"The standard fonts can't draw these characters: {bad!r}.")

    def mutate(p: pymupdf.Page) -> None:
        keep = _link_xrefs(p)
        for rect, s in spans:
            p.add_redact_annot(_redact_band(rect.x0, rect.x1, s.origin[1], s.size), fill=False)
        p.apply_redactions(
            images=pymupdf.PDF_REDACT_IMAGE_NONE,
            graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
        )
        for rect, s in spans:
            # baseline: bottom of span box minus descent approximated by origin y
            p.insert_text(
                pymupdf.Point(rect.x0, s.origin[1]), new,
                fontname=s.font, fontsize=s.size, color=s.color,
            )
        _restore_links(p, keep)

    doc.edit_page(index, "Replace text", mutate)
    return len(spans)


def add_text(
    doc: PdfDocument, index: int, x: float, y: float, text: str,
    size: float = 12, color: tuple[float, float, float] = (0, 0, 0), font: str = "helv",
) -> None:
    """Add a text line whose baseline starts at view-space (x, y)."""
    if not text.strip():
        raise PdfEditError("Nothing to add: the text is empty.")
    bad = _unsupported_chars(font, text)
    if bad:
        raise PdfEditError(f"The standard fonts can't draw these characters: {bad!r}.")
    _check_editable(doc.raw[index])

    def mutate(page: pymupdf.Page) -> None:
        for n, line in enumerate(text.split("\n")):
            page.insert_text(
                pymupdf.Point(x, y + n * size * 1.2), line,
                fontname=font, fontsize=size, color=color,
            )

    doc.edit_page(index, "Add text", mutate)


def add_image(
    doc: PdfDocument, index: int, rect: tuple[float, float, float, float],
    image_path: str | Path,
) -> None:
    """Place an image file inside a view-space rectangle (aspect kept)."""
    path = Path(image_path)
    if not path.is_file():
        raise PdfEditError(f"Image not found: {path}")
    _check_editable(doc.raw[index])
    r = pymupdf.Rect(rect)
    if r.is_empty or r.width < 4 or r.height < 4:
        raise PdfEditError("The image area is too small.")

    def mutate(page: pymupdf.Page) -> None:
        try:
            page.insert_image(r, filename=str(path), keep_proportion=True)
        except Exception as exc:
            raise PdfEditError(f"Could not read {path.name} as an image: {exc}") from exc

    doc.edit_page(index, "Add image", mutate)
