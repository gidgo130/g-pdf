# SPDX-License-Identifier: AGPL-3.0-or-later
r"""One page of engine blocks (the standard tier's layout) into the atlas page format:

    <!-- page printed="54" file="73" -->
    prose with \( inline \) math

    $$
    display
    \tag{44.74}
    $$

Headings take their level from the printed numbering (a chapter opener or a doc_title
-> #, "1" -> ##, "2.4" -> ###), else the engine's level. Simple HTML tables become
Markdown; tables with rowspan/colspan stay HTML with a table_unstructured flag. Figures
are "[Figure n: caption]", footnotes "[^n]: text" at the page end. Running heads and feet
are dropped and recorded as ``dropped`` flags; page numbers are the label. Every page
has balanced $$ and \( \): prose parts cannot open math.

Post-passes over the assembled pages: ``link_split_displays`` (an untagged display at
the foot of a page followed by a display at the top of the next one is closed, reopened
and flagged page_anomaly on both) and ``drop_banners`` (a paragraph repeated on the
first page of several runs is a banner).
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from html.parser import HTMLParser

from gpdf.pipeline.mathocr import latex as L
from gpdf.pipeline.mathocr.labels import Label

SOURCE = "mineru-standard"
FURNITURE = {"header": "running_head", "footer": "running_foot"}
CHAPTER_RE = re.compile(r"^(?:CHAPTER|Chapter|PART|Part)\s+([0-9]+|[IVXLC]+)\s*\.?$")
NUMBERED_RE = re.compile(r"^(\d+(?:\.\d+)*)\.?\s+\S")
FIGURE_RE = re.compile(r"^(?:FIGURE|Figure|Fig\.)\s*([\d.]*\d)[.:]?\s*(.*)$", re.S)
FOOTNOTE_RE = re.compile(r"^(\d{1,3}|[*†‡§¶])\s*(.*)$", re.S)
TRAILING_SPACE_RE = re.compile(r"(\\q?quad|\\[,;:!>]|~|\s)+$")
BANNER_MIN = 20                 # characters


class AssemblyError(Exception):
    pass


@dataclass
class Display:
    index: int                 # position among the page's blocks
    latex: str                 # body as written in the page file (tag taken out)
    tag: str | None
    bbox: list[float] | None
    raw: str                   # the engine's content, untouched


@dataclass
class Paragraph:
    index: int
    text: str                  # as written in the page file
    inlines: list[str]
    bbox: list[float] | None


@dataclass
class Item:
    kind: str                  # heading | para | display | table | figure | list | text
    md: str
    display: Display | None = None
    paragraph: Paragraph | None = None


@dataclass
class Page:
    file: int
    label: Label
    items: list[Item] = field(default_factory=list)
    footnotes: list[str] = field(default_factory=list)
    flags: list[dict] = field(default_factory=list)
    continues_display: bool = False

    @property
    def displays(self) -> list[Display]:
        return [it.display for it in self.items if it.display]

    @property
    def paragraphs(self) -> list[Paragraph]:
        return [it.paragraph for it in self.items if it.paragraph]

    @property
    def marker(self) -> str:
        return f'<!-- page printed="{self.label.marker}" file="{self.file}" -->'

    def render(self) -> str:
        chunks = [it.md for it in self.items if it.md] + self.footnotes
        if not chunks:
            return self.marker + "\n"
        first = self.items[0] if self.items else None
        continues = self.continues_display or (
            first is not None and first.kind == "para" and first.md[:1].islower())
        md = self.marker + ("\n" if continues else "\n\n") + "\n\n".join(chunks) + "\n"
        if md.count("$$") % 2 or md.count("\\(") != md.count("\\)"):
            raise AssemblyError(f"file page {self.file}: unbalanced math delimiters")
        return md

    def flags_json(self) -> str:
        return json.dumps(self.flags, ensure_ascii=False, indent=1) + "\n"


# ---------------------------------------------------------------- block text

def _parts(block: dict) -> list:
    c = block.get("content")
    if isinstance(c, list):
        return c
    return [{"type": "text", "content": c}] if isinstance(c, str) else []


def plain_text(block: dict) -> str:
    """The block's text with inline math as \\( \\)."""
    out = []
    for part in _parts(block):
        if not isinstance(part, dict):
            out.append(str(part))
        elif part.get("type") == "equation_inline":
            out.append(r"\(" + str(part.get("content", "")).strip() + r"\)")
        elif isinstance(part.get("content"), (list, dict)):
            out.append(plain_text(part))
        else:
            out.append(str(part.get("content", "")))
    return " ".join("".join(out).split())


def _prose(s: str) -> str:
    """Prose may not open or close math."""
    return s.replace("$", r"\$").replace(r"\(", "(").replace(r"\)", ")")


def _paragraph(block: dict, index: int) -> Paragraph:
    out, inlines = [], []
    for part in _parts(block):
        if not isinstance(part, dict):
            part = {"type": "text", "content": str(part)}
        if part.get("type") == "equation_inline":
            tex = _math(str(part.get("content", "")).strip())
            inlines.append(tex)
            if out and out[-1] and not out[-1][-1].isspace() and out[-1][-1].isalnum():
                out.append(" ")
            out.append(r"\(" + tex + r"\)")
        else:
            text = _prose(str(part.get("content", "")))
            if out and out[-1].endswith(r"\)") and text[:1].isalnum():
                out.append(" ")
            out.append(text)
    s = " ".join("".join(out).split())
    s = re.sub(r"\\\)\s+([.,;:)\]])", r"\\)\1", s)
    return Paragraph(index, s, inlines, block.get("bbox"))


TAG_NUMBER_RE = re.compile(r"[A-Za-z0-9]+(?:\.[0-9]+)+[a-z]?|[0-9]+[a-z]?")


def _math(tex: str) -> str:
    """Math may not carry the page's delimiters: $, \\( and \\) are invalid inside it."""
    return tex.replace("$", "").replace(r"\(", "(").replace(r"\)", ")")


def _display(block: dict, index: int) -> Display:
    raw = str(block.get("content") or "")
    body, tag = L.split_tag(raw)
    body = _math(TRAILING_SPACE_RE.sub("", body.strip()).strip())
    if tag:
        m = TAG_NUMBER_RE.search(tag)
        tag = m.group(0) if m else None
    return Display(index, body, tag, block.get("bbox"), raw)


def display_md(d: Display) -> str:
    return "$$\n" + d.latex + (f"\n\\tag{{{d.tag}}}" if d.tag else "") + "\n$$"


# ---------------------------------------------------------------- headings

def heading_level(text: str, block: dict, chapter: bool = False) -> int:
    if chapter or block.get("type") == "doc_title":
        return 1
    m = NUMBERED_RE.match(text)
    if m:
        return m.group(1).count(".") + 2
    level = block.get("level")
    return int(level) if isinstance(level, int) and level > 0 else 2


# ---------------------------------------------------------------- tables

class _TableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows, self.cell, self.spans, self.in_eq = [], None, False, False

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.rows.append([])
        elif tag in ("td", "th"):
            self.cell = []
            if any(k in ("rowspan", "colspan") and v not in (None, "1") for k, v in attrs):
                self.spans = True
        elif tag == "eq" and self.cell is not None:
            self.cell.append(r"\(")
            self.in_eq = True

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.cell is not None:
            if not self.rows:
                self.rows.append([])
            self.rows[-1].append(" ".join("".join(self.cell).split()))
            self.cell = None
        elif tag == "eq" and self.cell is not None:
            self.cell.append(r"\)")
            self.in_eq = False

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data if self.in_eq else _prose(data))


def table_markdown(html: str) -> str | None:
    """A Markdown table, or None when the table has spans or ragged rows."""
    p = _TableParser()
    p.feed(html)
    rows = [r for r in p.rows if r]
    if p.spans or not rows or len({len(r) for r in rows}) != 1:
        return None
    rows = [[c.replace("|", r"\|") for c in r] for r in rows]
    line = lambda r: "| " + " | ".join(r) + " |"            # noqa: E731
    return "\n".join([line(rows[0]), "|" + "---|" * len(rows[0])] + [line(r) for r in rows[1:]])


def _table(block: dict, page: Page) -> str:
    chunks = []
    for part in _parts(block):
        if not isinstance(part, dict):
            continue
        kind = part.get("type")
        if kind == "table_body":
            html = str(part.get("content") or "")
            md = table_markdown(html)
            if md is None:
                page.flags.append({"type": "table_unstructured",
                                   "note": "rowspan/colspan: kept as HTML"})
                md = html.strip()
            chunks.append(md)
        else:
            text = plain_text(part)
            if text:
                chunks.append(text)
    return "\n\n".join(chunks)


# ---------------------------------------------------------------- figures, footnotes, lists

def _figure(block: dict) -> str:
    caption = " ".join(plain_text(p) for p in _parts(block)
                       if isinstance(p, dict) and p.get("type") == "image_caption").strip()
    if not caption:
        return "[Figure: no caption]"
    m = FIGURE_RE.match(caption)
    if m and m.group(2).strip():
        return f"[Figure {m.group(1).rstrip('.')}: {m.group(2).strip()}]"
    if m:
        return f"[Figure {m.group(1).rstrip('.')}]"
    return f"[Figure: {caption}]"


def _footnote(block: dict, n: int) -> str:
    text = plain_text(block)
    m = FOOTNOTE_RE.match(text)
    if m and m.group(2):
        return f"[^{m.group(1)}]: {m.group(2)}"
    return f"[^{n}]: {text}"


def _list(block: dict) -> str:
    items = []
    for part in _parts(block):
        text = plain_text(part) if isinstance(part, dict) else str(part)
        if text:
            items.append("- " + text)
    return "\n".join(items)


# ---------------------------------------------------------------- the page

def assemble(blocks: list[dict], file: int, label: Label) -> Page:
    """The page of one engine layout (blocks in reading order)."""
    page = Page(file, label)
    flag = label.flag()
    if flag:
        page.flags.append(flag)
    blocks = sorted(blocks, key=lambda b: b.get("index", 0)) if all(
        isinstance(b.get("index"), int) for b in blocks) else list(blocks)
    i = 0
    while i < len(blocks):
        b = blocks[i]
        kind = b.get("type")
        if kind in FURNITURE:
            text = plain_text(b)
            if text:
                page.flags.append({"type": "dropped", "text": text, "what": FURNITURE[kind]})
        elif kind == "page_number":
            pass
        elif kind in ("doc_title", "paragraph_title"):
            text = plain_text(b)
            if text:
                page.items.append(Item("heading", "#" * heading_level(text, b) + " " + text))
        elif kind == "equation":
            d = _display(b, i)
            if d.latex:
                page.items.append(Item("display", display_md(d), display=d))
        elif kind == "table":
            md = _table(b, page)
            if md:
                page.items.append(Item("table", md))
        elif kind == "image":
            page.items.append(Item("figure", _figure(b)))
        elif kind == "footnote":
            page.footnotes.append(_footnote(b, len(page.footnotes) + 1))
        elif kind == "list":
            md = _list(b)
            if md:
                page.items.append(Item("list", md))
        else:                                             # text, code, ref_text, ...
            text = plain_text(b)
            m = CHAPTER_RE.match(text) if kind == "text" else None
            nxt = blocks[i + 1] if i + 1 < len(blocks) else None
            if m and nxt and nxt.get("type") in ("doc_title", "paragraph_title"):
                page.items.append(Item("heading", f"# {m.group(1)} {plain_text(nxt)}"))
                i += 2
                continue
            if kind == "text":
                p = _paragraph(b, i)
                if p.text:
                    page.items.append(Item("para", p.text, paragraph=p))
            elif text:
                page.items.append(Item("text", text))
        i += 1
    page.flags.append({"type": "summary", "display_formulas": len(page.displays),
                       "source": SOURCE})
    return page


def link_split_displays(pages: list[Page]) -> None:
    """An untagged display at the foot of a page followed by a display at the top of the
    next file page: flag both (page_anomaly) and reopen the display right after the
    marker."""
    by_file = {p.file: p for p in pages}
    for a in pages:
        b = by_file.get(a.file + 1)
        if b is None or not a.items or not b.items:
            continue
        last, first = a.items[-1], b.items[0]
        if last.kind == "display" and last.display.tag is None and first.kind == "display":
            a.flags.insert(0, {"type": "page_anomaly", "what": "split_display",
                               "note": f"the last display may continue on file page {b.file} "
                                       "(closed here, reopened there)"})
            b.flags.insert(0, {"type": "page_anomaly", "what": "split_display",
                               "note": f"display may continue from file page {a.file}; $$ "
                                       "reopened at the top of this page"})
            b.continues_display = True


def drop_banners(pages: list[Page], openers: set[int]) -> None:
    """A paragraph of BANNER_MIN+ characters repeated on at least two run-opener pages is
    a banner: dropped there, with a ``dropped`` flag."""
    def key(text):
        return " ".join(text.split())

    counts = Counter()
    for pg in pages:
        if pg.file in openers:
            for k in {key(it.md) for it in pg.items if it.kind == "para"}:
                if len(k) >= BANNER_MIN:
                    counts[k] += 1
    banners = {k for k, n in counts.items() if n >= 2}
    if not banners:
        return
    for pg in pages:
        if pg.file not in openers:
            continue
        keep = []
        for it in pg.items:
            if it.kind == "para" and key(it.md) in banners:
                pg.flags.insert(0, {"type": "dropped", "text": it.md, "what": "banner"})
            else:
                keep.append(it)
        pg.items = keep
