# SPDX-License-Identifier: AGPL-3.0-or-later
"""pipeline.cleanup: the pure text rules of the text export (labels, head keys, line
joins, letter-spaced runs, the "m"-split glitch, markers, scan artefacts, dot leaders)."""

import pytest

pytest.importorskip("wordfreq")

from gpdf.pipeline import cleanup as c  # noqa: E402


# ---------------------------------------------------------------------------- labels, heads
@pytest.mark.parametrize("text, want", [
    ("74", (74, "arabic", "74")), ("- 12 -", (12, "arabic", "12")),
    ("xxxv", (35, "roman", "xxxv")), ("XIV", (14, "roman", "XIV")),
    ("Página 3 de 92", (3, "arabic", "3")), ("Page 2 of 16", (2, "arabic", "2")),
    ("Tn", None), ("CANTO I", None), ("1927.", (1927, "arabic", "1927")), ("Mi", None)])
def test_label_line(text, want):
    assert c.label_line(text) == want


def test_head_parts():
    assert c.head_parts("74 | INTRODUCCIÓN") == ("INTRODUCCIÓN", [(74, "arabic", "74")], [])
    assert c.head_parts("BRASIL (REVISTAS) | 165") == (
        "BRASIL (REVISTAS)", [(165, "arabic", "165")], [])
    assert c.head_parts("CANTO VII") == ("CANTO", [(7, "roman", "VII")], [])
    assert c.head_parts("La revolución de entremés (II)") == (
        "La revolución de entremés (II)", [], [(2, "roman", "II")])
    core, edge, inner = c.head_parts("Nota Extensa 6. Un despiste dramático")
    assert edge == [] and inner == [(6, "arabic", "6")]


def test_similar_heads_fold_accents_and_ocr_variants():
    assert c.similar("Bibliografia", "Bibliografìa")
    assert c.similar("INTRODUCCIÓN", "Introduccion")
    assert not c.similar("Antología crítica", "Francine Masiello")


# ---------------------------------------------------------------------------- line joins
@pytest.mark.parametrize("a, b, text, how", [
    ("la transfor-", "mación social", "la transformación social", c.JOINED),
    ("el diálogo luso-", "hispánico de", "el diálogo luso-hispánico de", c.KEPT),
    ("sus aportes teórico-", "críticas", "sus aportes teórico-críticas", c.KEPT),
    ("la Avant-", "garde histórica", "la Avant-garde histórica", c.KEPT),
    ("pp. 57-", "79.", "pp. 57-79.", c.KEPT),
    ("entre 1920-", "1940", "entre 1920-1940", c.KEPT),
    ("Wentzlaff-", "Eggebert", "Wentzlaff-Eggebert", c.KEPT),
    ("maes­", "tro, á quien", "maestro, á quien", c.SOFT),
    ("pp. 39­", "69", "pp. 39-69", c.KEPT),
    ("el de insti.", "tución arte", "el de institución arte", c.DOT),
    ("y así termina.", "luego vino", "y así termina. luego vino", c.SPACE),
    ("una palabra", "otra", "una palabra otra", c.SPACE),
])
def test_join_lines(a, b, text, how):
    assert c.join_lines(a, b, "es") == (text, how)


@pytest.mark.parametrize("a, b, lang, joined", [
    ("Hui-", "dobro’s poetry", "en", "Huidobro’s poetry"),     # names the lists split up
    ("Jar-", "nés", "es", "Jarnés"), ("re-", "cobrados", "es", "recobrados"),
    ("neo-", "criollo", "es", "neocriollo"), ("Liza-", "so", "es", "Lizaso"),
    ("teórico-", "críticas", "es", "teórico-críticas"), ("Avant-", "garde", "en", "Avant-garde"),
])
def test_compounds_need_two_solid_halves(a, b, lang, joined):
    assert c.join_lines(a, b, lang)[0] == joined


def test_join_lines_without_the_dictionary():
    assert c.join_lines("nuestros ma-", "nantiales", "es", nodict=True)[0] == "nuestros manantiales"
    assert c.join_lines("diálogo luso-", "hispánico", "es", nodict=True)[0] == "diálogo lusohispánico"
    assert c.join_lines("el de insti.", "tución", "es", nodict=True) == ("el de insti. tución",
                                                                        c.SPACE)
    assert c.join_lines("pp. 57-", "79", "es", nodict=True)[0] == "pp. 57-79"


def test_soft_hyphens_inside_a_line():
    assert c.soft_hyphens("pala­bra 39­69") == "palabra 39-69"


# ---------------------------------------------------------------------------- letter-spacing
def test_spaced_runs():
    assert c.spaced_runs("la C h il e de".split()) == [(0, 6)]   # "la", "de" too
    assert c.spaced_runs("y h e t e n d i d o".split()) == [(0, 10)]
    assert c.spaced_runs("una palabra normal".split()) == []
    assert c.spaced_runs("to d a s, y".split()) == [(0, 4)]          # the comma ends it


def test_split_gaps():
    assert c.split_gaps([1.2, 1.4, 1.5, 3.1, 4.9]) == pytest.approx((1.5 * 3.1) ** 0.5)
    assert c.split_gaps([3.0, 3.2, 3.5]) is None


def test_respace_by_gaps_keeps_one_letter_words():
    # "Yo h e t e n d i d o": a word gap after "Yo" and after "h e"
    toks = "Yo h e t e n d i d o".split()
    gaps = [6.0, 1.5, 6.0, 1.5, 1.4, 1.6, 1.5, 1.5, 1.4]
    assert c.respace(toks, gaps, "es")[0] == ["Yo", "he", "tendido"]
    toks = "dijo y a l l í".split()
    gaps = [6.0, 6.2, 1.5, 1.4, 1.6]
    out, counts = c.respace(toks, gaps, "es")
    assert out == ["dijo", "y", "allí"] and counts["geometry"] == 1
    # the geometry also runs without the dictionary
    assert c.respace(toks, gaps, "es", nodict=True)[0] == ["dijo", "y", "allí"]


def test_respace_needs_gaps_below_the_lines_word_gap():
    # one wide gap after ")," makes a jump, but the run's gaps are ordinary word gaps
    toks = "el citado), o de la consideración fisiológica o sociológica del parque".split()
    gaps = [3.0, 7.0, 2.9, 3.1, 3.0, 3.2, 2.9, 3.1, 3.0, 2.8]
    assert c.respace(toks, gaps, "es")[0] == toks
    toks = "en la revista A m a u t a es ecléctica en Arte con todos los credos".split()
    gaps = [7.0, 8.0, 9.0, 1.9, 2.0, 1.8, 2.1, 2.0, 8.5, 5.0, 8.0, 7.5, 9.0, 8.0, 7.0]
    assert c.respace(toks, gaps, "es")[0][:5] == ["en", "la", "revista", "Amauta", "es"]
    toks = "1920: J u a n M a r t ín . Z a in".split()
    gaps = [6.0, 1.5, 1.5, 1.5, 6.0, 1.5, 1.5, 1.5, 1.5, 1.4, 6.0, 1.5, 1.5]
    assert c.respace(toks, gaps, "es")[0] == ["1920:", "Juan", "Martín.", "Zain"]


def test_respace_on_a_fully_spaced_line_takes_the_pages_word_gap():
    # Mexico p. 277: when most of the line is letter-spaced, the line's own median gap IS
    # the letter gap; the page's ordinary word gap (from lines without runs) decides
    toks = "t o m a d o s c o m o e j e m p l o s d e la s e g u n d a .".split()
    gaps = [1.5, 1.5, 1.5, 1.5, 1.5, 1.5, 4.0, 1.5, 1.5, 1.5, 3.5, 1.2, 1.2, 1.2, 1.2, 1.2, 1.2,
            1.2, 3.8, 1.3, 3.1, 2.7, 1.2, 1.2, 1.2, 1.2, 1.2, 1.2, 1.2, 1.2, 1.2, 3.4, 4.0]
    assert c.respace(toks, gaps, "es")[0] == toks                      # nothing to go by
    out, counts = c.respace(toks, gaps, "es", word_gap=2.5)
    assert out == ["tomados", "como", "ejemplos", "de", "la", "segunda."]
    assert counts["geometry"] == 1
    toks = "del p o e m a c o m o canto, desarrollo".split()
    gaps = [2.2, 1.5, 1.5, 1.5, 1.5, 3.3, 1.4, 1.4, 1.4, 3.2, 3.0]
    assert c.respace(toks, gaps, "es", word_gap=2.5)[0] == ["del", "poema", "como", "canto,",
                                                            "desarrollo"]
    # the page's gap keeps the guard: ordinary word gaps on the line are not joined
    toks = "el citado), o de la consideración fisiológica o sociológica del parque".split()
    gaps = [3.0, 7.0, 2.9, 3.1, 3.0, 3.2, 2.9, 3.1, 3.0, 2.8]
    assert c.respace(toks, gaps, "es", word_gap=3.0)[0] == toks
    # a loosely justified line on the same page (p. 277 "a N o v o y a Pellicer": word gaps
    # 4.3-5.3 pt, letter gaps 1.8) keeps its own, larger reference
    toks = "rescata aquí a N o v o y a Pellicer, los dos condenados".split()
    gaps = [4.4, 4.5, 4.9, 1.8, 1.8, 1.8, 5.3, 4.5, 4.7, 4.3, 4.4, 4.6]
    assert c.respace(toks, gaps, "es", word_gap=2.5)[0] == (
        "rescata aquí a Novo y a Pellicer, los dos condenados".split())


def test_respace_veto_on_a_tiny_gap_between_real_words():
    toks = "ni yo ni fosa ni hoyo".split()
    gaps = [2.5, 4.0, 1.2, 5.6, 4.4]
    trace = []
    out, counts = c.respace(toks, gaps, "es", trace=trace)
    assert out == toks and counts["undone"] == 1 and counts["geometry"] == 0
    assert trace == [("undone", "ni yo ni fosa", "ni yo ni fosa")]
    assert c.respace(toks, gaps, "es", nodict=True)[0] == ["ni", "yo", "nifosa", "ni", "hoyo"]


def test_veto_ignores_one_letter_pieces():
    # every letter is in wordfreq's lists: a join with a one-letter piece is never undone,
    # even when the result is not a word ("x a l m e" -> "xalme")
    toks = "dijo x a l m e con voz".split()
    gaps = [6.0, 2.0, 2.0, 2.0, 2.0, 6.0, 6.0]
    trace = []
    out, counts = c.respace(toks, gaps, "es", trace=trace)
    assert out == ["dijo", "xalme", "con", "voz"] and counts["geometry"] == 1
    assert trace[0][0] == "geometry"
    toks = "dijo u o a con voz".split()           # Spanish one-letter words, "uoa" unknown
    assert c.respace(toks, [6.0, 2.0, 2.0, 6.0, 6.0], "es")[0] == ["dijo", "uoa", "con",
                                                                    "voz"]


def test_respace_a_letter_spaced_phrase():
    toks = "L o q u e G o r o s t i z a l l a m a".split()
    gaps = [1.3, 4.0, 1.2, 1.4, 4.2, 1.3, 1.5, 1.2, 1.4, 1.3, 1.2, 1.4, 1.3, 4.5, 1.3, 1.2, 1.4, 1.5]
    assert c.respace(toks, gaps, "es")[0] == ["Lo", "que", "Gorostiza", "llama"]


def test_respace_leaves_ordinary_short_words():
    toks = "se lo da a él".split()
    assert c.respace(toks, [3.0, 3.1, 2.9, 3.0], "es")[0] == toks       # no jump, not a word
    assert c.respace(toks, [None] * 4, "es")[0] == toks
    toks = "contemplación y a la masturbación.".split()                # "yala" is listed
    assert c.respace(toks, [4.05, 3.0, 2.96, 4.04], "es")[0] == toks
    assert c.respace(toks, [None] * 4, "es")[0] == toks


def test_respace_without_gaps_uses_the_word_list():
    toks = "en C h il e hoy".split()
    out, counts = c.respace(toks, [None] * 5, "es")
    assert out == ["en", "Chile", "hoy"] and counts["dictionary"] == 1
    assert c.respace(toks, [None] * 5, "es", nodict=True)[0] == toks
    toks = "a i a a i i i i o".split()                                 # Altazor: evenly spaced
    assert c.respace(toks, [3.0] * 8, "es", nodict=True)[0] == toks


def test_msplit_joins_real_words_on_small_gaps():
    toks = "es u n m ovim iento hom bres de la cósm ico".split()
    gaps = [9.0, 1.9, 9.0, 1.9, 2.0, 8.0, 2.1, 9.0, 8.5, 7.9, 1.8]
    out, joins = c.msplit(toks, gaps, "es")
    assert out == ["es", "un", "movimiento", "hombres", "de", "la", "cósmico"]
    assert joins == 5
    toks = "una palabra otra".split()
    assert c.msplit(toks, [7.0, 7.5], "es") == (toks, 0)                # no small cluster
    toks = "Bremer / Jochen ( 1 8 7 4 ) Inter -".split()                 # letters only
    assert c.msplit(toks, [7.0, 1.0, 1.0, 7.0, 1.0, 1.0, 1.0, 1.0, 1.0, 7.0, 1.0], "es")[1] == 0


def test_tight_joins_by_the_gap_alone_with_the_undo_guard():
    # Manifiestos (Batang): a space of 1.7-2.5 pt inside words ("com o", "runrunism o",
    # "m ovim iento"), at 11 pt and at 14.5 pt alike; the word list is not asked
    # ("runrunismo" is not in it)
    toks = "com o m ovim iento el runrunism o consta de seis colum nas".split()
    gaps = [1.8, 7.0, 2.0, 2.0, 6.9, 7.1, 2.1, 7.0, 6.8, 7.2, 7.0, 2.0]
    out, joins = c.tight_joins(toks, gaps, 11, "es")
    assert out == ["como", "movimiento", "el", "runrunismo", "consta", "de", "seis", "columnas"]
    assert joins == 5
    # a piece the lists hold at a low frequency ("ática" 2.4, "iento" 1.3) is a fragment, so
    # the rare joined word is not asked for; two solid words whose join is unknown stay
    toks = "paradigm ática acomodam iento tim onel im perm".split()
    gaps = [2.0, 7.0, 2.0, 7.0, 2.0, 7.0, 2.0]
    assert c.tight_joins(toks, gaps, 11, "es")[0] == ["paradigmática", "acomodamiento",
                                                      "timonel", "im", "perm"]
    toks = "cósm ica u m b ilical in fin ito".split()                  # chains
    assert c.tight_joins(toks, [1.75, 7.0, 1.67, 2.5, 1.66, 7.0, 2.0, 2.1], 11, "es")[0] == [
        "cósmica", "umbilical", "infinito"]
    # real word gaps on tightly justified 14.5 pt lines go down to 2.8 pt (0.19 em): the cap
    # in pt keeps them; "adm itirlo" (2.44 pt) still joins
    toks = "es la mayor riqueza de adm itirlo!... y las".split()
    gaps = [3.36, 8.0, 3.18, 8.0, 8.0, 2.44, 8.0, 3.47]
    assert c.tight_joins(toks, gaps, 14.5, "es") == (
        "es la mayor riqueza de admitirlo!... y las".split(), 1)
    # the undo guard: real words set with overlapping or near-zero gaps stay apart; a dash
    # never joins, a quote does
    toks = "que anda Favorables París-Poemas santana - andrade « dicen»".split()
    gaps = [-0.45, 8.0, 0.78, 8.0, 2.0, 2.1, 8.0, 2.49]
    assert c.tight_joins(toks, gaps, 12, "es") == (
        "que anda Favorables París-Poemas santana - andrade «dicen»".split(), 1)
    assert c.tight_joins(toks, [None] * 8, 12, "es") == (toks, 0)        # no gaps: nothing


# ---------------------------------------------------------------------------- notes, markers
def test_note_start():
    assert c.note_start("24. B. Brecht, «Der Dreigroschenprozeß»") == (24, "24.", False)
    assert c.note_start("2 Conocida es la estructura") == (2, "2", False)
    assert c.note_start("* Véase el capítulo") == (None, "*", False)
    assert c.note_start("?1 El segundo texto") == (1, "?1", True)
    assert c.note_start("$8 Fransisqo Chugiwanka") == (8, "$8", True)
    assert c.note_start("% Boletín Titikaka") == (None, "%", True)
    assert c.note_start("1925, p. 1. Ver también") is None
    assert c.note_start("Martín Fierro, 3") is None


def test_note_start_glued_to_the_first_word():
    # the Mexico anthology: "2Una notable excepción", "20Mitre, p. 676.", "7H texto" (OCR
    # for "El texto"); a year or a digit followed by lowercase is not a marker
    assert c.note_start("2Una notable excepción: la de") == (2, "2", False)
    assert c.note_start("14Sobre el mismo poema, véase") == (14, "14", False)
    assert c.note_start("20Mitre, p. 676.") == (20, "20", False)
    assert c.note_start("8Véase mi artículo") == (8, "8", False)
    assert c.note_start("7H texto dice") == (7, "7", False)
    assert c.note_start('47"La poesía actual", en Prosa') == (47, "47", False)
    assert c.note_start("3¿Quién lo dijo?") == (3, "3", False)
    assert c.note_start("1925Véase") is None
    assert c.note_start("2a edición") is None
    assert c.note_start("2101ivio Jiménez, p. 41.") is None


def test_link_markers():
    text = "las vanguardias1 y luego pienso.”45 el estado26— en p.5 y 1927 pp.3 vol. 13.1"
    out, linked, left = c.link_markers(text, {1, 45})
    assert out == ("las vanguardias[^1] y luego pienso.”[^45] el estado26— en p.5 y 1927 "
                   "pp.3 vol. 13.1")
    assert (linked, left) == (2, 1)


def test_speaker_ornament_url_index():
    assert c.speaker("GALÁN: Es la contrahecha")
    assert c.speaker("REINA BIZANTINA (moviendo desalentadamente los brazos): Ya voy")
    assert c.speaker("Miranda:")
    assert not c.speaker("la ciudad española, trazando una clara analogía: «")
    # Tempestad, inside a speech: a one-letter first word, a comma or a period in the "name"
    assert not c.speaker("O aquello: cuando tú no conocías,")
    assert not c.speaker("Inquisiciones, concluyendo: «No.")
    assert not c.speaker("De mago...Sí. Así: [Deja su manto en el suelo.]")
    assert c.speaker("Próspero: Es un demonio, nacido demonio,")
    assert c.reference("(I, II, 355 – 360)") and c.reference("(IV, I, 190)")
    assert c.reference("(I, II, 336 -338)") and not c.reference("(1929; The Sentimental)")
    assert not c.reference("Y en su naturaleza")
    assert c.ornament("*****") and c.ornament("* * *") and not c.ornament("*")
    assert c.url("http://www.scielo.org.mx/scielo.php?script=sci_arttext")
    assert c.index_heading("ÍNDICE") and c.index_heading("Índice general")
    assert c.ends_in_number("Borges, Jorge Luis, 12, 45") and c.ends_in_number("Prólogo … 7")
    assert not c.ends_in_number("Silencio la tierra va a dar a luz un árbol")
    assert not c.ends_in_number("Mastican el universo que me atraviesa como un túnel")
    assert not c.ends_in_number("las palabras de 1920s")
    assert c.ends_in_number("Advertencia 31") and c.ends_in_number("Alonso, Cecilio 1091")
    assert not c.ends_in_number("México: Mundial, 1935.")          # bibliography entries
    assert not c.ends_in_number("Madrid: Cátedra, 1983, pp. 71-90.")
    assert not c.index_heading("Índice de la modernidad en la poesía de la época")


# ---------------------------------------------------------------------------- leaders, artefacts
def test_dot_leaders():
    assert c.leaders("Prólogo ........o.ooooomommmoommm.... 175") == "Prólogo … 175"
    assert c.leaders("Bibliografía . . . . . . 9") == "Bibliografía … 9"
    assert c.leaders("Capítulo III ......»«::") == "Capítulo III …"
    assert c.leaders("una frase... que sigue") == "una frase... que sigue"
    assert c.has_leader("Índice ........ 3") and not c.has_leader("fin...")


def test_unruh_artefacts():
    assert c.symbol_only("_ ~ |") and c.symbol_only("—") and not c.symbol_only("a _")
    text, counts, reported = c.artefacts("_ Language may_be considered an actor ~")
    assert text == "Language may be considered an actor"
    assert counts == {"edge_mark": 2, "underscore": 1}
    assert c.edge_mark("—~") and c.edge_mark("~—") and not c.edge_mark("—")
    assert c.artefacts("—~As early as 1882")[0] == "As early as 1882"
    assert c.artefacts("~— Ernest Renan")[0] == "Ernest Renan"
    assert c.lead_mark("—~As early") == ("As early", 1) and c.lead_mark("Renan") == ("Renan", 0)
    text, counts, reported = c.artefacts("in culture but also-a-protagonist in the history")
    assert text == "in culture but also-a-protagonist in the history"
    assert reported == ["also-a-protagonist"]
    text, _, reported = c.artefacts("as.a matter of fact language—when spoken")
    assert text == "as.a matter of fact language—when spoken" and reported == ["as.a"]
