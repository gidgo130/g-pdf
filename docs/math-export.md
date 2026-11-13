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
`--style-page-consistency`; `--identity-letters L=KIND,...`; `--allow-sleep`.

`--ocr-mode ocr|txt` chooses what MinerU reads: `ocr` (default) a grayscale render of
each page at `--dpi`; `txt` the PDF's own text layer (each page goes to the engine as a
one-page PDF cut from the source, MinerU's `--ocr-mode txt`), for born-digital books.
The two are cached apart (`.../<dpi>/` against `.../txt/`) and the choice is in
`meta.json`.

**DjVu input.** A `.djvu` BOOK is read through DjVuLibre's `ddjvu` and `djvused` (GPL;
an external program, never a Python dependency): `--ddjvu PATH` (either executable or
their folder), `$GPDF_DDJVU`, `ddjvu` on `PATH`, then `C:\Program Files (x86)\DjVuLibre`.
On Windows, install it with `winget install DjVuLibre.DjView` (the official DjVuLibre +
DjView installer from djvu.sourceforge.net; conda-forge has no Windows build). The
selected pages are rendered at `--dpi` (`ddjvu -format=pnm -page=N -scale=DPI`, cached
as PNG under `cache/<sha>/ddjvu-<dpi>/`) into a proxy PDF, `OUT/<stem>/source/<stem>.pdf`,
with the DjVu's page count (the other pages blank); everything downstream reads the proxy,
so `file=""` is the 1-based DjVu page index. `meta.json` keeps the DjVu as the source
(path, SHA-256, page count) and adds `render_pdf` and `djvu` (dpi, tools, version); the
engine and Marker caches are keyed by the DjVu's SHA-256 and the render dpi. A DjVu's own
hidden text layer is never read, and `--ocr-mode txt` is refused for it.

On Windows, math-export keeps the PC from sleeping while it runs
(`SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)`; the display may
still turn off) and always clears the request when it ends, on errors too;
`--allow-sleep` turns this off, and on other systems nothing is done. `run.json`
records wall time (`seconds`, `engine_seconds.total`, `.marker`) and awake time
apart (`awake_seconds`, `engine_seconds.total_awake`, `.marker_awake`,
`.marker_per_page_awake`; Windows' unbiased interrupt time, the monotonic clock
elsewhere), so timings stay honest if the machine sleeps anyway.

**Stall watching.** Every progress line, stage and identity-pass page rewrites
`OUT/<stem>/heartbeat.json` (pid, stage, page, time). `gpdf-cli math-watch --log FILE
--watch PATH ... [--stall 600] [--heartbeat FILE] [--step NAME] -- CMD ...` runs a long step
(an engine group, an assembly, a checks script) and stops it, child processes included,
when its log and watched paths (the cache folder, the step's own heartbeat) have not grown
for `--stall` seconds (600 by default; 300 for checks); exit code 3 then, and the watcher's
heartbeat says `stalled` with the reason, so a driver marks the step `retry_later` and goes
on. Each identity check also has a hard per-display limit (`ITEM_TIMEOUT`, 20 s) on top of
its own 5 s deadline: the deadline is read only between function evaluations, the hard
limit interrupts whatever the check is doing (Batch B-2's j21b check sat inside mpmath for
7.5 hours).

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
| `inline` | inline spans that differ from the other engines after pairing the paragraphs by position: `--inline-with either` (default) flags a span when the basic tier or Marker reads it differently (the basic tier alone on pages Marker did not read); `basic`, `marker` and `both` (only when neither agrees) are the alternatives. On KBJ (5 143 spans, 522 wrong): basic 32 % of spans flagged, recall 56 %, 258 page items; marker 8 %, 37 %, 145; either 36 %, 72 %, 261; both 4 %, 20 %, 92. Inline flags are grouped into **one item per page** (`pNNN-inline`, with `spans` (each with `latex_second` and `latex_marker`) and one crop per text line of each flagged paragraph). Each span also records where it is: `bbox` (its paragraph's box), `at` (the span's middle as a fraction of the paragraph's printed length; the engines give no box per span), `line_box` (the text line at that position with one line above and one below) and `crop` (`crops/pNNN-inline-sK.png`, that box), so a reviewer of one span sees its own lines, not the whole page; `--inline per-span` gives one item per span. |
| `inline_missing` | prose that looks like math the engine left as text (inline math is never looked at): Greek letters and math symbols (with script digits or primes), a letter at a relation with a number or a single letter (`γ = 0`, `x > 0`), an operator between single letters or numbers with at least one letter (`n - 1`, `x/y`), a letter with a digit index (`x1`), a lone letter that is not a word (not `a`, `A`, `I`, an initial, `e.g.` or `(a)`), and an `a` that cannot be the article (before a comma or a verb such as "increases", or after "parameter": Paper Capture reads α as `a`). One item per page (`pNNN-inline-missing`, `runs`: each with its `text`, `why`, `occurrence` in the page prose, `context`, and a crop of its line like an inline span's). Not packed by default (`--kinds inline_missing`); a replace writes runs as inline math (`"runs": [{"text", "latex", "occurrence"}]`). On the JKB Paper Capture pilot (10 pages): 11 of 12 reference spans that came out as prose are covered, 2.1 runs and 0.7 items per page; on JKK (born-digital) 0.3 runs per page. |
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
`\pi`, `\operatorname{erfc}` / `erf`, the normal quantile `\Phi^{-1}(p)` (and `N(p)` of a
probability variable, as sqrt(2) erfinv(2p - 1)) and indicator notation are mapped.
`--identity-letters S=quantile,Q=quantile` (or `G=cdf`, `M=skip`, ...) declares the book's own
letters, which then decide the quantity before the table above; a declared `quantile` is checked as
increasing on (0, 1). Undeclared, a density or distribution function whose only variable is `p` (a
probability: Gilchrist's S(p)) is not classified, since its letter cannot confirm the quantity. Steps
of a derivation are not definitions: three or more equalities with one left side are left out, and of
a chain of two (or rows that continue with "=") only the first equation is checked; a footnote mark
`(...)^{*}` is dropped.

Checks, at four stratified positive draws of every parameter:

- density: non-negative, integrates to 1 over the support its own condition gives
  (0 < x < θ: integrated over (0, θ); x = 0, 1, ..., n: summed), else over (0, ∞), (−∞, ∞) or
  (0, 1) (the support that worked is reported; 2-D with a bounded cubature); with no condition and
  no standard support that integrates to 1 the check is inconclusive ("no support"), not failed;
- cdf and survival: within [0, 1], monotone, the right limits;
- mgf, cf, pgf: 1 at 0 (pgf at 1), and a non-negative variance, M″(0) − M′(0)²
  in 1-D and the Hessian minus the outer product of the gradient PSD in 2-D
  (this is what catches a sign error such as exp{−½ t′Σt}, which still equals
  1 at 0);
- pmf (`\Pr[X = x]`, `P(X = x)`, `p_x`; a density `f(x)` / `p(x)` that integrates to
  nothing near 1 is tried as a pmf too): non-negative, sums to 1 over {0, 1, …},
  {1, 2, …} or {0..n} for an integer parameter n (the support that worked is reported);
- pairs in the same section (between headings, within a page run): F′ = f,
  h = f/S, S = 1 − F, a joint survival at x₂ = 0 against the marginal;
- a printed mean (`E[X]`, `\mu`, `\mu'_1`) and variance (`\operatorname{Var}(X)`, `\sigma^2`,
  `\mu_2`) against the section's MGF (M′(0), M″(0) − M′(0)²), PGF (G′(1),
  G″(1) + G′(1) − G′(1)²) and pmf (Σ x p(x), Σ x² p(x) − mean²), by numerical derivatives
  and sums;
- a recurrence (pmf values on either side: `\frac{\Pr[X = x+1]}{\Pr[X = x]} = …`,
  `p_{x+1} = … p_x`, `(x+1) p_{x+1} = …`) and a point value (`\Pr[X = 0] = …`) against the
  section's pmf, both sides at x = 1, 2, 3.

For a discrete distribution (pmf, PGF, recurrence) n, m, M and N are drawn as integers and
p, θ, π, ω in (0.1, 0.9); everywhere q is 1 − p and Q is 1 + P (JKK's negative binomial)
unless the section defines them. A section's constants (`C = …`, `c`, `K`, `q`, `Q`
printed as a display anywhere in the section, before or after the formula) are put in;
an unnamed constant still skips the display. Equations separated by a comma, `\quad` or a
word (`\mu = np, \quad \sigma^2 = npq`) are checked one by one; side conditions with
dots (`x = 0, 1, \ldots`) are dropped, and an equation with a sum, an integral or dots in
it is left out.

Parameter constraints are unknown, so a check that fails for some draws and
passes for others is **inconclusive**: counted, not flagged. A failed check on a quantity the display does
not type by itself (taken from the prose before it, or a letter with an unusual variable such as G(x)
as a pgf) is **unsure**: it still becomes a review item, with the reason "identity unsure". Each display gets
about 5 s; on timeout it is skipped. A failure becomes an `identity` review
item (or joins the display's existing item) with the identity, the parameter
values and the numbers, and a note that it may be a printed error rather than
a misread: the reviewer compares the crop and, if the LaTeX matches the page,
marks `possible_source_typo` instead of changing it; the packed item carries the check
as `identity_check` (`kind`, `failed`, `numbers`), and math-apply-review gives such a
flag `"subtype": "math_error"` (a decision may also set `subtype` itself). `run.json` has the
per-page counts: checked, passed, failed, inconclusive, skipped.

## Applying the review

```powershell
gpdf-cli math-apply-review --export OUT\book --decisions review_decisions.jsonl
```

`--decisions` is a file or a folder (every `*.jsonl` in it, in name order: one file per
review batch, as `gpdf-cli math-review-pack` lays them out; see docs/math-review.md).
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

Accept and flag on such an item change no text. A display may get a replace and a flag (two lines); the flag finds the display by the
replaced LaTeX.

A replace on a display is compared with the old LaTeX (for a missing display, only
its own balance is checked) and refused when it loses or unbalances the layout: fewer
`\\` row breaks (a trailing one before `\end`, also with only `\hline` rules between, and the
rows of a `\substack` are not counted), fewer rows aligned with `&` (an `r c l` array's `& = &` and an aligned `&=` count alike), fewer `\begin`/`\end` environments (renaming
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

## Provenance: formulas.jsonl and the method block

```powershell
gpdf-cli math-provenance --export OUT\book --reviewer sonnet [--out DELIVERY_DIR] [--meta DELIVERY_DIR\meta.json]
```

Run it after the review is applied. It writes `formulas.jsonl` next to `text.md`, with one
line per display and inline span of the final pages in page order and no formulas:

```json
{"id": "p073-d02", "file": 73, "printed": "54", "kind": "display", "tag": "44.74",
 "status": "replaced", "reviewer": "sonnet", "flags": [], "notes": [], "review_item": "p073-d02"}
```

With `--flag-latex` (opt-in) each entry of `flags` also quotes the formula as the final page
has it, `latex`: the flag's own LaTeX when it is part of the display (a flag on one row or a
fragment), else the display without its `\tag`; for an inline flag, its span. Without it,
formulas.jsonl holds no formulas.

`id` gives the formula's position in the final page (`d` for displays, `i` for inline).
`status` is the first of these that applies:

- `flagged`: a reviewer's flag. `flags` lists the type and any `subtype`, and
  `"replaced": true` marks a display that was also replaced.
- `replaced`, `reviewed_accept`: a reviewer's replace or accept.
- `unreviewed`: in the review queue but not reviewed, such as a style item or an inline
  span outside the sample. `notes` gives the item kinds.
- `identity_checked`: not queued, and a numeric identity check passed.
- `engines_agree`: not queued; every engine read it the same way.

Formulas are matched to queue items, decisions and flags by their LaTeX. A flag whose LaTeX no
longer equals any display, for example after a split or a corrected field, goes to the display
whose `\tag` its `eq` names. Failing that, it goes to the first display that contains its LaTeX, as
with a flag quoting part of a display. `reviewer` is set
only for reviewed formulas. `--review-dir REVIEW_DIR` takes a packed review folder and can be
given more than once. Where a reviewer's accept on an inline item names no spans, it counts
for the spans sampled on that page that no other decision covers. The identity results come from `identities.jsonl`, which
math-export writes beside `review.jsonl` when the identity pass runs: one line per checked
display, with page, position, LaTeX, kind, status and the checks.

The command also writes a `method` block into `meta.json`, either the export's or the
file given by `--meta`. The block holds:

- `engines`: MinerU, marker-pdf, surya-ocr and llama.cpp, with versions;
- `ocr_mode` and `dpi`;
- `input`: the file name, SHA-256 and pages, plus the DjVu details for a DjVu;
- `reviewer_model`;
- `coverage`: displays and inline spans reviewed, as counts and %, and the number of
  numeric checks and failures;
- `formulas_by_status`.

## Cross-checking against SciPy

```powershell
gpdf-cli math-crosscheck --spec SPEC.json --out DIR --scipy-python C:\path\python.exe [--formulas DELIVERY\formulas.jsonl --meta DELIVERY\meta.json]
```

A per-book spec (JSON, kept private: it quotes the book's notation) names the final pages
(a text.md, pages folder or export), an explicit **parametrisation table** (per
distribution: the SciPy name, SciPy's arguments as Python expressions in the book's
parameter names, such as `"scale": "1 / lambda_"` (a keyword gets a trailing `_`), and
three parameter points with the argument of each quantity: `@x`, `@t`, `@z`, `@p`), the
**entries** (located by `\tag`, by page and display number, or by an inline pattern; `row`
and `eq` pick a row and the side) and **scan** ranges, where every display row whose
left-hand side names a quantity (`f(x)`, `p_X(x)`, `F(x)`, `\Pr[X = x]`, `M(t)`, `\varphi(t)`,
`G(z)`, `E[X]`, `\operatorname{var}(X)`, `\mu'_r`, `E[X^r]`, `\mu_r`, `\kappa_r`,
`\sqrt{\beta_1}`, `\beta_1`, `\beta_2`, `H(X)`, median, mode) is harvested for that section's
distribution. `notation` rewrites (regex, replacement) and `defs` put the book's own
functions and constants in before parsing. A table row can name `standard` values (a
density printed without σ is the σ = 1 form; the result records it) and `derived`
parameters (θ = αβ, φ = λ/μ), computed after them. `skip` and `overrides` (by harvested
id) take out or adjust harvested entries. **identify** ranges are for a book whose summary
tables lost their row labels (Forbes): every display row and every inline span with an
operation in it is tried against each quantity its free variable allows (none: mean, var,
sd, skewness, kurtosis, mode, median, entropy, coefficient of variation, raw moments of
order 2-4, central moments and cumulants of order 3-4, a constant pdf; x: pdf/pmf, cdf, sf, hazard, cumulative
hazard; t: mgf, cf, log cf, cgf (pgf for a discrete row); s: Laplace transform; r:
moments, cumulants; α, p: inverse survival, quantile) and passes under the first it
matches; one that matches none is `unidentified` (inconclusive, with the closest quantity)
and goes to the page image.

**Piecewise formulas and side conditions.** A display `lhs = \begin{cases} value & condition
\\ ... \end{cases}` (or a left brace with an `array` or `aligned`) is one entry checked branch
by branch: at each point only the branch whose condition holds is evaluated (the first that
holds; an `otherwise` branch when every other fails). A row without `&` takes its condition
after a comma, `\quad` or a word (`if`, `for`); a row that starts with an operator continues the
previous value. A plain row keeps its side condition the same way (`F(x) = 0, \quad x < 0`, or a
row of its own that is only a relation). A condition is read as chained relations (`<`, `\le`,
`=`, `\ne`, ...; `\infty` allowed) joined by commas or `and`, a parity (`r` even/odd), or a
support list (`x = 1, 2, \ldots` or `k = 0, 1, \ldots, n`: integers from the first value, to the
last when one is printed); a condition that cannot be read (`x \in \mathbb{N}`) is no
condition, so the points stay as they were. A point outside every condition is skipped and counted (`outside`);
an entry with no point inside is inconclusive. A table row or an entry may give `probe`
arguments (`{"@x": [-0.7]}`), tried at every point for entries with a condition only, so a
branch the points miss (x < 0 of a cdf) is reached. The result lists each branch with its
condition and the points it got; a formula with a branch that got no point is not marked
`crosschecked` (its record says `partial`).

**Multivariate distributions (step 1).** A table row with `k` (the dimension, what the book's
`k` stands for) is multivariate: `multinomial`, `multivariate_hypergeom`, `dirichlet_multinomial`,
`negative_multinomial`, `bivariate_poisson` and `bivariate_binomial` (trivariate reduction), and
`multivariate_normal` (`args` may be lists: `"p": "[p_1, p_2, p_3]"`, `"cov": "[[...], [...]]"`).
A point's `"@x": [2, 3, 1]` gives the components `x_1, x_2, ...` (`var`, `first` and
`components` rename or renumber them; `"@t"` a pgf's, mgf's or cf's vector); `derived` parameters
are computed in order and see the components. Before parsing, at each point, a sum or product over
a range the point fixes (`\sum_{i=1}^{k}`, `\prod_{i=0}^{k}`, `\sum_{i=0}^{\min(x_1, x_2)}`) is
written out term by term, an entry's free indices go in (`"index": [1, 2]` for i, j), a dotted
list (`x_1 + \cdots + x_k`, `n_1! \cdots n_k!`, `x_1, \dots, x_k`) is written out, a multinomial
coefficient `\binom{n}{x_1, x_2, x_3}` becomes `n!/(x_1! x_2! x_3!)`, and a bold symbol with a
subscript is that component. Quantities: `pmf`/`pdf`, `marginal_pmf`/`marginal_pdf` (`index`;
`sum_out` sums the book's joint over the other component instead), `mean`, `var`, `cov`, `corr`
(`index`), `fact_moment` (`r`, one order per component, falling factorials), `pgf`, `mgf`, `cf`,
`cond_pmf`, `cond_mean`, `cond_var` (`index` and `given`). The reference sums exactly over the
support (a cut where each marginal's tail is below 1e-18 for an infinite one, the mass left out
recorded as the tolerance floor) and uses closed forms for the normal; `ref_x` gives the
reference's vector when the book leaves a cell out (`[n - x_1 - x_2, x_1, x_2]`).

**Matrix variate (step 2).** A table row with `"matrix": true` (or a `matrices` / `vectors` list)
gives matrices and vectors in its points as nested lists (`"Sigma": [[1, 0.3], [0.3, 2]]`; a flat
list is a column vector), and `var` names the variable the reference reads (`"W"`; a point's `"@t"`
may name a matrix too: `"Theta"`). In its formulas a bold symbol or a name in `matrices` / `vectors`
is a matrix or vector (a subscript is part of the name; `x_1` beside a vector `x` stays a scalar
unless the point gives `x_1` a matrix) and `I`, `I_p` is the identity. Before the scalar parser
(`crosscheck_mat.py`, mpmath): `|A|`, `\det`, `\operatorname{tr}`, `\operatorname{etr}` (exp of the
trace) and `\|v\|` over a matrix are evaluated and put in as numbers (bars around a scalar stay an
absolute value); every other maximal product holding a matrix (a quadratic form) must come out
1 x 1 and is put in; `\Gamma_p(a)` and `B_p(a, b)` are written as products over j = 1..p. The
grammar: sums, products, scalar factors, `\frac` with a scalar denominator, `\otimes`, transposes
(`'`, `^T`, `^\top`), `^{-1}`, the symmetric `^{1/2}` and `^{-1/2}`, integer powers, exp / log / sqrt
of a 1 x 1 result; anything else leaves the entry inconclusive ("matrix expression not read").
A piece that is a whole matrix or vector (`n \Sigma`, `\boldsymbol{\mu}`) is compared element by
element with the reference's whole mean, mode or covariance (of vec(X), columns stacked). References:
SciPy's `dirichlet`, `multivariate_t`, `wishart`, `invwishart`, `matrix_normal`, `vonmises_fisher`
and `multivariate_normal` for pdf and entropy; closed forms for mean and mode (`index` [i] or [i, j]),
element variances and covariances (`index` [[i, j], [k, l]]: Wishart n(s_ik s_jl + s_il s_jk), the
inverted Wishart's, U_ik V_jl), marginals (Dirichlet beta, multivariate t, a Wishart diagonal
element s_ii chi-square, a matrix normal element), the Dirichlet `mixed_moment` (`r`), and the
Wishart and matrix normal mgf / cf.

**Closed forms from a book's own definitions.** A family no library has (Topp–Leone, Marshall–Olkin,
a bivariate gamma) takes `"scipy": "closed_form"` and an `expr` of Python expressions in the row's
`args` (mpmath / SciPy special functions by name: `exp`, `gamma`, `besseli`, `e1`, ...). Univariate:
`pdf` (or `pmf`) in `x` with `support` [lo, hi] (and `breaks`, points where the density has a kink);
every quantity comes by quadrature or an exact sum of that density. Bivariate (`k` 2): the joint
`sf` in `x_1, x_2` on [0, inf)^2, or on the `support` it gives (singular parts allowed; for a support
bounded below, moments come from the survival function of X minus its lower bounds, the density and the
marginals by differentiation) or the joint `pdf` with `support` [[lo1, hi1], [lo2, hi2]];
quantities pdf, sf, cdf, marginal_pdf, mean, var, cov, corr, mixed_moment (`r`), cond_pdf (divided
by the marginal), cond_mean, cond_var, cond_moment (`index` [i, other]), mgf. A quantity given as its
own expression in `expr` is evaluated as it stands. The quadrature's error estimate is the tolerance
floor. Such a reference tests the book against itself (internal consistency), not an independent
library: mark the row `"internal_consistency": true`.

Quantities: pdf, pmf, cdf, sf, hazard, quantile, raw and central moments, cumulants, mean,
var, sd, skewness (`skew`, `skew2` = β₁), kurtosis (`kurt` = β₂, `excess_kurt`), mgf, cgf,
cf, pgf, entropy, mode, median. Each entry is evaluated at the three points, the book's side
through the identity checks' LaTeX parser (sympy, mpmath) and SciPy's side by
`crosscheck_ref.py` in the external Python (SciPy is never a g-pdf dependency; the worker
imports nothing from g-pdf), and compared at a relative tolerance of 1e-8 (absolute where
the reference is 0), loosened only to ten times the reference's own quadrature error
estimate (mgf, cf, pgf by quadrature or sum; the reason is recorded); a log cf is compared
on the reference's branch of the complex log (modulo 2πi). A parametrisation
mismatch is fixed in the table, never counted as a failure. An entry is **inconclusive**
when it can't be evaluated: it does not parse, it leaves symbols the table does not give,
SciPy has no value, or it runs past the 20 s per-entry limit (the reference worker is
restarted after a request past it). `crosscheck.json` and `REPORT.md` (formulas) go to
`--out`, counts by quantity to the screen; exit code 2 when an entry failed. A failure is
checked against the page image: an engine misread is fixed in the text and recorded as a
replace (reviewer `cross-check`), a printed error gets an independent re-check before it is
flagged `possible_source_typo`. With `--formulas`, every formula all of whose entries passed
gets status `crosschecked` in that formulas.jsonl (the earlier status kept as `was`; a
flagged formula stays flagged; a copy is kept as `formulas.jsonl.before-crosscheck`), and
`--meta` updates `method.formulas_by_status`.

## Scoring

```powershell
gpdf-cli math-score --ref REF_DIR --cand OUT\book --out SCORES [--pages 63-81]
```

`--ref` and `--cand` are page folders (`pNNN.md` + `pNNN.flags.json`),
math-export folders, or `text.md` files (split by their page markers). A flags file that is
not a readable JSON list of objects is an error naming the file (exit code 1), never
read as "no flags".

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
the report lists them with what was ignored. Where the reference writes a figure as a
placeholder line (`[Figure 17.2: caption]`), that line and the candidate's caption line for
the same figure (similarity ≥ 0.8; a sentence about the figure stays) are left out of both
(`figure_captions_left_out`). The
scorer is the trial's: displays normalized (sizing, spacing and style wrappers
dropped, synonyms folded, alignment environments reduced, script order and
grouping braces canonicalised, the tag split off), aligned by tag then reading
order (joining a display the candidate split in two), then exact match, mean
token edit distance, tag accuracy, missed and extra formulas, near-misses of
1-3 tokens split into value-changing and cosmetic, the inline exact rate (a bag
of spans; `inline_style_free_pct` is the same with style commands, braces and spaces taken
out, for font substitution), the prose character error rate and the page-label accuracy.
Cosmetic means the two readings agree once style commands (`\mathbf`,
`\mathbb`, `\mathcal`, …), dot style, trailing punctuation and line breaks are
taken out; a cosmetic difference that involves a style command is a style
error. **Layout errors** are a class of their own, reported beside value
(`value_changing_errors`) and style (`style_errors`) errors and never cosmetic: a
matched display whose `\\` row breaks differ in number (a trailing break before `\end`
and `\substack` rows not counted; a row run into the next by a stray `\ ` is caught
here although the token comparison calls it cosmetic), and a reference display that the
candidate set as inline math (missed, with an unmatched inline span of the page within
0.2 normalized edit distance). `layout_errors`, `layout_row_breaks` and
`layout_display_as_inline` are in the summary, the list in `REPORT.md`. Notation-only forms compare equal: prime and script order (`x'_1` =
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

## Checking page structure

```powershell
gpdf-cli math-check-pages REF_OR_OUT_DIR [--out check.json]
```

Runs on any folder of `pNNN.md` / `pNNN.flags.json` (a reference transcription or
math-export output; an export folder is read through `pages/`) or on a `text.md` split by
its page markers. Per page: balanced `$$` and `\( \)`; in multi-line environments
(`aligned`, `array`, `cases`, the matrices, ...) a line ending in a single `\` (or a
`\[2ex]` with one backslash) where a `\\` row break was meant (`single_backslash_break`),
and an environment of several lines without any row break (`no_row_breaks`); balanced
braces, `\begin`/`\end` and `\left`/`\right` in every display and inline span; anywhere on
the page, a control character (below U+0020 except the newline: `control_character`) and an
orphaned escape fragment, what a helper script leaves of a command when a string escape
eats its backslash and first letter (`oldsymbol`, `heta`, `rac{`, `arepsilon`, `imes`,
`ilde{`, `athbf{`, `ightarrow`, ... not preceded by a backslash or a letter:
`escape_fragment`); a flags file that parses as a JSON list of objects (`flags_json`,
`flags_missing`). The screen gets
counts by kind and the pages; `--out` lists each problem with its formula. Exit code 2 when
there are problems. Run it on a reference before scoring against it.

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
