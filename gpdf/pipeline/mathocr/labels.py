# SPDX-License-Identifier: AGPL-3.0-or-later
"""Printed page labels of the selected pages, from the header and footer bands.

Candidates come from the engine's page_number / header / footer blocks (the page number
alone, or at either edge of a running head), else from the PDF's own text in the top and
bottom bands (born-digital files), else from Tesseract on those bands when one is given.
The readings go through ``manuals.label_candidates`` (arabic, letter-spaced, roman,
"Page n", chapter-page), so the pipelines share one notion of a page number.

Fitting: within each contiguous page range, a reading is accepted when at least
MIN_SUPPORT pages within WINDOW pages agree on its offset (printed minus file page); a
lone disagreeing reading is a misread and is rejected. Runs are the stretches of equal
offset; a change of offset between adjacent pages (a chapter start whose blank verso was
not scanned) is a step. Pages without an accepted reading get the run's label as an
inference (``printed=""`` plus a ``label_inferred`` flag in the page files).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from gpdf.pipeline import tesseract
from gpdf.pipeline.manuals import _format_label, int_to_roman, label_candidates

BAND = 0.10               # top / bottom share of the page height
MIN_SUPPORT = 2           # pages that must agree on an offset
WINDOW = 4                # pages either side that vote
BAND_TYPES = ("page_number", "header", "footer")
OCR_DPI = 300
OCR_LANG = "eng"

roman = int_to_roman


@dataclass
class Label:
    printed: str | None            # as printed on the page (accepted reading)
    inferred: str | None           # from the run, when nothing accepted was printed

    @property
    def marker(self) -> str:
        return self.printed or ""

    @property
    def text(self) -> str | None:
        return self.printed or self.inferred

    def flag(self) -> dict | None:
        if self.printed:
            return None
        if self.inferred:
            return {"type": "label_inferred", "value": self.inferred}
        return {"type": "label_missing"}


@dataclass
class Fit:
    labels: dict[int, Label] = field(default_factory=dict)
    runs: list[dict] = field(default_factory=list)
    steps: list[dict] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------- candidates

def _text_of(block: dict) -> str:
    c = block.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return " ".join(_text_of(x) if isinstance(x, dict) else str(x) for x in c)
    return ""


def band_texts(blocks: list[dict]) -> list[str]:
    """The texts of the engine's page_number, header and footer blocks."""
    return [t for b in blocks if b.get("type") in BAND_TYPES
            if (t := " ".join(_text_of(b).split()))]


def _edge(word: str) -> list[tuple]:
    return [c for c in label_candidates(word.strip(" |·•-–—.:,")) if c[0] in ("a", "r")]


def candidates(texts: list[str]) -> list[tuple]:
    """(style, value, chapter) readings: the whole text as a page number, else a number
    at its first or last word (the edge of a running head)."""
    out = []
    for t in texts:
        cs = label_candidates(t)
        if not cs:
            words = t.split()
            if len(words) >= 2:
                cs = _edge(words[0]) or _edge(words[-1])
        out.extend(cs)
    return out


def band_texts_from_pdf(doc, page: int) -> list[str]:
    """The PDF's own words in the top and bottom bands, one text per band."""
    pg = doc[page - 1]
    h = pg.rect.height
    top, bottom = [], []
    for x0, y0, x1, y1, word, *_ in pg.get_text("words"):
        if y1 <= pg.rect.y0 + BAND * h:
            top.append((round(y0), x0, word))
        elif y0 >= pg.rect.y1 - BAND * h:
            bottom.append((round(y0), x0, word))
    return [" ".join(w for _, _, w in sorted(band)) for band in (top, bottom) if band]


def band_texts_from_ocr(t: tesseract.Tesseract, doc, page: int, workdir,
                        lang: str = OCR_LANG) -> list[str]:
    """Tesseract's words in the top and bottom bands, one text per band."""
    import pymupdf

    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    pg = doc[page - 1]
    r = pg.rect
    out = []
    for name, clip in (("top", pymupdf.Rect(r.x0, r.y0, r.x1, r.y0 + BAND * r.height)),
                       ("bottom", pymupdf.Rect(r.x0, r.y1 - BAND * r.height, r.x1, r.y1))):
        image = workdir / f"p{page:03d}_{name}.png"
        pg.get_pixmap(dpi=OCR_DPI, clip=clip, colorspace=pymupdf.csGRAY).save(str(image))
        outbase = workdir / f"p{page:03d}_{name}"
        if tesseract.recognize(t, image, outbase, lang, OCR_DPI):
            continue
        words = _tsv_words(Path(f"{outbase}.tsv"))
        if words:
            out.append(" ".join(words))
    return out


def _tsv_words(path: Path) -> list[str]:
    words = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[1:]:
        cols = line.split("\t")
        if len(cols) >= 12 and cols[0] == "5" and cols[11].strip():
            words.append(cols[11].strip())
    return words


def collect(doc, pages, blocks_of, t: tesseract.Tesseract | None = None,
            workdir=None) -> tuple[dict[int, list[tuple]], dict[int, str]]:
    """Readings per page and where they came from ("engine", "pdf_text", "ocr", "none")."""
    cands, sources = {}, {}
    for p in pages:
        cs = candidates(band_texts(blocks_of(p)))
        src = "engine"
        if not cs:
            cs = candidates(band_texts_from_pdf(doc, p))
            src = "pdf_text"
        if not cs and t is not None and workdir is not None:
            cs = candidates(band_texts_from_ocr(t, doc, p, workdir))
            src = "ocr"
        cands[p] = cs
        sources[p] = src if cs else "none"
    return cands, sources


# ---------------------------------------------------------------- fitting

def _ranges(pages: list[int]) -> list[list[int]]:
    out, cur = [], []
    for p in pages:
        if cur and p != cur[-1] + 1:
            out.append(cur)
            cur = []
        cur.append(p)
    if cur:
        out.append(cur)
    return out


def _label(key: tuple, page: int) -> str:
    style, chapter, off = key
    return _format_label(style, page + off, chapter)


def fit(cands: dict[int, list[tuple]]) -> Fit:
    """Labels, runs, steps and rejected readings from the readings per page."""
    res = Fit()
    for rng in _ranges(sorted(cands)):
        votes = {p: {(s, ch, v - p) for s, v, ch in cands[p]} for p in rng}
        support = {}
        for p in rng:
            c = Counter()
            for q in rng:
                if abs(q - p) <= WINDOW:
                    c.update(votes[q])
            support[p] = c
        glob = Counter()
        for p in rng:
            glob.update(votes[p])
        accepted, key = {}, {}
        for p in rng:
            own = [(support[p][k], k) for k in votes[p] if support[p][k] >= MIN_SUPPORT]
            if own:
                accepted[p] = key[p] = max(own)[1]
        for p in rng:
            if p in accepted:
                continue
            near = [(n, -min(abs(q - p) for q in rng if accepted.get(q) == k), k)
                    for k, n in support[p].items() if n >= MIN_SUPPORT and any(
                        accepted.get(q) == k for q in rng)]
            if near:
                key[p] = max(near)[2]
            else:
                best = [(n, k) for k, n in glob.items() if n >= MIN_SUPPORT]
                key[p] = max(best)[1] if best else None
            if votes[p]:
                for k in sorted(votes[p]):
                    res.rejected.append({"page": p, "value": _label(k, p),
                                         "reason": "does not fit the run"})
        for p in rng:
            if p in accepted:
                res.labels[p] = Label(_label(key[p], p), None)
            elif key[p]:
                res.labels[p] = Label(None, _label(key[p], p))
            else:
                res.labels[p] = Label(None, None)
        run = None
        for p in rng:
            k = key[p]
            if k is None:
                run = None
                continue
            if run and run["key"] == k:
                run["pages"][1] = p
                run["found"] += p in accepted
                continue
            if run and run["key"][:2] == k[:2]:
                res.steps.append({"at": p, "from": run["key"][2], "to": k[2]})
            run = {"key": k, "pages": [p, p], "found": int(p in accepted)}
            res.runs.append(run)
    for run in res.runs:
        style, chapter, off = run.pop("key")
        a, b = run["pages"]
        run.update({"printed": [res.labels[a].text, res.labels[b].text], "offset": off,
                    "style": style})
        if chapter is not None:
            run["chapter"] = chapter
        run["found"] = run.pop("found")
    return res
