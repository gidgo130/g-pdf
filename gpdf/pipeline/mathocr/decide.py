# SPDX-License-Identifier: AGPL-3.0-or-later
"""gpdf-cli math-decide: write one review decision for one packed item.

A reviewer who writes the decisions JSON by hand has to double every backslash, and a
`\\\\` row break then easily comes out as `\\ ` (Batch B: three displays in one book). This
helper takes the LaTeX raw, from a file or stdin, checks it against the item
(math-apply-review's structure rules, the inline span's occurrence, the missing display's
place) and writes the decision with ``json.dumps`` to the batch's decisions file:
``<review dir>/decisions/<batch>.jsonl``. Each decision carries the item's ``key``. One
accept-or-replace and one flag are kept per key: deciding a key again replaces the
earlier decision of that kind, so running the same call twice changes nothing.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from gpdf.pipeline.manuals import write_atomic
from gpdf.pipeline.mathocr.review import ACTIONS, FLAG_TYPES, structure_problems

SUBTYPES = ("math_error", "notation")


class DecideError(Exception):
    """The decision cannot be written; the message is for the reviewer."""


def _items(batch: Path) -> list[dict]:
    path = batch / "items.jsonl"
    if not path.is_file():
        raise DecideError(f"{path} not found: is {batch} a math-review-pack batch folder?")
    return [json.loads(s) for s in path.read_text("utf-8").splitlines() if s.strip()]


def decisions_path(batch: Path) -> Path:
    return batch.parent / "decisions" / f"{batch.name}.jsonl"


def _norm(s: str | None) -> str:
    return re.sub(r"\s+", "", s or "")


def _slot(d: dict) -> str:
    return "flag" if d.get("action") == "flag" else "main"


def decide(batch, key: str, action: str, *, latex: str | None = None,
           reason: str | None = None, note: str | None = None, flag_type: str | None = None,
           subtype: str | None = None, tag: str | None = None, page_text: str | None = None,
           after: str | None = None, run_text: str | None = None,
           occurrence: int | None = None, structure_change: bool = False) -> dict:
    """Check one decision against its item and write it; returns the decision written."""
    batch = Path(batch).resolve()           # "." would give an empty batch name
    items = _items(batch)
    order = {it["key"]: n for n, it in enumerate(items)}
    if key not in order:
        raise DecideError(f"unknown key {key!r}: use an item's \"key\" from items.jsonl")
    it = items[order[key]]
    if action not in ACTIONS:
        raise DecideError(f"action must be one of {', '.join(ACTIONS)}")
    d: dict = {"key": key, "id": it["id"], "action": action}
    if action == "flag":
        if flag_type not in FLAG_TYPES:
            raise DecideError(f"a flag needs --flag-type, one of {', '.join(FLAG_TYPES)}")
        if not (note or "").strip():
            raise DecideError("a flag needs --note (what looks wrong, or what can't be read)")
        if subtype is not None and (flag_type != "possible_source_typo" or subtype not in SUBTYPES):
            raise DecideError(f"--subtype goes with possible_source_typo: {', '.join(SUBTYPES)}")
        d.update(flag_type=flag_type, **({"subtype": subtype} if subtype else {}), note=note.strip(),
                 reason=(reason or note).strip())
    elif action == "accept":
        if it["kind"] == "inline_span":
            d["spans"] = [{"latex_primary": it["latex_primary"], "latex": it["latex_primary"],
                           "occurrence": it.get("occurrence")}]
    else:
        if latex is None or not latex.strip():
            raise DecideError("a replace needs the LaTeX: --latex-file FILE, or on stdin")
        if not (reason or "").strip():
            raise DecideError("a replace needs --reason")
        d.update(_replace(it, latex.strip(), page_text, after, run_text, occurrence,
                          structure_change))
    if tag is not None:
        if action == "flag" or it["kind"] in ("inline_span", "inline_missing"):
            raise DecideError("--tag goes with an accept or replace of a display")
        d["tag"] = tag.strip().strip("()")
    if reason and "reason" not in d:
        d["reason"] = reason.strip()
    if structure_change:
        d["structure_change"] = True
    _write(batch, order, d)
    return d


def _replace(it, latex, page_text, after, run_text, occurrence, structure_change) -> dict:
    kind, primary = it["kind"], it.get("latex_primary")
    if kind == "inline_missing":
        if not run_text:
            raise DecideError("an inline_missing replace needs --run-text (the exact page text)")
        runs = [r for r in it.get("runs", []) if run_text in (r.get("text") or "")]
        if not runs:
            raise DecideError("--run-text is not part of any of the item's runs")
        occ = occurrence or runs[0].get("occurrence")
        if occ is None:
            raise DecideError("this run's occurrence is not known: pass --occurrence N from "
                              "its occurrence_candidates")
        return {"runs": [{"text": run_text, "latex": latex, "occurrence": occ}]}
    if primary is not None and _norm(latex) == _norm(primary):
        raise DecideError("the LaTeX is the same as latex_primary: decide accept")
    if not structure_change:
        problems = structure_problems(primary, latex)
        if problems:
            raise DecideError("structure lost: " + "; ".join(problems) + " (if the page really "
                              "prints it so, add --structure-change and a --reason)")
    if kind == "inline_span":
        occ = occurrence or it.get("occurrence")
        if occ is None:
            raise DecideError("the span's occurrence is not known: pass --occurrence N from "
                              "its occurrence_candidates")
        return {"spans": [{"latex_primary": primary, "latex": latex, "occurrence": occ}]}
    out: dict = {"latex": latex}
    if kind == "missing_display":
        if (page_text is None) == (after is None):
            raise DecideError("a missing display needs exactly one of --page-text or --after")
        piece = page_text if page_text is not None else after
        if (it.get("page_md") or "").count(piece) != 1:
            raise DecideError("that piece is not in page_md exactly once: copy an exact, "
                              "unique piece of it")
        out["page_text" if page_text is not None else "after"] = piece
    return out


def _write(batch: Path, order: dict, d: dict) -> None:
    path = decisions_path(batch)
    path.parent.mkdir(parents=True, exist_ok=True)
    old = ([json.loads(s) for s in path.read_text("utf-8").splitlines() if s.strip()]
           if path.is_file() else [])
    keep = [x for x in old if (x.get("key"), _slot(x)) != (d["key"], _slot(d))] + [d]
    keep.sort(key=lambda x: (order.get(x.get("key"), len(order)), _slot(x) == "flag"))
    write_atomic(path, "".join(json.dumps(x, ensure_ascii=False) + "\n" for x in keep))


def status(batch) -> dict:
    """{total, decided, missing: [keys without a decision]} for one batch."""
    batch = Path(batch).resolve()
    keys = [it["key"] for it in _items(batch)]
    path = decisions_path(batch)
    done = ({json.loads(s).get("key") for s in path.read_text("utf-8").splitlines() if s.strip()}
            if path.is_file() else set())
    return {"total": len(keys), "decided": sum(k in done for k in keys),
            "missing": [k for k in keys if k not in done]}
