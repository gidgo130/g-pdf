# manual-check: page-cited extracts of the machine shop manuals

`gpdf-cli manual-check` proves that an extract quotes what the cited pages say: every
verbatim block is on its cited page, every number in a summary is too, every table row is a
row of the page's table, and every page tag carries the page's printed label. The pages are
the layer-1 records written by `manual-pages` (see [manual-pages.md](manual-pages.md)) and
the source PDF's own `page.get_text()`.

```
gpdf-cli manual-check --extracts "<output folder>" --pages private\machine-shop\pages ^
    --config private\machine-shop\manuals.json --report private\machine-shop\check [FILE ...]
```

Without FILE, every `.md` in `--extracts` is checked. It writes `check.json` and `check.md`
(per file: pass or fail, each error and warning with its line number, the quote and the
cited page) to `--report`, which must not be inside the extracts folder. The extracts, the
page records, the config and the PDFs are only read. Exit code 1 when a file fails (an
error); warnings do not fail a file.

The rules below are also the writing rules for the extracts.

## 1. File name

`MAKER_MODEL_DOCTYPE_DocNo_Rev_topic.md`, six fields joined by `_`:

- maker, model, doctype: upper-case letters and digits, words joined by `-`
  (`HAAS`, `MINI-MILL`, `OPERATOR-ADDENDUM`);
- DocNo: the document number, letters (either case), digits and `.`, parts joined by `-`
  (`96-8000`, `ES0205l`); `NA` when the manual has none;
- Rev: `Rev` and the revision without spaces (`RevN`, `RevU`, `Rev06-2012`, `RevNA` when
  there is none);
- topic: lower case, words joined by `-` (`offsets`, `power-up-and-homing`).

Example: `HAAS_MILL_OPERATOR_96-8000_RevN_offsets.md`. `00_MANIFEST.md` and
`01_FINDINGS.md` are exempt.

## 2. Header block

At the top (after an optional `# ` title line), these lines in this order, each with a
value; blank lines between them, a `- ` prefix and bold keys are allowed:

```
Title: ...
Manufacturer: ...
Document number / revision / date: ...
Control generation (Haas): Classic | NGC | both | unknown | n/a   (a remark may follow)
Models and years covered: ...
Source PDF filename: ...
Pages extracted (printed page numbers): ...
Notes on applicability to our machines: ...
```

`Source PDF filename` is exactly the `file` of one `manuals.json` entry (curly apostrophes
included): one source manual per extract file. That entry's slug names the page records
and the PDF every quote of the file is checked against.

## 3. Page tags

Every content block (paragraph, list item, table, warning, figure line, summary, code
block) carries a tag:

| tag | when |
| --- | --- |
| `[p. 32 / pdf 33]` | one page; `p.` is the printed label, `pdf` the PDF page (1-based) |
| `[p. 32–33 / pdf 33–34]` | a range; the printed range is split at an en dash `–` (a hyphen is part of labels such as `3-28` or `SC-2`) |
| `[pdf 5]` | a page with no printed label (covers, blank pages) |
| `[haas-mill-2006 p. 32 / pdf 33]` | in `01_FINDINGS.md`: the manual's slug first. In an extract, a slug, if given, must be the file's own |

The printed label must equal the layer-1 `label` of that pdf page; a page with a label must
be tagged with it, and `[pdf N]` is only for pages without one. A label that layer 1
`inferred` passes with a warning: check it on the page.

Where a block gets its tag:

- its own text (anywhere in the block; all its tags together are the cited pages);
- a list item without a tag takes the latest tag of its list; the first item may take the
  tag of the block directly above (a tag-only line, a heading with a tag, or a tagged
  paragraph);
- a table takes a tag in its header row or the block directly above; a body row may carry
  its own tag (a table that runs onto the next page);
- a code block takes the tag of the block directly above (`[p. 120 / pdf 122]` on its own
  line is the usual form).

Headings (`#`) need no tag. A block that holds only the writer's remarks
(`[GAP: ...]`, `[NOTE: ...]`) needs none either.

## 4. Verbatim by default

Every content block that is not a summary is verbatim: steps, bullets, warnings, specs,
table cells, key names, G/M-codes, plain paragraphs. Before matching, these are removed:
page tags, the writer's remarks `[APPLIES TO: ...]`, `[OPTION: ...]`, `[MODEL: ...]`,
`[GAP: ...]`, `[NOTE: ...]` (no `]` inside them), Markdown emphasis and code backticks,
and a list item's own marker or number (`1.`, `-`). A blockquote's `>` is not part of the
quote; a warning's leading level word (`WARNING`, `CAUTION`, `DANGER`, `NOTICE`, `NOTE`,
`IMPORTANT`, with or without `:` or `!`) may differ from the page (layer 1's `.md` writes
`**WARNING:**` for every warning box).

`…` (one character, U+2026) marks an omission: each fragment must be on the page, in the
order written. Three periods `...` are text, not an omission.

A summary is a paragraph or list item starting `*Summary:*`. Its own words are not checked;
its numbers are (rule 7), and so is any text inside curly double quotes `“...”` (checked as
a verbatim quote). Straight `"` is left alone (inch marks).

A sentence that reads oddly because layer 1 dropped a key icon gets the writer's remark
`[NOTE: key icon on the page: <name>]`; the icon's name never goes inside the quote.

## 5. Matching

Both sides are normalised: NFKC, curly quotes and apostrophes to straight, every dash to
`-`, soft hyphens removed, Markdown escapes, emphasis and `<br>` removed, whitespace
collapsed, a line-end split such as `CON- TACT` joined, a number split after its hyphen
(`Figure 7-` / `6`) closed up to `7-6`; case-sensitive. A hyphen between
two letters is also tried without it on both sides, so a word the page breaks at a line end
(`self-` / `contained`) matches either way.

A quote passes when it is a substring of the cited pages' text, read three ways: the
layer-1 records (block texts, table cells, spec labels and values, captions, figure
labels), the layer-1 printed `lines` of each block, or the PDF's own `page.get_text()`
(the PDF named in the header, from the config's source folder, read-only). The cited pages
of a range are joined in page order, so a quote may run from one page onto the next.

## 6. Tables: rows, not just cells

The header row (the first row) may be reworded into one header line; each of its words must
still be on the cited page. Every body row is then checked:

- When a cited page has layer-1 tables, the row must be one row of one of them, column by
  column: a non-empty layer-1 cell must equal the extract cell; an empty one (`""`) must be
  empty in the extract; a merged cell (`null`) accepts an empty extract cell, or the text of
  the cell it continues: the nearest cell above it in its column, or to its left in its row,
  that is not merged (when that cell is not empty).
- The extract keeps the source table's columns in order. A column may be left out only
  where it is empty (or merged) in that row; a row with more cells than the source table
  fails.
- When no cited page has a layer-1 table (an unruled table rebuilt from the page image),
  each cell of 3 or more characters is checked on its own as a quote, and the table gets the
  warning `unanchored table`.

A table cell is plain text, as layer 1 writes it: a row gets the warning `table cell` when a
cell holds `<br>`, emphasis markup (`**`, `_..._`), a lowercase letter glued to `[`
(`after[POWER UP]`), or a lowercase letter glued to an upper-case run that the cited page
shows as two words (`inSETUP:JOG` where the page has `in SETUP:JOG`).

A table that cannot be transcribed faithfully (icon tables, damaged extraction) is written
`[Table: <description>, not transcribed: see the page] [tag]`: only its tag is checked.

In the extracts, a merged cell repeats the spanning value in each row it covers, as the page
shows it; the checker accepts that, and the empty cell too.

## 7. Numbers in summaries

Every number in a summary (`6,000`, `0.0005`, `.0005`, `3/4`, `1.5`, `20`) must be on a
cited page as a number of its own (not as part of a longer one: `20` is not found in `200`
or `2.05`). Thousands separators are ignored (`6,000` = `6000`), and a leading zero may be
written or left out (`0.0005` = `.0005`). Digits glued to letters (`G54`, `CAT40`) are not
numbers; write them in a verbatim block or in “curly quotes” to have them checked.

## 8. Figures

```
[Figure: <one-line description> — figures/<file>.png] [p. 140 / pdf 142]
```

The file must exist in `<extracts>/figures/`, and the `pdfNNN` in its name must be one of
the cited pdf pages. The description is the writer's own and is not checked. Figures come
from `private/machine-shop/pages/figures` (named `<slug>_pdf142_f1.png` by `manual-pages`).

## 9. Size

A warning above 45,000 characters, an error above 60,000 (about 15-20 printed pages).

## 10. Manifest

`00_MANIFEST.md` must list every extract file in the folder (by file name anywhere in its
text) and no file that is not there; both are warnings. No manifest in the folder is a
warning on a whole-folder run.

## `01_FINDINGS.md`

Follows rules 3-8. Every tag names the manual (`[haas-mill-2006 p. 32 / pdf 33]`), and the
quotes and numbers of a block are checked against the pages its tags cite (of each manual
named, joined). Answers are usually summaries; "not found" is a remark:
`[GAP: not found in haas-mill-2006, haas-tm-addendum-2010]`.

## `check.json`

```json
{"made": "...", "made_with": "g-pdf 0.2.0", "extracts": "...", "pages": "...", "config": "...",
 "summary": "12 file(s): 12 pass, 0 fail; 0 error(s), 3 warning(s)",
 "folder_warnings": [],
 "files": [{"file": "...", "status": "pass", "chars": 23110, "source": "haas-mill-2006",
            "errors": [{"line": 42, "rule": "verbatim", "message": "not on the cited page",
                        "quote": "...", "cite": "[p. 32 / pdf 33]"}],
            "warnings": []}]}
```

`rule` is one of `name`, `header`, `tag`, `verbatim`, `number`, `table`, `figure`, `size`,
`manifest`, `source` (the PDF was not found: quotes matched against layer 1 only).
