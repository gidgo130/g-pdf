# SPDX-License-Identifier: AGPL-3.0-or-later
"""gpdf-cli math-score: a candidate transcription against a reference, page by page
(the trial scorer, mathocr-trial/scripts/score.py, ported as is).

Displays are normalized (latex.tokens), aligned by tag then reading order (align.align),
and scored: exact match, token edit distance, tag accuracy, missed and extra formulas,
near-misses of 1-3 tokens split into value-changing and cosmetic; inline spans as a bag
(exact rate); prose character error rate. A display split across a page break (a
page_anomaly flag on the second page) is joined on each side before scoring.

With a review.jsonl beside the candidate, flag recall: the share of the candidate's
wrong formulas that some review item covers, by the kind of the covering reasons.

score.json and REPORT.md (formulas quoted only there) go to --out; the terminal gets
numbers only.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

from gpdf.pipeline.manuals import ManualsError, parse_pages, write_atomic
from gpdf.pipeline.mathocr import align
from gpdf.pipeline.mathocr import latex as L

MARKER_RE = re.compile(r'<!-- page printed="([^"]*)" file="(\d+)" -->')
SPLIT_NOTE = re.compile(r"continu|split|reopen", re.I)
KIND_OF_REASON = [("tiers disagree", "disagree"), ("no second reading", "disagree"),
                  ("style differs", "style"), ("both bold", "style"),
                  ("index variables", "stack"), ("relation in the limits", "stack"),
                  ("sum of indices", "stack"), ("unbalanced", "syntax"), ("without", "syntax"),
                  ("unknown command", "syntax"), ("tag ", "tag"), ("inline", "inline"),
                  ("marker disagrees", "marker"), ("standard tier lacks", "missing_display"),
                  ("gave no display", "missing_display"), ("prose", "prose"),
                  ("identity failed", "identity")]


class ScoreError(Exception):
    pass


# ---------------------------------------------------------------- loading

def _pages_from_text(text: str) -> dict[str, str]:
    out = {}
    marks = list(MARKER_RE.finditer(text))
    for k, m in enumerate(marks):
        end = marks[k + 1].start() if k + 1 < len(marks) else len(text)
        out[f"p{int(m.group(2)):03d}"] = text[m.start():end].rstrip("\n") + "\n"
    return out


def read_pages(src) -> tuple[dict[str, str], dict[str, list], Path | None]:
    """{stem: md}, {stem: flags}, the folder holding review.jsonl (if any) of a pages
    folder, an export folder (its pages/ subfolder) or a text.md file."""
    src = Path(src)
    if src.is_file():
        mds = _pages_from_text(src.read_text("utf-8"))
        folder = src.parent
    elif src.is_dir():
        folder = src / "pages" if (src / "pages").is_dir() else src
        mds = {p.stem: p.read_text("utf-8") for p in sorted(folder.glob("p[0-9][0-9][0-9].md"))}
        if not mds and (folder / "text.md").is_file():
            mds = _pages_from_text((folder / "text.md").read_text("utf-8"))
        folder = src
    else:
        raise ScoreError(f"{src} not found")
    if not mds:
        raise ScoreError(f"no pNNN.md pages (or page markers) found in {src}")
    flags = {}
    for stem in mds:
        for d in (folder / "pages", folder, src.parent if src.is_file() else src):
            f = d / f"{stem}.flags.json"
            if f.is_file():
                try:
                    flags[stem] = json.loads(f.read_text("utf-8"))
                except json.JSONDecodeError:
                    flags[stem] = []
                break
        flags.setdefault(stem, [])
    return mds, flags, folder


def _continues(flags: list) -> bool:
    return any(f.get("type") == "page_anomaly" and (
        f.get("what") == "split_display" or SPLIT_NOTE.search(str(f.get("note", ""))))
        for f in flags)


def link_points(mds: dict[str, str], flags: dict[str, list]) -> set[str]:
    """Pages (stems) whose first display continues the previous page's last one, by their
    page_anomaly flags."""
    return {s for s in mds if _continues(flags.get(s, []))}


def load_units(mds: dict[str, str], flags: dict[str, list],
               links: set[str] | None = None) -> dict[str, dict]:
    """Per-page units. A display split over a page break is joined where ``links`` (default:
    this side's own flags) says the page continues the previous one."""
    links = link_points(mds, flags) if links is None else links
    units = {}
    for stem in sorted(mds):
        md = mds[stem]
        disp, inline, prose = L.extract(md)
        items = [dict(align.unit(raw, trail), page=stem) for raw, trail in disp]
        m = MARKER_RE.search(md)
        units[stem] = {"items": items, "inline": [L.norm_str(L.tokens(x)) for x in inline],
                       "prose": L.prose_norm(prose), "md": md,
                       "printed": m.group(1) if m else None, "flags": flags.get(stem, [])}
    stems = sorted(units)
    for a, b in zip(stems, stems[1:]):
        if int(b[1:]) == int(a[1:]) + 1 and b in links:
            ia, ib = units[a]["items"], units[b]["items"]
            if ia and ib and ia[-1]["tag"] is None and units[b]["md"].split("\n", 1)[1:] \
                    and units[b]["md"].split("\n", 1)[1].lstrip("\n").startswith("$$"):
                j = {"raw": ia[-1]["raw"] + "\n%% page break %%\n" + ib[0]["raw"],
                     "toks": ia[-1]["toks"] + ib[0]["toks"], "tag": ib[0]["tag"] or ia[-1]["tag"],
                     "page": a + "+" + b}
                units[a]["items"] = ia[:-1]
                units[b]["items"] = [j] + ib[1:]
    return units


def header_leak(cand_md: str, printed: str | None, ref_md: str = "") -> list[str]:
    lines = [s.strip() for s in cand_md.strip().splitlines() if s.strip()]
    edge = lines[:2] + lines[-2:]
    hits = []
    for s in edge:
        if printed and len(s) < 90 and re.search(r"(^|\s|#)%s(\s|$)" % re.escape(printed), s):
            hits.append(s[:80])
        elif (len(s) < 60 and re.fullmatch(r"[A-Z][A-Z ,.'\-]{3,}", s) and not s.startswith("CHAPTER")
              and s.lower() not in {r.strip("# ").lower() for r in ref_md.splitlines()}):
            hits.append(s[:80])
    return hits


# ---------------------------------------------------------------- scoring

def score(ref_units: dict, cand_units: dict) -> tuple[dict, dict]:
    res = {"pages": {}, "pairs": [], "missed": [], "extra": [], "inline_errors": []}
    tot = dict(n_ref=0, n_cand=0, matched=0, exact=0, ned_sum=0.0, tagged=0, tag_ok=0,
               inl_ref=0, inl_hit=0, cer_ed=0, cer_len=0)
    empty = {"items": [], "inline": [], "prose": "", "md": "", "printed": None, "flags": []}
    for p, ru in ref_units.items():
        cu = cand_units.get(p, empty)
        R, C = ru["items"], cu["items"]
        pairs, missed, extra = align.align(R, C)
        tot["n_ref"] += len(R)
        tot["n_cand"] += len(C)
        typo = [f for f in ru["flags"] if f.get("type") == "possible_source_typo"]
        for ri, ci in pairs:
            a, b = align.cat(R, ri), align.cat(C, ci)
            d = L.distance(a, b)
            rtag = next((R[i]["tag"] for i in ri if R[i]["tag"]), None)
            ctag = next((C[i]["tag"] for i in ci if C[i]["tag"]), None)
            diffs = L.editops(a, b) if d else []
            # cosmetic: only markup, dots, punctuation or breaks differ (also when the lost
            # markup takes its braces with it, as \mathbf{V}_{\mathbf{X}} against V_X)
            cosmetic = bool(d) and (L.is_cosmetic(diffs) or L.style_free(L.norm_str(a))
                                    == L.style_free(L.norm_str(b)))
            style = cosmetic and any(x in L.STYLE_CMDS or y in L.STYLE_CMDS
                                     for _, x, y in diffs)
            res["pairs"].append({"page": R[ri[0]]["page"], "ref_tag": rtag, "cand_tag": ctag,
                                 "n_ref": len(ri), "n_cand": len(ci), "ed": d,
                                 "ned": round(L.ned(a, b), 4), "ref_len": len(a),
                                 "cosmetic": cosmetic, "style": style,
                                 "diffs": diffs[:12],
                                 "ref_raw": "\n".join(R[i]["raw"] for i in ri),
                                 "cand_raw": "\n".join(C[i]["raw"] for i in ci),
                                 "ref_norm": L.norm_str(a), "cand_norm": L.norm_str(b),
                                 "cand_norms": [L.norm_str(C[i]["toks"]) for i in ci],
                                 "page_typo_flags": typo})
            tot["matched"] += len(ri)
            tot["exact"] += len(ri) if d == 0 else 0
            tot["ned_sum"] += L.ned(a, b) * len(ri)
            if rtag:
                tot["tagged"] += 1
                tot["tag_ok"] += 1 if ctag == rtag else 0
        for i in missed:
            res["missed"].append({"page": R[i]["page"], "ref_tag": R[i]["tag"],
                                  "ref_raw": R[i]["raw"]})
            if R[i]["tag"]:
                tot["tagged"] += 1
        for j in extra:
            res["extra"].append({"page": C[j]["page"], "cand_tag": C[j]["tag"],
                                 "cand_raw": C[j]["raw"]})
        pool = list(cu["inline"])
        hit = 0
        for s in ru["inline"]:
            if s in pool:
                pool.remove(s)
                hit += 1
        res["inline_errors"] += [{"page": p, "cand": s} for s in pool]   # spans with no match
        tot["inl_ref"] += len(ru["inline"])
        tot["inl_hit"] += hit
        ed = L.distance(ru["prose"], cu["prose"])
        tot["cer_ed"] += ed
        tot["cer_len"] += len(ru["prose"])
        res["pages"][p] = {"n_ref": len(R), "n_cand": len(C), "missed": len(missed),
                           "extra": len(extra), "cer": round(ed / max(1, len(ru["prose"])), 4),
                           "inline": f"{hit}/{len(ru['inline'])}",
                           "printed_ref": ru["printed"], "printed_cand": cu["printed"],
                           "header_leak": header_leak(cu["md"], ru["printed"], ru["md"])}
    errs = [x for x in res["pairs"] if x["ed"] > 0]
    near = [x for x in errs if x["ed"] <= 3]
    labels = [(v["printed_ref"], v["printed_cand"]) for v in res["pages"].values()
              if v["printed_cand"] is not None]
    s = {"ref_formulas": tot["n_ref"], "cand_formulas": tot["n_cand"], "matched": tot["matched"],
         "missed": len(res["missed"]), "extra": len(res["extra"]),
         "exact_pct": round(100 * tot["exact"] / max(1, tot["n_ref"]), 1),
         "mean_ned_matched": round(tot["ned_sum"] / max(1, tot["matched"]), 4),
         "mean_ned_incl_missed": round((tot["ned_sum"] + len(res["missed"])) / max(1, tot["n_ref"]), 4),
         "tag_acc_pct": round(100 * tot["tag_ok"] / max(1, tot["tagged"]), 1),
         "tagged": tot["tagged"],
         "errors": len(errs), "value_changing_errors": sum(1 for x in errs if not x["cosmetic"]),
         "near_miss_1to3": len(near),
         "near_miss_dangerous": sum(1 for x in near if not x["cosmetic"]),
         "inline_exact_pct": round(100 * tot["inl_hit"] / max(1, tot["inl_ref"]), 1),
         "inline_ref": tot["inl_ref"],
         "prose_cer": round(tot["cer_ed"] / max(1, tot["cer_len"]), 4),
         "pages": len(ref_units), "pages_in_candidate": sum(1 for p in ref_units if p in cand_units),
         "pages_with_header_leak": sum(1 for v in res["pages"].values() if v["header_leak"]),
         "label_pages": len(labels),
         "label_acc_pct": round(100 * sum(1 for a, b in labels if a == b) / max(1, len(labels)), 1)}
    return s, res


# ---------------------------------------------------------------- flag recall

def _kinds(item: dict) -> set[str]:
    kinds = {item.get("kind")}
    for r in item.get("reasons", []):
        for needle, kind in KIND_OF_REASON:
            if needle in r:
                kinds.add(kind)
    return {k for k in kinds if k}


def _is_style(pair: dict) -> bool:
    if "style" in pair:
        return pair["style"]
    return pair["cosmetic"] and any(x in L.STYLE_CMDS or y in L.STYLE_CMDS
                                    for _, x, y in pair["diffs"])


def flag_recall(res: dict, items: list[dict]) -> dict:
    """Which wrong candidate formulas some review item covers, by kind."""
    by_page: dict[str, list] = {}
    for it in items:
        if it.get("latex_primary") is None:
            continue
        norm = L.norm_str(L.tokens(L.split_tag(it["latex_primary"])[0]))
        by_page.setdefault(f"p{int(it['file']):03d}", []).append((norm, _kinds(it), it["id"]))
    wrong = [x for x in res["pairs"] if x["ed"] > 0]
    classes = {"all": wrong, "value_changing": [x for x in wrong if not x["cosmetic"]],
               "style": [x for x in wrong if _is_style(x)]}
    kinds_all = sorted({k for its in by_page.values() for _, ks, _ in its for k in ks})
    out = {"items": len(items), "by_class": {}, "unflagged_value_changing": []}
    for name, pairs in classes.items():
        covered = Counter()
        n_cov = 0
        for x in pairs:
            hits = set()
            for stem in x["page"].split("+"):
                for norm, ks, _ in by_page.get(stem, []):
                    if norm in x["cand_norms"] or norm == x["cand_norm"]:
                        hits |= ks
            if hits:
                n_cov += 1
                for k in hits:
                    covered[k] += 1
            elif name == "value_changing":
                out["unflagged_value_changing"].append(
                    {"page": x["page"], "ref_tag": x["ref_tag"], "ed": x["ed"],
                     "diffs": x["diffs"], "ref_raw": x["ref_raw"], "cand_raw": x["cand_raw"]})
        out["by_class"][name] = {"wrong": len(pairs), "covered": n_cov,
                                 "recall": round(n_cov / len(pairs), 3) if pairs else None,
                                 "by_kind": {k: covered.get(k, 0) for k in kinds_all}}
    out["unflagged_value_changing_count"] = len(out["unflagged_value_changing"])
    # inline: the candidate's spans without a match in the reference (per page, as a bag)
    # against the spans the inline items list
    flagged: dict[str, list] = {}
    for it in items:
        for sp in it.get("spans") or ([{"latex_primary": it["latex_primary"]}]
                                      if it.get("kind") == "inline" else []):
            if sp.get("latex_primary") is not None:
                flagged.setdefault(f"p{int(it['file']):03d}", []).append(
                    L.norm_str(L.tokens(sp["latex_primary"])))
    n_flagged = sum(len(v) for v in flagged.values())
    pools = {k: list(v) for k, v in flagged.items()}
    hit = 0
    for e in res.get("inline_errors", []):
        pool = pools.get(e["page"], [])
        if e["cand"] in pool:
            pool.remove(e["cand"])
            hit += 1
    n_err = len(res.get("inline_errors", []))
    out["inline"] = {"errors": n_err, "covered": hit,
                     "recall": round(hit / n_err, 3) if n_err else None,
                     "flagged_spans": n_flagged, "flagged_correct": n_flagged - hit}
    # missed displays: a missing_display item on the page whose reading is close, or that
    # has none (an equation block of the layout that gave no display)
    miss_items: dict[str, list] = {}
    for it in items:
        if it.get("kind") == "missing_display" or any(
                k == "missing_display" for k in _kinds(it)):
            miss_items.setdefault(f"p{int(it['file']):03d}", []).append(it.get("latex_second"))
    covered = 0
    for x in res.get("missed", []):
        rt = L.tokens(L.split_tag(x["ref_raw"])[0])
        if any(sec is None or L.ned(L.tokens(L.split_tag(sec)[0]), rt) < 0.6
               for stem in x["page"].split("+") for sec in miss_items.get(stem, [])):
            covered += 1
    n_miss = len(res.get("missed", []))
    out["missing"] = {"missed": n_miss, "covered": covered,
                      "recall": round(covered / n_miss, 3) if n_miss else None,
                      "items": sum(len(v) for v in miss_items.values())}
    return out


# ---------------------------------------------------------------- outputs

def _q(s: str) -> str:
    return "`" + s.replace("\n", " ") + "`"


def write_report(path: Path, summary: dict, res: dict, recall: dict | None) -> None:
    L_ = ["# math-score report (private: formulas are quoted here)", "", "## Summary", ""]
    for k, v in summary.items():
        L_.append(f"- {k}: {v}")
    if recall:
        L_ += ["", "## Flag recall", "", f"- review items: {recall['items']}"]
        for name, c in recall["by_class"].items():
            L_.append(f"- {name}: {c['covered']} of {c['wrong']} wrong formulas covered "
                      f"(recall {c['recall']}); by kind: "
                      + (", ".join(f"{k} {n}" for k, n in c["by_kind"].items()) or "none"))
        i, m = recall["inline"], recall["missing"]
        L_.append(f"- inline: {i['covered']} of {i['errors']} wrong spans flagged (recall "
                  f"{i['recall']}); {i['flagged_spans']} spans flagged, {i['flagged_correct']} "
                  f"of them right")
        L_.append(f"- missed displays: {m['covered']} of {m['missed']} covered by a "
                  f"missing_display item (recall {m['recall']}); {m['items']} such items")
        L_ += ["", f"## Unflagged value-changing errors: {recall['unflagged_value_changing_count']}", ""]
        for x in recall["unflagged_value_changing"]:
            L_ += [f"- **{x['page']}** tag {x['ref_tag'] or '—'} ed={x['ed']} · edits: "
                   + "; ".join(f"{t} `{a}`→`{b}`" for t, a, b in x["diffs"]),
                   f"  - ref: {_q(x['ref_raw'])}", f"  - cand: {_q(x['cand_raw'])}"]
    near = [x for x in res["pairs"] if 0 < x["ed"] <= 3]
    L_ += ["", f"## Near-misses (1-3 token edits): {len(near)} "
           f"({sum(not x['cosmetic'] for x in near)} value-changing, "
           f"{sum(x['cosmetic'] for x in near)} cosmetic)", ""]
    for x in sorted(near, key=lambda x: (x["cosmetic"], x["page"])):
        kind = "cosmetic" if x["cosmetic"] else "VALUE-CHANGING"
        L_ += [f"- **{x['page']}** tag {x['ref_tag'] or '—'} (cand tag {x['cand_tag'] or '—'}) · {kind} "
               f"· edits: " + "; ".join(f"{t} `{a}`→`{b}`" for t, a, b in x["diffs"]),
               f"  - ref: {_q(x['ref_raw'])}", f"  - cand: {_q(x['cand_raw'])}"]
        if x["page_typo_flags"]:
            L_.append("  - page has possible_source_typo flags: "
                      + "; ".join(str(f.get("latex", ""))[:80] for f in x["page_typo_flags"]))
    big = [x for x in res["pairs"] if x["ed"] > 3]
    L_ += ["", f"## Larger mismatches (>3 edits): {len(big)}", ""]
    for x in sorted(big, key=lambda x: -x["ned"]):
        L_ += [f"- **{x['page']}** tag {x['ref_tag'] or '—'}/{x['cand_tag'] or '—'} ed={x['ed']} "
               f"ned={x['ned']} (ref parts {x['n_ref']}, cand parts {x['n_cand']})",
               f"  - ref: {_q(x['ref_raw'][:300])}", f"  - cand: {_q(x['cand_raw'][:300])}"]
    L_ += ["", f"## Missed: {len(res['missed'])}", ""]
    for x in res["missed"]:
        L_.append(f"- {x['page']} tag {x['ref_tag'] or '—'}: {_q(x['ref_raw'][:160])}")
    L_ += ["", f"## Extra: {len(res['extra'])}", ""]
    for x in res["extra"]:
        L_.append(f"- {x['page']} tag {x['cand_tag'] or '—'}: {_q(x['cand_raw'][:160])}")
    L_ += ["", "## Pages", "", "| page | printed ref | printed cand | ref | cand | missed | extra "
           "| CER | inline | header leak |", "|---|---|---|---|---|---|---|---|---|---|"]
    for p, v in res["pages"].items():
        L_.append(f"| {p} | {v['printed_ref']} | {v['printed_cand']} | {v['n_ref']} | {v['n_cand']} "
                  f"| {v['missed']} | {v['extra']} | {v['cer']} | {v['inline']} "
                  f"| {'; '.join(v['header_leak'])} |")
    write_atomic(path, "\n".join(L_) + "\n")


def run(ref, cand, out, pages: str | None = None) -> tuple[dict, dict | None]:
    ref_mds, ref_flags, _ = read_pages(ref)
    cand_mds, cand_flags, cand_folder = read_pages(cand)
    if pages:
        try:
            keep = {f"p{i + 1:03d}" for i in parse_pages(pages, 10 ** 6)}
        except ManualsError as e:
            raise ScoreError(str(e)) from None
        ref_mds = {k: v for k, v in ref_mds.items() if k in keep}
        cand_mds = {k: v for k, v in cand_mds.items() if k in keep}
        if not ref_mds:
            raise ScoreError(f"--pages {pages!r} selects none of the reference pages")
    # a display split over a page break is joined on both sides where either side links
    # the pages, so a link only one side makes does not turn two right formulas into
    # a missed one and a wrong one; how often the sides differ is counted on its own
    ref_links, cand_links = link_points(ref_mds, ref_flags), link_points(cand_mds, cand_flags)
    links = ref_links | cand_links
    ref_units = load_units(ref_mds, ref_flags, links)
    cand_units = load_units(cand_mds, cand_flags, links)
    summary, res = score(ref_units, cand_units)
    summary["split_joins"] = {"both": len(ref_links & cand_links & set(ref_mds)),
                              "ref_only": len((ref_links - cand_links) & set(ref_mds)),
                              "cand_only": len((cand_links - ref_links) & set(ref_mds))}
    recall = None
    review = cand_folder / "review.jsonl"
    if review.is_file():
        items = [json.loads(s) for s in review.read_text("utf-8").splitlines() if s.strip()]
        if pages:
            items = [it for it in items if f"p{int(it['file']):03d}" in keep]
        recall = flag_recall(res, items)
        summary["flag_recall"] = {name: c["recall"] for name, c in recall["by_class"].items()}
        summary["unflagged_value_changing"] = recall["unflagged_value_changing_count"]
        summary["inline_flag_recall"] = recall["inline"]["recall"]
        summary["missing_display_recall"] = recall["missing"]["recall"]
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    write_atomic(out / "score.json", json.dumps({"summary": summary, "flag_recall": recall, **res},
                                                ensure_ascii=False, indent=1) + "\n")
    write_report(out / "REPORT.md", summary, res, recall)
    return summary, recall


def terminal(summary: dict, recall: dict | None) -> str:
    """Numbers only."""
    lines = [f"{k}: {v}" for k, v in summary.items() if k != "flag_recall"]
    if recall:
        for name, c in recall["by_class"].items():
            lines.append(f"flag recall {name}: {c['covered']}/{c['wrong']} = {c['recall']}; "
                         + ", ".join(f"{k} {n}" for k, n in c["by_kind"].items()))
        i, m = recall["inline"], recall["missing"]
        lines.append(f"inline flag recall: {i['covered']}/{i['errors']} = {i['recall']}; "
                     f"{i['flagged_spans']} spans flagged")
        lines.append(f"missing-display recall: {m['covered']}/{m['missed']} = {m['recall']}; "
                     f"{m['items']} items")
    return "\n".join(lines)
