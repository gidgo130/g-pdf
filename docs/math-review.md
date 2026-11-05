# math-review: the standard review of a math-export queue

`gpdf-cli math-export` flags the formulas worth a second look (docs/math-export.md); this
is how they are checked. A reviewer reads **one short brief and one batch, nothing
else**; `gpdf-cli math-review-pack` writes both. The procedure comes from the KBJ ch. 47
check (MATH_3): every quality target was met, but at 0.67× the cost of a vision
transcription. The inline check took 57 % of the tokens and fixed no values, Sonnet
matched Opus on 39 of 40 items, and each 25-item batch spent 20–30k tokens on reading
its brief and inputs. Hence: display items only, a small inline sample, Sonnet, batches
of 50, and a brief of about a page.

## What is reviewed

- **Displays, all of them** in the queue, by kind: `disagree`, `marker`, `style`,
  `identity`, `stack`, `tag`, `missing_display`, `syntax` (the pack's default `--kinds`).
- **Inline math, a sample**: 40 flagged spans per chapter (all of them when there are
  fewer), picked at random with a recorded seed (`--inline-sample 40`, the default, `--seed
  S`), each its own item with a crop of its own line. Review all of a chapter's inline
  items only when the sample finds **2 or more value errors** (a `replace` that changes
  a value, not only style), or **1** when the chapter's sample has fewer than 20 spans:
  at the measured ~1 % inline value-error rate, one error in 40 spans is expected and
  says nothing about the chapter. `gpdf-cli math-review-followup REVIEW_DIR [--out
  FILE]` applies the rule from the decisions and prints, per chapter, the spans sampled,
  the value errors and the `--pages` to pack again with `--kinds inline`.
- **No prose review.** The engine's prose error rate is already under the target.

## How

1. Pack: `gpdf-cli math-review-pack --export OUT\<stem> --out REVIEW_DIR --inline-sample
   40 --seed S [--pages RANGE]` (`--batch-size 50` is the default). `REVIEW_DIR` must
   be in the run's private folder, never a temp or scratch folder.
2. One subagent per batch, **Sonnet** (see "Which model" below), with a fresh context and this prompt:
   "Read `REVIEW_DIR\batchNN\BRIEF.md` and do what it says." Nothing else goes into the
   prompt: the brief and the batch are self-contained.
3. Each finished batch: append one line to `REVIEW_DIR\cost_log.jsonl`:
   `{"batch": "batch01", "model": "sonnet", "items": 50, "tokens": 61234, "seconds": 410}`
   (tokens and time as the agent run reports them).
4. Apply all batches at once: `gpdf-cli math-apply-review --export OUT\<stem> --decisions
   REVIEW_DIR\decisions` (a folder: every `*.jsonl` in it). Refused decisions are listed
   with their reason; a structure refusal (a lost `\\` row break, `&`, environment,
   `\left`/`\right` pair, or unbalanced braces) is fixed in the decision, not forced.
5. Score against a reference when there is one (`gpdf-cli math-score`); layout errors are
   reported apart from value and style errors.

Helper scripts, renders and notes stay in the batch folder: they hold the book's formulas.

## Which model

Sonnet reviews the displays. Batch B-1 tested Opus on the 12 KBJ items where Sonnet had
gone wrong: 7 printed-typo flags it had dropped and 5 accept-vs-replace items, using the
current brief. The rule was to switch to Opus if it recovered at least 5 of the 6
reference-confirmed typos with no new value errors. Opus recovered 4 of the 6 (5 of the
7 flags) and 5 of the 5 fixes, using 84k tokens for the batch (Sonnet: 74k). Both models
plain-accept a display that reads correctly but is printed wrong. So Sonnet stays, and
that kind of typo is left to the numeric identity checks (docs/math-export.md).

## The reviewer's brief

The pack copies the text between the markers into each batch's `BRIEF.md`, filling in
the `{…}` fields.

<!-- brief -->
# Math review, {batch}: check OCR'd LaTeX against the printed page

You check {count} flagged formulas from an OCR pipeline against the page images and
write one decision per item. The book is copyrighted: everything you write stays in
`{batch_dir}`. Read only this brief, `{items}` and the image files it names. Open no
other file or folder, and do not search for reference transcriptions or scores.

**Transcribe what is printed. Never correct the book's mathematics.**

## Each item (one JSON line in items.jsonl)

`latex_primary` is what the page file holds now. `latex_second` (a smaller model) and
`latex_marker` (another engine) are hints only; **the crop decides**. `context` is the
page text just before the formula. `crops` are 300 dpi images: view them with the Read
tool. For more detail, render a region: `{render} FILE_PAGE OUT.png --box X0 Y0 X1 Y1
--dpi 500`, where the box is in page fractions (0..1, left top right bottom) and OUT
lies under `{batch_dir}\renders\`.

Compare symbol by symbol: signs, sub/superscripts, primes, bars/hats/tildes, Greek vs
Latin, bold/roman/calligraphic, fraction structure, sum/integral limits (a printed
double sum is two `\sum`), brackets, missing or extra terms, side conditions, trailing
punctuation, the equation number. Check sub/superscript **nesting** against the crop:
which symbols sit in a sub-subscript, and where a subscript ends (in `p_{i_\ell|i_1}` the
bar is outside the sub-subscript; moving it inside changes the formula).

If the reading matches the page but the page looks mathematically wrong (a sign, a
missing factor, an exponent), decide `flag` with `possible_source_typo` and say what
looks wrong in the note; never plain `accept`.

- `disagree`, `marker`, `style`, `stack`, `syntax`: a display; check all of it.
- `identity`: a numeric check failed (`identity_check` gives which one and its numbers),
  and the checker is often wrong. LaTeX matches the
  page and the math is fine: `accept` with a reason. It matches but the printed math is
  wrong: flag `possible_source_typo`. It misreads the page: `replace`.
- `tag`: read the printed number; if it was misread, add `"tag": "<number>"`. If the
  misread number also appears in the body (`\tag{4'}` or a stray `(47.1)` inside the
  formula), decide `replace` on the whole display, body without the number, plus the
  `"tag"`. Never accept plus tag when the body holds a misread number.
- `missing_display`: another engine saw a display that the page lacks (`latex_primary`
  is null; `page_md` holds the page file). Write the display in full and say where it
  goes: `"page_text"` (an exact, unique piece of `page_md` that it replaces) or
  `"after"` (an exact, unique piece it follows).
- `inline_missing`: prose that may be math the engine left as text (`runs`, each with its
  page `text`, why it was picked, a line crop and its `occurrence`). For each run that the
  page prints as math, write it as inline LaTeX: `replace` with `"runs": [{"text": "<exact
  page text>", "latex": "...", "occurrence": n}]` (the text may be part of the run; one
  line for all the item's runs). If none is math, `accept`.
- `inline_span`: one inline span. Its crop shows the span's line with one line above and
  one below (`context` gives the text around it). Find the span in the crop first. If you
  can't find it there, decide `flag` with `formula_uncertain` and say so in the note,
  never `accept`.

Change something only when the page differs in **value** or **style**. Leave
notation-only variants alone: `x'_1` vs `x_1'`, spacing, grouping braces, `\dots` vs
`\cdots`, `aligned` vs `array`.

## Keep the structure

A `replace` starts from `latex_primary` and changes only what is wrong. Keep every `\\`
row break, every `&`, every `\begin`/`\end` pair and every `\left`/`\right` pair.
**Never** write `\ ` where a row break was. If you build the LaTeX with a script, write
the JSON with `json.dumps` from a raw string (`r"..."`), never by hand-escaping. The
apply step refuses a replace that loses any of these. If the page really prints it
differently (the rows on one line), add `"structure_change": true` and a `"reason"`.
Write the whole display body without `\tag{…}`.

## Decisions: `{decisions}`

`{template}` has one line per item with `"action": "TODO"`. Copy it to `{decisions}` and
fill it in: every item gets at least one decision. Every replace and flag carries a short
`"reason"`; for a flag, also put it in `"note"`.

```
{{"id": "p012-d01", "action": "accept"}}
{{"id": "p012-d01", "action": "replace", "latex": "...", "reason": "printed sign is minus"}}
{{"id": "p012-d02", "action": "flag", "flag_type": "possible_source_typo", "note": "...", "reason": "..."}}
{{"id": "p014-d01", "action": "accept", "tag": "4.27", "reason": "tag misread"}}
{{"id": "p015-inline", "action": "replace", "spans": [{{"latex_primary": "x_1", "latex": "x_2", "occurrence": 1}}], "reason": "printed x_2"}}
```

For an inline span, the id is the page's inline item. A `replace` lists the span, with
`latex_primary` copied exactly and the `occurrence` the item gives. `accept` means the
span is right; keep the template's `"spans"` line in an accept too (it says which span). Flag types: `possible_source_typo` (the LaTeX matches a page that looks
wrong), `copy_corrupt` (the printed page itself is damaged or garbled: write what is
shown and give the likely reading in the note), `formula_uncertain` (you can't decide;
a flag is better than a guess), `illegible`. An item may get both a `replace` and a
`flag` (two lines).

Scripts and scratch files go under `{batch_dir}\work\` only, never in a temp folder.
Write `{batch_dir}\notes.md` with one line per replace or flag.

**Reply** (short, no formulas): counts of accept / replace / flag, the ids you flagged,
anything you couldn't do, and a line confirming you opened only the files allowed above.
<!-- /brief -->
