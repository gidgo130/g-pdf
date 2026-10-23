# The review sheets

Book prep has three review sheets. Each is a single HTML file that a `gpdf-cli`
command writes next to its output. Open it in Edge, Chrome or Firefox. It works
offline, straight from the file, and sends nothing anywhere.

| sheet | written by | you decide | it saves | the command that reads it |
| --- | --- | --- | --- | --- |
| split review, `<name>_split.review.html` | `gpdf-cli split --dry-run` | where each two-page scan is cut | `<name>_split.decisions.json` | `gpdf-cli split --decisions` |
| catalogue, `catalogue.review.html` | `gpdf-cli survey` | what happens to each file, and its title and author | `catalogue.decisions.json` | `survey`, `apply`, `export` |
| structure, `structure.review.html` | `gpdf-cli structure` | the parts, sections, titles, authors and roles of each book | `structure.decisions.json` | `structure`, then `handoff` |

A sheet never changes a PDF or applies anything. It only saves a decisions
file. The command reads that file on its next run.

## Saving and loading (all three sheets)

**Save decisions** writes the decisions file:

- In Edge and Chrome a save dialog opens with the right file name filled in.
  Save it in the folder the sheet names (see below).
- In Firefox, or when the dialog is blocked, the file goes to your downloads
  folder. Move it into the right folder yourself, keeping its name.

Where each file belongs:

- split: the split output folder, the same folder as the sheet;
- catalogue: the catalogue folder, shown at the top of the sheet;
- structure: the structure folder, `private\book-prep\structure`, also shown
  at the top of the sheet.

**Load decisions...** opens a decisions file you saved earlier and **replaces**
everything in the sheet with it. Nothing is merged. Your unsaved changes are
lost, so save first if you want to keep them. A file the sheet cannot use (not
JSON, the wrong kind of file, or a split file made for a different PDF) is
refused with the reason, and nothing changes.

**When the sheet opens**, it starts from the decisions the command read when
it wrote the sheet:

- split: the decisions file the dry run wrote, or the file given with
  `--sheet-only --decisions`;
- catalogue: `catalogue.decisions.json` in the catalogue folder;
- structure: `structure.decisions.json` in the structure folder, or the file
  given with `--decisions`.

Re-running the command after you save puts your saved decisions into the new
sheet.

**Unsaved work stays in the browser.** Every change is also kept in the
browser's own storage. If you close the sheet without saving and open it again,
a bar at the top asks **Restore** or **Discard**:

- **Restore** brings back your unsaved changes.
- **Discard** forgets them and keeps what the sheet opened with.

The catalogue and structure sheets add "The decisions file has changed since"
when the file was saved or replaced after your unsaved changes. Restoring then
replaces the newer file's content in the sheet, so check before you choose.
The browser keeps this copy only on this computer and only in this browser,
and clearing its site data deletes it. It is not a backup: save the file.

If you try to close a sheet with unsaved changes, the browser asks you to
confirm.

## Split review sheet

This sheet is for two-page spreads that `split` is unsure how to cut. It opens
at the first **flagged** page, one the detector is unsure about.

**Top bar**

- the file name and **Flagged reviewed: x / y**;
- filters for the page list: **All**, **Flagged** (pages the detector is
  unsure about), **Changed** (pages where your decision differs from the
  detector's);
- **Next step**: the `gpdf-cli split ... --decisions ...` command to run once
  you have saved, with a **Copy** button. Fix the path if you saved the file
  somewhere else.

**The page**

The large view shows the page. Next to it is a close-up of the gutter at full
resolution. The dashed line is the detector's cut; the red line is yours.
To move the red line:

- click near the top or bottom edge to put that end of the cut there;
- drag a round handle to move one end (hold Shift to move the whole line);
- drag the line itself to move all of it; while you hold it, the mouse wheel
  turns it 0.1° per notch;
- **Ctrl+←/→** move it 0.1% of the page width (with Shift, 1%);
- **Ctrl+↑/↓** turn it 0.1° (with Shift, 1°);
- **V** makes it vertical through its middle.

**The panel**

- **Facts**: what the detector found (spread or single page, and its
  confidence), whether the page is flagged, your action, whether it is
  reviewed, the cut (where its top and bottom cross the page, as fractions of
  the width), and its slant. **Changed** appears when your decision differs
  from the detector's.
- A warning appears when the cut slants more than 3°, and when g-pdf cannot
  split the page at all (keep it whole).
- **Reasons**: why the detector flagged the page.
- **S Split** / **K Keep whole**: decide this page and go to the next flagged
  page you have not reviewed yet.
- **Enter Accept as shown**: take what is shown (split, or keep whole), mark
  the page reviewed and go to the next one.
- **R Reset cut**: go back to the detector's cut.
- **← Previous** / **Next →**: move through the pages the filter shows.
- **? keys**: the list of keys.

**Page list** (below): a thumbnail of every page. Click one to open it, including
pages the detector was sure about. The badges are:

- **flagged**: not reviewed yet;
- **reviewed**: a flagged page you have decided;
- **changed**: your decision differs from the detector's;
- **single**: one page, not split;
- **kept whole**: a spread you chose not to split.

**Loading** checks that the file is for this PDF: the same source path, page
count, size and modification time. A file made for another version of the PDF
is refused. Run the dry run again for that version.

## Catalogue sheet (survey)

One row per PDF or `.txt` the survey found. Click a row to open it; click it
again to close it.

**Top bar**

- **Confirmed x / y**, and how many need re-confirming;
- filters: **All**, **Pending** (not confirmed), **Needs re-confirm**,
  **Suggested rebuild** (the survey suggests `rebuild_layer`), **Excluded**,
  **Missing** (the file is no longer where it was);
- **Sort**: by root folder and name, by name, or worst text quality first;
- **Confirm all shown**: confirms every row the current filter shows, as it
  stands. Filter first, then use it;
- the catalogue folder, where the decisions file belongs.

**Columns**: root folder, file, kind, pages, text % (pages with text), scan or
born-digital, language, suspect % (the median page and the weighted average),
fair / poor pages, cap-unknown % and missing-accent % (shown for information
only), the working file being measured, the duplicate group, the survey's
suggestion, and your decision.

**Decision badges**

- **pending**: no decision, or not confirmed yet;
- **confirmed**: you confirmed it;
- **applied**: confirmed, and `apply` has made the rebuilt file;
- **needs re-confirm**: the file changed since you decided, or the survey's
  suggestion changed;
- **→ action**: your decision differs from the suggestion;
- **no dictionary fixes**: see below.

**An open row** shows, on the left:

- the suggestion and its reasons;
- facts about the file: its metadata, bookmarks, pages without text, the
  working file, and the source's own text layer for comparison;
- a bar per page for its suspect %: click a bar for that page's numbers and
  its sample picture, if one is embedded;
- the worst pages and pages without text, each with its picture and text;
- the trial OCR, if it ran;
- duplicates and related files.

On the right is the decision form:

- **Action**: `keep_text` (use the text layer as it is), `ocr_missing` (OCR
  only the pages listed under Pages), `rebuild_layer` (replace the whole text
  layer by OCR), `exclude`, or `review` (not decided).
- **Pages**: `all`, or a list such as `4, 7-9`.
- **Reason**: for `exclude`, for example `duplicate`, `other_language` or
  `off_topic`.
- **Duplicate of**: the copy this file duplicates.
- **Title**, **Author**, **Year**, **Page offset** (a whole number; it is
  saved with the decision, but no command uses it yet), **Notes**.
- **No dictionary fixes**: the text export uses layout rules only and keeps
  invented words (for a poet's coinages). Ticking it does not change the
  confirmation.
- **Confirm**: confirms this decision. Any later edit to this row's decision
  un-confirms it until you press **Confirm** again.
- **Reset to suggestion**: puts back the survey's action, pages, reason and
  duplicate, and keeps your title, author, year, page offset, notes and
  no-dictionary setting.

When a file changes, its decision carries over but needs re-confirming.

## Structure sheet

One row per exported book or article. Rows with low-confidence sections come
first.

### How a book is divided

- The text of a book is a list of **units**, numbered from 1 (its `seq`): each
  heading, paragraph, stanza, quote or note is one unit. Units come from the
  export and never change unless you export again.
- A **section** is a start point: the unit it starts at and its **level**.
  Level 1 is a part (front matter, an anthology's critical part, the
  bibliography, the plates), level 2 a section (an essay, a text, a chapter,
  an act), level 3 a subsection. The detector stops at level 3; you can nest
  deeper (level 4, 5, ...), for example the texts of a group inside a part.
  A section is at most one level deeper than the section around it (its
  parent): a level-4 section sits inside a level-3 one, never directly inside
  a level 2.
- A section runs until the next section of the same level or a higher one
  (a smaller number). A level-2 section ends at the next level-2 or level-1
  start.
- Every unit belongs to exactly one section: the **deepest** one around it.
  A part that holds sections only owns the units before its first section.
- A unit takes its **role** (and its author) from that deepest section.
  Roles:
  - **front**: title pages, copyright, dedication, a contents page in the
    text;
  - **editorial**: the editor's apparatus, such as acknowledgements, notes on
    the edition and series prefaces;
  - **primary**: period texts, such as poems, manifestos, novels and essays of
    the time;
  - **criticism**: later scholarship, such as introductions and critical
    essays;
  - **notes**, **bibliography**, **index**, **back** (series lists,
    colophon), **other** (plates, a list of contributors).
- An **author** is set only when it is not the book's own author: the critic
  of an anthology essay, the writer of a manifesto. Leave it empty for the
  book's author.

### The entry list

- top bar: **Reviewed x / y** and how many entries have stale or unmatched
  decisions;
- filters: **All**, **Low confidence** (entries with sections detected with
  low confidence), **Not reviewed**, **Stale or unmatched**;
- columns: file, kind (pdf or txt, and monograph, anthology or texts), number of
  sections, low-confidence sections, metadata (confirmed or not, with title,
  author and year), number of decisions, and status:
  - **not reviewed**: no decisions at all;
  - **reviewed**: at least one decision (an edit, confirmed metadata, a
    role or the Structure reviewed tick), all of them applied;
  - **stale / unmatched**: at least one decision does not apply.

### An open entry

**Entry metadata**: title, author or editor, year and publisher, filled in from
the book where g-pdf could. Fix or fill them in, then press **Confirm
metadata**. Changing a field afterwards un-confirms it. The search app is told
whether the metadata is confirmed.

**Whole entry**

- **Role of every section**: one role for the whole entry, applied before the
  other decisions. Use it for a single text or essay. "(as detected, per
  section)" removes it.
- **Structure reviewed**: tick it when you have looked and nothing needs
  changing. It counts as reviewed.
- **Decisions**: every decision for this entry, in the order it is applied,
  each with a badge (**applied**, **stale**, **unmatched**) and the reason
  when it does not apply. **Remove** deletes one decision. Later decisions
  then replay without it, so check that they still apply.

**Sections**: the tree after your decisions. Titles are indented one step per
level, at any depth. Rows detected with low confidence are shaded. Columns:

- **Title**, **Author**, **Role**: edit them in place. Each change adds an
  edit decision; several changes in a row to the same section go into one
  decision. Clearing the author removes it. A cleared title keeps the old
  one.
- **Lvl**, the level.
- **Pages**: the PDF pages, the printed pages, and the units it covers.
- **Conf.**: high, medium or low. **yours** marks a section you changed or
  added.
- **Evidence**: why the detector put the section there. Click to see it.
- **Text**: the end of the section before ‖ the start of this one, to check
  the boundary.
- **Move…** (not on sections that start at unit 1): lists the units up to 12 on either
  side of the start. Pick one to make the section start there. Sections of
  deeper levels that start at the same unit move with it. You cannot move a
  start past the start of a neighbouring section. You cannot move a section
  that starts at the same unit as its parent; move the parent instead.
- **Merge** (not on the first section): removes this boundary. Its units join
  the section before, and its title, author and role are gone. Sections
  inside it stay. If they now sit two levels below their new parent, change
  their level too (merge and split them, see below).
- **Split…**: starts a new section at a unit inside this one. Fill in the
  unit number (it starts at the next heading, and the line below shows that
  unit's text), the level, the title, the author and the role, then **Add
  split**. The level is set to that of the deepest section at that unit;
  change it if needed. The list offers levels 1 down to one below that
  deepest section; a split that would land two levels below the section
  around it is refused.

A decision is tried before it is added. If it would not apply (the section is
gone, or a move would empty a section), the sheet refuses it and says why.

### What the sheet cannot do

- **Change a level directly.** Merge the section, then split at the same unit
  with the new level, title, author and role.
- **Cut inside a unit.** Sections start at units. If a heading is glued to the
  end of the paragraph before it, start the section at the nearer unit
  boundary.
- **Put two sections of the same level at one unit.** Sections of different
  levels may start at the same unit (a part, its first section, that one's
  first subsection, ...).

### Stale and unmatched

Each decision names a unit by its number and a short hash of its text.

- A decision is **stale** when that unit's text has changed, which means the
  book was exported again. It is not applied. Remove it and decide again.
- A decision is **unmatched** when the section it names no longer exists,
  when it would break the order of sections, or when a split would start a
  section two or more levels below the section around it. It is not applied either.

`gpdf-cli structure` prints how many decisions applied and how many are stale
or unmatched. `handoff` marks an entry's structure as confirmed only when it
has decisions and none is stale or unmatched.

### A draft decisions file

To review a prepared draft (for example
`structure.decisions.draft.json`), open the sheet, use **Load decisions...**
and pick the draft. Review it, then **Save decisions**: the file is saved as
`structure.decisions.json`, the name `structure` reads, and the draft stays as
it was. You can also apply a draft without the sheet:
`gpdf-cli structure ... --decisions <draft>`.

## Decisions files and backups

Your decisions cannot be made again by g-pdf. Everything else (PDFs, the
export, the sections files, the sheets themselves) the commands write again
from the sources and your decisions. Back up the decisions files:

- `gpdf-cli backup --to <folder>` copies every decisions file under
  `private\book-prep` into a new dated folder, with a manifest of their
  hashes. It never changes the files it copies. If nothing changed since the
  newest backup, it makes no new folder (`--always` forces one).
- With `GPDF_BACKUP_DIR` set, every command that reads decisions (`split
  --decisions`, `survey`, `apply`, `export`, `structure`, `handoff`) backs
  them up there first. See the README, "Backing up your review work".
- A forced split dry run first copies an existing reviewed decisions file to
  `<name>_split.decisions.<date-time>.bak.json`.
- Saving in a sheet overwrites the file you pick. To keep an earlier version,
  back up first, or save under another name and load it when you need it.
- To restore, copy a backed-up file back to its place under
  `private\book-prep`. Then run the command, or **Load** the file in its
  sheet.

The browser's saved copy of unsaved work is not a backup (see above).
