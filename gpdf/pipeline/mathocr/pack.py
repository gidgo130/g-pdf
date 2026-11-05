# SPDX-License-Identifier: AGPL-3.0-or-later
"""gpdf-cli math-review-pack: a math-export review queue as self-contained batches.

Each batch folder (batchNN/) holds BRIEF.md (the reviewer's brief from
docs/math-review.md, filled in), items.jsonl (one item per line: every engine's reading,
the crops as absolute paths, the page text before the formula, inline occurrences) and
decisions.template.jsonl (one line per item, "action": "TODO"). A reviewer reads the
brief and the batch and nothing else. render.py beside the batches renders a region of
the source PDF; pack.json records the options, the seed and the counts.

By default the display kinds are packed. ``inline_sample`` N picks N flagged inline
spans per chapter (a chapter starts at a level-1 heading in the page files), seeded,
each its own item (kind ``inline_span``) whose decision goes to the page's inline item.
"""

from __future__ import annotations

import json
import random
import re
import sys
from pathlib import Path

from gpdf.pipeline.manuals import ManualsError, parse_pages, write_atomic

DISPLAY_KINDS = ("disagree", "marker", "style", "identity", "stack", "tag",
                 "missing_display", "syntax")
KINDS = DISPLAY_KINDS + ("inline", "inline_missing")
BATCH_SIZE = 50
CONTEXT = 300                   # characters of page text before a display
SPAN_CONTEXT = 80               # characters each side of an inline span
DOC = Path(__file__).resolve().parents[3] / "docs" / "math-review.md"
BRIEF_RE = re.compile(r"<!-- brief -->\n(.*?)<!-- /brief -->", re.S)
FIELD_RE = re.compile(r"\{(batch|count|batch_dir|items|render|decisions|template)\}")
HEADING_RE = re.compile(r"(?m)^# (?!#)\S.*$")

RENDER = '''# Renders a region of the source PDF (written by gpdf-cli math-review-pack).
# usage: python -I render.py FILE_PAGE OUT.png [--box X0 Y0 X1 Y1] [--dpi 400]
import sys
import pymupdf

PDF = {pdf!r}
args = sys.argv[1:]
page, out = int(args[0]), args[1]
box = [float(x) for x in args[args.index("--box") + 1:args.index("--box") + 5]] if "--box" in args else None
dpi = int(args[args.index("--dpi") + 1]) if "--dpi" in args else 400
doc = pymupdf.open(PDF)
p = doc[page - 1]
r = p.rect
clip = pymupdf.Rect(r.x0 + box[0] * r.width, r.y0 + box[1] * r.height,
                    r.x0 + box[2] * r.width, r.y0 + box[3] * r.height) if box else None
p.get_pixmap(dpi=dpi, clip=clip).save(out)
print(out)
'''


class PackError(Exception):
    """The export folder or the options cannot be used; the message is for the user."""


def brief_template(doc: Path = DOC) -> str:
    if not doc.is_file():
        raise PackError(f"{doc} not found (the reviewer's brief comes from it)")
    m = BRIEF_RE.search(doc.read_text("utf-8"))
    if not m:
        raise PackError(f"{doc} has no <!-- brief --> ... <!-- /brief --> section")
    return m.group(1)


def fill(text: str, **fields) -> str:
    """The {field}s filled in; {{ and }} become braces; any other braces stay as written."""
    out = FIELD_RE.sub(lambda m: str(fields[m.group(1)]), text)
    return out.replace("{{", "{").replace("}}", "}")


def chapters(pages_dir: Path) -> dict[int, str]:
    """{file page: the chapter heading in force there} (pages before any: "")."""
    out, current = {}, ""
    for p in sorted(pages_dir.glob("p[0-9][0-9][0-9].md")):
        md = p.read_text("utf-8")
        heads = HEADING_RE.findall(md)
        first = HEADING_RE.search(md)
        out[int(p.stem[1:])] = first.group().strip() if first and md.index(first.group()) < 200 \
            else current
        if heads:
            current = heads[-1].strip()
    return out


def _occurrences(latex: str, md: str) -> list[int]:
    needle, where = "\\(" + latex + "\\)", []
    i = md.find(needle)
    while i >= 0:
        where.append(i)
        i = md.find(needle, i + 1)
    return where


def _span_place(latex: str, md: str, paragraph: int | None) -> dict:
    where = _occurrences(latex, md)
    ctx = [md[max(0, i - SPAN_CONTEXT):i + len(latex) + 4 + SPAN_CONTEXT] for i in where]
    if len(where) == 1:
        return {"occurrence": 1, "context": ctx[0]}
    if not where:
        return {"occurrence": None, "context": None}
    return {"occurrence": None, "occurrence_candidates": [
        {"occurrence": n, "context": c} for n, c in enumerate(ctx, 1)]}


def _display_context(latex: str | None, md: str) -> str | None:
    if not latex:
        return None
    i = md.find("$$\n" + latex + "\n")
    return md[max(0, i - CONTEXT):i].strip() if i >= 0 else None


def _abs(root: Path, paths) -> list[str]:
    return [str((root / p).resolve()) for p in paths if p]


def _display_item(it: dict, root: Path, md: str) -> dict:
    out = {"key": it["id"], "id": it["id"], "kind": it["kind"], "file": it["file"],
           "printed": it.get("printed"), "reasons": it.get("reasons", []), "tag": it.get("tag"),
           "latex_primary": it.get("latex_primary"), "latex_second": it.get("latex_second")}
    if it.get("latex_marker") is not None:
        out["latex_marker"] = it["latex_marker"]
    out["crops"] = _abs(root, it.get("crops") or [it.get("crop")])
    out["context"] = _display_context(it.get("latex_primary"), md)
    if it.get("latex_primary") is None:
        out["page_md"] = md
    return out


def _spans(it: dict) -> list[dict]:
    if it.get("spans") is not None:
        return it["spans"]
    if it.get("latex_primary") is not None:
        return [{"latex_primary": it["latex_primary"], "latex_second": it.get("latex_second"),
                 "paragraph": None}]
    return []


def _inline_item(it: dict, root: Path, md: str) -> dict:
    out = {"key": it["id"], "id": it["id"], "kind": "inline", "file": it["file"],
           "printed": it.get("printed"), "reasons": it.get("reasons", []),
           "crops": _abs(root, it.get("crops") or [it.get("crop")]), "spans": []}
    for s in _spans(it):
        out["spans"].append({**s, **_span_place(s["latex_primary"], md, s.get("paragraph"))})
    return out


def _span_item(it: dict, k: int, s: dict, root: Path, md: str) -> dict:
    out = {"key": f"{it['id']}#{k}", "id": it["id"], "kind": "inline_span", "file": it["file"],
           "printed": it.get("printed"), "latex_primary": s["latex_primary"],
           "latex_second": s.get("latex_second")}
    if s.get("latex_marker") is not None:
        out["latex_marker"] = s["latex_marker"]
    out["paragraph"] = s.get("paragraph")
    if s.get("crop"):           # the span's own line with the lines around it
        out["crops"] = _abs(root, [s["crop"]])
        out["line_box"] = s.get("line_box")
    else:                       # an older export: every line crop of the page item
        out["crops"] = _abs(root, it.get("crops") or [it.get("crop")])
    out.update(_span_place(s["latex_primary"], md, s.get("paragraph")))
    return out


def _missing_inline_item(it: dict, root: Path, md: str) -> dict:
    """An inline_missing item: the page's prose runs that look like math, each with its
    own line crop and its occurrence in the page prose (or the candidates)."""
    from gpdf.pipeline.mathocr.flags import prose_occurrences

    out = {"key": it["id"], "id": it["id"], "kind": "inline_missing", "file": it["file"],
           "printed": it.get("printed"), "reasons": it.get("reasons", []), "runs": []}
    for r in it.get("runs", []):
        run = {k: r.get(k) for k in ("paragraph", "text", "why", "occurrence", "context")}
        run["crop"] = _abs(root, [r.get("crop")])[0] if r.get("crop") else None
        if run["occurrence"] is None:
            where = prose_occurrences(r["text"], md)
            run["occurrence_candidates"] = [
                {"occurrence": n, "context": md[max(0, i - SPAN_CONTEXT):i + len(r["text"])
                                                + SPAN_CONTEXT]} for n, i in enumerate(where, 1)]
        out["runs"].append(run)
    return out


def _template(item: dict) -> dict:
    d = {"id": item["id"], "action": "TODO"}
    if item["kind"] == "inline_span":
        d["spans"] = [{"latex_primary": item["latex_primary"], "latex": item["latex_primary"],
                       "occurrence": item.get("occurrence")}]
    return d


def pack(export, out, kinds=DISPLAY_KINDS, batch_size: int = BATCH_SIZE,
         pages: str | None = None, inline_sample: int = 0, seed: int = 0,
         doc: Path = DOC) -> dict:
    root, out = Path(export), Path(out)
    review = root / "review.jsonl"
    if not review.is_file():
        raise PackError(f"{review} not found: is {root} a math-export folder?")
    bad = [k for k in kinds if k not in KINDS]
    if bad:
        raise PackError(f"unknown kind(s) {', '.join(bad)}; known: {', '.join(KINDS)}")
    if batch_size < 1 or inline_sample < 0:
        raise PackError("--batch-size must be at least 1 and --inline-sample not negative")
    template = brief_template(doc)
    items = [json.loads(s) for s in review.read_text("utf-8").splitlines() if s.strip()]
    if pages:
        try:
            keep = {i + 1 for i in parse_pages(pages, 10 ** 6)}
        except ManualsError as e:
            raise PackError(str(e)) from None
        items = [it for it in items if int(it["file"]) in keep]
    mds: dict[int, str] = {}

    def md_of(file: int) -> str:
        if file not in mds:
            p = root / "pages" / f"p{int(file):03d}.md"
            mds[file] = p.read_text("utf-8") if p.is_file() else ""
        return mds[file]

    packed = []
    for it in items:
        if it["kind"] in kinds and it["kind"] not in ("inline", "inline_missing"):
            packed.append(_display_item(it, root, md_of(it["file"])))
        elif it["kind"] == "inline" and "inline" in kinds:
            packed.append(_inline_item(it, root, md_of(it["file"])))
        elif it["kind"] == "inline_missing" and "inline_missing" in kinds:
            packed.append(_missing_inline_item(it, root, md_of(it["file"])))
    sample = {}
    if inline_sample:
        chap = chapters(root / "pages")
        pools: dict[str, list] = {}
        for it in items:
            if it["kind"] == "inline":
                for k, s in enumerate(_spans(it), 1):
                    pools.setdefault(chap.get(int(it["file"]), ""), []).append((it, k, s))
        rng = random.Random(seed)
        for name in sorted(pools):
            pool = sorted(pools[name], key=lambda x: (int(x[0]["file"]), x[0]["id"], x[1]))
            picks = rng.sample(pool, min(inline_sample, len(pool)))
            sample[name] = {"spans": len(pool), "picked": len(picks)}
            for it, k, s in picks:
                packed.append(_span_item(it, k, s, root, md_of(it["file"])))
    packed.sort(key=lambda x: (int(x["file"]), x["key"]))
    out.mkdir(parents=True, exist_ok=True)
    meta_path = root / "meta.json"
    pdf = json.loads(meta_path.read_text("utf-8"))["source"]["path"] if meta_path.is_file() else ""
    write_atomic(out / "render.py", RENDER.format(pdf=pdf))
    render = f'"{sys.executable}" -I "{(out / "render.py").resolve()}"'
    batches = []
    for n in range(0, len(packed), batch_size):
        chunk = packed[n:n + batch_size]
        name = f"batch{n // batch_size + 1:02d}"
        bdir = out / name
        (bdir / "work").mkdir(parents=True, exist_ok=True)
        (bdir / "renders").mkdir(exist_ok=True)
        write_atomic(bdir / "items.jsonl",
                     "".join(json.dumps(x, ensure_ascii=False) + "\n" for x in chunk))
        write_atomic(bdir / "decisions.template.jsonl",
                     "".join(json.dumps(_template(x), ensure_ascii=False) + "\n" for x in chunk))
        b = bdir.resolve()
        write_atomic(bdir / "BRIEF.md", fill(
            template, batch=name, count=len(chunk), batch_dir=b, items=b / "items.jsonl",
            render=render, decisions=out.resolve() / "decisions" / f"{name}.jsonl",
            template=b / "decisions.template.jsonl"))
        batches.append({"batch": name, "items": len(chunk),
                        "pages": sorted({int(x["file"]) for x in chunk})})
    (out / "decisions").mkdir(exist_ok=True)
    by_kind: dict[str, int] = {}
    for x in packed:
        by_kind[x["kind"]] = by_kind.get(x["kind"], 0) + 1
    summary = {"export": str(root.resolve()), "kinds": list(kinds), "pages": pages,
               "batch_size": batch_size, "inline_sample": inline_sample, "seed": seed,
               "items": len(packed), "by_kind": by_kind, "chapters": sample,
               "batches": batches}
    write_atomic(out / "pack.json", json.dumps(summary, ensure_ascii=False, indent=1) + "\n")
    return summary


def report(summary: dict) -> str:
    lines = [f"{summary['items']} item(s) in {len(summary['batches'])} batch(es): "
             + ", ".join(f"{k} {n}" for k, n in sorted(summary["by_kind"].items()))]
    for n, c in enumerate(summary["chapters"].values(), 1):     # numbers only on screen
        lines.append(f"inline sample, chapter {n}: {c['picked']} of {c['spans']} spans")
    lines += [f"{b['batch']}: {b['items']} items" for b in summary["batches"]]
    return "\n".join(lines)
