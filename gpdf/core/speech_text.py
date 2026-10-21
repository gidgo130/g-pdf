# SPDX-License-Identifier: AGPL-3.0-or-later
"""Turn page text into sentence-sized chunks for a text-to-speech engine.

Pure Python (no Qt) so it is easy to test. Works fully offline.
"""

from __future__ import annotations

import re

from gpdf.core.document import PdfDocument

_SENTENCE_END = re.compile(r"(?<=[.!?¿¡…。])\s+(?=[A-ZÁÉÍÓÚÑ¿¡0-9\"'(\[])")
MAX_CHUNK = 400


def clean(text: str) -> str:
    text = text.replace("\u00ad", "")                      # soft hyphens
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)           # re-join hyphenated breaks
    paragraphs = re.split(r"\n\s*\n", text)
    return "\n".join(re.sub(r"\s+", " ", p).strip() for p in paragraphs if p.strip())


def split_chunks(text: str, max_len: int = MAX_CHUNK) -> list[str]:
    chunks: list[str] = []
    for para in clean(text).split("\n"):
        for sentence in _SENTENCE_END.split(para):
            sentence = sentence.strip()
            while len(sentence) > max_len:
                cut = sentence.rfind(" ", 0, max_len)
                cut = cut if cut > 0 else max_len
                chunks.append(sentence[:cut].strip())
                sentence = sentence[cut:].strip()
            if sentence:
                chunks.append(sentence)
    return chunks


def page_chunks(doc: PdfDocument, index: int) -> list[str]:
    """Reading-order sentences of one page (empty for image-only pages)."""
    return split_chunks(doc.raw[index].get_text("text", sort=True))
