# math-export: a math book or paper as per-page Markdown + LaTeX

`gpdf-cli math-export` turns a PDF of a mathematical book or paper (or a page
range of it) into one Markdown + LaTeX file per page in the distribution-atlas
page format, plus a review queue of the formulas worth a second look. A
reviewer (PROMPT_MATH_3: Claude) checks only the queue; `gpdf-cli
math-apply-review` applies the decisions; `gpdf-cli math-score` measures any
output against a reference transcription.

The engine is **MinerU**, run as an external program that you install in its
own environment. g-pdf never imports, bundles or vendors it: MinerU's code is
Apache-2.0 with extra terms (attribution for online services, a separate
licence above US$20 M monthly revenue) and g-pdf is AGPL. Both MinerU tiers run
on every page: `standard` (a 1.2 B vision-language model) gives the text,
`basic` (a smaller formula recogniser) is the second opinion that drives the
flags. Optionally **Marker** (Surya 2.5) reads the pages with math as a third
opinion; the two MinerU tiers share one layout model and often make the same
mistake, Marker does not. Everything else is offline and local.

## Installing MinerU (the trial setup, Windows, 8 GB GPU)

```powershell
uv venv --python 3.12 C:\path\venv-mineru
C:\path\venv-mineru\Scripts\activate
uv pip install torch torchvision --index-url https://download.pytorch.org/whl/cu130
uv pip install "mineru[core]==4.1.7" mineru-llama-cpp
mineru-kit parse some.png -o out --tier standard --ocr-mode ocr   # first run downloads the models
```

The models land under `%USERPROFILE%\.mineru\models` (about 2 GB; set
`MINERU_HOME` to put them elsewhere). The pins come from the trial that chose
the engine:

| | |
|---|---|
| `mineru-kit --version` | MinerU 4.1.7 |
| `MinerU2.5-Pro-2605-1.2B-Q8_0.gguf` | sha256 `3f352342…8488f03e7` (531 066 496 bytes) |
| `mmproj-MinerU2.5-Pro-2605-1.2B-Q8_0.gguf` | sha256 `b6c08ab5…c37514d3f8` (709 409 600 bytes) |

The two GGUF files are the standard tier's quantized weights. MinerU fetches
them from a personal Hugging Face account (`jinzhenj/MinerU2.5-Pro-2605-1.2B-GGUF`,
hard-coded in its registry; the original weights are Apache-2.0), which is why
they are verified by hash. A version or hash mismatch stops the run unless you
pass `--allow-unpinned`; either way the versions and hashes found are recorded
in `meta.json`.

Point g-pdf at the engine with `--mineru PATH` (the `mineru-kit` executable or
the venv folder), `$GPDF_MINERU`, or `mineru-kit` on `PATH`.

## Installing Marker (optional third opinion)

Marker's code is Apache-2.0; its weights carry a modified OpenRAIL-M licence
(free for personal use, research and organisations under US$5 M revenue). Like
MinerU it is an external program in its own environment, never bundled. It
serves the Surya VLM through the official llama.cpp `llama-server` binary:

```powershell
uv venv --python 3.12 C:\path\venv-marker
C:\path\venv-marker\Scripts\activate
uv pip install torch torchvision --index-url https://download.pytorch.org/whl/cu130
uv pip install "marker-pdf==2.1.0"            # brings surya-ocr 0.23.0
# llama.cpp release b11765, CUDA 12.4 build for Windows, from
# github.com/ggml-org/llama.cpp/releases/tag/b11765:
#   llama-b11765-bin-win-cuda-12.4-x64.zip   sha256 1dd1eddb…f0393c5eae
#   cudart-llama-bin-win-cuda-12.4-x64.zip   sha256 8c79a9b2…d7e32ae1d6
# check both against the release's published digests, unzip both into one folder
```

The first Marker run downloads the Surya GGUF files from Hugging Face
(`datalab-to/surya-ocr-2.5-gguf`, about 1.4 GB, into the Hugging Face cache);
g-pdf finds them there or in `--marker-gguf DIR`. Pins (a mismatch stops the run
unless `--allow-unpinned`; the values found go to `meta.json`):

| | |
|---|---|
| marker-pdf / surya-ocr (venv metadata) | 2.1.0 / 0.23.0 |
| `llama-server --version` | build 11765 (commit 9df0b73c7) |
| `surya-2.5.gguf` | sha256 `45fa3e58…172445d4b6bf` (1 180 178 976 bytes) |
| `surya-2.5-mmproj.gguf` | sha256 `f245dbf7…9476f861f659` (207 345 920 bytes) |

g-pdf starts `llama-server` itself on a free local port with the checked GGUF
files (Surya's own settings: 8 slots, `--jinja --special`), points `marker` at
it (`SURYA_INFERENCE_URL`, the documented external-server mode; never
`--use_llm`) and stops it when the run ends. Marker runs only after MinerU has
finished, so one engine holds the GPU at a time, and only on the pages where
either MinerU tier found display or inline math. Pages go to Marker as 16-page
PDFs made of the source's own pages; each finished chunk is cached per page at
once (key: source SHA-256, page, Marker version, its 192 dpi OCR render), so a
stopped run resumes. On KBJ (284 math pages, RTX 4060 laptop): 7.5 s per page
including a 25 s model load, peak 3.4 GB of GPU memory.

Point g-pdf at it with `--marker PATH` (the executable or the venv folder) or
`$GPDF_MARKER`, and `--llama-server PATH` or `$GPDF_LLAMA_SERVER`. Without
either `--marker` or `$GPDF_MARKER` there is no third opinion; `--no-marker`
turns it off when the variable is set.

## Running

```powershell
gpdf-cli math-export book.pdf --out OUT --pages 63-81,123-139
gpdf-cli math-export paper.pdf --out OUT --mineru C:\path\venv-mineru
```

Options: `--pages RANGE` (1-based file pages), `--dpi 300` (the page render
the engine reads), `--crop-dpi 300` (review crops), `--check-identities` /
`--no-check-identities`, `--inline per-page|per-span`, `--cache DIR`,
`--tesseract PATH` (page numbers the engine and the PDF's own text lack),
`--mineru-home DIR`, `--allow-unpinned`; Marker: `--marker PATH`,
`--llama-server PATH`, `--marker-gguf DIR`, `--no-marker`, `--marker-rule
any|value|small`, `--inline-with basic|marker|either|both`; and
`--style-page-consistency`; `--allow-sleep`.

On Windows, math-export keeps the PC from sleeping while it runs
(`SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)`; the display may
still turn off) and always clears the request when it ends, on errors too;
`--allow-sleep` turns this off, and on other systems nothing is done. `run.json`
records wall time (`seconds`, `engine_seconds.total`, `.marker`) and awake time
apart (`awake_seconds`, `engine_seconds.total_awake`, `.marker_awake`,
`.marker_per_page_awake`; Windows' unbiased interrupt time, the monotonic clock
elsewhere), so timings stay honest if the machine sleeps anyway.

Time on the trial machine (RTX 4060 laptop, 8 GB): about 7 s per page for the
standard tier and 6 s for the basic one, plus about a minute of model loading
per tier. Raw engine output is cached per page under `OUT/<stem>/cache/`, keyed
by the source file's SHA-256, the page, the tier, the engine version and the
dpi: a stopped run resumes where it left off, and a re-run over the same pages
never starts the engine. Half-written cache files from a stopped run are
discarded. Delete the cache folder to force a fresh parse.

## Output

```
OUT/<stem>/pages/pNNN.md            the page (NNN = 1-based file page)
OUT/<stem>/pages/pNNN.flags.json    its flags
OUT/<stem>/text.md                  the pages joined, in order
OUT/<stem>/review.jsonl             one review item per flagged formula
OUT/<stem>/crops/                   the items' page crops (300 dpi)
OUT/<stem>/meta.json                source, engine, pins, page labels, options, notices
OUT/<stem>/run.json                 per-page counts, timing, cache size
OUT/<stem>/cache/, logs/            engine output and logs
```

### The page format

Every page starts with `<!-- page printed="54" file="73" -->`. The printed
number comes from the engine's header/footer blocks (the number alone, or at
either edge of a running head), else from the PDF's own text in the top and
bottom bands, else from Tesseract on those bands when one is available.
Readings are fitted as runs of a constant offset (printed minus file page)
within each contiguous page range; a reading needs a second page within four
that agrees, so a misread lone number is rejected and listed. A change of
offset between adjacent pages is a **step** (a chapter start whose blank verso
was not scanned); runs, steps and rejected readings are printed and kept in
`meta.json`. A page without an accepted number gets `printed=""` and a
`label_inferred` flag with the run's value.

- Headings: a chapter opener (`CHAPTER 48` + title) or a `doc_title` is `#`;
  a title numbered `1` is `##`, `2.4` is `###`, `2.4.1` is `####`; without a
  number the engine's level is kept.
- Math: inline `\( \)`, display `$$ ... $$` with the equation number as
  `\tag{47.12}` on its own line inside the display (a trailing `(47.12)` the
  engine wrote inside the formula becomes a tag).
- Tables: simple HTML tables become Markdown; one with rowspan/colspan stays
  HTML with a `table_unstructured` flag.
- Figures: `[Figure 45.2: caption]`; footnotes `[^1]: text` at the page end.
- Running heads and feet are dropped and recorded as `dropped` flags; page
  numbers are the marker. A paragraph repeated on the first page of two or more
  runs (a publisher's banner on chapter openers) is dropped there, with a flag.
- Every page has balanced `$$` and `\( \)`: prose cannot open math. An untagged
  display at the foot of a page followed by a display at the top of the next
  is a possible split display: it is closed, reopened right after the next
  marker, and both pages get a `page_anomaly` flag.
- The `|…|` artifact: the standard tier sometimes wraps a whole display in
  `\left| … \right|`. The bars are stripped only when the basic tier reads
  that display without them, and the change is recorded as an `auto_fix` flag
  with before and after. Bars inside a display, or that both tiers agree on,
  are never touched.
- A page beginning mid-paragraph continues right after the marker, so pages
  concatenate.

### The review queue

`review.jsonl` has one item per flagged formula:

```json
{"id": "p073-d02", "file": 73, "printed": "54", "kind": "disagree",
 "reasons": ["tiers disagree"], "tag": "44.74",
 "latex_primary": "...", "latex_second": "...", "crop": "crops/p073-d02.png"}
```

`kind` is the first of the item's reasons in this order; all reasons are listed.

| kind | rule |
|---|---|
| `disagree` | the two tiers' readings differ after normalization, compared loosely (bold/roman markup, line breaks, dot style and trailing punctuation ignored); or the basic tier has no reading for the display. The trial's rule: on 20 KBJ pages it flagged 28 % of displays and caught 11 of 12 value-changing errors. |
| `marker` | Marker reads the display differently (its display aligned to the standard tier's by tag, then reading order). `--marker-rule value` (default): any value-changing difference (style commands, line breaks, dot style, trailing punctuation ignored); `small`: a value-changing difference of 1-3 tokens only; `any`: any normalized difference. On KBJ (288 pages, 53 value-changing errors) against no Marker: `small` +24 items, +2 errors caught (51/53); `value` +55 items, +4 (53/53); `any` +116 items, +4. `value` is the default because the two errors only it catches are lost content (a dropped side-condition line, a missed display): 31 more items over 288 pages is worth that. The item carries `latex_marker`. |
| `style` | the tiers differ only in `\mathbf` / `\boldsymbol` / `\mathrm` / `\mathit` / `\mathcal` / `\mathbb`. With `--style-page-consistency` also: the same symbol (same subscript) is both bold and not on one page; off by default, since bold **X** for a vector and X₁ for its component is normal multivariate notation (on KBJ: 74 fewer items, 2 of 21 style errors no longer flagged). |
| `stack` | a `\sum` / `\prod` / `\int` / `\bigcup` whose limits hold two index variables, a relation (`<`, `\le`, `\ne`) or a sum of indices: every engine collapses a printed ΣΣ into one Σ. |
| `syntax` | unbalanced braces, `\left`/`\right` or `\begin`/`\end`, or a command outside the whitelist (standard LaTeX math plus the trial's outputs). |
| `tag` | within a contiguous page run a tag repeats, goes backwards or skips a number. |
| `missing_display` | Marker (or the basic tier) has a display that none of the standard tier's aligns with, or an equation block of the layout gave no display. `latex_primary` is null, `latex_second` the other engine's reading, the crop its box (the whole page without one). The two MinerU tiers share one layout, so in practice the signal comes from Marker: on KBJ 6 items, 3 of the 6 missed displays. |
| `inline` | inline spans that differ from the other engines after pairing the paragraphs by position: `--inline-with either` (default) flags a span when the basic tier or Marker reads it differently (the basic tier alone on pages Marker did not read); `basic`, `marker` and `both` (only when neither agrees) are the alternatives. On KBJ (5 143 spans, 522 wrong): basic 32 % of spans flagged, recall 56 %, 258 page items; marker 8 %, 37 %, 145; either 36 %, 72 %, 261; both 4 %, 20 %, 92. Inline flags are grouped into **one item per page** (`pNNN-inline`, with `spans` (each with `latex_second` and `latex_marker`) and one crop per text line of each flagged paragraph); `--inline per-span` gives one item per span. |
| `identity` | a numeric identity failed (below). |

Crops are the layout box of the formula padded a little, or the whole page
when there is no box.

### Identity checks (`g-pdf[mathcheck]`)

With the optional extra installed (`pip install "g-pdf[mathcheck]"`: sympy,
mpmath and `antlr4-python3-runtime==4.11`, which sympy's `parse_latex` needs)
the displays that are real expressions get a numeric sanity pass; without it
the pass is skipped with a one-line notice (`--check-identities` makes the
missing extra an error). The pass never edits text and never judges syntax: a
display that will not parse is skipped, not flagged.

A display is classified by its left-hand side (density `f(x)`, `f_X(x)`,
`p(x_1, x_2)`; cdf `F`; survival `\bar F`, `S`, `\Pr[X > x]`; hazard `h`; mgf
`M(t)`, `E[e^{tX}]`; characteristic function `\varphi(t)`; pgf `G(z)`,
`E[z^X]`; mean `E[X]`), or by the text just before it for a function name the
table does not know ("density", "distribution function", "survival",
"hazard", "moment generating", "characteristic function", "probability
generating"). Tags, sizes and spacing are stripped, aligned rows and `=`
chains split, side conditions dropped; `\Gamma`, `B(·,·)`, `\Phi`, `e`, `i`,
`\pi` and indicator notation are mapped.

Checks, at four stratified positive draws of every parameter:

- density: non-negative, integrates to 1 over (0, ∞), (−∞, ∞) or (0, 1) (the
  support that worked is reported; 2-D with a bounded cubature);
- cdf and survival: within [0, 1], monotone, the right limits;
- mgf, cf, pgf: 1 at 0 (pgf at 1), and a non-negative variance, M″(0) − M′(0)²
  in 1-D and the Hessian minus the outer product of the gradient PSD in 2-D
  (this is what catches a sign error such as exp{−½ t′Σt}, which still equals
  1 at 0);
- pairs in the same section (between headings, within a page run): F′ = f,
  h = f/S, S = 1 − F, a joint survival at x₂ = 0 against the marginal, M′(0)
  against a printed mean.

Parameter constraints are unknown, so a check that fails for some draws and
passes for others is **inconclusive**: counted, not flagged. Each display gets
about 5 s; on timeout it is skipped. A failure becomes an `identity` review
item (or joins the display's existing item) with the identity, the parameter
values and the numbers, and a note that it may be a printed error rather than
a misread: the reviewer compares the crop and, if the LaTeX matches the page,
marks `possible_source_typo` instead of changing it. `run.json` has the
per-page counts: checked, passed, failed, inconclusive, skipped.

## Applying the review

```powershell
gpdf-cli math-apply-review --export OUT\book --decisions review_decisions.jsonl
```

`review_decisions.jsonl` has one line per item:

```json
{"id": "p073-d02", "action": "accept"}
{"id": "p073-d02", "action": "replace", "latex": "..."}
{"id": "p073-d02", "action": "flag", "flag_type": "possible_source_typo", "note": "..."}
{"id": "p073-inline", "action": "replace", "spans": [{"latex_primary": "x_1", "latex": "x_i"}]}
```

`flag_type` is one of `formula_uncertain`, `possible_source_typo`,
`copy_corrupt`, `illegible`. A replace keeps the display's `\tag`; a `"tag":
"47.27"` in a display's decision (accept or replace) sets it instead, added
when the display had none (a number the engine read into the body).

A `missing_display` item has no `latex_primary`; its replace writes the display
in, either after a piece of page text (`after`) or in place of one
(`page_text`: what the engine made of it, such as a broken display and an inline
span); the text must occur exactly once in the page. `tag` gives it a `\tag`:

```json
{"id": "p418-m01", "action": "replace", "latex": "\\begin{aligned} ... \\end{aligned}",
 "page_text": "$$\n...\n$$\n\nand \\(...\\),", "tag": "47.120"}
```

Accept and flag on such an item change no text.

A replace on a display is compared with the old LaTeX (for a missing display, only
its own balance is checked) and refused when it loses or unbalances the layout: fewer
`\\` row breaks (a trailing one before `\end` and the rows of a `\substack` are not
counted), fewer `&` alignment points, fewer `\begin`/`\end` environments (renaming
`array` to `aligned` is fine), fewer `\left`/`\right` pairs, braces or environments
newly unbalanced, or a stray `\ ` where the old text had a `\\` (what a helper script
does when it collapses `\\`). A change that is meant (the page prints the rows on one
line) carries `"structure_change": true` and a `"reason"`; both go into the `reviewed`
record.

An inline span whose `\(…\)` occurs more than once in the page needs
`"occurrence": n` (1-based, in page order) in its replace; without it the
decision is refused rather than applied to the first one. Every
applied decision leaves a `reviewed` record in the page's flags, so running
the same file again changes nothing. A decision whose id is unknown, or whose
`latex_primary` is no longer in the page, is refused and listed (exit code 2);
the others still go through. Pages without a decision are never touched.
`text.md` is rebuilt from the page files.

## Scoring

```powershell
gpdf-cli math-score --ref REF_DIR --cand OUT\book --out SCORES [--pages 63-81]
```

`--ref` and `--cand` are page folders (`pNNN.md` + `pNNN.flags.json`),
math-export folders, or `text.md` files (split by their page markers).

**Reference scope.** A reference page whose flags include a `dropped` (or
`page_anomaly`) flag saying part of the page was not transcribed is scored
only as far as the reference goes. The flag's `text` (or `note`) must contain "not transcribed",
"outside this pass", "outside the pass/scope" or "out of scope", and must not
mention a figure, plot, chart, axis, label, caption, table or image (a note
about a figure's own labels is not a scope cut); for example `{"type":
"dropped", "text": "Bibliography not transcribed in this pass"}`. On such a
page, bracketed placeholder lines of the reference (`[… not transcribed …]`)
are left out, the candidate block (a run of non-blank lines) most like the
reference's last block (similarity ≥ 0.8) is found, and the candidate's text,
displays and inline math after it are not scored; when the reference has
nothing left on the page, none of the candidate page is scored. `scope_cut` in the summary
counts the pages cut and those whose last block was not found (scored whole);
the report lists them with what was ignored. The
scorer is the trial's: displays normalized (sizing, spacing and style wrappers
dropped, synonyms folded, alignment environments reduced, script order and
grouping braces canonicalised, the tag split off), aligned by tag then reading
order (joining a display the candidate split in two), then exact match, mean
token edit distance, tag accuracy, missed and extra formulas, near-misses of
1-3 tokens split into value-changing and cosmetic, the inline exact rate (a bag
of spans), the prose character error rate and the page-label accuracy.
Cosmetic means the two readings agree once style commands (`\mathbf`,
`\mathbb`, `\mathcal`, …), dot style, trailing punctuation and line breaks are
taken out; a cosmetic difference that involves a style command is a style
error. Notation-only forms compare equal: prime and script order (`x'_1` =
`x_1'`, `x'^2` = `x^{\prime 2}`, `q''` = `q^{\prime\prime}`, `a^{*\prime}` =
`a^{*^{\prime}}`), `\overset` = `\stackrel`, `\binom{a}{b}` = a two-row
`pmatrix`, `\phantom{…}` dropped, punctuation at the end of `\text{…}` moved
out, `\stackrel` limits = `\substack`. A display split across a page break is
joined on **both** sides where either side's `page_anomaly` flag links the
pages, so a link only one side makes does not turn two right formulas into a
missed and a wrong one; `split_joins` counts where the sides agree. With a
`review.jsonl` beside the candidate, **flag recall**: the share of the
candidate's wrong formulas (all, value-changing, style) that some review item
covers (an item on either half of a joined display counts), by the kind of the
covering reasons, and the list of unflagged value-changing errors; the
**inline flag recall** (a span without a match in the reference bag of its page,
listed by an inline item of the page) with the number of spans flagged; and the
**missing-display recall** (a missed display with a `missing_display` item on
its page whose reading is close). `score.json` and `REPORT.md` quote the
formulas; the screen gets numbers only.

## Tests

`tests/pipeline/test_math_*.py` use hand-written fake engine outputs of our own
formulas and a fake `mineru-kit` (`tests/pipeline/fake_mineru.py`); the
identity tests skip without the extra. `test_math_integration.py` renders a
two-page PDF from our own formulas and runs it through the real engine; it is
marked `slow` and skipped unless `$GPDF_MINERU` (or `mineru-kit` on `PATH`)
points at a working install. Marker has its fakes too (`tests/pipeline/fake_marker.py`:
a `marker` that writes Marker JSON from a page script and a `llama-server` that
answers `/health`); `test_math_marker_integration.py` runs the real Marker on a
page of our own formulas, `slow` and skipped unless `$GPDF_MARKER` is set.
