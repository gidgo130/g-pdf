# manual-pages: page records of the machine shop manuals

`gpdf-cli manual-pages` writes, for each born-digital equipment manual listed in
`manuals.json`, every page as text in reading order with its PDF page and printed
page label. The files are the base that excerpts are copied from and checked
against: a quote must appear on the cited page of these files. Nothing is
paraphrased or corrected (no word lists, no dictionary); the only changes to the
characters are the ones listed under *Characters*.

This command is separate from the book pipeline (`survey`, `apply`, `export`,
`structure`, `handoff`) and does not read or write its folders.

## Files

```
OUTDIR/<slug>.pages.jsonl      one file record, then one record per page
OUTDIR/<slug>.md               the same, readable
OUTDIR/figures/<slug>_pdf030_f1.png
OUTDIR/manual-pages.json       per-manual counts, the excluded files
```

Files are UTF-8; `.jsonl` is one JSON object per line. Every output is written
to a temporary file and renamed, so a reader never sees half a file. The source
folder is only read.

## `manuals.json` (the config, not committed)

```json
{
 "source": "C:\\...\\machine shop manuals",
 "manuals": [
  {"slug": "grizzly-g0602", "file": "g0602_m.pdf", "maker": "Grizzly",
   "model": "G0602, G0752", "doctype": "Owner's Manual", "doc_no": "#BL15556",
   "rev": "V4.10.25", "date": "October 2025", "control": null,
   "machines": ["manual lathe"], "pages": null, "notes": ""}
 ],
 "excluded": [{"file": "Mill operators 2019.pdf", "reason": "superseded by Rev U"}]
}
```

`pages` is a PDF page range (`"1-4"`, 1-based) or `null` for all. `control`
is the Haas control generation (Classic, NGC) or `null`.

## The file record (first line of `<slug>.pages.jsonl`)

```json
{"type": "file", "manual": {...the manuals.json entry...},
 "sha256": "<sha256 of the PDF>", "pages": 96, "processed": [1, 2, ...],
 "made": "2026-10-23T21:00:00+00:00", "made_with": "g-pdf 0.2.0", "dpi": 150}
```

## The page record

```json
{"pdf": 30, "label": "28", "label_source": "pdf_label", "columns": 2,
 "blocks": [...], "stripped": ["-28-", "Model G0602/Z, G0752/Z (Mfd. Since 05/24)"],
 "warnings": [], "dropped_chars": 0, "contents": true}
```

| field | meaning |
| --- | --- |
| `pdf` | PDF page number, 1-based. Cite with it; it never changes. |
| `label` | the printed page label, or `null` when the page shows none (covers, blank pages) |
| `label_source` | `pdf_label`: the PDF's own page label (an InDesign `Sec1:` prefix is dropped); `text`: a number in the head/foot band (or a lone number in the side margin) that belongs to a run: label minus PDF index constant over >= 3 consecutive pages, chapter-page counters like `4-1` included; `inferred`: a single gap inside such a run, filled; `none` |
| `columns` | 1 or 2 (a gutter was found) |
| `blocks` | the page's content in reading order, see below |
| `stripped` | running heads and feet removed from the flow: band lines whose text (digits masked) repeats on >= 25% of the manual's pages, lines in the same slot that repeat, and lines on the same baseline as one of those |
| `warnings` | per-page notes, see below |
| `dropped_chars` | glyphs with no Unicode meaning dropped from this page (C0/C1 controls other than whitespace, private use, unassigned, U+FFFD): key icons, safety symbols, NUL padding, bell bullets |
| `contents` | only when true: the page's leader lists are page numbers in rising order (a contents or index page) and its spec blocks are of kind `contents` |

Reading order: head/foot, figure, table and caption lines leave the flow; the
page is cut into horizontal bands at full-width elements (a line, table or
figure that crosses the gutter); inside a band the left column is read top to
bottom, then the right. Each figure and table is placed at its top edge in its
column.

## Blocks

Every block has `kind`, `bbox` (`[x0, y0, x1, y1]` in PDF points, origin top
left) and, except figures, `text`. Lines join into a block with a space. A soft
hyphen (U+00AD) at a line end joins the word; a hyphen at a line end joins
without a space only when the next line starts lowercase (so `CON-` / `TACT`
stays `CON- TACT`). Case, punctuation, units, key names and G/M-codes are kept
as printed.

| kind | fields | what it is |
| --- | --- | --- |
| `heading` | `text` | a line set >= 1.15 x the body size, or a short bold line (<= 90 characters) on its own; consecutive heading lines of one size join |
| `para` | `text` | running text; a new block at a vertical gap > 1.5 x the line pitch |
| `step` | `n`, `text` | a numbered step: `1.` or `1)` at the line start (also when the number sits alone on its line and the text follows on the next); `n` is the manual's own number as a string; continuation lines are indented at least to the text |
| `bullet` | `marker`, `text` | a line starting with a bullet character or a dash |
| `warning` | `level`, `text` | a block starting WARNING, CAUTION, DANGER, NOTICE, NOTE or IMPORTANT (with or without `!` or `:`, or the word alone on a line and the box text under it); it runs to a gap > 1.5 x the pitch or to a heading, step or spec line; `level` is the word, upper case; `text` keeps the word as printed |
| `specs` | `items`, `text` | consecutive lines `label <dot leader> value` (4+ dots or dot-space); `items` is `[{"label": ..., "values": [...]}]`, with every non-empty value when a line has two leaders (`Stock Number ... 321357A ... 321359A`); `text` is the same as a Markdown table (`Item`, `Value`, `Value 2`). Tools-needed lists are specs too |
| `contents` | as `specs` | the specs of a contents page (see `contents` above) |
| `table` | `rows`, `cols`, `text` | a ruled table found by PyMuPDF (>= 2 rows and columns, outside the head/foot band); `text` is its Markdown; the cells' lines are not in the flow |
| `figure` | `file`, `caption`, `labels`, `bbox` | an image or drawing region (images > 2000 pt², drawing clusters > 4000 pt², merged when overlapping or within 6 pt; thin bands and regions that are a table or a box around text are not figures). `file` is `figures/<slug>_pdf030_f1.png`, the region plus 4 pt cropped at `--dpi`, numbered in reading order. `caption` is the nearest `Figure n` line within 40 pt above or below (with its continuation lines) or `null`. `labels` are the callout labels: short lines (<= 8 words) inside or touching the figure, stacked lines joined (`Inside Set`) |

## Warnings

| kind | fields | meaning |
| --- | --- | --- |
| `overlap` | `kept`, `dropped`, `bbox` | two runs on one baseline whose boxes overlapped by > 30% of the shorter: a hidden span under body text; the body (not a caption; else the one drawn last) was kept |
| `duplicate` | `kept`, `dropped`, `bbox` | the same text drawn twice at one place; one copy kept |
| `glued_text` | `text`, `bbox` | a `Figure n` line in the flow with words run together (`workpiecThe`): a caption printed over text in the manual itself |
| `rotated` | `rotation` | the page has a /Rotate; coordinates may not match the display |

## `<slug>.md`

A header from the manuals.json entry (and the sha256), then per page
`## pdf 30 · p. 28` (`no label` when there is none; `(contents)` when marked)
and its blocks: headings as `###`, steps `1. ...` with the manual's numbers,
bullets `- ...`, warnings `> **WARNING:** ...`, specs and tables as Markdown
tables, figures as `[Figure: <caption or "no caption"> — figures/<file>;
labels: a, b, c]`, paragraphs as they are.

## `manual-pages.json`

Per manual: `pdf_pages`, `pages` (processed), `labels` (`text`, `pdf_label`,
`inferred`, `none`), `figures` (`count`, `mb`), `tables`, `specs`, `warnings`
(warning blocks), `steps`, `headings`, `paras`, `chars`, `dropped_chars`,
`stripped_lines`, `overlap_warnings` (pages with one), `pages_with_warnings`,
`two_column_pages`, `seconds`; plus `excluded` (from the config), `errors`,
`dpi` and the run time. A run with `--only` or `--pages` updates only those
manuals' entries.
