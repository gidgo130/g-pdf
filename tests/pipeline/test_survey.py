# SPDX-License-Identifier: AGPL-3.0-or-later
"""Survey measurements, .txt handling, duplicate groups and suggestions (synthetic files)."""

import pytest

pytest.importorskip("numpy")
pytest.importorskip("PIL")
pytest.importorskip("wordfreq")

from gpdf.pipeline import survey  # noqa: E402

from . import corpus  # noqa: E402

GUTENBERG = """The Project Gutenberg eBook of Obra
This eBook is for the use of anyone anywhere at no cost.
Title: Obra

*** START OF THE PROJECT GUTENBERG EBOOK OBRA ***

Produced by volunteers at http://www.pgdp.net

{body}

*** END OF THE PROJECT GUTENBERG EBOOK OBRA ***

Section 1. General Terms of Use
More license text.
"""
SITE = "Obra\n[Ensayo - Texto completo.]\n\nAutora\n\nhttps://ciudadseva.com/texto/obra/\n\n{body}\n"


@pytest.fixture(scope="module")
def files(tmp_path_factory):
    d = tmp_path_factory.mktemp("survey")
    pages = [corpus.prose(10 + i, 220) for i in range(6)]
    corpus.born_digital(d / "digital.pdf", pages, title="Obra digital", author="Autora",
                        toc=[[1, "Uno", 1], [1, "Dos", 3]], blank_pages=(6,))
    good = [corpus.prose(30 + i, 110) for i in range(3)]
    corpus.scan(d / "scan.pdf", [(t, t) for t in good] + [(None, None), ("picture", None),
                                                          (good[0], None)])
    corpus.scan(d / "garbled.pdf", [(t, corpus.noisy(t, 0.3, i)) for i, t in enumerate(good)])
    return d, pages, good


def test_born_digital_facts(files):
    d, pages, _ = files
    m, texts = survey.measure_pdf(d / "digital.pdf")
    assert (m["kind"], m["pages"], m["text_pages"], m["scan"]) == ("pdf", 6, 5, False)
    assert m["text_pct"] == pytest.approx(100 * 5 / 6, abs=0.1)
    assert m["bookmarks"] == 2 and m["producer"] == "corpus.py"
    assert (m["title"], m["author"], m["suggested_title"]) == ("Obra digital", "Autora",
                                                               "Obra digital")
    assert m["no_text"] == {"blank": [], "ink": [], "empty": [6]}
    assert m["language"]["lang"] == "es" and m["language"]["tesseract"] == "spa"
    assert m["score"]["median"] == 0.0 and m["score"]["scored"] == 5
    assert [r["page"] for r in m["page_scores"]] == [1, 2, 3, 4, 5, 6]
    assert m["page_scores"][5]["cls"] == "unscored" and m["page_scores"][0]["image"] is False
    assert len(texts) == 6 and m["errors"] == []


def test_scan_facts_and_pages_without_text(files):
    d, _, good = files
    m, _ = survey.measure_pdf(d / "scan.pdf")
    assert m["scan"] and m["scan_pages"] == 6 and m["dpi"] == 300
    assert m["text_pages"] == 3 and m["suggested_title"] == "scan"
    assert m["no_text"] == {"blank": [4], "ink": [5, 6], "empty": []}
    assert m["score"]["median"] == 0.0


def test_garbled_layer_scores_poor_and_triggers_a_trial(files):
    d, *_ = files
    m, _ = survey.measure_pdf(d / "garbled.pdf")
    s = m["score"]
    assert s["poor"] == 3 and s["weighted"] >= 8
    assert [x["page"] for x in m["samples"]] == [1, 2, 3]
    assert all(len(x["text"]) <= survey.SAMPLE_CHARS for x in m["samples"])
    why = survey.rebuild_triggers(s)
    assert any("weighted suspect" in w for w in why)


def test_unreadable_pdf_raises(tmp_path):
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"%PDF-1.4 this is not a pdf at all")
    with pytest.raises(survey.MeasureError):
        survey.measure_pdf(bad)
    import pymupdf

    doc = pymupdf.open()
    doc.new_page()
    doc.save(str(tmp_path / "locked.pdf"), encryption=pymupdf.PDF_ENCRYPT_AES_256,
             user_pw="u", owner_pw="o")
    with pytest.raises(survey.MeasureError, match="encrypted"):
        survey.measure_pdf(tmp_path / "locked.pdf")


# ---------------------------------------------------------------------------- .txt
def test_gutenberg_boilerplate_stripped_and_counted(tmp_path):
    body = corpus.prose(5, 900)
    corpus.txt(tmp_path / "g.txt", GUTENBERG.format(body=body))
    m, texts = survey.measure_txt(tmp_path / "g.txt")
    b = m["boilerplate"]
    assert b["gutenberg_header"] == 5 and b["gutenberg_footer"] == 4 and b["url"] == 1
    joined = "\n".join(texts)
    assert "Gutenberg" not in joined and "license" not in joined.lower()
    assert m["pseudo_pages"] == 3 and m["pages"] is None and m["text_pct"] is None
    assert m["encoding"] == "utf-8" and m["language"]["lang"] == "es"


def test_site_header_and_url_lines(tmp_path):
    corpus.txt(tmp_path / "s.txt", SITE.format(body=corpus.prose(6, 500)))
    m, _ = survey.measure_txt(tmp_path / "s.txt")
    assert m["boilerplate"] == {"gutenberg_header": 0, "gutenberg_footer": 0, "url": 1,
                                "site": 1}


def test_cp1252_fallback_is_flagged(tmp_path):
    corpus.txt(tmp_path / "w.txt", "la canción del pingüino\n" + corpus.prose(7, 300),
               encoding="cp1252")
    m, texts = survey.measure_txt(tmp_path / "w.txt")
    assert m["encoding"] == "cp1252" and "cp1252" in m["errors_note"]
    assert "canción" in texts[0] and m["score"]["median"] == 0.0


# ---------------------------------------------------------------------------- duplicates
def entry(i, kind="pdf", weighted=1.0, bookmarks=0, path=None):
    return {"id": i, "kind": kind, "root": "R", "rel": path or f"{i}.{kind}",
            "path": path or f"R/{i}.{kind}",
            "measured": {"score": {"weighted": weighted}, "bookmarks": bookmarks}}


def test_noisy_copy_is_the_same_work_and_a_shared_fifth_is_related():
    base = corpus.prose(1, 3000)
    other = corpus.prose(2, 2400)
    shared = "\n".join([base[: len(base) // 5], other])           # ~20% of base
    sets = {"a": survey.shingles(base), "b": survey.shingles(corpus.noisy(base, 0.05, 1)),
            "c": survey.shingles(shared), "d": survey.shingles(corpus.prose(3, 2000)),
            "tiny": survey.shingles("casa grande del mar azul")}
    entries = {k: entry(k) for k in sets}
    dup = survey.duplicates(entries, sets)
    assert [x["id"] for x in dup["a"]["same_work"]] == ["b"]
    assert dup["a"]["same_work"][0]["containment"] >= 0.7
    assert dup["a"]["group"] == dup["b"]["group"] == "G1"
    rel = {x["id"]: x["containment"] for x in dup["c"]["related"]}
    assert set(rel) == {"a", "b"} and 0.1 < rel["a"] < 0.5
    assert {x["id"] for x in dup["a"]["related"]} == {"c"}               # listed both ways
    assert dup["d"] == {"group": None, "same_work": [], "related": [], "keeper": None}
    assert dup["tiny"]["group"] is None                                  # < 100 shingles


def test_keeper_order_pdf_superset_suspect_bookmarks():
    text = corpus.prose(1, 3000)
    prologue = corpus.prose(9, 1200) + "\n" + text
    sets = {"pdf": survey.shingles(text), "txt": survey.shingles(text)}
    dup = survey.duplicates({"pdf": entry("pdf", weighted=5.0),
                             "txt": entry("txt", "txt", weighted=0.1)}, sets)
    assert dup["txt"]["keeper"] == "pdf"                       # a PDF before a .txt
    sets = {"a": survey.shingles(text), "b": survey.shingles(prologue)}
    dup = survey.duplicates({"a": entry("a", weighted=0.5), "b": entry("b", weighted=2.0)}, sets)
    assert dup["a"]["keeper"] == "b"                           # the superset, despite suspect
    sets = {"a": survey.shingles(text), "b": survey.shingles(corpus.noisy(text, 0.02, 3))}
    dup = survey.duplicates({"a": entry("a", weighted=2.0), "b": entry("b", weighted=1.0)}, sets)
    assert dup["a"]["keeper"] == "b"                           # lower suspect
    sets = {"a": survey.shingles(text), "b": survey.shingles(text)}
    dup = survey.duplicates({"a": entry("a", bookmarks=3), "b": entry("b", bookmarks=43)}, sets)
    assert dup["a"]["keeper"] == "b"                           # more bookmarks


# ---------------------------------------------------------------------------- suggestions
def measured(files, name):
    d = files[0]
    m, _ = survey.measure_pdf(d / name)
    return {"id": name, "status": "present", "path": str(d / name), "measured": m}


def test_suggestions_without_a_trial(files):
    names = {}
    e = measured(files, "digital.pdf")
    s = survey.suggest(e, None, names)
    assert s["action"] == survey.KEEP_TEXT and "born-digital" in s["reasons"][0]
    e = measured(files, "garbled.pdf")
    s = survey.suggest(e, None, names)
    assert (s["action"], s["reason"]) == (survey.REVIEW, "trial not run")
    e = measured(files, "scan.pdf")                 # good layer, but ink pages unchecked
    s = survey.suggest(e, None, names)
    assert (s["action"], s["reason"]) == (survey.REVIEW, "trial not run")
    assert any("p. 5, 6" in r for r in s["reasons"]) and any("1 blank" in r for r in s["reasons"])


def test_suggestions_with_trial_results(files):
    e = measured(files, "scan.pdf")
    e["trial"] = {"ran": True, "ink_text": [6], "pictures": [5]}
    s = survey.suggest(e, None, {})
    assert (s["action"], s["pages"]) == (survey.OCR_MISSING, [6])
    assert any("picture" in r and "p. 5" in r for r in s["reasons"])
    e = measured(files, "garbled.pdf")
    e["trial"] = {"ran": True, "ink_text": [], "pictures": [],
                  "verdict": {"action": survey.REBUILD, "why": "trial: 30.0% -> 1.0%"}}
    s = survey.suggest(e, None, {})
    assert (s["action"], s["pages"]) == (survey.REBUILD, "all")
    e["trial"]["verdict"] = {"action": survey.KEEP_TEXT, "why": "trial: no clear gain"}
    assert survey.suggest(e, None, {})["action"] == survey.KEEP_TEXT


def test_duplicate_loser_and_other_language_are_excluded(files, tmp_path):
    e = measured(files, "digital.pdf")
    dup = {"group": "G1", "keeper": "k", "same_work": [{"id": "k", "containment": 0.9}],
           "related": []}
    s = survey.suggest(e, dup, {"k": "keeper.pdf"})
    assert (s["action"], s["reason"], s["duplicate_of"]) == (survey.EXCLUDE, "duplicate", "k")
    assert "keeper.pdf" in s["reasons"][0]
    corpus.born_digital(tmp_path / "cat.pdf", [corpus.prose(i, 200, "ca") for i in range(3)])
    m, _ = survey.measure_pdf(tmp_path / "cat.pdf")
    assert m["language"]["lang"] == "ca"
    s = survey.suggest({"id": "c", "status": "present", "measured": m}, None, {})
    assert (s["action"], s["reason"]) == (survey.EXCLUDE, "other_language")


def test_unreadable_entry_is_reviewed():
    s = survey.suggest({"id": "x", "status": "error", "error": "encrypted"}, None, {})
    assert (s["action"], s["reason"]) == (survey.REVIEW, "could not read")
