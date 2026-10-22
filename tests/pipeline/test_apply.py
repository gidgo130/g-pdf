# SPDX-License-Identifier: AGPL-3.0-or-later
"""gpdf-cli apply: confirmed decisions only, the old invisible layer replaced by
Tesseract's text with the scan, links, annotations and bookmarks untouched; refusals;
nothing written outside the output folder but applied.json."""

import hashlib
import json
import os
import shutil
from collections import Counter

import pytest

pytest.importorskip("numpy")
pytest.importorskip("PIL")
pytest.importorskip("wordfreq")
import pymupdf  # noqa: E402

from gpdf import cli  # noqa: E402
from gpdf.pipeline import apply, catalogue, quality, survey, tesseract  # noqa: E402

from . import corpus, synth  # noqa: E402
from .fake_exe import make_exe  # noqa: E402

PUNCT = "¿¡\"'(«.,;:!?)»"
GARBLED, LIST, VISIBLE, LINKS, ROTATED, INK = range(6)           # page indexes of book.pdf


def norm(word):
    return word.strip(PUNCT)


def md5(path):
    return hashlib.md5(open(path, "rb").read()).hexdigest()


def state(path):
    return md5(path), os.stat(path).st_mtime_ns


def image_md5s(page):
    return sorted(hashlib.md5(page.parent.xref_stream_raw(x[0])).hexdigest()
                  for x in page.get_images())


def mid(r):
    return pymupdf.Point((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2)


def scan(doc, seed, layer=True):
    """A 300 dpi 1-bit scan page of Spanish lines with a garbled invisible layer:
    (true words with their boxes, garbled layer text)."""
    lines = synth.spanish_lines(seed)
    dark, words, *_ = synth.typeset(lines)
    page = doc.new_page(width=synth.OCR_W, height=synth.OCR_H)
    synth.put_image(page, synth.g4(doc, synth.to_black(dark)))
    garbled = corpus.noisy("\n".join(lines), 0.4, seed)
    if layer:
        rc = page.insert_textbox(pymupdf.Rect(36, 40, synth.OCR_W - 36, synth.OCR_H - 40),
                                 garbled, fontname="tiro", fontsize=11, render_mode=3)
        assert rc >= 0
    return words, garbled


def build_book(path):
    doc = pymupdf.open()
    truth = [scan(doc, 11), scan(doc, 12), scan(doc, 13), scan(doc, 14)]
    page = doc[VISIBLE]
    page.insert_text((36, 30), "AccesoAbierto", fontname="helv", fontsize=8)  # a stamp
    rot = synth.add_ocr_rotate90(doc, synth.spanish_lines(15))
    garbled = corpus.noisy("\n".join(synth.spanish_lines(15)), 0.4, 15)
    doc[ROTATED].insert_text((60, 60), garbled.replace("\n", " ")[:80], fontname="tiro",
                             fontsize=9, render_mode=3)
    truth.append((rot.words, garbled))
    truth.append(scan(doc, 16, layer=False))
    page = doc[LINKS]
    page.insert_link({"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(50, 60, 200, 80),
                      "uri": "https://example.org/"})
    page.insert_link({"kind": pymupdf.LINK_GOTO, "from": pymupdf.Rect(50, 100, 200, 120),
                      "page": GARBLED, "to": pymupdf.Point(0, 0)})
    page.add_text_annot((300, 300), "nota del lector")
    doc.set_toc([[1, "Uno", 1], [1, "Enlaces", LINKS + 1], [2, "Girada", ROTATED + 1]])
    doc.save(str(path), garbage=3, deflate=True)
    doc.close()
    return truth


@pytest.fixture(scope="module")
def tess():
    try:
        return tesseract.prepare("spa")
    except tesseract.TesseractError as e:
        pytest.skip(f"needs Tesseract 5 with spa: {e}")


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    """root/ with book.pdf (built once) and four small files for the gates."""
    d = tmp_path_factory.mktemp("apply_base")
    truth = build_book(d / "book.pdf")
    for i, name in enumerate(("unconfirmed", "reconfirm", "gone", "changed", "kept",
                              "dropped")):
        text = [corpus.prose(800 + 10 * i + k, 110) for k in range(2)]
        corpus.scan(d / f"{name}.pdf", [(t, corpus.noisy(t, 0.4, k)) for k, t in
                                        enumerate(text)])
    return d, truth


def make_layout(base, tmp_path):
    """A surveyed copy: root/, cat/ (catalogue + decisions), out/."""
    src, truth = base
    root, cat, out = tmp_path / "root", tmp_path / "cat", tmp_path / "out"
    shutil.copytree(src, root)
    survey.run([root], cat, trial=False)
    files = {e["rel"]: e for e in catalogue.load(cat)["files"].values()}

    def dec(rel, action, confirmed=True, pages="all", suggestion=None):
        e = files[rel]
        return e["id"], {"path": e["path"], "action": action, "pages": pages,
                         "confirmed": confirmed,
                         "suggestion_action": suggestion or e["suggestion"]["action"]}

    decisions = dict([dec("book.pdf", "rebuild_layer"),
                      dec("unconfirmed.pdf", "rebuild_layer", confirmed=False),
                      dec("reconfirm.pdf", "rebuild_layer", suggestion="keep_text"),
                      dec("gone.pdf", "rebuild_layer"), dec("changed.pdf", "rebuild_layer"),
                      dec("kept.pdf", "keep_text"), dec("dropped.pdf", "exclude")])
    (cat / catalogue.DECISIONS).write_text(json.dumps({"schema": 1, "files": decisions}),
                                           "utf-8")
    return {"root": root, "cat": cat, "out": out, "truth": truth, "files": files,
            "decisions": decisions}


@pytest.fixture
def lay(base, tmp_path):
    return make_layout(base, tmp_path)


def run(lay, *extra):
    return cli.main(["apply", "--catalogue", str(lay["cat"]), "--derived", str(lay["out"]),
                     *extra])


@pytest.fixture(scope="module")
def applied(base, tess, tmp_path_factory):
    """book.pdf rebuilt once through the CLI (all pages): (layout, output, report)."""
    lay_ = make_layout(base, tmp_path_factory.mktemp("applied"))
    assert run(lay_, "--only", "book", "--jobs", "3") == 0
    out = lay_["out"] / "book_rebuilt.pdf"
    report = json.loads((lay_["out"] / "book_rebuilt.report.json").read_text("utf-8"))
    return lay_, out, report


# ---------------------------------------------------------------------------- the rebuild
def test_statuses_and_report(applied):
    lay, out, report = applied
    assert report["schema"] == 1 and report["action"] == "rebuild_layer"
    assert report["langs"] == "spa" and report["pages_spec"] == "all"
    assert [p["status"] for p in report["pages"]] == [
        "rebuilt", "rebuilt", "visible_text_kept", "rebuilt", "rebuilt", "ocr"]
    for p in report["pages"]:
        if p["status"] == "rebuilt":
            if p["page"] != ROTATED + 1:                # its old layer is one short line
                assert p["old_pct"] >= 15 and p["new_pct"] < 5, p
            assert p["mean_conf"] > 80 and p["new_words"] > 50 and p["seconds"] > 0
    s = report["summary"]
    assert s["counts"]["rebuilt"] == 4 and s["failed"] == []
    assert s["median_before"] >= 15 and s["median_after"] < 5
    assert s["size_ratio"] == pytest.approx(os.path.getsize(out) /
                                            os.path.getsize(lay["root"] / "book.pdf"), abs=1e-3)


def test_scan_untouched_old_words_gone_new_words_once(applied):
    lay, out, _ = applied
    words, garbled = lay["truth"][GARBLED]
    with pymupdf.open(lay["root"] / "book.pdf") as a, pymupdf.open(out) as b:
        assert len(a) == len(b)
        for pa, pb in zip(a, b):
            assert image_md5s(pa) == image_md5s(pb)
            assert pa.get_pixmap(dpi=72).samples == pb.get_pixmap(dpi=72).samples
        page = b[GARBLED]
        text = page.get_text()
        true = Counter(norm(w[4]) for w in words)
        bad = {norm(w) for w in garbled.split()} - set(true)
        got = Counter(norm(w[4]) for w in page.get_text("words"))
        assert bad and not bad & set(got), bad & set(got)       # old layer gone
        once = [w for w, n in true.items() if n == 1 and len(w) >= 6]
        hits = [w for w in once if got[w] == 1 and text.count(w) == 1]
        assert len(hits) >= 0.9 * len(once), set(once) - set(hits)
        assert all(got[w] <= 1 for w in once)                    # never doubled
        before = quality.score_text(a[GARBLED].get_text(), "es").pct
        assert quality.score_text(text, "es").pct < before
        fonts = [x for x in range(1, b.xref_length())
                 if b.xref_get_key(x, "Type")[1] == "/Font"
                 and "GlyphLessFont" in b.xref_get_key(x, "BaseFont")[1]
                 and b.xref_get_key(x, "Subtype")[1] == "/Type0"]
        assert len(fonts) == 1


def test_visible_text_page_unchanged(applied):
    lay, out, _ = applied
    with pymupdf.open(lay["root"] / "book.pdf") as a, pymupdf.open(out) as b:
        assert b[VISIBLE].read_contents() == a[VISIBLE].read_contents()
        assert b[VISIBLE].get_text() == a[VISIBLE].get_text()
        assert "AccesoAbierto" in b[VISIBLE].get_text()


def test_links_annotations_and_bookmarks_kept(applied):
    lay, out, _ = applied
    with pymupdf.open(lay["root"] / "book.pdf") as a, pymupdf.open(out) as b:
        def links(page):
            return [(x["kind"], tuple(round(v) for v in x["from"]), x.get("uri"),
                     x.get("page")) for x in page.get_links()]

        assert len(links(a[LINKS])) == 2
        assert links(b[LINKS]) == links(a[LINKS])
        assert [(x.type, x.info["content"]) for x in b[LINKS].annots()] == [
            (x.type, x.info["content"]) for x in a[LINKS].annots()]
        def toc(doc):                     # saving renumbers objects: compare destinations
            return [(lvl, title, page, d["kind"], d["page"], tuple(d["to"]))
                    for lvl, title, page, d in doc.get_toc(simple=False)]

        assert toc(b) == toc(a)
        assert b.get_toc()[1] == [1, "Enlaces", LINKS + 1]
        assert b[LINKS].get_text().strip() and "nota" not in b[LINKS].get_text()


def test_rotated_page_text_in_place(applied):
    lay, out, _ = applied
    words, _ = lay["truth"][ROTATED]
    with pymupdf.open(out) as b:
        page = b[ROTATED]
        assert page.rotation == 90
        got = page.get_text("words")
        count = Counter(norm(w[4]) for w in words)
        once = [w for w in words if count[norm(w[4])] == 1 and len(norm(w[4])) >= 6][:8]
        for w in once:
            found = [g for g in got if norm(g[4]) == norm(w[4])]
            assert len(found) == 1, norm(w[4])
            c = mid(pymupdf.Rect(found[0][:4]) * page.rotation_matrix)
            t = mid(pymupdf.Rect(w[:4]))
            assert abs(c.x - t.x) <= 3 and abs(c.y - t.y) <= 3, (w[4], c, t)


def test_applied_record(applied):
    lay, out, _ = applied
    data = json.loads((lay["cat"] / apply.APPLIED).read_text("utf-8"))
    (rec,) = data["applied"]
    e = lay["files"]["book.pdf"]
    assert rec["id"] == e["id"] and rec["source"] == e["path"]
    assert rec["action"] == "rebuild_layer" and rec["pages"] == "all"
    assert os.path.samefile(rec["output"], out)
    assert rec["output_sha256"] == hashlib.sha256(out.read_bytes()).hexdigest()
    assert rec["time"] and rec["tool_version"]


# ---------------------------------------------------------------------------- the survey after
def test_survey_after_apply_uses_the_rebuilt_file_and_shows_applied(applied, capsys):
    lay, out, report = applied
    res = survey.run([lay["root"]], lay["cat"], derived=[lay["out"]], trial=False)
    e = next(x for x in res.catalogue["files"].values() if x["rel"] == "book.pdf")
    assert e["working_file"]["path"] == str(out)
    assert e["working_file"]["ocr_pages"] == [1, 2, 4, 5, 6]       # rebuilt and ocr pages
    assert e["measured"]["score"]["weighted"] < e["source_layer"]["score"]["weighted"]
    assert e["applied"]["action"] == "rebuild_layer"
    assert e["applied"]["median_after"] < e["applied"]["median_before"]
    decisions = catalogue.load_decisions(lay["cat"])
    status, _, why = catalogue.decision_status(e, decisions, res.catalogue["files"])
    assert status == catalogue.APPLIED and "rebuild_layer applied: median suspect" in why
    assert e["suggestion"]["action"] != "rebuild_layer"            # the new file is good
    capsys.readouterr()
    assert run(lay, "--only", "book.pdf") == 0                     # nothing to do again
    assert "rebuild_layer (already applied) 1" in capsys.readouterr().out
    data = catalogue.sheet_data(res.catalogue, decisions, lay["cat"])
    view = next(v for v in data["entries"] if v["rel"] == "book.pdf")
    assert view["decision_status"]["status"] == "applied" and view["applied"]


def test_applied_status_needs_the_same_bytes(applied):
    lay, out, _ = applied
    e = {"id": "x", "working_file": {"path": str(out), "sha256": "other"}}
    rec = {"id": "x", "output": str(out), "output_sha256": "abc", "action": "rebuild_layer"}
    assert catalogue.applied_record(e, {"applied": [rec]}) is None
    assert catalogue.applied_record({**e, "working_file": {"path": str(out), "sha256": "abc"}},
                                    {"applied": [rec]}) == rec


# ---------------------------------------------------------------------------- page lists
def test_page_list_touches_only_the_listed_pages(lay, tess):
    d = lay["decisions"]
    book = lay["files"]["book.pdf"]["id"]
    d[book]["pages"] = [GARBLED + 1]
    (lay["cat"] / catalogue.DECISIONS).write_text(json.dumps({"schema": 1, "files": d}),
                                                  "utf-8")
    assert run(lay, "--only", "book.pdf") == 0
    report = json.loads((lay["out"] / "book_rebuilt.report.json").read_text("utf-8"))
    assert [(p["page"], p["status"]) for p in report["pages"]] == [(1, "rebuilt")]
    with pymupdf.open(lay["root"] / "book.pdf") as a, \
            pymupdf.open(lay["out"] / "book_rebuilt.pdf") as b:
        assert b[GARBLED].get_text() != a[GARBLED].get_text()
        for i in range(1, len(a)):
            assert b[i].read_contents() == a[i].read_contents(), i
            assert b[i].get_text() == a[i].get_text(), i


FAILING = '''import subprocess, sys
REAL = {real!r}
if len(sys.argv) > 1 and sys.argv[1].endswith("p00001.png"):
    sys.stderr.write("forced failure for page 1\\n")
    sys.exit(3)
sys.exit(subprocess.run([REAL, *sys.argv[1:]]).returncode)
'''


def test_failed_page_keeps_its_old_layer_and_exits_2(lay, tess, capsys):
    wrapper = make_exe(lay["out"].parent, "tess", FAILING.format(real=tess.exe))
    assert run(lay, "--only", "book", "--tesseract", wrapper, "--jobs", "2") == 2
    report = json.loads((lay["out"] / "book_rebuilt.report.json").read_text("utf-8"))
    p1 = report["pages"][0]
    assert p1["status"] == "failed" and "forced failure for page 1" in p1["error"]
    assert report["pages"][1]["status"] == "rebuilt"
    with pymupdf.open(lay["root"] / "book.pdf") as a, \
            pymupdf.open(lay["out"] / "book_rebuilt.pdf") as b:
        assert b[GARBLED].read_contents() == a[GARBLED].read_contents()
        assert b[GARBLED].get_text() == a[GARBLED].get_text()
    assert "p. 1 failed" in capsys.readouterr().out


def test_text_the_redaction_cannot_remove_keeps_the_old_layer(lay, tess, monkeypatch):
    monkeypatch.setattr(apply, "removable", lambda doc, index: index != GARBLED)
    assert run(lay, "--only", "book.pdf") == 0
    report = json.loads((lay["out"] / "book_rebuilt.report.json").read_text("utf-8"))
    assert report["pages"][GARBLED]["status"] == "text_not_removable"
    assert report["pages"][LIST]["status"] == "rebuilt"
    with pymupdf.open(lay["root"] / "book.pdf") as a, \
            pymupdf.open(lay["out"] / "book_rebuilt.pdf") as b:
        assert b[GARBLED].read_contents() == a[GARBLED].read_contents()
        assert b[GARBLED].get_text() == a[GARBLED].get_text()      # not doubled either


def test_removable_on_a_one_page_copy(base):
    with pymupdf.open(base[0] / "book.pdf") as doc:
        before = doc.tobytes(no_new_id=True)
        assert apply.removable(doc, GARBLED)
        assert doc.tobytes(no_new_id=True) == before                 # the probe is a copy


# ---------------------------------------------------------------------------- gates
def test_gates_dry_run_and_read_only(lay, capsys):
    os.remove(lay["root"] / "gone.pdf")
    with open(lay["root"] / "changed.pdf", "ab") as f:
        f.write(b"\n% changed\n")
    watched = [lay["root"] / n for n in ("book.pdf", "unconfirmed.pdf", "reconfirm.pdf",
                                         "kept.pdf", "dropped.pdf", "changed.pdf")]
    watched += [lay["cat"] / catalogue.DECISIONS, lay["cat"] / catalogue.CATALOGUE]
    before = {p: state(p) for p in watched}
    assert run(lay, "--dry-run") == 2
    out = capsys.readouterr().out
    assert "would rebuild_layer  book.pdf: pages all, --lang spa" in out
    assert "refused unconfirmed.pdf: not confirmed" in out
    assert "refused reconfirm.pdf: needs re-confirm: suggestion changed" in out
    assert "refused gone.pdf: source not found" in out
    assert "refused changed.pdf: the source changed since the survey" in out
    assert "nothing to write: exclude 1, keep_text 1" in out
    assert "1 file(s) would be written, 4 refused; nothing written" in out
    assert not lay["out"].exists() and not (lay["cat"] / apply.APPLIED).exists()
    assert {p: state(p) for p in watched} == before


def test_missing_entry_is_refused(lay, capsys):
    os.remove(lay["root"] / "gone.pdf")
    survey.run([lay["root"]], lay["cat"], trial=False)            # now "missing"
    assert run(lay, "--dry-run", "--only", "gone") == 2
    assert "refused gone.pdf: missing" in capsys.readouterr().out


def test_existing_output_refused_without_force(lay, tess, capsys):
    lay["out"].mkdir()
    (lay["out"] / "book_rebuilt.pdf").write_bytes(b"old")
    assert run(lay, "--only", "book.pdf") == 2
    assert "book_rebuilt.pdf already exists (pass --force" in capsys.readouterr().out
    assert (lay["out"] / "book_rebuilt.pdf").read_bytes() == b"old"
    assert not (lay["cat"] / apply.APPLIED).exists()
    watched = [lay["root"] / "book.pdf", lay["cat"] / catalogue.DECISIONS,
               lay["cat"] / catalogue.CATALOGUE]
    before = {p: state(p) for p in watched}
    assert run(lay, "--only", "book.pdf", "--force") == 0
    assert (lay["out"] / "book_rebuilt.pdf").read_bytes()[:5] == b"%PDF-"
    assert {p: state(p) for p in watched} == before


def test_output_folder_inside_a_source_folder_refused(lay, capsys):
    rc = cli.main(["apply", "--catalogue", str(lay["cat"]), "--derived",
                   str(lay["root"] / "out"), "--dry-run"])
    assert rc == 1 and "inside the source folder" in capsys.readouterr().err
