# SPDX-License-Identifier: AGPL-3.0-or-later
"""The offline review sheet of the split step: ONE self-contained HTML file in which the
owner reviews flagged pages, adjusts cuts and saves a schema-1 decisions file.

No network, no server, no Python at review time: page images are embedded as JPEG data
URIs in a JSON block, and the page's own CSS and JS (review_sheet/) are inlined. The
sheet holds no state of its own (the browser keeps unsaved work in localStorage), so it
is always rewritten.
"""

from __future__ import annotations

import base64
import html
import json
import os
import tempfile
from datetime import datetime
from importlib import resources
from pathlib import Path

import pymupdf

THUMB_DPI, THUMB_QUALITY = 30, 60          # every page
FULL_DPI, FULL_QUALITY = 110, 75           # flagged pages
CLOSE_DPI, CLOSE_QUALITY = 200, 75         # flagged pages: the gutter strip
CLOSE_MARGIN = 0.06                        # close-up: this much of the width around the cut
CANNOT_SPLIT = "cannot split:"


def _template(name: str) -> str:
    return resources.files("gpdf.pipeline").joinpath("review_sheet", name).read_text("utf-8")


def _jpeg(page, dpi, quality, clip=None) -> dict:
    """A grey JPEG render of the page (display orientation) as a data URI, with its size."""
    pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY, clip=clip)
    data = base64.b64encode(pix.tobytes("jpg", jpg_quality=quality)).decode("ascii")
    return {"src": "data:image/jpeg;base64," + data, "w": pix.width, "h": pix.height}


def close_range(cut) -> tuple[float, float]:
    """x range (fractions of the width) of the gutter close-up for a cut (or none)."""
    lo, hi = (min(cut), max(cut)) if cut else (0.5, 0.5)
    return round(max(0.0, lo - CLOSE_MARGIN), 6), round(min(1.0, hi + CLOSE_MARGIN), 6)


def _page_view(page, d) -> dict:
    r = page.rect
    view = {"page": d.page, "width": round(r.width, 3), "height": round(r.height, 3),
            "splittable": not any(x.startswith(CANNOT_SPLIT) for x in d.detected.reasons),
            "thumb": _jpeg(page, THUMB_DPI, THUMB_QUALITY), "full": None, "close": None}
    if d.detected.flagged:
        view["full"] = _jpeg(page, FULL_DPI, FULL_QUALITY)
        x0, x1 = close_range(d.cut or d.detected.cut)
        clip = pymupdf.Rect(r.x0 + x0 * r.width, r.y0, r.x0 + x1 * r.width, r.y1)
        view["close"] = {**_jpeg(page, CLOSE_DPI, CLOSE_QUALITY, clip), "x": [x0, x1]}
    return view


def sheet_data(doc, decisions, outdir, stem, progress=None) -> dict:
    """Everything the sheet shows: the decisions as written for the dry run, the exact
    stamp (JS numbers cannot hold source_mtime_ns), and per page its size and images.
    Pages are rendered one at a time."""
    pages = []
    for k, d in enumerate(decisions.pages):
        pages.append(_page_view(doc[d.page - 1], d))
        if progress:
            progress(k + 1, len(decisions.pages))
    return {
        "decisions": decisions.to_dict(),
        "source_size_text": str(decisions.source_size),
        "source_mtime_ns_text": str(decisions.source_mtime_ns),
        "stem": stem,
        "source_name": Path(decisions.source).name,
        "outdir": str(outdir),
        "generated": datetime.now().astimezone().isoformat(timespec="seconds"),
        "pages": pages,
    }


def render_html(data: dict) -> str:
    """The sheet: template with CSS, JS and the data inlined. ``</`` is escaped in the JSON
    so no string in it can end the script element."""
    blob = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    page = _template("sheet.html")
    for token, value in (("{{title}}", html.escape(f"Review: {data['source_name']}")),
                         ("{{css}}", _template("sheet.css")),
                         ("{{js}}", _template("sheet.js"))):
        page = page.replace(token, value)
    return page.replace("{{data}}", blob)          # last: the data may contain anything


def write_review_sheet(doc, decisions, path, stem, progress=None) -> int:
    """Write the review sheet for ``decisions`` (rendered from the SOURCE ``doc``) to
    ``path``, replacing any old sheet; returns its size in bytes."""
    path = Path(path)
    text = render_html(sheet_data(doc, decisions, path.resolve().parent, stem, progress))
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".gpdf-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    return path.stat().st_size
