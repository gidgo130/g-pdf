# SPDX-License-Identifier: AGPL-3.0-or-later
"""gpdf-cli math-score on hand-written reference and candidate pages: normalization
cases, a sign near-miss counted as value-changing, tags, missed and extra formulas,
inline rate, prose CER, a split display, --pages, text.md input, flag recall, the
terminal output without formulas."""

import json

from gpdf import cli
from gpdf.pipeline.mathocr import score

REF = {
    "p010": ('<!-- page printed="1" file="10" -->\n\nLet \\(x_1\\) and \\(y\\) be real. Then\n\n'
             "$$\nx^2 + y^2 = z^2\n\\tag{1.1}\n$$\n\nand with \\(\\sigma^2\\)\n\n"
             "$$\n\\left( a \\right) \\leq b + \\cdots + c\n$$\n\n"
             "$$\nf(x) = 1 - e^{-x}\n\\tag{1.2}\n$$\n\nThe hyphen-\nated prose ends here.\n"),
    "p011": ('<!-- page printed="2" file="11" -->\n\nA display that continues\n\n'
             "$$\np(x) = \\exp\\{-(a_0 + a_1 x_1\n$$\n"),
    "p012": ('<!-- page printed="3" file="12" -->\n$$\n+ a_2 x_2)\\}\n\\tag{1.3}\n$$\n\n'
             "Only prose here.\n"),
}
REF_FLAGS = {"p012": [{"type": "page_anomaly", "note": "display continues from p. 2; $$ reopened"}]}
CAND = {
    "p010": ('<!-- page printed="1" file="10" -->\n\nLet \\(x_{1}\\) and \\(y\\) be real. Then\n\n'
             "$$\nx^{2} + y^{2} = z^{2}\n\\tag{1.1}\n$$\n\nand with \\(\\sigma^3\\)\n\n"
             "$$\n(a) \\le b + \\dots + c\n$$\n\n"
             "$$\nf(x) = 1 + e^{-x}\n\\tag{1.2}\n$$\n\n$$\nextra = 1\n$$\n\n"
             "The hyphenated prose ends here!\n"),
    "p011": ('<!-- page printed="2" file="11" -->\n\nA display that continues\n\n'
             "$$\np(x) = \\exp\\{-(a_0 + a_1 x_1\n$$\n"),
    "p012": ('<!-- page printed="3" file="12" -->\n$$\n+ a_2 x_2)\\}\n\\tag{1.3}\n$$\n\n'
             "Only prose here.\n"),
}
CAND_FLAGS = {"p012": [{"type": "page_anomaly", "what": "split_display", "note": "x"}]}
ITEMS = [{"id": "p010-d03", "file": 10, "printed": "1", "kind": "disagree",
          "reasons": ["tiers disagree"], "tag": "1.2", "latex_primary": "f(x) = 1 + e^{-x}",
          "latex_second": "f(x) = 1 - e^{-x}", "crop": None},
         {"id": "p010-d02", "file": 10, "printed": "1", "kind": "stack",
          "reasons": ["two index variables (a, b)"], "tag": None,
          "latex_primary": "(a) \\le b + \\dots + c", "latex_second": None, "crop": None}]


def write(folder, pages, flags, items=None):
    (folder / "pages").mkdir(parents=True)
    for stem, md in pages.items():
        (folder / "pages" / f"{stem}.md").write_text(md, "utf-8")
        (folder / "pages" / f"{stem}.flags.json").write_text(json.dumps(flags.get(stem, [])), "utf-8")
    (folder / "text.md").write_text("\n".join(pages[s] for s in sorted(pages)), "utf-8")
    if items is not None:
        (folder / "review.jsonl").write_text("".join(json.dumps(i) + "\n" for i in items), "utf-8")
    return folder


def test_score_counts_exact_near_misses_tags_missed_extra_inline_and_prose(tmp_path):
    ref = write(tmp_path / "ref", REF, REF_FLAGS)
    cand = write(tmp_path / "cand", CAND, CAND_FLAGS)
    s, recall = score.run(ref, cand, tmp_path / "out")
    assert recall is None
    # 4 reference displays after joining the split one; the braces and \leq/\left cases are exact
    assert (s["ref_formulas"], s["cand_formulas"], s["matched"], s["missed"], s["extra"]) == (4, 5, 4, 0, 1)
    assert s["exact_pct"] == 50.0                          # 1.1 and the joined 1.3 exact
    assert s["tag_acc_pct"] == 100.0 and s["tagged"] == 3
    assert s["near_miss_1to3"] == 2 and s["near_miss_dangerous"] == 1    # sign; dots cosmetic
    assert s["errors"] == 2 and s["value_changing_errors"] == 1
    assert s["inline_exact_pct"] == 66.7 and s["inline_ref"] == 3
    assert 0 < s["prose_cer"] < 0.05
    assert s["pages"] == 3 and s["pages_in_candidate"] == 3
    assert s["label_acc_pct"] == 100.0 and s["label_pages"] == 3
    out = json.loads((tmp_path / "out" / "score.json").read_text("utf-8"))
    pairs = {p["ref_tag"]: p for p in out["pairs"]}
    assert pairs["1.2"]["ed"] == 1 and not pairs["1.2"]["cosmetic"]
    assert pairs["1.2"]["diffs"] == [["replace", "-", "+"]]
    assert pairs["1.3"]["page"] == "p011+p012"
    assert pairs[None]["cosmetic"] and pairs[None]["ed"] == 1
    report = (tmp_path / "out" / "REPORT.md").read_text("utf-8")
    assert "VALUE-CHANGING" in report and "f(x) = 1 + e^{-x}" in report


def test_flag_recall_by_kind_and_the_unflagged_list(tmp_path):
    ref = write(tmp_path / "ref", REF, REF_FLAGS)
    cand = write(tmp_path / "cand", CAND, CAND_FLAGS, ITEMS)
    s, recall = score.run(ref, cand, tmp_path / "out")
    assert recall["by_class"]["all"] == {"wrong": 2, "covered": 2, "recall": 1.0,
                                         "by_kind": {"disagree": 1, "stack": 1}}
    assert recall["by_class"]["value_changing"] == {"wrong": 1, "covered": 1, "recall": 1.0,
                                                    "by_kind": {"disagree": 1, "stack": 0}}
    assert recall["by_class"]["style"]["wrong"] == 0 and recall["unflagged_value_changing"] == []
    assert s["flag_recall"] == {"all": 1.0, "value_changing": 1.0, "style": None}
    cand2 = write(tmp_path / "cand2", CAND, CAND_FLAGS, ITEMS[1:])
    s, recall = score.run(ref, cand2, tmp_path / "out2")
    assert recall["by_class"]["value_changing"]["covered"] == 0
    assert [x["ref_tag"] for x in recall["unflagged_value_changing"]] == ["1.2"]
    assert "Unflagged value-changing errors: 1" in (tmp_path / "out2" / "REPORT.md").read_text("utf-8")


def test_text_md_input_and_pages_restriction(tmp_path):
    ref = write(tmp_path / "ref", REF, REF_FLAGS)
    cand = write(tmp_path / "cand", CAND, CAND_FLAGS)
    s, _ = score.run(ref / "text.md", cand / "text.md", tmp_path / "out", pages="10")
    assert s["pages"] == 1 and s["ref_formulas"] == 3 and s["extra"] == 1
    s2, _ = score.run(ref, cand, tmp_path / "out2", pages="11-12")
    assert s2["pages"] == 2 and s2["ref_formulas"] == 1 and s2["exact_pct"] == 100.0


def test_cli_prints_numbers_only(tmp_path, capsys):
    ref = write(tmp_path / "ref", REF, REF_FLAGS)
    cand = write(tmp_path / "cand", CAND, CAND_FLAGS, ITEMS)
    assert cli.main(["math-score", "--ref", str(ref), "--cand", str(cand),
                     "--out", str(tmp_path / "out")]) == 0
    out = capsys.readouterr().out
    assert "exact_pct: 50.0" in out and "flag recall value_changing: 1/1 = 1.0" in out
    numbers = out.rsplit("\n", 2)[0]                       # all but the output-folder line
    assert "\\" not in numbers and "e^{-x}" not in numbers and "$$" not in numbers
    assert (tmp_path / "out" / "score.json").is_file() and (tmp_path / "out" / "REPORT.md").is_file()
    assert cli.main(["math-score", "--ref", str(tmp_path / "none"), "--cand", str(cand),
                     "--out", str(tmp_path / "out")]) == 1
    assert "not found" in capsys.readouterr().err
