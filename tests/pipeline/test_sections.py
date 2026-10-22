# SPDX-License-Identifier: AGPL-3.0-or-later
"""gpdf-cli structure, the assembly: parts, sections and subsections tiling the units,
roles, authors, confidence and evidence, the sections files and run log, and that the
text folder and the working files are only read."""

import hashlib
import json
import os

import pymupdf
import pytest

from gpdf import cli
from gpdf.pipeline import sections as sc
from gpdf.pipeline import structure as st

from .test_structure import page_rows, unit


def rows_for(n, heads=None, labels=None, kinds=None):
    rows = page_rows(labels or [str(i + 1) for i in range(n)])
    for r in rows:
        r["head"] = (heads or {}).get(r["page"])
        r["kind"] = (kinds or {}).get(r["page"], "body")
        if r["label"] is None:
            r["label_status"] = "none"
    return rows


def entry(units, rows, apparatus=(), toc=(), kind="pdf", meta=None, stem="libro", id_="e" * 64):
    for k, u in enumerate(units, 1):
        u["seq"] = k
        u["entry"] = id_
    return sc.Entry(id=id_, stem=stem, input=f"{stem}.pdf", input_sha="f" * 64, kind=kind,
                    units=units, rows=rows, apparatus=list(apparatus), toc=list(toc),
                    page_count=len(rows), metadata=meta or {}, lang="es")


def leaves(struct):
    """(seq, section id) for every unit: the innermost section."""
    out = {}
    for s in struct.sections:
        for seq in range(s.first, s.last + 1):
            if seq not in out or struct.by_id[out[seq]].level < s.level:
                out[seq] = s.id
    return out


def check_invariants(struct, units):
    ids = [s.id for s in struct.sections]
    assert ids == sorted(ids) and len(set(ids)) == len(ids)
    firsts = [s.first for s in struct.sections]
    assert firsts == sorted(firsts)
    assert all(s.first <= s.last for s in struct.sections)
    for s in struct.sections:
        if s.parent:
            p = struct.by_id[s.parent]
            assert p.level < s.level and p.first <= s.first and s.last <= p.last
    assert struct.sections[0].first == 1 and struct.sections[0].level == 1
    assert max(s.last for s in struct.sections) == len(units)
    assert sorted(leaves(struct)) == list(range(1, len(units) + 1))


# ---------------------------------------------------------------------------- anthology
def anthology():
    """A small critical anthology: title pages, Cinco prefacios, Bibliografía with two
    country blocks (heads), Antología crítica with two essays (contents page, bookmarks
    28 pages late, heads title/author), a Bibliografía heading inside essay two, index
    pages without units, a series list at the back."""
    heads = {}
    for p in range(5, 11):
        heads[p] = "Bibliografía" if p % 2 == 0 else ("Cuba" if p < 9 else "Puerto Rico")
    for p in range(12, 15):
        heads[p] = "Ensayo uno sobre la vanguardia" if p % 2 == 0 else "Autor Uno"
    for p in range(15, 19):
        heads[p] = "Ensayo dos sobre el choteo" if p % 2 else "Autor Dos"
    rows = rows_for(22, heads, kinds={2: "contents", 19: "index", 20: "index"})
    units = [unit(0, 1, "Las vanguardias en la isla", "heading"), unit(0, 1, "Editorial Prueba"),
             unit(0, 1, "© 1999 Editorial Prueba, Madrid"),
             unit(0, 3, "Cinco prefacios", "heading"), unit(0, 3, "La vanguardia en telegrama",
                                                         "heading"),
             unit(0, 3, "Texto del prefacio."), unit(0, 4, "Más prefacio."),
             unit(0, 5, "Cuba", "heading"), unit(0, 5, "Visiones de conjunto", "heading"),
             unit(0, 5, "1 AUTOR, Nombre. Libro. La Habana, 1930."),
             unit(0, 7, "2 OTRO, Nombre. Libro. La Habana, 1931."),
             unit(0, 9, "Puerto Rico", "heading"), unit(0, 9, "3 TAL, Cual. Libro. San Juan, 1932."),
             unit(0, 10, "4 TAL, Cual. Otro libro. San Juan, 1933."),
             unit(0, 11, "ANTOLOGÍA CRÍTICA"),
             unit(0, 12, "Autor Uno Universidad de Prueba"),
             unit(0, 12, "Ensayo uno sobre la vanguardia", "heading"),
             unit(0, 12, "Primer párrafo del ensayo uno."), unit(0, 13, "Segundo párrafo."),
             unit(0, 14, "Tercer párrafo del ensayo uno."),
             unit(0, 15, "Ensayo dos sobre el choteo", "heading"), unit(0, 15, "Autor Dos"),
             unit(0, 15, "Primer párrafo del ensayo dos."), unit(0, 16, "Segundo párrafo."),
             unit(0, 17, "Bibliografía", "heading"),
             unit(0, 17, "AUTOR, Nombre. Libro citado. Madrid, 1990."),
             unit(0, 18, "OTRO, Nombre. Libro citado. Madrid, 1991."),
             unit(0, 21, "OTROS TÍTULOS SOBRE VANGUARDIA", "heading"),
             unit(0, 21, "Alguien: Un libro. 2005, 200 p."), unit(0, 22, "Otro: Otro libro.")]
    apparatus = [{"entry": "e" * 64, "page": 2, "label": None, "kind": "contents", "columns": 1,
                  "lines": ["Índice", "Cinco prefacios", "La vanguardia en telegrama 3",
                            "Bibliografía", "Cuba 5", "Puerto Rico 9", "Antología crítica",
                            "Ensayo uno sobre la vanguardia", "Autor Uno 12",
                            "Ensayo dos sobre el choteo", "Autor Dos 15",
                            "Índice onomástico de la bibliografía 19"]},
                 {"entry": "e" * 64, "page": 19, "label": "19", "kind": "index", "columns": 2,
                  "lines": ["Autor, Nombre, 5, 7", "Tal, Cual, 9, 10"]}]
    toc = [[1, "Índice", 2], [1, "CINCO PREFACIOS", 3], [1, "BIBLIOGRAFÍA", 5], [1, "Cuba", 5],
           [1, "Puerto Rico", 9], [1, "ANTOLOGÍA CRÍTICA", 1],
           [1, "Ensayo uno sobre la vanguardia", 40], [1, "Ensayo dos sobre el choteo", 43],
           [1, "ÍNDICE ONOMÁSTICO", 1]]
    return entry(units, rows, apparatus, toc, meta={"title": "", "author": ""})


def test_anthology_parts_sections_roles_and_confidence():
    e = anthology()
    s = sc.detect(e)
    check_invariants(s, e.units)
    parts = [(p.title, p.role) for p in s.sections if p.level == 1]
    assert parts == [("Front matter", "front"), ("Bibliografía", "bibliography"),
                     ("Antología crítica", "criticism"),
                     ("OTROS TÍTULOS SOBRE VANGUARDIA", "back")]
    by_title = {x.title: x for x in s.sections}
    cinco = by_title["Cinco prefacios"]
    assert cinco.level == 2 and cinco.role == "editorial" and cinco.first == 4
    assert by_title["Cuba"].level == 2 and by_title["Cuba"].role == "bibliography"
    assert by_title["Puerto Rico"].first == 12 and by_title["Puerto Rico"].last == 14
    uno = by_title["Ensayo uno sobre la vanguardia"]
    assert (uno.level, uno.role, uno.author, uno.first) == (2, "criticism", "Autor Uno", 16)
    assert uno.pdf_pages == [12, 14] and uno.printed == ["12", "14"]
    assert uno.confidence == "high"
    assert any("re-anchored to p12" in ev for ev in uno.evidence)
    dos = by_title["Ensayo dos sobre el choteo"]
    assert dos.author == "Autor Dos" and dos.first == 21 and dos.last == 27
    subs = [x for x in s.sections if x.level == 3]
    assert [(x.title, x.role, x.parent, x.first, x.last) for x in subs] == [
        ("La vanguardia en telegrama", "editorial", cinco.id, 5, 7),
        ("Bibliografía", "bibliography", dos.id, 25, 27)]
    assert s.outline.junk is False
    assert {c.title for c, _ in s.outline.dropped} == {"Índice", "ÍNDICE ONOMÁSTICO"}
    assert [(old, c.page) for c, old in s.outline.reanchored] == [(1, 11), (40, 12), (43, 15)]
    assert s.contents["parsed"] == 6 and s.contents["matched"] >= 5
    assert "index pages 19-20 carry no units" in " ".join(s.notes)
    assert s.metadata["year"] == "1999" and s.metadata["publisher"] == "Editorial Prueba"
    assert s.metadata["title"] == "Las vanguardias en la isla" and not s.metadata["confirmed"]


# ---------------------------------------------------------------------------- texts
def test_text_anthology_texts_primary_prologue_criticism_series_list_back():
    units = [unit(0, 1, "MANIFIESTOS DE PRUEBA", "heading"), unit(0, 2, "PROLOGO", "heading"),
             unit(0, 2, "Este libro reúne los manifiestos."), unit(0, 3, "Sigue el prólogo."),
             unit(0, 4, "CRITERIO DE ESTA EDICION", "heading"), unit(0, 4, "Se respetan las grafías."),
             unit(0, 5, "TITULO UNO", "heading"), unit(0, 5, "Autor Alfa"),
             unit(0, 5, "Texto del primer manifiesto."), unit(0, 6, "Sigue el manifiesto."),
             unit(0, 7, "TITULO DOS", "heading"), unit(0, 7, "Autor Beta"),
             unit(0, 7, "Texto del segundo manifiesto."),
             unit(0, 8, "BIBLIOGRAFIA", "heading"), unit(0, 8, "Libro, 1920."),
             unit(0, 10, "TITULOS PUBLICADOS", "heading"), unit(0, 10, "1 Otro libro de la serie.")]
    rows = rows_for(10, kinds={9: "index"}, labels=[None, "IX", "X", "XI", "1", "2", "3", "4",
                                                   None, None])
    apparatus = [{"entry": "e" * 64, "page": 9, "label": None, "kind": "index", "columns": 1,
                  "lines": ["INDICE", "PROLOGO", "Nelson Prueba … IX",
                            "CRITERIO DE ESTA EDICION … XI", "TITULO UNO", "Autor Alfa 1",
                            "TITULO DOS", "Autor Beta 3", "BIBLIOGRAFIA 4"]}]
    e = entry(units, rows, apparatus, [], stem="manifiestos",
              meta={"title": "aaa", "author": ""})
    s = sc.detect(e)
    check_invariants(s, e.units)
    by_title = {x.title: x for x in s.sections}
    assert by_title["PROLOGO"].role == "criticism" and by_title["PROLOGO"].author == "Nelson Prueba"
    assert by_title["CRITERIO DE ESTA EDICION"].role == "editorial"
    uno = by_title["TITULO UNO"]
    assert (uno.role, uno.author, uno.first, uno.last, uno.level) == ("primary", "Autor Alfa", 7, 10, 2)
    assert by_title["TITULO DOS"].author == "Autor Beta" and by_title["TITULO DOS"].role == "primary"
    assert by_title["BIBLIOGRAFIA"].role == "bibliography" and by_title["BIBLIOGRAFIA"].level == 1
    assert by_title["TITULOS PUBLICADOS"].role == "back"
    assert s.kind == "texts" and s.outline.junk is False and s.contents["parsed"] == 5
    assert s.metadata["title"] == "MANIFIESTOS DE PRUEBA"      # "aaa" is not a title


# ---------------------------------------------------------------------------- drama, txt
def test_drama_acts_and_cuadros_with_an_inferred_first_act():
    units = [unit(0, 1, "Trescientos millones", "heading"), unit(0, 1, "ROBERTO ARLT"),
             unit(0, 2, "Escena primera", "heading"), unit(0, 2, "SIRVIENTA.— Habla."),
             unit(0, 3, "CUADRO SEGUNDO", "heading"), unit(0, 3, "Escena primera", "heading"),
             unit(0, 3, "ROCAMBOLE.— Habla."), unit(0, 4, "ACTO II", "heading"),
             unit(0, 4, "Escena primera", "heading"), unit(0, 4, "SIRVIENTA.— Sigue.")]
    e = entry(units, rows_for(4, labels=[None] * 4), [], [], stem="drama")
    s = sc.detect(e)
    check_invariants(s, e.units)
    acts = [(x.title, x.level, x.first, x.confidence) for x in s.sections if x.level == 2]
    assert acts == [("Acto I", 2, 3, "low"), ("ACTO II", 2, 8, "medium")]
    cuadros = [(x.title, x.level, x.first) for x in s.sections if x.level == 3]
    assert cuadros == [("CUADRO SEGUNDO", 3, 5)]
    assert all(x.role == "primary" for x in s.sections if x.title != "Front matter")
    assert not any("Escena" in x.title for x in s.sections)


def test_txt_entry_is_one_part_with_one_section():
    units = [{"entry": "e" * 64, "seq": 1, "kind": "heading", "text": "A Roosevelt",
              "lines": [1, 1], "labels": [None, None], "label_inferred": False, "note": None,
              "lang": "es", "flags": []},
             {"entry": "e" * 64, "seq": 2, "kind": "verse", "text": "¡Es con voz de Biblia!",
              "lines": [12, 19], "labels": [None, None], "label_inferred": False, "note": None,
              "lang": "es", "flags": []}]
    rows = [{"entry": "e" * 64, "lines": [1, 30], "kind": "body", "removed": {}, "dropped": []}]
    e = sc.Entry(id="e" * 64, stem="A Roosevelt", input="A Roosevelt.txt", input_sha="f" * 64,
                 kind="txt", units=units, rows=rows, apparatus=[], toc=[], page_count=0,
                 metadata={}, lang="es")
    s = sc.detect(e)
    assert [(x.level, x.title, x.role, x.first, x.last) for x in s.sections] == [
        (1, "A Roosevelt", "primary", 1, 2)]
    assert s.sections[0].lines == [1, 19] and s.sections[0].pdf_pages is None


# ---------------------------------------------------------------------------- files, cli
def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


@pytest.fixture
def text_dir(tmp_path):
    """An export folder with the anthology entry and a working PDF that carries its outline."""
    e = anthology()
    pdf = tmp_path / "src" / "libro.pdf"
    pdf.parent.mkdir()
    doc = pymupdf.open()
    for k in range(22):
        page = doc.new_page(width=300, height=400)
        page.insert_text((40, 60), f"page {k + 1}", fontsize=12)
    doc.set_toc([[lv, t, min(pg, 22)] for lv, t, pg in e.toc])
    doc.set_metadata({"title": "Las vanguardias en la isla", "author": "Editor Prueba"})
    doc.save(str(pdf))
    doc.close()
    text = tmp_path / "text"
    text.mkdir()
    for name, rows in (("libro.jsonl", e.units), ("libro.pages.jsonl", e.rows),
                       ("libro.apparatus.jsonl", e.apparatus)):
        (text / name).write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                                 "utf-8")
    (text / "libro.txt").write_text("rendering\n", "utf-8")
    (text / "export.json").write_text(json.dumps({
        "schema": 1, "tool_version": "0.2.0", "entries": {
            e.id: {"rel": "libro.pdf", "input": str(pdf), "input_sha256": sha(pdf),
                   "input_from": "source", "stem": "libro",
                   "flags": {"no_dictionary_fixes": False, "pages": None, "force": True},
                   "counts": {"pages": 22}, "time": "2026-10-22T10:00:00+02:00"}}}), "utf-8")
    return text


def snapshot(folder):
    return {p: (sha(p), os.stat(p).st_mtime_ns) for p in folder.rglob("*") if p.is_file()}


def test_cli_structure_writes_sections_files_and_run_log_only(text_dir, tmp_path, capsys):
    out = tmp_path / "structure"
    before = snapshot(text_dir) | snapshot(tmp_path / "src")
    assert cli.main(["structure", "--text", str(text_dir), "--out", str(out)]) == 0
    assert sorted(p.name for p in out.iterdir()) == ["libro.sections.json", "structure.json"]
    assert snapshot(text_dir) | snapshot(tmp_path / "src") == before
    data = json.loads((out / "libro.sections.json").read_text("utf-8"))
    assert data["schema"] == 1 and data["entry"] == "e" * 64 and data["stem"] == "libro"
    assert data["input_sha256"] == sha(tmp_path / "src" / "libro.pdf")
    assert data["metadata"]["title"] == "Las vanguardias en la isla"
    assert data["metadata"]["author"] == "Editor Prueba" and data["metadata"]["confirmed"] is False
    secs = data["sections"]
    assert secs[0]["id"] == "s001" and secs[0]["level"] == 1 and secs[0]["parent"] is None
    assert all(k in secs[0] for k in ("title", "author", "role", "first", "last", "pdf_pages",
                                      "printed", "lang", "confidence", "evidence", "sources"))
    assert data["outline"]["reanchored"] and data["contents"]["parsed"] == 6
    log = json.loads((out / "structure.json").read_text("utf-8"))
    assert log["schema"] == 1 and log["tool_version"] and log["entries"]["e" * 64]["sections"] > 5
    assert log["totals"]["chars_by_role"]["criticism"] > 0
    outp = capsys.readouterr().out
    assert "libro" in outp and "essays" not in outp and "confidence" in outp
    assert "every unit in exactly one section" in outp

    # a second run replaces the files; a changed export is not a problem for the detector
    assert cli.main(["structure", "--text", str(text_dir), "--out", str(out)]) == 0


def test_cli_structure_refuses_a_missing_export_and_an_out_inside_text(text_dir, tmp_path, capsys):
    assert cli.main(["structure", "--text", str(tmp_path / "nowhere"), "--out",
                     str(tmp_path / "o")]) == 1
    assert "export.json" in capsys.readouterr().err
    assert cli.main(["structure", "--text", str(text_dir), "--out", str(text_dir / "s")]) == 1
    assert "inside" in capsys.readouterr().err
    assert not (text_dir / "s").exists()


def test_contents_lines_kept_in_the_text_are_a_contents_page_not_headings():
    units = [unit(0, 1, "Las vanguardias en el norte", "heading"), unit(0, 1, "Nombre Compilador"),
             unit(0, 2, "Antología crítica", "heading"), unit(0, 2, "Primer ensayo del tomo"),
             unit(0, 2, "Autora Alfa. 5"), unit(0, 2, "Segundo ensayo del tomo"),
             unit(0, 2, "Autor Beta. 7"), unit(0, 2, "Tercer ensayo del tomo"),
             unit(0, 2, "Autora Gamma. 9"), unit(0, 3, "Dedicatoria a los lectores."),
             unit(0, 5, "Primer ensayo del tomo", "heading"), unit(0, 5, "Autora Alfa"),
             unit(0, 5, "Texto."), unit(0, 7, "Segundo ensayo del tomo", "heading"),
             unit(0, 7, "Autor Beta"), unit(0, 7, "Texto."),
             unit(0, 9, "Tercer ensayo del tomo", "heading"), unit(0, 9, "Autora Gamma"),
             unit(0, 9, "Texto."), unit(0, 10, "Más texto.")]
    rows = rows_for(10)
    e = entry(units, rows, [], [], stem="norte", meta={"title": "Las vanguardias en el norte"})
    s = sc.detect(e)
    check_invariants(s, e.units)
    assert sc.inbody_contents_pages(st.units_on(units), 10) == {2}
    assert s.contents["in_body_pages"] == [2] and s.contents["parsed"] == 3
    essays = [(x.title, x.author, x.pdf_pages[0]) for x in s.sections if x.level == 2]
    assert essays == [("Primer ensayo del tomo", "Autora Alfa", 5),
                      ("Segundo ensayo del tomo", "Autor Beta", 7),
                      ("Tercer ensayo del tomo", "Autora Gamma", 9)]
    assert all(x.role == "criticism" for x in s.sections if x.level == 2)
