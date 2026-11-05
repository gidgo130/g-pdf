# SPDX-License-Identifier: AGPL-3.0-or-later
"""gpdf-cli math-apply-review on a hand-made export folder: accept, replace, flag, a
stale decision, an unknown id, idempotence, untouched pages, grouped inline spans,
text.md rebuilt, dry run, bad decisions."""

import json

import pytest

from gpdf import cli
from gpdf.pipeline.mathocr import review

P1 = ('<!-- page printed="54" file="1" -->\n\nSome prose with \\(a_1\\) inline.\n\n'
      "$$\nx^2 = 1\n\\tag{3.1}\n$$\n\n$$\n\\left( y \\right) = 2\n$$\n")
P2 = ('<!-- page printed="55" file="2" -->\n\nMore \\(u\\) and \\(v\\) here.\n\n$$\nz = 3\n$$\n')
P3 = '<!-- page printed="56" file="3" -->\n\nUntouched.\n'
ITEMS = [
    {"id": "p001-d01", "file": 1, "printed": "54", "kind": "disagree", "reasons": ["tiers disagree"],
     "tag": "3.1", "latex_primary": "x^2 = 1", "latex_second": "x^3 = 1", "crop": "crops/p001-d01.png"},
    {"id": "p001-d02", "file": 1, "printed": "54", "kind": "syntax", "reasons": ["x"],
     "tag": None, "latex_primary": "\\left( y \\right) = 2", "latex_second": None, "crop": None},
    {"id": "p001-i01", "file": 1, "printed": "54", "kind": "inline", "reasons": ["x"],
     "tag": None, "latex_primary": "a_1", "latex_second": "a_2", "crop": None},
    {"id": "p002-inline", "file": 2, "printed": "55", "kind": "inline", "reasons": ["x"],
     "tag": None, "latex_primary": None, "latex_second": None, "crop": None,
     "spans": [{"paragraph": 1, "latex_primary": "u", "latex_second": "u'"},
               {"paragraph": 1, "latex_primary": "v", "latex_second": "w"}]},
]


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "book"
    (r / "pages").mkdir(parents=True)
    for n, md in ((1, P1), (2, P2), (3, P3)):
        (r / "pages" / f"p{n:03d}.md").write_text(md, "utf-8")
        (r / "pages" / f"p{n:03d}.flags.json").write_text(json.dumps(
            [{"type": "summary", "display_formulas": 2}]), "utf-8")
    (r / "review.jsonl").write_text("".join(json.dumps(i) + "\n" for i in ITEMS), "utf-8")
    (r / "text.md").write_text("\n".join([P1, P2, P3]), "utf-8")
    return r


def decide(tmp_path, *decisions):
    path = tmp_path / "review_decisions.jsonl"
    path.write_text("".join(json.dumps(d) + "\n" for d in decisions), "utf-8")
    return path


def flags_of(root, n):
    return json.loads((root / "pages" / f"p{n:03d}.flags.json").read_text("utf-8"))


def test_accept_replace_and_flag(root, tmp_path):
    dec = decide(tmp_path,
                 {"id": "p001-d01", "action": "replace", "latex": "x^2 = 1 + 0"},
                 {"id": "p001-d02", "action": "accept"},
                 {"id": "p001-i01", "action": "flag", "flag_type": "possible_source_typo",
                  "note": "the page prints a_1 too"})
    rep = review.apply(root, dec)
    assert [a["action"] for a in rep.applied] == ["replace", "accept", "flag"]
    assert rep.refused == [] and rep.pages == [1]
    md = (root / "pages" / "p001.md").read_text("utf-8")
    assert "$$\nx^2 = 1 + 0\n\\tag{3.1}\n$$" in md                 # the tag stays
    assert "\\left( y \\right) = 2" in md and "\\(a_1\\)" in md
    fl = flags_of(root, 1)
    assert fl[-1]["type"] == "summary"
    assert {"type": "possible_source_typo", "eq": "p001-i01", "latex": "a_1",
            "note": "the page prints a_1 too"} in fl
    reviewed = [f for f in fl if f["type"] == "reviewed"]
    assert reviewed == [{"type": "reviewed", "id": "p001-d01", "action": "replace", "latex": "x^2 = 1 + 0"},
                        {"type": "reviewed", "id": "p001-d02", "action": "accept"},
                        {"type": "reviewed", "id": "p001-i01", "action": "flag",
                         "flag_type": "possible_source_typo", "note": "the page prints a_1 too"}]
    text = (root / "text.md").read_text("utf-8")
    assert "x^2 = 1 + 0" in text and text.count("<!-- page printed=") == 3
    assert (root / "pages" / "p003.md").read_text("utf-8") == P3        # untouched
    assert flags_of(root, 3) == [{"type": "summary", "display_formulas": 2}]


def test_the_same_decisions_twice_change_nothing_more(root, tmp_path):
    dec = decide(tmp_path, {"id": "p001-d01", "action": "replace", "latex": "x^2 = 1 + 0"},
                 {"id": "p001-i01", "action": "replace", "latex": "a_i"})
    review.apply(root, dec)
    before = {p.name: p.read_bytes() for p in (root / "pages").iterdir()}
    before["text.md"] = (root / "text.md").read_bytes()
    rep = review.apply(root, dec)
    assert rep.applied == [] and rep.refused == [] and sorted(rep.already) == ["p001-d01", "p001-i01"]
    after = {p.name: p.read_bytes() for p in (root / "pages").iterdir()}
    after["text.md"] = (root / "text.md").read_bytes()
    assert after == before
    assert "\\(a_i\\)" in (root / "pages" / "p001.md").read_text("utf-8")


def test_stale_and_unknown_decisions_are_refused_and_listed(root, tmp_path):
    (root / "pages" / "p001.md").write_text(P1.replace("x^2 = 1", "x^2 = 7"), "utf-8")
    dec = decide(tmp_path, {"id": "p001-d01", "action": "accept"},
                 {"id": "p009-d01", "action": "accept"},
                 {"id": "p001-d02", "action": "accept"},
                 {"id": "p001-i01", "action": "replace"},
                 {"id": "p001-i01", "action": "flag", "flag_type": "wrong"},
                 {"id": "p002-inline", "action": "replace", "latex": "u"})
    rep = review.apply(root, dec)
    assert rep.refused == [
        {"id": "p009-d01", "reason": "unknown id"},
        {"id": "p001-i01", "reason": "replace needs latex"},
        {"id": "p001-i01", "reason": "flag_type must be one of formula_uncertain, "
                                     "possible_source_typo, copy_corrupt, illegible"},
        {"id": "p002-inline", "reason": "a grouped inline item needs spans [{latex_primary, latex}]"},
        {"id": "p001-d01", "reason": "latex_primary is no longer in the page"}]
    assert [a["id"] for a in rep.applied] == ["p001-d02"]


def test_grouped_inline_spans_are_replaced_one_by_one(root, tmp_path):
    dec = decide(tmp_path, {"id": "p002-inline", "action": "replace",
                            "spans": [{"latex_primary": "v", "latex": "w"}]})
    rep = review.apply(root, dec)
    assert rep.refused == [] and rep.pages == [2]
    assert "More \\(u\\) and \\(w\\) here." in (root / "pages" / "p002.md").read_text("utf-8")
    dec = decide(tmp_path, {"id": "p002-inline", "action": "replace",
                            "spans": [{"latex_primary": "q", "latex": "w"}]})
    assert review.apply(root, dec).refused == [{"id": "p002-inline",
                                                "reason": "span 'q' is not in the item"}]


def test_a_repeated_inline_span_is_replaced_at_its_occurrence(tmp_path):
    md = ('<!-- page printed="57" file="5" -->\n\nFirst \\(x_1\\) and \\(y\\) here.\n\n'
          "Then \\(x_1\\) again, and \\(x_1\\) once more with \\(y\\).\n")
    item = {"id": "p005-inline", "file": 5, "printed": "57", "kind": "inline", "reasons": ["x"],
            "tag": None, "latex_primary": None, "latex_second": None, "crop": None,
            "spans": [{"paragraph": 2, "latex_primary": "x_1", "latex_second": "x_2"},
                      {"paragraph": 2, "latex_primary": "y", "latex_second": "z"}]}
    r = tmp_path / "book"
    (r / "pages").mkdir(parents=True)
    (r / "pages" / "p005.md").write_text(md, "utf-8")
    (r / "pages" / "p005.flags.json").write_text("[]", "utf-8")
    (r / "review.jsonl").write_text(json.dumps(item) + "\n", "utf-8")
    # without its occurrence a repeated span is refused, never applied to the first one
    rep = review.apply(r, decide(tmp_path, {"id": "p005-inline", "action": "replace",
                                            "spans": [{"latex_primary": "x_1", "latex": "x_2"}]}))
    assert rep.refused == [{"id": "p005-inline", "reason": "span 'x_1' occurs 3 times in the "
                            "page: give its occurrence"}]
    rep = review.apply(r, decide(tmp_path, {"id": "p005-inline", "action": "replace", "spans": [
        {"latex_primary": "x_1", "latex": "x_2", "occurrence": 3},
        {"latex_primary": "y", "latex": "z", "occurrence": 2},
        {"latex_primary": "x_1", "latex": "x_{11}", "occurrence": 2}]}))
    assert rep.refused == []
    assert (r / "pages" / "p005.md").read_text("utf-8").endswith(
        "First \\(x_1\\) and \\(y\\) here.\n\n"
        "Then \\(x_{11}\\) again, and \\(x_2\\) once more with \\(z\\).\n")
    (r / "pages" / "p005.flags.json").write_text("[]", "utf-8")
    rep = review.apply(r, decide(tmp_path, {"id": "p005-inline", "action": "replace", "spans": [
        {"latex_primary": "y", "latex": "w", "occurrence": 2}]}))
    assert rep.refused == [{"id": "p005-inline", "reason": "span 'y' has no occurrence 2 in "
                            "the page"}]


def test_a_grouped_item_can_be_replaced_and_flagged(root, tmp_path):
    dec = decide(tmp_path,
                 {"id": "p002-inline", "action": "replace",
                  "spans": [{"latex_primary": "v", "latex": "w"}]},
                 {"id": "p002-inline", "action": "flag", "flag_type": "possible_source_typo",
                  "note": "span u: the page prints u where v is meant"})
    rep = review.apply(root, dec)
    assert rep.refused == [] and [a["action"] for a in rep.applied] == ["replace", "flag"]
    assert review.apply(root, dec).already == ["p002-inline", "p002-inline"]


def test_a_decision_can_set_a_display_tag(root, tmp_path):
    # p001-d02 has no tag (the number was read into the body); p001-d01's tag was misread
    dec = decide(tmp_path,
                 {"id": "p001-d02", "action": "replace", "latex": "\\left( y \\right) = 2",
                  "tag": "3.2"},
                 {"id": "p001-d01", "action": "accept", "tag": "3.4"})
    rep = review.apply(root, dec)
    assert rep.refused == []
    md = (root / "pages" / "p001.md").read_text("utf-8")
    assert "$$\nx^2 = 1\n\\tag{3.4}\n$$" in md and "\\tag{3.1}" not in md
    assert "$$\n\\left( y \\right) = 2\n\\tag{3.2}\n$$" in md
    assert review.apply(root, dec).already == ["p001-d02", "p001-d01"]


def test_dry_run_writes_nothing(root, tmp_path):
    dec = decide(tmp_path, {"id": "p001-d01", "action": "replace", "latex": "x^2 = 1 + 0"})
    before = (root / "pages" / "p001.md").read_text("utf-8")
    rep = review.apply(root, dec, dry_run=True)
    assert [a["id"] for a in rep.applied] == ["p001-d01"] and rep.pages == []
    assert (root / "pages" / "p001.md").read_text("utf-8") == before


def test_cli_exit_codes_and_report(root, tmp_path, capsys):
    dec = decide(tmp_path, {"id": "p001-d01", "action": "accept"}, {"id": "nope", "action": "accept"})
    assert cli.main(["math-apply-review", "--export", str(root), "--decisions", str(dec)]) == 2
    out = capsys.readouterr().out
    assert "applied 1 decision(s) on 1 page(s)" in out and "refused nope: unknown id" in out
    assert cli.main(["math-apply-review", "--export", str(root), "--decisions", str(dec)]) == 2
    assert "1 applied earlier" in capsys.readouterr().out
    dec = decide(tmp_path, {"id": "p001-d02", "action": "accept"})
    assert cli.main(["math-apply-review", "--export", str(root), "--decisions", str(dec)]) == 0
    assert cli.main(["math-apply-review", "--export", str(tmp_path / "none"),
                     "--decisions", str(dec)]) == 1
    assert "review.jsonl not found" in capsys.readouterr().err
    bad = tmp_path / "bad.jsonl"
    bad.write_text("{not json\n", "utf-8")
    assert cli.main(["math-apply-review", "--export", str(root), "--decisions", str(bad)]) == 1


# ---------------------------------------------------------------- missing displays

M1 = ('<!-- page printed="60" file="4" -->\n\nThe classes are\n\n$$\nB = 2\n$$\n\n'
      "and \\(C = 3\\), so that the density is given by the form below. Next comes more.\n")
ALIGNED = r"\begin{aligned} A &= 1, \\ B &= 2, \\ C &= 3, \end{aligned}"
M_ITEMS = [
    {"id": "p004-m01", "file": 4, "printed": "60", "kind": "missing_display",
     "reasons": ["Marker has a display the standard tier lacks"], "tag": None,
     "latex_primary": None, "latex_second": "A = 1", "crop": "crops/p004-m01.png"},
    {"id": "p004-m02", "file": 4, "printed": "60", "kind": "missing_display",
     "reasons": ["Marker has a display the standard tier lacks"], "tag": None,
     "latex_primary": None, "latex_second": "f(x) = e^{-x}", "crop": "crops/p004-m02.png"},
]


def test_a_missing_display_is_inserted_or_replaces_page_text(tmp_path):
    r = tmp_path / "book"
    (r / "pages").mkdir(parents=True)
    (r / "pages" / "p004.md").write_text(M1, "utf-8")
    (r / "pages" / "p004.flags.json").write_text("[]", "utf-8")
    (r / "review.jsonl").write_text("".join(json.dumps(i) + "\n" for i in M_ITEMS), "utf-8")
    dec = decide(tmp_path,
                 # the engine broke one aligned display into a display and an inline span
                 {"id": "p004-m01", "action": "replace",
                  "latex": ALIGNED, "page_text": "$$\nB = 2\n$$\n\nand \\(C = 3\\),"},
                 {"id": "p004-m02", "action": "replace", "latex": "f(x) = e^{-x}",
                  "tag": "4.7", "after": "is given by the form below."})
    rep = review.apply(r, dec)
    assert rep.refused == [] and [a["id"] for a in rep.applied] == ["p004-m01", "p004-m02"]
    md = (r / "pages" / "p004.md").read_text("utf-8")
    assert md == ('<!-- page printed="60" file="4" -->\n\nThe classes are\n\n$$\n'
                  + ALIGNED + "\n$$\n\n"
                  "so that the density is given by the form below.\n\n$$\nf(x) = e^{-x}\n"
                  "\\tag{4.7}\n$$\n\nNext comes more.\n")
    assert review.apply(r, dec).already == ["p004-m01", "p004-m02"]       # idempotent
    # an anchor that is missing or not unique is refused
    (r / "pages" / "p004.flags.json").write_text("[]", "utf-8")
    bad = decide(tmp_path, {"id": "p004-m01", "action": "replace", "latex": "A = 1",
                            "after": "nowhere"},
                 {"id": "p004-m02", "action": "replace", "latex": "A = 1", "after": "$$"},
                 {"id": "p004-m02", "action": "replace", "latex": "A = 1"})
    assert [x["reason"] for x in review.apply(r, bad).refused] == [
        "anchor text not found in the page", "anchor text is not unique in the page",
        "a missing display needs 'after' or 'page_text'"]


def test_decisions_can_be_a_folder_of_batch_files(root, tmp_path):
    folder = tmp_path / "decisions"
    folder.mkdir()
    (folder / "batch02.jsonl").write_text(json.dumps({"id": "p001-d02", "action": "accept"}) + "\n",
                                          "utf-8")
    (folder / "batch01.jsonl").write_text(json.dumps(
        {"id": "p001-d01", "action": "replace", "latex": "x^2 = 4"}) + "\n", "utf-8")
    (folder / "notes.md").write_text("not decisions", "utf-8")
    rep = review.apply(root, folder)
    assert [a["id"] for a in rep.applied] == ["p001-d01", "p001-d02"] and not rep.refused
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(review.ReviewError, match="no .jsonl"):
        review.apply(root, empty)
