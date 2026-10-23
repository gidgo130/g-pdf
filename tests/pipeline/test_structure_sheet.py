# SPDX-License-Identifier: AGPL-3.0-or-later
"""The structure review sheet (structure.review.html): written by every full structure
run with the detected sections (before the decisions), a summary and hash of every unit,
the entry's prefilled metadata and the decisions file as loaded; entries with
low-confidence sections first; nothing in it can end its script element."""

import json
import re

from gpdf import cli
from gpdf.pipeline import sections as sc
from gpdf.pipeline import structure_decisions as sd

from .test_sections import anthology, text_dir  # noqa: F401


def sheet_data(path):
    html = path.read_text("utf-8")
    m = re.search(r'<script type="application/json" id="gpdf-data">(.*?)</script>', html, re.S)
    assert m and html.count("</script>") == 2
    return json.loads(m.group(1))


def test_structure_run_writes_the_sheet_with_detected_sections_units_and_decisions(
        text_dir, tmp_path, capsys):
    units = text_dir / "libro.jsonl"
    rows = [json.loads(s) for s in units.read_text("utf-8").splitlines()]
    rows[18]["text"] = "Segundo párrafo </script><b>no</b> " + "largo " * 80
    units.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), "utf-8")
    out = tmp_path / "structure"
    assert cli.main(["structure", "--text", str(text_dir), "--out", str(out)]) == 0
    sheet = out / sd.SHEET
    assert f"review sheet: {sheet}" in capsys.readouterr().out
    data = sheet_data(sheet)
    assert data["decisions_name"] == sd.DECISIONS and data["roles"][0] == "front"
    assert data["decisions"] == {"schema": 1, "entries": {}}
    (v,) = data["entries"]
    assert (v["id"], v["file"], v["kind"], v["pages"]) == ("e" * 64, "libro.pdf", "pdf", 22)
    assert len(v["units"]) == 30
    kind, p1, p2, l1, l2, text, hsh = v["units"][16]
    assert (kind, p1, p2, l1, l2) == ("h", 12, 12, "12", "12")
    assert text == "Ensayo uno sobre la vanguardia" and hsh == sd.unit_hash(text)
    long = v["units"][18][5]
    assert long.startswith("Segundo párrafo </script>") and " … " in long and len(long) < 210
    assert v["units"][18][6] == sd.unit_hash(rows[18]["text"])
    titles = [(d["first"], d["level"], d["title"]) for d in v["detected"]]
    assert titles[:3] == [(1, 1, "Front matter"), (4, 2, "Cinco prefacios"),
                          (5, 3, "La vanguardia en telegrama")]
    assert v["metadata"]["title"] == "Las vanguardias en la isla"
    assert v["metadata"]["confirmed"] is False

    rec = {"metadata": {"title": "Isla", "confirmed": True},
           "ops": [{"op": "merge", "seq": 12, "level": 2, "hash": v["units"][11][6]}]}
    dec = {"schema": 1, "saved": "x", "entries": {"e" * 64: rec}}
    (out / sd.DECISIONS).write_text(json.dumps(dec), "utf-8")
    assert cli.main(["structure", "--text", str(text_dir), "--out", str(out)]) == 0
    data = sheet_data(sheet)
    (v,) = data["entries"]
    assert data["decisions"] == dec
    assert any(d["title"] == "Puerto Rico" for d in v["detected"])        # before decisions
    assert v["metadata"]["title"] == "Las vanguardias en la isla"
    final = json.loads((out / "libro.sections.json").read_text("utf-8"))
    assert not any(x["title"] == "Puerto Rico" for x in final["sections"])

    before = sheet.stat().st_mtime_ns
    assert cli.main(["structure", "--text", str(text_dir), "--out", str(out), "--only",
                     "libro"]) == 0
    assert "review sheet not rewritten (--only)" in capsys.readouterr().out
    assert sheet.stat().st_mtime_ns == before


def test_entries_with_low_confidence_sections_come_first():
    plain, low = sc.detect(anthology()), sc.detect(anthology())
    plain.entry.input, low.entry.input = "a.pdf", "b.pdf"
    low.sections[3].confidence = "low"
    for s in (plain, low):
        s.detected = [x.to_json() for x in s.sections]
        s.detected_metadata = dict(s.metadata)
    data = sd.sheet_data([plain, low], sd.empty(), "OUT")
    assert [v["file"] for v in data["entries"]] == ["b.pdf", "a.pdf"]
    assert [v["low"] for v in data["entries"]] == [1, 0]


def test_unit_summary_keeps_short_texts_and_cuts_long_ones_in_the_middle():
    assert sd.unit_summary("corto") == "corto"
    t = "a" * 150 + "b" * 150
    s = sd.unit_summary(t)
    assert s.startswith("a" * 120) and s.endswith("b" * 80) and " … " in s
