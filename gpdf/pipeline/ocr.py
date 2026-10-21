# SPDX-License-Identifier: AGPL-3.0-or-later
"""OCR scanned pages into a NEW file, keeping the scan image untouched.

Each page with a scan image and no text is rendered for Tesseract only; Tesseract's
text-only PDF (invisible text, no image) is laid over the page. Image streams are never
re-encoded. Pages that already have text are left as they are: overlaying would
duplicate every word (re-OCR must rebuild the page from its image instead).

Render resolution is the scan's own (pixels per displayed inch, capped at MAX_DPI);
scans below UPSAMPLE_BELOW dpi are rendered at their dpi and upsampled 3x (Lanczos),
which recovers about half of what OCR loses at 96 dpi. Tesseract reads the page as
displayed (upright); the text is overlaid with /Rotate cleared, turned into the unrotated
page's frame, and the rotation restored afterwards: overlaying into a rotated page
misplaces the text.

Tesseract runs as ``jobs`` single-threaded processes in parallel; rendering and overlaying
stay in this thread (PyMuPDF documents are not thread-safe), with at most 2 x jobs page
images waiting on disk in a temporary folder.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
import pymupdf
from PIL import Image

from gpdf.pipeline import tesseract
from gpdf.pipeline.split import same_file

SCHEMA = 1
OCR, HAS_TEXT, BLANK, NO_IMAGE, FAILED = "ocr", "has_text", "blank", "no_image", "failed"
STATUSES = (OCR, HAS_TEXT, BLANK, NO_IMAGE, FAILED)
UPSAMPLE_BELOW = 150       # dpi
UPSAMPLE = 3
MAX_DPI = 400
BLANK_INK = 0.001          # a page with less ink than this fraction of its pixels is blank
INK_LEVEL = 128            # grey below this is ink
LOW_CONF = 60              # words below this confidence count as low
LOWEST = 15                # pages listed in the summary
ATTEMPTS = 2               # a failed Tesseract run is tried once more


class OcrError(Exception):
    """A refusal or failure with a message meant for the user."""


def output_name(src) -> str:
    return f"{Path(src).stem}_ocr.pdf"


@dataclass
class PageOcr:
    page: int                          # 1-based
    status: str
    d_src: int | None = None           # dpi of the page's largest image
    d_tesseract: int | None = None     # dpi of the image Tesseract read
    upsampled: bool = False
    words: int = 0
    mean_conf: float | None = None
    low_conf_share: float | None = None    # words with confidence < LOW_CONF
    seconds: float = 0.0               # Tesseract time, retries included
    error: str = ""


@dataclass
class OcrReport:
    source: str
    output: str
    langs: str
    tesseract: str                     # version
    tessdata: str
    jobs: int
    pages_total: int
    pages: list[PageOcr] = field(default_factory=list)   # the selected pages
    seconds: float = 0.0
    size_ratio: float = 0.0            # output / input bytes
    schema: int = SCHEMA

    def by_status(self, status) -> list[PageOcr]:
        return [p for p in self.pages if p.status == status]

    @property
    def mean_conf(self) -> float | None:
        """Word-weighted mean confidence of the OCR'd pages."""
        n = sum(p.words for p in self.pages if p.mean_conf is not None)
        if not n:
            return None
        return round(sum(p.mean_conf * p.words for p in self.pages
                         if p.mean_conf is not None) / n, 2)

    @property
    def lowest(self) -> list[PageOcr]:
        done = [p for p in self.by_status(OCR) if p.mean_conf is not None]
        return sorted(done, key=lambda p: (p.mean_conf, p.page))[:LOWEST]

    @property
    def exit_code(self) -> int:
        """0 ok; 2 finished, but some pages failed."""
        return 2 if self.by_status(FAILED) else 0

    def to_dict(self) -> dict:
        d = asdict(self)
        d = {"schema": d.pop("schema"), **d}
        d["summary"] = {
            "counts": {s: len(self.by_status(s)) for s in STATUSES},
            "mean_conf": self.mean_conf,
            "lowest_conf": [{"page": p.page, "mean_conf": p.mean_conf, "words": p.words}
                            for p in self.lowest],
            "failed": [p.page for p in self.by_status(FAILED)],
            "seconds": self.seconds,
            "size_ratio": self.size_ratio,
        }
        return d

    def save(self, path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=1, ensure_ascii=False) + "\n",
                              encoding="utf-8")


def parse_pages(spec, count) -> list[int]:
    """0-based page numbers from a 1-based spec like "40-59", "1,3,7-", "-10"."""
    if not spec:
        return list(range(count))
    pages = set()
    for part in str(spec).split(","):
        part = part.strip()
        try:
            if "-" in part:
                a, b = part.split("-", 1)
                lo, hi = int(a) if a.strip() else 1, int(b) if b.strip() else count
            else:
                lo = hi = int(part)
        except ValueError:
            raise OcrError(f"bad page range {spec!r} (use e.g. 40-59 or 1,3,7-)") from None
        if not 1 <= lo <= hi <= count:
            raise OcrError(f"page range {part!r} is outside 1-{count}")
        pages.update(range(lo - 1, hi))
    return sorted(pages)


def source_dpi(page) -> int | None:
    """Pixels per displayed inch of the page's largest image (by displayed area)."""
    best = None
    for info in page.get_image_info():
        a, b, c, d = info["transform"][:4]
        w_in, h_in = (a * a + b * b) ** 0.5 / 72, (c * c + d * d) ** 0.5 / 72
        if w_in * h_in <= 0 or not info["width"] or not info["height"]:
            continue
        dpi = (info["width"] / w_in + info["height"] / h_in) / 2
        if best is None or w_in * h_in > best[0]:
            best = (w_in * h_in, dpi)
    return round(best[1]) if best else None


class _Upright:
    """Clear /Rotate inside the block, restore it after."""

    def __init__(self, page):
        self.page, self.rot = page, page.rotation

    def __enter__(self):
        if self.rot:
            self.page.set_rotation(0)
        return self.page

    def __exit__(self, *exc):
        if self.rot:
            self.page.set_rotation(self.rot)


def render(page, d_src) -> tuple[Image.Image, int, bool, float]:
    """(grey image for Tesseract, its dpi, upsampled?, ink fraction) of the page as
    displayed."""
    d = min(d_src, MAX_DPI)
    pix = page.get_pixmap(dpi=d, colorspace=pymupdf.csGRAY, alpha=False)
    g = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.stride)[:, :pix.width]
    ink = float((g < INK_LEVEL).mean())
    im = Image.fromarray(np.ascontiguousarray(g), "L")
    if d < UPSAMPLE_BELOW:
        im = im.resize((im.width * UPSAMPLE, im.height * UPSAMPLE), Image.LANCZOS)
        return im, d * UPSAMPLE, True, ink
    return im, d, False, ink


def read_tsv(path) -> list[float]:
    """Confidences of the words in a Tesseract TSV."""
    confs = []
    lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    for line in lines[1:]:
        cols = line.split("\t")
        if len(cols) >= 12 and cols[0] == "5" and cols[11].strip():
            conf = float(cols[10])
            if conf >= 0:
                confs.append(conf)
    return confs


def overlay(page, textpdf) -> None:
    """Lay Tesseract's text-only page (of the page as displayed) over ``page``: with
    /Rotate cleared, turned by the page's rotation."""
    rot = page.rotation
    with pymupdf.open("pdf", Path(textpdf).read_bytes()) as text, _Upright(page):
        page.show_pdf_page(page.rect, text, 0, overlay=True, keep_proportion=False,
                           rotate=rot)


@dataclass
class _Job:
    index: int
    result: PageOcr
    image: Path
    outbase: Path
    attempt: int = 1


def _verify(path, pages):
    """Reopen the written file and load every page."""
    with pymupdf.open(path) as doc:
        if doc.is_repaired:
            raise OcrError("the written file needed repair when reopened")
        if len(doc) != pages:
            raise OcrError(f"the written file has {len(doc)} pages, expected {pages}")
        for page in doc:
            page.get_contents()
            page.get_images()
            page.get_text("words")


def ocr_document(src, out, langs, jobs=None, pages=None, force=False, tess=None,
                 tesseract_path=None, progress=None) -> OcrReport:
    """Write ``out``: ``src`` with an invisible text layer on the scanned pages that have
    none. ``pages`` is a 1-based range spec ("40-59"); ``tess`` a prepared Tesseract
    (else found via ``tesseract_path``). ``progress(done, total)`` is optional."""
    t0 = time.perf_counter()
    src, out = Path(src), Path(out)
    if not src.is_file():
        raise OcrError(f"no such file: {src}")
    if same_file(src, out):
        raise OcrError("the output must be a different file from the source")
    if out.exists() and not force:
        raise OcrError(f"{out} already exists (pass --force to replace it)")
    tess = tess or tesseract.prepare(langs, tesseract_path)
    jobs = max(1, jobs or (os.cpu_count() or 2) - 1)
    try:
        doc = pymupdf.open(src)
    except Exception as e:
        raise OcrError(f"cannot open {src}: {e}") from None
    try:
        if not doc.is_pdf or doc.needs_pass:
            raise OcrError(f"{src} is not a PDF" if not doc.is_pdf
                           else f"{src} is password-protected")
        selected = parse_pages(pages, len(doc))
        report = OcrReport(str(src), str(out), langs, tess.version, tess.tessdata, jobs,
                           len(doc))
        report.pages = _ocr_pages(doc, selected, tess, langs, jobs, progress)
        _save(doc, out)
    finally:
        doc.close()
    report.size_ratio = round(os.path.getsize(out) / max(1, os.path.getsize(src)), 4)
    report.seconds = round(time.perf_counter() - t0, 2)
    return report


def _ocr_pages(doc, selected, tess, langs, jobs, progress) -> list[PageOcr]:
    results = []
    done = 0

    def tick():
        nonlocal done
        done += 1
        if progress:
            progress(done, len(selected))

    tmp = tempfile.mkdtemp(prefix="gpdf-ocr-")
    pool = ThreadPoolExecutor(max_workers=jobs)
    pending = {}
    try:
        def submit(job):
            t = time.perf_counter()
            fut = pool.submit(tesseract.recognize, tess, job.image, job.outbase, langs,
                              job.result.d_tesseract)
            pending[fut] = (job, t)

        def finish(block):
            ready, _ = wait(pending, return_when=FIRST_COMPLETED) if block else (
                [f for f in pending if f.done()], None)
            for fut in ready:
                job, t = pending.pop(fut)
                err = fut.result()
                r = job.result
                r.seconds = round(r.seconds + time.perf_counter() - t, 2)
                if err and job.attempt < ATTEMPTS:
                    for ext in (".pdf", ".tsv"):
                        Path(f"{job.outbase}{ext}").unlink(missing_ok=True)
                    job.attempt += 1
                    submit(job)
                    continue
                if err:
                    r.status, r.error = FAILED, err
                else:
                    confs = read_tsv(f"{job.outbase}.tsv")
                    r.status, r.words = OCR, len(confs)
                    if confs:
                        r.mean_conf = round(sum(confs) / len(confs), 2)
                        r.low_conf_share = round(sum(c < LOW_CONF for c in confs) / len(confs), 4)
                        overlay(doc[job.index], f"{job.outbase}.pdf")
                for p in (job.image, Path(f"{job.outbase}.pdf"), Path(f"{job.outbase}.tsv")):
                    p.unlink(missing_ok=True)
                tick()

        for i in selected:
            page = doc[i]
            r = PageOcr(i + 1, OCR)
            results.append(r)
            if page.get_text("words"):
                r.status = HAS_TEXT
            elif (d := source_dpi(page)) is None:
                r.status = NO_IMAGE
            else:
                im, r.d_tesseract, r.upsampled, ink = render(page, d)
                r.d_src = d
                if ink < BLANK_INK:
                    r.status = BLANK
                else:
                    job = _Job(i, r, Path(tmp) / f"p{i + 1:05d}.png", Path(tmp) / f"p{i + 1:05d}")
                    im.save(job.image, compress_level=1)
                    while len(pending) >= 2 * jobs:
                        finish(block=True)
                    submit(job)
                    finish(block=False)
                    continue
            tick()
        while pending:
            finish(block=True)
    finally:
        for fut in pending:
            fut.cancel()
        pool.shutdown(wait=True)
        shutil.rmtree(tmp, ignore_errors=True)
    return results


def _save(doc, out):
    fd, tmp = tempfile.mkstemp(dir=out.parent, prefix=".gpdf-", suffix=".tmp")
    os.close(fd)
    try:
        doc.save(tmp, garbage=4, deflate=True)
        _verify(tmp, len(doc))
        os.replace(tmp, out)
        tmp = None
    finally:
        if tmp and os.path.exists(tmp):
            os.remove(tmp)
