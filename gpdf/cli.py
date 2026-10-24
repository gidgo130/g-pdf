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
    gpdf-cli export --catalogue DIR --out OUTDIR [--dry-run]
                                                    # clean, citable text per entry
    gpdf-cli structure --text DIR --out OUTDIR [--decisions FILE]
                                                    # parts, sections, authors and roles
                                                    # of every exported entry
    gpdf-cli handoff --structure DIR [--to OUTDIR]  # gpdf-handoff/1 files for the search
                                                    # app, named by the working file's sha256
    gpdf-cli backup --to DIR [--source PATH] [--always]
                                                    # copy the review decisions to DIR
                                                    # (nothing if unchanged since the last)
    gpdf-cli manual-pages --config FILE --out OUTDIR [--only SLUG ...] [--pages RANGE]
                                                    # page-stamped text of the machine shop
                                                    # manuals (separate from the book pipeline)

Exit codes: 0 ok; 2 finished, but flagged pages were left whole because they were not
reviewed (or spreads could not be split), OCR failed on some pages, the survey could
not read a file or run a trial page, apply or export refused a file, or handoff
skipped an entry; 1 error (for backup, also a copy that does not match its source).

With GPDF_BACKUP_DIR set, the commands that read your decisions (split --decisions or
--sheet-only, survey, apply, export, structure, handoff) first back up private/book-prep
there, if it changed; GPDF_NO_AUTO_BACKUP=1 turns that off.
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
    x = sub.add_parser("export", help="export clean, citable text of the confirmed entries",
                       description="Write clean text for every confirmed catalogue entry that "
                                   "is not excluded: OUTDIR/<name>.jsonl (one record per "
                                   "heading, paragraph, verse, quote or note, with its PDF "
                                   "pages and printed page labels), <name>.pages.jsonl, "
                                   "<name>.apparatus.jsonl (contents and index pages), "
                                   "<name>.txt, and OUTDIR/export.json. Sources, working "
                                   "files and the catalogue folder are only read.")
    x.add_argument("--catalogue", metavar="DIR", type=Path, required=True,
                   help="catalogue folder (catalogue.json, catalogue.decisions.json, "
                        "applied.json)")
    x.add_argument("--out", metavar="OUTDIR", type=Path, required=True,
                   help="folder for the output files (created if missing)")
    x.add_argument("--dry-run", action="store_true",
                   help="print which file each entry would be read from; write nothing")
    x.add_argument("--only", metavar="TEXT", nargs="+", default=[],
                   help="only files whose id starts with TEXT or whose path contains it")
    x.add_argument("--pages", metavar="RANGE",
                   help="only these PDF pages (1-based), e.g. 74-75,766; needs --only")
    x.add_argument("--jobs", metavar="N", type=int,
                   help="files exported in parallel (default: CPU count - 1)")
    x.add_argument("--force", action="store_true", help="replace existing output files")
    t = sub.add_parser("structure", help="find each entry's parts, sections, authors and roles",
                       description="For every entry in DIR/export.json: where each part, "
                                   "section and text starts and ends, who wrote it and what "
                                   "role it plays (front, editorial, primary, criticism, "
                                   "notes, bibliography, index, back, other), from the "
                                   "contents pages, the verified bookmarks, the running heads "
                                   "and the headings. Writes OUTDIR/<name>.sections.json and "
                                   "OUTDIR/structure.json. The text folder and the working "
                                   "files are only read.")
    t.add_argument("--text", metavar="DIR", type=Path, required=True,
                   help="the export folder (export.json, <name>.jsonl, .pages.jsonl, "
                        ".apparatus.jsonl)")
    t.add_argument("--out", metavar="OUTDIR", type=Path, required=True,
                   help="folder for the sections files (created if missing)")
    t.add_argument("--only", metavar="TEXT", nargs="+", default=[],
                   help="only entries whose id starts with TEXT or whose name contains it")
    t.add_argument("--decisions", metavar="FILE", type=Path,
                   help="your review decisions to apply over the detection (default: "
                        "OUTDIR/structure.decisions.json if it exists; never written)")
    hf = sub.add_parser("handoff", help="write the files the search app reads (gpdf-handoff/1)",
                        description="For every exported entry that is not excluded, write "
                                    "OUTDIR/<sha256 of the working file>.gpdf.jsonl (the "
                                    "entry, its sections and every unit in reading order; "
                                    "see docs/handoff-format.md) and OUTDIR/handoff.json. "
                                    "Entries whose export or working file changed are "
                                    "skipped. Nothing in OUTDIR is deleted: files no longer "
                                    "current are listed for you to remove. The structure, "
                                    "text and catalogue folders and the working files are "
                                    "only read.")
    hf.add_argument("--structure", metavar="DIR", type=Path, required=True,
                    help="the structure folder (structure.json, <name>.sections.json)")
    hf.add_argument("--to", metavar="OUTDIR", type=Path,
                    default=Path("private/book-prep/handoff"),
                    help="folder for the handoff files (default: private/book-prep/handoff; "
                         "created if missing)")
    hf.add_argument("--text", metavar="DIR", type=Path,
                    help="the export folder (default: the one structure.json names)")
    hf.add_argument("--catalogue", metavar="DIR", type=Path,
                    help="catalogue folder whose decisions say what is excluded (default: "
                         "private/book-prep/catalogue if it exists)")
    b = sub.add_parser("backup", help="copy your review decisions to a dated backup folder",
                       description="Copy the files only you can make (*.decisions.json and "
                                   "their *.bak.json backups) from the source tree to "
                                   "DIR/g-pdf-backup-YYYYmmdd-HHMMSS/, keeping their paths, "
                                   "with manifest.json. Each copy is checked against its "
                                   "source. Nothing in the source or in earlier backups is "
                                   "changed. If the files match the newest backup's "
                                   "manifest, no new folder is made.")
    b.add_argument("--to", metavar="DIR", type=Path, required=True,
                   help="backup folder, outside the source (created if missing)")
    b.add_argument("--source", metavar="PATH", type=Path, default=Path("private/book-prep"),
                   help="folder holding the decisions (default: private/book-prep)")
    b.add_argument("--always", action="store_true",
                   help="make a new dated folder even if nothing changed")
    mp = sub.add_parser("manual-pages", help="page-stamped text of born-digital equipment manuals",
                        description="For every manual in the config (slug, file, maker, model, "
                                    "doc number ...): every page as text in reading order, "
                                    "stamped with its PDF page and printed page label, with "
                                    "headings, steps, warnings, spec lists, tables and figures "
                                    "as blocks. Writes OUTDIR/<slug>.pages.jsonl, <slug>.md, "
                                    "figures/ and manual-pages.json. The source folder is only "
                                    "read. Separate from the book pipeline.")
    mp.add_argument("--config", metavar="FILE", type=Path, required=True,
                    help="manuals.json: source folder, the manuals, the excluded files")
    mp.add_argument("--out", metavar="OUTDIR", type=Path, required=True,
                    help="folder for the output files (created if missing)")
    mp.add_argument("--only", metavar="SLUG", nargs="+", default=[],
                    help="only these manuals")
    mp.add_argument("--pages", metavar="RANGE",
                    help="only these PDF pages (1-based), e.g. 30-35,40; needs --only with one slug")
    mp.add_argument("--dpi", type=int, default=150, help="figure crops (default 150)")
    mp.add_argument("--dry-run", action="store_true",
                    help="print which files would be processed; write nothing")
    return p


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = build_parser()
    args = parser.parse_args(argv)
    _auto_backup(args)
    if args.command == "ocr":
        return _ocr_main(args, parser)
    if args.command == "survey":
        return _survey_main(args, parser)
    if args.command == "apply":
        return _apply_main(args, parser)
    if args.command == "export":
        return _export_main(args, parser)
    if args.command == "backup":
        return _backup_main(args)
    if args.command == "structure":
        return _structure_main(args)
    if args.command == "handoff":
        return _handoff_main(args)
    if args.command == "manual-pages":
        return _manual_pages_main(args, parser)
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


def _export_main(args, parser) -> int:
    if args.jobs is not None and args.jobs < 1:
        parser.error("--jobs must be at least 1")
    if args.pages and not args.only:
        parser.error("--pages needs --only (it is for quick checks of one file)")
    try:
        from gpdf.pipeline import catalogue, export
    except ModuleNotFoundError as e:
        if e.name in ("numpy", "PIL", "wordfreq"):
            print(f"gpdf-cli: {PREP_HINT}", file=sys.stderr)
            return EXIT_ERROR
        raise
    try:
        items = export.plan(args.catalogue, args.out, args.only, args.pages, args.force)
    except (export.ExportError, catalogue.CatalogueError, OSError) as e:
        print(f"gpdf-cli: error: {e}", file=sys.stderr)
        return EXIT_ERROR
    t0 = time.perf_counter()
    todo = [it for it in items if it.writes]
    done, errors = [], 0
    if args.dry_run:
        for it in todo:
            print(f"would export {_short(it.rel, 60)} from the {it.input_from} "
                  f"{Path(it.input).name}" + (f", pages {args.pages}" if it.pages else "")
                  + (" [no dictionary fixes]" if it.nodict else ""))
    elif todo:
        args.out.mkdir(parents=True, exist_ok=True)
        jobs = max(1, min(args.jobs or (os.cpu_count() or 2) - 1, len(todo)))
        for it, result in _run_exports(export, todo, args.out, jobs):
            if isinstance(result, Exception):
                print(f"error    {_short(it.rel, 60)}: {type(result).__name__}: {result}",
                      file=sys.stderr)
                errors += 1
                continue
            done.append((it, result))
            _print_exported(it, result)
        if done:
            log = export.write_log(args.out, done, {"pages": args.pages, "force": args.force})
            print(f"recorded in {log}")
    for it in items:
        if it.excluded:
            print(f"excluded {_short(it.rel, 60)}")
    refused = [it for it in items if it.refused]
    for it in refused:
        print(f"refused  {_short(it.rel, 60)}: {it.refused}")
    flagged = [it for it in todo if it.nodict]
    if flagged:
        print("no dictionary fixes: " + ", ".join(_short(it.rel, 40) for it in flagged))
    excluded = sum(it.excluded for it in items)
    if args.dry_run:
        print(f"dry run: {len(todo)} file(s) would be exported, {excluded} excluded, "
              f"{len(refused)} refused; nothing written")
    else:
        print(f"exported {len(done)} file(s), {excluded} excluded, {len(refused)} refused, "
              f"{errors} error(s) ({time.perf_counter() - t0:.1f} s)")
    if errors:
        return EXIT_ERROR
    return EXIT_UNREVIEWED if refused else EXIT_OK


def _backup_main(args) -> int:
    from gpdf.pipeline import backup

    try:
        res = backup.run(args.source, args.to, always=args.always)
    except (backup.BackupError, OSError) as e:
        print(f"gpdf-cli: error: {e}", file=sys.stderr)
        return EXIT_ERROR
    if res.unchanged:
        print(f"unchanged since {res.dest.name}: no new backup (pass --always to make one)")
        return EXIT_OK
    print(f"copied {len(res.files)} file(s), {res.total / 1e3:.1f} KB:")
    for f in res.files:
        print(f"  {f['path']}")
    print(f"to:        {res.dest}")
    print(f"manifest:  {res.dest / backup.MANIFEST}")
    for rel in res.differs:
        print(f"gpdf-cli: error: the copy of {rel} differs from its source", file=sys.stderr)
    if res.differs:
        return EXIT_ERROR
    print("checked:   every copy matches its source (sha256)")
    print(f"restore:   copy the files back to the same relative paths under {args.source}")
    return EXIT_OK


READS_DECISIONS = ("survey", "apply", "export", "structure", "handoff")
BACKUP_TIP = ("tip: set GPDF_BACKUP_DIR to back up your review decisions automatically "
              "(README: Backing up your review work)")


def _auto_backup(args):
    """Before a command that reads the owner's decisions: back up private/book-prep to
    $GPDF_BACKUP_DIR (nothing if unchanged). One line on stderr; never stops the command.
    GPDF_NO_AUTO_BACKUP=1 turns it off."""
    reads = args.command in READS_DECISIONS or (
        args.command == "split" and (args.decisions or args.sheet_only))
    if not reads or os.environ.get("GPDF_NO_AUTO_BACKUP") == "1":
        return
    from gpdf.pipeline import backup

    if not backup.DEFAULT_SOURCE.is_dir():
        return
    to = os.environ.get("GPDF_BACKUP_DIR")
    if not to:
        print(BACKUP_TIP, file=sys.stderr)
        return
    try:
        res = backup.run(backup.DEFAULT_SOURCE, Path(to))
    except Exception as e:  # noqa: BLE001 - the backup must never block the pipeline
        print(f"gpdf-cli: warning: automatic backup failed ({e}); continuing without it",
              file=sys.stderr)
        return
    if res.differs:
        print(f"gpdf-cli: warning: automatic backup failed (the copy of {res.differs[0]} "
              f"differs from its source, in {res.dest}); continuing without it",
              file=sys.stderr)
    elif res.unchanged:
        print(f"backup: unchanged since {res.dest.name}", file=sys.stderr)
    else:
        print(f"backup: {len(res.files)} files -> {res.dest}", file=sys.stderr)


def _structure_main(args) -> int:
    from gpdf.pipeline import sections

    try:
        res = sections.run(args.text, args.out, args.only, args.decisions)
    except (sections.StructureError, OSError) as e:
        print(f"gpdf-cli: error: {e}", file=sys.stderr)
        return EXIT_ERROR
    print(sections.report(res))
    return EXIT_ERROR if res.errors else EXIT_OK


def _handoff_main(args) -> int:
    from gpdf.pipeline import handoff

    try:
        res = handoff.run(args.structure, args.to, args.text, args.catalogue)
    except (handoff.HandoffError, OSError) as e:
        print(f"gpdf-cli: error: {e}", file=sys.stderr)
        return EXIT_ERROR
    print(handoff.report(res))
    return EXIT_UNREVIEWED if res.skipped else EXIT_OK


def _manual_pages_main(args, parser) -> int:
    from gpdf.pipeline import manuals

    if args.dpi < 36:
        parser.error("--dpi must be at least 36")
    try:
        res = manuals.run(args.config, args.out, args.only, args.pages, args.dpi, args.dry_run)
    except (manuals.ManualsError, OSError) as e:
        print(f"gpdf-cli: error: {e}", file=sys.stderr)
        return EXIT_ERROR
    print(manuals.report(res))
    return EXIT_ERROR if res.errors else EXIT_OK


def _run_exports(export, todo, out, jobs):
    """(item, counts or the exception) per item, in parallel processes with jobs > 1."""
    work = [(it, export.job_for(it, out)) for it in todo]
    if jobs == 1:
        for it, job in work:
            try:
                yield it, export.run_job(job)
            except Exception as e:  # noqa: BLE001 - reported per file, the rest go on
                yield it, e
        return
    from concurrent.futures import ProcessPoolExecutor, as_completed

    with ProcessPoolExecutor(max_workers=jobs) as pool:
        futures = {pool.submit(export.run_job, job): it for it, job in work}
        for fut in as_completed(futures):
            try:
                yield futures[fut], fut.result()
            except Exception as e:  # noqa: BLE001 - reported per file, the rest go on
                yield futures[fut], e


def _print_exported(it, c):
    units = c.get("units", {})
    kinds = ", ".join(f"{k} {units[k]}" for k in ("heading", "prose", "verse", "quote", "note")
                      if units.get(k))
    labels = c.get("labels", {})
    print(f"exported {_short(it.rel, 60)}: {c.get('pages') or 'txt'} pages; {kinds}; labels "
          f"found {labels.get('found', 0)}, inferred {labels.get('inferred', 0)}; "
          f"{c.get('seconds', 0):.1f} s")
    print(f"  from the {it.input_from} {it.input}")


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
