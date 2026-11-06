# SPDX-License-Identifier: AGPL-3.0-or-later
"""gpdf-cli math-export end to end with the fake engine: the output layout, the page
files, text.md, review.jsonl, meta.json, run.json, the cache reuse, --pages, errors."""

import json

import pymupdf
import pytest

from gpdf import cli
from gpdf.pipeline.mathocr import engine

from .fake_mineru import make_fake_mineru, make_home


def T(text, y=0.3, kind="text", **extra):
    b = {"type": kind, "bbox": [0.1, y, 0.9, y + 0.05],
         "content": [{"type": "text", "content": text}]}
    b.update(extra)
    return b


def P(*parts, y=0.3):
    content = [{"type": "equation_inline", "content": p[1]} if isinstance(p, tuple)
               else {"type": "text", "content": p} for p in parts]
    return {"type": "text", "bbox": [0.1, y, 0.9, y + 0.05], "content": content}


def EQ(tex, y=0.5):
    return {"type": "equation", "bbox": [0.2, y, 0.8, y + 0.06], "content": tex}


def NUM(n, y=0.06):
    return {"type": "page_number", "bbox": [0.09, y, 0.12, y + 0.015],
            "content": [{"type": "text", "content": str(n)}]}


def page_script():
    """Four file pages (printed 44-47): a chapter opener with a banner-like header and
    a number at the foot, a disagreement, an untagged display split over pages 3-4, an
    inline difference, a stray |...| the basic tier reads without bars."""
    head = T("RUNNING HEAD", 0.06, "header")
    std = {
        "p001": [T("A Book. Copyright 2000 Publisher. ISBN 0-000-00000-0", 0.06, "header"),
                 T("CHAPTER 7", 0.15), T("Exponential Laws", 0.2, "doc_title", level=1),
                 T("1 INTRODUCTION", 0.3, "paragraph_title", level=2),
                 P("The density function is", y=0.4),
                 EQ(r"f(x) = \lambda e^{-\lambda x}, \qquad x > 0 \tag{7.1}", 0.5),
                 NUM(44, 0.9)],
        "p002": [NUM(45), head, P("Let ", ("eq", r"x_1"), " be fixed. Then", y=0.2),
                 EQ(r"x^2 + y^2 = z^2 \tag{7.2}", 0.4),
                 EQ(r"\left| Y_j = \frac{X_j}{X_0} \right|", 0.6),
                 P("Finally the sum", y=0.8), EQ(r"\sum_{1 \le i < j \le k} a_{ij}", 0.9)],
        "p003": [NUM(46), head, P("Text before a long display", y=0.2),
                 EQ(r"p(x_1, x_2) = \exp\{-(a_0 + a_1 x_1", 0.9)],
        "p004": [NUM(47), head, EQ(r"+ a_2 x_2)\} \tag{7.4}", 0.1), P("ends here.", y=0.3)],
    }
    basic = {
        "p001": std["p001"],
        "p002": [NUM(45), head, P("Let ", ("eq", r"x_2"), " be fixed. Then", y=0.2),
                 EQ(r"x^2 + y^2 = z^3 \tag{7.2}", 0.4),
                 EQ(r"Y_j = \frac{X_j}{X_0}", 0.6),
                 P("Finally the sum", y=0.8), EQ(r"\sum_{1 \le i < j \le k} a_{ij}", 0.9)],
        "p003": std["p003"],
        "p004": std["p004"],
    }

    def md(blocks):
        return "\n\n".join(b["content"] if isinstance(b["content"], str) else
                           " ".join(str(p["content"]) for p in b["content"]) for b in blocks) + "\n"

    return {stem: {"standard": {"markdown": md(std[stem]), "blocks": std[stem]},
                   "basic": {"markdown": md(basic[stem]), "blocks": basic[stem]}}
            for stem in std}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    exe = make_fake_mineru(tmp_path)
    home, pins = make_home(tmp_path)
    monkeypatch.setattr(engine, "PINNED_MODELS", pins)
    script = tmp_path / "pages.json"
    script.write_text(json.dumps(page_script()), "utf-8")
    log = tmp_path / "calls.jsonl"
    monkeypatch.setenv("FAKE_MINERU_PAGES", str(script))
    monkeypatch.setenv("FAKE_MINERU_LOG", str(log))
    monkeypatch.delenv("FAKE_MINERU_STOP_AFTER", raising=False)
    monkeypatch.delenv("FAKE_MINERU_VERSION", raising=False)
    monkeypatch.setenv("GPDF_TESSERACT", str(tmp_path / "no-tesseract"))   # fallback off
    pdf = tmp_path / "book.pdf"
    doc = pymupdf.open()
    for i in range(5):
        pg = doc.new_page(width=300, height=400)
        pg.insert_text((40, 60), f"page {i + 1}", fontsize=12)
    doc.save(str(path := pdf))
    doc.close()
    return exe, home, path, log


def run(exe, home, pdf, out, *extra):
    return cli.main(["math-export", str(pdf), "--out", str(out), "--mineru", exe,
                     "--mineru-home", str(home), "--no-check-identities", *extra])


def parses(log):
    return [json.loads(s) for s in log.read_text("utf-8").splitlines()
            if json.loads(s)["argv"][0] == "parse"]


def test_math_export_writes_the_atlas_layout(setup, tmp_path, capsys):
    exe, home, pdf, log = setup
    out = tmp_path / "out"
    assert run(exe, home, pdf, out, "--pages", "1-4") == 0
    root = out / "book"
    assert sorted(p.name for p in root.iterdir()) == [
        "cache", "crops", "heartbeat.json", "logs", "meta.json", "pages", "review.jsonl",
        "run.json", "text.md"]
    assert sorted(p.name for p in (root / "pages").iterdir()) == [
        "p001.flags.json", "p001.md", "p002.flags.json", "p002.md", "p003.flags.json", "p003.md",
        "p004.flags.json", "p004.md"]
    p1 = (root / "pages" / "p001.md").read_text("utf-8")
    assert p1.startswith('<!-- page printed="44" file="1" -->\n\n# 7 Exponential Laws\n\n'
                         "## 1 INTRODUCTION\n\nThe density function is\n\n$$\n"
                         "f(x) = \\lambda e^{-\\lambda x}, \\qquad x > 0\n\\tag{7.1}\n$$\n")
    flags1 = json.loads((root / "pages" / "p001.flags.json").read_text("utf-8"))
    assert {"type": "dropped", "text": "A Book. Copyright 2000 Publisher. ISBN 0-000-00000-0",
            "what": "running_head"} in flags1
    p2 = (root / "pages" / "p002.md").read_text("utf-8")
    assert "$$\nY_j = \\frac{X_j}{X_0}\n$$" in p2                 # bars stripped
    flags2 = json.loads((root / "pages" / "p002.flags.json").read_text("utf-8"))
    assert [f["what"] for f in flags2 if f["type"] == "auto_fix"] == ["bars"]
    p4 = (root / "pages" / "p004.md").read_text("utf-8")
    assert p4.startswith('<!-- page printed="47" file="4" -->\n$$\n+ a_2 x_2)\\}\n\\tag{7.4}\n$$')
    flags3 = json.loads((root / "pages" / "p003.flags.json").read_text("utf-8"))
    assert flags3[0]["type"] == "page_anomaly" and flags3[0]["what"] == "split_display"
    text = (root / "text.md").read_text("utf-8")
    assert text.count("<!-- page printed=") == 4 and text.count("$$") % 2 == 0
    assert text.index('file="3"') < text.index('file="4"')
    items = [json.loads(s) for s in (root / "review.jsonl").read_text("utf-8").splitlines()]
    by_id = {it["id"]: it for it in items}
    assert by_id["p002-d01"]["kind"] == "disagree" and by_id["p002-d01"]["tag"] == "7.2"
    assert by_id["p002-d01"]["latex_second"] == "x^2 + y^2 = z^3"
    assert by_id["p002-d03"]["kind"] == "stack"
    span = by_id["p002-inline"]["spans"][0]
    assert {k: span[k] for k in ("paragraph", "latex_primary", "latex_second")} == {
        "paragraph": 1, "latex_primary": "x_1", "latex_second": "x_2"}
    assert span["crop"] == "crops/p002-inline-s1.png" and (root / span["crop"]).is_file()
    assert len(span["bbox"]) == 4 and len(span["line_box"]) == 4
    assert all((root / it["crop"]).stat().st_size > 0 for it in items)
    assert by_id["p002-inline"]["crops"][0] == "crops/p002-inline-1.png"
    meta = json.loads((root / "meta.json").read_text("utf-8"))
    assert meta["engine"]["version"] == "4.1.7" and meta["engine"]["unpinned"] == []
    assert set(meta["engine"]["models"]) == set(engine.PINNED_MODELS)
    assert meta["source"]["pages"] == [1, 2, 3, 4] and meta["source"]["sha256"] == engine.sha256_of(pdf)
    assert meta["labels"]["runs"][0]["pages"] == [1, 4] and meta["labels"]["steps"] == []
    assert meta["options"]["check_identities"] is False
    rec = json.loads((root / "run.json").read_text("utf-8"))
    assert rec["totals"]["pages"] == 4 and rec["totals"]["displays"] == 6
    assert rec["totals"]["items"] == {"disagree": 1, "stack": 1, "inline": 1, "tag": 1}
    assert by_id["p004-d01"]["reasons"] == ["tag 7.4 skips 7.3"]
    assert rec["pages"][1] == {"file": 2, "printed": "45", "inferred": None,
                               "label_source": "engine", "displays": 3, "inline_spans": 1,
                               "items": {"disagree": 1, "stack": 1, "inline": 1}, "dropped": 1,
                               "identities": {}, "marker": False, "no_marker_reading": [],
                               "inline_flagged": 1, "inline_missing_runs": 0}
    assert rec["cache_bytes"] > 0 and rec["engine_seconds"]["parsed"] == {"standard": 4, "basic": 4}
    out_text = capsys.readouterr().out
    assert "4 pages, 6 displays" in out_text
    assert "review items: disagree 1, inline 1, stack 1, tag 1" in out_text
    assert "label run: file 1-4 printed 44-47 (offset +43, 4 read)" in out_text


def test_a_second_run_reuses_the_cache_and_pages_can_be_narrowed(setup, tmp_path):
    exe, home, pdf, log = setup
    out = tmp_path / "out"
    assert run(exe, home, pdf, out, "--pages", "1-4") == 0
    assert len(parses(log)) == 2
    assert run(exe, home, pdf, out, "--pages", "2-3") == 0
    assert len(parses(log)) == 2                      # nothing new to parse
    rec = json.loads((out / "book" / "run.json").read_text("utf-8"))
    assert [r["file"] for r in rec["pages"]] == [2, 3]
    assert rec["engine_seconds"]["parsed"] == {"standard": 0, "basic": 0}
    assert sorted(p.name for p in (out / "book" / "pages").iterdir()) == [
        "p001.flags.json", "p001.md", "p002.flags.json", "p002.md", "p003.flags.json", "p003.md",
        "p004.flags.json", "p004.md"]                 # older page files are left alone
    text = (out / "book" / "text.md").read_text("utf-8")
    assert text.count("<!-- page printed=") == 2


def test_marker_runs_after_mineru_on_the_math_pages_only(setup, tmp_path, monkeypatch):
    from gpdf.pipeline.mathocr import marker

    from .fake_marker import make_fake_marker, make_fake_server, make_gguf

    exe, home, pdf, log = setup
    mexe, server, (gguf, pins) = (make_fake_marker(tmp_path), make_fake_server(tmp_path),
                                  make_gguf(tmp_path))
    monkeypatch.setattr(marker, "PINNED_GGUF", pins)
    mlog = tmp_path / "marker.jsonl"
    monkeypatch.setenv("FAKE_MARKER_LOG", str(mlog))
    out = tmp_path / "out"
    args = ("--pages", "1-5", "--marker", mexe, "--llama-server", server, "--marker-gguf",
            str(gguf))
    assert run(exe, home, pdf, out, *args) == 0
    m = [json.loads(s) for s in mlog.read_text("utf-8").splitlines()]
    assert len(m) == 1 and m[0]["inputs"] == ["c001.pdf"]
    cached = sorted(p.name for p in (out / "book" / "cache").rglob("marker/p*.zip"))
    assert cached == ["p001.zip", "p002.zip", "p003.zip", "p004.zip"]     # p005: no math
    meta = json.loads((out / "book" / "meta.json").read_text("utf-8"))
    assert meta["marker"]["packages"]["marker-pdf"] == "2.1.0"
    assert meta["marker"]["llama_build"] == "11765" and meta["marker"]["gguf"] == pins
    rec = json.loads((out / "book" / "run.json").read_text("utf-8"))
    assert rec["engine_seconds"]["marker_pages"] == 4
    assert rec["engine_seconds"]["marker_parsed"] == 4
    assert run(exe, home, pdf, out, *args) == 0                     # all cached: no call
    assert len(mlog.read_text("utf-8").splitlines()) == 1
    assert run(exe, home, pdf, out, "--pages", "1-5", "--no-marker") == 0
    meta = json.loads((out / "book" / "meta.json").read_text("utf-8"))
    assert meta["marker"] is None


def test_marker_readings_reach_the_review_queue(setup, tmp_path, monkeypatch):
    from gpdf.pipeline.mathocr import marker

    from .fake_marker import make_fake_marker, make_fake_server, make_gguf

    exe, home, pdf, log = setup
    mexe, server, (gguf, pins) = (make_fake_marker(tmp_path), make_fake_server(tmp_path),
                                  make_gguf(tmp_path))
    monkeypatch.setattr(marker, "PINNED_GGUF", pins)
    # page 1: Marker reads the density with a different rate and has a second display
    script = {"p001": [
        {"block_type": "Text", "bbox": [30, 160, 270, 180], "html": "<p>The density function is</p>"},
        {"block_type": "Equation", "bbox": [60, 200, 240, 224],
         "html": '<math display="block">f(x) = \\mu e^{-\\mu x}, \\qquad x > 0 \\tag{7.1}</math>'},
        {"block_type": "Equation", "bbox": [60, 300, 240, 324],
         "html": '<math display="block">F(x) = 1 - e^{-\\lambda x}</math>'}]}
    (tmp_path / "mk.json").write_text(json.dumps(script), "utf-8")
    monkeypatch.setenv("FAKE_MARKER_PAGES", str(tmp_path / "mk.json"))
    out = tmp_path / "out"
    assert run(exe, home, pdf, out, "--pages", "1", "--marker", mexe, "--llama-server", server,
               "--marker-gguf", str(gguf)) == 0
    items = {json.loads(s)["id"]: json.loads(s)
             for s in (out / "book" / "review.jsonl").read_text("utf-8").splitlines()}
    assert items["p001-d01"]["kind"] == "marker"
    assert items["p001-d01"]["latex_marker"].startswith(r"f(x) = \mu e^{-\mu x}")
    assert items["p001-m01"]["kind"] == "missing_display"
    assert items["p001-m01"]["latex_second"] == r"F(x) = 1 - e^{-\lambda x}"
    assert items["p001-m01"]["crop"] == "crops/p001-m01.png"
    meta = json.loads((out / "book" / "meta.json").read_text("utf-8"))
    assert meta["options"]["marker_rule"] == "value" and meta["options"]["inline_with"] == "either"


def test_engine_problems_are_errors_not_tracebacks(setup, tmp_path, monkeypatch, capsys):
    exe, home, pdf, log = setup
    out = tmp_path / "out"
    assert run(exe, home, pdf, out, "--pages", "40") == 1
    assert "no pages selected" in capsys.readouterr().err
    monkeypatch.setenv("FAKE_MINERU_VERSION", "4.2.0")
    assert run(exe, home, pdf, out, "--pages", "1") == 1
    assert "4.2.0 found, 4.1.7 expected" in capsys.readouterr().err
    assert run(exe, home, pdf, out, "--pages", "1", "--allow-unpinned") == 0
    meta = json.loads((out / "book" / "meta.json").read_text("utf-8"))
    assert meta["engine"]["unpinned"] == ["version"]
    assert "running unpinned: version" in meta["notices"]
    assert meta["labels"]["runs"][0]["pages"] == [1, 1]       # a lone page keeps its number
    assert cli.main(["math-export", str(pdf), "--out", str(out), "--mineru",
                     str(tmp_path / "nowhere"), "--mineru-home", str(home)]) == 1
    assert "mineru-kit not found" in capsys.readouterr().err


def test_identity_pass_runs_when_the_extra_is_installed(setup, tmp_path):
    pytest.importorskip("sympy")
    pytest.importorskip("antlr4")
    exe, home, pdf, log = setup
    out = tmp_path / "out"
    assert cli.main(["math-export", str(pdf), "--out", str(out), "--mineru", exe,
                     "--mineru-home", str(home), "--pages", "1-2", "--check-identities"]) == 0
    rec = json.loads((out / "book" / "run.json").read_text("utf-8"))
    assert rec["pages"][0]["identities"] == {"checked": 1, "passed": 1, "failed": 0,
                                             "inconclusive": 0, "skipped": 0}
    assert rec["totals"]["identities"]["skipped"] == 3


def test_the_run_writes_a_heartbeat_for_a_stall_watcher(setup, tmp_path, monkeypatch):
    # every stage, and every page of the identity pass, beats: a watcher sees progress
    # through a long silent stage and can tell a stuck one
    pytest.importorskip("sympy")
    pytest.importorskip("antlr4")
    from gpdf.pipeline.mathocr import run as R
    beats = []
    real = R._Heartbeat.beat
    monkeypatch.setattr(R._Heartbeat, "beat", lambda self, stage, **kw: (
        beats.append((stage, kw.get("page"))), real(self, stage, **kw)))
    exe, home, pdf, log = setup
    out = tmp_path / "out"
    assert cli.main(["math-export", str(pdf), "--out", str(out), "--mineru", exe,
                     "--mineru-home", str(home), "--pages", "1-2", "--check-identities"]) == 0
    hb = json.loads((out / "book" / "heartbeat.json").read_text("utf-8"))
    assert hb["stage"] == "done" and hb["pid"] > 0
    assert ("identities", 1) in beats and ("identities", 2) in beats
    assert [b[0] for b in beats][0] == "engine"


def test_section_constants_and_identity_items_from_the_pass():
    pytest.importorskip("sympy")
    pytest.importorskip("antlr4")
    from types import SimpleNamespace as NS

    from gpdf.pipeline.mathocr import run as R

    def disp(latex):
        return NS(kind="display", md="", display=NS(latex=latex, tag=None, bbox=None))

    def page(file, *items):
        return NS(file=file, items=list(items), label=NS(marker=str(file)))

    pages = [page(1, NS(kind="heading", md="## 7.1 Logarithmic", display=None),
                  disp(r"\Pr[X = x] = C \frac{\theta^{x}}{x}, \quad x = 1, 2, \ldots"),
                  NS(kind="para", md="where", display=None),
                  disp(r"C = -1 / \log(1 - \theta)")),
             page(2, NS(kind="heading", md="## 7.2 Poisson", display=None),
                  disp(r"\Pr[X = x] = \frac{e^{-\theta} \theta^{x + 1}}{x!}"))]
    items = {1: [], 2: []}
    counts = R._identities(pages, items, [(1, 2)], lambda s: None)
    assert counts[1]["passed"] == 1 and counts[2]["failed"] == 1
    assert items[1] == []                                   # C came from the section
    it = items[2][0]
    assert it.kind == "identity" and it.extra["identity"] == ["sums to 1"]
    assert it.extra["identity_details"][0]["support"] == "{0,1,...}"


def test_the_identity_pass_writes_one_record_per_checked_display(setup, tmp_path):
    pytest.importorskip("sympy")
    pytest.importorskip("antlr4")
    exe, home, pdf, log = setup
    out = tmp_path / "out"
    assert cli.main(["math-export", str(pdf), "--out", str(out), "--mineru", exe,
                     "--mineru-home", str(home), "--pages", "1-2", "--check-identities"]) == 0
    recs = [json.loads(s) for s in (out / "book" / "identities.jsonl").read_text("utf-8").splitlines()]
    assert [(r["file"], r["display"], r["kind"], r["status"]) for r in recs] == [
        (1, 1, "density", "passed")]
    assert recs[0]["latex"].startswith("f(x) = ")
