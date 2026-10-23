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


# ---------------------------------------------------------------------------- texts: intros
def texts_book():
    """An anthology of texts like Schwartz's: the index lists a group («Chile») and its
    texts as "Author: “Title”, N"; the group opens with the editor's own essay."""
    units = [unit(0, 1, "Las vanguardias de prueba", "heading"), unit(0, 1, "EDITORA PRUEBA"),
             unit(0, 2, "Vicente Huidobro: “Arte poética” (1916). “Non serviam” (1914)."),
             unit(0, 2, "VICENTE HUIDOBRO, el fundador de las vanguardias, nació en 1893."),
             unit(0, 2, "Su trayectoria pasa por París y Madrid."),
             unit(0, 3, "ARTE POÉTICA", "heading"), unit(0, 3, "Que el verso sea como una llave."),
             unit(0, 4, "NON SERVIAM", "heading"), unit(0, 4, "Y he aquí que una buena mañana."),
             unit(0, 5, "Sigue el manifiesto hasta el final.")]
    rows = rows_for(6, kinds={6: "index"})
    apparatus = [{"entry": "e" * 64, "page": 6, "label": "6", "kind": "index", "columns": 1,
                  "lines": ["ÍNDICE", "MANIFIESTOS", "Chile 2",
                            "Vicente Huidobro: “Arte poética”, 3; “Non serviam”, 4"]}]
    return entry(units, rows, apparatus, [], stem="textos",
                 meta={"title": "Las vanguardias de prueba", "author": "Editora Prueba"})


def test_editor_introduction_before_the_first_text_is_its_own_criticism_section():
    e = texts_book()
    s = sc.detect(e)
    check_invariants(s, e.units)
    assert s.kind == "texts"
    by_title = {x.title: x for x in s.sections}
    chile = by_title["Chile"]
    arte = by_title["Arte poética"]
    assert chile.level == 2 and arte.level == 3 and arte.first == 6
    intro = [x for x in s.sections if x.parent == chile.id and x.first == chile.first]
    assert len(intro) == 1
    intro = intro[0]
    assert (intro.title, intro.role, intro.author, intro.level) == ("Introducción", "criticism", None, 3)
    assert (intro.first, intro.last) == (chile.first, arte.first - 1)
    assert intro.confidence == chile.confidence
    assert "editor" in " ".join(intro.evidence)
    held = {seq: sid for seq, sid in leaves(s).items()}          # the container holds no
    assert chile.id not in held.values()                           # units of its own


def test_texts_matched_to_an_authored_index_entry_are_primary_wherever_they_sit():
    """A text listed as "Author: “Title”, N" is primary even at level 2 inside a part named
    for critical texts; a piece listed as "..., por Author" is a prologue: criticism."""
    units = [unit(0, 1, "Las vanguardias de prueba", "heading"), unit(0, 1, "EDITORA PRUEBA"),
             unit(0, 2, "La parábola de las vanguardias", "heading"), unit(0, 2, "ALFREDO BOSI"),
             unit(0, 2, "Yo vi el mundo comenzaba en Recife."),
             unit(0, 3, "TEXTOS CRÍTICOS", "heading"),
             unit(0, 3, "El sector crítico reúne ensayos de la época."),
             unit(0, 4, "NATURALEZA Y ARTE", "heading"), unit(0, 4, "JOAQUÍN TORRES-GARCÍA"),
             unit(0, 4, "El arte constructivo parte de la naturaleza."),
             unit(0, 5, "QUERER CONSTRUIR", "heading"), unit(0, 5, "Construir es querer."),
             unit(0, 6, "Sigue el texto.")]
    rows = rows_for(7, kinds={7: "index"})
    apparatus = [{"entry": "e" * 64, "page": 7, "label": "7", "kind": "index", "columns": 1,
                  "lines": ["ÍNDICE", "La parábola de las vanguardias, por Alfredo Bosi 2",
                            "TEXTOS CRÍTICOS",
                            "Joaquín Torres-García: “Naturaleza y arte”, 4; “Querer construir”, 5"]}]
    e = entry(units, rows, apparatus, [], stem="textos2",
              meta={"title": "Las vanguardias de prueba", "author": "Editora Prueba"})
    s = sc.detect(e)
    check_invariants(s, e.units)
    assert s.kind == "texts"
    by_title = {x.title: x for x in s.sections}
    bosi = by_title["La parábola de las vanguardias"]
    assert (bosi.role, bosi.author) == ("criticism", "Alfredo Bosi")
    part = by_title["TEXTOS CRÍTICOS"]
    assert part.level == 1 and part.role == "criticism"
    for t in ("Naturaleza y arte", "Querer construir"):
        x = by_title[t]
        assert (x.role, x.author, x.parent) == ("primary", "Joaquín Torres-García", part.id), t


def test_authors_are_cleaned_of_heads_and_titles_and_must_read_as_person_names():
    heads = {5: "LA NUEVA POESÍA", 6: "LA NUEVA POESÍA", 7: "LA NUEVA POESÍA"}
    units = [unit(0, 1, "Las vanguardias de prueba", "heading"), unit(0, 1, "EDITORA PRUEBA"),
             unit(0, 2, "Alejo Carpentier: “En la extrema avanzada” (1928)."),
             unit(0, 2, "EL SURREALISMO llegó a Cuba por la revista de avance."),
             unit(0, 3, "EN LA EXTREMA AVANZADA", "heading"), unit(0, 3, "Texto de Carpentier."),
             unit(0, 4, "MANIFIESTO POR UN ARTE REVOLUCIONARIO", "heading"),
             unit(0, 4, "Texto del manifiesto."),
             unit(0, 5, "LA NUEVA POESÍA", "heading"), unit(0, 5, "La nueva poesía abre aquí."),
             unit(0, 6, "AL OPORTUNO LECTOR", "heading"), unit(0, 6, "Texto de Proa."),
             unit(0, 7, "SOMOS", "heading"), unit(0, 7, "Texto del movimiento."),
             unit(0, 8, "Sigue hasta el final.")]
    rows = rows_for(9, heads, kinds={9: "index"})
    apparatus = [{"entry": "e" * 64, "page": 9, "label": "9", "kind": "index", "columns": 1,
                  "lines": ["ÍNDICE", "MANIFIESTOS", "Surrealismo 2",
                            "Surrealismo Alejo Carpentier: “En la extrema avanzada”, 3; Diego Rivera, "
                            "André Breton, [León Trotsky]: “Manifiesto por un arte revolucionario”, 4",
                            "La NUEVA POCA 5",
                            "Proa [Jorge Luis Borges]: “Al oportuno lector”, 6; Movimiento de "
                            "Vanguardia: “Somos”, 7"]}]
    e = entry(units, rows, apparatus, [], stem="textos3",
              meta={"title": "Las vanguardias de prueba", "author": "Editora Prueba"})
    s = sc.detect(e)
    check_invariants(s, e.units)
    by_title = {x.title: x for x in s.sections}
    assert by_title["En la extrema avanzada"].author == "Alejo Carpentier"      # head stripped
    rev = by_title["Manifiesto por un arte revolucionario"]
    assert rev.author == "Diego Rivera, André Breton, León Trotsky"            # the quoted part
    assert by_title["Al oportuno lector"].author == "Jorge Luis Borges"         # the revista's
    nueva = by_title["LA NUEVA POESÍA"]                                         # the head names
    assert nueva.author is None and nueva.pdf_pages[0] == 5                     # it, not an author
    somos = by_title["Somos"]
    assert somos.author is None and somos.confidence == "low"
    assert any("person names" in ev for ev in somos.evidence)


def test_index_entries_without_numbers_and_with_damaged_numbers_are_placed_by_title_in_order():
    """A body page between two index pages holds index entries whose number column was
    lost (Manifiestos p. 450): each is placed by its title among the headings between the
    anchored neighbours, in order; an index line whose number resolves to no page (an
    OCR error) is placed the same way; leader junk is stripped from titles."""
    names = ["ALFA", "BETA", "GAMMA", "DELTA", "EPSILON", "ZETA", "ETA", "OMEGA"]
    units = [unit(0, 1, "Las vanguardias de prueba", "heading"), unit(0, 1, "EDITORA PRUEBA")]
    for k, n in enumerate(names):
        units.append(unit(0, k + 2, n, "heading"))
        if n in ("GAMMA", "DELTA", "EPSILON", "ZETA"):
            units.append(unit(0, k + 2, "AUTOR " + n.title()))
        units.append(unit(0, k + 2, f"Texto de {n.title()} que sigue."))
    units.append(unit(0, 10, "Sigue hasta el final."))
    for n in ("GAMMA", "DELTA", "EPSILON", "ZETA"):             # p12: the number column
        units.append(unit(0, 12, n))                             # was lost in the export
        units.append(unit(0, 12, "Autor " + n.title()))
    rows = rows_for(13, kinds={11: "index", 13: "index"})
    apparatus = [{"entry": "e" * 64, "page": 11, "label": None, "kind": "index", "columns": 1,
                  "lines": ["ÍNDICE", "Alfa 2", "Beta 3"]},
                 {"entry": "e" * 64, "page": 13, "label": None, "kind": "index", "columns": 1,
                  "lines": ["Eta ...oooccooscccnnncnn 0", "Omega ..<.o ooo ocooorrrs 9"]}]
    e = entry(units, rows, apparatus, [], stem="textos4",
              meta={"title": "Las vanguardias de prueba", "author": "Editora Prueba"})
    s = sc.detect(e)
    check_invariants(s, e.units)
    by_title = {st.fold(x.title): x for x in s.sections}
    for k, n in enumerate(names):
        x = by_title[n.lower()]
        assert x.pdf_pages[0] == k + 2, (n, x.pdf_pages, x.evidence)
        assert x.sources == ["contents"], n
    for n in ("Gamma", "Delta", "Epsilon", "Zeta"):
        assert by_title[n.lower()].author == "Autor " + n, n
        assert any("between" in ev for ev in by_title[n.lower()].evidence), n
    eta = by_title["eta"]
    assert any("number 0" in ev and "between" in ev for ev in eta.evidence), eta.evidence
    assert 12 in s.contents["in_body_pages"]


def test_author_lines_glued_authors_and_author_lists_with_a_revista_or_an_et_al():
    assert sc._author_line("[Alberto Rojas Jiménez - Martín Bunster]") == \
        "Alberto Rojas Jiménez, Martín Bunster"
    assert sc._author_line("Jorge Luis Borges ·") == "Jorge Luis Borges"
    assert sc._author_line("ANATOMIA DE MI ULTRA") is None            # a title in capitals
    assert sc._glued_author("PALABRAS PRELIMINARES A GALERAS DE PAFOS Otilio Vigil-Díaz") == \
        ("PALABRAS PRELIMINARES A GALERAS DE PAFOS", "Otilio Vigil-Díaz")
    assert sc._glued_author("LA POESIA") == ("LA POESIA", None)
    heads, titles = {"la raza cosmica"}, {"identidades"}
    assert sc.clean_author("revista de avance, Alejo Carpentier, Martí Casanovas", "x",
                           heads, titles)[0] == "Alejo Carpentier, Martí Casanovas"
    assert sc.clean_author("Samuel R. Quiñones, Emilio R. Delgado y otros noístas", "x",
                           heads, titles)[0] == "Samuel R. Quiñones, Emilio R. Delgado et al."
    assert sc.clean_author("IDENTIDADES La raza cósmica José Vasconcelos", "La raza cósmica",
                           heads, titles)[0] == "José Vasconcelos"
    assert st._number_at_end("Guillermo de Torre: “Madrid, meridiano intelectual”, 594 o")[1] == "594"
    assert sc._author_line("Jorge Luis Borges ¬") == "Jorge Luis Borges"
    assert sc.clean_author("Ortelli y Gasset [Jorge Luis Borges y Carlos Mastronardi]", "x",
                           heads, titles)[0] == "Jorge Luis Borges y Carlos Mastronardi"


def test_a_contents_page_continues_on_the_body_page_after_it():
    """Bürger: the Sumario's last entries sit on the page after the index page, exported
    as one glued body line ("IV. Vanguardia y compromiso . 151 l. El debate ... . 151
    2. Nota final sobre Hegel"); a number that breaks the sequence (1 after 3) is an OCR
    error and the entry is placed by its title between its neighbours."""
    units = [unit(0, 1, "Teoría de prueba", "heading"), unit(0, 1, "AUTOR PRUEBA"),
             unit(0, 2, "INTRODUCCIÓN", "heading"), unit(0, 2, "Reflexiones previas."),
             unit(0, 3, "I. UNO", "heading"), unit(0, 3, "1. SUB UNO"), unit(0, 3, "Texto uno."),
             unit(0, 4, "2. SUB DOS"), unit(0, 4, "Texto dos."),
             unit(0, 5, "3. SUB TRES"), unit(0, 5, "Texto tres."),
             unit(0, 6, "II. DOS", "heading"), unit(0, 6, "Texto del dos."), unit(0, 7, "Sigue."),
             unit(0, 8, "III. TRES", "heading"), unit(0, 8, "l. SUB A"), unit(0, 8, "Texto a."),
             unit(0, 9, "2. SUB B"), unit(0, 9, "Texto b."), unit(0, 9, "EPÍLOGO", "heading"),
             unit(0, 9, "Texto del epílogo."),
             unit(0, 11, "III. Tres . 8 l. Sub a . 8 2. Sub b . 9 Epílogo . 9")]
    rows = rows_for(12, kinds={10: "index", 12: "blank"})
    apparatus = [{"entry": "e" * 64, "page": 10, "label": None, "kind": "index", "columns": 1,
                  "lines": ["Sumario", "Introducción 2", "I. Uno 3", "1. Sub uno 3", "2. Sub dos",
                            "3. Sub tres . 1", "II. Dos 6"]}]
    e = entry(units, rows, apparatus, [], stem="teoria5",
              meta={"title": "Teoría de prueba", "author": "Autor Prueba"})
    s = sc.detect(e)
    check_invariants(s, e.units)
    by_title = {st.fold(x.title): x for x in s.sections}
    want = {"introduccion": (2, 2), "i. uno": (2, 3), "1. sub uno": (3, 3), "2. sub dos": (3, 4),
            "3. sub tres": (3, 5), "ii. dos": (2, 6), "iii. tres": (2, 8), "l. sub a": (3, 8),
            "2. sub b": (3, 9), "epilogo": (2, 9)}
    for t, (level, page) in want.items():
        x = by_title[t]
        assert (x.level, x.pdf_pages[0]) == (level, page), (t, x.level, x.pdf_pages, x.evidence)
    assert all("contents" in by_title[t].sources for t in want)
    assert 11 in s.contents["in_body_pages"]
    tres = by_title["3. sub tres"]
    assert any("between" in ev for ev in tres.evidence), tres.evidence


def test_a_contents_claim_and_a_head_run_for_one_chapter_merge_under_the_full_title():
    """Unruh: chapter 2 is claimed by the contents at its title page and by the running
    head "THE VAGABOND EYE/I" pages later: one section, titled by the contents,
    starting where the title is, the head kept in the evidence. The introduction's
    contents entry (no number) is found by its "Introduction:" prefix on the page."""
    heads = {4: "CONSTRUCTING THINGS", 5: "CONSTRUCTING THINGS"}
    heads.update({p: "THE VAGABOND EYE" for p in (8, 9, 10, 11, 12)})
    units = [unit(0, 1, "Vanguards of Test", "heading"), unit(0, 1, "AUTHOR TEST"),
             unit(0, 2, "Introduction"), unit(0, 2, "Alpha and Beta"),
             unit(0, 2, "In the novel the vanguards meet their public."),
             unit(0, 3, "Constructing Things", "heading"), unit(0, 3, "Sub"),
             unit(0, 3, "It is necessary to undertake the conquest of the public."),
             unit(0, 4, "The manifestos perform."), unit(0, 5, "They perform again."),
             unit(0, 6, "Outward Turns of the Vagabond Eye", "heading"), unit(0, 6, "Portraits"),
             unit(0, 6, "Distant voices reach my ears."), unit(0, 7, "The garden metaphor."),
             unit(0, 8, "The artist hero unfolds."), unit(0, 9, "Many documents speak."),
             unit(0, 10, "Isolated artists."), unit(0, 11, "Antiromanticism."),
             unit(0, 12, "The end of the chapter.")]
    rows = rows_for(13, heads, kinds={13: "contents"})
    apparatus = [{"entry": "e" * 64, "page": 13, "label": None, "kind": "contents", "columns": 1,
                  "lines": ["Contents", "Introduction: Alpha and Beta", "1. Constructing Things: Sub 3",
                            "2. Outward Turns of the Vagabond Eye: Portraits 6"]}]
    e = entry(units, rows, apparatus, [], stem="unruh6",
              meta={"title": "Vanguards of Test", "author": "Author Test"})
    s = sc.detect(e)
    check_invariants(s, e.units)
    chapters = [x for x in s.sections if x.level == 2]
    assert [x.title for x in chapters] == ["Introduction: Alpha and Beta", "1. Constructing Things: Sub",
                                           "2. Outward Turns of the Vagabond Eye: Portraits"]
    assert [x.pdf_pages[0] for x in chapters] == [2, 3, 6]
    assert all("contents" in x.sources for x in chapters)
    eye = chapters[2]
    assert "head" in eye.sources and eye.pdf_pages == [6, 12]
    assert any("THE VAGABOND EYE" in ev for ev in eye.evidence), eye.evidence
    assert "head" in chapters[1].sources


def test_a_bracketed_list_of_names_in_the_running_head_is_the_essays_author_not_a_section():
    """Caribe: the last essay is signed "Sin autor" with the attributed names in the
    running head "[F. Mieses Burgos, F. Gatón Arce, Manuel Llanes et al.]": one section,
    its author those names, starting at the "Sin autor" line."""
    heads = {3: "Autor Uno", 4: "Autor U no",                  # an OCR variant of the name
             6: "[F. Mieses Burgos, F. Gatón Arce, Manuel Llanes et al.]",
             7: "Sin autor [F. Mieses Burgos, F. Gatón Arce, Manuel Llanes et al.]"}
    units = [unit(0, 1, "Las vanguardias en el Caribe", "heading"), unit(0, 1, "EDITORA PRUEBA"),
             unit(0, 2, "Autor Uno"), unit(0, 2, "Primer ensayo", "heading"),
             unit(0, 2, "El primer ensayo empieza aquí."), unit(0, 3, "Sigue el primero."),
             unit(0, 4, "Y acaba el primero."),
             unit(0, 5, "Sin autor", "heading"), unit(0, 5, "La Poesía Sorprendida y el Postumismo*", "heading"),
             unit(0, 5, "Ha llegado una hora de visiones."), unit(0, 6, "Sentido de escuela."),
             unit(0, 7, "La posición de los Postumistas.")]
    rows = rows_for(7, heads)
    toc = [[1, "Primer ensayo", 2], [1, "La Poesía Sorprendida y el Postumismo", 5]]
    e = entry(units, rows, [], toc, stem="caribe7",
              meta={"title": "Las vanguardias en el Caribe", "author": "Editora Prueba"})
    s = sc.detect(e)
    check_invariants(s, e.units)
    essays = [x for x in s.sections if not x.implicit]
    assert [x.title for x in essays] == ["Primer ensayo", "La Poesía Sorprendida y el Postumismo"], \
        [(x.title, x.pdf_pages) for x in s.sections]
    first, last = essays
    assert first.author == "Autor Uno"
    assert last.author == "F. Mieses Burgos, F. Gatón Arce, Manuel Llanes et al."
    assert last.first == 8 and last.pdf_pages == [5, 7]              # from the "Sin autor" line
    assert last.confidence == "high" and "head" in last.sources


def test_a_contents_header_without_a_page_of_its_own_joins_its_first_child():
    """Vasconcelos: the contents header "INTRODUCCIÓN" above an entry without a number
    ("Una fiesta en la Normal", found on p10) has no page of its own near p10; it is not
    a one-page section on p10 but context for that entry (the real INTRODUCCIÓN starts
    on p2 from its heading, too far back for the header's own look-back)."""
    units = [unit(0, 1, "La raza de prueba", "heading"), unit(0, 1, "AUTOR PRUEBA"),
             unit(0, 2, "INTRODUCCIÓN", "heading"), unit(0, 2, "El mestizaje empieza aquí.")]
    for p in range(3, 10):
        units.append(unit(0, p, f"Sigue la introducción en la página {p}."))
    units += [unit(0, 10, "Terminaba la introducción con estas palabras."),
              unit(0, 10, "UNA FIESTA EN LA NORMAL"), unit(0, 10, "La fiesta fue en la Normal."),
              unit(0, 11, "Sigue la fiesta."), unit(0, 12, "FIN", "heading"), unit(0, 12, "Fin.")]
    rows = rows_for(13, kinds={13: "index"})
    apparatus = [{"entry": "e" * 64, "page": 13, "label": None, "kind": "index", "columns": 1,
                  "lines": ["ÍNDICE", "INTRODUCCIÓN", "Una fiesta en la Normal", "FIN 12"]}]
    e = entry(units, rows, apparatus, [], stem="vasc4",
              meta={"title": "La raza de prueba", "author": "Autor Prueba"})
    s = sc.detect(e)
    check_invariants(s, e.units)
    titles = [(x.title, x.pdf_pages[0]) for x in s.sections if not x.implicit]
    assert ("INTRODUCCIÓN", 10) not in titles, titles
    fiesta = next(x for x in s.sections if st.fold(x.title) == "una fiesta en la normal")
    assert fiesta.pdf_pages[0] == 10 and fiesta.first == 13       # at its own heading line
    assert any("INTRODUCCIÓN" in ev for ev in fiesta.evidence), fiesta.evidence
