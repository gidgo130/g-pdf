# SPDX-License-Identifier: AGPL-3.0-or-later
"""gpdf-cli structure: contents and index pages parsed into entries, bookmarks verified
against the running heads, running-head runs, sections tiling the units, roles and
confidence, the sections files and the run log; sources and the text folder only read."""

import pytest

from gpdf.pipeline import structure as st


# ---------------------------------------------------------------------------- contents
def titles(entries):
    return [(e.title, e.author, e.label, e.level) for e in entries]


def test_contents_lines_title_number_and_author_lines():
    lines = ["Índice", "Agradecimientos … xi", "Antología crítica",
             "Vanguardia heroica y trama nacionalista", "Bernardo Subercaseaux 257",
             "Poesía parlante de Vicente Huidobro 457", "Cedomil Goic",
             "Huidobro a la hora de las vanguardias:",
             "acercamiento a su obra poética en francés 471", "Waldo Rojas",
             "Mistral y la vanguardia", "Grínor Rojo/Paula Miranda 507",
             "Lenguaje e ideología. Las escuelas argentinas de vanguardia",
             "FRANCINE MASIELLO 1 7 3", "Indice onomástico de la bibliografía 519"]
    got = titles(st.parse_contents(lines, headers={"antología crítica"}))
    assert got == [
        ("Agradecimientos", None, "xi", 1),
        ("Antología crítica", None, None, 1),
        ("Vanguardia heroica y trama nacionalista", "Bernardo Subercaseaux", "257", 2),
        ("Poesía parlante de Vicente Huidobro", "Cedomil Goic", "457", 2),
        ("Huidobro a la hora de las vanguardias: acercamiento a su obra poética en francés",
         "Waldo Rojas", "471", 2),
        ("Mistral y la vanguardia", "Grínor Rojo/Paula Miranda", "507", 2),
        ("Lenguaje e ideología. Las escuelas argentinas de vanguardia", "FRANCINE MASIELLO",
         "173", 2),
        ("Indice onomástico de la bibliografía", None, "519", 2)]


def test_contents_caps_entries_are_one_level_above_mixed_ones_and_inline_lists_below():
    lines = ["PRÓLOGO 15", "INTRODUCCIÓN 33", "Vanguardia, vanguardias … 40",
             "Miradas retrospectivas … 78",
             "Borges: La vanguardia negada, 78; Mário de Andrade: La destrucción cons-",
             "tructiva, 82", "MANIFIESTOS", "Chile 95",
             "Vicente Huidobro: “Arte poética”, 100; Non serviam, 101;",
             "“Prefacio a Adán”, 102; “La creación pura”, 108, “Época de creación”,",
             "114; “El creacionismo”, 115", "Movimiento vanguardista chileno: Rosa náutica, 125",
             "“Klaxon” [Mário de Andrade], 262"]
    got = titles(st.parse_contents(lines))
    assert got[:4] == [("PRÓLOGO", None, "15", 1), ("INTRODUCCIÓN", None, "33", 1),
                       ("Vanguardia, vanguardias", None, "40", 2),
                       ("Miradas retrospectivas", None, "78", 2)]
    assert got[4:6] == [("Borges: La vanguardia negada", None, "78", 3),
                        ("Mário de Andrade: La destrucción constructiva", None, "82", 3)]
    assert got[6:8] == [("MANIFIESTOS", None, None, 1), ("Chile", None, "95", 2)]
    assert got[8:14] == [("Arte poética", "Vicente Huidobro", "100", 3),
                         ("Non serviam", "Vicente Huidobro", "101", 3),
                         ("Prefacio a Adán", "Vicente Huidobro", "102", 3),
                         ("La creación pura", "Vicente Huidobro", "108", 3),
                         ("Época de creación", "Vicente Huidobro", "114", 3),
                         ("El creacionismo", "Vicente Huidobro", "115", 3)]
    assert got[14] == ("Movimiento vanguardista chileno: Rosa náutica", None, "125", 3)
    assert got[15] == ("Klaxon", "Mário de Andrade", "262", 3)


def test_contents_same_page_sublists_numbered_subchapters_and_act_lists():
    lines = ["Leyenda de Matachines … 42", "Los brujos de la tormenta primaveral … 52",
             "1 … 52", "2 … 54", "6 … 59", "Cuculcán Serpiente-envuelta-en-plumas … 60",
             "Primera Cortina Amarilla … 60", "Primera Cortina Roja … 63"]
    got = titles(st.parse_contents(lines))
    assert [g[3] for g in got] == [1, 1, 2, 2, 2, 1, 2, 2]
    lines = ["Advertencia 31", "Teoría de la vanguardia y ciencia crítica de la li-",
             "teratura E 51", "1. La historicidad de las categorías estéticas . 51",
             "2. Vanguardia como autocrítica del arte en la", "sociedad burguesa",
             "3. Discusión de la teoría del arte de Benjamin . 11",
             "1. El problema de la autonomia del arte en la so-", "ciedad burguesa GALICIA 83",
             "I. Problemas de la investigación . 83", "ni. La obra de arte vanguardista 111",
             "La problemática de la uo de obra 111", "El azar. 124"]
    got = titles(st.parse_contents(lines))
    assert got[0] == ("Advertencia", None, "31", 1)
    assert got[1] == ("Teoría de la vanguardia y ciencia crítica de la literatura", None, "51", 1)
    assert got[2] == ("1. La historicidad de las categorías estéticas", None, "51", 2)
    assert got[3][0].startswith("2. Vanguardia como autocrítica") and got[3][3] == 2
    assert got[4] == ("3. Discusión de la teoría del arte de Benjamin", None, None, 2)
    assert got[5][0].startswith("1. El problema de la autonomia") and got[5][3] == 1
    assert got[6] == ("I. Problemas de la investigación", None, "83", 2)
    assert got[7] == ("ni. La obra de arte vanguardista", None, "111", 1)
    assert got[8] == ("La problemática de la uo de obra", None, "111", 2)
    assert got[9] == ("El azar", None, "124", 2)
    lines = ["William Shakespeare, La Tempestad……………………….. 1",
             "Acto I, 5.—Acto II, 35.—Acto III, 55.—Epílogo, 99.",
             "Notas Extensas………………………………………….. 101",
             "I, I, Arte de marear de Shakespeare .—“I flam’d amazement…” (I, II, 198) Ariel y",
             "el Fuego de San Telmo” .—Un despiste dramático .",
             "Cuatro ensayos………………………………………………. 125", "Gente de Próspero, 127.",
             "Ariel, 131.", "Su nombre, .—Espíritu, ."]
    got = titles(st.parse_contents(lines))
    assert got[0] == ("William Shakespeare, La Tempestad", None, "1", 1)
    assert got[1:5] == [("Acto I", None, "5", 2), ("Acto II", None, "35", 2),
                        ("Acto III", None, "55", 2), ("Epílogo", None, "99", 2)]
    assert got[5] == ("Notas Extensas", None, "101", 1)
    assert [g[0] for g in got[6:9]] == [
        "I, I, Arte de marear de Shakespeare",
        "“I flam’d amazement…” (I, II, 198) Ariel y el Fuego de San Telmo”",
        "Un despiste dramático"]
    assert all(g[2] is None and g[3] == 2 for g in got[6:9])
    assert got[9] == ("Cuatro ensayos", None, "125", 1)
    assert got[10:12] == [("Gente de Próspero", None, "127", 2), ("Ariel", None, "131", 2)]
    assert got[12:14] == [("Su nombre", None, None, 3), ("Espíritu", None, None, 3)]


def test_two_column_contents_pair_only_when_the_counts_match():
    lines = ["INDICE", "MARINETTI Y EL FUTURISMO", "Rubén Darío", "EL FUTURISMO ITALIANO",
             "Y NUESTRO MODERNISMO NATURALISTA", "Henrique Soublette", "ROSA NAUTICA",
             "3", "27", "119"]
    got = titles(st.parse_contents(lines, columns=2))
    assert got == [("MARINETTI Y EL FUTURISMO", "Rubén Darío", "3", 1),
                   ("EL FUTURISMO ITALIANO Y NUESTRO MODERNISMO NATURALISTA",
                    "Henrique Soublette", "27", 1),
                   ("ROSA NAUTICA", None, "119", 1)]
    assert st.parse_contents(lines[:-1], columns=2) == []       # 3 entries, 2 numbers


def test_name_index_pages_are_not_contents():
    lines = ["Index", "Academia Brasileira de Letras, 224 Alcántara Machado, António, 243,",
             "Acción (periodical), 13 280n27", "Activism: aesthetic, 8, 14; of artists, 80,",
             "Adán, Martin: La casa de cartón, 2, 17,"]
    assert st.is_name_index(lines)
    assert not st.is_name_index(["Índice", "Guatemala … 3", "Ahora que me acuerdo … 7"])


# ---------------------------------------------------------------------------- labels
def page_rows(labels):
    return [{"page": i + 1, "label": lab, "label_status": "found" if lab else "none",
             "kind": "body", "head": None} for i, lab in enumerate(labels)]


def test_label_map_resolves_printed_pages_in_sequence():
    rows = page_rows([None, "xi", "xii", "1", "2", "3", "1", "2", "3"])
    lm = st.LabelMap(rows)
    assert lm.page("xi") == 2 and lm.page("XII") == 3
    assert lm.page("2") == 5 and lm.page("2", after=6) == 8
    assert lm.page("9") is None and lm.page("3", after=20) == 9


# ---------------------------------------------------------------------------- outline
def unit(seq, page, text, kind="prose", label=None):
    return {"entry": "e", "seq": seq, "kind": kind, "text": text, "pages": [page, page],
            "labels": [label, label], "label_inferred": False, "note": None, "lang": "es",
            "flags": []}


def rows_with_heads(n, heads, labels=None):
    rows = page_rows(labels or [str(i + 1) for i in range(n)])
    for r in rows:
        r["head"] = heads.get(r["page"])
    return rows


def test_a_one_per_page_outline_is_junk():
    toc = [[1, f"BURGER Peter, Teoria de la vanguardia{k:03d}", 2 * k - 1] for k in range(1, 98)]
    assert st.outline_is_junk(toc, 194)
    assert st.outline_is_junk([[1, "A", 1], [1, "B", 2], [1, "C", 3], [1, "D", 4]], 5)
    assert not st.outline_is_junk([[1, "Prefacio", 1], [2, "Canto I", 5], [2, "Canto II", 22]], 60)


def test_bookmarks_are_confirmed_reanchored_or_dropped():
    heads = {}
    for p in range(257, 279):
        heads[p] = "Vanguardia heroica y trama nacionalista" if p % 2 else "Bernardo Subercaseaux"
    for p in range(279, 299):
        heads[p] = "Aspectos de la vanguardia en Chile" if p % 2 else "Jaime Concha"
    rows = rows_with_heads(320, heads)
    units = [unit(1, 1, "Portada"), unit(2, 257, "Bernardo Subercaseaux Universidad de Chile"),
             unit(3, 257, "Vanguardia heroica y recepción nacional", "heading"),
             unit(4, 258, "Texto del ensayo."), unit(5, 279, "Aspectos de la vanguardia en Chile",
                                                   "heading"),
             unit(6, 280, "Más texto."), unit(7, 310, "Fin.")]
    contents = [st.ContentsEntry("Aspectos de la vanguardia en Chile", "Jaime Concha", "279", 2,
                                 page=279)]
    toc = [[1, "Vanguardia heroica y trama nacionalista", 257],
           [1, "Aspectos de la vanguardia en Chile", 307],
           [1, "Hadatty Mora, Yanna", 318]]
    res = st.verify_outline(toc, rows, units, contents)
    assert [c.title for c in res.confirmed] == ["Vanguardia heroica y trama nacionalista"]
    assert res.confirmed[0].page == 257 and "head" in " ".join(res.confirmed[0].evidence)
    [(claim, old)] = [(c, o) for c, o in res.reanchored]
    assert claim.title == "Aspectos de la vanguardia en Chile" and old == 307 and claim.page == 279
    assert "contents" in " ".join(claim.evidence)
    assert [(c.title, c.page) for c, why in res.dropped] == [("Hadatty Mora, Yanna", 318)]
    assert "verifies nowhere" in res.dropped[0][1]


def test_an_unconfirmed_bookmark_in_a_verified_outline_is_kept_as_a_claim():
    rows = rows_with_heads(60, {}, [None] * 60)
    units = [unit(1, 1, "PREFACIO", "heading"), unit(2, 5, "CANTO I", "heading"),
             unit(3, 22, "CANTO II", "heading"), unit(4, 26, "CANTO III", "heading"),
             unit(5, 31, "Altazor ¿por qué perdiste tu primera serenidad?", "verse"),
             unit(6, 40, "CANTO V", "heading"), unit(7, 55, "CANTO VI", "heading"),
             unit(8, 59, "CANTO VII", "heading")]
    toc = [[1, "Prefacio Altazor", 1], [2, "Canto I", 5], [2, "Canto II", 22],
           [2, "Canto III", 26], [2, "Canto IV", 31], [2, "Canto V", 40], [2, "Canto VI", 55],
           [2, "Canto VI", 59]]
    res = st.verify_outline(toc, rows, units, [])
    kept = {c.title: c for c in res.confirmed + res.unconfirmed}
    assert "Canto IV" in kept and kept["Canto IV"].confidence == st.MEDIUM
    assert "in order" in " ".join(kept["Canto IV"].evidence)
    assert res.dropped == []
    seventh = [c for c in res.confirmed if c.page == 59][0]
    assert seventh.title == "CANTO VII"                 # the heading unit names it


def test_two_bookmarks_on_one_page_the_grouping_folds_into_the_section():
    heads = {p: ("Emilio de Armas" if p % 2 == 0 else "Trayectoria poética de Mariano Brull")
             for p in range(401, 410)}
    rows = rows_with_heads(420, heads)
    units = [unit(1, 1, "x"), unit(2, 401, "Trayectoria poética de Mariano Brull", "heading"),
             unit(3, 402, "y")]
    toc = [[1, "Cuba", 401], [1, "Trayectoria poética de Mariano Brull", 401]]
    res = st.verify_outline(toc, rows, units, [])
    assert [c.title for c in res.confirmed] == ["Trayectoria poética de Mariano Brull"]
    assert [(c.title, why) for c, why in res.dropped][0][0] == "Cuba"
    assert "same page" in res.dropped[0][1]


# ---------------------------------------------------------------------------- heads
def test_head_runs_cluster_ocr_variants_and_start_at_the_opening_page():
    heads = {}
    for p in range(401, 412):
        heads[p] = "Emilio de Armas" if p % 2 == 0 else (
            "Trayectoria poética de Mariano Bruii" if p < 405 else
            "Trayectoria poética de Mariano Brull")
    heads[401] = "Emilio de Armas | Trayectoria poética de Mariano Bruii*"
    for p in range(414, 420):
        heads[p] = "Antonio Benitez-Rojo" if p % 2 == 0 else "Nicolás Guillén and Sugar"
    rows = rows_with_heads(420, heads)
    for r in rows:
        r["kind"] = "blank" if r["page"] == 412 else "body"
    units = [unit(1, 1, "x"), unit(2, 401, "Trayectoria", "heading"), unit(3, 413, "Nicolás Guillén and Sugar", "heading")]
    runs = st.head_runs(rows)
    keys = {r.key for r in runs}
    assert len([r for r in runs if "trayectoria" in r.key]) == 1     # Bruii/Brull: one run
    assert len(keys) == 4
    bounds = st.head_boundaries(rows, units)
    assert [(b.page, b.title) for b in bounds][:2] == [
        (401, "Trayectoria poética de Mariano Brull"), (413, "Nicolás Guillén and Sugar")]
    assert bounds[0].author == "Emilio de Armas" and bounds[1].author == "Antonio Benitez-Rojo"
    assert "pp. 401-411" in " ".join(bounds[0].evidence)


def test_head_boundary_inside_a_constant_verso_head():
    heads = {}
    for p in range(33, 60):
        heads[p] = "Bibliografía" if p % 2 == 0 else ("Caribe" if p < 53 else "Cuba")
    rows = rows_with_heads(80, heads)
    units = [unit(1, 33, "x"), unit(2, 53, "Cuba", "heading")]
    bounds = st.head_boundaries(rows, units)
    assert [(b.page, b.title, b.author) for b in bounds] == [
        (33, "Caribe", None), (53, "Cuba", None)]


def test_anchor_a_claim_to_the_unit_where_the_section_starts():
    units = [unit(1, 605, "Lo curioso es que la obra acabe resolviendo."),
             unit(2, 606, "Bibliografía", "heading"),
             unit(3, 606, "BATAILLE, Georges. Espace, en Documents (1930): 41-2."),
             unit(4, 607, "Pablo de Rokha,", "heading"), unit(5, 607, "Naín Nómez"),
             unit(6, 607, "Universidad de Santiago de Chile"),
             unit(7, 607, "Partamos de la presunción de que en medio de nuestras vanguardias."),
             unit(8, 633, "María Inés Zaldívar"), unit(9, 633, "Universidad Católica"),
             unit(10, 633, "Winétt de Rokha y la vanguardia literaria en Chile", "heading"),
             unit(11, 634, "Texto."),
             unit(12, 128, "ZSIGMOND REMENYIK", "heading"),
             unit(13, 128, "CARTEL NÚM. 11I*", "heading"), unit(14, 128, "Texto del cartel."),
             unit(15, 150, "Texto que sigue en la página."),
             unit(16, 151, "ROSA NAUTICA", "heading"), unit(17, 151, "Texto."),
             unit(18, 40, "La Escena, una Isla deshabitada", "heading"),
             unit(19, 40, "ACTO I\nESCENA I", "verse"), unit(20, 40, "Entra el Contramaestre.")]
    on = st.units_on(units)

    def anchor(title, page, author=None):
        c = st.Claim(title, page, "contents", author=author)
        idx, how = st.anchor(c, units, on)
        return units[idx]["seq"], how

    assert anchor("Pablo de Rokha, las vanguardias y lo nacional-popular", 607) == (
        4, "heading unit on p607")
    assert anchor("Winétt de Rokha y la vanguardia literaria en Chile", 633) == (
        8, "heading unit on p633, after the author lines")
    assert anchor("Cartel núm. 111", 128, "Zsigmond Remenyik") == (
        12, "heading unit on p128, after the author lines")
    assert anchor("Rosa náutica", 150) == (16, "heading unit on p151")
    assert anchor("Acto I", 40) == (19, "title at the start of a unit on p40")
    assert anchor("Bibliografía", 606) == (2, "heading unit on p606")
    assert anchor("Nada que ver", 606) == (2, "first unit on p606 (title not found)")
