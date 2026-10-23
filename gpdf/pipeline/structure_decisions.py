# SPDX-License-Identifier: AGPL-3.0-or-later
"""The owner's structure decisions: OUTDIR/structure.decisions.json, written only by the
review sheet (structure.review.html) and read, never written, by gpdf-cli structure.

    {"schema": 1, "saved": "...", "entries": {<entry id>: {
        "metadata": {"title", "author", "year", "publisher", "confirmed"},   optional
        "role": "primary",                    optional: every section of the entry
        "reviewed": true,                     optional: the owner ticked "Structure reviewed";
                                              without it the structure is not confirmed
        "ops": [                              applied in order, after the role
          {"op": "edit",  "seq", "level", "hash", "title"?, "author"?, "role"?},
          {"op": "move",  "seq", "level", "hash", "to", "to_hash"},
          {"op": "merge", "seq", "level", "hash"},
          {"op": "split", "seq", "level", "hash", "title", "author", "role"}]}}}

A section is named by the unit it starts at (entry id + seq, stable while the export is
unchanged) and its level; ``hash`` is a short hash of that unit's text (``to_hash`` of the
unit a boundary moves to). When the text differs the export changed: the decision is
stale and is not applied. A decision whose section is gone (or that would break the
tiling) is unmatched and not applied either. The review sheet replays the same rules in
JavaScript (structure_sheet/sheet.js); keep the two in step.

A level is any whole number from 1 up (detection gives 1-3; the owner may go deeper), and a
section is at most one level deeper than its parent: a split that would start a section
further below the section around it is unmatched.

Moving a section's start moves the sections of deeper levels that start at the same unit
with it; merging removes the boundary (the units join the section before them); a split
starts a new section at a unit.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from gpdf.pipeline import catalogue
from gpdf.pipeline import structure as st

SCHEMA = 1
DECISIONS = "structure.decisions.json"
SHEET = "structure.review.html"
SUMMARY_HEAD, SUMMARY_TAIL = 120, 80   # characters of a long unit shown in the sheet
KIND_CODES = {"heading": "h", "prose": "p", "verse": "v", "quote": "q", "note": "n"}
HASH_LEN = 10
APPLIED, STALE, UNMATCHED = "applied", "stale", "unmatched"
OPS = ("edit", "move", "merge", "split")
META_FIELDS = ("title", "author", "year", "publisher")


class DecisionsError(Exception):
    """A decisions file that cannot be used; the message is meant for the user."""


def unit_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:HASH_LEN]


def empty() -> dict:
    return {"schema": SCHEMA, "entries": {}}


def load(path) -> dict:
    """The decisions file (empty if there is none). Never written here."""
    path = Path(path)
    if not path.exists():
        return empty()
    try:
        d = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError) as e:
        raise DecisionsError(f"{path} is not valid JSON ({e}); fix it or move it away "
                             "(structure never rewrites it)") from None
    if not (isinstance(d, dict) and d.get("schema") == SCHEMA
            and isinstance(d.get("entries"), dict)
            and all(isinstance(v, dict) for v in d["entries"].values())):
        raise DecisionsError(f"{path} is not a schema-{SCHEMA} structure decisions file "
                             '({"schema": 1, "entries": {id: {...}}}); fix it or move it away')
    for id_, rec in d["entries"].items():
        if not isinstance(rec.get("ops", []), list) or not all(
                isinstance(op, dict) for op in rec.get("ops", [])):
            raise DecisionsError(f"{path}: the ops of entry {id_[:12]} are not a list of "
                                 "objects; fix it or move it away")
        if not isinstance(rec.get("metadata", {}), dict):
            raise DecisionsError(f"{path}: the metadata of entry {id_[:12]} is not an object")
    return d


# ---------------------------------------------------------------------------- applying
def _result(op: str, status: str, why: str = "", seq=None, level=None) -> dict:
    return {"op": op, "seq": seq, "level": level, "status": status, "why": why}


def _ok_hash(units: list, seq, want) -> bool:
    return (isinstance(seq, int) and not isinstance(seq, bool) and 1 <= seq <= len(units)
            and unit_hash(units[seq - 1]["text"]) == want)


def _ends(secs: list, n: int) -> None:
    from gpdf.pipeline import sections as sc

    secs.sort(key=lambda y: (y.start, y.level))
    sc._ends(secs, n)


def _apply_op(secs: list, units: list, op: dict, new_section) -> tuple[str, str]:
    """(status, why) of one op; ``secs`` is changed only when it applies."""
    kind, seq, level = op.get("op"), op.get("seq"), op.get("level")
    if kind not in OPS:
        return UNMATCHED, f"unknown decision {kind!r}"
    if not _ok_hash(units, seq, op.get("hash")):
        return STALE, (f"unit {seq} is not the text it was when the decision was made "
                       "(the export changed)")
    if not (isinstance(level, int) and not isinstance(level, bool) and level >= 1):
        return UNMATCHED, f"level {json.dumps(level)} is not a whole number from 1 up"
    start = seq - 1
    role = op.get("role")
    if "role" in op and role not in st.ROLES:
        return UNMATCHED, f"role {role!r} is not one of {', '.join(st.ROLES)}"
    if kind == "split":
        if any(x.start == start and x.level == level for x in secs):
            return UNMATCHED, f"a level-{level} section already starts at unit {seq}"
        parent = max((x for x in secs if x.start <= start < x.end and x.level < level),
                     key=lambda x: x.level, default=None)
        if parent is not None and level > parent.level + 1:
            title = str(op.get("title") or "").strip() or "(untitled)"
            return UNMATCHED, (f"«{title[:40]}» (level {level}) would be "
                               f"{level - parent.level} levels below its parent "
                               f"«{parent.title[:40]}» (level {parent.level}): a section is "
                               "at most one level deeper than its parent")
        inner = max((x for x in secs if x.start <= start < x.end), key=lambda x: x.level)
        x = new_section(level=level, title=str(op.get("title") or "").strip() or "(untitled)",
                        author=str(op.get("author") or "").strip() or None,
                        role=role or inner.role, start=start)
        x.evidence = [f"owner: split at unit {seq}"]
        secs.append(x)
        return APPLIED, ""
    target = next((x for x in secs if x.start == start and x.level == level), None)
    if target is None:
        return UNMATCHED, f"no level-{level} section starts at unit {seq}"
    if kind == "edit":
        changed = []
        if "title" in op:
            target.title = str(op["title"] or "").strip() or target.title
            changed.append("title")
        if "author" in op:
            target.author = str(op["author"] or "").strip() or None
            changed.append("author")
        if "role" in op:
            target.role = role
            changed.append("role")
        target.evidence = list(target.evidence) + [f"owner: {', '.join(changed) or 'nothing'} "
                                                   "edited"]
        target.owner = True
        return APPLIED, ""
    if kind == "merge":
        if start == 0 and level == 1:
            return UNMATCHED, "the first part has nothing before it to merge into"
        secs.remove(target)
        return APPLIED, ""
    to = op.get("to")                                          # move
    if not _ok_hash(units, to, op.get("to_hash")):
        return STALE, (f"unit {to} is not the text it was when the decision was made "
                       "(the export changed)")
    new = to - 1
    if start == 0:
        return UNMATCHED, "the file's first section starts at the first unit"
    if new == start:
        return UNMATCHED, f"it already starts at unit {to}"
    if new < start:
        before = [x.start for x in secs if x.start < start]
        if new <= max(before):
            return UNMATCHED, f"moving it to unit {to} would empty the section before it"
        parent = next((x for x in secs if x.start == start and x.level < level), None)
        if parent is not None:
            return UNMATCHED, (f"its parent «{parent.title[:40]}» starts at the same unit: "
                               "move the parent")
    else:
        after = [x.start for x in secs if x.start > start]
        if new >= (min(after) if after else len(units)):
            return UNMATCHED, f"moving it to unit {to} would empty it"
    for x in secs:
        if x.start == start and x.level >= level:
            x.start = new
            if x is target or x.level > level:
                x.evidence = list(x.evidence) + [f"owner: start moved from unit {seq} to {to}"]
                x.owner = True
    return APPLIED, ""


def apply(s, rec: dict | None) -> list[dict]:
    """Apply one entry's decisions over its detected structure ``s`` (a sections.Structure;
    changed in place, its derived fields and checks refreshed). Returns one result per
    decision: applied, stale or unmatched, and why."""
    from gpdf.pipeline import sections as sc

    results: list[dict] = []
    if not rec:
        return results
    units = s.entry.units
    secs = s.sections
    role = rec.get("role")
    if role:
        if role in st.ROLES:
            for x in secs:
                x.role = role
            results.append(_result("role", APPLIED))
        else:
            results.append(_result("role", UNMATCHED, f"role {role!r} is not one of "
                                                      f"{', '.join(st.ROLES)}"))
    md = rec.get("metadata")
    if md:
        for k in META_FIELDS:
            if k in md:
                v = md[k]
                s.metadata[k] = (str(v).strip() or None) if v is not None else None
        s.metadata["confirmed"] = bool(md.get("confirmed"))
        s.metadata["evidence"] = ("confirmed by the owner" if s.metadata["confirmed"]
                                  else "edited by the owner; not confirmed")
        results.append(_result("metadata", APPLIED))

    def new_section(**kw):
        return sc.Section(confidence=st.HIGH, sources=["owner"], role_fixed=True, owner=True,
                          **kw)

    n = len(units)
    _ends(secs, n)
    for op in rec.get("ops", []):
        status, why = _apply_op(secs, units, op, new_section)
        results.append(_result(str(op.get("op")), status, why, op.get("seq"), op.get("level")))
        _ends(secs, n)
    sc.refresh(s)
    return results


def summary(results: list[dict], path, record: bool, reviewed: bool = False) -> dict:
    return {"file": str(path) if path else None, "record": record, "reviewed": reviewed,
            "applied": sum(r["status"] == APPLIED for r in results),
            "stale": sum(r["status"] == STALE for r in results),
            "unmatched": sum(r["status"] == UNMATCHED for r in results),
            "results": results}


def confirmed(summary: dict | None) -> tuple[bool, str]:
    """(structure confirmed, why not) from an entry's ``summary``: the owner ticked
    "Structure reviewed" and none of its decisions is stale or unmatched. Decisions
    alone (a planner's draft) do not confirm anything. The sheet's entryStatus uses the
    same rule."""
    d = summary or {}
    bad = d.get("stale", 0) + d.get("unmatched", 0)
    if bad:
        return False, f"{bad} stale or unmatched decision{'s' if bad != 1 else ''}"
    if not d.get("reviewed"):
        return False, "'Structure reviewed' not ticked"
    return True, ""


# ---------------------------------------------------------------------------- review sheet
def unit_summary(text: str) -> str:
    """A unit as the sheet shows it: whole when short, else its start and its end."""
    if len(text) <= SUMMARY_HEAD + SUMMARY_TAIL:
        return text
    return text[:SUMMARY_HEAD] + " … " + text[-SUMMARY_TAIL:]


def _units_view(e) -> list[list]:
    """Per unit (index = seq - 1): kind code, first and last page (or line), first and
    last printed label, summary, hash."""
    out = []
    for u in e.units:
        loc = u.get("pages") or u.get("lines") or [None, None]
        labels = u.get("labels") or [None, None]
        out.append([KIND_CODES.get(u["kind"], u["kind"][:1]), loc[0], loc[-1], labels[0],
                    labels[-1], unit_summary(u["text"]), unit_hash(u["text"])])
    return out


def _entry_view(s) -> dict:
    e = s.entry
    keep = ("first", "level", "title", "author", "role", "confidence", "evidence", "sources")
    return {"id": e.id, "stem": e.stem, "file": Path(e.input).name, "kind": e.kind,
            "book_kind": s.kind, "pages": e.page_count, "lang": e.lang,
            "metadata": s.detected_metadata,
            "detected": [{k: x[k] for k in keep} for x in s.detected],
            "low": sum(x["confidence"] == st.LOW for x in s.detected),
            "units": _units_view(e)}


def sheet_data(structures, decisions: dict, folder) -> dict:
    """The sheet's data: every entry's detected sections (before the decisions; the sheet
    replays them), units and prefilled metadata, low-confidence entries first."""
    entries = sorted((_entry_view(s) for s in structures),
                     key=lambda v: (-v["low"], v["file"].lower()))
    return {"folder": str(folder), "decisions_name": DECISIONS, "generated": catalogue.now(),
            "roles": list(st.ROLES), "decisions": decisions, "entries": entries}


def _template(name: str) -> str:
    from importlib import resources

    return resources.files("gpdf.pipeline").joinpath("structure_sheet", name).read_text("utf-8")


def render_html(data: dict) -> str:
    """The sheet: template with CSS, JS and the data inlined. ``</`` and ``://`` are
    escaped in the JSON (no string in it can end the script element or read as a link)."""
    blob = (json.dumps(data, ensure_ascii=False, separators=(",", ":"))
            .replace("</", "<\\/").replace("://", "\\u003a//"))
    page = _template("sheet.html")
    for token, value in (("{{css}}", _template("sheet.css")), ("{{js}}", _template("sheet.js"))):
        page = page.replace(token, value)
    return page.replace("{{data}}", blob)          # last: the data may contain anything


def write_sheet(structures, decisions: dict, folder) -> tuple[Path, int]:
    """Write DIR/structure.review.html (always replaced); returns (path, bytes)."""
    path = Path(folder) / SHEET
    catalogue.write_text(path, render_html(sheet_data(structures, decisions, Path(folder))))
    return path, path.stat().st_size
