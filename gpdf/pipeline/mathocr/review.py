# SPDX-License-Identifier: AGPL-3.0-or-later
"""gpdf-cli math-apply-review: apply the checked review decisions to the page files.

Decisions (review_decisions.jsonl, one JSON object per line):

    {"id": "p073-d02", "action": "accept"}
    {"id": "p073-d02", "action": "replace", "latex": "..."}
    {"id": "p073-d02", "action": "flag", "flag_type": "possible_source_typo", "note": "..."}
    {"id": "p073-inline", "action": "replace",
     "spans": [{"latex_primary": "x_1", "latex": "x_i"}]}      (a grouped inline item)

flag_type is one of formula_uncertain, possible_source_typo, copy_corrupt, illegible.
Every applied decision leaves a ``reviewed`` record in the page's flags, which makes a
second run a no-op. A decision whose id is unknown, or whose latex_primary is no longer
in the page (and that was not applied before), is refused and listed; the other
decisions still go through. A display replace that loses row breaks, alignment points,
environments or \\left/\\right pairs, unbalances braces, or writes a stray `\\ ` where the
old text had a `\\\\` row break is refused too, unless it carries "structure_change": true
and a "reason". Pages without a decision are never touched. text.md is
rebuilt from the page files.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from gpdf.pipeline.manuals import write_atomic
from gpdf.pipeline.mathocr import latex as L

ACTIONS = ("accept", "replace", "flag")
TAG_LINE = re.compile(r"\n\\tag\*?\{[^{}\n]*\}")
FLAG_TYPES = ("formula_uncertain", "possible_source_typo", "copy_corrupt", "illegible")


class ReviewError(Exception):
    """The folder or the decisions file cannot be used; the message is for the user."""


@dataclass
class Report:
    applied: list[dict] = field(default_factory=list)      # {id, action, page}
    already: list[str] = field(default_factory=list)       # ids applied by an earlier run
    refused: list[dict] = field(default_factory=list)      # {id, reason}
    pages: list[int] = field(default_factory=list)         # page files written


def load_decisions(path: Path) -> list[dict]:
    """The decisions of a JSON-lines (or JSON list) file, or of every *.jsonl file in a
    folder (one per review batch), in file-name order."""
    path = Path(path)
    if path.is_dir():
        files = sorted(path.glob("*.jsonl"))
        if not files:
            raise ReviewError(f"{path}: no .jsonl decision files in the folder")
        return [d for f in files for d in load_decisions(f)]
    text = path.read_text("utf-8")
    try:
        if text.lstrip().startswith("["):
            out = json.loads(text)
        else:
            out = [json.loads(s) for s in text.splitlines() if s.strip()]
    except json.JSONDecodeError as e:
        raise ReviewError(f"{path}: not JSON lines: {e}") from None
    if not isinstance(out, list) or not all(isinstance(d, dict) for d in out):
        raise ReviewError(f"{path}: expected one JSON object per line")
    return out


def load_items(root: Path) -> dict[str, dict]:
    path = root / "review.jsonl"
    if not path.is_file():
        raise ReviewError(f"{path} not found: is {root} a math-export folder?")
    items = {}
    for s in path.read_text("utf-8").splitlines():
        if s.strip():
            it = json.loads(s)
            items[it["id"]] = it
    return items


def _record(decision: dict) -> dict:
    rec = {"type": "reviewed", "id": decision["id"], "action": decision["action"]}
    for k in ("latex", "tag", "after", "page_text", "flag_type", "subtype", "note", "spans",
              "runs"):
        if decision.get(k) is not None:
            rec[k] = decision[k]
    if decision.get("structure_change") is True:
        rec["structure_change"] = True
        rec["reason"] = decision.get("reason")
    return rec


def structure_problems(old: str | None, new: str) -> list[str]:
    """What a display replace loses or unbalances: row breaks, alignment points,
    environments, \\left/\\right pairs, braces; a stray `\\ ` where the old had `\\\\`."""
    b = L.structure(new)
    if old is None:
        return list(b["balance"])
    a = L.structure(old)
    out = []
    if b["row_breaks"] < a["row_breaks"]:
        out.append(f"row breaks \\\\ {a['row_breaks']} -> {b['row_breaks']}")
    if b["control_space"] > a["control_space"] and a["row_breaks"]:
        out.append("a stray `\\ ` where the old had a `\\\\` row break")
    if b["align"] < a["align"]:
        out.append(f"alignment: rows with & {a['align']} -> {b['align']}")
    if b["envs"] < a["envs"]:
        out.append(f"\\begin/\\end environment pairs {a['envs']} -> {b['envs']}")
    if b["left"] < a["left"]:
        out.append(f"\\left/\\right pairs {a['left']} -> {b['left']}")
    out += [p for p in b["balance"] if p not in a["balance"]]
    return out


def _structure_refusal(old: str | None, d: dict) -> str | None:
    problems = structure_problems(old, d["latex"].strip())
    if not problems:
        return None
    if d.get("structure_change") is True:
        if isinstance(d.get("reason"), str) and d["reason"].strip():
            return None
        return "structure_change needs a reason: " + "; ".join(problems)
    return ("structure lost: " + "; ".join(problems)
            + " (if intended, add \"structure_change\": true and a reason)")


def _display_block(latex: str, md: str) -> tuple[int, int] | None:
    """(start, end) of the display body ``latex`` in the page text, or None."""
    needle = "$$\n" + latex + "\n"
    i = md.find(needle)
    while i >= 0:
        j = i + len(needle)
        rest = md[j:]
        if rest.startswith("$$") or rest.startswith("\\tag{"):
            return i + 3, i + 3 + len(latex)
        i = md.find(needle, i + 1)
    return None


def _inline_span(latex: str, md: str) -> tuple[int, int] | None:
    needle = "\\(" + latex + "\\)"
    i = md.find(needle)
    return (i + 2, i + 2 + len(latex)) if i >= 0 else None


def _validate(d: dict, items: dict) -> str | None:
    if not isinstance(d.get("id"), str) or d["id"] not in items:
        return "unknown id"
    if d.get("action") not in ACTIONS:
        return f"action must be one of {', '.join(ACTIONS)}"
    if d["action"] == "flag" and d.get("flag_type") not in FLAG_TYPES:
        return f"flag_type must be one of {', '.join(FLAG_TYPES)}"
    if d["action"] == "replace":
        if items[d["id"]].get("kind") == "inline_missing":
            runs = d.get("runs")
            if not runs or not all(isinstance(r, dict) and isinstance(r.get("text"), str)
                                   and r["text"] and isinstance(r.get("latex"), str)
                                   and r["latex"].strip() for r in runs):
                return "an inline_missing item needs runs [{text, latex}]"
        elif items[d["id"]].get("spans") is not None:
            spans = d.get("spans")
            if not spans or not all(isinstance(s, dict) and s.get("latex_primary") and
                                    isinstance(s.get("latex"), str) for s in spans):
                return "a grouped inline item needs spans [{latex_primary, latex}]"
        elif not isinstance(d.get("latex"), str) or not d["latex"].strip():
            return "replace needs latex"
    return None


def apply(root, decisions_path, dry_run: bool = False) -> Report:
    root = Path(root)
    items = load_items(root)
    decisions = load_decisions(Path(decisions_path))
    rep = Report()
    by_page: dict[int, list[dict]] = {}
    for d in decisions:
        why = _validate(d, items)
        if why:
            rep.refused.append({"id": d.get("id"), "reason": why})
            continue
        by_page.setdefault(items[d["id"]]["file"], []).append(d)
    for file in sorted(by_page):
        md_path = root / "pages" / f"p{file:03d}.md"
        fl_path = root / "pages" / f"p{file:03d}.flags.json"
        if not md_path.is_file():
            for d in by_page[file]:
                rep.refused.append({"id": d["id"], "reason": f"{md_path.name} not found"})
            continue
        md = md_path.read_text("utf-8")
        flags = json.loads(fl_path.read_text("utf-8")) if fl_path.is_file() else []
        changed = False
        for d in by_page[file]:
            rec = _record(d)
            if rec in flags:
                rep.already.append(d["id"])
                continue
            it = cur = items[d["id"]]
            if it.get("kind") == "inline_missing":
                new_md, why = _apply_runs(d, md)
            elif it.get("spans") is not None:
                new_md, why = _apply_grouped(d, it, md, flags)
            else:
                # a display an applied replace changed is found by its new LaTeX
                done = [f["latex"] for f in flags if f.get("type") == "reviewed"
                        and f.get("id") == d["id"] and f.get("action") == "replace"
                        and f.get("latex")]
                cur = dict(it, latex_primary=done[-1].strip()) if done and \
                    it.get("latex_primary") is not None else it
                new_md, why = _apply_one(d, cur, md)
            if why:
                rep.refused.append({"id": d["id"], "reason": why})
                continue
            md = new_md
            if d["action"] == "flag":
                # cur: the display as an earlier replace of the same id left it
                flag = {"type": d["flag_type"], "eq": it.get("tag") or d["id"],
                        "latex": cur.get("latex_primary"), "note": d.get("note", "")}
                # a printed error a numeric check found: the page's mathematics is wrong
                subtype = d.get("subtype") or (
                    "math_error" if d["flag_type"] == "possible_source_typo"
                    and it.get("identity") else None)
                if subtype:
                    flag["subtype"] = subtype
                if it.get("latex_primary") is None:
                    flag.pop("latex")
                flags.insert(len(flags) - 1 if flags and flags[-1].get("type") == "summary"
                             else len(flags), flag)
            flags.insert(len(flags) - 1 if flags and flags[-1].get("type") == "summary"
                         else len(flags), rec)
            rep.applied.append({"id": d["id"], "action": d["action"], "page": file})
            changed = True
        if changed and not dry_run:
            write_atomic(md_path, md)
            write_atomic(fl_path, json.dumps(flags, ensure_ascii=False, indent=1) + "\n")
            rep.pages.append(file)
    if rep.pages and not dry_run:
        rebuild_text(root)
    return rep


def _apply_one(d: dict, it: dict, md: str) -> tuple[str, str | None]:
    latex = it.get("latex_primary")
    if latex is None:
        return _apply_missing(d, md)
    is_inline = it.get("kind") == "inline"
    where = _inline_span(latex, md) if is_inline else _display_block(latex, md)
    if where is None:
        return md, "latex_primary is no longer in the page"
    a, b = where
    if d["action"] == "replace" and not is_inline:
        why = _structure_refusal(latex, d)
        if why:
            return md, why
    if d["action"] == "replace":
        new = d["latex"].strip()
        md, b = md[:a] + new + md[b:], a + len(new)
    if d.get("tag") is not None and not is_inline:  # set the display's \tag; "" removes it
        line = f"\n\\tag{{{d['tag']}}}" if d["tag"] else ""
        old = TAG_LINE.match(md, b)
        md = md[:b] + line + md[old.end() if old else b:]
    return md, None


def _apply_missing(d: dict, md: str) -> tuple[str, str | None]:
    """A display the standard tier lacks (no latex_primary): a replace writes it in after
    the page text ``after``, or in place of the page text ``page_text`` (what the engine
    made of it: a broken display, an inline span); either must occur once in the page."""
    if d["action"] != "replace":
        return md, None
    anchor = d.get("page_text") or d.get("after")
    if not isinstance(anchor, str) or not anchor:
        return md, "a missing display needs 'after' or 'page_text'"
    why = _structure_refusal(None, d)
    if why:
        return md, why
    n = md.count(anchor)
    if n != 1:
        return md, ("anchor text not found in the page" if n == 0
                    else "anchor text is not unique in the page")
    a = md.index(anchor)
    b = a + len(anchor)
    if not d.get("page_text"):
        a = b
    block = "$$\n" + d["latex"].strip() + (f"\n\\tag{{{d['tag']}}}" if d.get("tag") else "") + "\n$$"
    before, rest = md[:a].rstrip(" \n"), md[b:].lstrip(" \n")
    return before + "\n\n" + block + ("\n\n" + rest if rest else "\n"), None


def _apply_grouped(d: dict, it: dict, md: str, flags: list) -> tuple[str, str | None]:
    primaries = {s.get("latex_primary") for s in it.get("spans", [])}
    if d["action"] != "replace":
        # spans an applied replace of this item changed are no longer in the page
        replaced = {s.get("latex_primary") for f in flags if f.get("type") == "reviewed"
                    and f.get("id") == d["id"] and f.get("action") == "replace"
                    for s in f.get("spans") or []}
        missing = [p for p in primaries - replaced if _inline_span(p, md) is None]
        return md, ("latex_primary is no longer in the page" if missing else None)
    # a span repeated in the page is placed by its occurrence (1-based, in page order);
    # every place is found in the page as it stands before any span is replaced
    edits = {}
    for s in d["spans"]:
        p = s["latex_primary"]
        if p not in primaries:
            return md, f"span {p!r} is not in the item"
        where, i = [], md.find("\\(" + p + "\\)")
        while i >= 0:
            where.append(i + 2)
            i = md.find("\\(" + p + "\\)", i + 1)
        if not where:
            return md, "latex_primary is no longer in the page"
        occ = s.get("occurrence")
        if occ is None and len(where) > 1:
            return md, f"span {p!r} occurs {len(where)} times in the page: give its occurrence"
        occ = 1 if occ is None else occ
        if not isinstance(occ, int) or not 1 <= occ <= len(where) or where[occ - 1] in edits:
            return md, f"span {p!r} has no occurrence {occ} in the page"
        edits[where[occ - 1]] = (len(p), s["latex"].strip())
    for a in sorted(edits, reverse=True):
        n, new = edits[a]
        md = md[:a] + new + md[a + n:]
    return md, None


def _apply_runs(d: dict, md: str) -> tuple[str, str | None]:
    """An inline_missing replace: each run's page text (in the prose, never inside math)
    becomes inline math; a text that occurs more than once needs its occurrence."""
    from gpdf.pipeline.mathocr.flags import prose_occurrences

    if d["action"] != "replace":
        return md, None
    edits = {}
    for r in d["runs"]:
        where = prose_occurrences(r["text"], md)
        if not where:
            return md, f"run {r['text']!r} is not in the page prose"
        occ = r.get("occurrence")
        if occ is None and len(where) > 1:
            return md, (f"run {r['text']!r} occurs {len(where)} times in the page prose: "
                        "give its occurrence")
        occ = 1 if occ is None else occ
        if not isinstance(occ, int) or not 1 <= occ <= len(where) or where[occ - 1] in edits:
            return md, f"run {r['text']!r} has no occurrence {occ} in the page prose"
        edits[where[occ - 1]] = (len(r["text"]), "\\(" + r["latex"].strip() + "\\)")
    for a in sorted(edits, reverse=True):
        n, new = edits[a]
        md = md[:a] + new + md[a + n:]
    return md, None


def rebuild_text(root: Path) -> None:
    pages = sorted((Path(root) / "pages").glob("p[0-9][0-9][0-9].md"))
    write_atomic(Path(root) / "text.md", "\n".join(p.read_text("utf-8") for p in pages))


def report(rep: Report) -> str:
    lines = [f"applied {len(rep.applied)} decision(s) on {len(rep.pages)} page(s)"
             + (f"; {len(rep.already)} applied earlier" if rep.already else "")]
    for r in rep.refused:
        lines.append(f"refused {r['id']}: {r['reason']}")
    return "\n".join(lines)
