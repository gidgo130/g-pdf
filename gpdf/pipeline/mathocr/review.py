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
decisions still go through. Pages without a decision are never touched. text.md is
rebuilt from the page files.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from gpdf.pipeline.manuals import write_atomic

ACTIONS = ("accept", "replace", "flag")
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
    text = Path(path).read_text("utf-8")
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
    for k in ("latex", "flag_type", "note", "spans"):
        if decision.get(k) is not None:
            rec[k] = decision[k]
    return rec


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
        if items[d["id"]].get("spans") is not None:
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
            it = items[d["id"]]
            if it.get("spans") is not None:
                new_md, why = _apply_grouped(d, it, md)
            else:
                new_md, why = _apply_one(d, it, md)
            if why:
                rep.refused.append({"id": d["id"], "reason": why})
                continue
            md = new_md
            if d["action"] == "flag":
                flag = {"type": d["flag_type"], "eq": it.get("tag") or d["id"],
                        "latex": it.get("latex_primary"), "note": d.get("note", "")}
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
        return md, "item has no latex_primary"
    is_inline = it.get("kind") == "inline"
    where = _inline_span(latex, md) if is_inline else _display_block(latex, md)
    if where is None:
        return md, "latex_primary is no longer in the page"
    if d["action"] == "replace":
        a, b = where
        md = md[:a] + d["latex"].strip() + md[b:]
    return md, None


def _apply_grouped(d: dict, it: dict, md: str) -> tuple[str, str | None]:
    primaries = {s.get("latex_primary") for s in it.get("spans", [])}
    if d["action"] != "replace":
        missing = [p for p in primaries if _inline_span(p, md) is None]
        return md, ("latex_primary is no longer in the page" if missing else None)
    for s in d["spans"]:
        if s["latex_primary"] not in primaries:
            return md, f"span {s['latex_primary']!r} is not in the item"
        where = _inline_span(s["latex_primary"], md)
        if where is None:
            return md, "latex_primary is no longer in the page"
        a, b = where
        md = md[:a] + s["latex"].strip() + md[b:]
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
