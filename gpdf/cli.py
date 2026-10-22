# SPDX-License-Identifier: AGPL-3.0-or-later
"""gpdf-cli: book prep from the command line. No Qt (it must not import gpdf.core/gpdf.ui).

    gpdf-cli split book.pdf -o OUTDIR --dry-run     # detect; write report, decisions file
                                                    # and the review sheet (.review.html)
    gpdf-cli split book.pdf -o OUTDIR --decisions OUTDIR/book_split.decisions.json
    gpdf-cli split book.pdf -o OUTDIR --sheet-only [--decisions FILE]
                                                    # rewrite only the review sheet
    gpdf-cli ocr book.pdf -o OUTDIR --lang spa      # invisible text over the scans
    gpdf-cli survey ROOT [ROOT ...] --catalogue DIR [--derived DIR]
                                                    # read-only catalogue + suggestions
    gpdf-cli survey --sheet-only --catalogue DIR    # rewrite only its review sheet
    gpdf-cli apply --catalogue DIR --derived OUTDIR [--dry-run]
                                                    # apply the confirmed decisions

Exit codes: 0 ok; 2 finished, but flagged pages were left whole because they were not
reviewed (or spreads could not be split), OCR failed on some pages, the survey could
not read a file or run a trial page, or apply refused a file; 1 error.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
from pathlib import Path

from gpdf import __version__
from gpdf.pipeline import PREP_HINT

EXIT_OK, EXIT_ERROR, EXIT_UNREVIEWED = 0, 1, 2


class _Parser(argparse.ArgumentParser):
    def error(self, message):              # usage errors are errors (1), not "unreviewed" (2)
        self.print_usage(sys.stderr)
        self.exit(EXIT_ERROR, f"{self.prog}: error: {message}\n")


def build_parser() -> argparse.ArgumentParser:
    p = _Parser(prog="gpdf-cli", description="g-pdf book prep (writes new files; never "
                                             "changes the source).")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command", required=True, metavar="COMMAND")
    s = sub.add_parser("split", help="split two-page spread scans into single pages",
                       description="Split two-page spread scans into single pages, writing "
                                   "OUTDIR/<name>_split.pdf. Pages the detector is unsure "
                                   "about are left whole unless reviewed.")
    s.add_argument("input", metavar="IN", type=Path, help="the scanned PDF")
    s.add_argument("-o", "--outdir", metavar="OUTDIR", type=Path, required=True,
                   help="folder for the output files (created if missing)")
    s.add_argument("--dry-run", action="store_true",
                   help="detect only: write <name>_split.report.json, "
                        "<name>_split.decisions.json and the review sheet "
                        "<name>_split.review.html (open it in a browser)")
    s.add_argument("--no-sheet", action="store_true",
                   help="dry run: don't write the review sheet")
    s.add_argument("--sheet-only", action="store_true",
                   help="detect and write only the review sheet <name>_split.review.html, "
                        "never the report or decisions file; with --decisions the sheet "
                        "opens showing that file's review")
    s.add_argument("--decisions", metavar="FILE", type=Path,
                   help="apply a reviewed decisions file")
    s.add_argument("--accept-flagged", action="store_true",
                   help="split unreviewed flagged pages by the detector's guess")
    s.add_argument("--force", action="store_true",
                   help="replace existing output files; accept a decisions file made for "
                        "an older version of the source")
    s.add_argument("--report", metavar="FILE", type=Path,
                   help="where to write the JSON report (dry run default: "
                        "OUTDIR/<name>_split.report.json)")
    o = sub.add_parser("ocr", help="add an invisible OCR text layer to scanned pages",
                       description="Write OUTDIR/<name>_ocr.pdf: every page with a scan image "
                                   "and no text gets an invisible Tesseract text layer over "
                                   "the untouched image. Pages that already have text are "
                                   "left as they are.")
    o.add_argument("input", metavar="IN", type=Path, help="the scanned PDF")
    o.add_argument("-o", "--outdir", metavar="OUTDIR", type=Path, required=True,
                   help="folder for the output files (created if missing)")
    o.add_argument("--lang", metavar="LANGS", required=True,
                   help="Tesseract languages, e.g. spa or eng+spa")
    o.add_argument("--jobs", metavar="N", type=int,
                   help="Tesseract processes in parallel (default: CPU count - 1)")
    o.add_argument("--pages", metavar="RANGE",
                   help="only these pages (1-based), e.g. 40-59 or 1,3,7-")
    o.add_argument("--force", action="store_true", help="replace existing output files")
    o.add_argument("--report", metavar="FILE", type=Path,
                   help="where to write the JSON report (default: "
                        "OUTDIR/<name>_ocr.report.json)")
    o.add_argument("--tesseract", metavar="PATH",
                   help="the tesseract executable (default: $GPDF_TESSERACT, PATH, then "
                        r"C:\Program Files\Tesseract-OCR)")
    v = sub.add_parser("survey", help="read-only survey: catalogue, text quality, suggestions",
                       description="Walk the ROOT folders for *.pdf and *.txt and write "
                                   "DIR/catalogue.json with each file's facts, text-quality "
                                   "score, duplicates and a suggested action, plus the review "
                                   "sheet DIR/catalogue.review.html. Nothing under a ROOT or "
                                   "--derived folder is changed; nothing is applied.")
    v.add_argument("roots", metavar="ROOT", nargs="*", type=Path,
                   help="folders to walk (recursively)")
    v.add_argument("--sheet-only", action="store_true",
                   help="rewrite only the review sheet from DIR/catalogue.json and the "
                        "decisions file (no ROOT)")
    v.add_argument("--catalogue", metavar="DIR", type=Path, required=True,
                   help="catalogue folder (created if missing; keep "
                        "catalogue.decisions.json here)")
    v.add_argument("--derived", metavar="DIR", type=Path, action="append", default=[],
                   help="folder with working files (<name>_split_ocr.pdf, <name>_rebuilt.pdf, "
                        "<name>_ocr.pdf, <name>_split.pdf); may be repeated")
    v.add_argument("--no-trial", action="store_true",
                   help="don't run the Tesseract rebuild trial")
    v.add_argument("--remeasure", action="store_true",
                   help="measure every file again, even if unchanged")
    v.add_argument("--jobs", metavar="N", type=int,
                   help="Tesseract processes in parallel for the trial (default: CPU count - 1)")
    v.add_argument("--tesseract", metavar="PATH",
                   help="the tesseract executable (default: $GPDF_TESSERACT, PATH, then "
                        r"C:\Program Files\Tesseract-OCR)")
    a = sub.add_parser("apply", help="apply the confirmed catalogue decisions (new files)",
                       description="Act on the confirmed decisions in DIR/"
                                   "catalogue.decisions.json: rebuild_layer and ocr_missing "
                                   "write OUTDIR/<name>_rebuilt.pdf (Tesseract's text in "
                                   "place of the old layer, the scan untouched); keep_text "
                                   "and exclude write nothing. Sources, catalogue.json and "
                                   "the decisions file are never changed; each output is "
                                   "recorded in DIR/applied.json.")
    a.add_argument("--catalogue", metavar="DIR", type=Path, required=True,
                   help="catalogue folder (catalogue.json and catalogue.decisions.json)")
    a.add_argument("--derived", metavar="OUTDIR", type=Path, required=True,
                   help="folder for the output files (created if missing)")
    a.add_argument("--dry-run", action="store_true",
                   help="print what would be done per file; write nothing")
    a.add_argument("--only", metavar="TEXT", nargs="+", default=[],
                   help="only files whose id starts with TEXT or whose path contains it")
    a.add_argument("--jobs", metavar="N", type=int,
                   help="Tesseract processes in parallel (default: CPU count - 1)")
    a.add_argument("--force", action="store_true", help="replace existing output files")
    a.add_argument("--tesseract", metavar="PATH",
                   help="the tesseract executable (default: $GPDF_TESSERACT, PATH, then "
                        r"C:\Program Files\Tesseract-OCR)")
    return p


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "ocr":
        return _ocr_main(args, parser)
    if args.command == "survey":
        return _survey_main(args, parser)
    if args.command == "apply":
        return _apply_main(args, parser)
    if args.sheet_only:
        for flag, on in (("--dry-run", args.dry_run), ("--no-sheet", args.no_sheet),
                         ("--report", args.report), ("--accept-flagged", args.accept_flagged)):
            if on:
                parser.error(f"--sheet-only cannot be used with {flag}")
    try:
        from gpdf.pipeline import split
        from gpdf.pipeline.report import DecisionsError
    except ModuleNotFoundError as e:
        if e.name in ("numpy", "PIL"):
            print(f"gpdf-cli: {PREP_HINT}", file=sys.stderr)
            return EXIT_ERROR
        raise
    try:
        return _split(args, split)
    except (split.SplitError, DecisionsError, OSError) as e:
        print(f"gpdf-cli: error: {e}", file=sys.stderr)
    except Exception as e:  # noqa: BLE001 - one line for the user, not a traceback
        print(f"gpdf-cli: unexpected error: {type(e).__name__}: {e}", file=sys.stderr)
    return EXIT_ERROR


def _ocr_main(args, parser) -> int:
    if args.jobs is not None and args.jobs < 1:
        parser.error("--jobs must be at least 1")
    try:
        from gpdf.pipeline import ocr, tesseract
    except ModuleNotFoundError as e:
        if e.name in ("numpy", "PIL"):
            print(f"gpdf-cli: {PREP_HINT}", file=sys.stderr)
            return EXIT_ERROR
        raise
    try:
        return _ocr(args, ocr, tesseract)
    except (ocr.OcrError, tesseract.TesseractError, OSError) as e:
        print(f"gpdf-cli: error: {e}", file=sys.stderr)
    except Exception as e:  # noqa: BLE001 - one line for the user, not a traceback
        print(f"gpdf-cli: unexpected error: {type(e).__name__}: {e}", file=sys.stderr)
    return EXIT_ERROR


def _ocr(args, ocr, tesseract) -> int:
    from gpdf.pipeline.split import same_file

    src = args.input
    if not src.is_file():
        raise ocr.OcrError(f"no such file: {src}")
    out = args.outdir / ocr.output_name(src)
    report_path = args.report or args.outdir / f"{src.stem}_ocr.report.json"
    for path in (out, report_path):
        if same_file(src, path):
            raise ocr.OcrError(f"{path} is the source file")
    if out.exists() and not args.force:                   # before the slow part
        raise ocr.OcrError(f"{out} already exists (pass --force to replace it)")
    tess = tesseract.prepare(args.lang, args.tesseract)
    args.outdir.mkdir(parents=True, exist_ok=True)
    report = ocr.ocr_document(src, out, args.lang, jobs=args.jobs, pages=args.pages,
                              force=args.force, tess=tess, progress=_progress("ocr"))
    report.save(report_path)
    _print_ocr(report, ocr, out, report_path)
    return report.exit_code


def _print_ocr(report, ocr, out, report_path):
    print(f"wrote {out}: {len(report.pages)} of {report.pages_total} pages checked "
          f"({report.size_ratio:.2f}x the source size, {report.seconds:.1f} s)")
    print(f"  tesseract {report.tesseract}, tessdata {report.tessdata}, --lang {report.langs}, "
          f"{report.jobs} jobs")
    labels = {ocr.OCR: "OCR'd", ocr.HAS_TEXT: "has text layer", ocr.BLANK: "blank",
              ocr.NO_IMAGE: "no image", ocr.FAILED: "failed"}
    for status in ocr.STATUSES:
        pages = report.by_status(status)
        line = f"  {labels[status]:<15}{len(pages):>5}"
        if status == ocr.OCR and report.mean_conf is not None:
            line += f"   mean confidence {report.mean_conf:.1f}"
        elif status != ocr.OCR and pages:
            line += f"   p. {_pages([p.page for p in pages][:20])}" + (
                " ..." if len(pages) > 20 else "")
        print(line)
    if report.lowest:
        print("  lowest confidence: " + ", ".join(f"p. {p.page} ({p.mean_conf:.0f})"
                                                  for p in report.lowest))
    for p in report.by_status(ocr.FAILED):
        print(f"  p. {p.page} failed: {p.error.splitlines()[-1] if p.error else ''}")
    print(f"report:    {report_path}")


def _survey_main(args, parser) -> int:
    if args.jobs is not None and args.jobs < 1:
        parser.error("--jobs must be at least 1")
    if args.sheet_only:
        for flag, on in (("ROOT", args.roots), ("--derived", args.derived),
                         ("--no-trial", args.no_trial), ("--remeasure", args.remeasure),
                         ("--jobs", args.jobs), ("--tesseract", args.tesseract)):
            if on:
                parser.error(f"--sheet-only cannot be used with {flag}")
    elif not args.roots:
        parser.error("give at least one ROOT folder")
    try:
        from gpdf.pipeline import catalogue, survey
    except ModuleNotFoundError as e:
        if e.name in ("numpy", "PIL", "wordfreq"):
            print(f"gpdf-cli: {PREP_HINT}", file=sys.stderr)
            return EXIT_ERROR
        raise
    if args.sheet_only:
        return _survey_sheet_only(args, catalogue)
    try:
        res = survey.run(args.roots, args.catalogue, args.derived, trial=not args.no_trial,
                         remeasure=args.remeasure, jobs=args.jobs,
                         tesseract_path=args.tesseract, progress=_file_progress("measuring"))
    except (survey.SurveyError, catalogue.CatalogueError, OSError) as e:
        print(f"gpdf-cli: error: {e}", file=sys.stderr)
        return EXIT_ERROR
    except Exception as e:  # noqa: BLE001 - one line for the user, not a traceback
        print(f"gpdf-cli: unexpected error: {type(e).__name__}: {e}", file=sys.stderr)
        return EXIT_ERROR
    _print_survey(res, survey, catalogue)
    return res.exit_code


def _apply_main(args, parser) -> int:
    if args.jobs is not None and args.jobs < 1:
        parser.error("--jobs must be at least 1")
    try:
        from gpdf.pipeline import apply, catalogue, ocr, tesseract
    except ModuleNotFoundError as e:
        if e.name in ("numpy", "PIL", "wordfreq"):
            print(f"gpdf-cli: {PREP_HINT}", file=sys.stderr)
            return EXIT_ERROR
        raise
    try:
        items = apply.plan(args.catalogue, args.derived, args.only, args.force)
        tess = {} if args.dry_run else apply.prepare_tesseract(items, args.tesseract)
    except (apply.ApplyError, catalogue.CatalogueError, tesseract.TesseractError,
            OSError) as e:
        print(f"gpdf-cli: error: {e}", file=sys.stderr)
        return EXIT_ERROR
    t0 = time.perf_counter()
    written, failed, errors = [], 0, 0
    jobs = max(1, args.jobs or (os.cpu_count() or 2) - 1)
    for it in items:
        if not it.writes:
            continue
        out = args.derived / apply.output_name(it.source)
        if args.dry_run:
            print(f"would {it.action:<14} {_short(it.rel, 60)}: pages {_spec(it.pages)}, "
                  f"--lang {it.langs} -> {out}")
            continue
        try:
            args.derived.mkdir(parents=True, exist_ok=True)
            report = apply.apply_file(it, out, tess[it.langs], jobs,
                                      progress=_progress(Path(it.source).name[:30]))
            report.save(args.derived / apply.report_name(it.source))
            apply.record(args.catalogue, it, out, report)
        except (apply.ApplyError, ocr.OcrError, OSError) as e:
            print(f"error   {_short(it.rel, 60)}: {e}", file=sys.stderr)
            errors += 1
            continue
        except Exception as e:  # noqa: BLE001 - one line for the user, not a traceback
            print(f"error   {_short(it.rel, 60)}: {type(e).__name__}: {e}", file=sys.stderr)
            errors += 1
            continue
        written.append(report)
        failed += bool(report.by_status(apply.FAILED))
        _print_applied(it, report, apply)
    refused = [it for it in items if it.refused]
    for it in refused:
        print(f"refused {_short(it.rel, 60)}: {it.refused}")
    nothing = {}
    for it in items:
        if it.note:
            key = f"{it.action} ({it.note})" if it.action in apply.WRITES else it.action
            nothing[key] = nothing.get(key, 0) + 1
    if nothing:
        print("nothing to write: " + ", ".join(f"{k} {n}" for k, n in nothing.items()))
    if args.dry_run:
        print(f"dry run: {sum(it.writes for it in items)} file(s) would be written, "
              f"{len(refused)} refused; nothing written")
    else:
        pages = {}
        for r in written:
            for p in r.pages:
                pages[p.status] = pages.get(p.status, 0) + 1
        print(f"wrote {len(written)} file(s), {len(refused)} refused, {errors} error(s); "
              "pages: " + (", ".join(f"{s} {pages[s]}" for s in apply.STATUSES if s in pages)
                           or "none") + f" ({time.perf_counter() - t0:.1f} s)")
        if written:
            print(f"recorded in {args.catalogue / apply.APPLIED}")
    if errors:
        return EXIT_ERROR
    return EXIT_UNREVIEWED if refused or failed else EXIT_OK


def _spec(pages) -> str:
    if pages in (None, "all"):
        return "all"
    if isinstance(pages, list):
        return _pages(pages[:12]) + (" ..." if len(pages) > 12 else "")
    return str(pages)


def _print_applied(it, report, apply):
    before, after = report.medians()
    counts = ", ".join(f"{s} {len(report.by_status(s))}" for s in apply.STATUSES
                       if report.by_status(s))
    change = "n/a" if before is None else f"{before:.1f}% -> {after:.1f}%"
    print(f"{it.action:<14} {_short(it.rel, 60)}: {len(report.pages)} pages ({counts}); "
          f"median suspect {change}; {report.size_ratio:.2f}x size; {report.seconds:.1f} s")
    for p in report.by_status(apply.FAILED):
        print(f"  p. {p.page} failed: {p.error.splitlines()[-1] if p.error else ''}")
    print(f"  -> {report.output}")


def _survey_sheet_only(args, catalogue) -> int:
    folder = args.catalogue
    try:
        if not (folder / catalogue.CATALOGUE).is_file():
            raise catalogue.CatalogueError(f"no {catalogue.CATALOGUE} in {folder}: run the "
                                           "survey first")
        cat = catalogue.load(folder)
        decisions = catalogue.load_decisions(folder)
        t0 = time.perf_counter()
        path, size = catalogue.write_sheet(cat, decisions, folder)
    except (catalogue.CatalogueError, OSError) as e:
        print(f"gpdf-cli: error: {e}", file=sys.stderr)
        return EXIT_ERROR
    shown = len(decisions["files"])
    print(f"sheet:     {path} ({size / 1e6:.1f} MB, {len(cat['files'])} files, "
          f"{time.perf_counter() - t0:.1f} s)")
    print(f"showing:   {shown} decision(s) from {folder / catalogue.DECISIONS}"
          if (folder / catalogue.DECISIONS).exists() else "showing:   no decisions file yet")
    return EXIT_OK


def _short(text, n):
    return text if len(text) <= n else text[: n // 2 - 1] + "..." + text[-(n - n // 2 - 2):]


def _suggestion_text(s) -> str:
    if not s:
        return ""
    a = s["action"]
    if a == "ocr_missing" and s.get("pages"):
        return f"{a} p. {_pages(s['pages'][:6])}" + (" ..." if len(s["pages"]) > 6 else "")
    return f"{a} ({s['reason']})" if s.get("reason") else a


def _print_survey(res, survey, catalogue):
    files = res.catalogue["files"]
    roots = [r["path"] for r in res.catalogue["roots"]]
    shown = list(res.scanned) + sorted(
        (k for k, e in files.items() if e["status"] == catalogue.MISSING
         and any(catalogue.under(e["path"], r) for r in roots) and k not in res.scanned),
        key=lambda k: files[k]["rel"].lower())
    head = (f"{'file':<52} {'pages':>5} {'lang':<4} {'median/wtd %':>13} {'poor':>4}  "
            f"{'suggestion':<28} decision")
    print(head)
    print("-" * len(head))
    working = False
    for k in shown:
        e = files[k]
        m = e.get("measured") or {}
        name = e["rel"] + ("  +" if e.get("working_file") else "")
        working |= bool(e.get("working_file"))
        if e["status"] == catalogue.MISSING:
            print(f"{_short(name, 52):<52} {'':>5} {'':<4} {'':>13} {'':>4}  {'missing':<28}")
            continue
        s = m.get("score") or {}
        pages = m.get("pages")
        pages = "txt" if m.get("kind") == "txt" else "?" if pages is None else str(pages)
        med = "n/a" if s.get("median") is None else f"{s['median']:.1f}"
        wtd = "n/a" if s.get("weighted") is None else f"{s['weighted']:.1f}"
        lang = (m.get("language") or {}).get("lang") or "?"
        status, _, _ = catalogue.decision_status(e, res.decisions, files)
        print(f"{_short(name, 52):<52} {pages:>5} {lang:<4} {med + ' / ' + wtd:>13} "
              f"{s.get('poor', 0):>4}  {_short(_suggestion_text(e.get('suggestion')), 28):<28} "
              f"{status}")
    if working:
        print("+ measured on its working file (--derived)")
    counts = {}
    for k in res.scanned:
        a = (files[k].get("suggestion") or {}).get("action", "?")
        counts[a] = counts.get(a, 0) + 1
    print("suggestions: " + ", ".join(f"{a} {n}" for a, n in sorted(counts.items())))
    c = res.counts
    print(f"files: {len(res.scanned)} ({res.measured} measured, {res.reused} reused; "
          f"{c.get('new', 0)} new, {c.get('moved', 0)} moved, {c.get('changed', 0)} changed, "
          f"{c.get('missing', 0)} newly missing); hashed {res.hashed_bytes / 1e6:.0f} MB")
    reused = f"; earlier trials kept for {res.trial_reused} file(s)" if res.trial_reused else ""
    if res.trial_note:
        print(f"trial: {res.trial_note}{reused}")
    else:
        print(f"trial: {res.trial_pages} pages ({res.trial_failed} failed){reused}")
    if res.trial_pages:
        print(f"  note: {survey.TRIAL_NOTE}")
    for e in res.unreadable:
        why = e.get("error") or "; ".join(e["measured"]["errors"])
        print(f"  could not read {e['rel']}: {why}")
    for w in res.warnings:
        print(f"  warning: {w}")
    print(f"time:      {res.seconds:.1f} s")
    for path in res.written:
        if path.name == catalogue.SHEET:
            print(f"sheet:     {path} ({res.sheet_bytes / 1e6:.1f} MB): open it in a browser")
        else:
            print(f"wrote:     {path}")
    dec = res.folder / catalogue.DECISIONS
    if dec.exists():
        print(f"decisions: {dec} (read; the survey never writes it)")
    else:
        print(f"decisions: none yet; Save decisions from the sheet into {res.folder}")


def _file_progress(label):
    if not sys.stderr.isatty():
        return None

    def show(done, total, name):
        end = "\n" if done == total else ""
        print(f"\r  {label} {done}/{total} {_short(name, 50):<50}", end=end, file=sys.stderr,
              flush=True)

    return show


def _progress(label):
    if not sys.stderr.isatty():
        return None

    def show(done, total):
        end = "\n" if done == total else ""
        print(f"\r  {label} {done}/{total}", end=end, file=sys.stderr, flush=True)

    return show


def _pages(nums) -> str:
    return ", ".join(str(n) for n in nums) if nums else "none"


def _split(args, split) -> int:
    src = args.input
    if not src.is_file():
        print(f"gpdf-cli: error: no such file: {src}", file=sys.stderr)
        return EXIT_ERROR
    args.outdir.mkdir(parents=True, exist_ok=True)
    stem = src.stem
    out = args.outdir / f"{stem}_split.pdf"
    if args.dry_run:
        return _dry_run(args, split, src, stem)
    if args.sheet_only:
        return _sheet_only(args, split, src, stem)
    report = split.split_document(src, out, decisions=args.decisions,
                                  accept_flagged=args.accept_flagged, force=args.force,
                                  progress=_progress("detecting"),
                                  split_progress=_progress("splitting"))
    if args.report:
        _check_writable(split, src, args.report, force=True)
        report.save(args.report)
    _print_run(report, src, out)
    return report.exit_code


def _check_writable(split, src, path, force):
    if split.same_file(src, path):
        raise split.SplitError(f"{path} is the source file")
    if path.exists() and not force:
        raise split.SplitError(f"{path} already exists (it may hold your review); "
                               "pass --force to replace it")


def _dry_run(args, split, src, stem) -> int:
    report_path = args.report or args.outdir / f"{stem}_split.report.json"
    dec_path = args.outdir / f"{stem}_split.decisions.json"
    _check_writable(split, src, dec_path, args.force)
    _check_writable(split, src, report_path, force=True)
    decisions, report = split.dry_run(src, progress=_progress("detecting"))
    if dec_path.exists():                                 # only with --force
        backup = _backup_review(dec_path, decisions)
        if backup:
            print(f"backup:    {backup} (the decisions file held a review)")
    decisions.save(dec_path)
    report.save(report_path)
    spreads = report.spreads
    print(f"{src.name}: {report.pages_in} pages, {len(spreads)} spreads, "
          f"{report.pages_in - len(spreads)} singles, {len(report.flagged)} flagged "
          f"({report.seconds:.1f} s)")
    if report.flagged:
        print("flagged pages (left whole unless reviewed):")
        for p in report.flagged:
            kind = f"spread, cut {sum(p.cut) / 2:.3f}" if p.cut else "single"
            print(f"  p. {p.page:>4}  {p.confidence:.2f}  {kind}: {'; '.join(p.reasons)}")
    print(f"report:    {report_path}")
    print(f"decisions: {dec_path}")
    if args.no_sheet:
        print("review: set \"action\" (split/keep), optionally \"cut\", and \"reviewed\": true; "
              "then run again with --decisions")
        return EXIT_OK
    _write_sheet(args, split, src, stem, decisions)
    print("review: open the sheet in a browser, review the flagged pages and Save decisions; "
          "then run again with --decisions")
    return EXIT_OK


def _backup_review(path, fresh):
    """Copy the decisions file at ``path`` to <name>.<YYYYmmdd-HHMMSS>.bak.json if it holds a
    review: any action, cut or reviewed flag differs from ``fresh`` (or it does not load).
    Returns the copy's path, or None."""
    from gpdf.pipeline.report import Decisions, DecisionsError

    try:
        old = Decisions.load(path)
        same = len(old.pages) == len(fresh.pages) and all(
            (a.action, a.cut, a.reviewed) == (b.action, b.cut, b.reviewed)
            for a, b in zip(old.pages, fresh.pages))
    except DecisionsError:
        same = False
    if same:
        return None
    base, when = path.name.removesuffix(".json"), time.strftime("%Y%m%d-%H%M%S")
    backup, k = path.with_name(f"{base}.{when}.bak.json"), 2
    while backup.exists():
        backup, k = path.with_name(f"{base}.{when}-{k}.bak.json"), k + 1
    shutil.copy2(path, backup)
    return backup


def _sheet_only(args, split, src, stem) -> int:
    """Detect and write only the review sheet. With --decisions it shows that file's
    actions, cuts and reviewed flags (checked as for a real run); the detector's verdicts
    stay fresh, so "changed" still compares against the detector."""
    from gpdf.pipeline.report import Decisions

    saved = Decisions.load(args.decisions) if args.decisions else None
    decisions, report = split.dry_run(src, progress=_progress("detecting"))
    if saved:
        saved = split.load_decisions(saved, src, len(decisions.pages), args.force)
        decisions = split.with_review(decisions, saved)
    print(f"{src.name}: {report.pages_in} pages, {len(report.flagged)} flagged "
          f"({report.seconds:.1f} s)")
    if saved:
        reviewed = sum(d.reviewed for d in decisions.pages if d.detected.flagged)
        print(f"showing:   {args.decisions} ({reviewed} / {len(report.flagged)} flagged "
              "reviewed; the file is not changed)")
    _write_sheet(args, split, src, stem, decisions)
    return EXIT_OK


def _write_sheet(args, split, src, stem, decisions):
    sheet = args.outdir / f"{stem}_split.review.html"
    _check_writable(split, src, sheet, force=True)        # holds no state: always replaced
    import pymupdf

    from gpdf.pipeline import review

    t0 = time.perf_counter()
    with pymupdf.open(src) as doc:
        size = review.write_review_sheet(doc, decisions, sheet, stem, _progress("sheet"))
    print(f"sheet:     {sheet} ({size / 1e6:.1f} MB, {time.perf_counter() - t0:.1f} s)")


def _print_run(report, src, out):
    ratio = os.path.getsize(out) / max(1, os.path.getsize(src))
    print(f"wrote {out}: {report.pages_in} -> {report.pages_out} pages "
          f"({ratio:.2f}x the source size, {report.seconds:.1f} s)")
    print(f"  split {len(report.spreads)} spreads; "
          f"{report.pages_in - len(report.spreads)} pages kept whole")
    if report.unreviewed:
        print(f"  flagged, not reviewed, left whole: p. {_pages([p.page for p in report.unreviewed])}")
    for p in report.unsplittable:
        print(f"  could not split p. {p.page}: {p.left_whole}")
    if report.needs_ocr:
        print(f"  text layer dropped, needs OCR: {len(report.needs_ocr)} page(s): "
              f"p. {_pages(report.needs_ocr)}")
    links = sum(p.links_dropped for p in report.pages)
    annots = sum(p.annots_dropped for p in report.pages)
    if links or annots:
        print(f"  dropped from split pages: {links} link(s), {annots} annotation(s)")
    if report.page_labels_dropped:
        print("  page labels dropped")
    if report.exit_code == EXIT_UNREVIEWED:
        print("  some pages were left whole: review them (see --dry-run) and run again")


if __name__ == "__main__":
    sys.exit(main())
