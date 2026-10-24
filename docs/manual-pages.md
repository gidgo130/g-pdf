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
| `label_source` | `pdf_label`: the PDF's own page label; a hex UTF-16 prefix is decoded, an InDesign section prefix `SecN:` is dropped (`<FEFF0053006500630031003A>1` = `1`), any other prefix is part of the label (`<FEFF00530043002D>2` = `SC-2`, `<FEFF0031002D>7` = `1-7`); `text`: a number in the head/foot band (or a lone number in the side margin) that belongs to a run: label minus PDF index constant over >= 3 consecutive pages, chapter-page counters like `4-1` included; `inferred`: a single gap inside such a run, filled; `none` |
| `columns` | 1 or 2 (a gutter was found) |
| `blocks` | the page's content in reading order, see below |
| `stripped` | running heads and feet removed from the flow: band lines whose text (digits masked) repeats on >= 25% of the manual's pages, lines in the same slot that repeat, and lines on the same baseline as one of those. The head/foot band is the top and bottom 10% of the page height (Hypertherm's feet sit 8% from the bottom); the side margin for lone page numbers is 7% of the width. The repetition rule is what keeps body text in the band: a line that repeats on fewer pages stays (Hypertherm's `SC-2 Safety and compliance`), and a cover page's document number or date that matches a running foot is stripped there too |
| `warnings` | per-page notes, see below |
| `dropped_chars` | glyphs with no Unicode meaning dropped from this page (C0/C1 controls other than `\n` and `\t`, private use, unassigned, U+FFFD): key icons, safety symbols, NUL padding, bell bullets. A C1 control (U+0080-U+009F) is first read as the cp1252 character of that byte and kept (mini_mill.pdf sets `”` as 0x94, `’` as 0x92, `•` as 0x95); only the five code points cp1252 leaves unmapped stay junk. Each glyph of the page is counted once, vertical text included. The filter applies to everything written: block text, `lines`, `items`, table cells, captions, labels and stripped lines (a tab or line-break glyph inside a table cell becomes a space; `\x1c`-`\x1f` and `\r` break a word and are counted) |
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

Every block built from text lines (all kinds but `table` and `figure`) also has
`lines`: its lines as printed, after the glyph filter and before joining, in
reading order (a step's number and a bullet's marker included; a soft hyphen
at a line end stays as U+00AD). Quote line breaks from `lines`, not `text`.

| kind | fields | what it is |
| --- | --- | --- |
| `heading` | `text` | a line set >= 1.15 x the body size, or a short bold line (<= 90 characters) on its own; consecutive heading lines of one size join |
| `para` | `text` | running text; a new block at a vertical gap > 1.5 x the line pitch |
| `step` | `n`, `text` | a numbered step: `1.` or `1)` at the line start (also when the number sits alone on its line and the text follows on the next); `n` is the manual's own number as a string; continuation lines are indented at least to the text |
| `bullet` | `marker`, `text` | a line starting with a bullet character or a dash and a space; or a line whose first glyph is from a symbol font (font name containing Wingdings, Symbol, ZapfDingbats or PSOsym) and is followed by a space, `marker` `•` (Mastercam's Wingdings `l`, Haas's Symbol `♦`; `lines` keeps the glyph as extracted). Symbol glyphs dropped as junk (Miller's PSOsym icons) do not make a bullet |
| `warning` | `level`, `text` | a block starting WARNING, CAUTION, DANGER, NOTICE, NOTE or IMPORTANT (with or without `!` or `:`, or the word alone on a line and the box text under it); it runs to a gap > 1.5 x the pitch or to a heading, step or spec line; `level` is the word, upper case; `text` keeps the word as printed |
| `specs` | `items`, `text` | consecutive lines `label <dot leader> value` (4+ dots or dot-space); `items` is `[{"label": ..., "values": [...]}]`, with every non-empty value when a line has two leaders (`Stock Number ... 321357A ... 321359A`); `text` is the same as a Markdown table (`Item`, `Value`, `Value 2`). Tools-needed lists are specs too |
| `contents` | as `specs` | the specs of a contents page (see `contents` above) |
| `table` | `rows`, `cols`, `text`, `cells`, `merged` | a ruled table found by PyMuPDF (>= 2 rows and columns, outside the head/foot band); the cells' lines are not in the flow. `cells` is the list of rows (the header row included unless PyMuPDF found the header above the table): a string per cell, built from the cell's characters as plain text (no emphasis markup, no `<br>`; a space where the page has a word gap, as in body text, or a line break inside the cell), `""` for an empty cell, `null` for a cell covered by a merged cell to its left or above. `merged` is `true` only when the table has a `null` cell. `text` is its Markdown with merged cells written empty: a value printed once in a spanning cell appears once (it is never repeated into the cells it covers, so a row can read short of values the page shows for it); empty header names are `ColN` |
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
labels: a, b, c]`, paragraphs joined as they are.

A `para` that is an NC listing is a fenced code block instead, one printed line
per line (the `.jsonl` keeps it as a `para` with its `lines`). It is a listing
when it has >= 2 lines and >= 60% of them are code or comment lines:

- code: starts with `%`, a whole NC word (`O1234`, `N10`, `G00`, `M30`, `T1`,
  `X.565`, `X 1.115`, glued `N11G98` or `N10#104=`; not `G0752/G0752Z`), an axis
  expression (`X[#105]`) or a macro assignment (`#3000=1`, `#[#2+#19]=#22`),
  also after a line number (`(2) G00 X1.1`);
- comment: starts `(` and an upper-case letter, digit or `#`. A block of
  comments alone is a listing only when every comment is closed.

## `manual-pages.json`

Per manual: `pdf_pages`, `pages` (processed), `labels` (`text`, `pdf_label`,
`inferred`, `none`), `figures` (`count`, `mb`), `tables`, `specs`, `warnings`
(warning blocks), `steps`, `headings`, `paras`, `chars`, `dropped_chars`,
`stripped_lines`, `overlap_warnings` (pages with one), `pages_with_warnings`,
`two_column_pages`, `seconds`; plus `excluded` (from the config), `errors`,
`dpi` and the run time. A run with `--only` or `--pages` updates only those
manuals' entries.
