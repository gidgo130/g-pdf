# SPDX-License-Identifier: AGPL-3.0-or-later
"""gpdf-cli math-provenance on a hand-made, reviewed export folder: one formulas.jsonl line
per display and inline span with its status (engines_agree, reviewed_accept, replaced,
flagged, identity_checked, unreviewed), the reviewer, the flags and notes; a method block
in meta.json with the engines, OCR mode, input, reviewer and coverage."""

import json

import pytest

from gpdf import cli
from gpdf.pipeline.mathocr import provenance

P1 = ('<!-- page printed="54" file="1" -->\n\n# 7 Chapter\n\nProse with \\(a_2\\) and \\(b\\) and '
      "\\(c\\).\n\n$$\nx^2 = 2\n\\tag{7.1}\n$$\n\n$$\ny = 3\n$$\n\n$$\nf(x) = e^{-x}\n$$\n")
P2 = ('<!-- page printed="55" file="2" -->\n\nMore \\(d\\).\n\n$$\n\\mathbf{z} = 4\n$$\n\n'
      "$$\nw = 5\n\\tag{7.2}\n$$\n")
ITEMS = [
    {"id": "p001-d01", "file": 1, "printed": "54", "kind": "disagree", "tag": "7.1",
     "latex_primary": "x^2 = 1", "reasons": ["tiers disagree"]},
    {"id": "p001-d02", "file": 1, "printed": "54", "kind": "marker", "tag": None,
     "latex_primary": "y = 3", "reasons": ["marker"]},
    {"id": "p001-inline", "file": 1, "printed": "54", "kind": "inline", "latex_primary": None,
     "spans": [{"latex_primary": "a_1"}, {"latex_primary": "b"}, {"latex_primary": "c"}]},
    {"id": "p002-d01", "file": 2, "printed": "55", "kind": "style", "tag": None,
     "latex_primary": "\\mathbf{z} = 4", "reasons": ["style"]},
    {"id": "p002-d02", "file": 2, "printed": "55", "kind": "identity", "tag": "7.2",
     "latex_primary": "w = 5", "reasons": ["identity failed: sums to 1"], "identity": ["sums to 1"]},
]
F1 = [{"type": "reviewed", "id": "p001-d01", "action": "replace", "latex": "x^2 = 2"},
      {"type": "reviewed", "id": "p001-d02", "action": "accept"},
      {"type": "reviewed", "id": "p001-inline", "action": "replace",
       "spans": [{"latex_primary": "a_1", "latex": "a_2", "occurrence": 1}]},
      {"type": "reviewed", "id": "p001-inline", "action": "accept",
       "spans": [{"latex_primary": "b", "latex": "b", "occurrence": 1}]},
      {"type": "summary", "display_formulas": 3}]
F2 = [{"type": "possible_source_typo", "subtype": "math_error", "eq": "7.2", "latex": "w = 5",
       "note": "printed so"},
      {"type": "reviewed", "id": "p002-d02", "action": "flag", "flag_type": "possible_source_typo"},
      {"type": "summary", "display_formulas": 2}]
IDENT = [{"file": 1, "display": 3, "latex": "f(x) = e^{-x}", "kind": "density", "status": "passed"},
         {"file": 2, "display": 2, "latex": "w = 5", "kind": "pmf", "status": "failed"}]


@pytest.fixture
def export(tmp_path):
    r = tmp_path / "book"
    (r / "pages").mkdir(parents=True)
    for n, md, fl in ((1, P1, F1), (2, P2, F2)):
        (r / "pages" / f"p{n:03d}.md").write_text(md, "utf-8")
        (r / "pages" / f"p{n:03d}.flags.json").write_text(json.dumps(fl), "utf-8")
    (r / "review.jsonl").write_text("".join(json.dumps(i) + "\n" for i in ITEMS), "utf-8")
    (r / "identities.jsonl").write_text("".join(json.dumps(i) + "\n" for i in IDENT), "utf-8")
    (r / "text.md").write_text(P1 + "\n" + P2, "utf-8")
    (r / "meta.json").write_text(json.dumps({
        "source": {"path": "C:\\books\\book.pdf", "sha256": "ab" * 32, "page_count": 9},
        "engine": {"version": "4.1.7"}, "marker": {"packages": {"marker-pdf": "2.1.0",
                                                                "surya-ocr": "0.23.0"},
                                                   "llama_build": "11765"},
        "dpi": 300, "options": {"ocr_mode": "ocr", "pages": "1-2"}}), "utf-8")
    (r / "run.json").write_text(json.dumps({"totals": {"identities": {
        "checked": 2, "passed": 1, "failed": 1, "inconclusive": 0, "skipped": 3}}}), "utf-8")
    return r


def lines(p):
    return [json.loads(s) for s in p.read_text("utf-8").splitlines() if s.strip()]


def test_one_line_per_formula_with_its_status(export, tmp_path):
    out = tmp_path / "deliver"
    out.mkdir()
    (out / "meta.json").write_text(json.dumps({"title": "A Book"}), "utf-8")
    res = provenance.write(export, out, reviewer="sonnet", meta=out / "meta.json")
    recs = {r["id"]: r for r in lines(out / "formulas.jsonl")}
    assert list(recs) == ["p001-d01", "p001-d02", "p001-d03", "p001-i01", "p001-i02", "p001-i03",
                          "p002-d01", "p002-d02", "p002-i01"]
    d = recs["p001-d01"]
    assert (d["status"], d["kind"], d["tag"], d["printed"], d["file"], d["reviewer"]) == \
        ("replaced", "display", "7.1", "54", 1, "sonnet")
    assert recs["p001-d02"]["status"] == "reviewed_accept"
    assert (recs["p001-d03"]["status"], recs["p001-d03"]["reviewer"]) == ("identity_checked", None)
    assert [recs[f"p001-i0{k}"]["status"] for k in (1, 2, 3)] == \
        ["replaced", "reviewed_accept", "unreviewed"]
    assert recs["p002-d01"]["status"] == "unreviewed" and recs["p002-d01"]["notes"] == ["style"]
    f = recs["p002-d02"]
    assert f["status"] == "flagged" and f["flags"] == [
        {"type": "possible_source_typo", "subtype": "math_error"}]
    assert recs["p002-i01"]["status"] == "engines_agree"
    assert "latex" not in d                            # ids and statuses, no formulas
    meta = json.loads((out / "meta.json").read_text("utf-8"))
    assert meta["title"] == "A Book"
    m = meta["method"]
    assert m["engines"] == {"mineru": "4.1.7", "marker-pdf": "2.1.0", "surya-ocr": "0.23.0",
                            "llama.cpp": "11765"}
    assert (m["ocr_mode"], m["dpi"], m["input"]["file"], m["reviewer_model"]) == \
        ("ocr", 300, "book.pdf", "sonnet")
    c = m["coverage"]
    assert (c["displays"], c["displays_reviewed"], c["displays_reviewed_pct"]) == (5, 3, 60.0)
    assert (c["inline"], c["inline_reviewed"], c["inline_reviewed_pct"]) == (4, 2, 50.0)
    assert c["numeric_checks"] == 2
    assert res["by_status"]["replaced"] == 2


def test_cli_and_numbers_only_on_screen(export, tmp_path, capsys):
    assert cli.main(["math-provenance", "--export", str(export), "--reviewer", "sonnet"]) == 0
    out = capsys.readouterr().out
    assert "9 formulas" in out and "x^2" not in out
    assert (export / "formulas.jsonl").is_file()
    assert json.loads((export / "meta.json").read_text("utf-8"))["method"]["reviewer_model"] == "sonnet"


def test_an_inline_accept_without_spans_covers_the_sampled_spans_of_its_page(export, tmp_path):
    # the reviewer accepted span c (sampled) but dropped the template's "spans" line
    rev = tmp_path / "rev"
    (rev / "batch01").mkdir(parents=True)
    (rev / "decisions").mkdir()
    (rev / "batch01" / "items.jsonl").write_text(json.dumps(
        {"key": "p001-inline#3", "id": "p001-inline", "kind": "inline_span", "file": 1,
         "latex_primary": "c"}) + "\n", "utf-8")
    (rev / "decisions" / "batch01.jsonl").write_text(json.dumps(
        {"id": "p001-inline", "action": "accept"}) + "\n", "utf-8")
    provenance.write(export, reviewer="sonnet", review_dirs=[rev])
    recs = {r["id"]: r for r in lines(export / "formulas.jsonl")}
    assert recs["p001-i03"]["status"] == "reviewed_accept"


def test_a_flag_attaches_by_equation_number_or_as_a_fragment(export, tmp_path):
    # a display changed after it was flagged (a split, a corrected field) is still found by the
    # number in the flag's eq; a reference flag quoting part of a display is found as a fragment
    fl = json.loads((export / "pages" / "p001.flags.json").read_text("utf-8"))
    fl.insert(0, {"type": "possible_source_typo", "eq": "(7.1)", "latex": "x^2 = 3", "note": "old text"})
    fl.insert(0, {"type": "possible_source_typo", "latex": "e^{-x}", "note": "a fragment"})
    (export / "pages" / "p001.flags.json").write_text(json.dumps(fl), "utf-8")
    provenance.write(export, reviewer="sonnet")
    recs = {r["id"]: r for r in lines(export / "formulas.jsonl")}
    assert recs["p001-d01"]["status"] == "flagged"                  # tag 7.1
    assert recs["p001-d03"]["status"] == "flagged"                  # contains e^{-x}
    assert recs["p001-d02"]["status"] == "reviewed_accept"


def test_flag_latex_quotes_the_final_formula_of_each_flag(export, tmp_path):
    # opt-in: a flag entry quotes the formula as the final page has it; a flag whose LaTeX is
    # stale (attached by its eq) quotes the display, one quoting part of a display keeps its
    # fragment; an inline flag quotes its span. The default output stays formula-free.
    fl = json.loads((export / "pages" / "p001.flags.json").read_text("utf-8"))
    fl.insert(0, {"type": "possible_source_typo", "eq": "(7.1)", "latex": "x^2 = 3", "note": "stale"})
    fl.insert(0, {"type": "copy_corrupt", "latex": "e^{-x}", "note": "a fragment"})
    fl.insert(0, {"type": "formula_uncertain", "eq": "p001 inline", "latex": "c", "note": "span"})
    (export / "pages" / "p001.flags.json").write_text(json.dumps(fl), "utf-8")
    provenance.write(export, reviewer="sonnet", flag_latex=True)
    recs = {r["id"]: r for r in lines(export / "formulas.jsonl")}
    assert recs["p001-d01"]["flags"] == [{"type": "possible_source_typo", "latex": "x^2 = 2"}]
    assert recs["p001-d03"]["flags"] == [{"type": "copy_corrupt", "latex": "e^{-x}"}]
    assert recs["p001-i03"]["flags"] == [{"type": "formula_uncertain", "latex": "c"}]
    assert recs["p002-d02"]["flags"] == [
        {"type": "possible_source_typo", "subtype": "math_error", "latex": "w = 5"}]
    assert "latex" not in recs["p001-d02"]
    assert cli.main(["math-provenance", "--export", str(export), "--flag-latex"]) == 0
    assert lines(export / "formulas.jsonl")[0]["flags"][0]["latex"] == "x^2 = 2"
    provenance.write(export, reviewer="sonnet")
    assert all("latex" not in f for r in lines(export / "formulas.jsonl") for f in r["flags"])


def test_prose_only_marks_every_formula_unreviewed(export, tmp_path):
    for n in (1, 2):                                    # an export nobody reviewed: no decisions, no flags
        (export / "pages" / f"p{n:03d}.flags.json").write_text(json.dumps([{"type": "dropped", "kind": "running_head",
                                                                             "text": "RUNNING HEAD"}]), "utf-8")
    out = tmp_path / "deliver"
    out.mkdir()
    (out / "meta.json").write_text(json.dumps({"title": "A History"}), "utf-8")
    res = provenance.write(export, out, meta=out / "meta.json", prose_only=True)
    recs = lines(out / "formulas.jsonl")
    assert len(recs) == 9
    assert {r["status"] for r in recs} == {"unreviewed"}
    assert all(r["notes"] == ["prose_only"] and r["reviewer"] is None and r["flags"] == [] for r in recs)
    assert all("review_item" not in r for r in recs)
    m = json.loads((out / "meta.json").read_text("utf-8"))["method"]
    assert m["prose_only"] is True and m["reviewer_model"] is None and "not reviewed" in m["review"]
    assert m["coverage"]["displays_reviewed"] == 0 and m["coverage"]["inline_reviewed"] == 0
    assert m["coverage"]["displays"] == 5 and m["coverage"]["inline"] == 4
    assert m["formulas_by_status"]["unreviewed"] == 9 and res["by_status"]["engines_agree"] == 0
    # the CLI flag; an export that carries review decisions is refused in prose-only mode
    assert cli.main(["math-provenance", "--export", str(export), "--prose-only"]) == 0
    (export / "pages" / "p001.flags.json").write_text(json.dumps(F1), "utf-8")
    with pytest.raises(provenance.ProvenanceError):
        provenance.write(export, prose_only=True)
