# SPDX-License-Identifier: AGPL-3.0-or-later
"""gpdf-cli handoff: one gpdf-handoff/1 JSON Lines file per exported, non-excluded entry,
named by the sha256 of the working file, and handoff.json; every unit exactly once as a
para of its deepest section, sections tiling the file, ids resolving; entries whose
export or working file changed are skipped; files no longer current are listed, never
deleted; the text, structure and source folders are only read."""

import hashlib
import json
import os

import pytest

from gpdf import cli
from gpdf.pipeline import handoff as ho
from gpdf.pipeline import structure_decisions as sd

from .test_sections import text_dir  # noqa: F401
from .test_structure_decisions import h


def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def snapshot(folder):
    return {p: (sha(p), os.stat(p).st_mtime_ns) for p in folder.rglob("*") if p.is_file()}


def state(path):
    return sha(path), os.stat(path).st_mtime_ns


def structure(text, out, capsys):
    assert cli.main(["structure", "--text", str(text), "--out", str(out)]) == 0
    capsys.readouterr()


def lines(path):
    return [json.loads(s) for s in path.read_text("utf-8").splitlines()]


@pytest.fixture
def made(text_dir, tmp_path, capsys):
    out, to = tmp_path / "structure", tmp_path / "handoff"
    structure(text_dir, out, capsys)
    return text_dir, out, to, sha(tmp_path / "src" / "libro.pdf")


def test_handoff_file_is_named_by_the_working_file_and_its_lines_validate(made, tmp_path, capsys):
    text, out, to, pdf_sha = made
    before = snapshot(text) | snapshot(out) | snapshot(tmp_path / "src")
    assert cli.main(["handoff", "--structure", str(out), "--to", str(to)]) == 0
    stdout = capsys.readouterr().out
    assert snapshot(text) | snapshot(out) | snapshot(tmp_path / "src") == before
    path = to / f"{pdf_sha}{ho.SUFFIX}"
    assert sorted(p.name for p in to.iterdir()) == sorted([path.name, ho.INDEX])
    assert f"libro.pdf -> {path.name}" in stdout
    rows = lines(path)
    assert ho.validate(rows) == []
    f = rows[0]
    assert (f["type"], f["schema"], f["sha256"], f["file_name"], f["pages"]) == (
        "file", "gpdf-handoff/1.1", pdf_sha, "libro.pdf", 22)
    assert f["source_sha256"] == "e" * 64          # the catalogue id: the source's sha256
    assert f["title"] == "Las vanguardias en la isla" and f["metadata_confirmed"] is False
    assert f["structure_confirmed"] is False and f["lang"] == "es"
    assert f["apparatus_pages"] == [2, 19, 20] and f["made_with"].startswith("g-pdf ")
    secs = [r for r in rows if r["type"] == "section"]
    paras = [r for r in rows if r["type"] == "para"]
    assert [r["type"] for r in rows] == ["file"] + ["section"] * len(secs) + ["para"] * 30
    assert set(secs[0]) == {"type", "id", "parent", "level", "title", "author", "role", "first",
                            "last", "pdf_pages", "printed", "lang", "confidence"}
    assert [p["seq"] for p in paras] == list(range(1, 31))
    by = {s["id"]: s for s in secs}
    uno = next(s for s in secs if s["title"] == "Ensayo uno sobre la vanguardia")
    p17 = paras[16]
    assert (p17["section"], p17["kind"], p17["text"]) == (uno["id"], "heading",
                                                          "Ensayo uno sobre la vanguardia")
    assert (p17["pdf_pages"], p17["printed"], p17["printed_inferred"]) == ([12, 12],
                                                                         ["12", "12"], False)
    assert p17["note"] is None and p17["flags"] == [] and p17["lang"] == "es"
    assert by[paras[25]["section"]]["title"] == "Bibliografía"      # the deepest section
    assert by[paras[25]["section"]]["level"] == 3
    idx = json.loads((to / ho.INDEX).read_text("utf-8"))
    rec = idx["files"][pdf_sha]
    assert idx["format"] == "gpdf-handoff/1.1" and rec["file_name"] == "libro.pdf"
    assert rec["source_sha256"] == "e" * 64
    assert rec["file"] == path.name and rec["title"] == "Las vanguardias en la isla"
    assert rec["counts"]["paras"] == 30 and rec["counts"]["sections"] == len(secs)
    assert rec["structure_confirmed"] is False and rec["made"]
    assert idx["not_current"] == [] and idx["skipped"] == []
    assert not list(to.glob(".gpdf-*.tmp"))


def test_validate_finds_broken_tiling_unresolved_ids_and_bad_ranges(made, capsys):
    text, out, to, pdf_sha = made
    assert cli.main(["handoff", "--structure", str(out), "--to", str(to)]) == 0
    rows = lines(to / f"{pdf_sha}{ho.SUFFIX}")
    paras = [r for r in rows if r["type"] == "para"]
    secs = [r for r in rows if r["type"] == "section"]
    paras[3]["section"] = "s999"
    paras[4]["pdf_pages"] = [4, 3]
    secs[1]["parent"] = "nope"
    secs[5]["first"] = 13
    problems = ho.validate(rows[:-1] + [rows[-2]])
    joined = "\n".join(problems)
    for want in ("para 4", "s999", "para 5", "pages", "nope", "not the deepest", "seq 30",
                 "more than once"):
        assert want in joined, want


def test_decisions_confirm_the_structure_and_the_metadata(made, capsys):
    text, out, to, pdf_sha = made
    from .test_sections import anthology

    e = anthology()
    dec = {"schema": 1, "entries": {"e" * 64: {
        "metadata": {"title": "Isla", "author": "Editor Prueba", "year": "1999",
                     "publisher": "Editorial Prueba", "confirmed": True},
        "ops": [{"op": "edit", "seq": 16, "level": 2, "hash": h(e, 16), "role": "primary"}]}}}
    (out / sd.DECISIONS).write_text(json.dumps(dec), "utf-8")
    structure(text, out, capsys)
    assert cli.main(["handoff", "--structure", str(out), "--to", str(to)]) == 0
    assert "structure not confirmed ('Structure reviewed' not ticked)" in capsys.readouterr().out
    f = lines(to / f"{pdf_sha}{ho.SUFFIX}")[0]
    assert f["metadata_confirmed"] is True and f["structure_confirmed"] is False
    assert json.loads((to / ho.INDEX).read_text("utf-8"))["files"][pdf_sha][
        "structure_confirmed"] is False

    dec["entries"]["e" * 64]["reviewed"] = True                      # the owner ticks it
    (out / sd.DECISIONS).write_text(json.dumps(dec), "utf-8")
    structure(text, out, capsys)
    assert cli.main(["handoff", "--structure", str(out), "--to", str(to)]) == 0
    f, *rest = lines(to / f"{pdf_sha}{ho.SUFFIX}")
    assert (f["title"], f["author"], f["year"], f["publisher"]) == (
        "Isla", "Editor Prueba", "1999", "Editorial Prueba")
    assert f["metadata_confirmed"] is True and f["structure_confirmed"] is True
    assert any(r["type"] == "section" and r["role"] == "primary" for r in rest)

    dec["entries"]["e" * 64]["ops"][0]["hash"] = "0" * 10            # now stale
    (out / sd.DECISIONS).write_text(json.dumps(dec), "utf-8")
    structure(text, out, capsys)
    assert cli.main(["handoff", "--structure", str(out), "--to", str(to)]) == 0
    assert "structure not confirmed (1 stale or unmatched decision)" in capsys.readouterr().out
    f = lines(to / f"{pdf_sha}{ho.SUFFIX}")[0]
    assert f["structure_confirmed"] is False and f["metadata_confirmed"] is True


def test_stale_files_are_listed_never_deleted(made, capsys):
    text, out, to, pdf_sha = made
    to.mkdir()
    old = to / f"{'a' * 64}{ho.SUFFIX}"
    old.write_text('{"type": "file"}\n', "utf-8")
    other = to / "notes.txt"
    other.write_text("mine", "utf-8")
    assert cli.main(["handoff", "--structure", str(out), "--to", str(to)]) == 0
    stdout = capsys.readouterr().out
    assert old.exists() and other.read_text("utf-8") == "mine"
    assert "no longer current (remove them yourself)" in stdout and old.name in stdout
    idx = json.loads((to / ho.INDEX).read_text("utf-8"))
    assert idx["not_current"] == [old.name] and list(idx["files"]) == [pdf_sha]
    again = to / f"{pdf_sha}{ho.SUFFIX}"
    first = again.read_text("utf-8")
    assert cli.main(["handoff", "--structure", str(out), "--to", str(to)]) == 0
    assert again.read_text("utf-8").splitlines()[1:] == first.splitlines()[1:]


def test_changed_exports_working_files_and_excluded_entries_are_skipped(made, tmp_path, capsys):
    text, out, to, pdf_sha = made
    units = text / "libro.jsonl"
    saved = units.read_text("utf-8")
    units.write_text(saved.replace("Segundo párrafo.", "Segundo párrafo corregido.", 1), "utf-8")
    assert cli.main(["handoff", "--structure", str(out), "--to", str(to)]) == 2
    stdout = capsys.readouterr().out
    assert "skipped libro: the export changed since structure ran" in stdout
    assert not (to / f"{pdf_sha}{ho.SUFFIX}").exists()
    assert json.loads((to / ho.INDEX).read_text("utf-8"))["skipped"][0]["stem"] == "libro"
    units.write_text(saved, "utf-8")

    pdf = tmp_path / "src" / "libro.pdf"
    data = pdf.read_bytes()
    pdf.write_bytes(data + b"\n%changed\n")
    assert cli.main(["handoff", "--structure", str(out), "--to", str(to)]) == 2
    assert "the working file changed since the export" in capsys.readouterr().out
    pdf.write_bytes(data)

    cat = tmp_path / "catalogue"
    cat.mkdir()
    (cat / "catalogue.decisions.json").write_text(json.dumps({"schema": 1, "files": {
        "e" * 64: {"action": "exclude", "confirmed": True}}}), "utf-8")
    assert cli.main(["handoff", "--structure", str(out), "--to", str(to), "--catalogue",
                     str(cat)]) == 0
    assert "excluded in the catalogue: libro" in capsys.readouterr().out
    assert not (to / f"{pdf_sha}{ho.SUFFIX}").exists()


def test_txt_entry_uses_lines(tmp_path, capsys):
    src = tmp_path / "src" / "poema.txt"
    src.parent.mkdir()
    src.write_text("A Roosevelt\n\n¡Es con voz de Biblia!\n", "utf-8")
    text = tmp_path / "text"
    text.mkdir()
    units = [{"entry": "t" * 64, "seq": 1, "kind": "heading", "text": "A Roosevelt",
              "lines": [1, 1], "labels": [None, None], "label_inferred": False, "note": None,
              "lang": "es", "flags": []},
             {"entry": "t" * 64, "seq": 2, "kind": "verse", "text": "¡Es con voz\nde Biblia!",
              "lines": [3, 4], "labels": [None, None], "label_inferred": False, "note": None,
              "lang": "es", "flags": []}]
    (text / "poema.jsonl").write_text("".join(json.dumps(u) + "\n" for u in units), "utf-8")
    (text / "poema.pages.jsonl").write_text(json.dumps(
        {"entry": "t" * 64, "lines": [1, 4], "kind": "body", "removed": {}, "dropped": []})
        + "\n", "utf-8")
    (text / "export.json").write_text(json.dumps({"schema": 1, "entries": {"t" * 64: {
        "rel": "poema.txt", "input": str(src), "input_sha256": sha(src), "stem": "poema",
        "flags": {}, "counts": {}}}}), "utf-8")
    out, to = tmp_path / "structure", tmp_path / "handoff"
    structure(text, out, capsys)
    assert cli.main(["handoff", "--structure", str(out), "--to", str(to)]) == 0
    rows = lines(to / f"{sha(src)}{ho.SUFFIX}")
    assert ho.validate(rows) == []
    f, s, p1, p2 = rows
    assert f["pages"] is None and f["apparatus_pages"] == [] and f["file_name"] == "poema.txt"
    assert s["lines"] == [1, 4] and "pdf_pages" not in s and "printed" not in s
    assert p2["lines"] == [3, 4] and p2["text"] == "¡Es con voz\nde Biblia!"
    assert "pdf_pages" not in p2 and "printed" not in p2 and "printed_inferred" not in p2


def test_validate_accepts_major_1_and_refuses_another_major(made, capsys):
    text, out, to, pdf_sha = made
    assert cli.main(["handoff", "--structure", str(out), "--to", str(to)]) == 0
    rows = lines(to / f"{pdf_sha}{ho.SUFFIX}")
    for schema, ok in (("gpdf-handoff/1", True), ("gpdf-handoff/1.1", True),
                       ("gpdf-handoff/1.7", True), ("gpdf-handoff/2", False),
                       ("gpdf-handoff/2.1", False), ("gpdf-handoff/10", False),
                       ("gpdf-handoff/1.x", False)):
        rows[0]["schema"] = schema
        assert (ho.validate(rows) == []) is ok, schema


def test_two_entries_with_one_source_refuse_the_run(made, monkeypatch, capsys):
    text, out, to, pdf_sha = made
    log = json.loads((text / "export.json").read_text("utf-8"))
    log["entries"]["f" * 64] = dict(log["entries"]["e" * 64])
    (text / "export.json").write_text(json.dumps(log), "utf-8")
    monkeypatch.setattr(ho, "source_sha256", lambda id_, rec: "c" * 64)
    assert cli.main(["handoff", "--structure", str(out), "--to", str(to)]) == 1
    err = capsys.readouterr().err
    assert "share the source" in err and "c" * 64 in err
    assert not to.exists()


def add_poem(text, tmp_path):
    """A second entry in the export folder: a one-line .txt."""
    src = tmp_path / "src" / "poema.txt"
    src.write_text("A Roosevelt\n", "utf-8")
    unit = {"entry": "t" * 64, "seq": 1, "kind": "heading", "text": "A Roosevelt",
            "lines": [1, 1], "labels": [None, None], "label_inferred": False, "note": None,
            "lang": "es", "flags": []}
    (text / "poema.jsonl").write_text(json.dumps(unit) + "\n", "utf-8")
    (text / "poema.pages.jsonl").write_text(json.dumps(
        {"entry": "t" * 64, "lines": [1, 1], "kind": "body", "removed": {}, "dropped": []})
        + "\n", "utf-8")
    log = json.loads((text / "export.json").read_text("utf-8"))
    log["entries"]["t" * 64] = {"rel": "poema.txt", "input": str(src),
                                "input_sha256": sha(src), "stem": "poema", "flags": {},
                                "counts": {}}
    (text / "export.json").write_text(json.dumps(log), "utf-8")
    return src


def test_a_file_and_its_made_change_only_when_its_content_changes(made, tmp_path, monkeypatch,
                                                                    capsys):
    text, out, to, pdf_sha = made
    poem = to / f"{sha(add_poem(text, tmp_path))}{ho.SUFFIX}"
    libro = to / f"{pdf_sha}{ho.SUFFIX}"
    structure(text, out, capsys)

    def handoff(when):
        monkeypatch.setattr(ho.catalogue, "now", lambda: when)
        assert cli.main(["handoff", "--structure", str(out), "--to", str(to)]) == 0
        return capsys.readouterr().out, json.loads((to / ho.INDEX).read_text("utf-8"))

    stdout, idx = handoff("2026-11-01T10:00:00-06:00")
    assert f"libro.pdf -> {libro.name} (written)" in stdout
    assert f"poema.txt -> {poem.name} (written)" in stdout
    files = {libro: state(libro), poem: state(poem)}

    stdout, idx = handoff("2026-11-01T11:00:00-06:00")                 # nothing changed
    assert f"libro.pdf -> {libro.name} (unchanged)" in stdout
    assert f"poema.txt -> {poem.name} (unchanged)" in stdout
    assert "wrote 0 handoff file(s) (2 unchanged)" in stdout
    assert {p: state(p) for p in files} == files
    assert lines(libro)[0]["made"] == lines(poem)[0]["made"] == "2026-11-01T10:00:00-06:00"
    assert idx["made"] == "2026-11-01T11:00:00-06:00"
    assert {r["made"] for r in idx["files"].values()} == {"2026-11-01T10:00:00-06:00"}

    (out / sd.DECISIONS).write_text(json.dumps({"schema": 1, "entries": {"e" * 64: {
        "metadata": {"title": "Isla", "confirmed": True}, "ops": []}}}), "utf-8")
    structure(text, out, capsys)
    stdout, idx = handoff("2026-11-01T12:00:00-06:00")                 # libro's metadata
    assert f"libro.pdf -> {libro.name} (written)" in stdout
    assert f"poema.txt -> {poem.name} (unchanged)" in stdout
    assert "wrote 1 handoff file(s) (1 unchanged)" in stdout
    assert state(poem) == files[poem] and state(libro) != files[libro]
    f = lines(libro)[0]
    assert (f["title"], f["made"]) == ("Isla", "2026-11-01T12:00:00-06:00")
    assert idx["files"][pdf_sha]["made"] == "2026-11-01T12:00:00-06:00"
    assert idx["files"][poem.name[:64]]["made"] == "2026-11-01T10:00:00-06:00"
    assert not list(to.glob(".gpdf-*.tmp"))


def test_handoff_refuses_a_structure_folder_without_a_run_log(tmp_path, capsys):
    assert cli.main(["handoff", "--structure", str(tmp_path), "--to", str(tmp_path / "h")]) == 1
    assert "structure.json" in capsys.readouterr().err
    assert not (tmp_path / "h").exists()


def test_the_format_docs_synthetic_example_validates():
    import re
    from pathlib import Path

    doc = (Path(__file__).parents[2] / "docs" / "handoff-format.md").read_text("utf-8")
    block = re.search(r"## Example \(synthetic\).*?```json\n(.*?)```", doc, re.S).group(1)
    rows = [json.loads(s) for s in block.splitlines() if s.strip()]
    assert len(rows) > 5 and ho.validate(rows) == []


def with_catalogue(made, tmp_path):
    """A catalogue folder surveyed from src/, whose entry records libro.pdf as the working
    file (the size lets a moved file be found by its hash)."""
    text, out, to, pdf_sha = made
    pdf = tmp_path / "src" / "libro.pdf"
    cat = tmp_path / "catalogue"
    cat.mkdir()
    (cat / "catalogue.json").write_text(json.dumps({
        "schema": 1, "roots": [{"path": str(tmp_path / "src")}], "files": {"e" * 64: {
            "id": "e" * 64, "path": str(tmp_path / "src" / "orig.pdf"), "size": 1,
            "working_file": {"path": str(pdf), "sha256": pdf_sha,
                             "size": pdf.stat().st_size}}}}), "utf-8")
    return pdf, ["handoff", "--structure", str(out), "--to", str(to), "--catalogue", str(cat)]


def test_a_renamed_working_file_is_found_by_its_hash(made, tmp_path, monkeypatch, capsys):
    text, out, to, pdf_sha = made
    pdf, argv = with_catalogue(made, tmp_path)
    new = tmp_path / "src" / "otra carpeta" / "Libro renombrado.pdf"
    new.parent.mkdir()
    os.replace(pdf, new)
    (tmp_path / "src" / "otro.pdf").write_bytes(b"%PDF-1.7 not the same size\n")
    before = snapshot(tmp_path / "src")
    hashed = []
    real = ho.catalogue.sha256
    monkeypatch.setattr(ho.catalogue, "sha256", lambda p: hashed.append(p) or real(p))
    assert cli.main(argv) == 0
    stdout = capsys.readouterr().out
    assert snapshot(tmp_path / "src") == before
    assert hashed == [new]                         # only the file of the right size
    assert f"moved: {pdf} -> {new}" in stdout
    path = to / f"{pdf_sha}{ho.SUFFIX}"
    assert f"Libro renombrado.pdf -> {path.name} (written)" in stdout
    f = lines(path)[0]
    assert (f["sha256"], f["source_sha256"], f["file_name"], f["pages"]) == (
        pdf_sha, "e" * 64, "Libro renombrado.pdf", 22)
    idx = json.loads((to / ho.INDEX).read_text("utf-8"))
    assert idx["files"][pdf_sha]["file_name"] == "Libro renombrado.pdf"
    assert idx["skipped"] == []
    assert json.loads((text / "export.json").read_text("utf-8"))["entries"]["e" * 64][
        "input"] == str(pdf)                       # the record is not rewritten


def test_a_deleted_working_file_is_skipped(made, tmp_path, capsys):
    pdf, argv = with_catalogue(made, tmp_path)
    pdf.unlink()
    assert cli.main(argv) == 2
    assert f"skipped libro: the working file {pdf} is missing" in capsys.readouterr().out


def test_an_edited_file_at_the_recorded_path_is_changed_not_moved(made, tmp_path, capsys):
    pdf, argv = with_catalogue(made, tmp_path)
    data = pdf.read_bytes()
    (tmp_path / "src" / "copia.pdf").write_bytes(data)
    pdf.write_bytes(data[:-1] + b" ")              # same size, other bytes
    assert cli.main(argv) == 2
    stdout = capsys.readouterr().out
    assert "skipped libro: the working file changed since the export" in stdout
    assert "moved:" not in stdout


def test_identical_copies_use_the_first_by_sorted_path(made, tmp_path, capsys):
    text, out, to, pdf_sha = made
    pdf, argv = with_catalogue(made, tmp_path)
    b, a = tmp_path / "src" / "b.pdf", tmp_path / "src" / "A.pdf"
    os.replace(pdf, b)
    a.write_bytes(b.read_bytes())
    before = snapshot(tmp_path / "src")
    assert cli.main(argv) == 0
    stdout = capsys.readouterr().out
    assert snapshot(tmp_path / "src") == before
    assert f"moved: {pdf} -> {a}" in stdout
    assert f"same bytes also at: {b}" in stdout
    assert lines(to / f"{pdf_sha}{ho.SUFFIX}")[0]["file_name"] == "A.pdf"
