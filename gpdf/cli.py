# SPDX-License-Identifier: AGPL-3.0-or-later
"""gpdf-cli: book prep from the command line. No Qt (it must not import gpdf.core/gpdf.ui).

    gpdf-cli split book.pdf -o OUTDIR --dry-run     # detect; write report, decisions file
                                                    # and the review sheet (.review.html)
    gpdf-cli split book.pdf -o OUTDIR --decisions OUTDIR/book_split.decisions.json

Exit codes: 0 ok; 2 finished, but flagged pages were left whole because they were not
reviewed (or spreads could not be split); 1 error.
"""

from __future__ import annotations

import argparse
import os
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
    return p


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
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
    report = split.split_document(src, out, decisions=args.decisions,
                                  accept_flagged=args.accept_flagged, force=args.force,
                                  progress=_progress("pages"))
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
    sheet = args.outdir / f"{stem}_split.review.html"
    _check_writable(split, src, sheet, force=True)        # holds no state: always replaced
    import pymupdf

    from gpdf.pipeline import review

    t0 = time.perf_counter()
    with pymupdf.open(src) as doc:
        size = review.write_review_sheet(doc, decisions, sheet, stem, _progress("sheet"))
    print(f"sheet:     {sheet} ({size / 1e6:.1f} MB, {time.perf_counter() - t0:.1f} s)")
    print("review: open the sheet in a browser, review the flagged pages and Save decisions; "
          "then run again with --decisions")
    return EXIT_OK


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
