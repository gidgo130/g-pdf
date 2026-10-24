# SPDX-License-Identifier: AGPL-3.0-or-later
"""gpdf-cli manual-check: proves that page-cited extracts of the machine shop manuals quote
what the cited pages say. Every verbatim block must be on its cited page(s), every number of
a summary too, every table row must be a row of the page's table, and every page tag must
carry the page's printed label. The pages are the layer-1 records of ``manual-pages``
(``<slug>.pages.jsonl``) and the PDF's own ``page.get_text()``.

The conventions it enforces are in docs/manual-check.md. Reads the extracts, the page records,
the config and the source PDFs; writes only ``check.json`` and ``check.md`` to --report.
"""

from __future__ import annotations

import json
import os
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path

import pymupdf

from gpdf import __version__

MANIFEST = "00_MANIFEST.md"
FINDINGS = "01_FINDINGS.md"
HEADER_KEYS = ("Title", "Manufacturer", "Document number / revision / date",
               "Control generation (Haas)", "Models and years covered", "Source PDF filename",
               "Pages extracted (printed page numbers)", "Notes on applicability to our machines")
CONTROLS = ("Classic", "NGC", "both", "unknown", "n/a")
WARN_SIZE, FAIL_SIZE = 45_000, 60_000
MIN_CELL = 3                # an unanchored table's cells of this many characters are checked
_UP = r"[A-Z0-9]+(?:-[A-Z0-9]+)*"
NAME_RE = re.compile(rf"^(?P<maker>{_UP})_(?P<model>{_UP})_(?P<doctype>{_UP})"
                     r"_(?P<docno>[A-Za-z0-9.]+(?:-[A-Za-z0-9.]+)*)_Rev(?P<rev>[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*)"
                     r"_(?P<topic>[a-z0-9]+(?:-[a-z0-9]+)*)\.md$")
# [p. 32 / pdf 33], [p. 32–33 / pdf 33–34], [pdf 5], [haas-mill-2006 p. 32 / pdf 33]
TAG_RE = re.compile(r"\[(?:(?P<slug>[a-z0-9][a-z0-9-]*) )?(?:p\. (?P<p>[^\]/]+?) / )?"
                    r"pdf (?P<a>\d+)(?:\s*[–—-]\s*(?P<b>\d+))?\]")
REMARK_RE = re.compile(r"\[(?:APPLIES TO|OPTION|MODEL|GAP|NOTE):[^\]]*\]")
FIGURE_RE = re.compile(r"^\[Figure: (?P<desc>.+?)\s+[—–-]+\s+figures/(?P<file>[^\]\s]+)\]")
TABLE_NOTE_RE = re.compile(r"^\[Table: [^\]]*not transcribed[^\]]*\]")
SUMMARY_RE = re.compile(r"^\*Summary:\*\s*")
ITEM_RE = re.compile(r"^\s*(?:[-*+]|\d{1,3}[.)])\s+(.*)$")
SEP_RE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)*\|?\s*$")
LEVEL_RE = re.compile(r"^(?:WARNING|CAUTION|DANGER|NOTICE|NOTE|IMPORTANT)\s*[!:]?\s*")
CURLY_RE = re.compile(r"“([^”]+)”")
NUM_RE = re.compile(r"(?<![\w.])(?:(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?|\.\d+)(?:/\d+)?")
ELLIPSIS = "…"
# **bold**, _italic_ (PyMuPDF's table Markdown, "Shows_EDIT:MDI_ in"); a lone "**" is a
# footnote mark ("CE**"), underscores inside a name are not markup ("PROBE_SELECT_TO_PROBE")
EMPH_RE = re.compile(r"\*\*[^*\s][^*]*\*\*|(?<![A-Za-z0-9])_[^_\s|][^_|]*_"
                     r"|_[^_\s|][^_|]*_(?![A-Za-z0-9])")
GLUE_RE = re.compile(r"([A-Za-z]*[a-z])([A-Z]{2,}[A-Za-z]*)")   # "inSETUP"
_TRANS = str.maketrans({**{c: "'" for c in "‘’‚‛′"}, **{c: '"' for c in "“”„‟″"},
                        **{c: "-" for c in "‐‑‒–—―−⁃"}, "⁄": "/", "­": None, " ": " "})


class CheckError(Exception):
    pass


# ---------------------------------------------------------------------------- normalising
def norm(s: str) -> str:
    """Both sides of a comparison: NFKC, curly quotes straight, dashes to '-', soft hyphens
    out, Markdown emphasis, escapes and <br> out, whitespace collapsed, 'CON- TACT' joined.
    Case is kept."""
    s = unicodedata.normalize("NFKC", s).translate(_TRANS)
    s = s.replace("<br>", " ")
    s = re.sub(r"\\([\\`*_{}\[\]()#+\-.!|>])", r"\1", s)
    s = s.replace("**", "").replace("__", "").replace("*", "").replace("`", "")
    s = re.sub(r"\s+", " ", s).strip()
    return re.sub(r"(?<=[A-Za-z])- (?=[A-Za-z])", "", s)


def dehyphen(s: str) -> str:
    """A hyphen between letters out: a word the page breaks at a line end ('self-' /
    'contained') matches the word written whole, and the other way round."""
    return re.sub(r"(?<=[A-Za-z])-(?=[A-Za-z])", "", s)


def _num_text(s: str) -> str:
    return re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "", s)


def strip_marks(s: str) -> str:
    """The text a quote is matched with: page tags and the writer's remarks taken out."""
    return re.sub(r"\s+", " ", REMARK_RE.sub(" ", TAG_RE.sub(" ", s))).strip()


def fragments(raw: str) -> list[str]:
    return [f for f in (norm(p) for p in raw.split(ELLIPSIS)) if f]


def in_order(frags: list[str], hay: str) -> bool:
    pos = 0
    for f in frags:
        i = hay.find(f, pos)
        if i < 0:
            return False
        pos = i + len(f)
    return True


# ---------------------------------------------------------------------------- the pages
class Source:
    """One manual: its layer-1 page records and its PDF (opened read-only on first use)."""

    def __init__(self, entry: dict, pages_dir: Path, source_dir: Path):
        self.slug = entry["slug"]
        self.entry = entry
        path = pages_dir / f"{self.slug}.pages.jsonl"
        try:
            recs = [json.loads(l) for l in path.read_text("utf-8").splitlines() if l.strip()]
        except (OSError, ValueError) as e:
            raise CheckError(f"cannot read the page records {path}: {e}") from e
        self.records = {r["pdf"]: r for r in recs if r.get("type") != "file"}
        self.pdf_path = source_dir / entry["file"]
        self._doc = None
        self._hay: dict[tuple, dict] = {}

    def label(self, pdf: int):
        r = self.records[pdf]
        return r["label"], r["label_source"]

    def get_text(self, pdf: int) -> str:
        if self._doc is None:
            if not self.pdf_path.is_file():
                return ""
            self._doc = pymupdf.open(str(self.pdf_path))
        if 1 <= pdf <= self._doc.page_count:
            return self._doc[pdf - 1].get_text()
        return ""

    def tables(self, pdfs: tuple) -> list[list[list]]:
        return [b["cells"] for p in pdfs for b in self.records[p]["blocks"]
                if b["kind"] == "table" and b.get("cells")]

    def hay(self, pdfs: tuple) -> dict:
        """Normalised text of these pages, three ways (layer-1 block texts and cells, layer-1
        printed lines, the PDF's own text), each plain and with hyphens between letters out."""
        if pdfs in self._hay:
            return self._hay[pdfs]
        l1, lines, raw = [], [], []
        for p in pdfs:
            for b in self.records[p]["blocks"]:
                if b.get("text"):
                    l1.append(b["text"])
                for row in b.get("cells") or []:
                    l1.extend(c for c in row if c)
                for it in b.get("items") or []:
                    l1.append(it.get("label") or "")
                    l1.extend(it.get("values") or [])
                if b.get("caption"):
                    l1.append(b["caption"])
                l1.extend(b.get("labels") or [])
                if b.get("lines"):
                    lines.append(re.sub("­ ", "", " ".join(b["lines"])))
            raw.append(self.get_text(p))
        out = {}
        for k, parts in (("layer1", l1), ("lines", lines), ("pdf", raw)):
            t = norm(" ".join(parts))
            out[k] = (t, dehyphen(t))
        out["numbers"] = _num_text(" ".join(v[0] for v in out.values()))
        self._hay[pdfs] = out
        return out

    def close(self):
        if self._doc is not None:
            self._doc.close()
            self._doc = None


def quote_found(frags_raw: str, hay: dict) -> bool:
    frags = fragments(frags_raw)
    if not frags:
        return True
    dfr = [dehyphen(f) for f in frags]
    return any(in_order(frags, hay[k][0]) or in_order(dfr, hay[k][1])
               for k in ("layer1", "lines", "pdf"))


def number_found(n: str, hay: dict) -> bool:
    n = _num_text(n)
    forms = {n}
    if n.startswith("0."):
        forms.add(n[1:])
    elif n.startswith("."):
        forms.add("0" + n)
    return any(re.search(rf"(?<![\d.]){re.escape(f)}(?!\.?\d)", hay["numbers"]) for f in forms)


# ---------------------------------------------------------------------------- the extract
@dataclass
class Block:
    kind: str                   # heading, para, item, quote, table, code
    line: int
    text: str = ""
    rows: list = field(default_factory=list)     # table: (line, text) per row
    code: list = field(default_factory=list)     # code: (line, text) per line
    list_id: int = 0


def _starts_block(s: str) -> bool:
    t = s.lstrip()
    return t.startswith(("```", "|", ">")) or bool(re.match(r"^#{1,6}\s", s))


def parse_blocks(lines: list[str], start: int = 0) -> list[Block]:
    out: list[Block] = []
    i, n, list_id, in_list = start, len(lines), 0, False
    while i < n:
        s = lines[i]
        if not s.strip():
            i += 1
            continue
        if s.lstrip().startswith("```"):
            j, code = i + 1, []
            while j < n and not lines[j].lstrip().startswith("```"):
                code.append((j + 1, lines[j]))
                j += 1
            out.append(Block("code", i + 1, code=code))
            i, in_list = j + 1, False
            continue
        if re.match(r"^#{1,6}\s", s):
            out.append(Block("heading", i + 1, s))
            i, in_list = i + 1, False
            continue
        if s.lstrip().startswith("|"):
            rows = []
            while i < n and lines[i].lstrip().startswith("|"):
                rows.append((i + 1, lines[i]))
                i += 1
            out.append(Block("table", rows[0][0], rows=rows))
            in_list = False
            continue
        if s.lstrip().startswith(">"):
            j, parts = i, []
            while j < n and lines[j].lstrip().startswith(">"):
                parts.append(re.sub(r"^\s*>\s?", "", lines[j]))
                j += 1
            out.append(Block("quote", i + 1, " ".join(p for p in parts if p.strip())))
            i, in_list = j, False
            continue
        m = ITEM_RE.match(s)
        if m:
            if not in_list:
                list_id += 1
                in_list = True
            parts, j = [m.group(1)], i + 1
            while j < n and lines[j].strip() and not ITEM_RE.match(lines[j]) and not _starts_block(lines[j]):
                parts.append(lines[j].strip())
                j += 1
            out.append(Block("item", i + 1, " ".join(parts), list_id=list_id))
            i = j
            continue
        parts, j = [], i
        while j < n and lines[j].strip() and not _starts_block(lines[j]) and not ITEM_RE.match(lines[j]):
            parts.append(lines[j].strip())
            j += 1
        out.append(Block("para", i + 1, " ".join(parts)))
        i, in_list = j, False
    return out


def split_row(s: str) -> list[str]:
    s = s.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    return [c.strip() for c in re.split(r"(?<!\\)\|", s)]


def parse_header(lines: list[str]) -> tuple[dict, int, list[tuple[int, str]]]:
    """The header block: optional '# ' title lines, then the keys in order (blank lines
    between allowed, a '- ' prefix and bold allowed). Returns (values, the index after the
    block, errors as (line, message))."""
    vals, errs = {}, []
    i, n = 0, len(lines)
    while i < n and (not lines[i].strip() or lines[i].startswith("# ")):
        i += 1
    last = i
    for key in HEADER_KEYS:
        while i < n and not lines[i].strip():
            i += 1
        t = re.sub(r"^\s*[-*]\s+", "", lines[i]).replace("**", "") if i < n else ""
        if not t.startswith(key + ":"):
            errs.append((i + 1 if i < n else n, f"header line '{key}:' missing or out of order"))
            continue
        v = t[len(key) + 1:].strip()
        if not v:
            errs.append((i + 1, f"header line '{key}:' is empty"))
        vals[key] = v
        i += 1
        last = i
    return vals, last, errs


# ---------------------------------------------------------------------------- results
@dataclass
class FileResult:
    file: str
    chars: int = 0
    source: str | None = None
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    @property
    def status(self) -> str:
        return "fail" if self.errors else "pass"

    def err(self, line, rule, message, quote=None, cite=None):
        self.errors.append(_finding(line, rule, message, quote, cite))

    def warn(self, line, rule, message, quote=None, cite=None):
        self.warnings.append(_finding(line, rule, message, quote, cite))

    def to_dict(self) -> dict:
        return {"file": self.file, "status": self.status, "chars": self.chars,
                "source": self.source, "errors": self.errors, "warnings": self.warnings}


def _finding(line, rule, message, quote, cite) -> dict:
    if quote is not None and len(quote) > 300:
        quote = quote[:297] + "..."
    return {"line": line, "rule": rule, "message": message, "quote": quote, "cite": cite}


@dataclass
class Cite:
    slug: str
    pdfs: tuple
    text: str


# ---------------------------------------------------------------------------- checking
class Checker:
    def __init__(self, extracts: Path, pages_dir: Path, config: Path):
        try:
            cfg = json.loads(Path(config).read_text("utf-8"))
        except (OSError, ValueError) as e:
            raise CheckError(f"cannot read {config}: {e}") from e
        self.extracts = Path(extracts)
        self.pages_dir = Path(pages_dir)
        self.source_dir = Path(cfg.get("source", ""))
        self.entries = {m["slug"]: m for m in cfg.get("manuals", [])}
        self.by_file = {m["file"]: m["slug"] for m in cfg.get("manuals", [])}
        self.sources: dict[str, Source] = {}

    def source(self, slug: str) -> Source | None:
        if slug not in self.entries:
            return None
        if slug not in self.sources:
            self.sources[slug] = Source(self.entries[slug], self.pages_dir, self.source_dir)
        return self.sources[slug]

    def close(self):
        for s in self.sources.values():
            s.close()

    # -- tags
    def cites(self, text: str, line: int, res: FileResult, own: str | None) -> list[Cite]:
        out = []
        for m in TAG_RE.finditer(text):
            c = self._cite(m, line, res, own)
            if c is not None:
                out.append(c)
        return out

    def _cite(self, m, line, res, own) -> Cite | None:
        tag = m.group(0)
        slug = m.group("slug")
        if own is None and slug is None:
            res.err(line, "tag", "a findings tag must name the manual: [<slug> p. N / pdf N]", cite=tag)
            return None
        if own is not None and slug is not None and slug != own:
            res.err(line, "tag", f"tag names {slug}, but this file's source is {own}", cite=tag)
            return None
        slug = slug or own
        src = self.source(slug)
        if src is None:
            res.err(line, "tag", f"unknown manual {slug}", cite=tag)
            return None
        a = int(m.group("a"))
        b = int(m.group("b") or a)
        if b < a:
            res.err(line, "tag", "pdf range runs backwards", cite=tag)
            return None
        pdfs = tuple(range(a, b + 1))
        missing = [p for p in pdfs if p not in src.records]
        if missing:
            res.err(line, "tag", f"pdf {missing[0]} is not in the page records of {slug}", cite=tag)
            return None
        labels = [src.label(p) for p in (a, b)]
        printed = m.group("p")
        if printed is None:
            for p, (lab, _) in zip((a, b), labels):
                if lab is not None:
                    res.err(line, "tag", f"pdf {p} has the printed label {lab}: tag it "
                                         f"[p. {lab} / pdf {p}]", cite=tag)
                    return None
            return Cite(slug, pdfs, tag)
        parts = [x.strip() for x in re.split(r"[–—]", printed)]
        if len(parts) > 2 or (len(parts) == 2 and a == b) or (len(parts) == 1 and a != b
                                                             and labels[0][0] != labels[1][0]):
            res.err(line, "tag", "the printed range and the pdf range do not correspond", cite=tag)
            return None
        for part, p, (lab, how) in zip((parts[0], parts[-1]), (a, b), labels):
            if lab is None:
                res.err(line, "tag", f"pdf {p} has no printed label: tag it [pdf {p}]", cite=tag)
                return None
            if part != lab:
                res.err(line, "tag", f"pdf {p} is printed p. {lab}, not p. {part}", cite=tag)
                return None
            if how == "inferred":
                res.warn(line, "tag", f"the label of pdf {p} ({lab}) is inferred in layer 1: "
                                      "check it on the page", cite=tag)
        return Cite(slug, pdfs, tag)

    # -- one file
    def check_file(self, path: Path) -> FileResult:
        name = path.name
        text = path.read_text("utf-8")
        res = FileResult(name, chars=len(text))
        if len(text) > FAIL_SIZE:
            res.err(None, "size", f"{len(text)} characters (more than {FAIL_SIZE})")
        elif len(text) > WARN_SIZE:
            res.warn(None, "size", f"{len(text)} characters (more than {WARN_SIZE})")
        if name == MANIFEST:
            return res
        lines = text.splitlines()
        own = None
        start = 0
        if name != FINDINGS:
            if not NAME_RE.match(name):
                res.err(None, "name", "file name is not MAKER_MODEL_DOCTYPE_DocNo_Rev_topic.md")
            vals, start, errs = parse_header(lines)
            for ln, msg in errs:
                res.err(ln, "header", msg)
            ctl = vals.get("Control generation (Haas)", "")
            if ctl and not any(ctl == c or ctl.startswith(c + " ") for c in CONTROLS):
                res.err(None, "header", f"control generation '{ctl}' is not one of "
                                        + " / ".join(CONTROLS))
            fname = vals.get("Source PDF filename")
            if fname:
                own = self.by_file.get(fname)
                if own is None:
                    res.err(None, "header", f"source PDF '{fname}' is not a file in the config")
            if own is None:
                return res
            res.source = own
            if not self.source(own).pdf_path.is_file():
                res.warn(None, "source", f"{self.source(own).pdf_path} not found: quotes are "
                                         "matched against layer 1 only")
        self._check_blocks(parse_blocks(lines, start), res, own)
        return res

    def _check_blocks(self, blocks: list[Block], res: FileResult, own: str | None):
        lead: list[Cite] = []
        list_cites: dict[int, list[Cite]] = {}
        for b in blocks:
            if b.kind == "table":
                self._check_table(b, res, own, lead)
                lead = []
                continue
            if b.kind == "code":
                if not lead:
                    res.err(b.line, "tag", "a code block needs a page tag on the line above")
                else:
                    self._check_code(b, res, lead)
                lead = []
                continue
            cites = self.cites(b.text, b.line, res, own)
            bad_tag = len(cites) < len(TAG_RE.findall(b.text))
            if b.kind == "heading":
                lead = cites
                continue
            if b.kind == "item":
                if not cites:
                    cites = list_cites.get(b.list_id) or lead
                if cites:
                    list_cites[b.list_id] = cites
            lead = cites if b.kind == "para" else []
            body = strip_marks(b.text)
            if not body:
                continue        # a tag line, or only the writer's remarks ([GAP: ...])
            if not cites:
                if not bad_tag:
                    res.err(b.line, "tag", "no page tag", quote=body)
                continue
            self._check_content(b, body, cites, res)

    def _hay(self, cites: list[Cite]) -> dict:
        """The haystack of all cited pages (of one manual each; a findings line naming two
        manuals gets the pages of each, joined)."""
        by_slug: dict[str, set] = {}
        for c in cites:
            by_slug.setdefault(c.slug, set()).update(c.pdfs)
        hays = [self.source(s).hay(tuple(sorted(p))) for s, p in by_slug.items()]
        if len(hays) == 1:
            return hays[0]
        out = {k: (" ".join(h[k][0] for h in hays), " ".join(h[k][1] for h in hays))
               for k in ("layer1", "lines", "pdf")}
        out["numbers"] = " ".join(h["numbers"] for h in hays)
        return out

    def _check_content(self, b: Block, body: str, cites: list[Cite], res: FileResult):
        cite_txt = " ".join(c.text for c in cites)
        raw = TAG_RE.sub(" ", REMARK_RE.sub(" ", b.text)).strip()
        fm = FIGURE_RE.match(raw)
        if fm:
            f = fm.group("file")
            if not (self.extracts / "figures" / f).is_file():
                res.err(b.line, "figure", f"figures/{f} not found", cite=cite_txt)
            m = re.search(r"pdf(\d+)", f)
            if not m or int(m.group(1)) not in {p for c in cites for p in c.pdfs}:
                res.err(b.line, "figure", f"figures/{f} is not from the cited pdf page", cite=cite_txt)
            return
        if TABLE_NOTE_RE.match(raw):
            return
        hay = self._hay(cites)
        sm = SUMMARY_RE.match(raw)
        if sm:
            summary = raw[sm.end():]
            for q in CURLY_RE.findall(summary):
                if not quote_found(q, hay):
                    res.err(b.line, "verbatim", "quoted words not on the cited page", quote=norm(q),
                            cite=cite_txt)
            for n in NUM_RE.findall(norm(summary)):
                if not number_found(n, hay):
                    res.err(b.line, "number", f"number {n} not on the cited page", quote=norm(summary),
                            cite=cite_txt)
            return
        q = strip_marks(raw)
        if quote_found(q, hay):
            return
        nq = norm(q)
        if LEVEL_RE.match(nq) and quote_found(LEVEL_RE.sub("", nq, 1), hay):
            return
        res.err(b.line, "verbatim", "not on the cited page", quote=nq, cite=cite_txt)

    def _check_code(self, b: Block, res: FileResult, cites: list[Cite]):
        hay = self._hay(cites)
        for ln, t in b.code:
            t = strip_marks(t)
            if t and not quote_found(t, hay):
                res.err(ln, "verbatim", "code line not on the cited page", quote=norm(t),
                        cite=" ".join(c.text for c in cites))

    def _check_table(self, b: Block, res: FileResult, own, lead: list[Cite]):
        rows = b.rows
        head_line, head = rows[0]
        body = [(ln, t) for ln, t in rows[1:] if not SEP_RE.match(t)]
        tcites = self.cites(head, head_line, res, own) or lead
        if not tcites and not any(TAG_RE.search(t) for _, t in body):
            res.err(b.line, "tag", "a table needs a page tag (on the line above or in its header)")
            return
        if tcites:
            hay = self._hay(tcites)
            for cell in split_row(strip_marks(head)):
                for w in norm(cell).split():
                    w = w.strip("()[]{}:;,.")
                    if w and w not in hay["layer1"][0] and w not in hay["pdf"][0] and \
                            dehyphen(w) not in hay["pdf"][1]:
                        res.err(head_line, "table", f"header word '{w}' not on the cited page",
                                cite=" ".join(c.text for c in tcites))
        unanchored = False
        for ln, t in body:
            cites = self.cites(t, ln, res, own) or tcites
            if not cites:
                res.err(ln, "tag", "table row without a page tag")
                continue
            raw = [strip_marks(c) for c in split_row(t)]
            cells = [norm(c) for c in raw]
            cite_txt = " ".join(c.text for c in cites)
            glitch = cell_glitches(raw, self._hay(cites))
            if glitch:
                res.warn(ln, "table", "table cell: " + "; ".join(glitch), quote=" | ".join(raw),
                         cite=cite_txt)
            tables = [tb for c in cites for tb in self.source(c.slug).tables(c.pdfs)]
            if not tables:
                unanchored = True
                hay = self._hay(cites)
                for r, c in zip(raw, cells):
                    if len(c) >= MIN_CELL and not quote_found(r, hay):   # raw: "…" splits
                        res.err(ln, "table", f"cell '{c}' not on the cited page", quote=" | ".join(cells),
                                cite=cite_txt)
                continue
            if not any(row_in_table(cells, tb) for tb in tables):
                res.err(ln, "table", "row is not a row of a table on the cited page",
                        quote=" | ".join(cells), cite=cite_txt)
        if unanchored:
            res.warn(b.line, "table", "unanchored table: no layer-1 table on the cited page, "
                                      "cells checked one by one")


def cell_glitches(raw: list[str], hay: dict) -> list[str]:
    """Leftovers of PyMuPDF's table Markdown in a row's cells: <br>, emphasis markup, a
    lowercase letter glued to '[' or to an upper-case run that the page shows as two words."""
    out = []
    if any("<br>" in c for c in raw):
        out.append("<br> (write a line break inside a cell as a space)")
    if any(EMPH_RE.search(c) for c in raw):
        out.append("emphasis markup (** or _..._)")
    if any(re.search(r"[a-z]\[", c) for c in raw):
        out.append("a lowercase letter glued to '['")
    texts = [hay[k][0] for k in ("layer1", "lines", "pdf")]
    for c in raw:
        for m in GLUE_RE.finditer(norm(c)):
            glued, spaced = m.group(0), f"{m.group(1)} {m.group(2)}"
            if not any(glued in t for t in texts) and any(spaced in t for t in texts):
                out.append(f"'{glued}' is two words on the page ('{spaced}')")
    return out


def _cell_eq(a: str, b: str) -> bool:
    return a == b or dehyphen(a) == dehyphen(b)


def _origin(cells: list[list], r: int, j: int, dr: int, dc: int) -> str | None:
    """The cell a merged (null) cell continues: the nearest cell above (dr) or to the left
    (dc) that is not null; its text when it is not empty."""
    while 0 <= r < len(cells) and 0 <= j < len(cells[r]) and cells[r][j] is None:
        r, j = r - dr, j - dc
    if 0 <= r < len(cells) and 0 <= j < len(cells[r]) and cells[r][j]:
        return norm(cells[r][j])
    return None


def row_matches(ext: list[str], cells: list[list], r: int, cols: tuple) -> bool:
    row = cells[r]
    kept = set(cols)
    for j, c in enumerate(row):
        if j not in kept and c and norm(c):
            return False         # a dropped column must be empty in this row
    for e, j in zip(ext, cols):
        c = row[j]
        if c is None:
            if e and not any(o is not None and _cell_eq(e, o)
                             for o in (_origin(cells, r, j, 1, 0), _origin(cells, r, j, 0, 1))):
                return False
        elif not _cell_eq(e, norm(c)):
            return False
    return True


def row_in_table(ext: list[str], cells: list[list]) -> bool:
    """The extract row is one row of this layer-1 table, column by column; columns of the
    table may be left out where they are empty in that row."""
    width = max((len(r) for r in cells), default=0)
    if len(ext) > width:
        return False
    for r, row in enumerate(cells):
        if len(row) < len(ext):
            continue
        n = len(row)
        if len(ext) == n:
            if row_matches(ext, cells, r, tuple(range(n))):
                return True
            continue
        for cols in combinations(range(n), len(ext)):
            if row_matches(ext, cells, r, cols):
                return True
    return False


def check_manifest(extracts: Path, res: FileResult):
    text = (extracts / MANIFEST).read_text("utf-8")
    listed = set(re.findall(r"[A-Za-z0-9][A-Za-z0-9._-]*\.md", text)) - {MANIFEST, FINDINGS}
    present = {p.name for p in extracts.glob("*.md")} - {MANIFEST, FINDINGS}
    for f in sorted(present - listed):
        res.warn(None, "manifest", f"{f} is not listed in the manifest")
    for f in sorted(listed - present):
        res.warn(None, "manifest", f"{f} is listed but not in the folder")


# ---------------------------------------------------------------------------- run
@dataclass
class Result:
    files: list[FileResult]
    report: Path
    folder_warnings: list = field(default_factory=list)

    @property
    def failed(self) -> list[FileResult]:
        return [f for f in self.files if f.status == "fail"]


def run(extracts: Path, pages: Path, config: Path, report: Path, files: list = ()) -> Result:
    extracts = Path(extracts)
    if not extracts.is_dir():
        raise CheckError(f"no extracts folder {extracts}")
    report = Path(report)
    if report.resolve() == extracts.resolve() or extracts.resolve() in report.resolve().parents:
        raise CheckError("--report must not be inside the extracts folder")
    if files:
        paths = []
        for f in files:
            p = Path(f)
            if not p.is_file():
                p = extracts / f
            if not p.is_file():
                raise CheckError(f"no such extract {f}")
            paths.append(p)
    else:
        paths = sorted(extracts.glob("*.md"))
    checker = Checker(extracts, pages, config)
    res = Result([], report)
    try:
        for p in paths:
            fr = checker.check_file(p)
            if p.name == MANIFEST:
                check_manifest(extracts, fr)
            res.files.append(fr)
        if not files and not (extracts / MANIFEST).is_file():
            res.folder_warnings.append(_finding(None, "manifest", f"no {MANIFEST} in the folder", None, None))
    finally:
        checker.close()
    write_report(res, extracts, pages, config)
    return res


def _write(path: Path, text: str):
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def summary_line(res: Result) -> str:
    n_err = sum(len(f.errors) for f in res.files)
    n_warn = sum(len(f.warnings) for f in res.files) + len(res.folder_warnings)
    return (f"{len(res.files)} file(s): {len(res.files) - len(res.failed)} pass, "
            f"{len(res.failed)} fail; {n_err} error(s), {n_warn} warning(s)")


def write_report(res: Result, extracts, pages, config):
    res.report.mkdir(parents=True, exist_ok=True)
    data = {"made": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "made_with": f"g-pdf {__version__}", "extracts": str(extracts), "pages": str(pages),
            "config": str(config), "summary": summary_line(res),
            "folder_warnings": res.folder_warnings, "files": [f.to_dict() for f in res.files]}
    _write(res.report / "check.json", json.dumps(data, indent=1, ensure_ascii=False) + "\n")
    _write(res.report / "check.md", render_md(res))


def _md_finding(f: dict) -> str:
    where = f"line {f['line']}" if f["line"] else "file"
    s = f"- {where} [{f['rule']}] {f['message']}"
    if f.get("cite"):
        s += f"; cited {f['cite']}"
    if f.get("quote"):
        s += f"\n  > {f['quote']}"
    return s


def render_md(res: Result) -> str:
    out = ["# manual-check", "", f"**{summary_line(res)}**", "",
           "| file | status | KB | errors | warnings |", "|---|---|---|---|---|"]
    for f in res.files:
        out.append(f"| {f.file} | {f.status} | {f.chars / 1000:.1f} | {len(f.errors)} | {len(f.warnings)} |")
    for w in res.folder_warnings:
        out += ["", _md_finding(w)]
    for f in res.files:
        if not f.errors and not f.warnings:
            continue
        out += ["", f"## {f.file}: {f.status}"]
        if f.errors:
            out += ["", "Errors:"] + [_md_finding(e) for e in f.errors]
        if f.warnings:
            out += ["", "Warnings:"] + [_md_finding(w) for w in f.warnings]
    return "\n".join(out) + "\n"


def report(res: Result) -> str:
    lines = [summary_line(res) + f"; report in {res.report / 'check.md'}"]
    for f in res.files:
        if f.errors:
            lines.append(f"  FAIL {f.file}: {len(f.errors)} error(s)")
            for e in f.errors[:5]:
                where = f"line {e['line']}" if e["line"] else "file"
                lines.append(f"       {where} [{e['rule']}] {e['message']}")
    return "\n".join(lines)
