# SPDX-License-Identifier: AGPL-3.0-or-later
"""gpdf-cli math-export: a PDF (or a page range) through both MinerU tiers into the atlas
page files, text.md, the review queue and the identity checks.

    <out>/<stem>/pages/pNNN.md            the page in the atlas format
    <out>/<stem>/pages/pNNN.flags.json    its flags
    <out>/<stem>/text.md                  the pages joined
    <out>/<stem>/review.jsonl             one review item per flagged formula
    <out>/<stem>/crops/                   the items' page crops
    <out>/<stem>/meta.json                source, engine, pins, labels, options
    <out>/<stem>/run.json                 per-page counts, timing, cache size
    <out>/<stem>/cache/                   raw engine output (reused by the next run)
"""

from __future__ import annotations

import hashlib
import json
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pymupdf

from gpdf import __version__
from gpdf.pipeline import tesseract
from gpdf.pipeline.manuals import ManualsError, parse_pages, write_atomic
from gpdf.pipeline.mathocr import (MATHCHECK_HINT, assemble, awake, djvu, engine, flags, labels,
                                   marker, second)

SCHEMA = 1
CROP_DPI = 300


class MathExportError(Exception):
    """A refusal or failure with a message meant for the user."""


@dataclass
class Options:
    pdf: Path
    out: Path
    pages: str | None = None
    mineru: str | None = None
    mineru_home: str | None = None
    dpi: int = engine.DPI
    allow_unpinned: bool = False
    check_identities: bool | None = None     # None: when the extra is installed
    tesseract: str | None = None
    cache: Path | None = None
    group_inline: bool = True
    crop_dpi: int = CROP_DPI
    tiers: tuple = engine.TIERS
    marker: str | None = None                # the marker executable or its venv folder
    llama_server: str | None = None
    marker_gguf: str | None = None
    use_marker: bool | None = None           # None: when --marker or $GPDF_MARKER is given
    marker_rule: str = "value"               # flags.MARKER_RULES (chosen on KBJ, MATH_2B)
    inline_with: str = "either"              # flags.INLINE_WITH (chosen on KBJ, MATH_3)
    style_page: bool = False                 # the page-consistency style rule (opt-in)
    keep_awake: bool = True                  # hold a Windows PC awake (--allow-sleep: off)
    ocr_mode: str = "ocr"                    # engine.OCR_MODES: the render, or the text layer
    ddjvu: str | None = None                 # DjVuLibre (a .djvu source): folder or executable


@dataclass
class Summary:
    root: Path
    pages: int
    displays: int
    inline: int
    items: dict                 # kind -> count
    identities: dict
    seconds: float
    engine_seconds: dict
    cache_bytes: int
    runs: list
    steps: list
    rejected: list
    notices: list = field(default_factory=list)


def _ranges(pages: list[int]) -> list[tuple[int, int]]:
    out = []
    for p in pages:
        if out and p == out[-1][1] + 1:
            out[-1] = (out[-1][0], p)
        else:
            out.append((p, p))
    return out


def _dir_size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) if path.exists() else 0


def _identities_wanted(opt: bool | None, notices: list) -> bool:
    from gpdf.pipeline.mathocr import identities

    if opt is False:
        return False
    if not identities.AVAILABLE:
        if opt is True:
            raise MathExportError(MATHCHECK_HINT)
        notices.append(f"identity checks skipped: {MATHCHECK_HINT}")
        return False
    return True


def _marker(opts: Options, notices: list) -> marker.Marker | None:
    import os

    if opts.use_marker is False:
        return None
    if opts.use_marker is None and not (opts.marker or os.environ.get(marker.ENV_VAR)):
        notices.append("Marker not configured (--marker or $GPDF_MARKER): no third opinion")
        return None
    mk = marker.prepare(opts.marker, opts.llama_server, opts.marker_gguf,
                        allow_unpinned=opts.allow_unpinned)
    if mk.unpinned:
        notices.append("Marker running unpinned: " + ", ".join(mk.unpinned))
    return mk


def _tesseract(explicit, notices) -> tesseract.Tesseract | None:
    try:
        return tesseract.prepare(labels.OCR_LANG, explicit)
    except tesseract.TesseractError as e:
        notices.append(f"no Tesseract for the page-number fallback: {e}")
        return None


def run(opts: Options, progress=None) -> Summary:
    with awake.KeepAwake(opts.keep_awake):
        return _run(opts, progress)


def _run(opts: Options, progress=None) -> Summary:
    say = progress or (lambda *a, **k: None)
    t0 = time.time()
    a0 = awake.awake_clock()
    notices: list[str] = []
    pdf = Path(opts.pdf)
    if not pdf.is_file():
        raise MathExportError(f"{pdf} not found")
    root = Path(opts.out) / pdf.stem
    root.mkdir(parents=True, exist_ok=True)
    sha = engine.sha256_of(pdf)
    cache_key, render_pdf, djvu_info = sha, pdf, None
    if djvu.is_djvu(pdf):
        render_pdf, djvu_info = _djvu_proxy(opts, pdf, sha, root, say)
        # the engines read renders of the proxy: a different ddjvu dpi is another source
        cache_key = hashlib.sha256(f"{sha}:ddjvu:{opts.dpi}".encode()).hexdigest()
    doc = pymupdf.open(str(render_pdf))
    try:
        try:
            idx = parse_pages(opts.pages, doc.page_count)
        except ManualsError as e:
            raise MathExportError(str(e)) from None
        pages = [i + 1 for i in (idx if idx is not None else range(doc.page_count))]
        if not pages:
            raise MathExportError(f"no pages selected by --pages {opts.pages!r} "
                                  f"(the file has {doc.page_count})")
        want_ident = _identities_wanted(opts.check_identities, notices)
        eng = engine.prepare(opts.mineru, opts.mineru_home, allow_unpinned=opts.allow_unpinned)
        if eng.unpinned:
            notices.append("running unpinned: " + ", ".join(eng.unpinned))
        mk = _marker(opts, notices)
        cache = engine.Cache(opts.cache or root / "cache", cache_key, eng.version, opts.dpi,
                             opts.ocr_mode)
        say(f"engine: mineru-kit {eng.version} ({eng.exe})")
        engine_seconds = {}
        te, ae = time.time(), awake.awake_clock()
        parsed = engine.run(eng, render_pdf, pages, cache, tiers=opts.tiers,
                            progress=lambda tier, d, n: say(f"  {tier}: {d}/{n} pages"),
                            log_dir=root / "logs")
        engine_seconds["total"] = round(time.time() - te, 1)
        engine_seconds["total_awake"] = round(awake.awake_clock() - ae, 1)
        engine_seconds["parsed"] = parsed
        say(f"engine done in {engine_seconds['total']} s "
            f"(new pages: {', '.join(f'{k} {v}' for k, v in parsed.items())})")

        raw = {t: {p: cache.read(t, p) for p in pages} for t in opts.tiers}
        std = raw[opts.tiers[0]]
        basic = raw[opts.tiers[1]] if len(opts.tiers) > 1 else {}

        # Marker, after MinerU is done (one engine on the GPU at a time), on the math pages
        third = {}
        if mk:
            math_pages = [p for p in pages if marker.has_math(std[p].blocks)
                          or (p in basic and marker.has_math(basic[p].blocks))]
            mcache = marker.Cache(opts.cache or root / "cache", cache_key, mk.version)
            say(f"marker: {mk.version} with llama.cpp b{mk.llama_build} on {len(math_pages)} "
                f"math pages ({len(mcache.missing(math_pages))} not cached)")
            tm, am = time.time(), awake.awake_clock()
            n_new = marker.run(mk, render_pdf, math_pages, mcache,
                               progress=lambda d, n: say(f"  marker: {d}/{n} pages"),
                               log_dir=root / "logs")
            engine_seconds["marker"] = round(time.time() - tm, 1)
            engine_seconds["marker_awake"] = round(awake.awake_clock() - am, 1)
            engine_seconds["marker_pages"] = len(math_pages)
            engine_seconds["marker_parsed"] = n_new
            if n_new:
                engine_seconds["marker_per_page"] = round(engine_seconds["marker"] / n_new, 1)
                engine_seconds["marker_per_page_awake"] = round(
                    engine_seconds["marker_awake"] / n_new, 1)
            third = {p: marker.blocks(mcache.read(p)) for p in math_pages}

        # labels
        tess = _tesseract(opts.tesseract, notices)
        cands, sources = labels.collect(doc, pages, lambda p: std[p].blocks, tess,
                                        root / "cache" / "ocr")
        fit = labels.fit(cands)
        say(f"labels: {len(fit.runs)} runs, {len(fit.steps)} steps, "
            f"{len(fit.rejected)} rejected readings")

        # pages
        assembled = [assemble.assemble(std[p].blocks, p, fit.labels[p]) for p in pages]
        assemble.link_split_displays(assembled)
        openers = {a for a, _ in _ranges(pages)} | {s["at"] for s in fit.steps} | {
            pg.file for pg in assembled if any(it.kind == "heading" and it.md.startswith("# ")
                                               for it in pg.items)}
        assemble.drop_banners(assembled, openers)
        for pg in assembled:
            if pg.file in basic:
                others = [str(b.get("content") or "") for b in basic[pg.file].blocks
                          if b.get("type") == "equation"]
                second.strip_bars(pg, second.pair_displays(pg.displays, others))

        # flags
        runs_tags = {}
        for a, b in _ranges(pages):
            seq = [(f"p{pg.file:03d}", i, d.tag) for pg in assembled if a <= pg.file <= b
                   for i, d in enumerate(pg.displays)]
            runs_tags.update(flags.tag_problems(seq))
        items_of, page_stats = {}, {}
        for pg in assembled:
            other = _engine_view(basic[pg.file].blocks) if pg.file in basic else None
            mk_view = _engine_view(third[pg.file]) if pg.file in third else None
            page_stats[pg.file] = {}
            items_of[pg.file] = flags.review_items(
                pg, other or {"displays": [], "paragraphs": []}, runs_tags, opts.group_inline,
                mk_view, marker_rule=opts.marker_rule, inline_with=opts.inline_with,
                style_page=opts.style_page, std_blocks=std[pg.file].blocks,
                stats=page_stats[pg.file])

        # identities
        ident_counts = {}
        if want_ident:
            ident_counts = _identities(assembled, items_of, _ranges(pages), say)

        # outputs
        all_items = [it for pg in assembled for it in items_of[pg.file]]
        crops_dir = root / "crops"
        shutil.rmtree(crops_dir, ignore_errors=True)
        flags.write_crops(doc, all_items, crops_dir, opts.crop_dpi)
        pages_dir = root / "pages"
        pages_dir.mkdir(exist_ok=True)
        texts = []
        for pg in assembled:
            md = pg.render()
            write_atomic(pages_dir / f"p{pg.file:03d}.md", md)
            write_atomic(pages_dir / f"p{pg.file:03d}.flags.json", pg.flags_json())
            texts.append(md)
        write_atomic(root / "text.md", "\n".join(texts))
        write_atomic(root / "review.jsonl",
                     "".join(json.dumps(it.record(), ensure_ascii=False) + "\n" for it in all_items))
        kinds = {}
        for it in all_items:
            kinds[it.kind] = kinds.get(it.kind, 0) + 1
        per_page = []
        for pg in assembled:
            its = items_of[pg.file]
            per_page.append({
                "file": pg.file, "printed": pg.label.marker, "inferred": pg.label.inferred,
                "label_source": sources.get(pg.file, "none"),
                "displays": len(pg.displays),
                "inline_spans": sum(len(p.inlines) for p in pg.paragraphs),
                "items": {k: sum(1 for it in its if it.kind == k) for k in flags.KINDS
                          if any(it.kind == k for it in its)},
                "dropped": sum(1 for f in pg.flags if f["type"] == "dropped"),
                "identities": ident_counts.get(pg.file, {}),
                "marker": pg.file in third,
                **page_stats.get(pg.file, {}),
            })
        seconds = round(time.time() - t0, 1)
        awake_seconds = round(awake.awake_clock() - a0, 1)
        cache_bytes = _dir_size(Path(opts.cache or root / "cache"))
        totals = {"pages": len(assembled), "displays": sum(r["displays"] for r in per_page),
                  "inline_spans": sum(r["inline_spans"] for r in per_page), "items": kinds,
                  "identities": _sum_counts(ident_counts)}
        meta = {"schema": SCHEMA, "made": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "made_with": f"g-pdf {__version__}",
                "source": {"path": str(pdf), "sha256": sha, "page_count": doc.page_count,
                           "pages": pages,
                           **({"render_pdf": str(render_pdf), "djvu": djvu_info}
                              if djvu_info else {})},
                "engine": eng.describe(), "marker": mk.describe() if mk else None,
                "dpi": opts.dpi, "crop_dpi": opts.crop_dpi,
                "tiers": list(opts.tiers), "cache": str(cache.root),
                "options": {"pages": opts.pages, "group_inline": opts.group_inline,
                            "marker_rule": opts.marker_rule, "inline_with": opts.inline_with,
                            "style_page": opts.style_page, "keep_awake": opts.keep_awake,
                            "ocr_mode": opts.ocr_mode,
                            "check_identities": want_ident,
                            "tesseract": tess.version if tess else None},
                "labels": {"runs": fit.runs, "steps": fit.steps, "rejected": fit.rejected,
                           "sources": {str(p): sources[p] for p in pages}},
                "notices": notices}
        write_atomic(root / "meta.json", json.dumps(meta, ensure_ascii=False, indent=1) + "\n")
        run_rec = {"schema": SCHEMA, "seconds": seconds, "awake_seconds": awake_seconds,
                   "engine_seconds": engine_seconds,
                   "cache_bytes": cache_bytes, "totals": totals, "pages": per_page}
        write_atomic(root / "run.json", json.dumps(run_rec, ensure_ascii=False, indent=1) + "\n")
        return Summary(root, len(assembled), totals["displays"], totals["inline_spans"], kinds,
                       totals["identities"], seconds, engine_seconds, cache_bytes, fit.runs,
                       fit.steps, fit.rejected, notices)
    finally:
        doc.close()


def _djvu_proxy(opts: Options, path: Path, sha: str, root: Path, say) -> tuple[Path, dict]:
    """The DjVu's selected pages rendered by ddjvu into a proxy PDF with its page count."""
    if opts.ocr_mode == "txt":
        raise MathExportError("--ocr-mode txt reads a PDF text layer; a DjVu is read from its "
                              "page images (--ocr-mode ocr)")
    try:
        tools = djvu.prepare(opts.ddjvu)
        n = tools.page_count(path)
        try:
            idx = parse_pages(opts.pages, n)
        except ManualsError as e:
            raise MathExportError(str(e)) from None
        pages = [i + 1 for i in (idx if idx is not None else range(n))]
        say(f"djvu: {n} pages, rendering {len(pages)} at {opts.dpi} dpi with {tools.ddjvu}")
        cache_dir = Path(opts.cache or root / "cache") / sha[:16] / f"ddjvu-{opts.dpi}"
        proxy = djvu.proxy_pdf(tools, path, pages, opts.dpi, cache_dir,
                               root / "source" / f"{path.stem}.pdf")
    except djvu.DjvuError as e:
        raise MathExportError(str(e)) from None
    return proxy, {"dpi": opts.dpi, **tools.describe()}


def _engine_view(blocks: list[dict]) -> dict:
    """An engine's page as the flag rules read it: display LaTeX, their boxes, text blocks."""
    eqs = [b for b in blocks if b.get("type") == "equation"]
    return {"displays": [str(b.get("content") or "") for b in eqs],
            "boxes": [b.get("bbox") for b in eqs],
            "paragraphs": [b for b in blocks if b.get("type") == "text"]}


def _sum_counts(per_page: dict) -> dict:
    out = {"checked": 0, "passed": 0, "failed": 0, "inconclusive": 0, "skipped": 0}
    for c in per_page.values():
        for k in out:
            out[k] += c.get(k, 0)
    return out


def _identities(assembled, items_of, ranges, say) -> dict:
    """Run the identity pass page by page; failures become (or join) review items."""
    from gpdf.pipeline.mathocr import identities

    counts = {}
    for a, b in ranges:
        # first pass: the constants each section defines (C = ..., q = 1 - p), so a pmf
        # printed before its "where C = ..." is still checked
        defs, sec = [{}], 0
        for pg in assembled:
            if a <= pg.file <= b:
                for it in pg.items:
                    if it.kind == "heading" and it.md.startswith("##"):
                        defs.append({})
                    elif it.kind == "display":
                        for k, v in identities.definitions(it.display.latex).items():
                            defs[-1].setdefault(k, v)
        ctx = identities.Context()
        ctx.new_section("", defs[0])
        for pg in assembled:
            if not a <= pg.file <= b:
                continue
            results = []
            before = ""
            n = 0
            for it in pg.items:
                if it.kind == "heading" and it.md.startswith("##"):
                    sec += 1
                    ctx.new_section(it.md, defs[sec])
                if it.kind == "para":
                    before = it.md[-300:]
                if it.kind != "display":
                    continue
                n += 1
                d = it.display
                res = identities.check_display(d.latex, before, ctx)
                results.append(res)
                if res.status != "failed":
                    continue
                failed = [c for c in res.checks if c.status == "failed"]
                extra = {"identity": [c.identity for c in failed],
                         "identity_kind": res.kind,
                         "identity_details": [c.details for c in failed]}
                reason = "identity failed: " + "; ".join(c.identity for c in failed)
                pid = f"p{pg.file:03d}-d{n:02d}"
                existing = next((x for x in items_of[pg.file] if x.id == pid), None)
                if existing:
                    existing.reasons.append(reason)
                    existing.extra.update(extra)
                    existing.note = res.note
                else:
                    items_of[pg.file].append(flags.Item(
                        pid, pg.file, pg.label.marker, "identity", [reason], d.tag, d.latex,
                        None, bbox=d.bbox, note=res.note, extra=extra))
            counts[pg.file] = identities.counts(results)
    tot = _sum_counts(counts)
    say(f"identities: {tot['checked']} checked, {tot['passed']} passed, {tot['failed']} failed, "
        f"{tot['inconclusive']} inconclusive, {tot['skipped']} skipped")
    return counts


def report(s: Summary) -> str:
    lines = [f"{s.pages} pages, {s.displays} displays, {s.inline} inline spans in {s.seconds} s "
             f"(engine {s.engine_seconds.get('total', 0)} s); cache {s.cache_bytes / 1e6:.1f} MB",
             "review items: " + (", ".join(f"{k} {v}" for k, v in sorted(s.items.items()))
                                 or "none")]
    if s.identities.get("checked") or s.identities.get("skipped"):
        i = s.identities
        lines.append(f"identities: {i['checked']} checked, {i['passed']} passed, {i['failed']} "
                     f"failed, {i['inconclusive']} inconclusive, {i['skipped']} skipped")
    for r in s.runs:
        lines.append(f"label run: file {r['pages'][0]}-{r['pages'][1]} printed "
                     f"{r['printed'][0]}-{r['printed'][1]} (offset {r['offset']:+d}, "
                     f"{r['found']} read)")
    for st in s.steps:
        lines.append(f"label step at file {st['at']}: offset {st['from']:+d} -> {st['to']:+d}")
    for rj in s.rejected:
        lines.append(f"label rejected on file {rj['page']}: {rj['value']} ({rj['reason']})")
    for n in s.notices:
        lines.append(f"note: {n}")
    lines.append(f"output in {s.root}")
    return "\n".join(lines)
