# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pairing two lists of formulas (the trial scorer's alignment, ported as is): by tag
first, then Needleman-Wunsch in reading order on token similarity, then a pass that
joins an unmatched neighbour to a pair when that lowers the edit distance (an engine
that split one display in two)."""

from __future__ import annotations

from gpdf.pipeline.mathocr import latex as L

MIN_SIM = 0.35
JOIN_GAIN = 0.02


def cat(items: list[dict], idx) -> list[str]:
    out = []
    for i in sorted(idx):
        out += items[i]["toks"]
    return out


def align(ref: list[dict], cand: list[dict], min_sim: float = MIN_SIM):
    """ref/cand: dicts with 'toks' and 'tag'. Returns (pairs [(ref idx list, cand idx
    list)], unmatched ref indices, unmatched cand indices)."""
    pairs, used_r, used_c = [], set(), set()
    for i, r in enumerate(ref):
        if r["tag"]:
            for j, c in enumerate(cand):
                if j not in used_c and c["tag"] == r["tag"]:
                    pairs.append(([i], [j]))
                    used_r.add(i)
                    used_c.add(j)
                    break
    R = [i for i in range(len(ref)) if i not in used_r]
    C = [j for j in range(len(cand)) if j not in used_c]
    n, m = len(R), len(C)
    sim = [[1 - L.ned(ref[R[a]]["toks"], cand[C[b]]["toks"]) for b in range(m)] for a in range(n)]
    S = [[0.0] * (m + 1) for _ in range(n + 1)]
    for a in range(1, n + 1):
        for b in range(1, m + 1):
            s = sim[a - 1][b - 1]
            S[a][b] = max(S[a - 1][b], S[a][b - 1], S[a - 1][b - 1] + (s if s > min_sim else -1))
    a, b = n, m
    while a and b:
        s = sim[a - 1][b - 1]
        if s > min_sim and S[a][b] == S[a - 1][b - 1] + s:
            pairs.append(([R[a - 1]], [C[b - 1]]))
            used_r.add(R[a - 1])
            used_c.add(C[b - 1])
            a -= 1
            b -= 1
        elif S[a][b] == S[a - 1][b]:
            a -= 1
        else:
            b -= 1
    pairs.sort(key=lambda p: (min(p[0]), min(p[1])))
    for side, used, items in ((1, used_c, cand), (0, used_r, ref)):
        improved = True
        while improved:
            improved = False
            for p in pairs:
                for k in (min(p[side]) - 1, max(p[side]) + 1):
                    if 0 <= k < len(items) and k not in used:
                        trial = sorted(p[side] + [k])
                        cur = L.ned(cat(ref, p[0]), cat(cand, p[1]))
                        new = L.ned(cat(ref, trial if side == 0 else p[0]),
                                    cat(cand, trial if side == 1 else p[1]))
                        if new < cur - JOIN_GAIN:
                            p[side][:] = trial
                            used.add(k)
                            improved = True
    return (pairs, [i for i in range(len(ref)) if i not in used_r],
            [j for j in range(len(cand)) if j not in used_c])


def unit(raw: str, trailing: str | None = None) -> dict:
    """An alignment item from raw display LaTeX."""
    body, tag = L.split_tag(raw, trailing)
    return {"raw": raw.strip(), "toks": L.tokens(body), "tag": tag}
