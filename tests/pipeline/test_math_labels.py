# SPDX-License-Identifier: AGPL-3.0-or-later
"""Printed page labels from the header and footer bands: alternating sides, a number at the
foot of a chapter opener, an offset step where a blank verso is missing, a page without a
number, a misread number, roman front matter, the PDF-text and Tesseract fallbacks."""

import pymupdf
import pytest

from gpdf.pipeline import tesseract
from gpdf.pipeline.mathocr import labels

from .fake_exe import make_exe


def page_number(text, y):
    return {"type": "page_number", "bbox": [0.1, y, 0.14, y + 0.015],
            "content": [{"type": "text", "content": text}]}


def header(text, x0=0.5):
    return {"type": "header", "bbox": [x0, 0.057, 0.91, 0.076],
            "content": [{"type": "text", "content": text}]}


def text_block(text):
    return {"type": "text", "bbox": [0.1, 0.3, 0.9, 0.4],
            "content": [{"type": "text", "content": text}]}


def book(first, last, offset, missing=(), misread=None, foot=(), header_number=()):
    """Engine blocks per file page: the printed number is page + offset, alternating
    sides; ``foot`` pages print it at the foot; ``header_number`` pages carry it inside
    the running head; ``missing`` pages print none; ``misread`` = {page: text}."""
    out = {}
    for p in range(first, last + 1):
        n = str(p + offset)
        blocks = [text_block(f"Body of page {p}.")]
        if p in missing:
            pass
        elif misread and p in misread:
            blocks.insert(0, page_number(misread[p], 0.06))
        elif p in foot:
            blocks.append(page_number(n, 0.89))
        elif p in header_number:
            blocks.insert(0, header(f"{n} RUNNING HEAD" if p % 2 == 0 else f"RUNNING HEAD {n}"))
        else:
            blocks.insert(0, header("RUNNING HEAD", 0.5 if p % 2 == 0 else 0.1))
            blocks.insert(0, page_number(n, 0.06))
            if p % 2:
                blocks[0]["bbox"] = [0.87, 0.06, 0.91, 0.075]
        out[p] = blocks
    return out


def test_candidates_come_from_page_number_header_and_footer_blocks():
    blocks = [page_number("54", 0.06), header("MULTIVARIATE SYSTEMS"), text_block("Let 7 be")]
    assert labels.candidates(labels.band_texts(blocks)) == [("a", 54, None)]
    assert labels.candidates(["431 CHAPTER TITLE"]) == [("a", 431, None)]
    assert labels.candidates(["CHAPTER TITLE 432"]) == [("a", 432, None)]
    assert labels.candidates(["xiv"]) == [("r", 14, None)]
    assert labels.candidates(["Section 3 Title"]) == []     # a number inside is not a label


def test_alternating_sides_and_a_foot_number_fit_one_run():
    cands = {p: labels.candidates(labels.band_texts(b))
             for p, b in book(63, 70, -19, foot=(63,)).items()}
    fit = labels.fit(cands)
    assert {p: v.printed for p, v in fit.labels.items()} == {p: str(p - 19) for p in range(63, 71)}
    assert all(v.inferred is None for v in fit.labels.values())
    assert fit.runs == [{"pages": [63, 70], "printed": ["44", "51"], "offset": -19, "style": "a",
                         "found": 8}]
    assert fit.steps == [] and fit.rejected == []


def test_offset_step_at_a_chapter_start_gives_two_runs_and_a_step():
    pages = book(120, 126, -19)
    pages.update(book(127, 134, -18, foot=(127,)))      # chapter opener; blank verso missing
    fit = labels.fit({p: labels.candidates(labels.band_texts(b)) for p, b in pages.items()})
    assert [r["pages"] for r in fit.runs] == [[120, 126], [127, 134]]
    assert [r["offset"] for r in fit.runs] == [-19, -18]
    assert fit.steps == [{"at": 127, "from": -19, "to": -18}]
    assert fit.labels[126].printed == "107" and fit.labels[127].printed == "109"


def test_a_missing_number_is_inferred_and_a_misread_one_rejected():
    pages = book(200, 209, -18, missing=(204,), misread={207: "18"})
    fit = labels.fit({p: labels.candidates(labels.band_texts(b)) for p, b in pages.items()})
    assert fit.labels[204].printed is None and fit.labels[204].inferred == "186"
    assert fit.labels[207].printed is None and fit.labels[207].inferred == "189"
    assert fit.rejected == [{"page": 207, "value": "18", "reason": "does not fit the run"}]
    assert fit.runs[0]["pages"] == [200, 209] and fit.runs[0]["found"] == 8
    assert fit.steps == []


def test_a_lone_disagreeing_number_does_not_start_a_run():
    pages = book(300, 309, -18, misread={300: "7"})
    fit = labels.fit({p: labels.candidates(labels.band_texts(b)) for p, b in pages.items()})
    assert fit.runs[0]["pages"] == [300, 309] and fit.runs[0]["offset"] == -18
    assert fit.labels[300].inferred == "282" and fit.steps == []


def test_separate_page_ranges_make_separate_runs_without_a_step():
    pages = book(63, 66, -19)
    pages.update(book(123, 126, -18))
    fit = labels.fit({p: labels.candidates(labels.band_texts(b)) for p, b in pages.items()})
    assert [r["pages"] for r in fit.runs] == [[63, 66], [123, 126]]
    assert fit.steps == []


def test_roman_front_matter_and_a_number_inside_the_running_head():
    pages = {p: [page_number(labels.roman(p - 2), 0.9)] for p in range(5, 9)}
    pages.update(book(20, 25, -19, header_number=(20, 21, 22, 23, 24, 25)))
    fit = labels.fit({p: labels.candidates(labels.band_texts(b)) for p, b in pages.items()})
    assert [(r["pages"], r["style"], r["offset"]) for r in fit.runs] == [
        ([5, 8], "r", -2), ([20, 25], "a", -19)]
    assert fit.labels[6].printed == "iv" and fit.labels[22].printed == "3"
    assert fit.steps == []                      # a change of style is not a step


def test_a_lone_page_keeps_its_number_but_two_disagreeing_pages_keep_none():
    fit = labels.fit({44: [("a", 431, None)]})
    assert fit.labels[44].printed == "431" and fit.rejected == []
    assert fit.runs == [{"pages": [44, 44], "printed": ["431", "431"], "offset": 387,
                         "style": "a", "found": 1}]
    fit = labels.fit({44: [("a", 431, None)], 45: [("a", 9, None)]})
    assert fit.labels[44].printed is None and fit.labels[45].printed is None
    assert len(fit.rejected) == 2 and fit.runs == []


def test_a_page_with_nothing_to_go_on_gets_no_label():
    fit = labels.fit({1: [], 2: []})
    assert fit.labels[1].printed is None and fit.labels[1].inferred is None
    assert fit.runs == [] and fit.labels[2].flag() == {"type": "label_missing"}
    assert labels.Label("44", None).flag() is None
    assert labels.Label(None, "45").flag() == {"type": "label_inferred", "value": "45"}


# ---------------------------------------------------------------- fallbacks

@pytest.fixture
def pdf(tmp_path):
    path = tmp_path / "born.pdf"
    doc = pymupdf.open()
    for p in range(1, 4):
        page = doc.new_page(width=400, height=600)
        page.insert_text((40, 30), f"{p + 10}" if p % 2 else "", fontsize=10)   # top band
        page.insert_text((360, 580), f"{p + 10}" if not p % 2 else "", fontsize=10)
        page.insert_text((40, 300), "Body text.", fontsize=11)
    doc.save(str(path))
    doc.close()
    return path


def test_pdf_text_in_the_bands_is_used_when_the_engine_found_no_number(pdf):
    doc = pymupdf.open(str(pdf))
    texts = labels.band_texts_from_pdf(doc, 2)
    assert texts == ["12"]
    assert labels.band_texts_from_pdf(doc, 1) == ["11"]
    doc.close()


FAKE_TESS = '''import sys
args = sys.argv[1:]
out = args[1]
open(out + ".pdf", "wb").write(b"%PDF-fake")
rows = ["level\\tpage_num\\tblock_num\\tpar_num\\tline_num\\tword_num\\tleft\\ttop\\twidth\\theight\\tconf\\ttext",
        "1\\t1\\t0\\t0\\t0\\t0\\t0\\t0\\t100\\t100\\t-1\\t",
        "5\\t1\\t1\\t1\\t1\\t1\\t10\\t10\\t20\\t10\\t91\\t431",
        "5\\t1\\t1\\t1\\t1\\t2\\t40\\t10\\t20\\t10\\t91\\tTITLE"]
open(out + ".tsv", "w").write("\\n".join(rows) + "\\n")
'''


def test_tesseract_fallback_reads_the_band_words(pdf, tmp_path):
    t = tesseract.Tesseract(make_exe(tmp_path, "tess", FAKE_TESS), "5.5.0", "", ["eng"])
    doc = pymupdf.open(str(pdf))
    texts = labels.band_texts_from_ocr(t, doc, 3, tmp_path / "work")
    doc.close()
    assert texts == ["431 TITLE", "431 TITLE"]          # one text per band (top, bottom)
    assert labels.candidates(texts) == [("a", 431, None), ("a", 431, None)]
