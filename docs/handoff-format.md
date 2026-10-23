# The g-pdf handoff format (`gpdf-handoff/1`)

`gpdf-cli handoff` writes one file per prepared book or article for the search
app to read. Each file holds the text of one PDF (or `.txt`), divided into
sections, with page references for citing. g-pdf does not chunk the text. The
search app does that.

## Files

```
OUTDIR/<sha256>.gpdf.jsonl     one per entry
OUTDIR/handoff.json            index of the files written by the last run
```

- `<sha256>` is the SHA-256 (lowercase hex) of the exact PDF or `.txt` file the
  owner uploads to the search app: the working file the text was exported
  from. Use it to match an upload to its handoff file.
- Files are UTF-8 JSON Lines: one JSON object per line, `\n` line ends.
- Files are replaced whole (written to a temporary file, then renamed), so a
  reader never sees half a file.
- g-pdf never deletes anything in OUTDIR. When a working file changes, its new
  hash gets a new file. The old file stays until the owner removes it, and
  `handoff.json` lists it under `not_current`.

## `handoff.json`

```json
{
 "format": "gpdf-handoff/1",
 "made_with": "g-pdf 0.2.0",
 "made": "2026-10-22T16:05:11-05:00",
 "files": {
  "3f2a…c9": {
   "file": "3f2a…c9.gpdf.jsonl",
   "file_name": "ensayos-de-prueba.pdf",
   "entry_stem": "ensayos-de-prueba",
   "title": "Ensayos de prueba",
   "counts": {"sections": 4, "paras": 8, "kinds": {"heading": 3, "prose": 2, "note": 1, "quote": 1, "verse": 1},
              "chars": 1234, "pages": 12},
   "made": "2026-10-22T16:05:11-05:00",
   "metadata_confirmed": true,
   "structure_confirmed": true
  }
 },
 "not_current": ["9b1e…04.gpdf.jsonl"],
 "skipped": [{"stem": "otro-libro", "why": "the export changed since structure ran: …"}],
 "excluded": []
}
```

`files` lists only the files written by the last run. A file under
`not_current` has a hash that no current working file has: ignore it. A
`skipped` entry was not rewritten. If an older file with its hash is still in
OUTDIR, that file is from an earlier run.

## Lines of a `.gpdf.jsonl` file

The lines come in this order: one `file` line, then all `section` lines in
document order, then one `para` line per unit of text in reading order.

### `file` (first line)

| field | type | meaning |
| --- | --- | --- |
| `type` | `"file"` | |
| `schema` | `"gpdf-handoff/1"` | format version; a reader should refuse others |
| `sha256` | string | hash of the working file (also the file name) |
| `file_name` | string | the working file's name |
| `pages` | int or null | PDF page count; null for a `.txt` |
| `title`, `author`, `year`, `publisher` | string or null | the book's metadata (`author` may be an editor) |
| `metadata_confirmed` | bool | the owner checked and confirmed the four fields above |
| `lang` | string or null | main language (ISO 639-1, e.g. `"es"`) |
| `structure_confirmed` | bool | the owner reviewed this entry's sections: decisions exist for it, and none is stale or unmatched |
| `made_with` | string | e.g. `"g-pdf 0.2.0"` |
| `made` | string | ISO 8601 time the file was written |
| `apparatus_pages` | list of int | PDF pages of contents and index pages, which are not in the text |

### `section`

| field | type | meaning |
| --- | --- | --- |
| `type` | `"section"` | |
| `id` | string | `"s001"`, `"s002"`, … unique within the file |
| `parent` | string or null | id of the enclosing section |
| `level` | 1, 2 or 3 | 1 part, 2 section (essay, text, chapter), 3 subsection |
| `title` | string | |
| `author` | string or null | the author of this text, e.g. the critic who wrote an essay in an anthology or the author of a manifesto. Null when it is the book's author or unknown |
| `role` | string | see [Roles](#roles) |
| `first`, `last` | int | the first and last `seq` it covers (inclusive) |
| `pdf_pages` | [int, int] or null | first and last PDF page (1-based) |
| `printed` | [string, string] or null | first and last printed page label (`"xi"`, `"257"`) |
| `lang` | string or null | main language of its text |
| `confidence` | `"high"`, `"medium"` or `"low"` | how sure the detection is (owner edits are `"high"`) |

### `para`

| field | type | meaning |
| --- | --- | --- |
| `type` | `"para"` | |
| `seq` | int | 1, 2, 3, … in reading order, with no gaps |
| `section` | string | id of the deepest section that holds it |
| `kind` | string | `heading`, `prose`, `verse`, `quote` or `note` |
| `text` | string | as exported: footnote markers as `[^n]`, verse lines joined by `\n` |
| `pdf_pages` | [int, int] | first and last PDF page it is on |
| `printed` | [string or null, string or null] | printed page labels of those pages |
| `printed_inferred` | bool | the labels were counted from neighbouring pages, not read on the page |
| `lang` | string or null | its language |
| `flags` | list of string | as exported, e.g. `low_quality`, `front_matter`, `suspect_ocr`, `continues`, `continued` |
| `note` | int or null | for `kind` `note`: the note's number (`[^n]` in the text points to it); otherwise null |

### A `.txt` entry

A `.txt` has no pages. Its `section` lines carry `lines` ([first, last] line of
the file) instead of `pdf_pages` and `printed`. Its `para` lines carry `lines`
instead of `pdf_pages`, `printed` and `printed_inferred`. In its `file` line,
`pages` is null and `apparatus_pages` is `[]`.

## Roles

| role | what it is |
| --- | --- |
| `front` | title pages, copyright, dedication, the contents page if it is in the text |
| `editorial` | the editor's apparatus: acknowledgements, notes on the edition, series prefaces |
| `primary` | period texts: poems, manifestos, novels, essays of the time being studied |
| `criticism` | later scholarship about them: introductions, critical essays, monographs |
| `notes` | endnotes |
| `bibliography` | bibliographies, works cited |
| `index` | an index kept in the text |
| `back` | series lists, colophon |
| `other` | anything else, e.g. plates, a list of contributors |

## Rules a reader can rely on

1. `kind` only means something in the roles `primary`, `criticism`,
   `editorial` and `notes`. In other roles, treat every para as plain text.
2. Paras are in reading order. `seq` runs 1..N with no gaps, and `pdf_pages`
   never go back.
3. Each para belongs to exactly one section: the deepest one that holds it.
   `section` gives its id.
4. Sections tile the file. They come in document order. The first one starts
   at seq 1 at level 1. A section covers `first..last`, and its parent's range
   contains its own. Every seq falls in at least one section.
5. A para's role is the role of its section (`section` → `role`), the deepest
   one; an enclosing section's role does not override it.
6. Citations: section `author` and `title`, then the para's `printed` label,
   then `pdf_pages`, e.g. "Autora Uno, «Ensayo uno», p. 12".

## Example (synthetic)

An essay collection with a front part and two essays; the second holds a
quoted poem as a subsection. Each object is on one line, as in the file.

```json
{"type": "file", "schema": "gpdf-handoff/1", "sha256": "3f2a…c9", "file_name": "ensayos-de-prueba.pdf", "pages": 12, "title": "Ensayos de prueba", "author": "Editora Ejemplo", "year": "1999", "publisher": "Editorial Ficticia", "metadata_confirmed": true, "lang": "es", "structure_confirmed": true, "made_with": "g-pdf 0.2.0", "made": "2026-10-22T16:05:11-05:00", "apparatus_pages": [2]}
{"type": "section", "id": "s001", "parent": null, "level": 1, "title": "Front matter", "author": null, "role": "front", "first": 1, "last": 1, "pdf_pages": [1, 1], "printed": null, "lang": "es", "confidence": "high"}
{"type": "section", "id": "s002", "parent": null, "level": 1, "title": "Ensayo uno", "author": "Autora Uno", "role": "criticism", "first": 2, "last": 4, "pdf_pages": [3, 5], "printed": ["1", "3"], "lang": "es", "confidence": "high"}
{"type": "section", "id": "s003", "parent": null, "level": 1, "title": "Ensayo dos", "author": "Autor Dos", "role": "criticism", "first": 5, "last": 8, "pdf_pages": [6, 9], "printed": ["4", "7"], "lang": "es", "confidence": "medium"}
{"type": "section", "id": "s004", "parent": "s003", "level": 2, "title": "Poema citado", "author": "Poeta Tres", "role": "primary", "first": 7, "last": 8, "pdf_pages": [8, 9], "printed": ["6", "7"], "lang": "es", "confidence": "high"}
{"type": "para", "seq": 1, "section": "s001", "kind": "heading", "text": "Ensayos de prueba", "pdf_pages": [1, 1], "printed": [null, null], "printed_inferred": false, "lang": "es", "flags": ["front_matter"], "note": null}
{"type": "para", "seq": 2, "section": "s002", "kind": "heading", "text": "Ensayo uno", "pdf_pages": [3, 3], "printed": ["1", "1"], "printed_inferred": true, "lang": "es", "flags": [], "note": null}
{"type": "para", "seq": 3, "section": "s002", "kind": "prose", "text": "Un párrafo de prueba que sigue en la página siguiente.[^1]", "pdf_pages": [3, 4], "printed": ["1", "2"], "printed_inferred": false, "lang": "es", "flags": [], "note": null}
{"type": "para", "seq": 4, "section": "s002", "kind": "note", "text": "Una nota de prueba.", "pdf_pages": [5, 5], "printed": ["3", "3"], "printed_inferred": false, "lang": "es", "flags": [], "note": 1}
{"type": "para", "seq": 5, "section": "s003", "kind": "heading", "text": "Ensayo dos", "pdf_pages": [6, 6], "printed": ["4", "4"], "printed_inferred": false, "lang": "es", "flags": [], "note": null}
{"type": "para", "seq": 6, "section": "s003", "kind": "quote", "text": "Una cita de prueba.", "pdf_pages": [7, 7], "printed": ["5", "5"], "printed_inferred": false, "lang": "es", "flags": [], "note": null}
{"type": "para", "seq": 7, "section": "s004", "kind": "verse", "text": "Primer verso de prueba\nsegundo verso", "pdf_pages": [8, 8], "printed": ["6", "6"], "printed_inferred": false, "lang": "es", "flags": [], "note": null}
{"type": "para", "seq": 8, "section": "s004", "kind": "prose", "text": "Fin del poema y del ensayo.", "pdf_pages": [9, 9], "printed": ["7", "7"], "printed_inferred": false, "lang": "es", "flags": ["low_quality"], "note": null}
```

Here para 7 belongs to `s004` (the deepest section), so its role is `primary`,
even though `s003` (criticism) also covers it.

## Changes to the format

A change that breaks readers gets a new `schema` (`gpdf-handoff/2`). New
optional fields may be added to `gpdf-handoff/1`: readers should ignore fields
they do not know.
