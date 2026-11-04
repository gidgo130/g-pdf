# SPDX-License-Identifier: AGPL-3.0-or-later
r"""The basic tier as a second opinion on the standard tier's displays.

``pair_displays`` matches a page's displays with the other tier's (by tag, then reading
order; a display the other tier split in two is joined). ``strip_bars`` removes the
engine artifact of a \left| ... \right| (or \right.) wrapped around a whole display,
only when the second tier's reading of that display carries no such bars: real
absolute values exist, and a display both tiers wrap stays as it is.
"""

from __future__ import annotations

import re

from gpdf.pipeline.mathocr import align
from gpdf.pipeline.mathocr import latex as L
from gpdf.pipeline.mathocr.assemble import Display, Page, display_md

BARS_RE = re.compile(r"^\s*\\left\s*\|\s*(.*?)\s*\\right\s*[|.]\s*$", re.S)


def pair_displays(displays: list[Display], others: list[str]) -> list[tuple[Display, str | None]]:
    """For each display, the other tier's raw LaTeX for it (several joined with a
    newline), or None."""
    ref = [align.unit(d.raw) for d in displays]
    cand = [align.unit(t) for t in others]
    pairs, _, _ = align.align(ref, cand)
    other_of = {}
    for ri, ci in pairs:
        for i in ri:
            other_of[i] = "\n".join(cand[j]["raw"] for j in sorted(ci))
    return [(d, other_of.get(i)) for i, d in enumerate(displays)]


def _has_bars(latex: str) -> bool:
    body, _ = L.split_tag(latex)
    if BARS_RE.match(body):
        return True
    toks = L.tokens(body)
    return bool(toks) and toks[0] == "|" and toks[-1] in ("|", r"\|")


def strip_bars(page: Page, pairs: list[tuple[Display, str | None]]) -> int:
    """Strip the bars of each display that the second tier reads without them; records
    an auto_fix flag per change. Returns the number of displays changed."""
    fixes = []
    for n, (d, other) in enumerate(pairs, 1):
        m = BARS_RE.match(d.latex)
        if not m or other is None or _has_bars(other):
            continue
        before, after = d.latex, m.group(1)
        if not after or any("left" in p or "right" in p for p in L.balance_problems(after)):
            continue                      # the bars are not one pair round the whole
        d.latex = after
        for it in page.items:
            if it.display is d:
                it.md = display_md(d)
        fixes.append({"type": "auto_fix", "what": "bars",
                      "eq": f"display {n}" + (f" ({d.tag})" if d.tag else ""),
                      "before": before, "after": after})
    page.flags[0:0] = fixes
    return len(fixes)
