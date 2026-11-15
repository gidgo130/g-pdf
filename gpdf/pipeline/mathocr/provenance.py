# SPDX-License-Identifier: AGPL-3.0-or-later
"""gpdf-cli math-provenance: where each formula of a (reviewed) math-export came from.

formulas.jsonl has one line per display and inline span of the final pages, in page
order: ``id`` (pNNN-dKK / pNNN-iKK, the position in the final page), ``file``,
``printed``, ``kind`` (display | inline), ``tag``, ``status``, ``reviewer`` (the model, for
a reviewed formula), ``flags`` (type and subtype of the flags on it), ``notes`` (the kinds
of an unreviewed queue item) and ``review_item`` (the queue id). No formulas, unless
``flag_latex``: then each flag entry also quotes the formula as the final page has it
(``latex``: the flag's own LaTeX when it is part of the display, else the display; an inline
flag's span).

status, the first that applies:
    flagged           a reviewer's flag (possible_source_typo, copy_corrupt, ...)
    replaced          a reviewer's replace
    reviewed_accept   a reviewer's accept
    unreviewed        in the review queue but not reviewed (a style item, an inline span
                      outside the sample)
    identity_checked  not queued; a numeric identity check passed
    engines_agree     not queued: every engine read it the same way

The method block written into meta.json: engines and versions, OCR mode, input copy and
dpi, the reviewer model and the coverage (displays and inline reviewed, numeric checks).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from gpdf.pipeline.manuals import write_atomic
from gpdf.pipeline.mathocr import latex as L
from gpdf.pipeline.mathocr.review import FLAG_TYPES

REVIEWED = ("flagged", "replaced", "reviewed_accept")
STATUSES = REVIEWED + ("unreviewed", "identity_checked", "engines_agree")
MARKER_RE = re.compile(r'<!--\s*page\s+printed="([^"]*)"')


class ProvenanceError(Exception):
    """The export folder cannot be used; the message is for the user."""


def _jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(s) for s in path.read_text("utf-8").splitlines() if s.strip()]


def _key(latex: str | None) -> str:
    return " ".join((latex or "").split())


def _sampled_accepts(review_dirs) -> dict[int, list[tuple[str, str]]]:
    """{file: [(span LaTeX, item id)]}: sampled inline spans whose page item got an accept
    that names no spans, and that no decision with spans covers (a reviewer who dropped
    the template's "spans" line)."""
    out: dict[int, list] = {}
    for rd in review_dirs or []:
        rd = Path(rd)
        sampled = []
        for p in sorted(rd.glob("batch*/items.jsonl")):
            sampled += [x for x in _jsonl(p) if x.get("kind") == "inline_span"]
        blanket, covered = set(), set()
        for p in sorted((rd / "decisions").glob("*.jsonl")):
            for d in _jsonl(p):
                if d.get("spans"):
                    covered |= {(d["id"], _key(s.get("latex_primary"))) for s in d["spans"]}
                elif d.get("action") == "accept":
                    blanket.add(d["id"])
        for x in sampled:
            k = _key(x.get("latex_primary"))
            if x["id"] in blanket and (x["id"], k) not in covered:
                out.setdefault(int(x["file"]), []).append((k, x["id"]))
    return out


EQ_NUMBER = re.compile(r"\d+\.\d+[a-z']*")
# an eq that is nothing but equation numbers: "140", "(140)", "140a", "(140)/(141)", "3.1, 3.2"
_ONE_TAG = r"\(?\s*\d+(?:\.\d+)*[a-z']*\s*\)?"
TAG_STRING = re.compile(rf"\s*{_ONE_TAG}(?:\s*(?:/|,|;|&|and)\s*{_ONE_TAG})*\s*")
TAG_NUMBER = re.compile(r"\d+(?:\.\d+)*[a-z']*")


def _nospace(s: str | None) -> str:
    return re.sub(r"\s+", "", s or "")


def _eq_numbers(eq) -> tuple[list[str], bool]:
    """(the equation numbers a flag's eq names, whether the eq is a tag string). Plain integers
    count only in a tag string: an item id such as p073-d02 or p151-inline names no tag."""
    eq = str(eq or "")
    if TAG_STRING.fullmatch(eq):
        return list(dict.fromkeys(TAG_NUMBER.findall(eq))), True
    return list(dict.fromkeys(EQ_NUMBER.findall(eq))), False


def _attach_flags(page_flags: list, base: list, ambiguous: list | None = None) -> dict[int, list]:
    """{display index: flags}: a flag goes to the display whose LaTeX (or the LaTeX its review
    item had) equals the flag's; else to the display whose \\tag the flag's eq names (a display
    changed after it was flagged); else to the first display containing the flag's LaTeX (a
    flag quoting part of a display). An inline flag (eq ending in "inline") stays unattached.
    An eq that is a tag string naming several numbers ("(140)/(141)") attaches only to the one
    named display containing the flag's LaTeX; with none or several, the flag stays unattached
    and goes to ``ambiguous``."""
    out: dict[int, list] = {}
    for f in page_flags:
        if str(f.get("eq", "")).endswith("inline"):
            continue
        fk = _key(f.get("latex"))
        hit = next((i for i, b in enumerate(base) if fk and fk in b[4]), None)
        if hit is None:
            nums, tag_string = _eq_numbers(f.get("eq"))
            named = [i for i, b in enumerate(base) if b[1] and b[1] in nums]
            if tag_string and len(nums) > 1:
                fn = _nospace(fk)
                holding = [i for i in named if fn and fn in _nospace(base[i][0])]
                if len(holding) != 1:
                    if ambiguous is not None:
                        ambiguous.append(f)
                    continue
                hit = holding[0]
            elif named:
                hit = named[0]
        if hit is None and len(_nospace(fk)) >= 3:
            hit = next((i for i, b in enumerate(base) if _nospace(fk) in _nospace(b[0])), None)
        if hit is not None:
            out.setdefault(hit, []).append(f)
    return out


def _flag_entry(f: dict, quote: str | None) -> dict:
    e = {x: f[x] for x in ("type", "subtype") if f.get(x)}
    if quote is not None:
        fk = _nospace(f.get("latex"))
        e["latex"] = f["latex"] if fk and fk in _nospace(quote) else quote
    return e


def _page_records(file: int, md: str, flags: list, items: list[dict], idents: list[dict],
                  reviewer: str | None, extra_accepts: list | None = None,
                  flag_latex: bool = False, ambiguous: list | None = None) -> list[dict]:
    m = MARKER_RE.search(md)
    printed = m.group(1) if m else None
    displays, inline, _ = L.extract(md)
    reviewed = [f for f in flags if f.get("type") == "reviewed"]
    by_id = {it["id"]: it for it in items}
    page_flags = [f for f in flags if f.get("type") in FLAG_TYPES]
    passed = {_key(r["latex"]) for r in idents if r.get("status") == "passed"}
    out = []

    # displays: the review state of each, then each flag attached to one display
    base, finals = [], []
    for body, trailing in displays:
        lat, tag = L.split_tag(body, trailing)
        finals.append(lat.strip())
        k = _key(lat)
        status, review_item = None, None
        for r in reviewed:
            it = by_id.get(r["id"])
            if it is None or it.get("kind") in ("inline", "inline_missing"):
                continue
            if r["action"] == "replace" and _key(r.get("latex")) == k:
                status, review_item = "replaced", r["id"]
            elif _key(it.get("latex_primary")) == k and r["action"] in ("accept", "flag"):
                status = status or ("reviewed_accept" if r["action"] == "accept" else None)
                review_item = r["id"]
        olds = {k} | {_key(by_id[review_item].get("latex_primary"))} if review_item else {k}
        base.append((k, tag, status, review_item, olds))
    amb: list = []
    attached = _attach_flags(page_flags, base, amb)
    if ambiguous is not None:
        ambiguous += [{"file": file, "eq": f.get("eq"),
                       **{x: f[x] for x in ("type", "subtype", "latex") if f.get(x)}} for f in amb]
    for n, (k, tag, status, review_item, olds) in enumerate(base, 1):
        notes, also_replaced = [], False
        fl = [_flag_entry(f, finals[n - 1] if flag_latex else None) for f in attached.get(n - 1, [])]
        if fl:
            also_replaced = status == "replaced"
            status = "flagged"
        if status is None:
            queued = [it for it in items if it.get("kind") not in ("inline", "inline_missing")
                      and _key(it.get("latex_primary")) == k]
            if queued:
                status, review_item = "unreviewed", queued[0]["id"]
                notes = sorted({it["kind"] for it in queued})
            elif k in passed:
                status = "identity_checked"
            else:
                status = "engines_agree"
        rec = {"id": f"p{file:03d}-d{n:02d}", "file": file, "printed": printed, "kind": "display",
               "tag": tag, "status": status,
               "reviewer": reviewer if status in REVIEWED else None, "flags": fl, "notes": notes}
        if also_replaced:
            rec["replaced"] = True
        if review_item:
            rec["review_item"] = review_item
        out.append(rec)

    # inline spans: matched by their LaTeX, each decision span used once
    replaced, accepted, queued = [], [], []
    for r in reviewed:
        it = by_id.get(r["id"])
        if it is None or it.get("kind") != "inline":
            continue
        for s in r.get("spans") or []:
            if r["action"] == "replace" and s.get("latex") != s.get("latex_primary"):
                replaced.append((_key(s.get("latex")), r["id"]))
            elif r["action"] in ("accept", "replace"):
                accepted.append((_key(s.get("latex_primary")), r["id"]))
    accepted += list(extra_accepts or [])
    for it in items:
        if it.get("kind") == "inline":
            for s in it.get("spans") or ([{"latex_primary": it["latex_primary"]}]
                                          if it.get("latex_primary") else []):
                queued.append((_key(s.get("latex_primary")), it["id"]))

    def take(pool, k):
        for i, (lk, rid) in enumerate(pool):
            if lk == k:
                pool.pop(i)
                return rid
        return None

    for n, span in enumerate(inline, 1):
        k = _key(span)
        fl = [_flag_entry(f, span if flag_latex else None) for f in page_flags
              if _key(f.get("latex")) == k and str(f.get("eq", "")).endswith("inline")]
        rid = take(replaced, k)
        status = "replaced" if rid else None
        if not rid:
            rid = take(accepted, k)
            status = "reviewed_accept" if rid else None
        q = take(queued, k)
        if fl:
            status = "flagged"
        if status is None:
            status, rid = ("unreviewed", q) if q else ("engines_agree", None)
        rec = {"id": f"p{file:03d}-i{n:02d}", "file": file, "printed": printed, "kind": "inline",
               "tag": None, "status": status,
               "reviewer": reviewer if status in REVIEWED else None, "flags": fl,
               "notes": ["inline"] if status == "unreviewed" else []}
        if rid or q:
            rec["review_item"] = rid or q
        out.append(rec)
    return out


PROSE_ONLY_NOTE = ("none (prose-only mode): every display and inline span is the engines' output, "
                   "not reviewed")


def _prose_only(recs: list[dict]) -> list[dict]:
    """Prose-only mode: every formula is unreviewed engine output, whatever the queue or the
    identity pass said about it."""
    for r in recs:
        r.update(status="unreviewed", reviewer=None, notes=["prose_only"])
        r.pop("review_item", None)
        r.pop("replaced", None)
    return recs


def write(export, out=None, reviewer: str | None = None, meta=None, review_dirs=None,
          flag_latex: bool = False, prose_only: bool = False) -> dict:
    root = Path(export)
    pages_dir = root / "pages"
    if not pages_dir.is_dir():
        raise ProvenanceError(f"{pages_dir} not found: is {root} a math-export folder?")
    out = Path(out) if out else root
    items = _jsonl(root / "review.jsonl")
    idents = _jsonl(root / "identities.jsonl")
    extra = _sampled_accepts(review_dirs)
    recs, ambiguous = [], []
    for p in sorted(pages_dir.glob("p[0-9][0-9][0-9].md")):
        file = int(p.stem[1:])
        fp = p.with_suffix(".flags.json")
        flags = json.loads(fp.read_text("utf-8")) if fp.is_file() else []
        if prose_only and any(f.get("type") in ("reviewed",) + tuple(FLAG_TYPES) for f in flags):
            raise ProvenanceError(f"{p.name} carries review decisions or flags: not a prose-only export")
        recs += _page_records(file, p.read_text("utf-8"), flags,
                              [it for it in items if int(it["file"]) == file],
                              [r for r in idents if int(r["file"]) == file], reviewer,
                              extra.get(file), flag_latex, ambiguous)
    if prose_only:
        recs = _prose_only(recs)
        reviewer = None
    write_atomic(out / "formulas.jsonl",
                 "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recs))
    by_status = {s: sum(1 for r in recs if r["status"] == s) for s in STATUSES}
    disp = [r for r in recs if r["kind"] == "display"]
    inl = [r for r in recs if r["kind"] == "inline"]

    def pct(a, b):
        return round(100.0 * a / b, 1) if b else None
    d_rev = sum(1 for r in disp if r["status"] in REVIEWED)
    i_rev = sum(1 for r in inl if r["status"] in REVIEWED)
    src_meta = json.loads((root / "meta.json").read_text("utf-8")) \
        if (root / "meta.json").is_file() else {}
    run = json.loads((root / "run.json").read_text("utf-8")) \
        if (root / "run.json").is_file() else {}
    src = src_meta.get("source", {})
    engines = {}
    if src_meta.get("engine", {}).get("version"):
        engines["mineru"] = src_meta["engine"]["version"]
    mk = src_meta.get("marker") or {}
    engines.update(mk.get("packages", {}))
    if mk.get("llama_build"):
        engines["llama.cpp"] = mk["llama_build"]
    opts = src_meta.get("options", {})
    ident = run.get("totals", {}).get("identities", {})
    method = {
        "engines": engines,
        "ocr_mode": opts.get("ocr_mode"),
        "dpi": src_meta.get("dpi"),
        "input": {"file": Path(src.get("path", "")).name or None, "sha256": src.get("sha256"),
                  "pages": opts.get("pages"),
                  **({"djvu": src_meta["djvu"]} if src_meta.get("djvu") else {})},
        "reviewer_model": reviewer,
        "coverage": {"displays": len(disp), "displays_reviewed": d_rev,
                     "displays_reviewed_pct": pct(d_rev, len(disp)),
                     "inline": len(inl), "inline_reviewed": i_rev,
                     "inline_reviewed_pct": pct(i_rev, len(inl)),
                     "numeric_checks": ident.get("checked", 0),
                     "numeric_checks_failed": ident.get("failed", 0)},
        "formulas_by_status": by_status,
    }
    if prose_only:
        method["prose_only"] = True
        method["review"] = PROSE_ONLY_NOTE
    meta_path = Path(meta) if meta else out / "meta.json"
    m = json.loads(meta_path.read_text("utf-8")) if meta_path.is_file() else {}
    m["method"] = method
    write_atomic(meta_path, json.dumps(m, ensure_ascii=False, indent=1) + "\n")
    return {"formulas": len(recs), "by_status": by_status, "method": method,
            "out": str(out / "formulas.jsonl"), "meta": str(meta_path),
            "ambiguous_tag": ambiguous}


def report(res: dict) -> str:
    c = res["method"]["coverage"]
    amb = res.get("ambiguous_tag") or []
    head = [f"{len(amb)} flags with an ambiguous tag left unattached (several numbers named, not one "
            "named display holds the flag's LaTeX): "
            + ", ".join(f"file {a['file']} eq {a['eq']!r}" for a in amb)] if amb else []
    return "\n".join(head + [
        f"{res['formulas']} formulas: " + ", ".join(f"{k} {v}" for k, v in res["by_status"].items()),
        f"displays reviewed {c['displays_reviewed']}/{c['displays']}, inline reviewed "
        f"{c['inline_reviewed']}/{c['inline']}, numeric checks {c['numeric_checks']}",
        f"wrote {res['out']} and the method block in {res['meta']}"])
