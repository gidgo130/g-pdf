# SPDX-License-Identifier: AGPL-3.0-or-later
"""The owner's structure decisions (structure.decisions.json): edits, moved boundaries,
merges, splits, an entry's role and its metadata applied over the detection, in order;
a decision whose unit text changed is stale, one whose section is gone is unmatched;
neither is applied. The decisions file is only read."""

import json

import pytest

from gpdf import cli
from gpdf.pipeline import sections as sc
from gpdf.pipeline import structure_decisions as sd

from .test_sections import anthology, check_invariants, text_dir  # noqa: F401


def h(e, seq):
    return sd.unit_hash(e.units[seq - 1]["text"])


def tree(s):
    return [(x.first, x.last, x.level, x.title, x.author, x.role) for x in s.sections]


def detected():
    e = anthology()
    return e, sc.detect(e)


def test_edit_move_merge_split_role_and_metadata_apply_in_order():
    e, s = detected()
    by = {x.title: x for x in s.sections}
    assert (by["Ensayo dos sobre el choteo"].first, by["Puerto Rico"].first) == (21, 12)
    rec = {"role": "criticism",
           "metadata": {"title": "Las vanguardias en la isla", "author": "Editor Prueba",
                        "year": "1999", "publisher": "Editorial Prueba", "confirmed": True},
           "ops": [
               {"op": "edit", "seq": 16, "level": 2, "hash": h(e, 16), "author": "Autora Una",
                "role": "primary"},
               {"op": "move", "seq": 21, "level": 2, "hash": h(e, 21), "to": 20,
                "to_hash": h(e, 20)},
               {"op": "merge", "seq": 12, "level": 2, "hash": h(e, 12)},
               {"op": "split", "seq": 23, "level": 3, "hash": h(e, 23), "title": "Primera parte",
                "author": "", "role": "criticism"}]}
    results = sd.apply(s, rec)
    assert [(r["op"], r["status"]) for r in results] == [
        ("role", "applied"), ("metadata", "applied"), ("edit", "applied"), ("move", "applied"),
        ("merge", "applied"), ("split", "applied")]
    check_invariants(s, e.units)
    by = {x.title: x for x in s.sections}
    uno, dos = by["Ensayo uno sobre la vanguardia"], by["Ensayo dos sobre el choteo"]
    assert (uno.first, uno.last, uno.author, uno.role) == (16, 19, "Autora Una", "primary")
    assert uno.owner and "owner" in uno.evidence[-1]
    assert (dos.first, dos.last, dos.role) == (20, 27, "criticism")
    assert "Puerto Rico" not in by and (by["Cuba"].first, by["Cuba"].last) == (8, 14)
    part = by["Primera parte"]
    assert (part.first, part.last, part.level, part.parent) == (23, 24, 3, dos.id)
    assert part.author is None
    assert part.pdf_pages == [15, 16] and part.printed == ["15", "16"] and part.owner
    assert all(x.role in ("criticism", "primary") for x in s.sections)
    assert [x.id for x in s.sections] == [f"s{k:03d}" for k in range(1, len(s.sections) + 1)]
    assert s.metadata["confirmed"] is True and s.metadata["author"] == "Editor Prueba"
    assert "owner" in s.metadata["evidence"]
    assert s.checks["sections_by_role"]["primary"] == 1


def test_stale_and_unmatched_decisions_are_reported_and_never_applied():
    e, s = detected()
    before = tree(s)
    old21 = h(e, 21)
    e.units[20]["text"] = "Ensayo dos sobre el choteo (texto corregido)"   # the export changed
    rec = {"ops": [
        {"op": "edit", "seq": 21, "level": 2, "hash": old21, "title": "Otro"},
        {"op": "edit", "seq": 13, "level": 2, "hash": h(e, 13), "title": "Nada"},
        {"op": "move", "seq": 1, "level": 1, "hash": h(e, 1), "to": 2, "to_hash": h(e, 2)},
        {"op": "move", "seq": 16, "level": 2, "hash": h(e, 16), "to": 18, "to_hash": "0" * 10},
        {"op": "move", "seq": 8, "level": 2, "hash": h(e, 8), "to": 7, "to_hash": h(e, 7)},
        {"op": "move", "seq": 12, "level": 2, "hash": h(e, 12), "to": 15, "to_hash": h(e, 15)},
        {"op": "merge", "seq": 1, "level": 1, "hash": h(e, 1)},
        {"op": "split", "seq": 8, "level": 2, "hash": h(e, 8), "title": "x", "role": "primary"},
        {"op": "split", "seq": 9, "level": 2, "hash": h(e, 9), "title": "x", "role": "poetry"},
        {"op": "edit", "seq": 99, "level": 2, "hash": "abc", "title": "x"},
        {"op": "rename", "seq": 8, "level": 2, "hash": h(e, 8)}]}
    results = sd.apply(s, rec)
    assert [r["status"] for r in results] == [
        "stale", "unmatched", "unmatched", "stale", "unmatched", "unmatched", "unmatched",
        "unmatched", "unmatched", "stale", "unmatched"]
    whys = [r["why"] for r in results]
    assert "export changed" in whys[0] and "no level-2 section starts at unit 13" in whys[1]
    assert "first unit" in whys[2] and "unit 18" in whys[3]
    assert "parent" in whys[4] and "empty" in whys[5] and "first part" in whys[6]
    assert "already" in whys[7] and "role" in whys[8] and "unknown" in whys[10]
    assert tree(s) == before
    check_invariants(s, e.units)


def deep_ops(e):
    """Level 4 and 5 under essay two's Bibliografía (level 3 at unit 25), refused splits two
    and three levels below their parent, a move and merges with deep sections at the same
    unit (a merge may leave a deeper section below a shallower parent for a while: the
    split after it changes the level back), malformed levels."""
    return [
        {"op": "split", "seq": 26, "level": 4, "hash": h(e, 26), "title": "Cuba",
         "author": "", "role": "bibliography"},
        {"op": "split", "seq": 27, "level": 5, "hash": h(e, 27), "title": "Madrid",
         "author": "Otro", "role": "primary"},
        {"op": "split", "seq": 23, "level": 4, "hash": h(e, 23), "title": "Salto",
         "role": "primary"},
        {"op": "split", "seq": 22, "level": 5, "hash": h(e, 22), "title": "", "role": "primary"},
        {"op": "split", "seq": 25, "level": 4, "hash": h(e, 25), "title": "Lista",
         "role": "bibliography"},
        {"op": "move", "seq": 25, "level": 3, "hash": h(e, 25), "to": 24, "to_hash": h(e, 24)},
        {"op": "merge", "seq": 24, "level": 4, "hash": h(e, 24)},
        {"op": "merge", "seq": 24, "level": 3, "hash": h(e, 24)},
        {"op": "split", "seq": 24, "level": 3, "hash": h(e, 24), "title": "Bibliografía citada",
         "role": "bibliography"},
        {"op": "split", "seq": 20, "level": 0, "hash": h(e, 20), "title": "x"},
        {"op": "edit", "seq": 26, "level": True, "hash": h(e, 26), "title": "x"},
        {"op": "edit", "seq": 26, "level": "4", "hash": h(e, 26), "title": "x"}]


DEEP_STATUS = ["applied", "applied", "unmatched", "unmatched", "applied", "applied",
               "applied", "applied", "applied", "unmatched", "unmatched", "unmatched"]


def test_sections_nest_to_any_depth_one_level_below_their_parent():
    e, s = detected()
    results = sd.apply(s, {"ops": deep_ops(e)})
    assert [r["status"] for r in results] == DEEP_STATUS
    whys = [r["why"] for r in results]
    assert whys[2] == ("«Salto» (level 4) would be 2 levels below its parent «Ensayo dos "
                       "sobre el choteo» (level 2): a section is at most one level deeper "
                       "than its parent")
    assert whys[3].startswith("«(untitled)» (level 5) would be 3 levels below")
    assert whys[9:] == ["level 0 is not a whole number from 1 up",
                        "level true is not a whole number from 1 up",
                        'level "4" is not a whole number from 1 up']
    check_invariants(s, e.units)
    assert tree(s)[-4:-1] == [(24, 27, 3, "Bibliografía citada", None, "bibliography"),
                              (26, 27, 4, "Cuba", None, "bibliography"),
                              (27, 27, 5, "Madrid", "Otro", "primary")]
    by = {x.title: x for x in s.sections}
    assert by["Madrid"].parent == by["Cuba"].id
    assert by["Cuba"].parent == by["Bibliografía citada"].id
    assert "Lista" not in by and "Salto" not in by
    assert by["Ensayo dos sobre el choteo"].last == 27
    assert s.checks["sections_by_level"][5] == 1


def test_decisions_file_is_validated_and_only_read(tmp_path):
    path = tmp_path / sd.DECISIONS
    assert sd.load(path) == {"schema": 1, "entries": {}}
    path.write_text("{not json", "utf-8")
    with pytest.raises(sd.DecisionsError, match="not valid JSON"):
        sd.load(path)
    path.write_text(json.dumps({"schema": 2, "entries": {}}), "utf-8")
    with pytest.raises(sd.DecisionsError, match="schema-1"):
        sd.load(path)
    path.write_text(json.dumps({"schema": 1, "entries": {"a": {"ops": "x"}}}), "utf-8")
    with pytest.raises(sd.DecisionsError, match="ops"):
        sd.load(path)


def test_cli_structure_applies_the_decisions_file_in_out_by_default(text_dir, tmp_path, capsys):
    out = tmp_path / "structure"
    assert cli.main(["structure", "--text", str(text_dir), "--out", str(out)]) == 0
    assert "decisions: none" in capsys.readouterr().out
    first = json.loads((out / "libro.sections.json").read_text("utf-8"))
    assert first["decisions"] == {"file": None, "record": False, "applied": 0, "stale": 0,
                                  "unmatched": 0, "results": []}
    assert len(first["units_sha256"]) == 64
    e = anthology()
    rec = {"metadata": {"title": "Isla", "confirmed": True}, "ops": [
        {"op": "edit", "seq": 16, "level": 2, "hash": h(e, 16), "author": "Autora Una"},
        {"op": "edit", "seq": 21, "level": 2, "hash": "0" * 10, "author": "X"}]}
    dec = out / sd.DECISIONS
    dec.write_text(json.dumps({"schema": 1, "entries": {"e" * 64: rec, "f" * 64: {}}}), "utf-8")
    mtime = dec.stat().st_mtime_ns
    assert cli.main(["structure", "--text", str(text_dir), "--out", str(out)]) == 0
    outp = capsys.readouterr().out
    assert "decisions: 2 applied, 1 stale, 0 unmatched" in outp
    assert "stale: edit at unit 21" in outp and "1 entry not in the export" in outp
    data = json.loads((out / "libro.sections.json").read_text("utf-8"))
    assert data["decisions"]["record"] is True and data["decisions"]["stale"] == 1
    assert data["metadata"]["title"] == "Isla" and data["metadata"]["confirmed"] is True
    assert any(x["author"] == "Autora Una" and x.get("owner") for x in data["sections"])
    log = json.loads((out / "structure.json").read_text("utf-8"))
    assert log["decisions"]["file"] == str(dec) and log["decisions"]["stale"] == 1
    assert dec.stat().st_mtime_ns == mtime

    other = tmp_path / "elsewhere.json"
    other.write_text(json.dumps({"schema": 1, "entries": {}}), "utf-8")
    assert cli.main(["structure", "--text", str(text_dir), "--out", str(out), "--decisions",
                     str(other)]) == 0
    assert "decisions: none for these entries" in capsys.readouterr().out
    assert cli.main(["structure", "--text", str(text_dir), "--out", str(out), "--decisions",
                     str(tmp_path / "missing.json")]) == 1
    assert "missing.json" in capsys.readouterr().err
