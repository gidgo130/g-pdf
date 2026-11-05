# SPDX-License-Identifier: AGPL-3.0-or-later
"""The math-in-prose detector (review kind inline_missing): prose runs that look like math
the engine left as text (Greek letters, a lone letter at a relation, x1-style indices,
operators between letters), what it must leave alone, the page item with a crop per run,
packing it, and applying a decision that turns a run into inline math."""

import json

import pymupdf
import pytest

from gpdf.pipeline.mathocr import flags, pack, review
from gpdf.pipeline.mathocr.assemble import assemble
from gpdf.pipeline.mathocr.labels import Label


def runs(text):
    return [text[a:b] for a, b, _ in flags.prose_math_runs(text)]


@pytest.mark.parametrize("text, found", [
    ("If γ = 0, the distribution is termed", ["γ = 0"]),
    ("It depends on three parameters a, β, and y.", ["a", "β", "y"]),
    ("A random variable X has a gamma distribution", ["X"]),
    ("the sum of x1 and x2 is", ["x1", "x2"]),
    ("with n - 1 degrees of freedom", ["n - 1"]),
    ("when x > 0 and a = 2,", ["x > 0", "a = 2"]),
    ("the ratio x/y tends to ∞ as", ["x/y", "∞"]),
    ("the value θ² of", ["θ²"]),
    # Paper Capture reads alpha as a: an "a" that cannot be the article
    ("as a increases, the shape", ["a"]),
    ("the value of the parameter a tends to infinity", ["a"]),
    ("depends on a, and it would", ["a"]),
])
def test_math_looking_prose_is_found(text, found):
    assert runs(text) == found


@pytest.mark.parametrize("text", [
    "A random variable has a book in it.",
    "This is, e.g., i.e. what we mean.",
    "See Johnson, N. L. and Kotz, S. (1994), pp. 31-50.",
    "a well-known result in Chapter 12, Section 4.2.",
    "Type III of Pearson's system (Chapter 12).",
    "cases (a) and (b) are treated in 1938 and 1974.",
    "If a random variable is a positive integer for a given value, a gamma law.",
])
def test_ordinary_prose_is_left_alone(text):
    assert runs(text) == []


def P(*parts, y=0.3):
    content = [{"type": "equation_inline", "content": p[1]} if isinstance(p, tuple)
               else {"type": "text", "content": p} for p in parts]
    return {"type": "text", "bbox": [0.1, y, 0.9, y + 0.05], "content": content}


def test_a_page_with_math_in_prose_gets_one_item_with_its_runs():
    pg = assemble([P("If γ = 0 we have ", ("eq", "x_1"), " and the parameter β again.", y=0.2),
                   P("Nothing to see here.", y=0.5)], 12, Label("7", None))
    other = {"displays": [], "paragraphs": [P("If γ = 0 we have ", ("eq", "x_1"),
                                              " and the parameter β again.", y=0.2)]}
    items = flags.review_items(pg, other)
    miss = [it for it in items if it.kind == "inline_missing"]
    assert len(miss) == 1 and miss[0].id == "p012-inline-missing"
    rec = miss[0].record()
    assert [r["text"] for r in rec["runs"]] == ["γ = 0", "β"]
    first = rec["runs"][0]
    assert first["paragraph"] == 1 and first["occurrence"] == 1 and first["why"]
    assert first["bbox"] == [0.1, 0.2, 0.9, 0.25] and 0 <= first["at"] < 0.3
    assert "If γ = 0 we have" in first["context"]
    assert rec["latex_primary"] is None and rec["reasons"] == ["2 prose runs look like math"]


def test_runs_get_line_crops(tmp_path):
    path = tmp_path / "p.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=300, height=400)
    for k in range(3):
        page.insert_text((40, 100 + 16 * k), "a line of text with words", fontsize=11)
    doc.save(str(path))
    doc.close()
    bbox = [0.1, 85 / 400, 0.9, 155 / 400]
    it = flags.Item("p001-inline-missing", 1, "1", "inline_missing", ["x"], None, None, None,
                    extra={"runs": [{"paragraph": 1, "text": "β", "bbox": bbox, "at": 0.5}]})
    doc = pymupdf.open(str(path))
    flags.write_crops(doc, [it], tmp_path / "crops", dpi=100)
    doc.close()
    run = it.extra["runs"][0]
    assert run["crop"] == "crops/p001-inline-missing-s1.png" and (tmp_path / run["crop"]).is_file()
    assert it.crop == run["crop"] and len(run["line_box"]) == 4


MD = ('<!-- page printed="7" file="12" -->\n\nIf γ = 0 we have \\(x_1\\) and β, and β.\n')
ITEM = {"id": "p012-inline-missing", "file": 12, "printed": "7", "kind": "inline_missing",
        "reasons": ["2 prose runs look like math"], "tag": None, "latex_primary": None,
        "latex_second": None, "crop": "crops/p012-inline-missing-s1.png",
        "runs": [{"paragraph": 1, "text": "γ = 0", "occurrence": 1, "why": "relation",
                  "crop": "crops/p012-inline-missing-s1.png"},
                 {"paragraph": 1, "text": "β", "occurrence": None, "why": "greek",
                  "crop": "crops/p012-inline-missing-s2.png"}]}


@pytest.fixture
def export(tmp_path):
    r = tmp_path / "book"
    (r / "pages").mkdir(parents=True)
    (r / "pages" / "p012.md").write_text(MD, "utf-8")
    (r / "pages" / "p012.flags.json").write_text("[]", "utf-8")
    (r / "review.jsonl").write_text(json.dumps(ITEM, ensure_ascii=False) + "\n", "utf-8")
    (r / "text.md").write_text(MD, "utf-8")
    return r


def test_pack_inline_missing_only_when_asked(export, tmp_path):
    assert pack.pack(export, tmp_path / "a")["items"] == 0
    s = pack.pack(export, tmp_path / "b", kinds=("inline_missing",))
    assert s["by_kind"] == {"inline_missing": 1}
    it = json.loads((tmp_path / "b" / "batch01" / "items.jsonl").read_text("utf-8"))
    assert [r["text"] for r in it["runs"]] == ["γ = 0", "β"]
    assert it["runs"][0]["crop"] == str((export / "crops" / "p012-inline-missing-s1.png").resolve())
    assert [c["occurrence"] for c in it["runs"][1]["occurrence_candidates"]] == [1, 2]
    brief = (tmp_path / "b" / "batch01" / "BRIEF.md").read_text("utf-8")
    assert "inline_missing" in brief


def test_apply_turns_a_run_into_inline_math(export, tmp_path):
    dec = tmp_path / "d.jsonl"
    dec.write_text(json.dumps({"id": "p012-inline-missing", "action": "replace", "runs": [
        {"text": "γ = 0", "latex": "\\gamma = 0"},
        {"text": "β", "latex": "\\beta", "occurrence": 2}]}) + "\n", "utf-8")
    rep = review.apply(export, dec)
    assert rep.refused == []
    md = (export / "pages" / "p012.md").read_text("utf-8")
    assert md.endswith("If \\(\\gamma = 0\\) we have \\(x_1\\) and β, and \\(\\beta\\).\n")
    assert review.apply(export, dec).already == ["p012-inline-missing"]


def test_apply_refuses_an_ambiguous_or_absent_run(export, tmp_path):
    dec = tmp_path / "d.jsonl"
    dec.write_text("".join(json.dumps(d) + "\n" for d in [
        {"id": "p012-inline-missing", "action": "replace", "runs": [{"text": "β", "latex": "b"}]},
        {"id": "p012-inline-missing", "action": "replace",
         "runs": [{"text": "δ", "latex": "\\delta"}]},
        {"id": "p012-inline-missing", "action": "replace", "runs": [{"text": "x"}]}]), "utf-8")
    rep = review.apply(export, dec)
    assert sorted(r["reason"] for r in rep.refused) == [
        "an inline_missing item needs runs [{text, latex}]",
        "run 'β' occurs 2 times in the page prose: give its occurrence",
        "run 'δ' is not in the page prose"]
    assert review.apply(export, tmp_path / "d.jsonl").applied == []
