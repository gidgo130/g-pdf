# SPDX-License-Identifier: AGPL-3.0-or-later
"""gpdf-cli ocr with the real Tesseract: the scan stays byte-identical, the invisible
text is right and in the right place, refusals and failed pages."""

import hashlib
import json
import os
import re
import shutil
from collections import Counter

import pytest

pytest.importorskip("numpy")
pytest.importorskip("PIL")
import pymupdf  # noqa: E402

from gpdf import cli  # noqa: E402
from gpdf.pipeline import ocr, tesseract  # noqa: E402

from .fake_exe import make_exe  # noqa: E402

LANGS = "spa+eng"
try:
    TESS = tesseract.prepare(LANGS)
except tesseract.TesseractError as e:
    pytest.skip(f"needs Tesseract 5 with spa and eng: {e}", allow_module_level=True)

CLEAN = ["spanish_g4", "english_g4", "rotate90", "residual"]    # 1-bit 300 dpi pages
PUNCT = "¿¡\"'(«.,;:!?)»"


def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def norm(word):
    return word.strip(PUNCT)


def image_md5s(doc):
    return [sorted(hashlib.md5(doc.xref_stream_raw(img[0])).hexdigest()
                   for img in page.get_images()) for page in doc]


def known_words(truth, n=5):
    """``n`` words that occur once on the page, spread over it: (word, centre)."""
    count = Counter(norm(w[4]) for w in truth.words)
    once = [w for w in truth.words if count[norm(w[4])] == 1 and len(norm(w[4])) >= 5]
    pick = [once[round(k * (len(once) - 1) / (n - 1))] for k in range(n)]
    return [(norm(w[4]), mid(pymupdf.Rect(w[:4]))) for w in pick]


def mid(r):
    return pymupdf.Point((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2)


@pytest.fixture(scope="session")
def ocr_book(synth, tmp_path_factory):
    """(source path, truth, source sha) of the OCR fixture; tests must not modify it."""
    path = tmp_path_factory.mktemp("ocr") / "scan.pdf"
    truth = synth.build_ocr_book(path)
    return path, truth, sha(path)


@pytest.fixture(scope="session")
def ocr_run(ocr_book, tmp_path_factory):
    """The fixture OCR'd once via the CLI with 3 jobs: (output, report dict)."""
    src = ocr_book[0]
    outdir = tmp_path_factory.mktemp("ocr_out")
    assert cli.main(["ocr", str(src), "-o", str(outdir), "--lang", LANGS, "--jobs", "3"]) == 0
    report = json.loads((outdir / "scan_ocr.report.json").read_text("utf-8"))
    return outdir / "scan_ocr.pdf", report


@pytest.fixture
def pages(ocr_book, ocr_run):
    """(truth, report entry, output page) per page."""
    with pymupdf.open(ocr_run[0]) as doc:
        yield [(t, r, doc[i]) for i, (t, r) in enumerate(zip(ocr_book[1], ocr_run[1]["pages"]))]


def test_scan_images_untouched_and_text_invisible(ocr_book, ocr_run):
    with pymupdf.open(ocr_book[0]) as a, pymupdf.open(ocr_run[0]) as b:
        assert len(a) == len(b)
        assert image_md5s(a) == image_md5s(b)
        for pa, pb in zip(a, b):
            assert pa.rotation == pb.rotation
            assert pa.get_pixmap(dpi=72).samples == pb.get_pixmap(dpi=72).samples
    assert sha(ocr_book[0]) == ocr_book[2]


def test_statuses_and_report(ocr_run, ocr_book):
    report = ocr_run[1]
    assert report["schema"] == 1 and report["langs"] == LANGS and report["jobs"] == 3
    assert [p["status"] for p in report["pages"]] == [t.status for t in ocr_book[1]]
    assert report["summary"]["counts"] == {"ocr": 5, "has_text": 1, "blank": 1,
                                           "no_image": 0, "failed": 0}
    jpeg = report["pages"][1]
    assert (jpeg["d_src"], jpeg["d_tesseract"], jpeg["upsampled"]) == (96, 288, True)
    clean = report["pages"][0]
    assert (clean["d_src"], clean["d_tesseract"], clean["upsampled"]) == (300, 300, False)
    for p in report["pages"]:
        if p["status"] == "ocr":
            assert p["words"] > 50 and p["mean_conf"] > 80 and 0 <= p["low_conf_share"] < 0.2
    lowest = report["summary"]["lowest_conf"]
    assert len(lowest) == 5 and [x["mean_conf"] for x in lowest] == sorted(
        x["mean_conf"] for x in lowest)
    assert report["summary"]["mean_conf"] > 85
    assert report["tesseract"] == TESS.version and report["tessdata"] == TESS.tessdata


def test_word_recall(pages):
    for truth, rep, page in pages:
        if truth.status != "ocr":
            continue
        want = Counter(norm(w[4]) for w in truth.words)
        got = Counter(norm(w[4]) for w in page.get_text("words"))
        hit = sum(min(n, got[w]) for w, n in want.items())
        recall = hit / sum(want.values())
        accented = [w for w in want if re.search("[áéíóúñü]", w)]
        if truth.name.startswith("spanish") or truth.name in ("rotate90", "residual"):
            assert accented                      # accented words are part of the count
        assert recall >= (0.97 if truth.name in CLEAN else 0.90), (truth.name, recall)


def test_word_positions_and_no_duplicates(pages):
    for truth, rep, page in pages:
        if truth.status != "ocr":
            continue
        words = page.get_text("words")
        assert len(words) <= len(truth.words) * 1.05, truth.name    # no doubled layer
        for word, centre in known_words(truth):
            found = [w for w in words if norm(w[4]) == word]
            assert len(found) == 1, (truth.name, word, len(found))
            c = mid(pymupdf.Rect(found[0][:4]) * page.rotation_matrix)   # as displayed
            assert abs(c.x - centre.x) <= 3 and abs(c.y - centre.y) <= 3, (
                truth.name, word, c, centre)


def test_existing_text_page_unchanged_blank_skipped(ocr_book, ocr_run):
    with pymupdf.open(ocr_book[0]) as a, pymupdf.open(ocr_run[0]) as b:
        assert b[3].get_text() == a[3].get_text() == "Texto visible ya presente\n"
        assert b[3].read_contents() == a[3].read_contents()
        assert b[4].get_text() == ""
    assert ocr_run[1]["pages"][4]["d_src"] == 300


def test_one_font_object_and_size(ocr_book, ocr_run):
    with pymupdf.open(ocr_run[0]) as doc:
        fonts = [x for x in range(1, doc.xref_length())
                 if doc.xref_get_key(x, "Type")[1] == "/Font"
                 and doc.xref_get_key(x, "Subtype")[1] == "/Type0"
                 and "GlyphLessFont" in doc.xref_get_key(x, "BaseFont")[1]]
    assert len(fonts) == 1
    assert os.path.getsize(ocr_run[0]) <= 1.15 * os.path.getsize(ocr_book[0]) + 20_000
    assert ocr_run[1]["size_ratio"] == pytest.approx(
        os.path.getsize(ocr_run[0]) / os.path.getsize(ocr_book[0]), abs=1e-3)


def test_one_job_gives_the_same_text(ocr_book, ocr_run, tmp_path):
    out = tmp_path / "one.pdf"
    report = ocr.ocr_document(ocr_book[0], out, LANGS, jobs=1, tess=TESS)
    assert report.jobs == 1
    with pymupdf.open(out) as a, pymupdf.open(ocr_run[0]) as b:
        assert [p.get_text() for p in a] == [p.get_text() for p in b]
    assert sha(ocr_book[0]) == ocr_book[2]


def test_pages_option_reads_only_those_pages(ocr_book, tmp_path):
    rc = cli.main(["ocr", str(ocr_book[0]), "-o", str(tmp_path), "--lang", LANGS,
                   "--pages", "1,3-4"])
    assert rc == 0
    report = json.loads((tmp_path / "scan_ocr.report.json").read_text("utf-8"))
    assert [(p["page"], p["status"]) for p in report["pages"]] == [
        (1, "ocr"), (3, "ocr"), (4, "has_text")]
    with pymupdf.open(tmp_path / "scan_ocr.pdf") as doc:
        assert len(doc) == 7 and doc[1].get_text() == "" and doc[0].get_text()


def test_refusals(ocr_book, tmp_path, capsys):
    src = tmp_path / "book.pdf"
    shutil.copy(ocr_book[0], src)
    with pytest.raises(ocr.OcrError, match="different file from the source"):
        ocr.ocr_document(src, src, LANGS, tess=TESS)
    argv = ["ocr", str(src), "-o", str(tmp_path), "--lang", LANGS]
    assert cli.main([*argv, "--report", str(src)]) == 1
    assert "is the source file" in capsys.readouterr().err
    (tmp_path / "book_ocr.pdf").write_bytes(b"old")
    assert cli.main(argv) == 1
    assert "book_ocr.pdf already exists (pass --force" in capsys.readouterr().err
    assert (tmp_path / "book_ocr.pdf").read_bytes() == b"old"
    assert cli.main(["ocr", str(src), "-o", str(tmp_path / "x"), "--lang", "eng+xyz"]) == 1
    err = capsys.readouterr().err
    assert "Tesseract has no language data for xyz" in err and len(err.splitlines()) == 1
    assert cli.main([*argv, "--pages", "5-99"]) == 1          # (output exists: --force)
    capsys.readouterr()
    assert cli.main([*argv, "--force", "--pages", "5-99"]) == 1
    assert "outside 1-7" in capsys.readouterr().err
    assert sha(src) == ocr_book[2]


FAILING = '''import subprocess, sys
REAL, LOG = {real!r}, {log!r}
if len(sys.argv) > 1 and sys.argv[1].endswith("p00002.png"):
    open(LOG, "a").write("fail\\n")
    sys.stderr.write("forced failure for page 2\\n")
    sys.exit(3)
sys.exit(subprocess.run([REAL, *sys.argv[1:]]).returncode)
'''


def test_failed_page_is_left_without_text_and_exits_2(synth, tmp_path, capsys):
    import pymupdf as fitz

    src = tmp_path / "two.pdf"
    doc = fitz.open()
    synth.add_ocr_g4(doc, synth.spanish_lines(1))
    synth.add_ocr_g4(doc, synth.spanish_lines(2))
    doc.save(str(src))
    log = tmp_path / "calls.log"
    wrapper = make_exe(tmp_path, "tess", FAILING.format(real=TESS.exe, log=str(log)))
    rc = cli.main(["ocr", str(src), "-o", str(tmp_path / "out"), "--lang", LANGS,
                   "--tesseract", wrapper, "--jobs", "2"])
    assert rc == 2
    assert log.read_text().count("fail") == 2                  # tried, then retried once
    report = json.loads((tmp_path / "out" / "two_ocr.report.json").read_text("utf-8"))
    p1, p2 = report["pages"]
    assert p1["status"] == "ocr" and p2["status"] == "failed"
    assert "forced failure for page 2" in p2["error"] and "exit code 3" in p2["error"]
    assert report["summary"]["failed"] == [2]
    with fitz.open(tmp_path / "out" / "two_ocr.pdf") as out:
        assert out[0].get_text() and out[1].get_text() == ""
    assert "p. 2 failed: exit code 3: forced failure for page 2" in capsys.readouterr().out


def test_temp_files_removed_also_on_error(ocr_book, tmp_path, monkeypatch):
    import tempfile

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    ocr.ocr_document(ocr_book[0], tmp_path / "o.pdf", LANGS, jobs=2, tess=TESS, pages="1-3")
    assert sorted(os.listdir(tmp_path)) == ["o.pdf"]

    def broken(page, textpdf):
        raise RuntimeError("overlay broke")

    monkeypatch.setattr(ocr, "overlay", broken)
    with pytest.raises(RuntimeError, match="overlay broke"):
        ocr.ocr_document(ocr_book[0], tmp_path / "p.pdf", LANGS, jobs=2, tess=TESS)
    assert sorted(os.listdir(tmp_path)) == ["o.pdf"]
