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
    # a short brief: about a page (its own text, not the temp paths it names, whose length varies)
    assert len(brief.replace(str(out.resolve()), "OUT")) < 7000
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
    # Batch B-2: reviewers accepted short inline spans in a loop without opening their crops
    assert "Open every item's crop" in brief and "not in a loop" in brief


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


def test_the_full_assemblys_identity_failures_are_packed_unless_already_reviewed_with_them(tmp_path):
    # JKB p172-d03: a display failed its check only in the full assembly, so no group pack
    # carried it. --identity-failed packs every display whose check failed, whatever its
    # kind; --skip-reviewed leaves out those an earlier pack already gave with a failed check.
    r = tmp_path / "full"
    (r / "pages").mkdir(parents=True)
    md = ('<!-- page printed="1" file="1" -->\n\n$$\nf(x) = 2x\n$$\n\n$$\nF(x) = x^3\n$$\n\n'
          '$$\ng(x) = 3x\n$$\n\n$$\nh = 1\n$$\n')
    (r / "pages" / "p001.md").write_text(md, "utf-8")
    ident = dict(identity=["integrates to 1"], identity_kind="density",
                 identity_details=[{"integral": 2.0}])
    full = [item("p001-d01", 1, "identity", "f(x) = 2x", **ident),
            {**item("p001-d02", 1, "disagree", "F(x) = x^3", "F(x) = x^2", **ident),
             "reasons": ["tiers disagree", "identity failed: integrates to 1"]},
            item("p001-d03", 1, "identity", "g(x) = 3x", **ident),
            item("p001-d04", 1, "disagree", "h = 1", "h = 7")]
    (r / "review.jsonl").write_text("".join(json.dumps(i) + "\n" for i in full), "utf-8")
    # the group pack had d03 with its failed check, d02 without one (it failed only now)
    g = tmp_path / "groups" / "g1" / "batch01"
    g.mkdir(parents=True)
    (g / "items.jsonl").write_text(
        json.dumps({"key": "p001-d03", "latex_primary": "g(x) = 3x",
                    "identity_check": {"failed": ["integrates to 1"]}}) + "\n"
        + json.dumps({"key": "p001-d02", "latex_primary": "F(x) = x^3"}) + "\n", "utf-8")
    s = pack.pack(r, tmp_path / "rev", identity_failed=True, skip_reviewed=[tmp_path / "groups"])
    got = lines(tmp_path / "rev" / "batch01" / "items.jsonl")
    assert [x["id"] for x in got] == ["p001-d01", "p001-d02"]
    assert all(x["identity_check"]["failed"] == ["integrates to 1"] for x in got)
    assert s["skipped_reviewed"] == 1
    assert cli.main(["math-review-pack", "--export", str(r), "--out", str(tmp_path / "rev2"),
                     "--identity-failed", "--skip-reviewed", str(tmp_path / "groups"),
                     "--inline-sample", "0"]) == 0
    assert [x["id"] for x in lines(tmp_path / "rev2" / "batch01" / "items.jsonl")] == [
        "p001-d01", "p001-d02"]


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


def test_a_pooled_sample_draws_over_all_pages_and_the_follow_up_judges_the_pool(export, tmp_path):
    # --inline-pool: one pool for the packed pages (a book whose chapters are a few pages each)
    rev = tmp_path / "rev"
    s = pack.pack(export, rev, inline_sample=3, seed=2, inline_pool=True)
    assert s["inline_pool"] is True and list(s["chapters"].values()) == [{"spans": 6, "picked": 3}]
    (rev / "decisions" / "batch01.jsonl").write_text(json.dumps(
        {"id": "p001-inline", "action": "replace",
         "spans": [{"latex_primary": "a_1", "latex": "a_2", "occurrence": 1}]}) + "\n", "utf-8")
    ch = list(pack.followup(rev)["chapters"].values())
    assert [(c["sampled"], c["value_errors"], c["review_all"], c["pages"]) for c in ch] == [
        (3, 1, True, "1-3")]
    assert cli.main(["math-review-pack", "--export", str(export), "--out", str(tmp_path / "c"),
                     "--inline-sample", "4", "--inline-pool"]) == 0
    assert json.loads((tmp_path / "c" / "pack.json").read_text("utf-8"))["by_kind"]["inline_span"] == 4


# gpdf-cli math-decide: the reviewer writes each decision through a helper that reads the LaTeX
# raw (a file or stdin) and serialises it, so a `\\` row break can't collapse to `\ `.

def _packed(export, tmp_path, **kw):
    out = tmp_path / "rev"
    pack.pack(export, out, inline_sample=10, seed=3, **kw)
    return out, out / "batch01"


def test_decide_writes_a_row_break_replace_raw_and_it_applies(export, tmp_path):
    from gpdf.pipeline.mathocr import decide
    out, b = _packed(export, tmp_path)
    new = r"\begin{aligned} y &= 2 \\ z &= 4 \end{aligned}"
    d = decide.decide(b, "p002-d01", "replace", latex=new + "\n", reason="printed 4")
    assert d == {"key": "p002-d01", "id": "p002-d01", "action": "replace", "latex": new,
                 "reason": "printed 4"}
    assert lines(out / "decisions" / "batch01.jsonl") == [d]
    assert review.apply(export, out / "decisions" / "batch01.jsonl").refused == []
    assert "z &= 4" in (export / "pages" / "p002.md").read_text("utf-8")


def test_decide_refuses_a_collapsed_row_break_and_writes_nothing(export, tmp_path):
    from gpdf.pipeline.mathocr import decide
    out, b = _packed(export, tmp_path)
    with pytest.raises(decide.DecideError, match="row break"):
        decide.decide(b, "p002-d01", "replace", reason="x",
                      latex=r"\begin{aligned} y &= 2 \ z &= 4 \end{aligned}")
    with pytest.raises(decide.DecideError, match="same as latex_primary"):
        decide.decide(b, "p001-d01", "replace", latex=" x^2 = 1 ", reason="x")
    with pytest.raises(decide.DecideError, match="unknown key"):
        decide.decide(b, "p009-d01", "accept")
    with pytest.raises(decide.DecideError, match="note"):
        decide.decide(b, "p001-d01", "flag", flag_type="possible_source_typo")
    assert not (out / "decisions" / "batch01.jsonl").exists()
    d = decide.decide(b, "p002-d01", "replace", reason="rows on one line", structure_change=True,
                      latex="y = 2, z = 4")
    assert d["structure_change"] is True


def test_decide_replaces_single_spans_of_a_page_inline_item_and_they_apply(export, tmp_path):
    # method (a) reviews every inline item of a page; the reviewer must be able to correct one
    # span of a grouped item (Batch B-3, DM: reviewers parked span fixes in flag notes)
    from gpdf.pipeline.mathocr import decide
    out = tmp_path / "rev"
    pack.pack(export, out, kinds=("inline",), inline_sample=0)
    b = out / "batch01"
    with pytest.raises(decide.DecideError, match="--span"):
        decide.decide(b, "p001-inline", "replace", latex="a_2", reason="x")
    with pytest.raises(decide.DecideError, match="not one of the item"):
        decide.decide(b, "p001-inline", "replace", span="z", latex="a_2", reason="x")
    with pytest.raises(decide.DecideError, match="occurrence"):
        decide.decide(b, "p001-inline", "replace", span="b", latex="B", reason="x")
    decide.decide(b, "p001-inline", "replace", span="a_1", latex="a_2", reason="printed a_2")
    d = decide.decide(b, "p001-inline", "replace", span="b", occurrence=2, latex="B", reason="bold")
    assert d["action"] == "replace"
    assert {(s["latex_primary"], s["latex"], s["occurrence"]) for s in d["spans"]} == {
        ("a_1", "a_2", 1), ("b", "B", 2)}
    assert len(lines(out / "decisions" / "batch01.jsonl")) == 1      # merged into one decision
    res = review.apply(export, out / "decisions" / "batch01.jsonl")
    assert res.refused == []
    p1 = (export / "pages" / "p001.md").read_text("utf-8")
    assert "\\(a_2\\)" in p1 and "and \\(B\\) again" in p1 and "\\(b\\) be given" in p1


def test_cli_decide_takes_the_span_from_a_file(export, tmp_path):
    out = tmp_path / "rev"
    pack.pack(export, out, kinds=("inline",), inline_sample=0)
    b = out / "batch01"
    (b / "work" / "span.tex").write_text("a_1\n", "utf-8")
    (b / "work" / "new.tex").write_text("a_2\n", "utf-8")
    assert cli.main(["math-decide", "--batch", str(b), "p001-inline", "replace", "--span-file",
                     "work/span.tex", "--latex-file", str(b / "work" / "new.tex"),
                     "--reason", "printed a_2"]) == 0
    d = lines(out / "decisions" / "batch01.jsonl")[0]
    assert {"latex_primary": "a_1", "latex": "a_2", "occurrence": 1} in d["spans"]


def test_decide_accept_of_a_page_inline_item_names_its_spans(export, tmp_path):
    from gpdf.pipeline.mathocr import decide
    out = tmp_path / "rev"
    pack.pack(export, out, kinds=("inline",), inline_sample=0)
    d = decide.decide(out / "batch01", "p002-inline", "accept")
    assert [(s["latex_primary"], s["latex"]) for s in d["spans"]] == [("c", "c"), ("d", "d")]


def test_decide_keeps_every_run_of_an_inline_missing_item(tmp_path):
    from gpdf.pipeline.mathocr import decide
    r = tmp_path / "book"
    (r / "pages").mkdir(parents=True)
    md = '<!-- page printed="1" file="1" -->\n\nLet x1 be large and n - 1 small.\n'
    (r / "pages" / "p001.md").write_text(md, "utf-8")
    (r / "pages" / "p001.flags.json").write_text("[]", "utf-8")
    it = {"id": "p001-inline-missing", "file": 1, "printed": "1", "kind": "inline_missing",
          "reasons": ["inline_missing"], "runs": [
              {"paragraph": 1, "text": "x1", "why": "digit index", "occurrence": 1},
              {"paragraph": 1, "text": "n - 1", "why": "operator", "occurrence": 1}]}
    (r / "review.jsonl").write_text(json.dumps(it) + "\n", "utf-8")
    pack.pack(r, tmp_path / "rev", kinds=("inline_missing",), inline_sample=0)
    b = tmp_path / "rev" / "batch01"
    decide.decide(b, "p001-inline-missing", "replace", latex="x_1", run_text="x1", reason="math")
    d = decide.decide(b, "p001-inline-missing", "replace", latex="n - 1", run_text="n - 1",
                      reason="math")
    assert [r_["text"] for r_ in d["runs"]] == ["x1", "n - 1"]
    assert review.apply(r, tmp_path / "rev" / "decisions" / "batch01.jsonl").refused == []
    assert "Let \\(x_1\\) be large and \\(n - 1\\) small." in (r / "pages" / "p001.md").read_text("utf-8")


def test_decide_withdraw_removes_a_decision_cleanly(export, tmp_path, capsys):
    # B-2: a reviewer removed accept lines by script to leave flag-only items
    from gpdf.pipeline.mathocr import decide
    out, b = _packed(export, tmp_path)
    decide.decide(b, "p001-d01", "accept")
    decide.decide(b, "p001-d01", "flag", flag_type="possible_source_typo", note="sign")
    decide.decide(b, "p002-d01", "accept")
    assert [x["action"] for x in decide.withdraw(b, "p001-d01", "accept")] == ["accept"]
    assert [(x["key"], x["action"]) for x in lines(out / "decisions" / "batch01.jsonl")] == [
        ("p001-d01", "flag"), ("p002-d01", "accept")]
    with pytest.raises(decide.DecideError, match="no replace"):
        decide.withdraw(b, "p002-d01", "replace")         # it is an accept: kept
    with pytest.raises(decide.DecideError, match="unknown key"):
        decide.withdraw(b, "p009-d01")
    assert cli.main(["math-decide", "--batch", str(b), "p001-d01", "--withdraw"]) == 0
    assert "withdrawn" in capsys.readouterr().out
    assert [x["key"] for x in lines(out / "decisions" / "batch01.jsonl")] == ["p002-d01"]
    assert decide.status(b)["missing"][:1] == ["p001-d01"]
    assert cli.main(["math-decide", "--batch", str(b), "p001-d01", "--withdraw"]) == 2
    assert cli.main(["math-decide", "--batch", str(b), "p002-d01", "accept", "--withdraw"]) == 1


def test_decide_fills_an_inline_span_from_its_item_and_keeps_one_decision_per_key(export, tmp_path):
    from gpdf.pipeline.mathocr import decide
    out, b = _packed(export, tmp_path)
    decide.decide(b, "p001-inline#1", "accept")
    d = decide.decide(b, "p001-inline#1", "replace", latex="a_2", reason="printed a_2")
    assert d["spans"] == [{"latex_primary": "a_1", "latex": "a_2", "occurrence": 1}]
    with pytest.raises(decide.DecideError, match="occurrence"):     # b is printed twice
        decide.decide(b, "p001-inline#2", "replace", latex="h", reason="x")
    d2 = decide.decide(b, "p001-inline#2", "replace", latex="h", reason="x", occurrence=2)
    assert d2["spans"][0]["occurrence"] == 2
    decide.decide(b, "p001-d01", "flag", flag_type="possible_source_typo", subtype="math_error",
                  note="exponent looks wrong")
    decide.decide(b, "p001-d01", "accept", tag="1.2", reason="tag misread")
    got = lines(out / "decisions" / "batch01.jsonl")
    assert [(x["key"], x["action"]) for x in got] == [
        ("p001-d01", "accept"), ("p001-d01", "flag"), ("p001-inline#1", "replace"),
        ("p001-inline#2", "replace")]
    assert got[1]["reason"] == "exponent looks wrong" and got[1]["subtype"] == "math_error"
    s = decide.status(b)
    assert s["decided"] == 3 and "p002-d01" in s["missing"] and s["total"] == len(s["missing"]) + 3
    rep = review.apply(export, out / "decisions" / "batch01.jsonl")
    assert rep.refused == []
    assert r"\(a_2\)" in (export / "pages" / "p001.md").read_text("utf-8")


def test_decide_with_a_relative_batch_path_writes_the_batch_decisions_file(export, tmp_path,
                                                                          monkeypatch):
    # a reviewer ran `math-decide --batch .` from the batch folder: the decisions went to
    # decisions/.jsonl (the name of "." is empty)
    from gpdf.pipeline.mathocr import decide
    out, b = _packed(export, tmp_path)
    monkeypatch.chdir(b)
    decide.decide(".", "p001-d01", "accept")
    assert lines(out / "decisions" / "batch01.jsonl")[0]["key"] == "p001-d01"
    assert decide.status(".")["decided"] == 1


def test_decide_places_a_missing_display_by_an_exact_piece_of_the_page(export, tmp_path):
    from gpdf.pipeline.mathocr import decide
    out, b = _packed(export, tmp_path)
    with pytest.raises(decide.DecideError, match="after"):
        decide.decide(b, "p002-m01", "replace", latex="w = 4", reason="missing")
    with pytest.raises(decide.DecideError, match="not in page_md"):
        decide.decide(b, "p002-m01", "replace", latex="w = 4", reason="m", after="no such text")
    d = decide.decide(b, "p002-m01", "replace", latex="w = 4", reason="m", after=r"and \(d\).")
    assert d["after"] == r"and \(d\)." and d["latex"] == "w = 4"


def test_cli_decide_reads_the_latex_from_stdin_and_the_brief_names_the_helper(export, tmp_path,
                                                                             monkeypatch, capsys):
    import io
    out, b = _packed(export, tmp_path)
    monkeypatch.setattr("sys.stdin", io.StringIO(r"\begin{aligned} y &= 3 \\ z &= 3 \end{aligned}"
                                                 + "\n"))
    assert cli.main(["math-decide", "--batch", str(b), "p002-d01", "replace",
                     "--reason", "printed 3"]) == 0
    assert lines(out / "decisions" / "batch01.jsonl")[0]["latex"].count("\\\\") == 1
    tex = b / "work" / "x.tex"
    tex.write_text(r"\begin{aligned} y &= 2 \ z &= 3 \end{aligned}", "utf-8")
    assert cli.main(["math-decide", "--batch", str(b), "p002-d01", "replace", "--reason", "r",
                     "--latex-file", "work/x.tex"]) == 2         # relative to the batch
    assert "row break" in capsys.readouterr().err
    assert cli.main(["math-decide", "--batch", str(b), "status"]) == 0
    assert "1 of" in capsys.readouterr().out
    brief = (b / "BRIEF.md").read_text("utf-8")
    assert "math-decide" in brief and str(b.resolve()) in brief and not pack.FIELD_RE.search(brief)


def test_decide_flags_named_spans_of_a_page_inline_item_and_they_apply_tied(export, tmp_path):
    from gpdf.pipeline.mathocr import decide
    out = tmp_path / "rev"
    pack.pack(export, out, kinds=("inline",), inline_sample=0)
    b = out / "batch01"
    with pytest.raises(decide.DecideError, match="not one of the item"):
        decide.decide(b, "p001-inline", "flag", flag_type="formula_uncertain", note="n", span=["z"])
    with pytest.raises(decide.DecideError, match="occurrence"):           # b is printed twice
        decide.decide(b, "p001-inline", "flag", flag_type="formula_uncertain", note="n",
                      span=["a_1", "b"])
    with pytest.raises(decide.DecideError, match="--occurrence"):
        decide.decide(b, "p001-inline", "flag", flag_type="formula_uncertain", note="n",
                      span=["a_1", "b"], occurrence=[1, 2, 3])
    d = decide.decide(b, "p001-inline", "flag", flag_type="possible_source_typo", subtype="notation",
                      note="two slips", span=["a_1", "b"], occurrence=[1, 2])
    assert d["spans"] == [{"latex_primary": "a_1", "occurrence": 1},
                          {"latex_primary": "b", "occurrence": 2}]
    with pytest.raises(decide.DecideError, match="one --span"):
        decide.decide(b, "p001-inline", "replace", span=["a_1", "b"], latex="x", reason="r")
    with pytest.raises(decide.DecideError, match="not one of the item"):    # an accept names spans too
        decide.decide(out / "batch01", "p001-inline", "accept", span=["z"])
    out2 = tmp_path / "rev2"
    pack.pack(export, out2, kinds=("disagree", "style", "syntax"), inline_sample=0)
    with pytest.raises(decide.DecideError, match="--span goes with"):
        decide.decide(out2 / "batch01", "p001-d01", "accept", span=["x"])
    rep = review.apply(export, out / "decisions" / "batch01.jsonl")
    assert rep.refused == [] and rep.untied == []
    fl = [f for f in json.loads((export / "pages" / "p001.flags.json").read_text("utf-8"))
          if f["type"] == "possible_source_typo"]
    assert [(f["latex"], f.get("occurrence")) for f in fl] == [("a_1", None), ("b", 2)]


def test_cli_decide_round_trips_repeated_spans_and_occurrences(export, tmp_path):
    out = tmp_path / "rev"
    pack.pack(export, out, kinds=("inline",), inline_sample=0)
    b = out / "batch01"
    (b / "work" / "span.tex").write_text("b\n", "utf-8")
    assert cli.main(["math-decide", "--batch", str(b), "p001-inline", "flag", "--flag-type",
                     "formula_uncertain", "--note", "faint", "--span", "a_1", "--span-file",
                     "work/span.tex", "--occurrence", "1", "--occurrence", "2"]) == 0
    d = lines(out / "decisions" / "batch01.jsonl")[0]
    assert d["spans"] == [{"latex_primary": "a_1", "occurrence": 1},
                          {"latex_primary": "b", "occurrence": 2}]
    # a single --span and --occurrence still drive a replace
    (b / "work" / "new.tex").write_text("B\n", "utf-8")
    assert cli.main(["math-decide", "--batch", str(b), "p001-inline", "replace", "--span", "b",
                     "--occurrence", "2", "--latex-file", str(b / "work" / "new.tex"),
                     "--reason", "bold"]) == 0
    got = {x["action"]: x for x in lines(out / "decisions" / "batch01.jsonl")}
    assert {"latex_primary": "b", "latex": "B", "occurrence": 2} in got["replace"]["spans"]
    rep = review.apply(export, out / "decisions" / "batch01.jsonl")
    assert rep.refused == []
    fl = [f for f in json.loads((export / "pages" / "p001.flags.json").read_text("utf-8"))
          if f["type"] == "formula_uncertain"]
    assert [(f["latex"], f.get("occurrence")) for f in fl] == [("a_1", None), ("B", None)]


def test_decide_ties_an_inline_span_flag_to_its_span(export, tmp_path):
    from gpdf.pipeline.mathocr import decide
    out, b = _packed(export, tmp_path)
    d = decide.decide(b, "p001-inline#1", "flag", flag_type="formula_uncertain", note="faint")
    assert d["spans"] == [{"latex_primary": "a_1", "occurrence": 1}]
    with pytest.raises(decide.DecideError, match="occurrence"):           # b is printed twice
        decide.decide(b, "p001-inline#2", "flag", flag_type="formula_uncertain", note="faint")
    d = decide.decide(b, "p001-inline#2", "flag", flag_type="formula_uncertain", note="faint",
                      occurrence=2)
    assert d["spans"] == [{"latex_primary": "b", "occurrence": 2}]
    rep = review.apply(export, out / "decisions" / "batch01.jsonl")
    assert rep.refused == [] and rep.untied == []


def test_the_brief_tells_reviewers_to_name_the_flagged_spans(export, tmp_path):
    out = tmp_path / "rev"
    pack.pack(export, out, kinds=("inline",), inline_sample=0)
    brief = (out / "batch01" / "BRIEF.md").read_text("utf-8")
    assert "flag --span-file" in brief and "name the span" in brief


# decisions per span (a page inline item): deciding one span keeps the decisions of the others

def _spans_of(d):
    return {(s["latex_primary"], s.get("occurrence")): s["latex"] for s in d["spans"]}


def test_decide_accept_of_one_span_names_only_that_span(export, tmp_path):
    from gpdf.pipeline.mathocr import decide
    out = tmp_path / "rev"
    pack.pack(export, out, kinds=("inline",), inline_sample=0)
    b = out / "batch01"
    with pytest.raises(decide.DecideError, match="occurrence"):
        decide.decide(b, "p001-inline", "accept", span="b")        # b occurs twice: which one?
    d = decide.decide(b, "p001-inline", "accept", span="b", occurrence=1)
    assert d["action"] == "accept" and _spans_of(d) == {("b", 1): "b"}


def test_a_span_accepted_and_a_sibling_replaced_are_both_kept(export, tmp_path):
    from gpdf.pipeline.mathocr import decide
    out = tmp_path / "rev"
    pack.pack(export, out, kinds=("inline",), inline_sample=0)
    b = out / "batch01"
    decide.decide(b, "p001-inline", "accept", span="b", occurrence=1)
    d = decide.decide(b, "p001-inline", "replace", span="b", occurrence=2, latex="B", reason="bold")
    assert d["action"] == "replace"
    assert _spans_of(d)[("b", 1)] == "b" and _spans_of(d)[("b", 2)] == "B"
    assert len(lines(out / "decisions" / "batch01.jsonl")) == 1
    # the other way round: a replaced span stays when a sibling is accepted afterwards
    d = decide.decide(b, "p001-inline", "accept", span="a_1")
    assert _spans_of(d)[("b", 2)] == "B" and ("a_1", 1) in _spans_of(d) and d["action"] == "replace"


def test_withdrawing_one_span_leaves_the_other(export, tmp_path):
    from gpdf.pipeline.mathocr import decide
    out = tmp_path / "rev"
    pack.pack(export, out, kinds=("inline",), inline_sample=0)
    b = out / "batch01"
    decide.decide(b, "p001-inline", "accept", span="b", occurrence=1)
    decide.decide(b, "p001-inline", "replace", span="b", occurrence=2, latex="B", reason="bold")
    gone = decide.withdraw(b, "p001-inline", "all", span="b", occurrence=2)
    assert [(s["latex_primary"], s["occurrence"]) for g in gone for s in g["spans"]] == [("b", 2)]
    (d,) = lines(out / "decisions" / "batch01.jsonl")
    assert ("b", 2) not in _spans_of(d) and _spans_of(d)[("b", 1)] == "b"
    with pytest.raises(decide.DecideError, match="no span"):
        decide.withdraw(b, "p001-inline", "all", span="b", occurrence=2)
    for s, o in (("b", 1), ("a_1", 1)):
        try:
            decide.withdraw(b, "p001-inline", "all", span=s, occurrence=o)
        except decide.DecideError:
            pass
    assert lines(out / "decisions" / "batch01.jsonl") == []            # no span left: no record


def test_an_old_single_decision_file_gives_the_same_records_as_before(export, tmp_path):
    from gpdf.pipeline.mathocr import decide
    out = tmp_path / "rev"
    pack.pack(export, out, kinds=("inline",), inline_sample=0)
    b = out / "batch01"
    old = {"key": "p001-inline", "id": "p001-inline", "action": "replace", "reason": "printed a_2",
           "spans": [{"latex_primary": "a_1", "latex": "a_2", "occurrence": 1}]}
    path = out / "decisions" / "batch01.jsonl"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(old) + "\n", "utf-8")
    d = decide.decide(b, "p001-inline", "replace", span="b", occurrence=2, latex="B", reason="bold")
    assert {(s["latex_primary"], s["latex"], s["occurrence"]) for s in d["spans"]} == {
        ("a_1", "a_2", 1), ("b", "B", 2)}
    # a decision without a span still applies to every span of the item
    d = decide.decide(b, "p002-inline", "accept")
    assert [(s["latex_primary"], s["latex"]) for s in d["spans"]] == [("c", "c"), ("d", "d")]


def test_cli_withdraw_takes_a_span(export, tmp_path, capsys):
    out = tmp_path / "rev"
    pack.pack(export, out, kinds=("inline",), inline_sample=0)
    b = out / "batch01"
    assert cli.main(["math-decide", "--batch", str(b), "p001-inline", "accept", "--span", "b",
                     "--occurrence", "1"]) == 0
    assert cli.main(["math-decide", "--batch", str(b), "p001-inline", "accept", "--span", "a_1"]) == 0
    assert cli.main(["math-decide", "--batch", str(b), "p001-inline", "--withdraw", "--span", "b",
                     "--occurrence", "1"]) == 0
    (d,) = lines(out / "decisions" / "batch01.jsonl")
    assert [s["latex_primary"] for s in d["spans"]] == ["a_1"]
