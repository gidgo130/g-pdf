# SPDX-License-Identifier: AGPL-3.0-or-later
"""gpdf-cli math-review-pack on a hand-made export folder: display kinds by default,
batches with a filled-in brief from docs/math-review.md, items with every reading and
absolute crop paths, a seeded inline sample per chapter, a decisions template that
math-apply-review accepts once filled in."""

import json

import pytest

from gpdf import cli
from gpdf.pipeline.mathocr import pack, review

P1 = ('<!-- page printed="1" file="1" -->\n\n# 1 First Chapter\n\nLet \\(a_1\\) and \\(b\\) be '
      "given, and \\(b\\) again.\n\n$$\nx^2 = 1\n\\tag{1.1}\n$$\n")
P2 = ('<!-- page printed="2" file="2" -->\nmore about \\(c\\) and \\(d\\).\n\n'
      "$$\n\\begin{aligned} y &= 2 \\\\ z &= 3 \\end{aligned}\n$$\n")
P3 = ('<!-- page printed="3" file="3" -->\n\n# 2 Second Chapter\n\nNow \\(e\\), \\(f\\).\n')


def item(id_, file, kind, primary, second=None, **kw):
    return {"id": id_, "file": file, "printed": str(file), "kind": kind, "reasons": [kind],
            "tag": None, "latex_primary": primary, "latex_second": second,
            "crop": f"crops/{id_}.png", **kw}


ITEMS = [
    item("p001-d01", 1, "disagree", "x^2 = 1", "x^3 = 1", tag="1.1"),
    item("p001-inline", 1, "inline", None, crops=["crops/p001-inline-1.png"],
         spans=[{"paragraph": 1, "latex_primary": "a_1", "latex_second": "a_2", "latex_marker": "a_l"},
                {"paragraph": 1, "latex_primary": "b", "latex_second": "h", "latex_marker": "b"}]),
    item("p002-d01", 2, "marker", "\\begin{aligned} y &= 2 \\\\ z &= 3 \\end{aligned}",
         latex_marker="y = 2"),
    item("p002-m01", 2, "missing_display", None, "w = 4"),
    item("p002-inline", 2, "inline", None,
         spans=[{"paragraph": 1, "latex_primary": "c", "latex_second": "e"},
                {"paragraph": 1, "latex_primary": "d", "latex_second": "a"}]),
    item("p003-inline", 3, "inline", None,
         spans=[{"paragraph": 1, "latex_primary": "e", "latex_second": "c"},
                {"paragraph": 1, "latex_primary": "f", "latex_second": "t"}]),
]


@pytest.fixture
def export(tmp_path):
    r = tmp_path / "book"
    (r / "pages").mkdir(parents=True)
    (r / "crops").mkdir()
    for n, md in ((1, P1), (2, P2), (3, P3)):
        (r / "pages" / f"p{n:03d}.md").write_text(md, "utf-8")
        (r / "pages" / f"p{n:03d}.flags.json").write_text("[]", "utf-8")
    (r / "review.jsonl").write_text("".join(json.dumps(i) + "\n" for i in ITEMS), "utf-8")
    (r / "meta.json").write_text(json.dumps({"source": {"path": str(tmp_path / "book.pdf")}}),
                                 "utf-8")
    return r


def lines(path):
    return [json.loads(s) for s in path.read_text("utf-8").splitlines() if s.strip()]


def test_display_kinds_by_default_in_batches_with_a_filled_brief(export, tmp_path):
    out = tmp_path / "rev"
    s = pack.pack(export, out, batch_size=2)
    assert s["items"] == 3 and s["by_kind"] == {"disagree": 1, "marker": 1, "missing_display": 1}
    assert [b["items"] for b in s["batches"]] == [2, 1]
    b1 = lines(out / "batch01" / "items.jsonl")
    assert [x["id"] for x in b1] == ["p001-d01", "p002-d01"]
    assert b1[0]["latex_second"] == "x^3 = 1" and b1[1]["latex_marker"] == "y = 2"
    assert b1[0]["crops"] == [str((export / "crops" / "p001-d01.png").resolve())]
    assert "be given" in b1[0]["context"]
    m = lines(out / "batch02" / "items.jsonl")[0]
    assert m["kind"] == "missing_display" and m["page_md"] == P2
    assert lines(out / "batch01" / "decisions.template.jsonl") == [
        {"id": "p001-d01", "action": "TODO"}, {"id": "p002-d01", "action": "TODO"}]
    brief = (out / "batch01" / "BRIEF.md").read_text("utf-8")
    assert brief.startswith("# Math review, batch01")
    assert str((out / "batch01").resolve()) in brief and "render.py" in brief
    assert str((out / "decisions" / "batch01.jsonl").resolve()) in brief
    assert not pack.FIELD_RE.search(brief) and "{{" not in brief and "\\tag{…}" in brief
    assert len(brief) < 7000                       # a short brief: about a page
    assert (out / "render.py").is_file() and (out / "decisions").is_dir()
    assert json.loads((out / "pack.json").read_text("utf-8"))["items"] == 3


def test_inline_sample_is_seeded_per_chapter(export, tmp_path):
    a = pack.pack(export, tmp_path / "a", inline_sample=2, seed=7)
    b = pack.pack(export, tmp_path / "b", inline_sample=2, seed=7)
    assert a["by_kind"]["inline_span"] == 4                          # 2 per chapter
    assert list(a["chapters"].values()) == [{"spans": 4, "picked": 2}, {"spans": 2, "picked": 2}]
    ka = [x["key"] for x in lines(tmp_path / "a" / "batch01" / "items.jsonl")]
    kb = [x["key"] for x in lines(tmp_path / "b" / "batch01" / "items.jsonl")]
    assert ka == kb
    picked = {x["key"] for x in lines(tmp_path / "a" / "batch01" / "items.jsonl")
              if x["kind"] == "inline_span"}
    assert {"p003-inline#1", "p003-inline#2"} <= picked


def test_a_span_item_gives_its_occurrence_or_the_candidates(export, tmp_path):
    pack.pack(export, tmp_path / "r", inline_sample=10, seed=1)
    spans = {x["key"]: x for x in lines(tmp_path / "r" / "batch01" / "items.jsonl")
             if x["kind"] == "inline_span"}
    a1 = spans["p001-inline#1"]
    assert a1["id"] == "p001-inline" and a1["occurrence"] == 1 and a1["latex_marker"] == "a_l"
    assert "Let" in a1["context"]
    b = spans["p001-inline#2"]
    assert b["occurrence"] is None and [c["occurrence"] for c in b["occurrence_candidates"]] == [1, 2]
    tmpl = {d["spans"][0]["latex_primary"]: d for d in
            lines(tmp_path / "r" / "batch01" / "decisions.template.jsonl") if "spans" in d}
    assert tmpl["a_1"] == {"id": "p001-inline", "action": "TODO",
                           "spans": [{"latex_primary": "a_1", "latex": "a_1", "occurrence": 1}]}


def test_kinds_and_pages_select_items(export, tmp_path):
    s = pack.pack(export, tmp_path / "r", kinds=("inline",), pages="2-3")
    assert s["by_kind"] == {"inline": 2}
    g = lines(tmp_path / "r" / "batch01" / "items.jsonl")[0]
    assert g["id"] == "p002-inline" and [x["occurrence"] for x in g["spans"]] == [1, 1]
    with pytest.raises(pack.PackError, match="unknown kind"):
        pack.pack(export, tmp_path / "x", kinds=("prose",))
    with pytest.raises(pack.PackError, match="not found"):
        pack.pack(tmp_path / "nowhere", tmp_path / "x")


def test_a_filled_template_applies(export, tmp_path):
    out = tmp_path / "r"
    pack.pack(export, out, inline_sample=10, seed=3)
    filled = []
    for d in lines(out / "batch01" / "decisions.template.jsonl"):
        if d["id"] == "p001-d01":
            d.update(action="replace", latex="x^2 = 2", reason="printed 2")
        elif d.get("spans") and d["spans"][0]["latex_primary"] == "a_1":
            d["action"] = "replace"
            d["spans"][0]["latex"] = "a_2"
        elif d.get("spans"):
            d = {"id": d["id"], "action": "accept"}
        else:
            d["action"] = "accept"
        filled.append(d)
    (out / "decisions" / "batch01.jsonl").write_text(
        "".join(json.dumps(d) + "\n" for d in filled), "utf-8")
    rep = review.apply(export, out / "decisions" / "batch01.jsonl")
    assert rep.refused == []
    md = (export / "pages" / "p001.md").read_text("utf-8")
    assert "x^2 = 2" in md and "\\(a_2\\)" in md


def test_cli(export, tmp_path, capsys):
    code = cli.main(["math-review-pack", "--export", str(export), "--out", str(tmp_path / "r"),
                     "--batch-size", "50", "--inline-sample", "1", "--seed", "5",
                     "--kinds", "disagree,marker"])
    assert code == 0
    out = capsys.readouterr().out
    assert "4 item(s) in 1 batch(es)" in out and "chapter 1: 1 of 4 spans" in out
    assert "First Chapter" not in out                  # numbers only on screen


def test_a_span_with_its_own_line_crop_carries_only_that_crop(export, tmp_path):
    its = [dict(i) for i in ITEMS]
    its[1] = dict(its[1], spans=[dict(s, bbox=[0.1, 0.2, 0.9, 0.4], at=0.3,
                                      line_box=[0.1, 0.2, 0.9, 0.26],
                                      crop=f"crops/p001-inline-s{n}.png")
                                 for n, s in enumerate(its[1]["spans"], 1)])
    (export / "review.jsonl").write_text("".join(json.dumps(i) + "\n" for i in its), "utf-8")
    pack.pack(export, tmp_path / "r", inline_sample=10, seed=1)
    spans = {x["key"]: x for x in lines(tmp_path / "r" / "batch01" / "items.jsonl")
             if x["kind"] == "inline_span"}
    a1 = spans["p001-inline#1"]
    assert a1["crops"] == [str((export / "crops" / "p001-inline-s1.png").resolve())]
    assert a1["line_box"] == [0.1, 0.2, 0.9, 0.26]
    assert spans["p002-inline#1"]["crops"] == [str((export / "crops" / "p002-inline.png").resolve())]
    brief = (tmp_path / "r" / "batch01" / "BRIEF.md").read_text("utf-8")
    assert "formula_uncertain" in brief and "never `accept`" in brief


def test_cli_samples_40_inline_spans_per_chapter_by_default(export, tmp_path):
    assert cli.main(["math-review-pack", "--export", str(export), "--out", str(tmp_path / "r")]) == 0
    s = json.loads((tmp_path / "r" / "pack.json").read_text("utf-8"))
    assert s["inline_sample"] == 40 and s["by_kind"]["inline_span"] == 6     # all, fewer than 40
    assert cli.main(["math-review-pack", "--export", str(export), "--out", str(tmp_path / "z"),
                     "--inline-sample", "0"]) == 0
    assert "inline_span" not in json.loads((tmp_path / "z" / "pack.json").read_text("utf-8"))["by_kind"]


def test_the_brief_carries_the_tag_nesting_and_source_typo_rules(export, tmp_path):
    pack.pack(export, tmp_path / "r")
    brief = (tmp_path / "r" / "batch01" / "BRIEF.md").read_text("utf-8")
    assert "never accept plus tag" in brief.lower()
    assert "nesting" in brief
    assert "looks mathematically wrong" in brief and "never plain `accept`" in brief


def test_an_identity_item_carries_the_check_and_its_numbers(tmp_path):
    r = tmp_path / "book"
    (r / "pages").mkdir(parents=True)
    (r / "pages" / "p001.md").write_text('<!-- page printed="1" file="1" -->\n\n$$\nx = 1\n$$\n', "utf-8")
    it = item("p001-d01", 1, "identity", "x = 1", identity=["sums to 1"], identity_kind="pmf",
              identity_details=[{"params": {"theta": 0.5}, "support": "{0,1,...}", "sum": 1.5}])
    (r / "review.jsonl").write_text(json.dumps(it) + "\n", "utf-8")
    pack.pack(r, tmp_path / "rev")
    got = lines(tmp_path / "rev" / "batch01" / "items.jsonl")[0]
    assert got["identity_check"] == {"kind": "pmf", "failed": ["sums to 1"],
                                     "numbers": [{"params": {"theta": 0.5}, "support": "{0,1,...}",
                                                  "sum": 1.5}]}


@pytest.mark.parametrize("sampled, errors, full", [
    (40, 0, False), (40, 1, False), (40, 2, True), (19, 1, True), (19, 0, False), (20, 1, False)])
def test_the_inline_follow_up_rule(sampled, errors, full):
    assert pack.review_all_inline(sampled, errors) is full


def test_followup_counts_value_errors_per_chapter_from_the_decisions(export, tmp_path, capsys):
    rev = tmp_path / "rev"
    pack.pack(export, rev, inline_sample=40)
    # chapter 1 (pages 1-2, 4 spans): one value error and one style-only change -> review all
    # (fewer than 20 spans sampled); chapter 2 (page 3): a flag only -> nothing more
    (rev / "decisions" / "batch01.jsonl").write_text("".join(json.dumps(d) + "\n" for d in [
        {"id": "p001-inline", "action": "replace",
         "spans": [{"latex_primary": "a_1", "latex": "a_2", "occurrence": 1}]},
        {"id": "p002-inline", "action": "replace",
         "spans": [{"latex_primary": "c", "latex": "\mathbf{c}", "occurrence": 1}]},
        {"id": "p003-inline", "action": "flag", "flag_type": "formula_uncertain", "note": "?"},
    ]), "utf-8")
    res = pack.followup(rev)
    ch = list(res["chapters"].values())
    assert [(c["sampled"], c["value_errors"], c["style_changes"], c["review_all"], c["pages"])
            for c in ch] == [(4, 1, 1, True, "1-2"), (2, 0, 0, False, "3")]
    assert cli.main(["math-review-followup", str(rev)]) == 0
    out = capsys.readouterr().out
    assert "chapter 1: 4 sampled, 1 value error(s): review all inline (--pages 1-2)" in out
    assert "x^" not in out and "a_2" not in out                         # numbers only
