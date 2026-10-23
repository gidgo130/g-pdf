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
collapses into it (the play, a single essay).
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
                                     ratio)

SCHEMA = 1
RUN_LOG = "structure.json"
SUFFIX = ".sections.json"
FRONT, EDITORIAL, PRIMARY, CRITICISM, NOTES, BIBLIOGRAPHY, INDEX, BACK, OTHER = st.ROLES
APPARATUS_EDGE = 0.2            # share of the file: contents pages live in the first or last
TEXTS_SHARE = 0.5               # share of authored contents entries: an anthology of texts
POOR = 3                        # claims: with no more, plain headings are section claims
PART_ROLES = {BIBLIOGRAPHY, NOTES, INDEX, BACK, OTHER}
PLATE_PAGES = 3                 # consecutive pages without a head, mostly poor OCR: plates

# Series-wide names of the critical anthologies' front matter (all six volumes): headers
# on a contents page, editorial sections in the front part.
SERIES_NAMES = ("cinco prefacios", "introduccion vanguardista tripartita",
                "senas de identidad de este tomo", "retrato convencional de este libro",
                "retrato no convencional")
PART_NAMES = ("bibliografia", "antologia critica", "indice onomastico", "manifiestos",
              "revistas", "antologias: prologos y posfacio", "textos criticos", "notas",
              "notes", "index")
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
_EDITION = re.compile(r"edici[oó]n|edition|impresi[oó]n|ediciones|editorial|nascimento", re.I)
_TITLE_WORDS = re.compile(r"manifiest|vanguard|poes|literatur|novela|ensayo|teor|critic|"
                          r"historia|leyenda|antolog|obras|textos|revista|cuentos|edicion|"
                          r"segunda|primera|identidad|cosmopolit|modernis|contexto|libro|"
                          r"pertenec|gobierno|ministerio|instituto|fundacion|hnos|ltda|"
                          r"reading|^(un|una|el|la|los|las) ")
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
_NUM_LINE = re.compile(r"(^|\s)(\d{1,4}|[ivxlc]{1,6}|[IVXLC]{1,6})\s*$")
_PLATES = re.compile(r"l[áa]minas|ilustraciones|im[áa]genes", re.I)
_GLUED = re.compile(r"(\.\s*\d{1,4})\s+(?=(?:[IVXLivxl]{1,5}|\d{1,2})\.\s+[A-ZÁÉÍÓÚÑ]"
                    r"|[A-ZÁÉÍÓÚÑ][a-záéíóúñ]{2,}\b)")               # ". 151 l. El", ". 165 Epílogo"


class StructureError(Exception):
    """A refusal or failure with a message meant for the user."""


# ---------------------------------------------------------------------------- roles
def role_of_title(title: str) -> tuple[str | None, bool]:
    """(role, part_like) a title's words declare, else (None, False)."""
    f = fold(title).strip(" .:¿?¡!")
    if re.match(r"^(indice onomastico|index)\b", f) or f in ("indice de nombres",
                                                               "indice alfabetico"):
        return INDEX, True
    if re.match(r"^(bibliograf|bibliography|works cited|obras citadas|referencias|"
                r"obras basicas de referencia)", f):
        return BIBLIOGRAPHY, True
    if re.match(r"^(notas|notes|notas extensas|endnotes|notes to pages|notas al pie)$", f) or \
            f.startswith("notas extensas"):
        return NOTES, True
    if re.match(r"^(titulos publicados|otros titulos|colofon|este volumen|esta edicion se termino|"
                r"se termino de imprimir|lista de titulos|serie )", f) or "se termino de imprimir" in f:
        return BACK, True
    if re.match(r"^(laminas|ilustraciones|plates|imagenes|cuadernillo)", f):
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
    rel: int | None = None
    run_last: int | None = None
    page: int = 0

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


# ---------------------------------------------------------------------------- metadata
def _entry_title(e: Entry, on: dict, author: str | None) -> str | None:
    t = (e.metadata or {}).get("title") or ""
    if t and not _JUNK_TITLE.search(t) and len(t.split()) >= 2:
        if " - " in t and len(t.split(" - ")) == 2:        # "Asturias, Miguel Angel - Leyendas"
            t = t.split(" - ")[1]
        return t.strip()
    for pg in range(1, 5):
        heads = [u for u in on.get(pg, []) if u["kind"] == "heading"
                 and len(u["text"]) <= 80 and not _PUBLISHER.search(u["text"])
                 and not (author and ratio(u["text"], author) >= 0.8)
                 and u["text"].strip()[:1] not in "(“\"«"]
        clean = [u["text"].strip() for u in heads
                 if not re.search(r"\.\w|\d", u["text"]) and not _EDITION.search(u["text"])
                 and not any(len(tok) == 1 and tok not in "yaeoIA" for tok in u["text"].split())]
        for h in clean:
            if len(h.split()) >= 2:
                return h
        joined = []
        for h in clean:                          # "Latin American" + "Vanguards"
            if joined and not joined[-1].rstrip().endswith((".", ",", ":")) and \
                    len(joined[-1].split()) <= 3 and h[:1].isupper():
                joined[-1] = joined[-1] + " " + h
            else:
                joined.append(h)
        for h in joined:
            if len(h.split()) >= 2:
                return h
    for u in on.get(1, [])[:4]:                  # an essay's title set in capitals
        t = u["text"].strip()
        toks = t.split()
        if (t.isupper() and len(toks) >= 4 and "\n" not in t and len(t) <= 120
                and sum(len(x) == 1 for x in toks) < len(toks) / 2 and not re.search(r"\d", t)
                and not re.search(r"\b[MDCLXVI]{4,}\b", t)):
            return t
    return None


def _entry_author(e: Entry, on: dict) -> str | None:
    a = (e.metadata or {}).get("author") or ""
    if a and not _USERNAME.match(a.strip()) and len(a.split()) >= 2:
        return a.strip()
    t = (e.metadata or {}).get("title") or ""
    if " - " in t and len(t.split(" - ")) == 2 and "," in t.split(" - ")[0]:
        sur, given = t.split(" - ")[0].split(",", 1)
        return f"{given.strip()} {sur.strip()}"
    found = []
    for pg in range(1, 6):
        for u in on.get(pg, []):
            raw = u["text"].strip()
            s = raw.rstrip(",")
            if (name_like(s) and not _PUBLISHER.search(s) and "\n" not in s
                    and not _TITLE_WORDS.search(fold(s)) and len(s.split()) <= 4
                    and not any(len(tok) == 1 for tok in s.split())):
                if u["kind"] != "heading" or s.isupper() or raw.endswith(","):
                    found.append(s)
    return found[0] if found else None


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
    author = _entry_author(e, on)
    return {"title": _entry_title(e, on, author), "author": author, "year": year,
            "publisher": publisher, "confirmed": False,
            "evidence": "prefilled from the PDF metadata and the title and copyright pages; "
                        "not confirmed"}


# ---------------------------------------------------------------------------- contents
def contents_like_pages(on: dict, page_count: int) -> set[int]:
    """Pages near the file's ends with several lines of numbers alone (a contents page
    kept in the text, in one or two columns): never a source of heading claims."""
    out = set()
    for pg, units in on.items():
        if not (pg <= max(12, 0.1 * page_count) or pg >= page_count - max(12, 0.1 * page_count)):
            continue
        lines = [ln for u in units for ln in u["text"].split("\n") if ln.strip()]
        numbers = sum(bool(re.fullmatch(r"[\divxlcIVXLC ]+", ln.strip())) for ln in lines)
        if numbers >= 3 or (len(lines) >= 5 and sum(
                bool(_NUM_LINE.search(ln.strip())) for ln in lines) >= 0.4 * len(lines)):
            out.add(pg)
    return out


def inbody_contents_pages(on: dict, page_count: int) -> set[int]:
    """Pages near the file's ends whose units read like contents lines ("Author. 121", or
    "TITLE" / "Author" pairs): a contents page the export kept in the text (México,
    Manifiestos p. 450). A page with lines of several numbers (two columns) is left out."""
    out = set()
    for pg, units in on.items():
        edge = max(12, 0.1 * page_count)
        if not (pg <= edge or pg >= page_count - edge) or len(units) < 5:
            continue
        lines = [ln for u in units for ln in u["text"].split("\n") if ln.strip()]
        if any(re.fullmatch(r"[\divxlcIVXLC ]+", ln.strip()) and len(ln.split()) >= 2
               for ln in lines):
            continue
        numbered = [ln for ln in lines if _NUM_LINE.search(ln.strip()) and len(ln) <= 100]
        titled = [ln for ln in numbered if len(ln.split()) >= 3]   # "Author. 121", not "3 4"
        if (len(numbered) >= 3 and len(numbered) >= 0.2 * len(lines) and len(titled) >= 2
                and not st.is_name_index(lines)):
            out.add(pg)
            continue
        pairs = sum(1 for a, b in zip(lines, lines[1:])
                    if a.strip().isupper() and len(a) <= 80 and name_like(b.strip()))
        if pairs >= 4 and pairs >= 0.25 * len(lines) and not numbered:
            out.add(pg)                           # "TITLE" / "Author" pairs, numbers lost
    return out


def _between_apparatus(e: Entry, on: dict) -> set[int]:
    """Pages with units lying between two contents or index pages near the file's ends:
    a contents page the export classed as body (Manifiestos p. 450, its number column
    lost)."""
    total = e.page_count or len(e.rows)
    app = {a["page"] for a in e.apparatus if a.get("kind") in ("contents", "index")
           and not (APPARATUS_EDGE < a["page"] / max(1, total) < 1 - APPARATUS_EDGE)}
    return {pg for pg in on if pg not in app
            and any(q in app for q in (pg - 1, pg - 2)) and any(q in app for q in (pg + 1, pg + 2))}


def _page_lines(on: dict, pg: int) -> list[str]:
    """The lines of a page's units; a line gluing several contents entries ("IV. ... .
    151 l. El debate ... . 151 2. Nota final") is split before each ordinal."""
    out = []
    for u in on[pg]:
        for ln in u["text"].split("\n"):
            if not ln.strip():
                continue
            at = 0
            for m in _GLUED.finditer(ln):
                out.append(ln[at:m.end(1)].strip())
                at = m.end(1)
            if ln[at:].strip():
                out.append(ln[at:].strip())
    return out


def _continuation_pages(e: Entry, on: dict) -> set[int]:
    """Pages right after a contents or index page near the file's ends whose lines are
    contents-shaped (mostly ending in a number): the contents continue there although
    the export classed the page as body (Bürger's Sumario, p. 193)."""
    total = e.page_count or len(e.rows)
    app = {a["page"] for a in e.apparatus if a.get("kind") in ("contents", "index")
           and not (APPARATUS_EDGE < a["page"] / max(1, total) < 1 - APPARATUS_EDGE)}
    out = set()
    for pg in sorted(app):
        for q in (pg + 1, pg + 2):
            if q in app or q not in on or q in out:
                continue
            lines = _page_lines(on, q)
            numbered = sum(bool(_NUM_LINE.search(ln.strip())) for ln in lines)
            if len(lines) >= 2 and numbered >= 2 and numbered >= 0.5 * len(lines):
                out.add(q)
    return out


def _contents_pages(e: Entry, on: dict, inbody: set, stats: dict) -> list[tuple[int, int, list]]:
    """(page, columns, lines) of every contents page: apparatus pages of kind contents or
    index that are not name indexes nor in-text indexes, and in-body contents pages."""
    pages = []
    total = e.page_count or len(e.rows)
    for a in e.apparatus:
        if a.get("kind") not in ("contents", "index"):
            continue
        lines = a.get("lines") or []
        if st.is_name_index(lines):
            stats["skipped"].append(f"p{a['page']}: name index")
            continue
        frac = a["page"] / max(1, total)
        if APPARATUS_EDGE < frac < 1 - APPARATUS_EDGE:
            stats["skipped"].append(f"p{a['page']}: an in-text index, not the book's contents")
            continue
        pages.append((a["page"], a.get("columns", 1), lines))
    for pg in sorted(inbody):
        lines = _page_lines(on, pg)
        tail = 0
        while tail < len(lines) and re.fullmatch(r"[\divxlcIVXLC ]+", lines[-1 - tail].strip()):
            tail += 1
        numbered = sum(bool(_NUM_LINE.search(ln.strip())) for ln in lines)
        pages.append((pg, 2 if tail >= 4 or not numbered else 1, lines))
        stats["inbody"].append(pg)
    return sorted(pages)


def _contents_claims(e: Entry, lm: st.LabelMap, on: dict, grouping: set, stats: dict,
                     inbody: set) -> list[Claim]:
    """Claims from the contents and index pages: entries resolved to PDF pages in order
    (labelled ones first; an item without a number, or whose number resolves to no page
    or breaks the sequence (an OCR error), is found by its title among the headings
    between its placed neighbours, in order; a numbered item whose page does not carry
    its title moves to the page between its neighbours that does); headers get the page
    of their first child but look for their own text just before it. Levels: a header's
    children one deeper than it."""
    claims = []
    grouping_names = {fold(g) for g in grouping}
    headers = set(SERIES_NAMES) | set(PART_NAMES) | grouping_names
    parsed: list[tuple[int, list, list]] = []
    for page_no, columns, lines in _contents_pages(e, on, inbody, stats):
        entries = st.parse_contents(lines, columns, headers)
        if not entries:
            stats["skipped"].append(f"p{page_no}: {len(lines)} lines, no entries "
                                    + ("(two columns: counts differ)" if columns == 2
                                       else "(nothing parsed)"))
            continue
        for en in entries:                        # "México" alone under a header: a grouping
            if (not en.header and not en.label and not en.comma and not en.author
                    and len(en.title.split()) <= 2):
                en.header = True
        plain = [en for en in entries if not en.header]
        stats["pages"].append(page_no)
        stats["parsed"] += len(plain)
        parsed.append((page_no, entries, plain))
    plain_all = [en for _, _, plain in parsed for en in plain]
    notes = _place_entries(plain_all, lm, on, e.units, stats)
    for page_no, entries, plain in parsed:
        if page_no in inbody:
            placed = [en for en in plain if en.page]
            if len(placed) < 2 or len(placed) < 0.5 * len(plain):
                stats["skipped"].append(f"p{page_no}: contents lines in the text, but "
                                        f"{len(placed)} of {len(plain)} entries resolve")
                stats["inbody"].remove(page_no)
                stats["pages"].remove(page_no)
                stats["parsed"] -= len(plain)
                for en in plain:
                    en.page = None
    for page_no, entries, plain in parsed:
        if page_no not in stats["pages"]:
            continue
        prev_page = max((x.page for q, _, pl in parsed if q <= page_no for x in pl if x.page),
                        default=0)
        # claims, with the header context for the levels
        ctx: list[tuple[int, int]] = []            # (relative level, absolute level)
        pending: list[ContentsEntry] = []
        for en in entries:
            if en.header:
                pending.append(en)
                continue
            if not en.page:
                continue
            before = [c for c in claims if c.source == "contents"]
            prev_p = before[-1].page if before else 0
            for h in pending:
                while ctx and ctx[-1][0] > h.level:
                    ctx.pop()
                hp = _header_page(h.title, prev_p, en.page, on)
                part = role_of_title(h.title)[1]
                grouping_h = fold(h.title) in grouping_names
                h_level = 1 if part else min(3, ctx[-1][1] + 1 if ctx else 2)
                ctx.append((h.level, h_level if (hp or part) and not grouping_h else h_level - 1))
                if grouping_h:
                    continue                     # a grouping: its children take its level
                claims.append(Claim(h.title, hp or en.page, "header", level=h_level, evidence=[
                    f"contents p{page_no} header «{h.title}» before «{en.title[:40]}» "
                    f"{en.label or ''}"], key=str(page_no)))
            pending = []
            while ctx and ctx[-1][0] > en.level:
                ctx.pop()
            level = (ctx[-1][1] + (en.level - ctx[-1][0])) if ctx else (1 + en.level)
            level = max(2, min(3, level))
            evidence = [f"contents p{page_no}: «{en.title[:60]}» {en.label or ''} -> p{en.page}"]
            if id(en) in notes:
                evidence.append(notes[id(en)])
            c = Claim(en.title, en.page, "contents", level=level, author=en.author,
                      evidence=evidence, key=str(page_no), label=en.label)
            c.rel = en.level
            c.by_phrase = en.by_phrase
            claims.append(c)
        for h in pending:
            claims.append(Claim(h.title, prev_page, "header", level=1 if role_of_title(h.title)[1]
                                else 2, evidence=[f"contents p{page_no} trailing header «{h.title}»"],
                                key=str(page_no)))
    return claims


def _author_line(title: str) -> str | None:
    """The names when an unnumbered index line is only a list of person names (the
    authors of the text above, in a TITLE / Author layout), else None."""
    t = re.sub(r"[^\w\s.'’-]", " ", title).strip(" .,;:-–")     # "[...]", "·", "¬"
    if t.isupper():
        return None                               # "ANATOMIA DE MI ULTRA": a title
    parts = [x.strip() for x in _NAME_SEP.split(t) if x.strip()]
    if parts and all(name_like(x) for x in parts):
        return ", ".join(parts)
    return None


def _glued_author(title: str) -> tuple[str, str | None]:
    """"PALABRAS PRELIMINARES A GALERAS DE PAFOS Otilio Vigil-Díaz": a title in capitals
    with its author glued on (the number column lost) is split."""
    toks = title.split()
    for k in range(len(toks) - 1, 1, -1):
        head, tail = " ".join(toks[:k]), " ".join(toks[k:])
        if head.isupper() and len(toks[:k]) >= 2 and not tail.isupper() and name_like(tail):
            return head, tail
        if toks[k - 1].isupper():
            break
    return title, None


def _window(plain: list, k: int) -> tuple[int, int]:
    """[lo, hi) for the title search of plain[k]: the pages of its placed neighbours."""
    before = [x for x in plain[:k] if x.page]
    after = [x for x in plain[k + 1:] if x.page]
    lo = before[-1].page if before else 1
    hi = after[0].page if after else lo + 60
    return lo, hi


def _place_entries(plain: list, lm: st.LabelMap, on: dict, units: list, stats: dict) -> dict:
    """Pages for the entries, in three passes (see _contents_claims); {id(entry): note}
    for the entries placed by their title."""
    notes, why = {}, {}
    prev_page = 0
    for en in plain:                              # pass 1: the numbers, in order
        if not en.label:
            continue
        page = lm.page(en.label, after=prev_page)
        if page is not None and page < prev_page - 2:
            stats["out_of_order"].append(
                f"«{en.title[:40]}» {en.label} -> p{page} after p{prev_page}")
            why[id(en)] = f"the number {en.label} (p{page}) breaks the sequence after p{prev_page}"
            page = None
        elif page is None:
            why[id(en)] = f"the number {en.label} resolves to no page"
        if page is None:
            continue
        en.page = page
        prev_page = max(prev_page, page)
    pos = {id(u): i for i, u in enumerate(units)}
    for k, en in enumerate(plain):                # pass 2: no usable number: the title,
        if en.page:                               # between the placed neighbours
            continue
        if not en.label and not en.author:
            names = _author_line(en.title)        # "[Alberto Rojas Jiménez - Martín
            if names:                             # Bunster]": the authors of the entry
                owner = next((x for x in reversed(plain[:k]) if x.page), None)   # before
                if owner is not None and not owner.author:
                    owner.author = names
                stats["unresolved"].append(f"«{en.title[:40]}» (an author line)")
                continue
            en.title, en.author = _glued_author(en.title)
        lo, hi = _window(plain, k)
        pg = _title_between(en.title, lo, hi, on)
        if pg is None:
            stats["unresolved"].append(f"«{en.title[:40]}» {en.label or '(no number)'}")
            continue
        en.page = pg
        notes[id(en)] = (why.get(id(en)) or "no page number in the index") + \
            f"; title found on p{pg} between p{lo} and p{hi}"
    for k, en in enumerate(plain):                # pass 3: a number whose page does not
        if not en.page or not en.label or id(en) in notes or len(st.words(en.title, 1)) < 1:
            continue                              # carry the title: an OCR error
        _, how = st.anchor(Claim(en.title, en.page, "contents"), units, on, pos)
        if not how.startswith(("first unit", "last unit")):
            continue
        lo, hi = _window(plain, k)
        pg = _title_between(en.title, lo, hi, on, strong=True)
        if pg is not None and pg != en.page:
            notes[id(en)] = (f"the number {en.label} gives p{en.page}, which does not carry "
                             f"the title; title found on p{pg} between p{lo} and p{hi}")
            en.page = pg
    return notes


def _title_between(title: str, lo: int, hi: int, on: dict, strong: bool = False) -> int | None:
    """The first page in [lo, hi) with a heading, or a short unit in capitals, that
    carries ``title`` (``strong``: the same title, not only its words)."""
    need = 1.0 if strong else 0.5
    for pg in sorted(q for q in on if lo <= q < hi):
        for u in on[pg]:
            text = u["text"]
            if u["kind"] != "heading" and not (len(text) <= 80 and "\n" not in text
                                               and text.strip().isupper()):
                if len(text) <= 80 and "\n" not in text and st._heading_score(title, text) >= 1:
                    return pg                          # a short unit that is the title
                continue
            if len(fold(title)) < 8:                 # "Eta" is not "Beta": short titles
                if st._starts_with(title, text) and st.same_numerals(title, text):
                    return pg                          # open the unit, no fuzzy ratio
            elif st._heading_score(title, text) >= need:
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


# ---------------------------------------------------------------------------- headings
def _seq_number(tok: str) -> int | None:
    t = fold(tok)
    if t.isdigit():
        return int(t)
    if t in _ORDINALS:
        return _ORDINALS[t]
    return st._roman(t)


def _heading_claims(units: list, skip_pages: set) -> list[Claim]:
    """Claims from heading units alone: role headings (a bibliography or notes inside an
    essay, an index, a series list, plates) and numbered sequences (acts, cuadros, cantos,
    notas extensas); scenes are not sections; front-matter and contents pages are skipped."""
    claims = []
    seen_seq = set()
    for u in units:
        if u["kind"] != "heading":
            continue
        pg = (u.get("pages") or [1])[0]
        if pg in skip_pages or "front_matter" in u.get("flags", []):
            continue
        t = u["text"].strip()
        if re.search(r"\s\d{1,4}$", t):
            continue                                   # a contents line kept in the text
        role, _ = role_of_title(t)
        if role in PART_ROLES and len(t.split()) <= 6:
            claims.append(Claim(t, pg, "heading", evidence=[f"heading «{t[:50]}» on p{pg}"]))
            continue
        m = _SEQ.match(t)
        if m and m.group("kind").lower() != "escena" and len(t) <= 60:
            kind = fold(m.group("kind"))
            n = _seq_number(m.group("n"))
            if (kind, n) in seen_seq:
                continue                               # "Acto V" again: a subsection title
            seen_seq.add((kind, n))
            claims.append(Claim(t, pg, "heading", evidence=[f"heading «{t[:50]}» on p{pg}"],
                                key=f"seq:{kind}:{n}"))
    return claims


def _plain_heading_claims(units: list, skip_pages: set, entry_title: str | None) -> list[Claim]:
    """In a file with hardly any other evidence (no contents, no outline, no heads): clean
    short headings are section claims (Ariel's PROLOGO)."""
    claims = []
    for u in units:
        if u["kind"] != "heading":
            continue
        pg = (u.get("pages") or [1])[0]
        t = u["text"].strip()
        flags = u.get("flags", [])
        if (pg in skip_pages or "front_matter" in flags or "suspect_ocr" in flags
                or "low_quality" in flags or len(t) > 60 or re.search(r"[\d.]", t)
                or _ROMAN_HEADING.match(t) or _SEQ.match(t) or t[:1] in "(“\"«"
                or t.endswith((",", ";")) or name_like(t)):
            continue
        toks = t.split()
        if t[:1].islower() or _EDITION.search(t):
            continue
        if not (len(toks) >= 2 or re.match(r"^(prologo|prefacio|introduccion|epilogo|"
                                           r"conclusion|apendice|notas|bibliografia|"
                                           r"advertencia|presentacion)$", fold(t))):
            continue
        if entry_title and (ratio(t, entry_title) >= 0.8 or (
                st.title_match(t, entry_title) and st.title_match(entry_title, t))):
            continue
        claims.append(Claim(t, pg, "heading", evidence=[f"heading «{t[:50]}» on p{pg}"]))
    return claims


def _roman_claims(units: list, on: dict, other_pages: list[int], page_count: int,
                  s: Structure) -> list[Claim]:
    """Standalone roman-numeral headings (Ariel's I-IV) as numbered sections: level 3 when
    another claim ends their run early (a prologue's numbered parts), else level 2."""
    claims = []
    for u in units:
        if u["kind"] == "heading" and _ROMAN_HEADING.match(u["text"].strip()):
            n = st._roman(u["text"].strip())
            if n:
                pg = (u.get("pages") or [1])[0]
                claims.append(Claim(u["text"].strip().upper(), pg, "heading",
                                    evidence=[f"roman numeral heading «{u['text'].strip()}» on p{pg}"],
                                    key=f"seq:roman:{n}"))
    if not claims:
        return claims
    first, last = claims[0].page, claims[-1].page
    ended_early = any(first < q <= last + 1 and q < 0.5 * page_count for q in other_pages)
    if ended_early:
        for c in claims:
            c.key = c.key.replace("seq:roman:", "seq:roman3:")
    numbers = sorted({int(c.key.split(":")[2]) for c in claims})
    s.notes.append(f"roman-numeral sections {numbers[0]}-{numbers[-1]} found; no standalone "
                   f"unit for {numbers[-1] + 1} and later")
    return claims


def _plates_claims(rows: list, on: dict) -> list[Claim]:
    """A run of >= PLATE_PAGES pages without a running head whose units are mostly flagged
    low_quality or suspect_ocr, between pages with heads: plates (role other)."""
    heads = st.heads_of(rows)
    kinds = {r["page"]: r.get("kind") for r in rows if "page" in r}
    pages = sorted(p for p in kinds if kinds[p] in ("body", "blank"))
    claims = []
    run: list[int] = []

    def poor(pg: int) -> bool:
        us = on.get(pg, [])
        if not us:
            return False
        bad = sum(bool({"low_quality", "suspect_ocr"} & set(u.get("flags", []))) for u in us)
        lengths = sorted(len(u["text"]) for u in us)
        captions = len(us) <= 15 and (lengths[len(lengths) // 2] <= 200 or len(us) <= 2)
        return bad >= 0.5 * len(us) or captions

    def close():
        nonlocal run
        with_units = [p for p in run if on.get(p)]
        if len(with_units) >= PLATE_PAGES and any(
                q in heads for q in range(run[0] - 3, run[0])) and any(
                q in heads for q in range(run[-1] + 1, run[-1] + 7)):
            first = with_units[0]
            text = " ".join(u["text"] for p in run[:2] for u in on.get(p, []))[:300]
            m = _PLATES.search(text)
            title = m.group(0).capitalize() if m else "Láminas"
            claims.append(Claim(title, first, "heading", evidence=[
                f"pages {run[0]}-{run[-1]} without running heads, units mostly flagged "
                "low_quality or suspect_ocr: plates"], key="plates"))
        run = []

    for pg in pages:
        if pg not in heads and (poor(pg) or (run and (kinds.get(pg) == "blank" or not on.get(pg)))):
            run.append(pg)
        else:
            if run:
                close()
    if run:
        close()
    return claims


# ---------------------------------------------------------------------------- groups
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
    rel: int | None = None                            # the contents' relative level
    sub: bool = False                                 # a role heading alone
    seq_level: int | None = None
    authored: bool = False
    by_phrase: bool = False                           # the author from "..., por X"
    page: int = 0
    run_last: int | None = None
    plates: bool = False


_SOURCE_RANK = {"contents": 0, "bookmark": 1, "head": 2, "heading": 3, "header": 4,
                "inferred": 5}
_NOT_PERSON = {"movimiento", "grupo", "revista", "vanguardia", "manifiesto", "arte",
               "poesia", "nueva", "nuevo", "generacion", "escuela", "asociacion", "sociedad",
               "comite", "redaccion", "editorial", "antologia", "union", "frente", "liga",
               "circulo", "taller", "teatro", "poetas", "pintores", "artistas", "escritores",
               "anonimo", "anonymous", "varios", "autores", "estridentista", "estridentistas",
               "ultraista", "ultraistas", "creacionista", "surrealista", "surrealistas"}
_NAME_SEP = re.compile(r"\s*(?:,|;|\by\b|\be\b|\bet al\.?|\band\b|/|\s\.\s|\s[-–]\s)\s*")


def clean_author(author: str | None, title: str, heads: set, titles: set) -> tuple[str | None, str | None]:
    """(author, why) with the author cleaned: a revista's bracketed editors stand for it,
    a leading running-head or section text is stripped, a phrase that is a head or a
    section title is no author, and what is left must read as a list of person names."""
    if not author:
        return None, None
    a = author.strip()
    m = re.search(r"\[([^\]]+)\]", a)
    if m and len(a[:m.start()].split()) <= 3 and not a[m.end():].strip(" ,."):
        a = m.group(1).strip()        # "Proa [Jorge Luis Borges]": a revista's editors;
    a = re.sub(r"[\[\]]", "", a).strip()   # "Ortelli y Gasset [J. L. Borges]": a pseudonym
    m = re.match(r"^(?P<caps>[A-ZÁÉÍÓÚÑÜ][A-ZÁÉÍÓÚÑÜ\s.,:;-]*?)\s+(?=[A-ZÁÉÍÓÚÑÜ][a-záéíóúñü])", a)
    if m and any(len(w.strip(".,:;-")) >= 4 for w in m.group("caps").split()):
        a = a[m.end():].strip()       # "IDENTIDADES La raza cósmica José V.": a header
    et_al = re.search(r"\s*,?\s*\b(?:y|e|and|et)\s+(?:otros|outros|others|al)\b.*$", a)
    if et_al and et_al.start() > 0:
        a = a[:et_al.start()].strip(" ,")                    # "... y otros noístas"
    fa = fold(a)
    for _ in range(3):                            # "IDENTIDADES La raza cósmica José V.":
        for h in sorted(heads | titles | {fold(title)}, key=len, reverse=True):
            if len(h) >= 4 and fa.startswith(h + " ") and len(fa) > len(h) + 3:
                a, fa = a[len(h):].strip(" ,.:;-"), fa[len(h) + 1:]   # a header, a head
                break                                                 # and the title
        else:
            break
    if any(len(h) >= 4 and (ratio(fa, h) >= 0.75 or fa == h) for h in heads | titles) or \
            ratio(fa, fold(title)) >= 0.75:
        return None, f"«{author[:50]}» is a running head or a section title, not an author"
    parts = [p for p in _NAME_SEP.split(a) if p and p.strip()]
    def person(p):
        toks = p.strip(" .").split()
        return 1 <= len(toks) <= 7 and all(
            st._INITIAL.match(tok.strip("'’\"“”()")) or tok in st._PARTICLES
            or (tok[:1].isupper() and tok.lower() not in st._NOT_NAMES
                and fold(tok) not in _NOT_PERSON) for tok in toks)
    if len(parts) >= 2 and not person(parts[0]) and all(person(p) for p in parts[1:]):
        a = ", ".join(p.strip(" .") for p in parts[1:])     # "revista de avance, Alejo
        parts = parts[1:]                                   # Carpentier, ...": its editors
    if not parts or not all(person(p) for p in parts) or len(a) > 160:
        return None, f"«{author[:50]}» does not read as a list of person names"
    if et_al and et_al.start() > 0:
        a += " et al."
    return a, None


def _merge(g: _Group, units: list, not_authors: set, heads: set = frozenset(),
           sec_titles: set = frozenset()) -> _Group:
    """One group's claims into a title, author, sources, evidence and confidence."""
    ordered = sorted(g.claims, key=lambda ch: (_SOURCE_RANK.get(ch[0].source, 9),
                                               -len(ch[0].title)))
    titles = [c for c, _ in ordered if c.source != "header"] or [c for c, _ in ordered]
    plain = [c for c in titles if not name_like(c.title)]   # "Guillermo Sheridan" read as a title
    g.title = (plain or titles)[0].title
    if g.title.isupper():                      # the same name in mixed case, if any source has it
        for c, _ in ordered:
            if fold(c.title) == fold(g.title) and not c.title.isupper():
                g.title = c.title
                break
    for c, _ in ordered:
        if c.source == "header" and fold(c.title) != fold(g.title) and not g.part_like:
            g.evidence.append(f"under the contents header «{c.title}»")
    authors = [c.author for c, _ in ordered if c.author and fold(c.author) not in not_authors
               and ratio(c.author, g.title) < 0.8]
    if authors:
        g.author = next((a for a in authors if not a.isupper()), authors[0])
    author_why = None
    if g.author:
        g.author, author_why = clean_author(g.author, g.title, heads, sec_titles)
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
            if g.rel_level is None:
                g.rel_level, g.rel = c.level, c.rel
            g.authored = g.authored or bool(c.author)
            g.by_phrase = g.by_phrase or c.by_phrase
        elif c.source == "bookmark":
            strength += 1 if c.confidence == HIGH else 0.5
        elif c.source == "head":
            strength += 1
            g.run_last = c.run_last
        elif c.source == "heading":
            strength += 0.5
            if c.key.startswith("seq:"):
                g.seq_level = SEQ_LEVEL.get(c.key.split(":")[1], 2)
            if c.key == "plates":
                g.plates = True
        elif c.source == "header":
            strength += 0.5
            if g.rel_level is None:
                g.rel_level = c.level
    g.sub = g.sources == {"heading"} and not any(c.key for c, _ in g.claims)
    if heading_hit:
        strength += 1
    hows = [how for _, how in g.claims]
    if strength < 1 and all(h.startswith(("first unit", "last unit")) for h in hows):
        g.confidence = LOW
        g.evidence.append("low: the title was not found on the page; the section starts at "
                          "the page's first unit")
    else:
        g.confidence = HIGH if strength >= 2 else MEDIUM
    if author_why:
        g.evidence.append("author dropped: " + author_why)
        if "person names" in author_why:
            g.confidence = LOW
    g.role_hint = role_of_title(g.title)[0]
    g.page = (units[g.idx].get("pages") or [0])[0]
    seen, ev = set(), []
    for x in g.evidence:
        if x not in seen:
            seen.add(x)
            ev.append(x)
    g.evidence = ev
    return g


_STOP = {"the", "and", "los", "las", "del", "por", "con", "une", "des", "una", "the", "for"}


def _rank(g: _Group) -> int:
    return min((_SOURCE_RANK.get(c.source, 9) for c, _ in g.claims if c.source != "header"),
               default=9)


def _titled(g: _Group) -> bool:
    """Some claim of the group is anchored at its title, not at a page's first unit."""
    return any(how.startswith(("heading", "title")) for _, how in g.claims)


def _abridges(head: str, title: str) -> bool:
    """The head's words all appear in the longer title: a running head abridging the
    chapter's title ("THE VAGABOND EYE/I" for "2. Outward Turns of the Vagabond Eye/I:
    The Vanguards' Portraits of the Artist")."""
    hw = [w for w in st.words(head, 3) if w not in _STOP]
    tw = st.words(title, 3)
    return bool(hw) and len(tw) > len(hw) and all(st.word_in(w, tw) for w in hw)


def _head_within(a: _Group, b: _Group) -> bool:
    head, full = (a, b) if a.sources == {"head"} else (b, a)
    return _abridges(head.title, full.title)


def _absorb(into: _Group, g: _Group) -> None:
    if _rank(g) < _rank(into) or (_rank(g) == _rank(into) and "contents" in g.sources
                                  and len(g.title) > len(into.title)):
        into.title = g.title                  # the better source names the section
    if _titled(g) and not _titled(into):
        into.idx, into.page = g.idx, g.page   # it starts where its title is
    into.claims += g.claims
    into.sources |= g.sources
    into.evidence += [x for x in g.evidence if x not in into.evidence]
    into.author = into.author or g.author
    into.authored = into.authored or g.authored
    into.by_phrase = into.by_phrase or g.by_phrase
    if into.rel_level is None:
        into.rel_level, into.rel = g.rel_level, g.rel
    into.seq_level = into.seq_level or g.seq_level
    into.run_last = into.run_last or g.run_last
    into.sub = into.sub and g.sub
    if g.confidence == HIGH or (into.confidence == LOW and g.confidence == MEDIUM):
        into.confidence = g.confidence


def _fold_duplicates(merged: list[_Group]) -> list[_Group]:
    """Two groups with one title on one page or two pages apart (the bookmark at the
    heading, the contents entry at the author line, the head run on the next page) become
    one at the earlier unit."""
    out: list[_Group] = []
    for g in merged:
        prev = out[-1] if out else None
        head_only = prev is not None and (prev.sources == {"head"} or g.sources == {"head"})
        claimed = prev is not None and (prev.sources & {"contents", "bookmark"} or
                                        g.sources & {"contents", "bookmark"})
        if (prev is not None and prev.part_like == g.part_like and (
                (prev.page == g.page and st.match_score(prev.title, g.title) > 0)
                or (abs(prev.page - g.page) <= 2 and st.match_score(prev.title, g.title) >= 1)
                or (head_only and abs(prev.page - g.page) <= (6 if claimed else 2)
                    and st.match_score(prev.title, g.title) > 0)
                or (head_only and claimed and abs(prev.page - g.page) <= 5
                    and _head_within(prev, g)))):
            _absorb(prev, g)
            continue
        out.append(g)
    return out


def _attach_authors(merged: list[_Group], author_only: list[Claim], heads: set = frozenset(),
                    titles: set = frozenset()) -> list[str]:
    """Author-only head runs (an anthology whose recto head is the contributor) name the
    section opening on that page or up to two pages earlier; a head that is the section's
    own title (OCR-damaged in the contents) names it instead."""
    notes = []
    for c in author_only:
        target = None
        own = next((g for g in merged if not g.part_like and abs(g.page - c.page) <= 2
                    and ratio(g.title, c.author) >= 0.75), None)
        if own is not None:
            if ("bookmark" not in own.sources and own.title.upper() != own.title
                    and not _abridges(c.author, own.title)):
                own.evidence.append(f"title from the running head «{c.author}» (the contents "
                                    f"read «{own.title[:40]}»)")
                own.title = c.author
            own.sources.add("head")
            continue
        cleaned, why = clean_author(c.author, "", heads, titles)
        if cleaned is None:
            notes.append(f"head «{c.author}» from p{c.page} is no author: {why}")
            continue
        c.author = cleaned
        for g in merged:
            if not g.part_like and g.page <= c.page <= g.page + 2 and not g.author:
                target = g
        if target is None:
            for g in merged:
                if not g.part_like and c.page <= g.page <= c.page + 2 and not g.author:
                    target = g
                    break
        if target is not None:
            target.author = c.author
            target.sources.add("head")
            target.evidence.extend(c.evidence)
        else:
            notes.append(f"author head «{c.author}» from p{c.page} matched no section")
    return notes


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
    page_count = e.page_count or len(e.rows)
    # 1. claims
    grouping = {t[1] for t, n in zip(e.toc, e.toc[1:]) if t[2] == n[2]
                and not st.part_like(t[1])}
    front_pages = {r["page"] for r in e.rows if r.get("kind") == "front" and "page" in r}
    has_contents = any(a.get("kind") == "contents" for a in e.apparatus)
    inbody = set() if has_contents else inbody_contents_pages(on, page_count)
    inbody |= _between_apparatus(e, on) | _continuation_pages(e, on)
    skip = front_pages | inbody | contents_like_pages(on, page_count)
    stats = {"pages": [], "parsed": 0, "matched": 0, "skipped": [], "out_of_order": [],
             "unresolved": [], "inbody": []}
    c_claims = _contents_claims(e, lm, on, grouping, stats, inbody)
    contents_entries = [ContentsEntry(c.title, c.author, c.label, c.level or 1, page=c.page)
                        for c in c_claims if c.page]
    body_units = [u for u in units if (u.get("pages") or [0])[0] not in inbody]
    s.outline = st.verify_outline(e.toc, e.rows, body_units, contents_entries)
    more = {c.title for c, why in s.outline.dropped if "grouping" in why}
    if more - grouping:                            # groupings the outline revealed: again
        stats = {"pages": [], "parsed": 0, "matched": 0, "skipped": [], "out_of_order": [],
                 "unresolved": [], "inbody": []}
        c_claims = _contents_claims(e, lm, on, grouping | more, stats, inbody)
    b_claims = list(s.outline.kept)
    h_all = st.head_boundaries(e.rows, units)
    author_only = [c for c in h_all if c.key == "author-only"]
    h_claims = [c for c in h_all if c.key != "author-only"]
    g_claims = _heading_claims(units, skip)
    strong = [c for c in c_claims + b_claims + h_claims if c.source != "header"]
    if len(strong) + len(g_claims) <= POOR:
        g_claims += _plain_heading_claims(units, skip, title)
        other_pages = [c.page for c in strong + g_claims]
        g_claims += _roman_claims(units, on, other_pages, page_count, s)
    g_claims += _plates_claims(e.rows, on)
    header_titles = {fold(c.title) for c in c_claims if c.source == "header"}
    not_authors = {fold(t[1]) for t in e.toc} | header_titles | set(PART_NAMES)
    # 2. anchor every claim to a unit
    groups: dict[tuple, _Group] = {}
    unplaced = []
    for c in c_claims + b_claims + h_claims + g_claims:
        if c.page < 1:
            continue
        role, part_like = role_of_title(c.title)
        if role == FRONT:
            continue                               # a contents page, never a section
        if not (on.get(c.page) or on.get(c.page + 1)):
            unplaced.append(c)
            continue
        idx, how = st.anchor(c, units, on, pos)
        if c.source == "contents" and how.startswith(("heading", "title")):
            stats["matched"] += 1
        if c.source == "heading" and role in (BIBLIOGRAPHY, NOTES):
            part_like = False                      # a subsection or a part: decided below
        if c.source == "head" and role == PRIMARY and not any(
                ratio(c.title, h) >= 0.8 for h in header_titles):
            part_like = False                      # "Manifiestos" as a bibliography block
        if c.source in ("contents", "header") and (c.level or 1) >= 3:
            part_like = False                      # a sub-entry is never a part
        if c.key == "plates":
            part_like = True
        key = (idx, part_like)
        g = groups.setdefault(key, _Group(idx, part_like))
        g.claims.append((c, how))
    s.contents = {"pages": stats["pages"], "parsed": stats["parsed"], "matched": stats["matched"],
                  "skipped": stats["skipped"], "out_of_order": stats["out_of_order"],
                  "unresolved": stats["unresolved"], "in_body_pages": stats["inbody"]}
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
    # 3. merge the claims of each unit; authors from author-only heads; duplicates
    head_keys = {fold(h) for parts in st.heads_of(e.rows).values() for h in parts
                 if not name_like(h)}                   # contributor heads are authors
    contents_titles = {fold(c.title) for c in c_claims if c.source in ("contents", "header")
                       and (len(c.title.split()) >= 2 or len(c.title) >= 6)}
    merged = [_merge(g, units, not_authors, head_keys, contents_titles) for g in groups.values()]
    dropped_groupings = {fold(c.title) for c, why in s.outline.dropped if "grouping" in why}
    kept_groups = []
    for g in merged:
        if g.sources <= {"header"} and fold(g.title) in dropped_groupings:
            s.notes.append(f"contents header «{g.title[:40]}» on p{g.page} dropped: a grouping "
                           "(its bookmark shares the page with the first text)")
            continue
        kept_groups.append(g)
    merged = kept_groups
    merged.sort(key=lambda g: (g.idx, not g.part_like))
    s.notes += _attach_authors(merged, author_only, head_keys, contents_titles)
    merged = _fold_duplicates(merged)
    merged = _tidy(merged, s)
    # 4. the book's kind and default role
    authored = [g for g in merged if g.authored]
    content_groups = [g for g in merged if "contents" in g.sources and not g.part_like]
    if any(g.part_like and fold(g.title).startswith("antologia critica") for g in merged):
        s.kind = "anthology"
    elif len(authored) >= 2 and content_groups and len(authored) >= TEXTS_SHARE * len(content_groups):
        s.kind = "texts"
    else:
        s.kind = "monograph"
    s.default_role = CRITICISM if s.kind in ("anthology", "texts") else default_role(
        title + " " + e.stem)
    # 5. sections
    _sequences(merged, units, on)
    # back matter: whatever follows the first index page in the last tenth of the file
    late_index = [r["page"] for r in e.rows if r.get("kind") == "index" and "page" in r
                  and r["page"] >= 0.9 * page_count]
    back_from = min(late_index) + 1 if late_index else None
    secs = _build(merged, units, s, title, back_from)
    _finish(secs, units, e, s)
    s.sections = secs
    s.by_id = {x.id: x for x in secs}
    _apparatus_notes(e, s)
    s.checks = _checks(s)
    return s


def _tidy(merged: list[_Group], s: Structure) -> list[_Group]:
    """Header-only groups that found no unit of their own join the head run or section
    they belong to (an OCR-damaged index header, a grouping); a part opening after a
    section on the same page starts with it; a weaker duplicate of a title is dropped."""
    out: list[_Group] = []
    for g in merged:
        if g.sources == {"header"} and not g.part_like and all(
                h.startswith("first unit") for _, h in g.claims):
            home = None
            for prev in reversed(out):
                if prev.part_like or g.page - prev.page > 6:
                    continue
                pf, gf = fold(prev.title), fold(g.title)
                if len(gf) >= 3 and (pf.startswith(gf[:4]) or ratio(pf, gf) >= 0.7):
                    home = prev
                    break
            same_page = [prev for prev in out if prev.page == g.page and not prev.part_like]
            if home is not None:
                home.evidence.append(f"contents header «{g.title}» (no unit of its own) "
                                     f"belongs to this section")
                home.sources.add("header")
                continue
            if same_page:
                s.notes.append(f"contents header «{g.title[:40]}» on p{g.page} dropped: a "
                               f"grouping with no unit of its own")
                continue
        out.append(g)
    merged = out
    # a part opening on the same page as a section that starts earlier on it, or within
    # two pages after a head-only section: the part begins with that section
    for g in merged:
        if not g.part_like:
            continue
        earlier = [x for x in merged if not x.part_like and x.idx < g.idx and (
            x.page == g.page or (x.sources == {"head"} and 0 < g.page - x.page <= 2))]
        if earlier:
            x = earlier[-1]
            if not any(y.part_like and x.idx <= y.idx < g.idx for y in merged):
                g.idx = x.idx
                g.evidence.append(f"the part begins with «{x.title[:40]}» on p{x.page}")
    merged.sort(key=lambda g: (g.idx, not g.part_like))
    # a weaker duplicate of a title: the bookmark confirmed somewhere else by chance
    out = []
    for g in merged:
        dup = next((x for x in out if x.part_like == g.part_like
                    and st.match_score(x.title, g.title) >= 1 and ratio(x.title, g.title) >= 0.9
                    and ("contents" in x.sources or len(x.sources) >= 2)
                    and g.sources <= {"bookmark", "head", "heading"} and "contents" not in g.sources
                    and len(g.sources) <= 2), None)
        if dup is not None:
            s.notes.append(f"«{g.title[:40]}» on p{g.page} ({', '.join(g.sources)}) dropped: "
                           f"the same title starts on p{dup.page} with "
                           f"{', '.join(sorted(dup.sources))}")
            continue
        out.append(g)
    return out


def _sequences(merged: list[_Group], units: list, on: dict) -> None:
    """A numbered sequence whose headings start at II (ACTO II, CUADRO SEGUNDO): the first
    member is inferred at the start of the body (acts) or at its parent's start
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
        if not numbers or numbers[0] != 2 or kind.startswith("roman"):
            continue
        if any(re.match(rf"^{kind}\s+(i|1|primer\w*)\b", fold(g.title)) for g in merged):
            continue
        first_idx = members[0][1].idx
        if kind == "cuadro":
            acts = [g for g in merged if g.seq_level == 2 and g.idx < first_idx]
            if not acts:
                continue                           # its act is inferred too: one is enough
            start = acts[-1].idx
        else:
            start = _body_start(units)
        if start >= first_idx:
            continue
        label = {"acto": "Acto", "cuadro": "Cuadro", "canto": "Canto", "capitulo": "Capítulo",
                 "parte": "Parte", "libro": "Libro"}.get(kind, kind.title())
        g = _Group(start, False)
        g.title = f"{label} I"
        g.sources = {"inferred"}
        g.confidence = LOW
        g.seq_level = SEQ_LEVEL.get(kind, 2)
        g.evidence = [f"inferred: the {kind} headings start at 2; no heading for the first "
                      "one was found, so it starts where the text starts"]
        g.page = (units[start].get("pages") or [0])[0]
        merged.append(g)
    merged.sort(key=lambda g: (g.idx, not g.part_like))


def _body_start(units: list) -> int:
    """The first unit after the title page, or the first "Escena" heading if earlier."""
    first_page = (units[0].get("pages") or [1])[0]
    after_title = next((i for i, u in enumerate(units)
                        if (u.get("pages") or [1])[0] > first_page), 0)
    escena = next((i for i, u in enumerate(units)
                   if u["kind"] == "heading" and fold(u["text"]).startswith("escena")), None)
    return min(after_title, escena) if escena is not None else after_title


# ---------------------------------------------------------------------------- building
def _build(merged: list[_Group], units: list, s: Structure, entry_title: str,
           back_from: int | None) -> list[Section]:
    """Groups in unit order into parts, sections and subsections."""
    secs: list[Section] = []
    roman_pages = {r["page"] for r in s.entry.rows if "page" in r and r.get("label")
                   and str(r["label"]).isalpha()}
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
            x.rel_level = g.rel_level
            x.rel = g.rel
            x.run_last = g.run_last
            x.page = g.page
        return x

    def implicit_part(start: int, title: str, role: str) -> Section:
        x = new_section(None, 1, title, role, start=start, implicit=True)
        secs.append(x)
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
            if g.authored:                    # "Author: “Title”": a text of the period;
                return (CRITICISM if g.by_phrase else PRIMARY), True   # "..., por X": a prologue
            if (texts_level is not None and level == texts_level
                    and (parent is None or not parent.role_fixed)
                    and enclosing.role in (PRIMARY, CRITICISM)):
                return PRIMARY, False
            return CRITICISM, False
        if s.kind == "monograph" and s.default_role == PRIMARY and re.match(
                r"^(prologo|estudio preliminar|introduccion del editor)$", f):
            s.notes.append(f"«{g.title}» on p{g.page} marked editorial: a prologue in a primary "
                           "text may be the author's own (owner decides)")
            return EDITORIAL, True
        return (parent.role if parent is not None else enclosing.role), False

    for g in merged:
        role, _ = role_of_title(g.title)
        if role == PRIMARY and not g.part_like:
            role = None                            # a bibliography block named "Revistas"
        in_back = back_from is not None and g.page >= back_from and not g.part_like
        if in_back:
            if part is None or part.role != BACK:
                part = implicit_part(g.idx, "Back matter", BACK)
                last2 = None
            sec = new_section(g, 2, g.title, BACK)
            sec.role_fixed = True
            secs.append(sec)
            last2 = sec
            continue
        if g.part_like and part is not None and part.role == role and (
                g.rel_level or 2) >= 2 and role in PART_ROLES:
            g.part_like = False                    # "Bibliografías" inside the bibliography
        if g.part_like:
            if part is None and g.idx > 0:
                implicit_part(0, "Front matter", FRONT)
            part = new_section(g, 1, g.title, role)
            part.role_fixed = True
            secs.append(part)
            last2 = None
            continue
        if g.sub:
            # a bibliography or notes heading alone: a subsection of the section it is in,
            # a section of a part with that role, else a part of its own
            if part is not None and part.start == g.idx and part.role == role:
                part.evidence += [x for x in g.evidence if x not in part.evidence]
                part.sources = sorted(set(part.sources) | g.sources)
                continue
            if part is not None and last2 is not None:
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
                part = implicit_part(0, entry_title, s.default_role)
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
        front_item = role in (FRONT, EDITORIAL) or (
            g.sources == {"head"} and g.page in roman_pages)      # a head in the preliminaries
        level_hint = g.rel_level if g.rel_level is not None else (g.seq_level or 2)
        if part is None and (front_item or g.idx > 0):
            part = implicit_part(0, "Front matter", FRONT)
        opens_body = (part is None
                      or (part.implicit and part.role == FRONT and not front_item
                          and level_hint <= 2)
                      or (part.role in PART_ROLES and level_hint <= 2
                          and (role or s.default_role) != part.role
                          and not (role is None and part.role == BIBLIOGRAPHY))
                      or (part.run_last is not None and "head" in g.sources
                          and g.page > part.run_last + 1 and level_hint <= 2))
        if opens_body:
            part = implicit_part(g.idx, entry_title, s.default_role)
            last2 = None
        level = level_hint
        if g.rel_level is None and g.seq_level:
            level = g.seq_level
        if level == 3 and (last2 is None or last2.level != 2 or (
                last2.rel is not None and last2.rel == g.rel and last2.rel_level == 3)):
            level = 2                        # no level-2 above: the sub-entries are sections
        sec = new_section(g, level, g.title, None)
        sec.role, sec.role_fixed = role_for(g, level, last2 if level == 3 else None, part)
        secs.append(sec)
        if level == 2:
            last2 = sec
    if back_from is not None and not any(x.role == BACK for x in secs):
        first_back = next((i for i, u in enumerate(units)
                           if (u.get("pages") or [0])[0] >= back_from), None)
        if first_back is not None and all(x.start < first_back for x in secs):
            x = implicit_part(first_back, "Back matter", BACK)        # a series list after
            x.evidence = [f"units after the index pages (from p{back_from})"]  # the index
    if not secs:
        secs.append(new_section(None, 1, entry_title, s.default_role, start=0, implicit=True))
    if secs[0].start > 0:
        secs.insert(0, new_section(None, 1, "Front matter", FRONT, start=0, implicit=True))
    return secs


def _finish(secs: list[Section], units: list, e: Entry, s: Structure) -> None:
    """Ends, nesting, collapse of single-child implicit parts, ids, page ranges, roles
    inherited, language, characters, invariants."""
    n = len(units)
    for i, x in enumerate(secs):
        end = n
        for y in secs[i + 1:]:
            if y.level <= x.level:
                end = y.start
                break
        x.end = end
    kept = []
    for x in secs:
        if x.end > x.start:
            kept.append(x)
        else:
            s.notes.append(f"section «{x.title[:40]}» at unit {x.start + 1} is empty; dropped")
    secs[:] = kept
    changed = True
    while changed:
        changed = False
        for x in secs:
            if x.level != 1 or not x.implicit or x.role == FRONT:
                continue
            children = [y for y in secs if y.level == 2 and x.start <= y.start < x.end]
            if len(children) == 1 and children[0].start == x.start and children[0].end == x.end:
                child = children[0]
                child.level, child.implicit = 1, False
                for y in secs:
                    if y.level == 3 and child.start <= y.start < child.end:
                        y.level = 2
                secs.remove(x)
                changed = True
                break
    if s.kind == "texts":
        _editor_intros(secs, units, s)
    stack: list[Section] = []
    for k, x in enumerate(secs, 1):
        x.id = f"s{k:03d}"
        while stack and (stack[-1].level >= x.level or stack[-1].end <= x.start):
            stack.pop()
        x.parent = stack[-1].id if stack else None
        if stack and not x.role_fixed and x.level > 1 and x.role == s.default_role:
            x.role = stack[-1].role
        stack.append(x)
    first = secs[0] if secs else None
    if first is not None and first.implicit and first.role != FRONT:
        kids = [y for y in secs if y.level == 2 and first.start <= y.start < first.end]
        if kids and all(y.role in (FRONT, EDITORIAL) for y in kids):
            first.role, first.title = FRONT, "Front matter"
    for i, x in enumerate(secs):              # a part is as sure as the section it opens with
        if x.level == 1:
            child = next((y for y in secs[i + 1:i + 2] if y.start == x.start and y.level == 2), None)
            if child is not None and child.confidence == HIGH and x.confidence != HIGH:
                x.confidence = child.confidence
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


def _editor_intros(secs: list[Section], units: list, s: Structure) -> None:
    """In an anthology of texts, the units of a container before its first child are the
    volume editor's introduction: an implicit child section (criticism, no author, the
    container's confidence), titled by the first heading inside it that is not the
    container's own title, else "Introducción". A container then holds no units."""
    added = []
    for x in secs:
        if x.level >= 3:
            continue
        kids = [y for y in secs if y.level == x.level + 1 and x.start <= y.start < x.end]
        if not kids or kids[0].start <= x.start:
            continue
        span = units[x.start:kids[0].start]
        title = next((u["text"].strip() for u in span if u["kind"] == "heading"
                      and ratio(u["text"], x.title) < 0.8 and len(u["text"]) <= 80), "Introducción")
        intro = Section(level=x.level + 1, title=title, role=CRITICISM, role_fixed=True,
                        start=x.start, end=kids[0].start, confidence=x.confidence,
                        sources=["implicit"], page=x.page,
                        evidence=[f"the editor's introduction: the units of «{x.title[:40]}» "
                                  f"before its first text «{kids[0].title[:40]}»"])
        added.append(intro)
    secs += added
    secs.sort(key=lambda y: (y.start, y.level))


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
        out.append(f"  contents: pages {ct.get('pages') or 'none'}"
                   + (f" (in the text: {ct['in_body_pages']})" if ct.get("in_body_pages") else "")
                   + f"; {ct.get('parsed', 0)} entries parsed, {ct.get('matched', 0)} matched a "
                   "heading or the title on the page")
        skipped = ct.get("skipped", [])
        names = [x for x in skipped if x.endswith("name index")]
        for sk in [x for x in skipped if not x.endswith("name index")]:
            out.append(f"    skipped {sk}")
        if names:
            out.append(f"    skipped {len(names)} name-index page(s)")
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
