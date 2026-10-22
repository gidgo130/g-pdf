# SPDX-License-Identifier: AGPL-3.0-or-later
"""gpdf-cli structure, the assembly: the claims gathered by ``structure`` (contents pages,
verified bookmarks, running-head runs, heading units) become parts, sections and
subsections that tile every exported unit, each with a title, an author, a role, a
confidence and its evidence; the result is written as OUTDIR/<stem>.sections.json and
summed up in OUTDIR/structure.json. The export folder and the working files are only
read (the PDF for its outline, page count and metadata).

Levels: 1 = part (front matter, a bibliography, an anthology's critical part, notes,
plates, index, back), 2 = section (an essay, a text, a chapter, a canto, an act, a
country block), 3 = subsection (listed by a contents page or a bookmark, or a bibliography
or notes heading inside an essay). An implicit part that holds exactly one section
collapses into it (the play, a single essay, the notes).
"""

from __future__ import annotations

import json
import os
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from gpdf import __version__
from gpdf.pipeline import catalogue
from gpdf.pipeline import structure as st
from gpdf.pipeline.structure import (HIGH, LOW, MEDIUM, Claim, ContentsEntry, fold, name_like,
                                     ratio, title_match)

SCHEMA = 1
RUN_LOG = "structure.json"
SUFFIX = ".sections.json"
FRONT, EDITORIAL, PRIMARY, CRITICISM, NOTES, BIBLIOGRAPHY, INDEX, BACK, OTHER = st.ROLES
APPARATUS_EDGE = 0.2            # share of the file: contents pages live in the first or last
TEXTS_SHARE = 0.5               # share of authored contents entries: an anthology of texts
PART_ROLES = {BIBLIOGRAPHY, NOTES, INDEX, BACK, OTHER}

# Series-wide names of the critical anthologies' front matter (all six volumes): headers
# on a contents page, editorial sections in the front part.
SERIES_NAMES = ("cinco prefacios", "introduccion vanguardista tripartita",
                "senas de identidad de este tomo", "retrato convencional de este libro",
                "retrato no convencional")
CRITICISM_WORDS = re.compile(r"vanguard|teor[ií]a|cr[ií]tica|estudio|ensayo|identidad|"
                             r"cosmopolitismo|modernis|po[ée]tica|encounters|renovaci[oó]n")
_PUBLISHER = re.compile(r"editorial|ediciones|editores|press|universi|fondo de cultura|"
                        r"verlag|vervuert|iberoamericana|c[áa]tedra|biblioteca|nascimento|"
                        r"porr[úu]a|pen[íi]nsula|libros|publishing|imprenta", re.I)
_YEAR = re.compile(r"(?<!\d)(1[89]\d\d|20[0-2]\d)(?!\d)")
_COPYRIGHT = re.compile(r"(©|\(c\)|copyright|d\.\s*r\.|derechos reservados|primera edici[oó]n|"
                        r"first published)", re.I)
_JUNK_TITLE = re.compile(r"^\W*$|\.(doc|p65|pdf|indd)\b|^aaa+$|^untitled|^microsoft word", re.I)
_USERNAME = re.compile(r"^[a-z0-9_.-]+$|^(user|usuario|admin|owner)$", re.I)
_TITLE_WORDS = re.compile(r"manifiest|vanguard|poes|literatur|novela|ensayo|teor|critic|"
                          r"historia|leyenda|antolog|obras|textos|revista|cuentos|edicion|"
                          r"segunda|primera", re.I)
_SEQ = re.compile(r"^(?P<kind>acto|cuadro|canto|cap[íi]tulo|parte|libro|nota extensa|escena)"
                  r"\s+(?P<n>[ivxlc]+|\d+|primer[oa]?|segund[oa]|tercer[oa]?|cuart[oa]|quint[oa]|"
                  r"sext[oa]|s[ée]ptim[oa]|octav[oa]|noven[oa]|d[ée]cim[oa])\b", re.I)
_ORDINALS = {"primer": 1, "primero": 1, "primera": 1, "segundo": 2, "segunda": 2, "tercer": 3,
             "tercero": 3, "tercera": 3, "cuarto": 4, "cuarta": 4, "quinto": 5, "quinta": 5,
             "sexto": 6, "sexta": 6, "septimo": 7, "septima": 7, "octavo": 8, "octava": 8,
             "noveno": 9, "novena": 9, "decimo": 10, "decima": 10}
SEQ_LEVEL = {"acto": 2, "cuadro": 3, "canto": 2, "capitulo": 2, "parte": 2, "libro": 2,
             "nota extensa": 3, "roman": 2, "roman3": 3}
_ROMAN_HEADING = re.compile(r"^[IVXLC]{1,6}$|^[ivxlc]{1,6}$")


class StructureError(Exception):
    """A refusal or failure with a message meant for the user."""


# ---------------------------------------------------------------------------- roles
def role_of_title(title: str) -> tuple[str | None, bool]:
    """(role, part_like) a title's words declare, else (None, False)."""
    f = fold(title).strip(" .:")
    if re.match(r"^(indice onomastico|index)\b", f) or f in ("indice de nombres",
                                                               "indice alfabetico"):
        return INDEX, True
    if re.match(r"^(bibliograf|bibliography|works cited|obras citadas|referencias|"
                r"obras basicas de referencia|fuentes$)", f):
        return BIBLIOGRAPHY, True
    if re.match(r"^(notas|notes|notas extensas|endnotes|notes to pages|notas al pie)$", f) or \
            f.startswith("notas extensas"):
        return NOTES, True
    if re.match(r"^(titulos publicados|otros titulos|colofon|este volumen|esta edicion se termino|"
                r"se termino de imprimir|lista de titulos|serie )", f) or "se termino de imprimir" in f:
        return BACK, True
    if re.match(r"^(laminas|ilustraciones|plates|imagenes|figuras|cuadernillo)", f):
        return OTHER, True
    if re.match(r"^(antologia critica|textos criticos)", f):
        return CRITICISM, True
    if re.match(r"^(manifiestos|revistas|antologias: prologos y posfacio|textos programaticos)$", f):
        return PRIMARY, True
    if f in ("indice", "index", "contents", "sumario", "tabla de contenido", "indice general"):
        return FRONT, True
    if f in ("lista de autores", "lista de colaboradores", "colaboradores", "los autores"):
        return OTHER, True
    if re.match(r"^(agradecimientos|acknowledg|nota preliminar|advertencia|criterio de esta "
                r"edicion|criterios de edicion|avisos del lengua|preludios|cabeceras|"
                r"list of abbreviations|abreviaturas|presentacion|nota del editor|nota a la "
                r"edicion|nota a esta edicion|nota editorial|prefacio del editor|introduccion "
                r"vanguardista|la vanguardia en telegrama|preguntas sin respuestas|diez puntos|"
                r"senas de identidad|retrato (no )?convencional|introduccion a las vanguardias|"
                r"cinco prefacios|dedicatoria|a modo de prologo)", f):
        return EDITORIAL, False
    return None, False


def default_role(entry_title: str) -> str:
    return CRITICISM if CRITICISM_WORDS.search(fold(entry_title)) else PRIMARY


# ---------------------------------------------------------------------------- entry
@dataclass
class Entry:
    id: str
    stem: str
    input: str
    input_sha: str
    kind: str                        # pdf / txt
    units: list
    rows: list
    apparatus: list
    toc: list
    page_count: int
    metadata: dict
    lang: str | None = None
    rel: str = ""
    flags: dict = field(default_factory=dict)


def _jsonl(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def load_export(text_dir) -> dict:
    path = Path(text_dir) / "export.json"
    if not path.is_file():
        raise StructureError(f"no export.json in {text_dir}: run gpdf-cli export first")
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError) as e:
        raise StructureError(f"cannot read {path}: {e}") from None
    if not isinstance(data, dict) or not isinstance(data.get("entries"), dict):
        raise StructureError(f"{path} is not an export log")
    return data


def load_entry(text_dir, id_: str, rec: dict) -> Entry:
    """One entry's units, page rows and apparatus, plus the working PDF's outline, page
    count and metadata (the file is opened read-only and closed)."""
    text_dir = Path(text_dir)
    stem = rec["stem"]
    units = _jsonl(text_dir / f"{stem}.jsonl")
    rows = _jsonl(text_dir / f"{stem}.pages.jsonl")
    app_path = text_dir / f"{stem}.apparatus.jsonl"
    apparatus = _jsonl(app_path) if app_path.is_file() else []
    kind = "txt" if str(rec["input"]).lower().endswith(".txt") else "pdf"
    toc, count, meta = [], 0, {}
    if kind == "pdf" and os.path.isfile(rec["input"]):
        import pymupdf

        with pymupdf.open(rec["input"]) as doc:
            toc = [list(t[:3]) for t in doc.get_toc()]
            count = len(doc)
            meta = {k: v for k, v in (doc.metadata or {}).items() if v}
    langs = Counter(u.get("lang") for u in units if u.get("lang"))
    return Entry(id=id_, stem=stem, input=str(rec["input"]), input_sha=rec.get("input_sha256", ""),
                 kind=kind, units=units, rows=rows, apparatus=apparatus, toc=toc,
                 page_count=count or len(rows), metadata=meta,
                 lang=langs.most_common(1)[0][0] if langs else None, rel=rec.get("rel", ""),
                 flags=rec.get("flags", {}))


# ---------------------------------------------------------------------------- sections
@dataclass
class Section:
    id: str = ""
    parent: str | None = None
    level: int = 1
    title: str = ""
    author: str | None = None
    role: str = PRIMARY
    first: int = 0                   # unit seq (1-based)
    last: int = 0
    start: int = 0                   # unit index (0-based)
    end: int = 0
    pdf_pages: list | None = None
    printed: list | None = None
    lines: list | None = None
    lang: str | None = None
    confidence: str = MEDIUM
    evidence: list = field(default_factory=list)
    sources: list = field(default_factory=list)
    chars: int = 0
    part_like: bool = False
    implicit: bool = False
    role_fixed: bool = False
    rel_level: int | None = None
    contents_page: int | None = None
    authored: bool = False

    def to_json(self) -> dict:
        d = {"id": self.id, "parent": self.parent, "level": self.level, "title": self.title,
             "author": self.author, "role": self.role, "first": self.first, "last": self.last,
             "pdf_pages": self.pdf_pages, "printed": self.printed, "lang": self.lang,
             "confidence": self.confidence, "evidence": list(self.evidence),
             "sources": list(self.sources), "chars": self.chars}
        if self.lines is not None:
            d["lines"] = self.lines
        return d


@dataclass
class Structure:
    entry: Entry
    sections: list = field(default_factory=list)
    by_id: dict = field(default_factory=dict)
    outline: st.OutlineResult = field(default_factory=st.OutlineResult)
    contents: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)
    kind: str = "monograph"
    default_role: str = PRIMARY
    checks: dict = field(default_factory=dict)

    def to_json(self) -> dict:
        e = self.entry
        return {"schema": SCHEMA, "tool_version": __version__, "made": catalogue.now(),
                "entry": e.id, "stem": e.stem, "input": e.input, "input_sha256": e.input_sha,
                "kind": e.kind, "book_kind": self.kind, "pages": e.page_count,
                "units": len(e.units), "lang": e.lang, "metadata": self.metadata,
                "sections": [s.to_json() for s in self.sections],
                "outline": {
                    "junk": self.outline.junk,
                    "confirmed": [{"title": c.title, "page": c.page} for c in self.outline.confirmed],
                    "reanchored": [{"title": c.title, "from": old, "to": c.page,
                                    "evidence": c.evidence} for c, old in self.outline.reanchored],
                    "unconfirmed": [{"title": c.title, "page": c.page} for c in self.outline.unconfirmed],
                    "dropped": [{"title": c.title, "page": c.page, "why": why}
                                for c, why in self.outline.dropped]},
                "contents": self.contents, "notes": list(self.notes), "checks": self.checks}


# ---------------------------------------------------------------------------- detection
@dataclass
class _Group:
    idx: int
    part_like: bool
    claims: list = field(default_factory=list)       # (claim, how)
    title: str = ""
    author: str | None = None
    sources: set = field(default_factory=set)
    evidence: list = field(default_factory=list)
    confidence: str = MEDIUM
    role_hint: str | None = None
    rel_level: int | None = None
    contents_page: int | None = None
    sub: bool = False                                 # a subsection (level 3) claim
    seq_level: int | None = None
    authored: bool = False
    page: int = 0


_SOURCE_RANK = {"contents": 0, "bookmark": 1, "head": 2, "heading": 3, "header": 4,
                "inferred": 5}


def _entry_title(e: Entry, on: dict) -> str | None:
    t = (e.metadata or {}).get("title") or ""
    if t and not _JUNK_TITLE.search(t) and len(t.split()) >= 2:
        if " - " in t and len(t.split(" - ")) == 2:        # "Asturias, Miguel Angel - Leyendas"
            t = t.split(" - ")[1]
        return t.strip()
    author = _entry_author(e, on) or ""
    for pg in range(1, 5):
        heads = [u["text"].strip() for u in on.get(pg, []) if u["kind"] == "heading"
                 and len(u["text"].split()) >= 2 and len(u["text"]) <= 80
                 and not _PUBLISHER.search(u["text"])
                 and not (author and ratio(u["text"], author) >= 0.8)]
        if heads:
            return heads[0]
    return None


def _entry_author(e: Entry, on: dict) -> str | None:
    a = (e.metadata or {}).get("author") or ""
    if a and not _USERNAME.match(a.strip()) and len(a.split()) >= 2:
        return a.strip()
    t = (e.metadata or {}).get("title") or ""
    if " - " in t and len(t.split(" - ")) == 2 and "," in t.split(" - ")[0]:
        sur, given = t.split(" - ")[0].split(",", 1)
        return f"{given.strip()} {sur.strip()}"
    for pg in range(1, 4):
        for u in on.get(pg, []):
            s = u["text"].strip().rstrip(",")
            if (name_like(s) and not _PUBLISHER.search(s) and "\n" not in s
                    and not _TITLE_WORDS.search(fold(s))):
                return s
    return None


def _metadata(e: Entry, on: dict) -> dict:
    year = publisher = None
    for pg in range(1, 8):
        for u in on.get(pg, []):
            t = u["text"]
            if _COPYRIGHT.search(t):
                m = _YEAR.search(t)
                if m and not year:
                    year = m.group(1)
                after = re.split(r"©|\(c\)|D\.\s*R\.", t, flags=re.I)
                cand = after[1] if len(after) > 1 else t
                cand = _YEAR.sub("", cand).strip(" ,.:;-")
                cand = re.split(r"[,.;\n]", cand)[0].strip()
                if cand and _PUBLISHER.search(cand) and not publisher and len(cand) <= 60:
                    publisher = cand
    if not publisher:
        for pg in range(1, 5):
            for u in on.get(pg, []):
                t = u["text"].strip()
                if _PUBLISHER.search(t) and len(t) <= 50 and "\n" not in t and not _YEAR.search(t):
                    publisher = t
                    break
            if publisher:
                break
    return {"title": _entry_title(e, on), "author": _entry_author(e, on), "year": year,
            "publisher": publisher, "confirmed": False,
            "evidence": "prefilled from the PDF metadata and the title and copyright pages; "
                        "not confirmed"}


def _contents_claims(e: Entry, lm: st.LabelMap, on: dict, grouping: set, stats: dict,
                     units: list) -> list[Claim]:
    """Claims from the contents and index pages: entries resolved to PDF pages in order;
    headers get the page of their first child but look for their own text just before it;
    items without a page are found by their heading inside the preceding entry's range."""
    claims = []
    pages_total = e.page_count or len(e.rows)
    prev_page = 0
    headers = set(SERIES_NAMES) | {fold(g) for g in grouping} | {
        "bibliografia", "antologia critica", "indice onomastico", "manifiestos", "revistas",
        "antologias: prologos y posfacio", "textos criticos", "notas", "notes", "index"}
    for a in e.apparatus:
        if a.get("kind") not in ("contents", "index"):
            continue
        lines = a.get("lines") or []
        if st.is_name_index(lines):
            stats["skipped"].append(f"p{a['page']}: name index")
            continue
        frac = a["page"] / max(1, pages_total)
        if APPARATUS_EDGE < frac < 1 - APPARATUS_EDGE:
            stats["skipped"].append(f"p{a['page']}: an in-text index, not the book's contents")
            continue
        entries = st.parse_contents(lines, a.get("columns", 1), headers)
        if not entries:
            stats["skipped"].append(f"p{a['page']}: {len(lines)} lines, no entries "
                                    + ("(two columns: counts differ)" if a.get("columns") == 2
                                       else "(nothing parsed)"))
            continue
        stats["pages"].append(a["page"])
        pending_headers: list[ContentsEntry] = []
        last_entry: ContentsEntry | None = None
        ctx: list[tuple[int, int]] = []          # (relative level, absolute level) of headers
        for en in entries:
            if en.header:
                pending_headers.append(en)
                continue
            while ctx and ctx[-1][0] >= en.level:
                ctx.pop()
            level = (ctx[-1][1] + (en.level - ctx[-1][0])) if ctx else (1 + en.level)
            level = max(2, min(3, level))
            stats["parsed"] += 1
            page = lm.page(en.label, after=prev_page) if en.label else None
            if page is not None and page < prev_page - 2:
                stats["out_of_order"].append(f"«{en.title[:40]}» {en.label} -> p{page} after p{prev_page}")
                page = None
            if page is None and en.label:
                stats["unresolved"].append(f"«{en.title[:40]}» {en.label}")
            if page is None:
                # an item without a page: its heading inside the previous entry's range
                if last_entry is not None and last_entry.page:
                    hit = _heading_after(en.title, last_entry.page, on, prev_page)
                    if hit:
                        page = hit
            if page is None:
                continue
            for h in pending_headers:
                hp = _header_page(h.title, prev_page, page, on)
                while ctx and ctx[-1][0] >= h.level:
                    ctx.pop()
                part_like = role_of_title(h.title)[1]
                h_level = 1 if part_like else (ctx[-1][1] + 1 if ctx else 2)
                h_level = min(3, h_level)
                # a header without a unit of its own shares the first entry's unit: a
                # grouping, so its children take its level
                ctx.append((h.level, h_level if hp or part_like else h_level - 1))
                claims.append(Claim(h.title, hp or page, "header", level=h_level, evidence=[
                    f"contents p{a['page']} header «{h.title}» before «{en.title[:40]}» {en.label}"],
                    key=str(a["page"])))
            pending_headers = []
            while ctx and ctx[-1][0] >= en.level:
                ctx.pop()
            level = (ctx[-1][1] + (en.level - ctx[-1][0])) if ctx else (1 + en.level)
            level = max(2, min(3, level))
            en.page = page
            c = Claim(en.title, page, "contents", level=level, author=en.author,
                      evidence=[f"contents p{a['page']}: «{en.title[:60]}» {en.label or ''} -> p{page}"],
                      key=str(a["page"]), label=en.label)
            claims.append(c)
            prev_page = max(prev_page, page)
            last_entry = en
        for h in pending_headers:                         # trailing headers: no entry under them
            claims.append(Claim(h.title, prev_page, "header", level=h.level,
                                evidence=[f"contents p{a['page']} trailing header «{h.title}»"],
                                key=str(a["page"])))
    return claims


def _heading_after(title: str, page: int, on: dict, limit_prev: int) -> int | None:
    """The first page >= ``page`` with a heading that carries ``title`` (an inline item)."""
    for pg in sorted(q for q in on if q >= page):
        if pg > page + 60:
            break
        for u in on[pg]:
            if u["kind"] == "heading" and st._heading_matches(title, u["text"]):
                return pg
    return None


def _header_page(title: str, after: int, before: int, on: dict) -> int | None:
    """A short unit with the header's text on a page in (after, before]: the part's own
    title page (Chile "ANTOLOGÍA CRÍTICA" on p. 255)."""
    for pg in range(before, max(after, before - 6), -1):
        for u in on.get(pg, []):
            if len(u["text"]) <= 80 and ratio(title, u["text"]) >= 0.85:
                return pg
    return None


def _heading_claims(units: list, on: dict, pos: dict) -> list[Claim]:
    """Claims from heading units alone: role headings (a bibliography or notes inside an
    essay, an index, a series list) and numbered sequences (acts, cuadros, cantos, notas
    extensas); scenes are not sections."""
    claims = []
    for u in units:
        if u["kind"] != "heading":
            continue
        t = u["text"].strip()
        pg = (u.get("pages") or [1])[0]
        role, part_like = role_of_title(t)
        if role in PART_ROLES or role == FRONT and False:
            claims.append(Claim(t, pg, "heading", evidence=[f"heading «{t[:50]}» on p{pg}"]))
            continue
        m = _SEQ.match(t)
        if m and m.group("kind").lower() != "escena" and len(t) <= 60:
            kind = fold(m.group("kind"))
            claims.append(Claim(t, pg, "heading", evidence=[f"heading «{t[:50]}» on p{pg}"],
                                key=f"seq:{kind}:{_seq_number(m.group('n'))}"))
    return claims


def _seq_number(tok: str) -> int | None:
    t = fold(tok)
    if t.isdigit():
        return int(t)
    if t in _ORDINALS:
        return _ORDINALS[t]
    return st._roman(t)


def _roman_claims(units: list, on: dict) -> list[Claim]:
    """Standalone roman-numeral headings (Ariel's I-IV) as numbered sections."""
    claims = []
    for u in units:
        if u["kind"] == "heading" and _ROMAN_HEADING.match(u["text"].strip()):
            n = st._roman(u["text"].strip())
            if n:
                pg = (u.get("pages") or [1])[0]
                claims.append(Claim(u["text"].strip().upper(), pg, "heading",
                                    evidence=[f"roman numeral heading «{u['text'].strip()}» on p{pg}"],
                                    key=f"seq:roman:{n}"))
    # the sequence sits inside the section that opens on its first page (a prologue's
    # numbered parts) or stands on its own
    if claims:
        first = claims[0]
        same_page = [u for u in on.get(first.page, []) if u["kind"] == "heading"
                     and not _ROMAN_HEADING.match(u["text"].strip())]
        if same_page:
            for c in claims:
                c.key = c.key.replace("seq:roman:", "seq:roman3:")
    return claims


def detect(e: Entry, decisions: dict | None = None) -> Structure:
    """The sections of one entry (see the module docstring)."""
    s = Structure(entry=e)
    units = e.units
    on = st.units_on(units)
    pos = {id(u): i for i, u in enumerate(units)}
    s.metadata = _metadata(e, on)
    title = s.metadata.get("title") or e.stem
    if e.kind == "txt" or not units:
        return _txt_structure(s, title)
    lm = st.LabelMap(e.rows)
    # 1. claims
    grouping = {t[1] for t, n in zip(e.toc, e.toc[1:]) if t[2] == n[2]}
    stats = {"pages": [], "parsed": 0, "matched": 0, "skipped": [], "out_of_order": [],
             "unresolved": []}
    c_claims = _contents_claims(e, lm, on, grouping, stats, units)
    contents_entries = [ContentsEntry(c.title, c.author, c.label, c.level or 1, page=c.page)
                        for c in c_claims if c.source == "contents"]
    s.outline = st.verify_outline(e.toc, e.rows, units, contents_entries)
    b_claims = list(s.outline.kept)
    h_claims = st.head_boundaries(e.rows, units)
    g_claims = _heading_claims(units, on, pos) + _roman_claims(units, on)
    # 2. anchor every claim to a unit
    groups: dict[tuple, _Group] = {}
    unplaced = []
    for c in c_claims + b_claims + h_claims + g_claims:
        if c.page < 1:
            continue
        has_units = bool(on.get(c.page) or on.get(c.page + 1))
        if not has_units:
            unplaced.append(c)
            continue
        idx, how = st.anchor(c, units, on, pos)
        if c.source == "contents" and (how.startswith("heading") or how.startswith("title")):
            stats["matched"] += 1
        role, part_like = role_of_title(c.title)
        if c.source == "heading" and role in (BIBLIOGRAPHY, NOTES):
            part_like = False                      # a subsection or a part: decided below
        key = (idx, part_like)
        g = groups.setdefault(key, _Group(idx, part_like))
        g.claims.append((c, how))
    s.contents = {"pages": stats["pages"], "parsed": stats["parsed"], "matched": stats["matched"],
                  "skipped": stats["skipped"], "out_of_order": stats["out_of_order"],
                  "unresolved": stats["unresolved"]}
    for c in unplaced:
        why = f"p{c.page} carries no units (apparatus or blank pages)"
        s.outline.reanchored = [(x, o) for x, o in s.outline.reanchored if x is not c]
        if c in s.outline.confirmed:
            s.outline.confirmed.remove(c)
        if c in s.outline.unconfirmed:
            s.outline.unconfirmed.remove(c)
        if c.source == "bookmark":
            s.outline.dropped.append((c, why))
        s.notes.append(f"claim «{c.title[:50]}» ({c.source}) dropped: {why}")
    # 3. merge the claims of each unit, then near-duplicates on one page
    merged = [_merge(g, units) for g in groups.values()]
    merged.sort(key=lambda g: (g.idx, not g.part_like))
    merged = _fold_duplicates(merged, units)
    # 4. the book's kind and default role
    authored = [g for g in merged if g.authored]
    content_groups = [g for g in merged if "contents" in g.sources and not g.part_like]
    if any(role_of_title(g.title)[0] == CRITICISM and g.part_like for g in merged):
        s.kind = "anthology"
    elif len(authored) >= 2 and content_groups and len(authored) >= TEXTS_SHARE * len(content_groups):
        s.kind = "texts"
    else:
        s.kind = "monograph"
    s.default_role = CRITICISM if s.kind == "anthology" else (
        PRIMARY if s.kind == "texts" else default_role(title + " " + e.stem))
    # 5. sections
    _sequences(merged, units, on, pos, s)
    secs = _build(merged, units, s, title)
    _finish(secs, units, e, s)
    s.sections = secs
    s.by_id = {x.id: x for x in secs}
    _apparatus_notes(e, s)
    s.checks = _checks(s)
    return s


def _merge(g: _Group, units: list) -> _Group:
    """One group's claims into a title, author, sources, evidence and confidence."""
    ordered = sorted(g.claims, key=lambda ch: (_SOURCE_RANK.get(ch[0].source, 9),
                                               -len(ch[0].title)))
    titles = [c for c, _ in ordered if c.source != "header"] or [c for c, _ in ordered]
    g.title = titles[0].title
    if g.title.isupper():                      # the same name in mixed case, if any source has it
        for c, _ in ordered:
            if fold(c.title) == fold(g.title) and not c.title.isupper():
                g.title = c.title
                break
    for c, _ in ordered:                       # a header grouping ("Cuba") noted, not the title
        if c.source == "header" and c.title != g.title and not g.part_like:
            g.evidence.append(f"under the contents header «{c.title}»")
    for c, _ in ordered:
        if c.author and not g.author:
            g.author = c.author
    g.sources = {c.source for c, _ in g.claims}
    strength = 0.0
    heading_hit = False
    for c, how in g.claims:
        g.evidence.extend(c.evidence)
        g.evidence.append(f"{c.source} anchored: {how}")
        if how.startswith(("heading", "title")) and c.source != "heading":
            heading_hit = True
        if c.source == "contents":
            strength += 1 if how.startswith(("heading", "title")) else 0.5
            g.contents_page = int(c.key) if c.key.isdigit() else None
            g.rel_level = c.level
            g.authored = g.authored or bool(c.author)
        elif c.source == "bookmark":
            strength += 1 if c.confidence == HIGH else 0.5
        elif c.source == "head":
            strength += 1
        elif c.source == "heading":
            strength += 0.5
            if c.key.startswith("seq:"):
                g.seq_level = SEQ_LEVEL.get(c.key.split(":")[1], 2)
                strength += 0.5
        elif c.source == "header":
            strength += 0.5
            if g.rel_level is None:
                g.rel_level = c.level
    g.sub = g.sources == {"heading"} and not any(c.key.startswith("seq:") for c, _ in g.claims)
    if heading_hit:
        strength += 1
    hows = [how for _, how in g.claims]
    if strength < 1 and all(h.startswith("first unit") or h.startswith("last unit") for h in hows):
        g.confidence = LOW
        g.evidence.append("low: the title was not found on the page; the section starts at "
                          "the page's first unit")
    else:
        g.confidence = HIGH if strength >= 2 else MEDIUM
    g.role_hint = role_of_title(g.title)[0]
    g.page = (units[g.idx].get("pages") or [0])[0]
    seen, ev = set(), []
    for x in g.evidence:
        if x not in seen:
            seen.add(x)
            ev.append(x)
    g.evidence = ev
    return g


def _fold_duplicates(merged: list[_Group], units: list) -> list[_Group]:
    """Two groups on one page with one title (the bookmark at the heading, the contents
    entry at the author line) become one at the earlier unit."""
    out: list[_Group] = []
    for g in merged:
        prev = out[-1] if out else None
        if (prev is not None and prev.part_like == g.part_like and prev.page == g.page
                and (ratio(prev.title, g.title) >= 0.8
                     or (title_match(prev.title, g.title) and title_match(g.title, prev.title)))):
            prev.claims += g.claims
            prev.sources |= g.sources
            prev.evidence += [x for x in g.evidence if x not in prev.evidence]
            prev.author = prev.author or g.author
            prev.authored = prev.authored or g.authored
            prev.rel_level = prev.rel_level if prev.rel_level is not None else g.rel_level
            prev.contents_page = prev.contents_page or g.contents_page
            prev.seq_level = prev.seq_level or g.seq_level
            prev.sub = prev.sub and g.sub
            if g.confidence == HIGH or (prev.confidence == LOW and g.confidence == MEDIUM):
                prev.confidence = g.confidence
            if len(g.title) > len(prev.title) and "contents" in g.sources:
                prev.title = g.title
            continue
        out.append(g)
    return out


def _sequences(merged: list[_Group], units: list, on: dict, pos: dict, s: Structure) -> None:
    """A numbered sequence (ACTO II, CUADRO SEGUNDO) whose first member has no heading:
    the first member is inferred at the start of the body (acts) or at its parent's start
    (cuadros), with low confidence."""
    by_kind: dict[str, list[tuple[int, _Group]]] = {}
    for g in merged:
        for c, _ in g.claims:
            if c.key.startswith("seq:"):
                _, kind, n = c.key.split(":")
                if n != "None":
                    by_kind.setdefault(kind, []).append((int(n), g))
    for kind, members in by_kind.items():
        members.sort(key=lambda m: m[1].idx)
        numbers = [n for n, _ in members]
        if not numbers or numbers[0] == 1 or kind.startswith("roman"):
            if kind.startswith("roman") and numbers:
                s.notes.append(f"roman-numeral sections {min(numbers)}-{max(numbers)} found; "
                               f"no standalone unit for {max(numbers) + 1} and later")
            continue
        first_idx = members[0][1].idx
        if kind == "cuadro":
            acts = [g for g in merged if g.seq_level == 2 and g.idx < first_idx]
            start = acts[-1].idx if acts else _body_start(units, on)
        else:
            start = _body_start(units, on)
        if start >= first_idx:
            continue
        label = {"acto": "Acto", "cuadro": "Cuadro", "canto": "Canto", "capitulo": "Capítulo",
                 "parte": "Parte", "libro": "Libro"}.get(kind, kind.title())
        g = _Group(start, False)
        g.title = f"{label} I"
        g.sources = {"inferred"}
        g.confidence = LOW
        g.seq_level = SEQ_LEVEL.get(kind, 2)
        g.evidence = [f"inferred: the {kind} headings start at {numbers[0]}; no heading for "
                      f"the first one was found, so it starts where the text starts"]
        g.page = (units[start].get("pages") or [0])[0]
        merged.append(g)
    merged.sort(key=lambda g: (g.idx, not g.part_like))


def _body_start(units: list, on: dict) -> int:
    """The first unit after the title page: the first "Escena" heading, else the first
    unit on a page after the first page with units."""
    for i, u in enumerate(units):
        if u["kind"] == "heading" and fold(u["text"]).startswith("escena"):
            return i
    first_page = (units[0].get("pages") or [1])[0]
    for i, u in enumerate(units):
        if (u.get("pages") or [1])[0] > first_page:
            return i
    return 0


def _build(merged: list[_Group], units: list, s: Structure, entry_title: str) -> list[Section]:
    """Groups in unit order into parts, sections and subsections."""
    secs: list[Section] = []
    part: Section | None = None
    last2: Section | None = None
    authored_levels = Counter(g.rel_level or 2 for g in merged if g.authored)
    texts_level = authored_levels.most_common(1)[0][0] if authored_levels else None

    def new_section(g: _Group | None, level: int, title: str, role: str | None, **kw) -> Section:
        x = Section(level=level, title=title, role=role or s.default_role, **kw)
        if g is not None:
            x.start = g.idx
            x.author = g.author
            x.evidence = list(g.evidence)
            x.sources = sorted(g.sources)
            x.confidence = g.confidence
            x.part_like = g.part_like
            x.authored = g.authored
            x.rel_level = g.rel_level
        return x

    def role_for(g: _Group, level: int, parent: Section | None, enclosing: Section) -> tuple[str, bool]:
        role, _ = role_of_title(g.title)
        f = fold(g.title)
        if role:
            return role, True
        if re.match(r"^(cuatro|tres|dos|cinco|seis) ensayos|^ensayos\b", f):
            return CRITICISM, True
        if s.kind == "texts":
            if re.match(r"^(prologo|introduccion|introduction|presentacion|estudio preliminar|"
                        r"nota preliminar|epilogo|posfacio)\b", f):
                return CRITICISM, True
            if g.authored:
                return (PRIMARY if texts_level in (None, level) else CRITICISM), True
            if (texts_level is not None and level == texts_level
                    and (parent is None or not parent.role_fixed or parent.role == PRIMARY)
                    and enclosing.role in (PRIMARY, CRITICISM)):
                return PRIMARY, False
            return CRITICISM, False
        if s.kind == "monograph" and s.default_role == PRIMARY and re.match(
                r"^(prologo|estudio preliminar|introduccion del editor)$", f):
            return EDITORIAL, True
        return (parent.role if parent is not None else enclosing.role), False

    for g in merged:
        role, _ = role_of_title(g.title)
        if g.part_like:
            if part is None and g.idx > 0:
                secs.append(new_section(None, 1, "Front matter", FRONT, start=0, implicit=True))
            part = new_section(g, 1, g.title, role)
            part.role_fixed = True
            secs.append(part)
            last2 = None
            continue
        if g.sub:
            # a bibliography or notes heading alone: a subsection of the section it is in,
            # else a part of its own; on its own part's first unit it is that part's heading
            if part is not None and part.start == g.idx and part.role == role:
                part.evidence += [x for x in g.evidence if x not in part.evidence]
                part.sources = sorted(set(part.sources) | g.sources)
                continue
            if part is not None and last2 is not None and part.role != role:
                sub = new_section(g, 3, g.title, role)
                sub.role_fixed = True
                secs.append(sub)
                continue
            if part is not None and part.role == role:
                sec = new_section(g, 2, g.title, role)
                sec.role_fixed = True
                secs.append(sec)
                last2 = sec
                continue
            if part is None and g.idx > 0:
                part = new_section(None, 1, entry_title, s.default_role, start=0, implicit=True)
                secs.append(part)
                sub = new_section(g, 2, g.title, role)
                sub.role_fixed = True
                secs.append(sub)
                last2 = sub
                continue
            part = new_section(g, 1, g.title, role)
            part.role_fixed = True
            secs.append(part)
            last2 = None
            continue
        front_item = role in (FRONT, EDITORIAL)
        if part is None:
            if front_item or g.idx > 0:
                part = new_section(None, 1, "Front matter", FRONT, start=0, implicit=True)
                secs.append(part)
        if part is None or (part.implicit and part.role == FRONT and not front_item) or (
                part.role in PART_ROLES and (role or s.default_role) != part.role
                and not (role is None and part.role == BIBLIOGRAPHY)):
            part = new_section(None, 1, entry_title, s.default_role, start=g.idx, implicit=True)
            secs.append(part)
            last2 = None
        level = 2
        if g.seq_level:
            level = g.seq_level
            if g.seq_level == 3 and last2 is None:
                level = 2
        elif g.rel_level is not None:
            level = g.rel_level
        if level == 3 and (last2 is None or last2.level != 2):
            level = 2
        sec = new_section(g, level, g.title, None)
        sec.role, sec.role_fixed = role_for(g, level, last2 if level == 3 else None, part)
        secs.append(sec)
        if level == 2:
            last2 = sec
    if not secs:
        secs.append(new_section(None, 1, entry_title, s.default_role, start=0, implicit=True))
    if secs[0].start > 0:
        secs.insert(0, new_section(None, 1, "Front matter", FRONT, start=0, implicit=True))
    return secs


def _front_role(g: _Group, s: Structure) -> bool:
    """A section that belongs to the front matter: editorial by its words, or a page
    flagged front_matter."""
    role, _ = role_of_title(g.title)
    return role in (FRONT, EDITORIAL)


def _finish(secs: list[Section], units: list, e: Entry, s: Structure) -> None:
    """Ends, nesting, collapse of single-child implicit parts, ids, page ranges, roles
    inherited, language, characters, invariants."""
    n = len(units)
    # ends: a section ends where the next section of the same or a higher level starts
    for i, x in enumerate(secs):
        end = n
        for y in secs[i + 1:]:
            if y.level <= x.level:
                end = y.start
                break
        x.end = end
    # drop empty ones (two parts opening at the same unit: keep the last)
    kept = []
    for x in secs:
        if x.end > x.start:
            kept.append(x)
        else:
            s.notes.append(f"section «{x.title[:40]}» at unit {x.start + 1} is empty; dropped")
    secs[:] = kept
    # collapse: an implicit part whose only direct child starts with it
    changed = True
    while changed:
        changed = False
        for i, x in enumerate(secs):
            if x.level != 1 or not x.implicit:
                continue
            children = [y for y in secs if y.level == 2 and x.start <= y.start < x.end]
            if len(children) == 1 and children[0].start == x.start and children[0].end == x.end:
                child = children[0]
                child.level, child.implicit = 1, False
                if not child.role_fixed:
                    child.role = x.role if x.role != FRONT else child.role
                for y in secs:
                    if y.level == 3 and child.start <= y.start < child.end:
                        y.level = 2
                secs.remove(x)
                changed = True
                break
    # parents and ids
    stack: list[Section] = []
    for k, x in enumerate(secs, 1):
        x.id = f"s{k:03d}"
        while stack and (stack[-1].level >= x.level or stack[-1].end <= x.start):
            stack.pop()
        x.parent = stack[-1].id if stack else None
        if stack and not x.role_fixed and x.level > 1:
            x.role = stack[-1].role if x.role == s.default_role else x.role
        stack.append(x)
    # the entry's author is never a section author
    entry_author = fold(s.metadata.get("author") or "")
    for x in secs:
        if x.author and entry_author and ratio(x.author, entry_author) >= 0.9:
            x.author = None
        x.first, x.last = x.start + 1, x.end
        span = units[x.start:x.end]
        pages = [p for u in span for p in (u.get("pages") or [])]
        x.pdf_pages = [min(pages), max(pages)] if pages else None
        labels = [lab for u in span for lab in (u.get("labels") or []) if lab]
        x.printed = [labels[0], labels[-1]] if labels else None
        if e.kind == "txt":
            lines = [p for u in span for p in (u.get("lines") or [])]
            x.lines = [min(lines), max(lines)] if lines else None
        langs = Counter(u.get("lang") for u in span if u.get("lang"))
        x.lang = langs.most_common(1)[0][0] if langs else e.lang
        x.chars = sum(len(u["text"]) for u in span)
        if not x.sources:
            x.sources = ["implicit"]
            x.evidence = x.evidence or ["implicit part: the units before the first section"
                                        if x.role == FRONT else "implicit part"]
            x.confidence = HIGH if x.start == 0 else x.confidence
    _check_tiling(secs, n)


def _check_tiling(secs: list[Section], n: int) -> None:
    starts = [x.start for x in secs]
    if starts != sorted(starts):
        raise StructureError("sections out of order")
    if not secs or secs[0].start != 0:
        raise StructureError("the first section does not start at the first unit")
    leaf = [None] * n
    for x in secs:
        if x.end <= x.start:
            raise StructureError(f"empty section {x.title!r}")
        for i in range(x.start, x.end):
            if leaf[i] is None or leaf[i].level < x.level:
                leaf[i] = x
    if any(v is None for v in leaf):
        raise StructureError("a unit belongs to no section")


def _apparatus_notes(e: Entry, s: Structure) -> None:
    idx_pages = sorted(r["page"] for r in e.rows if r.get("kind") == "index" and "page" in r)
    if idx_pages and not any(x.role == INDEX for x in s.sections):
        s.notes.append(f"index pages {idx_pages[0]}-{idx_pages[-1]} carry no units "
                       "(apparatus); no index section")
    con = sorted(r["page"] for r in e.rows if r.get("kind") == "contents" and "page" in r)
    if con:
        s.notes.append(f"contents pages {con[0]}-{con[-1]} are apparatus (not in the text)")


def _txt_structure(s: Structure, title: str) -> Structure:
    e = s.entry
    x = Section(id="s001", level=1, title=title or e.stem, role=s.default_role, start=0,
                end=len(e.units), implicit=True, sources=["implicit"], confidence=HIGH,
                evidence=["a single text: one section"])
    x.first, x.last = 1, len(e.units)
    if e.units:
        if e.kind == "txt":
            lines = [p for u in e.units for p in (u.get("lines") or [])]
            x.lines = [min(lines), max(lines)] if lines else None
        else:
            pages = [p for u in e.units for p in (u.get("pages") or [])]
            x.pdf_pages = [min(pages), max(pages)] if pages else None
        x.chars = sum(len(u["text"]) for u in e.units)
        x.lang = e.lang
    s.sections = [x]
    s.by_id = {x.id: x}
    s.checks = _checks(s)
    return s


def _checks(s: Structure) -> dict:
    by_role = Counter()
    by_level = Counter()
    conf = Counter()
    leaf_chars = Counter()
    for x in s.sections:
        by_level[x.level] += 1
        by_role[x.role] += 1
        conf[x.confidence] += 1
    # characters by role at the leaf level
    units = s.entry.units
    leaf = [None] * len(units)
    for x in s.sections:
        for i in range(x.start, x.end):
            if leaf[i] is None or leaf[i].level < x.level:
                leaf[i] = x
    for i, x in enumerate(leaf):
        if x is not None:
            leaf_chars[x.role] += len(units[i]["text"])
    return {"sections_by_level": dict(sorted(by_level.items())),
            "sections_by_role": dict(by_role), "chars_by_role": dict(leaf_chars),
            "confidence": dict(conf), "units": len(units),
            "low": [{"title": x.title, "pages": x.pdf_pages or x.lines, "why": x.evidence[-1]}
                    for x in s.sections if x.confidence == LOW]}


# ---------------------------------------------------------------------------- outputs
def write_sections(s: Structure, outdir) -> Path:
    path = Path(outdir) / f"{s.entry.stem}{SUFFIX}"
    catalogue.write_text(path, json.dumps(s.to_json(), indent=1, ensure_ascii=False) + "\n")
    return path


@dataclass
class RunResult:
    structures: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    written: list = field(default_factory=list)
    seconds: float = 0.0
    log: Path | None = None


def plan_dirs(text_dir, outdir, export: dict) -> None:
    text_dir, outdir = Path(text_dir), Path(outdir)
    if catalogue.under(outdir, text_dir):
        raise StructureError(f"the output folder {outdir} is inside the text folder "
                             f"{text_dir}; put it elsewhere")
    for rec in export["entries"].values():
        src = Path(str(rec.get("input", ""))).parent
        if str(src) and catalogue.same_path(outdir, src):
            raise StructureError(f"the output folder {outdir} holds a working file "
                                 f"({rec.get('input')}); put it elsewhere")


def run(text_dir, outdir, only=(), decisions=None) -> RunResult:
    t0 = time.perf_counter()
    export = load_export(text_dir)
    plan_dirs(text_dir, outdir, export)
    res = RunResult()
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    items = sorted(export["entries"].items(), key=lambda kv: kv[1].get("stem", "").lower())
    for id_, rec in items:
        if only and not any(id_.startswith(o) or o.lower() in rec.get("stem", "").lower()
                            or o.lower() in rec.get("rel", "").lower() for o in only):
            continue
        try:
            e = load_entry(text_dir, id_, rec)
            s = detect(e, decisions)
            res.written.append(write_sections(s, outdir))
            res.structures.append(s)
        except (StructureError, OSError, ValueError, KeyError) as err:
            res.errors.append((rec.get("stem", id_), f"{type(err).__name__}: {err}"))
    res.seconds = time.perf_counter() - t0
    res.log = write_log(outdir, res)
    return res


def write_log(outdir, res: RunResult) -> Path:
    path = Path(outdir) / RUN_LOG
    totals = Counter()
    entries = {}
    for s in res.structures:
        for role, n in s.checks["chars_by_role"].items():
            totals[role] += n
        entries[s.entry.id] = {"stem": s.entry.stem, "input_sha256": s.entry.input_sha,
                               "book_kind": s.kind, "sections": len(s.sections),
                               "units": len(s.entry.units), "checks": s.checks,
                               "notes": s.notes, "file": f"{s.entry.stem}{SUFFIX}"}
    data = {"schema": SCHEMA, "tool_version": __version__, "made": catalogue.now(),
            "seconds": round(res.seconds, 2), "entries": entries,
            "errors": [{"stem": a, "error": b} for a, b in res.errors],
            "totals": {"chars_by_role": dict(totals), "entries": len(res.structures)}}
    catalogue.write_text(path, json.dumps(data, indent=1, ensure_ascii=False) + "\n")
    return path


# ---------------------------------------------------------------------------- report
def _pct(n, total) -> str:
    return f"{100 * n / total:.0f}%" if total else "0%"


def report(res: RunResult) -> str:
    out = []
    for s in res.structures:
        e = s.entry
        c = s.checks
        total = sum(c["chars_by_role"].values()) or 1
        out.append(f"{e.stem} ({s.kind}, default {s.default_role}): {len(e.units)} units, "
                   f"{len(s.sections)} sections; levels " + ", ".join(
                       f"{k}: {v}" for k, v in c["sections_by_level"].items()))
        out.append("  roles: " + ", ".join(
            f"{r} {c['sections_by_role'].get(r, 0)} ({_pct(c['chars_by_role'].get(r, 0), total)})"
            for r in st.ROLES if c["sections_by_role"].get(r) or c["chars_by_role"].get(r)))
        out.append("  confidence: " + ", ".join(f"{k} {v}" for k, v in sorted(
            c["confidence"].items(), key=lambda kv: ("high", "medium", "low").index(kv[0]))))
        for low in c["low"]:
            out.append(f"    low: «{low['title'][:60]}» pp. {low['pages']}: {low['why']}")
        o = s.outline
        if o.junk:
            out.append("  outline: junk (one bookmark per page), ignored")
        elif e.toc:
            out.append(f"  outline: {len(o.confirmed)} confirmed, {len(o.reanchored)} "
                       f"re-anchored, {len(o.unconfirmed)} kept unverified, {len(o.dropped)} "
                       "dropped")
            for cl, old in o.reanchored:
                out.append(f"    re-anchored «{cl.title[:50]}» p{old} -> p{cl.page}: "
                           + "; ".join(cl.evidence)[:120])
            for cl, why in o.dropped:
                out.append(f"    dropped «{cl.title[:50]}» p{cl.page}: {why}")
        else:
            out.append("  outline: none")
        ct = s.contents
        out.append(f"  contents: pages {ct.get('pages') or 'none'}; {ct.get('parsed', 0)} entries "
                   f"parsed, {ct.get('matched', 0)} matched a heading or the title on the page")
        for sk in ct.get("skipped", []):
            out.append(f"    skipped {sk}")
        for oo in ct.get("out_of_order", []):
            out.append(f"    out of order: {oo}")
        for note in s.notes:
            out.append(f"  note: {note}")
        out.append("  invariants: every unit in exactly one section; sections in order; none "
                   "empty: ok")
    for stem, err in res.errors:
        out.append(f"error {stem}: {err}")
    totals = Counter()
    for s in res.structures:
        for role, n in s.checks["chars_by_role"].items():
            totals[role] += n
    grand = sum(totals.values()) or 1
    out.append("corpus: characters by role: " + ", ".join(
        f"{r} {totals.get(r, 0)} ({_pct(totals.get(r, 0), grand)})" for r in st.ROLES
        if totals.get(r)))
    out.append(f"wrote {len(res.written)} sections file(s), {len(res.errors)} error(s) "
               f"({res.seconds:.1f} s); run log: {res.log}")
    return "\n".join(out)
