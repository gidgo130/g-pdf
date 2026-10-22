# SPDX-License-Identifier: AGPL-3.0-or-later
"""The survey's catalogue: one entry per file (id = sha256 of its bytes), merged across
runs so the owner's decisions survive every re-run.

Files in the catalogue folder:
  catalogue.json            tool-owned, schema 1, rewritten by every survey (temp + replace)
  catalogue.decisions.json  owner-owned, written only by the review sheet; read, never written
  catalogue.review.html     the review sheet, always rewritten
  cache/<sha256>.npy        shingles of each measured file
  applied.json              written only by gpdf-cli apply: a record per output it wrote

A run touches only the entries under the roots it was given. A file that is gone becomes
"missing" (never dropped); the same bytes at a new path keep their entry (moved or
renamed); new bytes at a known path make a new entry, and the old one gets
``replaced_by`` while the new one remembers it as ``previous_id``: a decision made for the
old bytes is shown as "needs re-confirm", never applied silently to changed content.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

import numpy as np

SCHEMA = 1
CATALOGUE, DECISIONS, SHEET, CACHE, APPLIED_LOG = (
    "catalogue.json", "catalogue.decisions.json", "catalogue.review.html", "cache",
    "applied.json")
PRESENT, MISSING, ERROR = "present", "missing", "error"
CONFIRMED, RECONFIRM, PENDING, APPLIED = "confirmed", "needs re-confirm", "pending", "applied"


class CatalogueError(Exception):
    """A problem with the catalogue folder; the message is meant for the user."""


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def stamp(t: float) -> str:
    return datetime.fromtimestamp(t).astimezone().isoformat(timespec="seconds")


def norm(path) -> str:
    """Absolute, normalised, case kept as given."""
    return os.path.normpath(os.path.abspath(path))


def same_path(a, b) -> bool:
    return os.path.normcase(norm(a)) == os.path.normcase(norm(b))


def under(path, folder) -> bool:
    """``path`` is ``folder`` or inside it (case-insensitively on Windows)."""
    p, f = os.path.normcase(norm(path)), os.path.normcase(norm(folder))
    return p == f or p.startswith(f.rstrip(os.sep) + os.sep)


# ---------------------------------------------------------------------------- JSON
def dumps(obj) -> str:
    """Indented JSON with flat lists and flat objects on one line (per-page rows)."""
    def flat(o):
        return all(not isinstance(v, (dict, list)) for v in o)

    def enc(o, ind):
        if isinstance(o, dict) and o and not flat(o.values()):
            pad = " " * (ind + 1)
            body = ",\n".join(f"{pad}{json.dumps(k, ensure_ascii=False)}: {enc(v, ind + 1)}"
                              for k, v in o.items())
            return "{\n" + body + "\n" + " " * ind + "}"
        if isinstance(o, list) and o and not flat(o):
            pad = " " * (ind + 1)
            return "[\n" + ",\n".join(pad + enc(v, ind + 1) for v in o) + "\n" + " " * ind + "]"
        return json.dumps(o, ensure_ascii=False)

    return enc(obj, 0) + "\n"


def write_text(path, text) -> None:
    """Write via a temporary file in the same folder and os.replace."""
    path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".gpdf-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


# ---------------------------------------------------------------------------- catalogue
def empty() -> dict:
    return {"schema": SCHEMA, "tool_version": "", "score_version": 0, "updated": None,
            "roots": [], "files": {}}


def load(folder) -> dict:
    path = Path(folder) / CATALOGUE
    if not path.exists():
        return empty()
    try:
        cat = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError) as e:
        raise CatalogueError(f"cannot read {path}: {e} (move it away to start a new "
                             "catalogue)") from None
    if not isinstance(cat, dict) or cat.get("schema") != SCHEMA or not isinstance(
            cat.get("files"), dict):
        raise CatalogueError(f"{path} is not a schema-{SCHEMA} catalogue")
    cat.setdefault("roots", [])
    return cat


def save(folder, cat) -> Path:
    path = Path(folder) / CATALOGUE
    write_text(path, dumps(cat))
    return path


def cache_path(folder, sha) -> Path:
    return Path(folder) / CACHE / f"{sha}.npy"


def load_shingles(folder, sha):
    path = cache_path(folder, sha)
    try:
        return np.load(path, allow_pickle=False)
    except (OSError, ValueError):
        return None


def save_shingles(folder, sha, arr) -> None:
    path = cache_path(folder, sha)
    path.parent.mkdir(exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".gpdf-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            np.save(f, np.asarray(arr, np.uint32), allow_pickle=False)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def merge(cat, roots, found, when) -> dict:
    """Merge the files ``found`` under ``roots`` into ``cat`` (in place). ``found``: dicts
    with sha, path, root, rel, size, mtime, kind, sorted by path. Returns counts."""
    files = cat["files"]
    in_scope = {k for k, e in files.items() if any(under(e["path"], r) for r in roots)}
    counts = {"new": 0, "moved": 0, "changed": 0, "missing": 0}
    by_sha = {}
    for f in found:
        by_sha.setdefault(f["sha"], []).append(f)
    for sha, items in by_sha.items():
        e = files.get(sha)
        primary = items[0]
        if e:
            primary = next((f for f in items if same_path(f["path"], e["path"])), items[0])
            if not same_path(primary["path"], e["path"]):
                counts["moved"] += 1
                e.setdefault("moved_from", [])
                e["moved_from"].append(e["path"])
            if e.get("status") != PRESENT:
                e.pop("replaced_by", None)
                e.pop("missing_since", None)
        else:
            e = files[sha] = {"id": sha, "first_seen": when}
            counts["new"] += 1
        e.update({"path": primary["path"], "root": primary["root"], "rel": primary["rel"],
                  "size": primary["size"], "mtime": primary["mtime"], "kind": primary["kind"],
                  "status": PRESENT, "last_seen": when})
        copies = [f["path"] for f in items if f is not primary]
        if copies:
            e["copies"] = copies
        else:
            e.pop("copies", None)
    at_path = {os.path.normcase(f["path"]): f["sha"] for f in found}
    for k in sorted(in_scope - by_sha.keys()):
        e = files[k]
        if e.get("status") != MISSING:
            e["status"] = MISSING
            e["missing_since"] = when
            counts["missing"] += 1
        new = at_path.get(os.path.normcase(e["path"]))
        if new and new != k:
            e["replaced_by"] = new
            if "previous_id" not in files[new]:
                files[new]["previous_id"] = k
                counts["changed"] += 1
    return counts


def set_root(cat, root, when) -> None:
    for r in cat["roots"]:
        if same_path(r["path"], root):
            r["path"], r["last_survey"] = root, when
            return
    cat["roots"].append({"path": root, "last_survey": when})


# ---------------------------------------------------------------------------- decisions
def load_decisions(folder) -> dict:
    """The owner's decisions file ({} of files if there is none). Never written here."""
    path = Path(folder) / DECISIONS
    if not path.exists():
        return {"schema": SCHEMA, "files": {}}
    try:
        d = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError) as e:
        raise CatalogueError(f"{path} is not valid JSON ({e}); fix it or move it away "
                             "(the survey never rewrites it)") from None
    if not (isinstance(d, dict) and d.get("schema") == SCHEMA and isinstance(d.get("files"), dict)
            and all(isinstance(v, dict) for v in d["files"].values())):
        raise CatalogueError(f"{path} is not a schema-{SCHEMA} decisions file "
                             '({"schema": 1, "files": {id: {...}}}); fix it or move it away')
    return d


def load_applied(folder) -> dict:
    """DIR/applied.json ({"schema": 1, "applied": [records]}; empty if there is none)."""
    path = Path(folder) / APPLIED_LOG
    if not path.exists():
        return {"schema": SCHEMA, "applied": []}
    try:
        d = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError) as e:
        raise CatalogueError(f"cannot read {path}: {e}") from None
    if not (isinstance(d, dict) and d.get("schema") == SCHEMA
            and isinstance(d.get("applied"), list)):
        raise CatalogueError(f"{path} is not a schema-{SCHEMA} applied file")
    return d


def applied_record(entry, applied) -> dict | None:
    """The latest applied.json record whose output is ``entry``'s working file, with the
    same bytes."""
    wf = entry.get("working_file")
    if not wf:
        return None
    for rec in reversed(applied.get("applied", [])):
        if (rec.get("id") == entry["id"] and rec.get("output_sha256") == wf["sha256"]
                and same_path(rec.get("output", ""), wf["path"])):
            return rec
    return None


def _pct(v) -> str:
    return "n/a" if v is None else f"{v:.1f}%"


def decision_status(entry, decisions, files) -> tuple[str, dict | None, str]:
    """(status, the decision that applies, why) for an entry: confirmed, applied (the
    entry's working file is this decision's output), needs re-confirm (the file changed,
    or the suggestion changed since it was confirmed) or pending."""
    dec = decisions.get("files", {})
    d = dec.get(entry["id"])
    if d is not None:
        if not d.get("confirmed"):
            return PENDING, d, "not confirmed"
        a = entry.get("applied")
        if a and a.get("action") == d.get("action"):
            return APPLIED, d, (f"{a['action']} applied: median suspect "
                                f"{_pct(a.get('median_before'))} -> "
                                f"{_pct(a.get('median_after'))}")
        was = d.get("suggestion_action")
        now_ = (entry.get("suggestion") or {}).get("action")
        if was and now_ and was != now_:
            return RECONFIRM, d, f"suggestion changed ({was} -> {now_})"
        return CONFIRMED, d, ""
    prev, seen = entry.get("previous_id"), set()
    while prev and prev not in seen:
        seen.add(prev)
        if prev in dec:
            return RECONFIRM, dec[prev], "file changed since the decision"
        prev = files.get(prev, {}).get("previous_id")
    return PENDING, None, "no decision"


# ---------------------------------------------------------------------------- review sheet
THUMB_DPI, THUMB_QUALITY = 60, 60
MAX_INK_SHOWN = 12
CLASS_CODES = {"good": 0, "fair": 1, "poor": 2, "unscored": 3}
ROW_FIELDS = ("page", "lang", "words", "lower", "pct", "cls", "merged", "spaced", "digit",
              "chaos", "junk_pct", "cap_unknown_pct", "missing_accent_pct")


def _template(name: str) -> str:
    from importlib import resources

    return resources.files("gpdf.pipeline").joinpath("catalogue_sheet", name).read_text("utf-8")


def _jpeg(page) -> dict:
    import base64

    import pymupdf

    pix = page.get_pixmap(dpi=THUMB_DPI, colorspace=pymupdf.csGRAY)
    data = base64.b64encode(pix.tobytes("jpg", jpg_quality=THUMB_QUALITY)).decode("ascii")
    return {"src": "data:image/jpeg;base64," + data, "w": pix.width, "h": pix.height}


def _images(e) -> list[dict]:
    """60 dpi renders of the worst scored pages and the ink pages without text, with the
    start of their extracted text and of the trial's text."""
    import pymupdf

    m = e.get("measured") or {}
    if e.get("status") != PRESENT or m.get("kind") != "pdf":
        return []
    trial = {p["page"]: p for p in (e.get("trial") or {}).get("pages", [])}
    shown = [(s["page"], "worst", s.get("text", "")) for s in m.get("samples", [])]
    shown += [(p, "no text, ink", "") for p in m["no_text"]["ink"][:MAX_INK_SHOWN]]
    wf = e.get("working_file")
    out = []
    try:
        with pymupdf.open(wf["path"] if wf else e["path"]) as doc:
            for page, label, text in shown:
                if 1 <= page <= len(doc):
                    t = trial.get(page) or {}
                    out.append({"page": page, "label": label, "text": text,
                                "trial_text": t.get("text"), **_jpeg(doc[page - 1])})
    except Exception:  # noqa: BLE001 - a file that vanished or broke: no pictures
        return out
    return out


def _view(e, decisions, files) -> dict:
    status, d, why = decision_status(e, decisions, files)
    m = e.get("measured")
    view = {k: e.get(k) for k in ("id", "path", "root", "rel", "kind", "status", "size",
                                  "mtime", "first_seen", "last_seen", "previous_id",
                                  "replaced_by", "error", "working_file", "duplicates",
                                  "suggestion", "applied")}
    if m:
        view["measured"] = {k: v for k, v in m.items() if k not in ("page_scores", "samples")}
        view["rows"] = [[r.get(f) if f != "cls" else CLASS_CODES.get(r["cls"], 3)
                         for f in ROW_FIELDS] for r in m["page_scores"]]
    else:
        view["measured"], view["rows"] = None, []
    sl = e.get("source_layer")
    view["source_layer"] = {k: sl[k] for k in ("score", "language") if k in sl} if sl else None
    t = e.get("trial")
    view["trial"] = ({k: v for k, v in t.items()} if t else None)
    view["decision_status"] = {"status": status, "why": why, "decision": d}
    view["images"] = _images(e)
    return view


def sheet_data(cat, decisions, folder) -> dict:
    files = cat["files"]
    order = sorted(files, key=lambda k: (files[k]["root"].lower(), files[k]["rel"].lower()))
    return {
        "folder": str(folder),
        "decisions_name": DECISIONS,
        "generated": now(),
        "score_version": cat.get("score_version"),
        "roots": cat.get("roots", []),
        "row_fields": list(ROW_FIELDS),
        "classes": list(CLASS_CODES),
        "decisions": decisions,
        "entries": [_view(files[k], decisions, files) for k in order],
    }


def render_html(data: dict) -> str:
    """The sheet: template with CSS, JS and the data inlined. ``</`` and ``://`` are
    escaped in the JSON (no string in it can end the script element or read as a link)."""
    import html

    blob = (json.dumps(data, ensure_ascii=False, separators=(",", ":"))
            .replace("</", "<\\/").replace("://", "\\u003a//"))
    page = _template("sheet.html")
    for token, value in (("{{title}}", html.escape("Catalogue review")),
                         ("{{css}}", _template("sheet.css")),
                         ("{{js}}", _template("sheet.js"))):
        page = page.replace(token, value)
    return page.replace("{{data}}", blob)          # last: the data may contain anything


def write_sheet(cat, decisions, folder) -> tuple[Path, int]:
    """Write DIR/catalogue.review.html (always replaced); returns (path, bytes)."""
    path = Path(folder) / SHEET
    write_text(path, render_html(sheet_data(cat, decisions, Path(folder))))
    return path, path.stat().st_size
