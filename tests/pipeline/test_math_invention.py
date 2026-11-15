# SPDX-License-Identifier: AGPL-3.0-or-later
"""The engine-invention check on synthetic pages: a paragraph the page does not hold is
flagged against the text layer or Tesseract; CJK and the fixed phrases need no reference;
short paragraphs are not scored; OCR slips and hyphen breaks still match; an image-only page
without Tesseract is unchecked; re-runs replace only the check's own flags; --dry-run
writes no flags; math-check-pages and math-provenance accept the new flag."""

import json
import shutil

import pymupdf
import pytest

from gpdf import cli
from gpdf.pipeline import tesseract
from gpdf.pipeline.mathocr import check, invention, provenance

REAL = [
    "The gardeners planted seven rows of tomatoes beside the northern fence before the rain.",
    "Every morning the baker counted loaves and wrote the totals inside a small green notebook.",
    "Travellers crossing the valley usually stopped at the bridge to water their tired horses.",
]
INVENTED = ("Quantum lighthouses harmonize purple elephants through magnetic sandwiches while "
            "orchestras photosynthesize.")


def text_pdf(path, paras, image_only=False):
    """A one-page PDF with the paragraphs as text (or as a picture of them)."""
    doc = pymupdf.open()
    pg = doc.new_page(width=612, height=792)
    pg.insert_textbox(pymupdf.Rect(60, 60, 552, 760), "\n\n".join(paras), fontsize=14)
    if image_only:
        pix = pg.get_pixmap(dpi=200)
        img = pymupdf.open()
        ip = img.new_page(width=612, height=792)
        ip.insert_image(ip.rect, pixmap=pix)
        doc.close()
        doc = img
    doc.save(str(path))
    doc.close()
    return path


def page_md(paras, file=1):
    return f'<!-- page printed="{file}" file="{file}" -->\n\n' + "\n\n".join(paras) + "\n"


def export(tmp_path, paras, md_paras, image_only=False, flags=None):
    src = text_pdf(tmp_path / "book.pdf", paras, image_only)
    r = tmp_path / "out" / "book"
    (r / "pages").mkdir(parents=True)
    (r / "pages" / "p001.md").write_text(page_md(md_paras), "utf-8")
    (r / "pages" / "p001.flags.json").write_text(json.dumps(
        flags if flags is not None else [{"type": "summary", "display_formulas": 0}]), "utf-8")
    (r / "meta.json").write_text(json.dumps({"source": {"path": str(src)}}), "utf-8")
    return r


@pytest.fixture
def no_tess(monkeypatch, tmp_path):
    monkeypatch.setenv("GPDF_TESSERACT", str(tmp_path / "no-tesseract"))
    monkeypatch.setattr(shutil, "which", lambda *a, **k: None)


def suspects(r, n=1):
    return [f for f in json.loads((r / "pages" / f"p{n:03d}.flags.json").read_text("utf-8"))
            if f["type"] == "invention_suspect"]


def have_tesseract():
    try:
        tesseract.prepare("eng")
        return True
    except tesseract.TesseractError:
        return False


def test_only_the_invented_paragraph_is_flagged_against_the_text_layer(tmp_path, no_tess):
    r = export(tmp_path, REAL, REAL + [INVENTED])
    res = invention.check_export(r)
    s = suspects(r)
    assert len(s) == 1
    assert s[0] == {"type": "invention_suspect", "para_index": 3, "kind": "prose", "words": 11,
                    "share": 0.0, "ref": "layer", "threshold": 0.6, "rule": [],
                    "start": " ".join(INVENTED.split()[:12])}
    assert res["summary"]["by_layer"] == 1 and res["summary"]["candidates"] == 1
    assert res["summary"]["rule_confirmed"] == 0 and res["summary"]["unchecked"] == []


def test_cjk_and_fixed_phrases_are_flagged_with_no_reference():
    md = page_md(["这是一个测试段落，没有任何意义。", "The quick brown fox jumps.", "注 x",
                  "$$\nx = 1 \\quad \\text{图}\n$$", "The image contains no text.",
                  "Plain words only here."])
    pc = invention.check_page(md, None, None)
    assert pc.status == "unchecked" or pc.status == "no_prose"
    got = [(f["para_index"], f["kind"], f["rule"], f["ref"], f["share"]) for f in pc.flags]
    assert got == [(0, "prose", ["CJK"], None, None), (1, "prose", ["quick brown fox"], None, None),
                   (2, "prose", ["CJK (few)"], None, None), (3, "display", ["CJK (few)"], None, None),
                   (4, "prose", ["no discernible text"], None, None)]
    assert invention.confirmed_by_rule(["CJK"]) and not invention.confirmed_by_rule(["CJK (few)"])


def test_short_paragraphs_are_not_scored():
    md = page_md(["Seven words here are not quite enough.", "Tiny bits of a an to is it be or by."])
    pc = invention.check_page(md, "nothing alike in this reference text at all " * 5)
    assert pc.status == "no_prose" and pc.flags == []
    ps = invention.paragraphs(md)
    assert [invention.scored(p) for p in ps] == [False, False]
    # inline math does not count as words
    p = invention.paragraphs(page_md(["Let \\(alpha beta gamma delta epsilon zeta\\) be some numbers."]))[0]
    assert p["words"] == 4 and p["w4"] == ["some", "numbers"]


def test_an_ocr_slip_still_matches():
    ref = invention.Ref(invention.ref_words("The statistics of the problem"))
    assert ref.has("statistcs") and ref.has("statistics") and not ref.has("elephants")
    assert invention.fold("Café Naïve") == "cafe naive"


def test_hyphenated_line_breaks_are_joined_and_both_pieces_kept():
    w = invention.ref_words("a smooth distri-\nbution of\nprob\u00ad\nabilities")
    assert {"distribution", "distri", "bution", "probabilities", "abilities", "prob"} <= w


def test_an_image_only_page_without_tesseract_is_unchecked(tmp_path, no_tess):
    r = export(tmp_path, REAL, REAL + [INVENTED], image_only=True)
    res = invention.check_export(r)
    assert res["summary"]["unchecked"] == [1] and res["summary"]["candidates"] == 0
    assert res["summary"]["by_layer"] == res["summary"]["by_tesseract"] == 0
    assert any("no Tesseract" in n for n in res["notices"])
    assert suspects(r) == []


@pytest.mark.skipif(not have_tesseract(), reason="Tesseract (eng) is not installed")
def test_an_image_only_page_is_checked_against_tesseract(tmp_path):
    r = export(tmp_path, REAL, REAL + [INVENTED], image_only=True)
    res = invention.check_export(r)
    assert res["summary"]["by_tesseract"] == 1 and res["summary"]["tesseract"]
    s = suspects(r)
    assert [(f["para_index"], f["ref"]) for f in s] == [(3, "tesseract")]


@pytest.mark.skipif(not have_tesseract(), reason="Tesseract (eng) is not installed")
def test_forced_tesseract_mode_reads_a_text_page(tmp_path):
    src = text_pdf(tmp_path / "t.pdf", REAL)
    doc = pymupdf.open(str(src))
    t = tesseract.prepare("eng")
    res = invention.check_pages({1: page_md(REAL + [INVENTED])}, invention.PdfPages(doc), t,
                                mode="tesseract")
    doc.close()
    assert [(f["para_index"], f["ref"]) for f in res["flags"][1]] == [(3, "tesseract")]


def test_a_rerun_replaces_only_its_own_flags(tmp_path, no_tess):
    other = [{"type": "dropped", "text": "HEAD", "what": "running_head"},
             {"type": "invention_suspect", "para_index": 9, "kind": "prose", "words": 9,
              "share": 0.1, "ref": "layer", "threshold": 0.6, "rule": [], "start": "stale"},
             {"type": "summary", "display_formulas": 0}]
    r = export(tmp_path, REAL, REAL + [INVENTED], flags=other)
    invention.check_export(r)
    first = json.loads((r / "pages" / "p001.flags.json").read_text("utf-8"))
    invention.check_export(r)
    second = json.loads((r / "pages" / "p001.flags.json").read_text("utf-8"))
    assert first == second
    assert [f["type"] for f in second] == ["dropped", "invention_suspect", "summary"]
    assert second[1]["para_index"] == 3


def test_dry_run_writes_no_flags_and_a_report(tmp_path, no_tess, capsys):
    r = export(tmp_path, REAL, REAL + [INVENTED])
    before = {p.name: p.read_bytes() for p in (r / "pages").iterdir()}
    rep = tmp_path / "report.json"
    assert cli.main(["math-invention-check", str(r), "--dry-run", "--report", str(rep)]) == 0
    assert {p.name: p.read_bytes() for p in (r / "pages").iterdir()} == before
    got = json.loads(rep.read_text("utf-8"))
    assert got["dry_run"] is True and got["summary"]["candidates"] == 1
    assert got["flags"][0]["file"] == 1 and got["flags"][0]["para_index"] == 3
    out = capsys.readouterr().out
    assert "1 by text layer" in out and "1 candidates" in out


def test_cli_options_thresholds_and_source(tmp_path, no_tess):
    r = export(tmp_path, REAL, REAL + [INVENTED])
    other = text_pdf(tmp_path / "other.pdf", REAL + [INVENTED])
    # the invented paragraph is on this source: nothing is flagged
    assert cli.main(["math-invention-check", str(r), "--source", str(other)]) == 0
    assert suspects(r) == []
    # a threshold of 0: no share is below it
    assert cli.main(["math-invention-check", str(r), "--threshold-layer", "0"]) == 0
    assert suspects(r) == []
    assert cli.main(["math-invention-check", str(r), "--usable", "0.5"]) == 0
    assert len(suspects(r)) == 1
    assert cli.main(["math-invention-check", str(tmp_path / "nope")]) == 1
    assert cli.main(["math-invention-check", str(r), "--tesseract", str(tmp_path / "x.exe")]) == 1


def test_the_layer_must_be_usable(tmp_path, no_tess):
    # half the scored paragraphs missing from the layer: the layer is not usable, and with no
    # Tesseract the page is unchecked
    r = export(tmp_path, REAL[:1], REAL[:1] + [INVENTED])
    res = invention.check_export(r)
    assert res["summary"]["unchecked"] == [1] and suspects(r) == []


def test_math_check_pages_accepts_the_flag_and_provenance_ignores_it(tmp_path, no_tess):
    r = export(tmp_path, REAL, REAL + [INVENTED, "$$\nx = 1\n$$"])
    invention.check_export(r)
    assert suspects(r)
    res = check.check_pages(r / "pages")
    assert not [p for p in res["problems"] if p["kind"] in ("flags_json", "flags_missing")]
    pr = provenance.write(r)
    assert pr["by_status"]["flagged"] == 0 and pr["formulas"] == 1
    provenance.write(r, prose_only=True)                  # not a review flag: not refused
