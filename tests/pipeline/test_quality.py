# SPDX-License-Identifier: AGPL-3.0-or-later
"""The survey's text-quality scorer and language votes (wordfreq word lists)."""

import pytest

pytest.importorskip("wordfreq")

from gpdf.pipeline import quality as q  # noqa: E402

ES = ("la ciudad era grande y los poetas jóvenes escribían en el café sobre la vida "
      "moderna, el arte nuevo y la ruptura con las formas antiguas de la poesía que todos "
      "leían con atención durante las noches largas del invierno.")
EN = ("the young poets of the city wrote about modern life in the small cafe near the "
      "river, and their friends read every new poem aloud during the long winter nights "
      "while the old critics complained about the noise.")
CA = ("la ciutat era molt gran i els poetes joves escrivien sobre la vida moderna, l'art "
      "nou i la ruptura amb les formes antigues de la poesia que tothom llegia amb atenció "
      "durant les nits llargues de l'hivern.")
FR = ("la ville était très grande et les jeunes poètes écrivaient sur la vie moderne, "
      "l'art nouveau et la rupture avec les formes anciennes de la poésie que tout le monde "
      "lisait pendant les longues nuits d'hiver.")


def extra(text, lang="es", base=ES):
    """The score of ``text`` added to a clean base page, minus the base page's."""
    a, b = q.score_text(base + "\n" + text, lang), q.score_text(base, lang)
    return {k: getattr(a, k) - getattr(b, k)
            for k in ("words", "lower", "suspect", "merged", "spaced", "digit", "chaos",
                      "repeated", "caps", "caps_unknown", "cap_junk")}


def test_clean_base_pages_have_no_suspect_words():
    for text, lang in ((ES, "es"), (EN, "en"), (CA, "ca"), (FR, "fr")):
        s = q.score_text(text, lang)
        assert s.lower >= q.MIN_SCORED and s.suspect == 0 and s.cls == q.GOOD, (lang, s)


# ---------------------------------------------------------------------------- prep
def test_soft_hyphen_joined_across_a_line_break_and_between_letters():
    text, _ = q.prepare("la pala­\nbra y la conti­  \n  nuación, ca­sa")
    assert text == "la palabra y la continuación, casa"
    assert extra("pala­\nbra conti­nuación")["suspect"] == 0


def test_soft_hyphen_between_digits_keeps_a_hyphen():
    assert q.prepare("pp. 39­69")[0] == "pp. 39-69"


def test_letter_spaced_run_rejoined_and_counted():
    text, runs = q.prepare("dijo: h e t e n d i d o mucho, a l m a mía")
    assert text == "dijo: hetendido mucho, alma mía" and runs == 2
    assert extra("el p o e m a nuevo")["spaced"] == 1
    assert q.prepare("a y o")[1] == 0                         # three single letters: words


def test_letter_spaced_rejoin_keeps_a_one_letter_word():
    assert q.prepare("dijo y h e t e n d i d o mucho", "es")[0] == "dijo y hetendido mucho"
    assert q.prepare("y a l l í estaba", "es")[0] == "y allí estaba"
    assert q.prepare("su a l m a, el o c a s o", "es")[0] == "su alma, el ocaso"   # words
    assert q.rejoin_spaced("p o e m a s y", "es") == "poemas y"           # trailing
    assert extra("y a l l í")["lower"] == extra("y allí")["lower"]


def test_line_end_hard_hyphen():
    assert q.join_hyphens("la con-\ntinuación", "es") == "la continuación"
    assert q.join_hyphens("el franco-\nalemán", "es") == "el franco-alemán"   # both known
    assert q.join_hyphens("de qzx-\nwvb", "es") == "de qzxwvb"                # unknown part


# ---------------------------------------------------------------------------- suspects
def test_inner_digit_counted_but_footnote_markers_not():
    e = extra("en jap6n y trav6s de las vanguardias1 del siglo")
    assert e["digit"] == 2 and e["suspect"] == 2
    e = extra("Jap6n de Cuculc4n")
    assert e["digit"] == 2 and e["suspect"] == 2 and e["lower"] == 1     # only "de"


def test_edge_digit_misreads_counted():
    e = extra("dijo e1 poeta que 10s versos a1 final y 1a casa sin0 nada")
    assert e["digit"] == 4 and e["suspect"] == 4          # e1, 10s, a1, 1a
    assert e["lower"] == extra("dijo el poeta que los versos al final y la casa sin nada")[
        "lower"]                                           # "sin0": "sin" + a footnote
    assert q.digit_word("10s", "es") == "los" and q.digit_word("e1", "es") == "el"


def test_footnote_markers_and_years_not_counted():
    e = extra("las vanguardias1 del poema5 en los 1920s y 1930")
    assert e["digit"] == 0 and e["suspect"] == 0
    assert q.digit_word("vanguardias1", "es") is None     # "vanguardiasl" is not a word
    assert q.digit_word("1920s", "es") is None


def test_index_codes_and_ordinals_not_counted():
    e = extra("Andrade, Raúl E38, 44; E48, 46; P8, 64; P86, 73; la 4a y la 5a parte")
    assert e["digit"] == 0 and e["suspect"] == 0           # "PB", "aa", "sa": junk


def test_case_chaos_counted():
    e = extra("dUjxJLcjíMyix opouyIy")
    assert e["chaos"] == 2 and e["suspect"] == 2


def test_merged_words_counted():
    e = extra("porqueel mayorde unacivilización")
    assert e["merged"] == 3 and e["suspect"] == 3
    assert not q.merged("civilización", "es")


def test_foreign_passages_are_not_suspect():
    german = "und die nacht ist kalt, ich weiß nicht, was soll es bedeuten"
    french = "la poésie est une chose qui demeure dans le cœur des hommes"
    assert extra(german)["suspect"] == 0
    assert extra(french)["suspect"] == 0


def test_capitalised_unknown_names_are_not_in_the_rate():
    e = extra("Qzxwvb Wvbqzx Xqzwvb")
    assert e["suspect"] == 0 and e["lower"] == 0
    assert e["caps"] == 3 and e["caps_unknown"] == 3


def test_cap_unknown_is_a_rate_of_its_own():
    e = extra("Qzxwvb AND THE il oi AT Xqzwvb Calderón la casa de")
    assert e["suspect"] == 0                       # not in suspect %
    assert e["cap_junk"] == 6                      # Qzxwvb Xqzwvb AND THE il AT
    s = q.score_text(ES + " Qzxwvb AND THE il", "es")
    assert s.cap_unknown_pct == round(100 * 4 / s.lower, 2) and s.pct == 0.0
    assert s.row()["cap_unknown_pct"] == s.cap_unknown_pct
    assert q.summarise([s])["cap_unknown_pct"] == s.cap_unknown_pct
    assert not any(q.short_foreign(w, "es") for w in "de la el en un se lo va da".split())
    assert q.short_foreign("the", "es") and not q.short_foreign("the", "en")


def test_missing_accent_rate():
    e = extra("tambien su corazon habia sido")
    assert e["suspect"] == 0                       # the lists hold these web spellings
    s = q.score_text(ES + " tambien su corazon", "es")
    assert s.accent == 2 and s.missing_accent_pct == round(200 / s.lower, 2)
    assert s.row()["missing_accent_pct"] == s.missing_accent_pct
    assert q.summarise([s])["missing_accent_pct"] == s.missing_accent_pct
    for word in ("fué", "á", "dió", "mas", "aun", "esta", "solo", "el", "como", "casa"):
        assert not q.missing_accent(word, "es"), word      # period spelling and real words
    assert q.score_text(ES, "es").accent == 0
    assert q.score_text(ES + " fué á dió", "es").accent == 0


def test_period_orthography_is_known():
    assert extra("fué á dió")["suspect"] == 0


def test_known_ocr_errors_are_suspect():
    assert extra("frorn rnatter", "en", EN)["suspect"] == 2
    assert extra("identificaci caricter")["suspect"] == 2


def test_documented_limitations():
    assert q.known("tbis", "en")                   # a web typo in the en list (1.97)
    assert q.known("mitologia", "es")              # accent dropped, still in the es list
    assert extra("po<e:try", "en", EN)["suspect"] == 0     # splits into known pieces


def test_repeated_characters_and_junk():
    assert extra("ooooomm y aaaa")["repeated"] == 2
    junk, chars = q.junk_count("¿qué? «sí» — 39–69 … § 1° ­ � ж \x07  ¹")
    assert junk == 4 and chars == 25


def test_page_classes_at_the_thresholds():
    def page(known, unknown, junk=0):
        return q.score_text(" ".join(["casa"] * known + ["qzxwvb"] * unknown) +
                            "�" * junk, "es")

    assert page(98, 2).cls == q.GOOD and page(98, 2).pct == 2.0
    assert page(97, 3).cls == q.FAIR_CLASS and page(97, 3).pct == 3.0
    assert page(93, 7).cls == q.FAIR_CLASS
    assert page(92, 8).cls == q.POOR_CLASS and page(92, 8).pct == 8.0
    assert page(29, 0).cls == q.UNSCORED and page(29, 0).pct is None
    assert page(30, 0).cls == q.GOOD
    assert page(100, 0, junk=7).cls == q.GOOD                 # 7 / 407 = 1.7 %
    assert page(100, 0, junk=9).cls == q.POOR_CLASS           # 9 / 409 = 2.2 %


def test_word_broken_across_the_page_break_is_not_suspect():
    first = ES + " la noche mis-"
    second = "teriosa " + ES
    s = q.score_pages([first, second])
    assert s.pages[1].suspect == 0 and s.pages[1].words == q.score_text(ES, "es").words
    alone = q.score_pages(["la noche", second])                  # no hyphen: counted
    assert alone.pages[1].suspect == 1


def test_file_summary():
    pages = [q.score_text(" ".join(["casa"] * (100 - k) + ["qzxwvb"] * k), "es", i + 1)
             for i, k in enumerate([0, 1, 2, 4, 10])] + [q.score_text("casa", "es", 6)]
    s = q.summarise(pages)
    assert s["weighted"] == 3.4 and s["median"] == 2.0 and s["p95"] == 10.0
    assert (s["good"], s["fair"], s["poor"], s["unscored"], s["scored"]) == (3, 1, 1, 1, 5)
    assert s["words"] == 501


# ---------------------------------------------------------------------------- language
@pytest.mark.parametrize("text, lang", [(ES, "es"), (EN, "en"), (CA, "ca"), (FR, "fr")])
def test_page_language(text, lang):
    scored = q.score_pages([text * 3])
    assert scored.lang == lang and scored.pages[0].lang == lang


def test_mixed_english_page_with_spanish_quotes_stays_english():
    page = EN + " As Calderón wrote, «la vida es sueño, y los sueños, sueños son». " + EN
    scored = q.score_pages([page])
    assert scored.lang == "en" and scored.pages[0].lang == "en"
    assert scored.pages[0].suspect == 0


def test_short_pages_take_the_file_language_and_shares_are_reported():
    scored = q.score_pages([ES * 3, ES * 3, "the old house", EN * 2])
    assert [p.lang for p in scored.pages] == ["es", "es", "es", "en"]
    assert scored.lang == "es" and {"es", "en"} <= set(scored.shares)
    assert sum(scored.shares.values()) <= 100
