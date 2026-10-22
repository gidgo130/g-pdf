# SPDX-License-Identifier: AGPL-3.0-or-later
"""Typesetting for the text-export tests: pages built line by line with PyMuPDF, so every
word sits where the test says (justified prose, ragged verse, hanging indents, footnotes in
smaller type, letter-spaced runs with exact gaps). No binary fixtures."""

from __future__ import annotations

import pymupdf

from . import corpus

W, H = 420, 600
X0, X1 = 50.0, 370.0
SIZE, PITCH = 10.0, 12.0
FONT = "tiro"


def width(text: str, size: float = SIZE, font: str = FONT) -> float:
    return pymupdf.Font(font).text_length(text, fontsize=size)


def words(seed: int, n: int, lang: str = "es") -> list[str]:
    return corpus.prose(seed, n, lang).split()


class Sheet:
    """One page with a cursor ``y`` (the next baseline)."""

    def __init__(self, doc, y: float = 80.0, font: str = FONT):
        self.page = doc.new_page(width=W, height=H)
        self.y = y
        self.font = font

    def text(self, x, y, s, size=SIZE, font=None) -> None:
        self.page.insert_text((x, y), s, fontname=font or self.font, fontsize=size)

    def line(self, s, x=X0, size=SIZE, pitch=PITCH) -> None:
        self.text(x, self.y, s, size)
        self.y += pitch

    def justify(self, ws, x0=X0, x1=X1, size=SIZE, pitch=PITCH) -> None:
        """``ws`` spread so the line runs from x0 to x1 exactly."""
        total = sum(width(w, size, self.font) for w in ws)
        gap = (x1 - x0 - total) / max(1, len(ws) - 1)
        x = x0
        for w in ws:
            self.text(x, self.y, w, size)
            x += width(w, size, self.font) + gap
        self.y += pitch

    def para(self, ws, x0=X0, x1=X1, indent=15.0, size=SIZE, pitch=PITCH,
             last_full=False) -> list[list[str]]:
        """A justified paragraph (first line indented, last line ragged unless
        ``last_full``); returns its lines."""
        lines, cur, x = [], [], x0 + indent
        space = width(" ", size, self.font)
        for w in ws:
            left = x0 + indent if not lines else x0
            if cur and left + sum(width(c, size, self.font) for c in cur) + space * len(cur) \
                    + width(w, size, self.font) > x1:
                lines.append(cur)
                cur = []
            cur.append(w)
        if cur:
            lines.append(cur)
        for k, ln in enumerate(lines):
            left = x0 + indent if k == 0 else x0
            if k < len(lines) - 1 or last_full:
                self.justify(ln, left, x1, size, pitch)
            else:
                self.line(" ".join(ln), left, size, pitch)
        return lines

    def line_to(self, seed, x1, x=X0, size=SIZE, pitch=PITCH) -> str:
        """A ragged line of corpus words that ends exactly at ``x1`` (its last word is
        placed there; the gap before it is an ordinary word gap). Returns the text."""
        ws = words(seed, 60)
        last = ws.pop()
        end = x1 - width(last, size, self.font) - 3          # where the rest must stop
        head = []
        for w in ws:
            if x + width(" ".join(head + [w]), size, self.font) > end:
                break
            head.append(w)
        self.text(x, self.y, " ".join(head), size)
        self.text(x1 - width(last, size, self.font), self.y, last, size)
        self.y += pitch
        return " ".join(head + [last])

    def spaced(self, tokens, gaps, x=X0, size=SIZE, pitch=PITCH) -> None:
        """Tokens placed with exact gaps (pt) between their boxes."""
        for i, t in enumerate(tokens):
            self.text(x, self.y, t, size)
            x += width(t, size, self.font) + (gaps[i] if i < len(gaps) else 0)
        self.y += pitch


def save(doc, path) -> None:
    doc.save(str(path), garbage=3, deflate=True)
    doc.close()


def job(path, **kw) -> dict:
    """A job for export.export_pdf / export.run_job."""
    d = {"id": "f" * 64, "rel": path.name, "input": str(path), "kind": "pdf", "pages": None,
         "outdir": str(path.parent), "stem": path.stem, "nodict": False, "scan": False,
         "lang": "es", "rows": {}}
    d.update(kw)
    return d
