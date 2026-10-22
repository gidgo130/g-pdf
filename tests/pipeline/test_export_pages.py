# SPDX-License-Identifier: AGPL-3.0-or-later
"""The text export on synthetic pages: running heads and page labels, footnotes and
quotes, the fit test (prose, verse, drama, bibliography, paragraphs over a page break),
hyphens, letter-spacing, drop caps, page junk, scan-layer artefacts, contents and index
pages, and the plain-text entry."""

import pytest

pytest.importorskip("numpy")
pytest.importorskip("wordfreq")
import pymupdf  # noqa: E402

from gpdf.pipeline import export  # noqa: E402
from gpdf.pipeline import textlayout as tl  # noqa: E402

from .exportsynth import PITCH, X0, X1, Sheet, job, save, width, words  # noqa: E402


def run(path, **kw):
    """(records, page rows, apparatus, text, counts)."""
    return export.export_pdf(job(path, **kw))


def texts(records, kind=None):
    return [r["text"] for r in records if kind is None or r["kind"] == kind]


def body(sheet, seed, n=60, **kw):
    return sheet.para(words(seed, n), **kw)


@pytest.fixture
def scan_font(monkeypatch):
    """Courier stands for Tesseract's GlyphLessFont (word boxes only, a scan layer)."""
    monkeypatch.setattr(tl, "TESSERACT_FONTS", ("Courier",))


# ---------------------------------------------------------------------------- heads, labels
def test_running_heads_stripped_headings_kept(tmp_path):
    path = tmp_path / "heads.pdf"
    doc = pymupdf.open()
    for i in range(12):
        s = Sheet(doc)
        n = i + 11                                         # printed = PDF page + 10
        if i % 2 == 0:
            s.text(X0, 40, f"{n} | INTRODUCCIÓN", 8)       # verso: number first
        else:
            s.text(220, 40, f"BRASIL (REVISTAS) | {n}", 8)  # recto: number last
        if i in (0, 8):
            s.text(180, 62, "CANTO I", 14)                 # every 8 pages: a heading
            s.y = 90
        if i in (4, 5, 6):
            s.text(X0, 62, f"Nota Extensa {i - 1}", 14)    # numbers off the sequence
            s.y = 90
        if i in (9, 10, 11):                               # Tempestad: a roman in brackets
            s.text(X0, 62, f"La revolución de entremés ({'I' * (i - 8)})", 14)
            s.y = 90
        body(s, 100 + i, 80)
    save(doc, path)
    records, rows, _, _, counts = run(path)
    assert [r["label"] for r in rows] == [str(i + 11) for i in range(12)]
    assert all(r["label_status"] == "found" for r in rows)
    assert [r["head"] for r in rows] == ["INTRODUCCIÓN", "BRASIL (REVISTAS)"] * 6
    assert all(r["removed"].get("running_head") == 1 for r in rows)
    heads = texts(records, "heading")
    assert heads.count("CANTO I") == 2
    assert {"Nota Extensa 3", "Nota Extensa 4", "Nota Extensa 5"} <= set(heads)
    assert {"La revolución de entremés (II)", "La revolución de entremés (III)"} <= set(heads)
    assert not any("INTRODUCCIÓN" in t or "BRASIL" in t for t in texts(records))
    assert counts["heads"] == ["BRASIL (REVISTAS)", "INTRODUCCIÓN"]


def test_bottom_page_numbers_roman_pagina_and_a_misread_label(tmp_path):
    path = tmp_path / "numbers.pdf"
    doc = pymupdf.open()
    for i in range(10):
        s = Sheet(doc)
        body(s, 200 + i, 70)
        label = ["i", "ii", "iii", "iv"][i] if i < 4 else str(i - 3)
        if i == 7:
            label = "Tn"                                   # Bürger p. 78: "77" misread
        s.text(205, 575, label, 9)
    save(doc, path)
    _, rows, _, _, counts = run(path)
    assert [r["label"] for r in rows] == ["i", "ii", "iii", "iv", "1", "2", "3", "4", "5", "6"]
    assert rows[7]["label_status"] == "inferred"
    assert rows[7]["dropped"] == [{"reason": "page_number_misread", "text": "Tn"}]
    assert sum(r["label_status"] == "found" for r in rows) == 9

    path = tmp_path / "pagina.pdf"
    doc = pymupdf.open()
    for i in range(3):
        s = Sheet(doc)
        body(s, 300 + i, 60)
        s.text(300, 585, f"Página {i + 1} de 3", 8)
    save(doc, path)
    records, rows, _, _, _ = run(path)
    assert [(r["label"], r["label_status"]) for r in rows] == [("1", "found"), ("2", "found"),
                                                              ("3", "found")]
    assert not any("Página" in t for t in texts(records))


# ---------------------------------------------------------------------------- notes, quotes
def _notes_doc(path, superscript=True):
    doc = pymupdf.open()
    s = Sheet(doc)
    ws = words(400, 40)
    s.justify(ws[:12])
    x = X0 + 15
    s.y += 0
    # a line with a glued marker ("vanguardias1") and a superscript one
    line = "las vanguardias1 de la época y la crítica"
    s.text(X0, s.y, line)
    xe = X0 + width(line)
    if superscript:
        s.text(xe, s.y - 4, "2", 6)
    s.text(xe + 4, s.y, " siguen aquí.")
    s.y += PITCH
    s.para(ws[12:40], indent=15)
    # a small-type verse quote at the bottom of the body
    s.y += 6
    for ln in ("Forjada en la fábrica de armas", "la ciudad", "muerde con sus almenas"):
        s.line(ln, x=120, size=8, pitch=10)
    s.y += 18
    s.line("1 Véase el capítulo tercero, donde se discuten las polémicas de la época.", size=8,
           pitch=10)
    s.line("2 Cf. P. Bürger, Teoría de la vanguardia, trad. de J. García, p. 45, y también",
           size=8, pitch=10)
    s.line("las páginas que siguen", size=8, pitch=10)       # note 2 goes on next page
    s2 = Sheet(doc)
    s2.para(words(401, 50), indent=0)
    s2.y += 18
    s2.line("donde se explica la recepción del libro.", size=8, pitch=10)
    s2.line("3 Una nota nueva con su número.", size=8, pitch=10)
    save(doc, path)


def test_footnotes_markers_quotes_and_a_continued_note(tmp_path):
    path = tmp_path / "notes.pdf"
    _notes_doc(path)
    records, _, _, _, counts = run(path)
    notes = [r for r in records if r["kind"] == "note"]
    assert [n["note"] for n in notes] == [1, 2, 3]
    assert notes[1]["pages"] == [1, 2]
    assert notes[1]["text"].endswith("y también las páginas que siguen donde se explica la "
                                     "recepción del libro.")
    assert notes[0]["text"].startswith("Véase el capítulo")
    prose = " ".join(texts(records, "prose"))
    assert "las vanguardias[^1] de la época y la crítica[^2] siguen aquí." in prose
    assert counts["markers"]["linked"] == 1 and counts["markers"]["superscript"] >= 1
    verse = [r for r in records if r["kind"] == "verse"]
    assert verse and verse[0]["text"] == ("Forjada en la fábrica de armas\nla ciudad\n"
                                          "muerde con sus almenas")
    assert "quote" in verse[0]["flags"]
    kinds = [r["kind"] for r in records]
    assert kinds.index("verse") < kinds.index("note")       # notes after the page's body


def test_ocr_damaged_note_markers_on_a_scan_layer(tmp_path, scan_font):
    # Schwartz p. 73: indented note starts whose numbers OCR damaged ("8", "$8", "89", "%")
    path = tmp_path / "damaged.pdf"
    doc = pymupdf.open()
    s = Sheet(doc, font="cour")
    for k in range(14):
        s.line(" ".join(words(950 + k, 8)), size=9)
    s.y += 16
    for marker, text in (("15", "Véase el libro."), ("8", "Ibidem, p. 12."),
                         ("8", "Ibidem, p. 13."), ("$8", "Alfabeto syentifigo keshwa.")):
        s.line(f"{marker} {text}", x=X0 + 10, size=7, pitch=8)
    s.line("89 Cf. la bibliografía de Miguel Ángel Rodríguez Rea, Guía del", x=X0 + 10,
           size=7, pitch=8)
    s.line("Boletín, Lima, 1981.", size=7, pitch=8)
    s.line("% Boletín Titikaka, núm. 17, p. 1.", x=X0 + 10, size=7, pitch=8)
    save(doc, path)
    records, *_ = run(path, scan=True)
    notes = [r for r in records if r["kind"] == "note"]
    assert [n["note"] for n in notes] == [15, 16, 17, 18, 19, 20]
    assert notes[0]["flags"] == [] and all("marker_inferred" in n["flags"] for n in notes[1:])
    assert notes[4]["text"] == "Cf. la bibliografía de Miguel Ángel Rodríguez Rea, Guía del " \
        "Boletín, Lima, 1981."


def test_superscript_ordinal_is_not_a_marker(tmp_path):
    path = tmp_path / "ordinal.pdf"
    doc = pymupdf.open()
    s = Sheet(doc)
    s.text(X0, s.y, "la 2")
    s.text(X0 + width("la 2"), s.y - 4, "a", 6)
    s.text(X0 + width("la 2") + 4, s.y, " edición del libro de poemas")
    save(doc, path)
    records, *_ = run(path)
    assert "[^" not in " ".join(texts(records))


def test_unmarked_small_block_is_an_editorial_note_with_the_rule(tmp_path, monkeypatch):
    path = tmp_path / "editorial.pdf"
    doc = pymupdf.open()
    s = Sheet(doc)
    body(s, 500, 80)
    s.line("Octubre de 1909.")
    s.y += 30
    for ln in ("Publicado en Buenos Aires (Moen y Hno. Editores, 1909) cuando el",
               "Modernismo estaba aún en vigencia; el prólogo anticipa ideas nuevas."):
        s.line(ln, x=X0 + 40, size=8, pitch=10)
    save(doc, path)
    records, *_ = run(path)
    assert not [r for r in records if r["kind"] == "note"]       # no rule: a quote
    monkeypatch.setitem(export.RULES, "e" * 16, {"editorial_notes": True})
    records, *_ = run(path, id="e" * 64)
    notes = [r for r in records if r["kind"] == "note"]
    assert len(notes) == 1 and notes[0]["note"] is None and "editorial" in notes[0]["flags"]
    assert notes[0]["text"].startswith("Publicado en Buenos Aires")


# ---------------------------------------------------------------------------- the fit test
def test_justified_prose_reflows_and_paragraphs_split_at_indents(tmp_path):
    path = tmp_path / "prose.pdf"
    doc = pymupdf.open()
    s = Sheet(doc)
    a = s.para(words(600, 70))
    b = s.para(words(601, 50))
    save(doc, path)
    records, *_ = run(path)
    assert [r["kind"] for r in records] == ["prose", "prose"]
    assert records[0]["text"] == " ".join(w for ln in a for w in ln)
    assert records[1]["text"] == " ".join(w for ln in b for w in ln)


def test_ragged_verse_keeps_its_breaks(tmp_path):
    path = tmp_path / "verse.pdf"
    doc = pymupdf.open()
    s = Sheet(doc)
    s.text(180, 60, "CANTO I", 14)
    poem = ["El día se suicida arrojándose al mar", "Un barco vestido de luces se aleja",
            "Y al fondo de las olas un pez escucha el paso de los hombres",
            "Silencio la tierra va a dar a luz un árbol", "La muerte se ha dormido"]
    for ln in poem:
        s.line(ln)
    s.y += PITCH
    for ln in poem[:3]:
        s.line(ln)
    save(doc, path)
    records, *_ = run(path)
    assert [r["kind"] for r in records] == ["heading", "verse", "verse"]
    assert records[1]["text"] == "\n".join(poem)
    assert records[2]["text"] == "\n".join(poem[:3])


def test_drama_one_unit_per_speech(tmp_path):
    path = tmp_path / "drama.pdf"
    doc = pymupdf.open()
    s = Sheet(doc)
    s.line("HOMBRE CÚBICO (con tono lacrimoso): No quiero.")
    s.justify("REINA BIZANTINA: No, ocurrirá que los seres humanos en vez de pensar".split())
    s.line("cavilarán cosas lindas...")
    s.line("GALÁN (llevándose la mano a la oreja): Siento que llaman...")
    s.line("Una voz remota: ¿Dónde estás, mi reina?")
    s.line("ROCAMBOLE: ¡Maldito sea! ¿Dónde dejé mi látigo?")
    save(doc, path)
    records, *_ = run(path)
    assert [r["text"][:9] for r in records] == ["HOMBRE CÚ", "REINA BIZ", "GALÁN (ll",
                                                "Una voz r", "ROCAMBOLE"]
    assert records[1]["text"].endswith("en vez de pensar cavilarán cosas lindas...")
    assert all(r["kind"] == "prose" for r in records)


def test_bibliography_hanging_indent(tmp_path):
    path = tmp_path / "biblio.pdf"
    doc = pymupdf.open()
    s = Sheet(doc)
    entries = [
        ("Bürger, Peter. Teoría de la vanguardia. Trad. de Jorge García. Barcelona:",
         "Península, 1987."),
        ("Schwartz, Jorge. Las vanguardias latinoamericanas. Textos programáticos y",
         "críticos. Madrid: Cátedra, 1991."),
        ("Unruh, Vicky. Latin American Vanguards. The Art of Contentious Encounters.",
         "Berkeley: University of California Press, 1994."),
    ]
    for first, rest in entries:
        s.justify(first.split())
        s.line(rest, x=X0 + 18)
    save(doc, path)
    records, *_ = run(path)
    assert texts(records) == [f"{a} {b}" for a, b in entries]


def test_paragraph_continued_over_a_page_break(tmp_path):
    path = tmp_path / "break.pdf"
    doc = pymupdf.open()
    s = Sheet(doc)
    ws = words(700, 120)
    lines = s.para(ws[:60], last_full=True)
    s2 = Sheet(doc)
    rest = s2.para(ws[60:], indent=0)
    s2.para(words(701, 30))
    save(doc, path)
    records, *_ = run(path)
    assert records[0]["pages"] == [1, 2]
    assert records[0]["text"] == " ".join(ws[:60] + [w for ln in rest for w in ln])
    assert records[1]["pages"] == [2, 2]


# ---------------------------------------------------------------------------- hyphens
def test_hyphens(tmp_path):
    path = tmp_path / "hyphens.pdf"
    doc = pymupdf.open()
    s = Sheet(doc)
    # (a soft hyphen inside a line is not representable in a base-14 font: see the
    # cleanup tests; at a line end MuPDF reads it as a hyphen, which joins the same way)
    rows = ["Este texto trata con cuidado de la transfor-",
            "mación social y también del diálogo luso-",
            "hispánico en el libro, en las páginas 57-",
            "79 del capítulo tercero de Wentzlaff-",
            "Eggebert sobre la palabra dicha y la pala­",
            "bra escrita en el libro, páginas 39­",
            "69 y sobre el concepto de la insti.",
            "tución arte en la sociedad que transfor-"]
    for r in rows:
        s.justify(r.split(" "), x0=X0)
    s2 = Sheet(doc)
    s2.line("ma la vida de todos.")
    save(doc, path)
    records, *_ = run(path)
    text = records[0]["text"]
    assert text == ("Este texto trata con cuidado de la transformación social y también del "
                    "diálogo luso-hispánico en el libro, en las páginas 57-79 del capítulo "
                    "tercero de Wentzlaff-Eggebert sobre la palabra dicha y la palabra escrita en "
                    "el libro, páginas 39-69 y sobre el concepto de la institución arte en la "
                    "sociedad que transforma la vida de todos.")
    assert records[0]["pages"] == [1, 2]


# ---------------------------------------------------------------------------- letter-spacing
def test_letter_spacing_by_gaps(tmp_path):
    path = tmp_path / "spaced.pdf"
    doc = pymupdf.open()
    s = Sheet(doc)
    s.spaced("Yo h e t e n d i d o la mano".split(),
             [6, 2, 6, 2, 2, 2, 2, 2, 2, 6, 6, 6])
    s.y += PITCH
    s.spaced("dijo y a l l í con voz".split(), [6, 6, 2, 2, 2, 6, 6])
    s.y += PITCH
    s.spaced("L o q u e G o r o s t i z a l l a m a".split(),
             [2, 6, 2, 2, 6, 2, 2, 2, 2, 2, 2, 2, 2, 6, 2, 2, 2, 2])
    s.y += PITCH
    s.justify("una frase de la que se lo da a él y a ella".split(), x1=X0 + 190)
    s.y += PITCH
    s.spaced("es u n m ovim iento hom bres y la cósm ico".split(),
             [8, 1.9, 8, 1.9, 2.0, 8, 1.9, 8, 8, 8, 1.8])
    save(doc, path)
    records, _, _, _, counts = run(path)
    t = "\n".join(texts(records))
    assert "Yo he tendido la mano" in t
    assert "dijo y allí con voz" in t
    assert "Lo que Gorostiza llama" in t
    assert "una frase de la que se lo da a él y a ella" in t
    assert "es un movimiento hombres y la cósmico" in t
    assert counts["msplit"] >= 3 and counts["spacing"]["geometry"] >= 3

    records, *_ = run(path, nodict=True)
    t = "\n".join(texts(records))
    assert "Yo he tendido la mano" in t and "dijo y allí con voz" in t    # layout only
    assert "es un movim iento hom bres y la cósm ico" in t   # no word list (only the gaps)


# ---------------------------------------------------------------------------- caps, junk
def test_drop_cap_and_giant_junk(tmp_path):
    path = tmp_path / "dropcap.pdf"
    doc = pymupdf.open()
    s = Sheet(doc, y=120)
    s.text(X0, 140, "A", 40)
    s.text(110, 30, "NE", 60)                     # giant, beside no text
    s.justify("quella tarde, el viejo y venerado maestro, á quien solían".split(), x0=X0 + 32)
    s.justify("llamar Próspero, por alusión al sabio mago de La Tempestad".split(), x0=X0 + 32)
    s.justify("shakespiriana, se despedía de sus jóvenes discípulos, pasado".split(), x0=X0 + 32)
    s.line("un año de tareas.")
    save(doc, path)
    records, rows, _, _, counts = run(path)
    assert records[0]["text"].startswith("Aquella tarde, el viejo")
    assert counts["drop_caps"] == 1
    assert {"reason": "giant_junk", "text": "NE"} in rows[0]["dropped"]


def test_margin_junk_on_a_scan_layer_only(tmp_path, scan_font):
    for font, scan in (("cour", True), ("tiro", False)):
        path = tmp_path / f"margin-{font}.pdf"
        doc = pymupdf.open()
        s = Sheet(doc, font=font)
        for k in range(8):
            if k == 3:
                s.text(12, s.y, "oo")              # left margin: no real word
                s.text(375, s.y, "hve")            # right margin
            if k == 5:
                s.text(12, s.y, "1234")            # a number: left to the number rules
            s.line(" ".join(words(800 + k, 7)), size=9)
        save(doc, path)
        records, rows, *_ = run(path, scan=scan)
        allt = " ".join(texts(records))
        dropped = {d["text"] for d in rows[0]["dropped"] if d["reason"] == "margin_junk"}
        if font == "cour":
            assert dropped == {"oo", "hve"} and "oo" not in allt.split()
        else:
            assert not dropped and "hve" in allt
        assert "1234" in allt


def test_unruh_artefacts(tmp_path, scan_font):
    path = tmp_path / "unruh.pdf"
    doc = pymupdf.open()
    s = Sheet(doc, font="cour")
    s.line("_ ~ |", size=9)
    s.line("_ Language may_be considered an actor in material culture but", size=9)
    s.line("also-a-protagonist in the history of language when spoken ~", size=9)
    save(doc, path)
    records, rows, _, _, counts = run(path, scan=True, lang="en")
    t = " ".join(texts(records))
    assert "Language may be considered an actor" in t
    assert "also-a-protagonist" in t and "language when spoken" in t   # (em dash: cleanup)
    assert not t.startswith("_") and not t.endswith("~")
    assert {"reason": "symbol_line", "text": "_ ~ |"} in rows[0]["dropped"]
    assert any("also-a-protagonist" in r for r in counts["reported"])


# ---------------------------------------------------------------------------- apparatus
def test_contents_and_two_column_index_go_to_the_apparatus(tmp_path):
    path = tmp_path / "apparatus.pdf"
    doc = pymupdf.open()
    s = Sheet(doc)
    s.line("ÍNDICE", x=180)
    for t, n in (("Prólogo", 7), ("Bello Horizonte", 79), ("La despedida de un ministro", 83),
                 ("El Centenario de la Independencia", 86), ("Una revista naval", 88)):
        s.line(f"{t} ........o.ooooomommmoommm.... {n}")
    for i in range(18):
        b = Sheet(doc)
        body(b, 900 + i, 60)
    idx = Sheet(doc)
    for k in range(14):
        idx.text(X0, 80 + k * 12, f"Autor{k}, Nombre, {k + 3}, {k + 20}", 9)
        idx.text(230, 80 + k * 12, f"Obra{k}, Título, {k + 40}", 9)
    save(doc, path)
    records, rows, app, _, counts = run(path)
    assert rows[0]["kind"] == "contents" and rows[-1]["kind"] == "index"
    assert app[0]["lines"][1] == "Prólogo … 7"
    assert app[-1]["columns"] == 2
    assert app[-1]["lines"][0] == "Autor0, Nombre, 3, 20" and app[-1]["lines"][14] == \
        "Obra0, Título, 40"
    allt = " ".join(texts(records))
    assert "Prólogo" not in allt and "Autor0" not in allt
    assert counts["apparatus_pages"] == 2


# ---------------------------------------------------------------------------- invariants
def test_every_record_has_entry_pages_and_a_valid_kind(tmp_path):
    path = tmp_path / "notes.pdf"
    _notes_doc(path)
    records, rows, _, txt, _ = run(path, id="a" * 64)
    assert [r["seq"] for r in records] == list(range(1, len(records) + 1))
    for r in records:
        assert r["entry"] == "a" * 64 and r["kind"] in export.KINDS
        assert 1 <= r["pages"][0] <= r["pages"][1] <= 2
    firsts = [r["pages"][0] for r in records if r["kind"] != "note"]
    assert firsts == sorted(firsts)
    assert txt.startswith("[pdf 1]") and "[^1] Véase" in txt


# ---------------------------------------------------------------------------- .txt
ROOSEVELT = ("A Roosevelt\r\n[Poema - Texto completo.]\r\n\r\nRubén Darío\r\n\r\n"
             "https://ciudadseva.com/texto/a-roosevelt/\r\n\r\n– VIII –\r\n\r\nA Roosevelt\r\n\r\n"
             "¡Es con voz de Biblia, o verso de Walt Whitman,\r\n"
             "que habría que llegar hasta ti, Cazador!\r\n"
             "¡Primitivo y moderno, sencillo y complicado,\r\n\r\n"
             "Eres soberbio y fuerte ejemplar de tu raza;\r\n"
             "eres culto, eres hábil; te opones a Tolstoy.\r\n")


def test_txt_entry(tmp_path):
    path = tmp_path / "A Roosevelt.txt"
    path.write_bytes(ROOSEVELT.encode("utf-8"))
    records, rows, app, txt, _ = export.export_txt(job(path, kind="txt"))
    assert [r["kind"] for r in records] == ["heading", "verse", "verse"]
    assert records[0]["text"] == "A Roosevelt"
    assert records[1]["text"] == ("¡Es con voz de Biblia, o verso de Walt Whitman,\n"
                                  "que habría que llegar hasta ti, Cazador!\n"
                                  "¡Primitivo y moderno, sencillo y complicado,")
    assert records[1]["lines"] == [12, 14] and records[2]["lines"] == [16, 17]
    assert sum(r["text"] == "A Roosevelt" for r in records) == 1
    assert {d["text"] for d in rows[0]["dropped"]} >= {
        "[Poema - Texto completo.]", "Rubén Darío", "https://ciudadseva.com/texto/a-roosevelt/",
        "– VIII –"}
    assert app == [] and "ciudadseva" not in txt


# ---------------------------------------------------------------------------- calibration
def test_running_head_with_a_misread_page_number(tmp_path):
    # Unruh p. 237: "12 TALES OF LINGUISTIC ENCOUNTER" where the page is 222
    path = tmp_path / "misread_head.pdf"
    doc = pymupdf.open()
    for i in range(8):
        s = Sheet(doc)
        n = 215 + i
        s.text(X0, 40, f"{12 if i == 4 else n} TALES OF LINGUISTIC ENCOUNTER", 8)
        body(s, 1100 + i, 70)
    save(doc, path)
    records, rows, *_ = run(path, lang="en")
    assert rows[4]["head"] == "TALES OF LINGUISTIC ENCOUNTER"
    assert rows[4]["label"] == "219" and rows[4]["label_status"] == "inferred"
    assert not any("TALES" in t for t in texts(records))


def test_short_tesseract_line_is_not_small_type_by_its_size(tmp_path, scan_font):
    # Bürger p. 78: "insignif-" + "cante." (a one-word line OCR'd at a smaller size)
    path = tmp_path / "cante.pdf"
    doc = pymupdf.open()
    s = Sheet(doc, font="cour")
    for k in range(10):
        s.line(" ".join(words(1200 + k, 7)), size=9)
    s.line("de ordenación cuyo valor de conocimiento es insignif-", size=9)
    s.line("cante.", size=7.5)
    s.line(" ".join(words(1220, 7)), x=X0 + 15, size=9)
    s.line(" ".join(words(1221, 7)), size=9)
    save(doc, path)
    records, *_ = run(path, scan=True)
    t = " ".join(texts(records))
    assert "insignifcante." in t                   # as OCR read it
    assert not [r for r in records if r["kind"] == "quote"]


def test_notes_by_pitch_where_the_layer_states_the_body_small(tmp_path):
    # Mexico anthology p. 277: body and notes both stated at 8 pt; the notes are tighter;
    # a marker glued to a quote ('47"La poesía')
    path = tmp_path / "pitch.pdf"
    doc = pymupdf.open()
    for i in range(3):                              # the file's body: 10.5 pt
        body(Sheet(doc), 1300 + i, 90)
    s = Sheet(doc)
    for k in range(14):
        s.line(" ".join(words(1310 + k, 9)), size=8, pitch=12.2)
    s.y += 20
    s.line('47"La poesía actual de México", en Prosa, 178.', size=8, pitch=9.6)
    s.line("48 Prosa, 181.", size=8, pitch=9.6)
    save(doc, path)
    records, *_ = run(path)
    mine = [r for r in records if r["pages"][0] == 4]
    assert [r["kind"] for r in mine].count("quote") == 0
    assert [r["note"] for r in mine if r["kind"] == "note"] == [47, 48]


def test_headings_need_more_than_the_page_body_size(tmp_path):
    # Ariel: the file's body (10.5) is smaller than this page's (12.5): not all headings
    path = tmp_path / "bigger.pdf"
    doc = pymupdf.open()
    for i in range(4):
        body(Sheet(doc), 1400 + i, 120, size=9, pitch=11)
    s = Sheet(doc)
    s.text(150, 60, "I", 20)
    body(s, 1410, 60, size=12.5, pitch=17)
    save(doc, path)
    records, *_ = run(path)
    mine = [r for r in records if r["pages"][0] == 5]
    assert [r["kind"] for r in mine] == ["heading", "prose"]


def test_one_line_sticking_out_does_not_set_the_column_edge(tmp_path):
    path = tmp_path / "edge.pdf"
    doc = pymupdf.open()
    s = Sheet(doc)
    ws = words(1500, 80)
    s.para(ws[:40], x1=300, last_full=True)
    s.justify(ws[40:50], x0=X0, x1=360)              # wider than the column
    s.para(ws[50:], indent=0, x1=300)
    save(doc, path)
    records, *_ = run(path)
    assert len(records) == 1 and records[0]["text"] == " ".join(ws)


def test_indent_after_a_nearly_full_line_that_ends_a_sentence(tmp_path):
    # Manifiestos p. 51: "...acometido por la vida”." ends near the edge; "Habría..." indented
    path = tmp_path / "nearly.pdf"
    doc = pymupdf.open()
    s = Sheet(doc)
    s.para(words(1600, 40), last_full=True)
    last = " ".join(words(1601, 30))
    while width(last) > X1 - X0 - 12:
        last = last.rsplit(" ", 1)[0]
    s.line(last + ".")
    b = s.para(words(1602, 40))
    save(doc, path)
    records, *_ = run(path)
    assert len(records) == 2
    assert records[1]["text"] == " ".join(w for ln in b for w in ln)


def test_verse_with_space_indents_and_a_file_of_mostly_verse(tmp_path):
    # Altazor: one prose page, then verse pages with long lines; indents made of spaces
    path = tmp_path / "altazor.pdf"
    doc = pymupdf.open()
    body(Sheet(doc), 1700, 140)
    long_lines = ["Y al fondo de las olas un pez escucha el paso de los hombres",
                  "Silencio la tierra va a dar a luz un árbol",
                  "Mastican el universo que me atraviesa como un túnel",
                  "Un escalofrío de pájaro me sacude los hombros"]   # long, not full
    for i in range(3):
        s = Sheet(doc)
        for k in range(4):
            for ln in long_lines:
                s.line(ln if k or i == 0 else ln.replace(" ", f" {'aeo'[i]} ", 1))  # vary
    s = Sheet(doc)
    stair = ["Aruaru", "         urulario", "Lalilá", "          Montesur en lasurido",
             "          Montesol", "Lusponsedo solinario"]
    for ln in stair:
        s.line(ln)
    save(doc, path)
    records, *_ = run(path, nodict=True)
    verse = [r for r in records if r["kind"] == "verse"]
    assert all(ln in verse[0]["text"].split("\n") for ln in long_lines)
    assert verse[-1]["text"].endswith("\n" + "\n".join(stair))       # spaces kept


def test_verse_lines_ending_near_one_x_are_not_a_prose_column(tmp_path):
    # Altazor p. 25: 8 of 26 long verse lines end within 3 pt of x=400 and 5 run past it,
    # so the page passed as justified prose with a 400 pt column and the lines ending there
    # looked full (joined to the next line); the file's prose column (the preface) is wider
    path = tmp_path / "altazor25.pdf"
    doc = pymupdf.open()
    for i in range(3):
        body(Sheet(doc), 2100 + i, 140)
    s = Sheet(doc)
    ends = ([300, 296, 291, 286] + [270, 269.5, 269, 268.5, 268, 267.5, 270, 269]
            + [230 + 3 * k for k in range(13)])         # 25 long lines: 8 of them at one x
    lines = [s.line_to(2200 + k, x1) for k, x1 in enumerate(ends)]
    for ln in ("Eres más hermosa que el relincho de un potro", "en la montaña"):
        s.line(ln)                                 # a turnover: stays its own line
        lines.append(ln)
    save(doc, path)
    records, *_ = run(path, nodict=True)
    mine = [r for r in records if r["pages"][0] == 4]
    assert [r["kind"] for r in mine] == ["verse"]
    assert mine[0]["text"] == "\n".join(lines)


def test_verse_after_a_speaker_label_is_not_joined(tmp_path):
    # Tempestad p. 31: "Miranda: ...Te tuve lástima," then verse lines set further in
    path = tmp_path / "miranda.pdf"
    doc = pymupdf.open()
    s = Sheet(doc)
    s.para(words(1800, 60))
    s.y += 14
    s.text(X0, s.y, "Miranda:")
    s.line("…Te tuve lástima,", x=X0 + 100)
    verse = ["Me tomé el trabajo de hacerte hablar,", "Te enseñaba cada hora esto",
             "O aquello: cuando tú no conocías,", "Salvaje, tu propio significado,",
             "Y balbuceabas como los brutos,"]
    for ln in verse:
        s.line(ln, x=X0 + 35)
    save(doc, path)
    records, *_ = run(path)
    text = "\n".join(texts(records))
    assert "O aquello: cuando tú no conocías,\nSalvaje, tu propio significado," in text
    # the label line starts the verse unit; its first verse lines are not its wrapped tail
    # (the base-14 font renders the ellipsis as another glyph: not asserted)
    unit = [r for r in records if r["kind"] == "verse"][0]["text"]
    assert unit.startswith("Miranda: ") and "lástima,\nMe tomé el trabajo de hacerte hablar,\nTe" in unit


def test_last_line_of_an_indented_paragraph_is_not_a_hanging_indent(tmp_path):
    # Altazor p. 2: an indented first line, its continuation back at the margin, the next
    # paragraph indented again
    path = tmp_path / "outdent.pdf"
    doc = pymupdf.open()
    s = Sheet(doc)
    ws = words(1900, 60)
    a = s.para(ws[:20])
    b = s.para(ws[20:40])
    c = s.para(ws[40:])
    save(doc, path)
    records, *_ = run(path)
    assert [len(r["text"].split()) for r in records] == [20, 20, 20]


def test_per_file_rule_drops_a_fragment_before_it_joins_a_body_line(tmp_path, monkeypatch):
    # La raza cósmica p. 177: "DADDE MEXICO." in the left margin, on a body line's baseline
    path = tmp_path / "rule.pdf"
    doc = pymupdf.open()
    s = Sheet(doc)
    for k in range(6):
        if k == 2:
            s.text(5, s.y, "DADDE MEXICO.", 7)
        s.line(" ".join(words(2000 + k, 6)), x=X0 + 50)
    save(doc, path)
    monkeypatch.setitem(export.RULES, "d" * 16,
                        {"drop": {1: ("margin_rule", lambda ln: ln.x1 < X0 + 45)}})
    records, rows, *_ = run(path, id="d" * 64)
    assert "DADDE" not in " ".join(texts(records))
    assert {"reason": "margin_rule", "text": "DADDE MEXICO."} in rows[0]["dropped"]


def test_small_type_that_fills_a_page_is_its_body(tmp_path, monkeypatch, scan_font):
    # Ariel pp. 9-37: the prologue set at 10 pt / 13 pt pitch in a file whose body is
    # 12 pt / 17 pt; smaller and tighter than the file's lines, but it fills its pages
    path = tmp_path / "prologue.pdf"
    doc = pymupdf.open()
    for i in range(4):
        body(Sheet(doc), 2300 + i, 90, size=12, pitch=17)
    s = Sheet(doc)
    s.para(words(2310, 120), size=10, pitch=13)
    s.para(words(2311, 60), size=10, pitch=13)
    s.y += 12
    s.line("1 Una nota al pie de la página del prólogo.", size=8, pitch=10)
    save(doc, path)
    records, *_ = run(path)
    mine = [r for r in records if r["pages"][0] == 5]
    assert [r["kind"] for r in mine] == ["prose", "prose", "note"]
    assert mine[2]["note"] == 1 and mine[2]["text"].startswith("Una nota")

    # Unruh pp. 270+: the Notes section, set tighter (10.3 vs 12 pt pitch) and OCR'd at
    # sizes around the small-type threshold, so only 55-70% of its lines came out small
    # and every flip made a unit; with the endnotes rule its units are flagged
    path = tmp_path / "endnotes.pdf"
    doc = pymupdf.open()
    for i in range(4):
        s = Sheet(doc, font="cour")
        for k in range(12):
            s.line(" ".join(words(2400 + 10 * i + k, 7)), size=9)
    s = Sheet(doc, font="cour")
    s.text(170, 60, "Notes", 14)
    s.y = 90
    for k in range(5):
        s.justify(words(2500 + k, 8), x0=X0 + 10, size=8.0 if k in (1, 3) else 7.5, pitch=10.5)
        s.line(" ".join(words(2510 + k, 5)), size=8.0 if k in (0, 2) else 7.5, pitch=10.5)
    save(doc, path)
    monkeypatch.setitem(export.RULES, "b" * 16, {"endnotes": True})
    records, *_ = run(path, scan=True, lang="en", id="b" * 64)
    mine = [r for r in records if r["pages"][0] == 5]
    assert [r["kind"] for r in mine] == ["heading"] + ["prose"] * 5
    assert mine[0]["text"] == "Notes" and "endnotes" not in mine[0]["flags"]
    assert all("endnotes" in r["flags"] for r in mine[1:])


def test_page_with_long_footnotes_keeps_them(tmp_path, scan_font):
    # Bürger p. 110, Schwartz p. 174: body type on top, footnotes over half the page (and
    # the page's pitch is theirs): not a page set in small type, its notes stay notes
    path = tmp_path / "longnotes.pdf"
    doc = pymupdf.open()
    for i in range(4):
        s = Sheet(doc, font="cour")
        for k in range(12):
            s.line(" ".join(words(3200 + 10 * i + k, 7)), size=9)
    s = Sheet(doc, font="cour")
    for k in range(5):
        s.line(" ".join(words(3250 + k, 7)), size=9)
    s.y += 10
    s.line("12 Véase el capítulo tercero, donde se discuten las polémicas.", size=7.5, pitch=10)
    for k in range(5):
        s.line(" ".join(words(3260 + k, 8)), size=7.5, pitch=10)
    s.line("13 Cf. P. Bürger, Teoría de la vanguardia, p. 45.", size=7.5, pitch=10)
    for k in range(3):
        s.line(" ".join(words(3270 + k, 8)), size=7.5, pitch=10)
    save(doc, path)
    records, *_ = run(path, scan=True)
    assert [r["note"] for r in records if r["kind"] == "note"] == [12, 13]


def test_superscript_marker_read_as_two_digits(tmp_path):
    # Mexico p. 277: the marker "51" came as two superscript fragments, "[^5] [^1]"
    path = tmp_path / "fiftyone.pdf"
    doc = pymupdf.open()
    s = Sheet(doc)
    s.para(words(2600, 40), last_full=True)
    s.text(X0, s.y, "una forma poética paralítica.")
    xe = X0 + width("una forma poética paralítica.")
    s.text(xe, s.y - 4, "5", 6)
    s.text(xe + width("5", 6) + 1, s.y - 4, "1", 6)
    s.text(xe + 10, s.y, " Curiosamente, Gorostiza alaba a Novo.")
    s.y += PITCH
    s.para(words(2601, 30), indent=0)
    s.y += 18
    s.line("50 Prosa, 181.", size=8, pitch=10)
    s.line('51 "La poesía actual de México", en Prosa, 178.', size=8, pitch=10)
    save(doc, path)
    records, *_ = run(path)
    prose = " ".join(texts(records, "prose"))
    assert "paralítica.[^51] Curiosamente" in prose
    assert [r["note"] for r in records if r["kind"] == "note"] == [50, 51]


def test_fully_letter_spaced_line_is_measured_against_the_page(tmp_path):
    # Mexico p. 277: "t o m a d o s c o m o e j e m p l o s d e la s e g u n d a" (letter
    # gaps 1.2-1.5 pt, word gaps 3.1-4.9 pt) on a page whose ordinary word gap is 2.5 pt
    path = tmp_path / "spacedline.pdf"
    doc = pymupdf.open()
    s = Sheet(doc)
    for k in range(6):
        s.spaced(words(2700 + k, 9), [2.5] * 8, size=8, pitch=10)
    s.spaced("t o m a d o s c o m o e j e m p l o s d e la s e g u n d a .".split(),
             [1.4] * 6 + [4.0] + [1.4] * 3 + [3.5] + [1.3] * 7 + [3.8, 1.3, 3.1, 2.7]
             + [1.3] * 9 + [3.4, 4.0], size=8, pitch=10)
    for k in range(6):
        s.spaced(words(2710 + k, 9), [2.5] * 8, size=8, pitch=10)
    save(doc, path)
    records, *_ = run(path)
    assert "tomados como ejemplos de la segunda" in " ".join(texts(records))


def test_per_file_tight_gap_rule_joins_without_the_word_list(tmp_path, monkeypatch):
    # Manifiestos (Batang 11 pt): a 2 pt space inside words; real word gaps >= 6.8 pt
    path = tmp_path / "tight.pdf"
    doc = pymupdf.open()
    s = Sheet(doc)
    for k in range(4):
        s.spaced(words(2800 + k, 7), [7.0] * 6, size=11, pitch=15)
    s.spaced("com o m ovim iento el runrunism o es la síntesis".split(),
             [1.8, 7.0, 2.0, 2.0, 6.9, 7.1, 2.1, 7.0, 6.8, 7.2], size=11, pitch=15)
    s.spaced("la mayor riqueza de la rima".split(), [7.0, 3.2, 7.0, 7.0, 7.0], size=11, pitch=15)
    save(doc, path)
    records, *_ = run(path)
    t = " ".join(texts(records))
    assert "com o movimiento el runrunismo es" in t     # the word list alone: "como" is not
    #                                                   clearly more frequent than "com" + "o"
    monkeypatch.setitem(export.RULES, "c" * 16, {"tight_gap": True})
    records, _, _, _, counts = run(path, id="c" * 64)
    t = " ".join(texts(records))
    assert "como movimiento el runrunismo es la síntesis" in t
    assert "la mayor riqueza de la rima" in t
    assert counts["tight_joins"] == 1          # "com o"; the others the word list already joins


def test_suspected_handwriting_is_flagged_not_dropped(tmp_path, scan_font):
    # Unruh: handwritten marginalia read by Tesseract come out as headings or short units
    # ("b DENADTA| Bunives de Ralz GUNOCTA"); avant-garde sound-words look the same to a
    # word list (Schwartz "Catitú Catitú"), so the unit stays, flagged, and the .txt marks
    # it; a real foreign-language heading is not flagged
    path = tmp_path / "marginalia.pdf"
    doc = pymupdf.open()
    s = Sheet(doc, font="cour")
    s.text(X0, 40, "b DENADTA| Bunives de Ralz GUNOCTA", 13)
    s.y = 70
    for k in range(6):
        s.line(" ".join(words(3000 + k, 7)), size=9)
    s.y += 18
    s.line("Qresho = beatralidal,", size=9)
    s.y += 18
    s.text(X0, s.y, "Prefácio interessantíssimo", 13)
    s.y += 24
    for k in range(6):
        s.line(" ".join(words(3010 + k, 7)), size=9)
    save(doc, path)
    records, rows, _, txt, counts = run(path, scan=True, lang="en")
    by_text = {r["text"]: r for r in records}
    assert by_text["b DENADTA| Bunives de Ralz GUNOCTA"]["kind"] == "heading"
    assert "suspect_ocr" in by_text["b DENADTA| Bunives de Ralz GUNOCTA"]["flags"]
    assert "suspect_ocr" in by_text["Qresho = beatralidal,"]["flags"]
    assert "suspect_ocr" not in by_text["Prefácio interessantíssimo"]["flags"]
    assert rows[0]["suspect_ocr"] == ["b DENADTA| Bunives de Ralz GUNOCTA",
                                      "Qresho = beatralidal,"]
    assert not [d for d in rows[0]["dropped"] if d["reason"] == "marginalia"]
    assert counts["suspect_ocr"] == ["p. 1: b DENADTA| Bunives de Ralz GUNOCTA",
                                     "p. 1: Qresho = beatralidal,"]
    assert "## [?] b DENADTA| Bunives de Ralz GUNOCTA" in txt
    assert "\n[?] Qresho = beatralidal,\n" in txt and "[?] Prefácio" not in txt
    records, *_ = run(path, scan=True, lang="en", nodict=True)      # no dictionary: no flag
    assert not any("suspect_ocr" in r["flags"] for r in records)
    monkeypatch_font = tl.TESSERACT_FONTS
    tl.TESSERACT_FONTS = ("GlyphLessFont",)                          # born-digital: no flag
    try:
        records, *_ = run(path, lang="en")
    finally:
        tl.TESSERACT_FONTS = monkeypatch_font
    assert not any("suspect_ocr" in r["flags"] for r in records)


def test_dash_tilde_mark_and_a_hyphen_never_ends_a_unit(tmp_path, scan_font):
    # Unruh p. 237 "—~As early as 1882"; p. 278 "Tulio Halperin Don-" / "ghi in Historia"
    # (the second line OCR'd at a size that flipped the small-type flag)
    path = tmp_path / "hyphen.pdf"
    doc = pymupdf.open()
    s = Sheet(doc, font="cour")
    for k in range(8):
        s.line(" ".join(words(3100 + k, 7)), size=9)
    s.line("—~As early as 1882, Ernest Renan cautioned in his address", size=9)
    for k in range(4):
        s.line(" ".join(words(3110 + k, 7)), size=9)
    s.line("Skidmore and Peter H. Smith in Modern Latin America; Tulio Halperin Don-",
           size=7.5)
    s.line("ghi in Historia contemporánea de América Latina (280-355); and, for", size=8.2)
    s.line("connections between major historical trends.", size=7.5)
    save(doc, path)
    records, *_ = run(path, scan=True, lang="en")
    allt = " | ".join(r["text"] for r in records)
    assert "As early as 1882" in allt and "—~" not in allt
    assert "Tulio Halperin Donghi in Historia" in allt


def test_speaker_label_starts_the_verse_unit(tmp_path):
    # Tempestad p. 114: "Próspero: Es un demonio, nacido demonio," at the margin, the rest of
    # the speech set further in, then the act/scene reference; 264 speeches broke at the label
    path = tmp_path / "prospero.pdf"
    doc = pymupdf.open()
    s = Sheet(doc)
    s.para(words(2900, 60))
    s.y += 14
    label = "Próspero: Es un demonio, nacido demonio,"
    s.line(label)
    verse = ["Y en su naturaleza", "La educación no puede hacer mella.",
             "O aquello: cuando tú no conocías,", "Todos, están perdidos,",
             "Más va corroyendo el cáncer su mente."]
    for ln in verse:
        s.line(ln, x=X0 + 35)
    s.line("(I, II, 355 – 360)", x=X0 + 70)
    s.y += 14
    s.para(words(2901, 40))
    s.line("Ariel: Voy, voy. Sale.")
    s.line("Próspero: Bien hecho, pajarillo")
    s.line("Mío. Conserva aún", x=X0 + 35)
    s.line("Tu forma invisible.", x=X0 + 35)
    save(doc, path)
    records, *_ = run(path)
    kinds = [(r["kind"], r["text"]) for r in records]
    assert ("verse", "\n".join([label] + verse)) in kinds
    assert [k for k, t in kinds if t.startswith("(I, II, 355")] == ["prose"]   # the reference:
    #                                                 its own unit (the dash glyph is the font's)
    assert ("prose", "Ariel: Voy, voy. Sale.") in kinds
    assert ("verse", "Próspero: Bien hecho, pajarillo\nMío. Conserva aún\nTu forma invisible.") in kinds
