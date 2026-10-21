# SPDX-License-Identifier: AGPL-3.0-or-later
"""OCR for scanned pages, via Tesseract (through PyMuPDF).

Needs Tesseract's language data (``*.traineddata``). We look in, in order:
``$GPDF_TESSDATA``, ``$TESSDATA_PREFIX``, PyMuPDF's own discovery, and the
default Windows install folder. If none is found OCR is simply unavailable and
the UI says how to fix that; nothing else depends on it.

OCR replaces the page by an image of itself plus an invisible text layer, so it
is meant for image-only pages (we refuse pages that already have text).
"""

from __future__ import annotations

import os
from pathlib import Path

import pymupdf

from gpdf.core.document import PdfDocument, PdfEditError

WINDOWS_DEFAULTS = (
    r"C:\Program Files\Tesseract-OCR\tessdata",
    r"C:\Program Files (x86)\Tesseract-OCR\tessdata",
)

INSTALL_HINT = (
    "OCR needs Tesseract. On Windows, install it from "
    "https://github.com/UB-Mannheim/tesseract/wiki (tick the Spanish language "
    "data if you need it), then restart g-pdf."
)


def find_tessdata() -> str | None:
    candidates = [os.environ.get("GPDF_TESSDATA"), os.environ.get("TESSDATA_PREFIX")]
    try:
        candidates.append(pymupdf.get_tessdata())
    except Exception:
        pass
    candidates.extend(WINDOWS_DEFAULTS)
    for c in candidates:
        if c and Path(c).is_dir() and any(Path(c).glob("*.traineddata")):
            return str(c)
    return None


def available_languages() -> list[str]:
    d = find_tessdata()
    return sorted(p.stem for p in Path(d).glob("*.traineddata")) if d else []


def ocr_page(doc: PdfDocument, index: int, language: str = "eng", dpi: int = 300) -> None:
    """Make an image-only page searchable/readable. Undoable."""
    tessdata = find_tessdata()
    if tessdata is None:
        raise PdfEditError(INSTALL_HINT)
    if language not in available_languages():
        raise PdfEditError(
            f"No OCR data for language '{language}'. Installed: "
            f"{', '.join(available_languages()) or 'none'}."
        )
    page = doc.raw[index]
    if page.get_text("text").strip():
        raise PdfEditError("This page already contains text; OCR isn't needed.")
    try:
        pix = page.get_pixmap(dpi=dpi, alpha=False)
        data = pix.pdfocr_tobytes(language=language, tessdata=tessdata)
    except Exception as exc:
        raise PdfEditError(f"OCR failed: {exc}") from exc
    doc.replace_page_bytes(index, "OCR page", data)
