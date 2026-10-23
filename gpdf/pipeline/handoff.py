# SPDX-License-Identifier: AGPL-3.0-or-later
"""gpdf-cli handoff: the files the owner's search app reads (format "gpdf-handoff/1",
described in docs/handoff-format.md). For every exported entry that is not excluded:

  OUTDIR/<sha256 of the working file>.gpdf.jsonl   a "file" line, the "section" lines,
                                                   then one "para" line per unit
  OUTDIR/handoff.json                              sha256 -> file name, title, counts,
                                                   time, structure confirmed yes/no

The sha256 is export.json's input_sha256: the exact file the owner uploads. Inputs are
only read: the structure folder (structure.json, <stem>.sections.json), the export folder
(export.json, <stem>.jsonl, .pages.jsonl, .apparatus.jsonl), the working files (hashed
again) and the catalogue's decisions (for exclusions). An entry whose export changed
since structure ran, or whose working file changed since the export, is skipped. Files
are written atomically; nothing in OUTDIR is ever deleted: .gpdf.jsonl files whose hash
is no longer current are listed for the owner to remove. No chunking: the app does that.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from gpdf import __version__
from gpdf.pipeline import catalogue

FORMAT = "gpdf-handoff/1"
SUFFIX = ".gpdf.jsonl"
INDEX = "handoff.json"
DEFAULT_OUT = Path("private/book-prep/handoff")
DEFAULT_CATALOGUE = Path("private/book-prep/catalogue")
_NAME = re.compile(r"^[0-9a-f]{64}" + re.escape(SUFFIX) + "$")


class HandoffError(Exception):
    """A refusal or failure with a message meant for the user."""


@dataclass
class Written:
    stem: str
    sha: str
    file_name: str
    path: Path
    title: str | None
    counts: dict
    metadata_confirmed: bool
    structure_confirmed: bool
    why_unconfirmed: str


@dataclass
class Result:
    out: Path
    written: list = field(default_factory=list)
    skipped: list = field(default_factory=list)       # (stem, why)
    excluded: list = field(default_factory=list)      # stems
    not_current: list = field(default_factory=list)   # file names in OUT
    catalogue: Path | None = None
    index: Path | None = None


def sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _jsonl(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _read_json(path: Path, what: str) -> dict:
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, ValueError) as e:
        raise HandoffError(f"cannot read {what} {path}: {e}") from None


# ---------------------------------------------------------------------------- lines
def _pair(v) -> list | None:
    return [v[0], v[-1]] if v else None


def lines_for(sections: dict, units: list[dict], apparatus_pages: list[int]) -> list[dict]:
    """The handoff lines of one entry, from its sections file and its units."""
    txt = sections["kind"] == "txt"
    md = sections.get("metadata") or {}
    dec = sections.get("decisions") or {}
    secs = sections["sections"]
    head = {"type": "file", "schema": FORMAT, "sha256": sections["input_sha256"],
            "file_name": Path(sections["input"]).name,
            "pages": None if txt else sections.get("pages"),
            "title": md.get("title"), "author": md.get("author"), "year": md.get("year"),
            "publisher": md.get("publisher"), "metadata_confirmed": bool(md.get("confirmed")),
            "lang": sections.get("lang"), "structure_confirmed": confirmed(dec)[0],
            "made_with": f"g-pdf {__version__}", "made": catalogue.now(),
            "apparatus_pages": apparatus_pages}
    out = [head]
    for x in secs:
        d = {"type": "section", "id": x["id"], "parent": x["parent"], "level": x["level"],
             "title": x["title"], "author": x["author"], "role": x["role"],
             "first": x["first"], "last": x["last"]}
        if txt:
            d["lines"] = x.get("lines")
        else:
            d["pdf_pages"], d["printed"] = x["pdf_pages"], x["printed"]
        d["lang"], d["confidence"] = x["lang"], x["confidence"]
        out.append(d)
    deepest = _deepest(secs, len(units))
    for u in units:
        d = {"type": "para", "seq": u["seq"], "section": deepest[u["seq"] - 1],
             "kind": u["kind"], "text": u["text"]}
        if txt:
            d["lines"] = _pair(u.get("lines"))
        else:
            d["pdf_pages"], d["printed"] = _pair(u.get("pages")), _pair(u.get("labels"))
            d["printed_inferred"] = bool(u.get("label_inferred"))
        d["lang"], d["flags"] = u.get("lang"), list(u.get("flags") or [])
        d["note"] = u.get("note") if u["kind"] == "note" else None
        out.append(d)
    return out


def _deepest(secs: list[dict], n: int) -> list[str | None]:
    """Per unit (index seq - 1): the id of the deepest section holding it."""
    leaf: list[tuple[int, str] | None] = [None] * n
    for x in secs:
        for i in range(x["first"] - 1, min(x["last"], n)):
            if leaf[i] is None or leaf[i][0] < x["level"]:
                leaf[i] = (x["level"], x["id"])
    return [v[1] if v else None for v in leaf]


def confirmed(dec: dict) -> tuple[bool, str]:
    """(structure confirmed, why not): the owner's decisions exist for the entry and none
    of them is stale or unmatched."""
    if not dec or not dec.get("record"):
        return False, "no decisions"
    bad = dec.get("stale", 0) + dec.get("unmatched", 0)
    if bad:
        return False, f"{bad} stale or unmatched decision{'s' if bad != 1 else ''}"
    return True, ""


def _ascending(v) -> bool:
    return v is None or (isinstance(v, list) and len(v) == 2 and v[0] is not None
                         and v[1] is not None and v[0] <= v[1])


def validate(rows: list[dict]) -> list[str]:
    """Problems with one file's lines (empty when it is valid): a file line first; sections
    in order, ids unique, parents resolving to a shallower enclosing section, tiling the
    paras from the first; every unit exactly once, in order, as a para of the deepest
    section holding it; page (or line) ranges ascending, never going back."""
    out = []
    if not rows or rows[0].get("type") != "file" or rows[0].get("schema") != FORMAT:
        return ["the first line is not a gpdf-handoff/1 file line"]
    secs = [r for r in rows if r.get("type") == "section"]
    paras = [r for r in rows if r.get("type") == "para"]
    kinds = [r.get("type") for r in rows[1:]]
    if kinds != ["section"] * len(secs) + ["para"] * len(paras):
        out.append("lines are not the file line, then sections, then paras")
    by_id = {}
    for s in secs:
        if s["id"] in by_id:
            out.append(f"section id {s['id']} used twice")
        by_id[s["id"]] = s
    n = max([p["seq"] for p in paras] + [s["last"] for s in secs], default=0)
    for k, s in enumerate(secs):
        if s["parent"] is not None:
            p = by_id.get(s["parent"])
            if p is None:
                out.append(f"section {s['id']}: parent {s['parent']} does not resolve")
            elif not (p["level"] < s["level"] and p["first"] <= s["first"]
                      and s["last"] <= p["last"]):
                out.append(f"section {s['id']} is not inside its parent {s['parent']}")
        if not (1 <= s["first"] <= s["last"]):
            out.append(f"section {s['id']}: units {s['first']}-{s['last']} not ascending")
        for key in ("pdf_pages", "lines"):
            if not _ascending(s.get(key)):
                out.append(f"section {s['id']}: {key} {s.get(key)} not ascending")
        if k and (secs[k - 1]["first"], secs[k - 1]["level"]) >= (s["first"], s["level"]):
            out.append(f"section {s['id']} is out of order")
    if secs and (secs[0]["first"] != 1 or secs[0]["level"] != 1):
        out.append("the first section does not start at the first unit")
    seen = {}
    for p in paras:
        seen[p["seq"]] = seen.get(p["seq"], 0) + 1
    for q in range(1, n + 1):
        if q not in seen:
            out.append(f"para seq {q} is missing")
        elif seen[q] > 1:
            out.append(f"para seq {q} appears more than once")
    if [p["seq"] for p in paras] != sorted(p["seq"] for p in paras):
        out.append("paras are not in reading order")
    deepest = _deepest(secs, n)
    last_page = None
    for p in paras:
        if p["section"] not in by_id:
            out.append(f"para {p['seq']}: section {p['section']} does not resolve")
        elif 1 <= p["seq"] <= n and deepest[p["seq"] - 1] != p["section"]:
            out.append(f"para {p['seq']}: section {p['section']} is not the deepest section "
                       f"holding it ({deepest[p['seq'] - 1]})")
        loc = p.get("pdf_pages") or p.get("lines")
        if not _ascending(loc) or not _ascending(p.get("pdf_pages")):
            out.append(f"para {p['seq']}: pages {loc} not ascending")
        elif loc:
            if last_page is not None and loc[0] < last_page:
                out.append(f"para {p['seq']}: pages go back ({loc[0]} after {last_page})")
            last_page = loc[0]
    if any(d is None for d in deepest):
        out.append("some units belong to no section")
    return out


# ---------------------------------------------------------------------------- run
def _apparatus_pages(text: Path, stem: str, units: list[dict]) -> list[int]:
    """PDF pages of contents and index pages that carry no unit of the text."""
    pages = set()
    app = text / f"{stem}.apparatus.jsonl"
    if app.is_file():
        pages |= {r["page"] for r in _jsonl(app) if r.get("page")}
    rows = text / f"{stem}.pages.jsonl"
    if rows.is_file():
        pages |= {r["page"] for r in _jsonl(rows)
                  if r.get("kind") in ("contents", "index") and r.get("page")}
    used = {p for u in units for p in range(u["pages"][0], u["pages"][-1] + 1)
            if u.get("pages")}
    return sorted(pages - used)


def _excluded(cat: Path | None) -> set[str]:
    if cat is None:
        return set()
    try:
        dec = catalogue.load_decisions(cat)
    except catalogue.CatalogueError as e:
        raise HandoffError(str(e)) from None
    return {k for k, d in dec["files"].items()
            if d.get("confirmed") and d.get("action") == "exclude"}


def plan(structure, to, text=None, cat=None) -> tuple[Path, dict, dict, Path | None]:
    """(text folder, structure.json, export.json, catalogue folder or None), checked."""
    structure, to = Path(structure), Path(to)
    log_path = structure / "structure.json"
    if not log_path.is_file():
        raise HandoffError(f"no structure.json in {structure}: run gpdf-cli structure first")
    log = _read_json(log_path, "the structure run log")
    text = Path(text) if text else Path(log.get("text") or "")
    if not (text / "export.json").is_file():
        raise HandoffError(f"no export.json in the text folder {text} (pass --text)")
    export = _read_json(text / "export.json", "the export log")
    for folder, what in ((structure, "structure"), (text, "text")):
        if catalogue.under(to, folder) or catalogue.under(folder, to):
            raise HandoffError(f"the handoff folder {to} overlaps the {what} folder {folder}; "
                               "put it elsewhere")
    for rec in export.get("entries", {}).values():
        src = Path(str(rec.get("input", ""))).parent
        if str(src) and catalogue.same_path(to, src):
            raise HandoffError(f"the handoff folder {to} holds a working file "
                               f"({rec.get('input')}); put it elsewhere")
    if cat is None and DEFAULT_CATALOGUE.is_dir():
        cat = DEFAULT_CATALOGUE
    return text, log, export, Path(cat) if cat else None


def run(structure, to, text=None, cat=None) -> Result:
    structure, to = Path(structure), Path(to)
    text, _log, export, cat = plan(structure, to, text, cat)
    res = Result(out=to, catalogue=cat)
    excluded = _excluded(cat)
    to.mkdir(parents=True, exist_ok=True)
    current = set()
    items = sorted(export.get("entries", {}).items(),
                   key=lambda kv: kv[1].get("stem", "").lower())
    for id_, rec in items:
        stem = rec.get("stem", id_[:12])
        if id_ in excluded:
            res.excluded.append(stem)
            continue
        want = rec.get("input_sha256", "")
        current.add(want)
        try:
            res.written.append(_one(structure, text, to, stem, rec))
        except HandoffError as e:
            res.skipped.append((stem, str(e)))
    for p in sorted(to.glob(f"*{SUFFIX}")):
        if _NAME.match(p.name) and p.name[:64] not in current:
            res.not_current.append(p.name)
    res.index = _write_index(to, res)
    return res


def _one(structure: Path, text: Path, to: Path, stem: str, rec: dict) -> Written:
    sec_path = structure / f"{stem}.sections.json"
    if not sec_path.is_file():
        raise HandoffError(f"no {sec_path.name}: run gpdf-cli structure")
    sections = _read_json(sec_path, "the sections file")
    units_path = text / f"{stem}.jsonl"
    if not units_path.is_file():
        raise HandoffError(f"no {units_path.name} in the text folder")
    if sha256(units_path) != sections.get("units_sha256"):
        raise HandoffError("the export changed since structure ran: run gpdf-cli structure "
                           "again (and check its review sheet)")
    want = rec.get("input_sha256", "")
    if sections.get("input_sha256") != want:
        raise HandoffError("the sections file is for another version of the working file: run "
                           "gpdf-cli structure again")
    src = Path(str(rec.get("input", "")))
    if not src.is_file():
        raise HandoffError(f"the working file {src} is missing")
    if sha256(src) != want:
        raise HandoffError(f"the working file changed since the export ({src}): export it "
                           "again, then run structure")
    units = _jsonl(units_path)
    app = [] if sections["kind"] == "txt" else _apparatus_pages(text, stem, units)
    rows = lines_for(sections, units, app)
    problems = validate(rows)
    if problems:
        raise HandoffError("the lines do not validate: " + "; ".join(problems[:5]))
    path = to / f"{want}{SUFFIX}"
    catalogue.write_text(path, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    paras = [r for r in rows if r["type"] == "para"]
    kinds: dict[str, int] = {}
    for p in paras:
        kinds[p["kind"]] = kinds.get(p["kind"], 0) + 1
    ok, why = confirmed(sections.get("decisions") or {})
    return Written(stem=stem, sha=want, file_name=rows[0]["file_name"], path=path,
                   title=rows[0]["title"],
                   counts={"sections": len(rows) - 1 - len(paras), "paras": len(paras),
                           "kinds": kinds, "chars": sum(len(p["text"]) for p in paras),
                           "pages": rows[0]["pages"]},
                   metadata_confirmed=rows[0]["metadata_confirmed"], structure_confirmed=ok,
                   why_unconfirmed=why)


def _write_index(to: Path, res: Result) -> Path:
    path = to / INDEX
    now = catalogue.now()
    data = {"format": FORMAT, "made_with": f"g-pdf {__version__}", "made": now,
            "files": {w.sha: {"file": w.path.name, "file_name": w.file_name, "entry_stem": w.stem,
                              "title": w.title, "counts": w.counts, "made": now,
                              "metadata_confirmed": w.metadata_confirmed,
                              "structure_confirmed": w.structure_confirmed}
                      for w in res.written},
            "not_current": res.not_current,
            "skipped": [{"stem": s, "why": why} for s, why in res.skipped],
            "excluded": res.excluded}
    catalogue.write_text(path, json.dumps(data, indent=1, ensure_ascii=False) + "\n")
    return path


def report(res: Result) -> str:
    out = []
    for w in res.written:
        struct = ("confirmed" if w.structure_confirmed
                  else f"not confirmed ({w.why_unconfirmed})")
        out.append(f"{w.file_name} -> {w.path.name}: {w.counts['paras']} paras, "
                   f"{w.counts['sections']} sections; metadata "
                   f"{'confirmed' if w.metadata_confirmed else 'not confirmed'}; structure "
                   f"{struct}")
    for stem, why in res.skipped:
        out.append(f"skipped {stem}: {why}")
    if res.excluded:
        out.append("excluded in the catalogue: " + ", ".join(res.excluded))
    out.append(f"exclusions: checked against {res.catalogue}" if res.catalogue else
               "exclusions: not checked (no catalogue folder; pass --catalogue)")
    if res.not_current:
        out.append(f"{len(res.not_current)} file(s) in {res.out} are no longer current (remove "
                   "them yourself): " + ", ".join(res.not_current))
    size = sum(p.stat().st_size for p in res.out.glob(f"*{SUFFIX}"))
    out.append(f"wrote {len(res.written)} handoff file(s) and {res.index.name}, skipped "
               f"{len(res.skipped)}; folder {res.out}: {size / 1e6:.1f} MB of handoff files")
    return "\n".join(out)
