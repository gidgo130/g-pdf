# SPDX-License-Identifier: AGPL-3.0-or-later
"""Finding Tesseract and the preflight checks, with fake executables (no Tesseract needed)."""

import pytest

from gpdf.pipeline import tesseract
from gpdf.pipeline.tesseract import TesseractError

from .fake_exe import make_exe

FAKE = '''import sys
VERSION, TESSDATA, LANGS = {version!r}, {tessdata!r}, {langs!r}
if sys.argv[1] == "--version":
    print("tesseract " + VERSION)
    print(" leptonica-1.87.0")
elif sys.argv[1] == "--list-langs":
    print('List of available languages in "' + TESSDATA + '/" (%d):' % len(LANGS))
    print("\\n".join(LANGS))
'''


@pytest.fixture
def tessdata(tmp_path):
    d = tmp_path / "tessdata"
    (d / "configs").mkdir(parents=True)
    (d / "configs" / "pdf").write_text("tessedit_create_pdf 1\n")
    (d / "pdf.ttf").write_bytes(b"\0")
    return d


def fake(tmp_path, tessdata, version="v5.5.3.20260724", langs=("eng", "osd", "spa")):
    return make_exe(tmp_path, "tess", FAKE.format(version=version, tessdata=str(tessdata),
                                                  langs=list(langs)))


def test_probe_reads_version_tessdata_and_languages(tmp_path, tessdata):
    t = tesseract.probe(fake(tmp_path, tessdata))
    assert t.version == "5.5.3.20260724"
    assert t.tessdata.rstrip("\\/") == str(tessdata) and t.langs == ["eng", "osd", "spa"]
    tesseract.check(t, "spa")
    tesseract.check(t, "eng+spa")


def test_old_tesseract_is_refused(tmp_path, tessdata):
    with pytest.raises(TesseractError, match="4.1.1 is too old"):
        tesseract.probe(fake(tmp_path, tessdata, version="4.1.1"))


def test_unknown_language_is_refused_with_the_installed_ones(tmp_path, tessdata):
    t = tesseract.probe(fake(tmp_path, tessdata))
    with pytest.raises(TesseractError, match=r"no language data for xyz .*eng, osd, spa"):
        tesseract.check(t, "eng+xyz")


@pytest.mark.parametrize("missing", ["configs/pdf", "pdf.ttf"])
def test_missing_pdf_renderer_files_are_refused(tmp_path, tessdata, missing):
    (tessdata / missing).unlink()
    t = tesseract.probe(fake(tmp_path, tessdata))
    with pytest.raises(TesseractError, match=r"cannot write PDFs: .*is missing"):
        tesseract.check(t, "spa")


def test_find_order(tmp_path, tessdata, monkeypatch):
    exe = fake(tmp_path, tessdata)
    monkeypatch.setenv(tesseract.ENV_VAR, exe)
    assert tesseract.find() == exe
    with pytest.raises(TesseractError, match="not found at .*nope.exe .from --tesseract"):
        tesseract.find(str(tmp_path / "nope.exe"))
    monkeypatch.setenv(tesseract.ENV_VAR, str(tmp_path / "gone.exe"))
    with pytest.raises(TesseractError, match=f"from {tesseract.ENV_VAR}"):
        tesseract.find()
    monkeypatch.delenv(tesseract.ENV_VAR)
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    monkeypatch.setattr(tesseract, "WINDOWS_DEFAULT", str(tmp_path / "missing.exe"))
    with pytest.raises(TesseractError, match="Tesseract not found: install"):
        tesseract.find()


def test_not_tesseract(tmp_path):
    exe = make_exe(tmp_path, "other", "print('hello')\n")
    with pytest.raises(TesseractError, match="does not look like Tesseract"):
        tesseract.probe(exe)
