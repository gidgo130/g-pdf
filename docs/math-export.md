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
flags. Everything else is offline and local.

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

## Running

```powershell
gpdf-cli math-export book.pdf --out OUT --pages 63-81,123-139
gpdf-cli math-export paper.pdf --out OUT --mineru C:\path\venv-mineru
```

Options: `--pages RANGE` (1-based file pages), `--dpi 300` (the page render
the engine reads), `--crop-dpi 300` (review crops), `--check-identities` /
`--no-check-identities`, `--inline per-page|per-span`, `--cache DIR`,
`--tesseract PATH` (page numbers the engine and the PDF's own text lack),
`--mineru-home DIR`, `--allow-unpinned`.

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
| `style` | the tiers differ only in `\mathbf` / `\boldsymbol` / `\mathrm` / `\mathit` / `\mathcal` / `\mathbb`; or the same symbol (same subscript) is both bold and not on one page (bold = vector or matrix in multivariate notation). |
| `stack` | a `\sum` / `\prod` / `\int` / `\bigcup` whose limits hold two index variables, a relation (`<`, `\le`, `\ne`) or a sum of indices: every engine collapses a printed ΣΣ into one Σ. |
| `syntax` | unbalanced braces, `\left`/`\right` or `\begin`/`\end`, or a command outside the whitelist (standard LaTeX math plus the trial's outputs). |
| `tag` | within a contiguous page run a tag repeats, goes backwards or skips a number. |
| `inline` | inline spans that differ between the tiers after pairing the paragraphs by position. On the trial pages 29.8 % of the standard tier's spans got flagged, catching 51.6 % of its inline errors, so inline flags are grouped into **one item per page** (`pNNN-inline`, with `spans` and one crop per text line of each flagged paragraph); `--inline per-span` gives one item per span. |
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
`copy_corrupt`, `illegible`. A replace keeps the display's `\tag`. Every
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
math-export folders, or `text.md` files (split by their page markers). The
scorer is the trial's: displays normalized (sizing, spacing and style wrappers
dropped, synonyms folded, alignment environments reduced, script order and
grouping braces canonicalised, the tag split off), aligned by tag then reading
order (joining a display the candidate split in two), then exact match, mean
token edit distance, tag accuracy, missed and extra formulas, near-misses of
1-3 tokens split into value-changing and cosmetic (dot style, trailing
punctuation, bold/roman markup, line breaks), the inline exact rate (a bag of
spans), the prose character error rate and the page-label accuracy. A display
split across a page break is joined on each side from the `page_anomaly` flag.
With a `review.jsonl` beside the candidate, **flag recall**: the share of the
candidate's wrong formulas (all, value-changing, style) that some review item
covers, by the kind of the covering reasons, and the list of unflagged
value-changing errors. `score.json` and `REPORT.md` quote the formulas; the
screen gets numbers only.

## Tests

`tests/pipeline/test_math_*.py` use hand-written fake engine outputs of our own
formulas and a fake `mineru-kit` (`tests/pipeline/fake_mineru.py`); the
identity tests skip without the extra. `test_math_integration.py` renders a
two-page PDF from our own formulas and runs it through the real engine; it is
marked `slow` and skipped unless `$GPDF_MINERU` (or `mineru-kit` on `PATH`)
points at a working install.
