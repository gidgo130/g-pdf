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
A page inline item is decided per span: ``accept --span`` / ``replace --span`` add that span
to the key's decision and keep the spans already decided; ``--withdraw --span`` removes one.
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


class AmbiguousSpan(DecideError):
    """A replace, flag or withdraw names a span whose LaTeX the page holds more than once, with
    no occurrence: nothing is changed, the reviewer adds --occurrence."""

    def __init__(self, key: str, latex: str, count: int, page_count: int | None = None):
        self.key, self.latex, self.count, self.page_count = key, latex, count, page_count
        where = f"{count} times in the item" + (f", {page_count} in the page" if page_count else "")
        super().__init__(f"ambiguous_span: {key}: span {latex!r} occurs {where}: give its "
                         "--occurrence N from its occurrence_candidates")


def _count(it: dict, lp: str) -> tuple[int, int | None]:
    """(times the item names the span's LaTeX, times the page holds it as the pack found it)."""
    same = [x for x in it.get("spans", []) if x["latex_primary"] == lp]
    if any(x.get("occurrence") is not None for x in same):
        return len(same), 1
    cand = next((x.get("occurrence_candidates") for x in same if x.get("occurrence_candidates")), None)
    return len(same), (len(cand) if cand else None)


def _places(it: dict, lp: str) -> list[int] | None:
    """Every occurrence of a span's LaTeX in the page, when the item names each of them (so the
    item's spans of that LaTeX are those occurrences); None when the item holds fewer."""
    k, n = _count(it, lp)
    return list(range(1, n + 1)) if n is not None and n == k else None


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
           occurrence: int | list[int] | None = None, structure_change: bool = False,
           span: str | list[str] | None = None) -> dict:
    """Check one decision against its item and write it; returns the decision written.
    ``span`` and ``occurrence`` may be lists: a flag of a page inline item names each
    flagged span (``occurrence`` one per span, or one for a single span)."""
    batch = Path(batch).resolve()           # "." would give an empty batch name
    items = _items(batch)
    order = {it["key"]: n for n, it in enumerate(items)}
    if key not in order:
        raise DecideError(f"unknown key {key!r}: use an item's \"key\" from items.jsonl")
    it = items[order[key]]
    if action not in ACTIONS:
        raise DecideError(f"action must be one of {', '.join(ACTIONS)}")
    spans = [span] if isinstance(span, str) else list(span or [])
    occs = [occurrence] if isinstance(occurrence, int) else list(occurrence or [])
    if spans and not (it["kind"] == "inline" and action in ("accept", "replace", "flag")):
        raise DecideError("--span goes with an accept, replace or flag of a page inline item")
    if action == "replace":
        if len(spans) > 1 or len(occs) > 1:
            raise DecideError("a replace names one --span (and one --occurrence)")
        span, occurrence = (spans or [None])[0], (occs or [None])[0]
    d: dict = {"key": key, "id": it["id"], "action": action}
    if action == "flag":
        if it["kind"] in ("inline", "inline_span"):
            d.update(_flag_spans(it, spans, occs))
        elif occs:
            raise DecideError("--occurrence goes with an inline item")
        if flag_type not in FLAG_TYPES:
            raise DecideError(f"a flag needs --flag-type, one of {', '.join(FLAG_TYPES)}")
        if not (note or "").strip():
            raise DecideError("a flag needs --note (what looks wrong, or what can't be read)")
        if subtype is not None and (flag_type != "possible_source_typo" or subtype not in SUBTYPES):
            raise DecideError(f"--subtype goes with possible_source_typo: {', '.join(SUBTYPES)}")
        d.update(flag_type=flag_type, **({"subtype": subtype} if subtype else {}), note=note.strip(),
                 reason=(reason or note).strip())
    elif action == "accept":
        if spans:                             # one page span: its siblings keep their decisions
            d["spans"] = [{**x, "latex": x["latex_primary"]}
                          for x in _flag_spans(it, spans, occs, accept=True)["spans"]]
        elif occs:
            raise DecideError("--occurrence needs the --span it belongs to")
        elif it["kind"] == "inline_span":
            d["spans"] = [{"latex_primary": it["latex_primary"], "latex": it["latex_primary"],
                           "occurrence": it.get("occurrence")}]
        elif it["kind"] == "inline":          # a page's grouped spans: all of them accepted
            d["spans"] = [{"latex_primary": x["latex_primary"], "latex": x["latex_primary"],
                           "occurrence": x.get("occurrence")} for x in it.get("spans", [])]
    else:
        if latex is None or not latex.strip():
            raise DecideError("a replace needs the LaTeX: --latex-file FILE, or on stdin")
        if not (reason or "").strip():
            raise DecideError("a replace needs --reason")
        d.update(_replace(it, latex.strip(), page_text, after, run_text, occurrence,
                          structure_change, span))
    if tag is not None:
        if action == "flag" or it["kind"] in ("inline_span", "inline_missing"):
            raise DecideError("--tag goes with an accept or replace of a display")
        d["tag"] = tag.strip().strip("()")
    if reason and "reason" not in d:
        d["reason"] = reason.strip()
    if structure_change:
        d["structure_change"] = True
    return _write(batch, order, d, it, partial=bool(spans) and action == "accept")


def _flag_spans(it: dict, spans: list[str], occs: list[int], accept: bool = False) -> dict:
    """The "spans" of a flag on an inline item: an inline_span item's own span; for a page
    inline item, the spans named (none: a page-level flag), each with its occurrence. An
    ``accept`` of a span the item names more than once, with no occurrence, names every one
    of its occurrences (marked ``undecided``: the merge keeps those already decided)."""
    if it["kind"] == "inline_span":
        if len(occs) > 1:
            raise DecideError("an inline span item takes one --occurrence")
        occ = (occs or [None])[0] or it.get("occurrence")
        if occ is None:
            raise DecideError("this span occurs several times in the page: pass --occurrence N "
                              "from its occurrence_candidates")
        return {"spans": [{"latex_primary": it["latex_primary"], "occurrence": occ}]}
    if not spans:
        if occs:
            raise DecideError("--occurrence needs the --span it belongs to")
        return {}
    if occs and len(occs) != len(spans):
        raise DecideError(f"{len(spans)} --span and {len(occs)} --occurrence: give one "
                          "--occurrence per --span, in the same order")
    out = []
    for n, s in enumerate(spans):
        hits = [x for x in it.get("spans", []) if _norm(x["latex_primary"]) == _norm(s)]
        if not hits:
            raise DecideError(f"--span {s!r} is not one of the item's spans (copy its latex_primary)")
        lp = hits[0]["latex_primary"]
        occ = occs[n] if occs else hits[0].get("occurrence")
        if occ is None:
            places = _places(it, lp) if accept and len(hits) > 1 else None
            if places is None:
                raise AmbiguousSpan(it["key"], lp, *_count(it, lp))
            out += [{"latex_primary": lp, "occurrence": o, "undecided": True} for o in places]
            continue
        out.append({"latex_primary": lp, "occurrence": occ})
    return {"spans": out}


def _replace(it, latex, page_text, after, run_text, occurrence, structure_change,
             span=None) -> dict:
    kind, primary = it["kind"], it.get("latex_primary")
    if kind == "inline":                      # one span of a page's grouped inline item
        if span is None or not span.strip():
            raise DecideError("a page inline item holds several spans: name the one to replace "
                              "with --span (its latex_primary, or --span-file)")
        hits = [x for x in it.get("spans", []) if _norm(x["latex_primary"]) == _norm(span)]
        if not hits:
            raise DecideError("--span is not one of the item's spans (copy its latex_primary)")
        primary = hits[0]["latex_primary"]
        occ = occurrence or hits[0].get("occurrence")
        if occ is None:
            raise AmbiguousSpan(it["key"], primary, *_count(it, primary))
        if _norm(latex) == _norm(primary):
            raise DecideError("the LaTeX is the same as the span: nothing to replace")
        if not structure_change:
            problems = structure_problems(primary, latex)
            if problems:
                raise DecideError("structure lost: " + "; ".join(problems))
        return {"spans": [{"latex_primary": primary, "latex": latex, "occurrence": occ}]}
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


def _action_of(spans: list[dict]) -> str:
    """A page item's decision: a replace when any span's LaTeX changed, else an accept."""
    return "replace" if any(x.get("latex") != x.get("latex_primary") for x in spans) else "accept"


def _put(out: list[dict], x: dict) -> None:
    """Span decision ``x`` takes the place of the one for its occurrence, or of one of its
    LaTeX whose occurrence is not known; else it is added."""
    same = [i for i, y in enumerate(out) if y["latex_primary"] == x["latex_primary"]]
    at = ([i for i in same if out[i].get("occurrence") == x["occurrence"]]
          or [i for i in same if out[i].get("occurrence") is None])
    if at:
        out[at[0]] = x
    else:
        out.append(x)


def _fill(out: list[dict], it: dict, lp: str) -> int:
    """Add the not-yet-decided occurrences of span ``lp`` as unchanged (equal LaTeX, equal
    verdict); returns how many were added."""
    places = _places(it, lp) or []
    known = {y.get("occurrence") for y in out if y["latex_primary"] == lp}
    room = len(places) - sum(y["latex_primary"] == lp for y in out)
    new = [o for o in places if o not in known][:max(room, 0)]
    out += [{"latex_primary": lp, "latex": lp, "occurrence": o} for o in new]
    return len(new)


def _merge(it: dict, d: dict, prev: dict | None, partial: bool = False) -> tuple[dict, list]:
    """A page inline item's decisions accumulate their spans: every span an earlier accept or
    replace of the key named stays; a replace also takes the item's other spans whose places
    are known as unchanged, so the item counts as reviewed (a ``partial`` accept of named spans
    takes none). A repeated span's places are known when the item names every occurrence the
    page holds; otherwise it is returned as ambiguous, never dropped silently. An accept of a
    repeated span without occurrence takes its undecided occurrences. An inline_missing item's
    replaces accumulate their runs. Returns (decision, ambiguous spans)."""
    main = prev if prev and prev.get("action") != "flag" else None
    kept = [dict(x) for x in (main or {}).get("spans") or []]
    if partial and it["kind"] == "inline":
        out = kept
        for x in d["spans"]:
            if not x.get("undecided"):
                _put(out, dict(x))
        for lp in dict.fromkeys(x["latex_primary"] for x in d["spans"] if x.get("undecided")):
            if not _fill(out, it, lp):
                raise DecideError(f"every occurrence of span {lp!r} is already decided: give "
                                  "--occurrence N to change one")
        return {**(main or {}), **d, "action": _action_of(out), "spans": out}, []
    if d.get("action") != "replace":
        return d, []
    if it["kind"] == "inline":
        out, ambiguous = kept, []
        for x in d["spans"]:
            _put(out, dict(x))
        for lp in dict.fromkeys(x["latex_primary"] for x in it.get("spans", [])):
            if _places(it, lp) is not None:
                _fill(out, it, lp)
            elif not any(y["latex_primary"] == lp for y in out):
                k, n = _count(it, lp)
                ambiguous.append({"key": it["key"], "latex": lp, "count": k, "page_count": n})
        return {**d, "spans": out}, ambiguous
    if it["kind"] == "inline_missing" and prev and prev.get("action") == "replace":
        runs = {(r["text"], r.get("occurrence")): r for r in prev.get("runs") or []}
        for r in d["runs"]:
            runs[(r["text"], r.get("occurrence"))] = r
        return {**d, "runs": list(runs.values())}, []
    return d, []


def _write(batch: Path, order: dict, d: dict, it: dict | None = None, partial: bool = False) -> dict:
    path = decisions_path(batch)
    path.parent.mkdir(parents=True, exist_ok=True)
    old = ([json.loads(s) for s in path.read_text("utf-8").splitlines() if s.strip()]
           if path.is_file() else [])
    ambiguous = []
    if it is not None:
        prev = next((x for x in old if (x.get("key"), _slot(x)) == (d["key"], _slot(d))), None)
        d, ambiguous = _merge(it, d, prev, partial)
    keep = [x for x in old if (x.get("key"), _slot(x)) != (d["key"], _slot(d))] + [d]
    keep.sort(key=lambda x: (order.get(x.get("key"), len(order)), _slot(x) == "flag"))
    write_atomic(path, "".join(json.dumps(x, ensure_ascii=False) + "\n" for x in keep))
    return {**d, "ambiguous_spans": ambiguous} if ambiguous else d


WITHDRAW = ("all", "accept", "replace", "flag")


def withdraw(batch, key: str, what: str = "all", span: str | None = None,
             occurrence: int | None = None) -> list[dict]:
    """Remove KEY's decisions of one kind (accept, replace, flag) or all of them; returns
    the decisions removed. With ``span`` (and ``occurrence``): only that span of a page
    inline item's decisions; a decision left with no span is removed, otherwise it is kept
    with the other spans. Nothing to remove is a refusal, so a typo can't pass silently."""
    batch = Path(batch).resolve()
    items = _items(batch)
    order = {it["key"]: n for n, it in enumerate(items)}
    if key not in order:
        raise DecideError(f"unknown key {key!r}: use an item's \"key\" from items.jsonl")
    if what not in WITHDRAW:
        raise DecideError(f"--withdraw takes one of {', '.join(WITHDRAW)}")
    path = decisions_path(batch)
    old = ([json.loads(s) for s in path.read_text("utf-8").splitlines() if s.strip()]
           if path.is_file() else [])
    gone = [x for x in old if x.get("key") == key and what in ("all", x.get("action"))]
    if span is None:
        if occurrence is not None:
            raise DecideError("--occurrence needs the --span it belongs to")
        if not gone:
            raise DecideError(f"no {'decision' if what == 'all' else what} of {key} to withdraw")
        write_atomic(path, "".join(json.dumps(x, ensure_ascii=False) + "\n"
                                   for x in old if not any(x is g for g in gone)))
        return gone
    it = items[order[key]]
    if it["kind"] != "inline":
        raise DecideError("--span goes with a page inline item")
    lps = [x["latex_primary"] for x in it.get("spans", []) if _norm(x["latex_primary"]) == _norm(span)]
    if occurrence is None and len(lps) > 1:
        raise AmbiguousSpan(key, lps[0], *_count(it, lps[0]))
    removed, new = [], []
    for x in old:
        if not any(x is g for g in gone) or not x.get("spans"):
            new.append(x)
            continue
        hit = [s for s in x["spans"] if _norm(s["latex_primary"]) == _norm(span)
               and (occurrence is None or s.get("occurrence") == occurrence)]
        if len(hit) > 1:
            raise DecideError(f"{len(hit)} spans match: pass --occurrence N")
        if not hit:
            new.append(x)
            continue
        left = [s for s in x["spans"] if s is not hit[0]]
        removed.append({**x, "spans": hit})
        if left:
            new.append({**x, "spans": left,
                        **({"action": _action_of(left)} if x.get("action") != "flag" else {})})
    if not removed:
        raise DecideError(f"no span {span!r} of {key} to withdraw")
    write_atomic(path, "".join(json.dumps(x, ensure_ascii=False) + "\n" for x in new))
    return removed


def status(batch) -> dict:
    """{total, decided, missing: [keys without a decision]} for one batch."""
    batch = Path(batch).resolve()
    keys = [it["key"] for it in _items(batch)]
    path = decisions_path(batch)
    done = ({json.loads(s).get("key") for s in path.read_text("utf-8").splitlines() if s.strip()}
            if path.is_file() else set())
    return {"total": len(keys), "decided": sum(k in done for k in keys),
            "missing": [k for k in keys if k not in done]}
