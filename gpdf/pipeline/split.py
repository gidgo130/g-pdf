# SPDX-License-Identifier: AGPL-3.0-or-later
"""Split two-page spread scans into single pages, writing a NEW file.

Pages the detector is unsure about (flagged) are never guessed: they stay whole unless the
owner reviewed them in a decisions file or passed accept_flagged. The source file is only
read; the output goes to a temporary file next to it and is moved into place at the end.
"""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

import pymupdf

from gpdf.pipeline import images, spreads
from gpdf.pipeline.report import (
    KEEP, SPLIT, UNREVIEWED, Decisions, DecisionsError, Detected, PageDecision, PageResult,
    SplitReport,
)

METADATA_KEYS = ("title", "author", "subject", "keywords", "creator", "producer",
                 "creationDate", "modDate", "trapped")


class SplitError(Exception):
    """A refusal or failure with a message meant for the user."""


def output_name(src) -> str:
    return f"{Path(src).stem}_split.pdf"


def same_file(a, b) -> bool:
    """Resolved paths equal (case-insensitively on Windows), or the same existing file."""
    a, b = Path(a), Path(b)
    if os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b)):
        return True
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def stamp(path) -> tuple[int, int]:
    st = os.stat(path)
    return st.st_size, st.st_mtime_ns


def _open_source(src):
    try:
        doc = pymupdf.open(src)
    except Exception as e:
        raise SplitError(f"cannot open {src}: {e}") from None
    if not doc.is_pdf:
        doc.close()
        raise SplitError(f"{src} is not a PDF")
    if doc.needs_pass:
        doc.close()
        raise SplitError(f"{src} is password-protected")
    return doc


def analyze(doc, progress=None) -> list[spreads.PageCut]:
    """Detector verdicts, plus a flag on pages that look like spreads but cannot be split
    by this method (several images, transparency, unsupported colour, ...)."""
    cuts = spreads.detect(doc, progress)
    for c in cuts:
        r = doc[c.page].rect
        if c.is_spread or r.width / r.height >= spreads.SPREAD_ASPECT:
            src, why = images.examine(doc[c.page])
            if src is None or src.flatten:             # unsplittable, or flattened: review
                c.confidence = round(min(c.confidence, spreads.FLAG_BELOW - 0.05), 3)
                c.reasons.extend(why)
    return cuts


def make_decisions(src, doc, cuts) -> Decisions:
    """The pre-filled decisions file: the detector's guess, reviewed except on flagged pages."""
    size, mtime = stamp(src)
    pages = []
    for c in cuts:
        w = doc[c.page].rect.width
        cut = [round(c.x_top / w, 6), round(c.x_bottom / w, 6)] if c.is_spread else None
        pages.append(PageDecision(
            c.page + 1, Detected(c.is_spread, cut, c.confidence, c.flagged, list(c.reasons)),
            SPLIT if c.is_spread else KEEP, cut, not c.flagged))
    return Decisions(str(Path(src).resolve()), size, mtime, pages)


def dry_run(src, progress=None) -> tuple[Decisions, SplitReport]:
    """Detect only: the decisions file to review and a report of the detector's guesses."""
    t0 = time.perf_counter()
    with _open_source(src) as doc:
        cuts = analyze(doc, progress)
        decisions = make_decisions(src, doc, cuts)
    pages = [PageResult(d.page, d.action, [], d.cut, d.detected.confidence, d.detected.flagged,
                        d.reviewed, d.detected.reasons) for d in decisions.pages]
    return decisions, SplitReport(str(src), None, len(pages), 0, pages, dry_run=True,
                                  seconds=round(time.perf_counter() - t0, 2))


def load_decisions(decisions, src, pages, force) -> Decisions:
    """``decisions`` (path or Decisions), checked against ``src`` and its page count; a
    stale source stamp is refused unless ``force``."""
    if not isinstance(decisions, Decisions):
        decisions = Decisions.load(decisions)
    if len(decisions.pages) != pages:
        raise DecisionsError(f"decisions file has {len(decisions.pages)} pages, "
                             f"the source has {pages}")
    if (decisions.source_size, decisions.source_mtime_ns) != stamp(src) and not force:
        raise DecisionsError("the source changed since the decisions file was made (size or "
                             "modification time differ): run --dry-run again, or pass --force")
    return decisions


def _split_page(out, pno, page, cut, result):
    """Insert the two halves of ``page`` at out[pno], out[pno + 1]; False if it cannot split."""
    src, why = images.examine(page)
    if src is None:
        result.left_whole = "; ".join(why)
        return False
    left, right, result.whitened_px = images.split_halves(page, src, cut)
    for k, half in enumerate((left, right)):
        p = out.new_page(pno + k, width=half.width, height=half.height)
        images.write_half(p, half, src)
    result.encoding = src.kind
    result.image_rotation_deg = round(src.rotation_deg, 2)
    result.image_cover = round(src.cover, 3)
    result.needs_ocr = bool(src.flatten) or bool(page.get_text("text").strip())
    result.flattened = src.flatten
    result.links_dropped = len(page.get_links())
    result.annots_dropped = len(list(page.annots())) + len(list(page.widgets()))
    return True


def _verify(path, pages):
    """Reopen the written file and load every page (some MuPDF errors only show then)."""
    with pymupdf.open(path) as doc:
        if doc.is_repaired:
            raise SplitError("the written file needed repair when reopened")
        if len(doc) != pages:
            raise SplitError(f"the written file has {len(doc)} pages, expected {pages}")
        for page in doc:
            page.get_contents()
            page.get_images()


def split_document(src, out, decisions=None, accept_flagged=False, force=False,
                   progress=None) -> SplitReport:
    """Write ``out`` with the spreads of ``src`` split into single pages.

    Without ``decisions`` the detector's guesses are used for unflagged pages. With a
    decisions file (path or Decisions), reviewed entries are applied exactly; unreviewed
    flagged pages stay whole unless ``accept_flagged``. ``force`` allows overwriting
    ``out`` and a decisions file whose source stamp no longer matches.
    ``progress(done, total)`` is called while detecting and while splitting.
    """
    t0 = time.perf_counter()
    src, out = Path(src), Path(out)
    if not src.is_file():
        raise SplitError(f"no such file: {src}")
    if same_file(src, out):
        raise SplitError("the output must be a different file from the source")
    if out.exists() and not force:
        raise SplitError(f"{out} already exists (pass --force to replace it)")
    doc = _open_source(src)
    try:
        if decisions is None:
            decisions = make_decisions(src, doc, analyze(doc, progress))
        else:
            decisions = load_decisions(decisions, src, len(doc), force)
        report = _build(doc, src, out, decisions, accept_flagged, progress)
    finally:
        doc.close()
    report.seconds = round(time.perf_counter() - t0, 2)
    return report


def _build(doc, src, out, decisions, accept_flagged, progress):
    results, plan = [], {}
    for d in decisions.pages:
        r = PageResult(d.page, KEEP, [], None, d.detected.confidence, d.detected.flagged,
                       d.reviewed, d.detected.reasons)
        results.append(r)
        if not d.reviewed and d.detected.flagged and not accept_flagged:
            r.left_whole = UNREVIEWED          # whatever the guess, single or spread
            continue
        if d.action != SPLIT:
            continue
        cut = d.cut or d.detected.cut
        if cut is None:
            raise DecisionsError(f"page {d.page}: action is split but there is no cut")
        plan[d.page - 1] = cut

    # Kept-page links into pages that will be split: re-aimed at the first half later
    # (deleting the original spread removes them).
    inbound = [(i, link) for i, page in enumerate(doc) if i not in plan
               for link in page.get_links()
               if link.get("kind") == pymupdf.LINK_GOTO and link.get("page") in plan]

    out_doc = pymupdf.open()
    tmp = None
    try:
        out_doc.insert_pdf(doc)                # everything, for the pages we keep
        originals = []                         # spreads, still in out_doc after their halves
        for i in range(len(doc)):
            pno = i + 2 * len(originals)
            if i in plan and _split_page(out_doc, pno + 1, doc[i], plan[i], results[i]):
                results[i].action, results[i].cut = SPLIT, plan[i]
                originals.append(pno)
            if progress:
                progress(i + 1, len(doc))
        if originals:
            out_doc.delete_pages(originals)
        final, splits = [], 0                  # 0-based output position of each source page
        for r in results:
            final.append(len(final) + splits)
            r.out_pages = [final[-1] + 1, final[-1] + 2] if r.action == SPLIT else [final[-1] + 1]
            splits += r.action == SPLIT

        relinked = 0
        for i, link in inbound:
            if results[link["page"]].action != SPLIT:
                continue
            out_doc[final[i]].insert_link({"kind": pymupdf.LINK_GOTO, "from": link["from"],
                                           "page": final[link["page"]], "to": pymupdf.Point(0, 0)})
            relinked += 1

        _copy_outline(doc, out_doc, final, results)
        meta = {k: v for k, v in (doc.metadata or {}).items() if k in METADATA_KEYS and v}
        out_doc.set_metadata(meta)
        xml = doc.get_xml_metadata()
        if xml:
            out_doc.set_xml_metadata(xml)

        fd, tmp = tempfile.mkstemp(dir=out.parent, prefix=".gpdf-", suffix=".tmp")
        os.close(fd)
        out_doc.save(tmp, garbage=3, deflate=True)
        out_doc.close()
        pages_out = sum(len(r.out_pages) for r in results)
        _verify(tmp, pages_out)
        os.replace(tmp, out)
        tmp = None
    finally:
        if not out_doc.is_closed:
            out_doc.close()
        if tmp and os.path.exists(tmp):
            os.remove(tmp)
    return SplitReport(str(src), str(out), len(doc), pages_out, results,
                       page_labels_dropped=bool(doc.get_page_labels()),
                       links_retargeted=relinked)


def _copy_outline(doc, out_doc, final, results):
    """Source outline with page numbers remapped; entries on a split page go to its first
    half (top of the page, since the old target point no longer applies)."""
    toc = []
    for item in doc.get_toc(simple=False):
        lvl, title, pno = item[:3]
        if pno < 1 or pno > len(final):
            toc.append(item)
        elif results[pno - 1].action == SPLIT:
            toc.append([lvl, title, final[pno - 1] + 1])
        else:
            toc.append([lvl, title, final[pno - 1] + 1, *item[3:]])
    if toc:
        out_doc.set_toc(toc)
