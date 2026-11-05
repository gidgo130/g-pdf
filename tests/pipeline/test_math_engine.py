# SPDX-License-Identifier: AGPL-3.0-or-later
"""The MinerU runner: finding and pinning the engine, the per-page cache, resuming a
stopped run. A fake mineru-kit stands in for the engine (tests/pipeline/fake_mineru.py)."""

import json
import zipfile

import pymupdf
import pytest

from gpdf.pipeline.mathocr import engine
from gpdf.pipeline.mathocr.engine import EngineError

from .fake_mineru import make_fake_mineru, make_home


@pytest.fixture
def fake(tmp_path, monkeypatch):
    """(exe, home, pins, log) with the fake engine logging every invocation."""
    exe = make_fake_mineru(tmp_path)
    home, pins = make_home(tmp_path)
    log = tmp_path / "calls.jsonl"
    monkeypatch.setenv("FAKE_MINERU_LOG", str(log))
    monkeypatch.delenv("FAKE_MINERU_STOP_AFTER", raising=False)
    monkeypatch.delenv("FAKE_MINERU_PAGES", raising=False)
    monkeypatch.delenv("FAKE_MINERU_VERSION", raising=False)
    return exe, home, pins, log


def calls(log):
    return [json.loads(s) for s in log.read_text("utf-8").splitlines()] if log.exists() else []


@pytest.fixture
def pdf(tmp_path):
    path = tmp_path / "book.pdf"
    doc = pymupdf.open()
    for i in range(6):
        page = doc.new_page(width=300, height=400)
        page.insert_text((40, 60), f"Page {i + 1}", fontsize=14)
    doc.save(str(path))
    doc.close()
    return path


# ---------------------------------------------------------------- finding and pinning

def test_prepare_reads_version_and_model_hashes(fake):
    exe, home, pins, _ = fake
    e = engine.prepare(exe, home, pins=pins)
    assert e.version == "4.1.7" and e.exe == exe and e.unpinned == []
    assert e.model_hashes == pins
    d = e.describe()
    assert d["version"] == "4.1.7" and d["pinned_version"] == engine.PINNED_VERSION
    assert d["models"] == pins and d["unpinned"] == []


def test_find_accepts_a_venv_folder_and_the_environment(fake, monkeypatch, tmp_path):
    exe, home, pins, _ = fake
    monkeypatch.setenv(engine.ENV_VAR, exe)
    assert engine.find(None) == exe
    monkeypatch.delenv(engine.ENV_VAR)
    monkeypatch.setattr(engine.shutil, "which", lambda name: None)
    with pytest.raises(EngineError, match="mineru-kit not found"):
        engine.find(None)
    with pytest.raises(EngineError, match="not found at"):
        engine.find(str(tmp_path / "nowhere"))


def test_version_mismatch_stops_unless_allowed(fake, monkeypatch):
    exe, home, pins, _ = fake
    monkeypatch.setenv("FAKE_MINERU_VERSION", "4.2.0")
    with pytest.raises(EngineError, match=r"MinerU 4.2.0 found, 4.1.7 expected.*--allow-unpinned"):
        engine.prepare(exe, home, pins=pins)
    e = engine.prepare(exe, home, pins=pins, allow_unpinned=True)
    assert e.version == "4.2.0" and e.unpinned == ["version"]


def test_model_hash_mismatch_stops_unless_allowed(fake):
    exe, home, pins, _ = fake
    name = next(iter(pins))
    (home / "models" / "MinerU2.5-Pro-2605-1.2B-GGUF" / name).write_bytes(b"other")
    with pytest.raises(EngineError, match=f"{name}.*sha256.*--allow-unpinned"):
        engine.prepare(exe, home, pins=pins)
    e = engine.prepare(exe, home, pins=pins, allow_unpinned=True)
    assert e.unpinned == [name] and e.model_hashes[name] != pins[name]


def test_missing_model_file_is_an_error(fake):
    exe, home, pins, _ = fake
    name = next(iter(pins))
    (home / "models" / "MinerU2.5-Pro-2605-1.2B-GGUF" / name).unlink()
    with pytest.raises(EngineError, match=f"{name} not found"):
        engine.prepare(exe, home, pins=pins)


# ---------------------------------------------------------------- running and caching

def test_run_caches_both_tiers_per_page_and_reuses_the_cache(fake, pdf, tmp_path):
    exe, home, pins, log = fake
    e = engine.prepare(exe, home, pins=pins)
    cache = engine.Cache(tmp_path / "cache", engine.sha256_of(pdf), e.version, 300)
    stats = engine.run(e, pdf, [2, 3, 5], cache)
    assert stats == {"standard": 3, "basic": 3}
    assert sorted(c["argv"][0] for c in calls(log)) == ["--version", "parse", "parse"]
    for tier in ("standard", "basic"):
        assert cache.has(tier, 3) and not cache.has(tier, 4)
        raw = cache.read(tier, 5)
        assert raw.markdown == f"Page p005 by the {tier} tier.\n"
        assert raw.blocks[0]["content"][0]["content"].startswith("Page p005")
    # a second run over a superset only runs the missing page, per tier
    stats = engine.run(e, pdf, [2, 3, 4, 5], cache)
    assert stats == {"standard": 1, "basic": 1}
    parses = [c for c in calls(log) if c["argv"][0] == "parse"]
    assert len(parses) == 4 and parses[-1]["inputs"] == ["p004.png"]
    # nothing left to do: the engine is not started at all
    assert engine.run(e, pdf, [2, 3, 4, 5], cache) == {"standard": 0, "basic": 0}
    assert len([c for c in calls(log) if c["argv"][0] == "parse"]) == 4
    assert not list(cache.root.rglob("*.png"))          # page images are not kept


def test_text_layer_mode_passes_one_page_pdfs_and_caches_apart(fake, pdf, tmp_path):
    exe, home, pins, log = fake
    e = engine.prepare(exe, home, pins=pins)
    ocr = engine.Cache(tmp_path / "cache", engine.sha256_of(pdf), e.version, 300)
    txt = engine.Cache(tmp_path / "cache", engine.sha256_of(pdf), e.version, 300, ocr_mode="txt")
    assert ocr.path("standard", 2) != txt.path("standard", 2)
    assert engine.run(e, pdf, [2, 5], txt, tiers=("standard",)) == {"standard": 2}
    parse = [c for c in calls(log) if c["argv"][0] == "parse"][-1]
    assert parse["argv"][parse["argv"].index("--ocr-mode") + 1] == "txt"
    assert parse["inputs"] == ["p002.pdf", "p005.pdf"]
    assert txt.has("standard", 5) and not ocr.has("standard", 5)
    assert not list(txt.root.rglob("*.pdf"))              # the one-page PDFs are not kept
    with pytest.raises(ValueError):
        engine.Cache(tmp_path / "cache", "a" * 64, "4.1.7", 300, ocr_mode="auto")


def test_cache_key_includes_source_version_and_dpi(fake, pdf, tmp_path):
    exe, home, pins, _ = fake
    e = engine.prepare(exe, home, pins=pins)
    a = engine.Cache(tmp_path / "cache", "a" * 64, "4.1.7", 300)
    b = engine.Cache(tmp_path / "cache", "b" * 64, "4.1.7", 300)
    c = engine.Cache(tmp_path / "cache", "a" * 64, "4.1.8", 300)
    d = engine.Cache(tmp_path / "cache", "a" * 64, "4.1.7", 200)
    paths = {x.path("standard", 1) for x in (a, b, c, d)}
    assert len(paths) == 4
    engine.run(e, pdf, [1], a, tiers=("standard",))
    assert a.has("standard", 1) and not b.has("standard", 1)
    assert not c.has("standard", 1) and not d.has("standard", 1)


def test_a_stopped_run_resumes_where_it_left_off(fake, pdf, tmp_path, monkeypatch):
    exe, home, pins, log = fake
    e = engine.prepare(exe, home, pins=pins)
    cache = engine.Cache(tmp_path / "cache", engine.sha256_of(pdf), e.version, 300)
    monkeypatch.setenv("FAKE_MINERU_STOP_AFTER", "2")
    with pytest.raises(EngineError, match="standard tier stopped after 2 of 4 pages"):
        engine.run(e, pdf, [1, 2, 3, 4], cache, tiers=("standard",))
    assert cache.has("standard", 1) and cache.has("standard", 2) and not cache.has("standard", 3)
    # a half-written zip from the stopped run is thrown away, not trusted
    cache.path("standard", 3).write_bytes(b"PK\x03\x04 truncated")
    monkeypatch.delenv("FAKE_MINERU_STOP_AFTER")
    assert engine.run(e, pdf, [1, 2, 3, 4], cache, tiers=("standard",)) == {"standard": 2}
    last = calls(log)[-1]
    assert last["argv"][0] == "parse" and last["inputs"] == ["p003.png", "p004.png"]
    assert all(cache.has("standard", p) for p in (1, 2, 3, 4))
    with zipfile.ZipFile(cache.path("standard", 3)) as z:
        assert "middle_json.json" in z.namelist()


def test_engine_failure_reports_the_log_tail(fake, pdf, tmp_path):
    exe, home, pins, _ = fake
    e = engine.prepare(exe, home, pins=pins)
    cache = engine.Cache(tmp_path / "cache", engine.sha256_of(pdf), e.version, 300)
    bad = tmp_path / "nopages.pdf"
    doc = pymupdf.open()
    doc.new_page()
    doc.save(str(bad))
    doc.close()
    with pytest.raises(EngineError, match="page 9 is beyond"):
        engine.run(e, bad, [9], cache)
