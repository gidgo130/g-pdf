# SPDX-License-Identifier: AGPL-3.0-or-later
"""Marker as the third opinion: finding and pinning it, the server it needs, the per-page
cache, resuming, and its JSON as MinerU-shaped blocks. Fakes stand in for marker and
llama-server (tests/pipeline/fake_marker.py)."""

import json

import pymupdf
import pytest

from gpdf.pipeline.mathocr import marker
from gpdf.pipeline.mathocr.engine import EngineError

from .fake_marker import make_fake_marker, make_fake_server, make_gguf


@pytest.fixture
def fake(tmp_path, monkeypatch):
    exe = make_fake_marker(tmp_path)
    server = make_fake_server(tmp_path)
    gguf, pins = make_gguf(tmp_path)
    log = tmp_path / "marker.jsonl"
    monkeypatch.setenv("FAKE_MARKER_LOG", str(log))
    monkeypatch.setenv("FAKE_LLAMA_LOG", str(tmp_path / "llama.jsonl"))
    for v in ("FAKE_MARKER_STOP_AFTER", "FAKE_MARKER_PAGES", "FAKE_LLAMA_BUILD",
              marker.ENV_VAR, marker.SERVER_ENV_VAR, marker.GGUF_ENV_VAR):
        monkeypatch.delenv(v, raising=False)
    return exe, server, gguf, pins, log


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


def test_prepare_reads_versions_build_and_gguf_hashes(fake):
    exe, server, gguf, pins, _ = fake
    m = marker.prepare(exe, server, gguf, pins=pins)
    assert m.packages == {"marker-pdf": "2.1.0", "surya-ocr": "0.23.0"}
    assert m.llama_build == "11765" and m.gguf_hashes == pins and m.unpinned == []
    d = m.describe()
    assert d["pinned_llama_build"] == marker.PINNED_LLAMA_BUILD and d["gguf"] == pins


def test_unpinned_versions_build_or_weights_stop_unless_allowed(fake, tmp_path, monkeypatch):
    exe, server, gguf, pins, _ = fake
    other = make_fake_marker(tmp_path / "o", marker="2.2.0")
    with pytest.raises(EngineError, match="marker-pdf 2.2.0 found"):
        marker.prepare(other, server, gguf, pins=pins)
    assert marker.prepare(other, server, gguf, pins=pins, allow_unpinned=True).unpinned == [
        "marker-pdf"]
    monkeypatch.setenv("FAKE_LLAMA_BUILD", "12000")
    with pytest.raises(EngineError, match="build 12000"):
        marker.prepare(exe, server, gguf, pins=pins)
    monkeypatch.delenv("FAKE_LLAMA_BUILD")
    (gguf / "surya-2.5.gguf").write_bytes(b"other")
    with pytest.raises(EngineError, match="surya-2.5.gguf has sha256"):
        marker.prepare(exe, server, gguf, pins=pins)


def test_finding_by_folder_environment_and_hf_cache(fake, tmp_path, monkeypatch):
    exe, server, gguf, pins, _ = fake
    monkeypatch.setenv(marker.ENV_VAR, exe)
    monkeypatch.setenv(marker.SERVER_ENV_VAR, str(tmp_path / "llama"))
    assert marker.find() == exe and marker.find_server().endswith(("llama-server.cmd",
                                                                   "llama-server"))
    snap = tmp_path / "hf" / "models--datalab-to--surya-ocr-2.5-gguf" / "snapshots" / "abc"
    snap.mkdir(parents=True)
    for f in pins:
        (snap / f).write_bytes((gguf / f).read_bytes())
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "hf"))
    assert marker.find_gguf() == snap
    with pytest.raises(EngineError, match="not both found"):
        marker.find_gguf(tmp_path)
    with pytest.raises(EngineError, match="marker not found at"):
        marker.find(tmp_path / "nowhere")


def test_run_caches_each_page_and_stops_the_server(fake, pdf, tmp_path):
    exe, server, gguf, pins, log = fake
    m = marker.prepare(exe, server, gguf, pins=pins)
    cache = marker.Cache(tmp_path / "cache", "ab" * 32, m.version)
    seen = []
    n = marker.run(m, pdf, [2, 3, 5], cache, progress=lambda d, t: seen.append((d, t)),
                   log_dir=tmp_path / "logs", chunk=2)
    assert n == 3 and seen[-1] == (3, 3)
    assert cache.path(3) == (tmp_path / "cache" / ("ab" * 8) / "marker-2.1.0" / "192" / "marker"
                             / "p003.zip")
    page = cache.read(5)
    assert page["block_type"] == "Page" and "p005" in page["children"][0]["html"]
    c = calls(log)
    assert len(c) == 1 and c[0]["inputs"] == ["c001.pdf", "c002.pdf"]
    assert c[0]["backend"] == "llamacpp" and c[0]["url"].endswith("/v1")
    srv = [json.loads(s) for s in (tmp_path / "llama.jsonl").read_text("utf-8").splitlines()]
    assert any("--special" in s["argv"] and "--jinja" in s["argv"] for s in srv)
    assert not (cache.dir().parent / "_work").exists()
    # the server is gone: its port no longer answers
    import urllib.request
    port = next(s["argv"] for s in srv if "--port" in s["argv"])
    port = port[port.index("--port") + 1]
    with pytest.raises(OSError):
        urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2)
    assert marker.run(m, pdf, [2, 3, 5], cache) == 0 and len(calls(log)) == 1


def test_a_stopped_run_resumes_from_the_finished_chunks(fake, pdf, tmp_path, monkeypatch):
    exe, server, gguf, pins, log = fake
    m = marker.prepare(exe, server, gguf, pins=pins)
    cache = marker.Cache(tmp_path / "cache", "cd" * 32, m.version)
    monkeypatch.setenv("FAKE_MARKER_STOP_AFTER", "1")
    with pytest.raises(EngineError, match="2 of 4 pages left"):
        marker.run(m, pdf, [1, 2, 3, 4], cache, chunk=2)
    assert cache.missing([1, 2, 3, 4]) == [3, 4]
    monkeypatch.delenv("FAKE_MARKER_STOP_AFTER")
    assert marker.run(m, pdf, [1, 2, 3, 4], cache, chunk=2) == 2
    assert calls(log)[-1]["inputs"] == ["c001.pdf"]


def test_marker_json_becomes_mineru_shaped_blocks():
    page = {"block_type": "Page", "bbox": [0, 0, 200, 400], "children": [
        {"id": "a", "block_type": "PageHeader", "bbox": [0, 0, 200, 20], "html": "<p>12 HEAD</p>"},
        {"id": "b", "block_type": "Text", "bbox": [20, 40, 180, 80],
         "html": "<p>where <math>a_{12} &gt; 1</math> and <i>b</i> is <math>x</math>.</p>"},
        {"id": "c", "block_type": "Equation", "bbox": [40, 100, 160, 140],
         "html": '<p block-type="Equation"><math display="block">x &lt; y \\tag{1.2}</math></p>'},
        {"id": "d", "block_type": "ListGroup", "bbox": [20, 150, 180, 200],
         "html": "<ul><content-ref src='e'></content-ref></ul>",
         "children": [{"id": "e", "block_type": "ListItem", "html": "<li>(i) <math>y</math></li>"}]},
        {"id": "f", "block_type": "Table", "bbox": [0, 300, 200, 380], "html": "<table></table>"},
    ]}
    b = marker.blocks(page)
    assert [x["type"] for x in b] == ["text", "equation", "text", "table"]
    assert b[0]["bbox"] == [0.1, 0.1, 0.9, 0.2]
    assert b[0]["content"] == [{"type": "text", "content": "where "},
                               {"type": "equation_inline", "content": "a_{12} > 1"},
                               {"type": "text", "content": " and b is "},
                               {"type": "equation_inline", "content": "x"},
                               {"type": "text", "content": "."}]
    assert b[1]["content"] == "x < y \\tag{1.2}"
    assert [p["content"] for p in b[2]["content"] if p["type"] == "equation_inline"] == ["y"]
    assert marker.has_math(b) and not marker.has_math([b[3]])


def test_a_display_inside_a_text_block_becomes_its_own_display():
    page = {"bbox": [0, 0, 100, 100], "children": [
        {"id": "t", "block_type": "Text", "bbox": [0, 0, 100, 50],
         "html": '<p>so <math display="block">z = 1</math> holds</p>'}]}
    b = marker.blocks(page)
    assert [x["type"] for x in b] == ["text", "equation"] and b[1]["content"] == "z = 1"
