# SPDX-License-Identifier: AGPL-3.0-or-later
"""gpdf-cli survey: catalogue identity and merging across runs, derived working files,
the owner's decisions file (read, never written), and that the survey is read-only."""

import hashlib
import json
import os
import re
import shutil
import time

import pytest

pytest.importorskip("numpy")
pytest.importorskip("PIL")
pytest.importorskip("wordfreq")

from gpdf import cli  # noqa: E402
from gpdf.pipeline import catalogue, survey  # noqa: E402

from . import corpus  # noqa: E402

GUTENBERG = ("The Project Gutenberg eBook of Obra\n\n*** START OF THE PROJECT GUTENBERG EBOOK "
             "OBRA ***\n\n{body}\n\n*** END OF THE PROJECT GUTENBERG EBOOK OBRA ***\nEnd.\n")


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    """Source files built once; tests copy them (``layout``)."""
    d = tmp_path_factory.mktemp("cat_base")
    pages = [corpus.prose(100 + i, 220) for i in range(5)]
    corpus.born_digital(d / "obra.pdf", pages, title="Obra", toc=[[1, "Uno", 1]])
    corpus.txt(d / "obra.txt", GUTENBERG.format(body="\n".join(pages)))
    corpus.born_digital(d / "otra.pdf", [corpus.prose(200 + i, 220) for i in range(3)])
    corpus.born_digital(d / "b.pdf", [corpus.prose(300 + i, 220) for i in range(3)])
    good = [corpus.prose(400 + i, 110) for i in range(3)]
    corpus.scan(d / "libro.pdf", [(t, corpus.noisy(t, 0.3, i)) for i, t in enumerate(good)])
    corpus.scan(d / "libro_split_ocr.pdf", [(t, t) for t in good])
    (d / "libro_split_ocr.report.json").write_text(json.dumps(
        {"schema": 1, "pages": [{"page": 1, "status": "ocr"}, {"page": 2, "status": "ocr"},
                                {"page": 3, "status": "has_text"}]}), "utf-8")
    return d


@pytest.fixture
def layout(base, tmp_path):
    """A/ (obra.pdf, obra.txt, sub/otra.pdf, libro.pdf), B/ (b.pdf), derived/ (the
    working file of libro.pdf and its report), cat/ for the catalogue."""
    a, b, der = tmp_path / "A", tmp_path / "B", tmp_path / "derived"
    (a / "sub").mkdir(parents=True)
    b.mkdir()
    der.mkdir()
    for name, dest in (("obra.pdf", a), ("obra.txt", a), ("otra.pdf", a / "sub"),
                       ("libro.pdf", a), ("b.pdf", b), ("libro_split_ocr.pdf", der),
                       ("libro_split_ocr.report.json", der)):
        shutil.copy2(base / name, dest / name)
    return {"A": a, "B": b, "derived": der, "cat": tmp_path / "cat", "tmp": tmp_path}


def survey_cli(lay, *roots, extra=()):
    return cli.main(["survey", *(str(lay[r]) for r in roots), "--catalogue", str(lay["cat"]),
                     "--derived", str(lay["derived"]), "--no-trial", *extra])


def load(lay):
    return json.loads((lay["cat"] / catalogue.CATALOGUE).read_text("utf-8"))


def by_rel(cat):
    return {e["rel"].replace("\\", "/"): e for e in cat["files"].values()}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_decisions(lay, files):
    path = lay["cat"] / catalogue.DECISIONS
    text = json.dumps({"schema": 1, "saved": "2026-10-21T12:00:00", "files": files}, indent=1)
    path.write_text(text, "utf-8")
    return path, path.read_bytes()


def snapshot(*folders, skip=None):
    """{path: (md5, mtime_ns)} of every file under the folders, except under ``skip``."""
    out = {}
    for folder in folders:
        for dirpath, _, names in os.walk(folder):
            if skip and catalogue.under(dirpath, skip):
                continue
            for n in names:
                p = os.path.join(dirpath, n)
                out[p] = (hashlib.md5(open(p, "rb").read()).hexdigest(), os.stat(p).st_mtime_ns)
    return out


# ---------------------------------------------------------------------------- basics
def test_first_run_catalogue_and_table(layout, capsys):
    assert survey_cli(layout, "A") == 0
    out = capsys.readouterr().out
    cat = load(layout)
    assert cat["schema"] == 1 and cat["score_version"] == survey.quality.SCORE_VERSION
    assert [r["path"] for r in cat["roots"]] == [str(layout["A"])]
    files = by_rel(cat)
    assert set(files) == {"libro.pdf", "obra.pdf", "obra.txt", "sub/otra.pdf"}
    for rel, e in files.items():
        assert e["id"] == sha(layout["A"] / rel) and e["status"] == "present"
        assert e["root"] == str(layout["A"]) and e["path"] == str(layout["A"] / rel)
        assert e["first_seen"] == e["last_seen"]
    assert files["obra.txt"]["suggestion"]["action"] == "exclude"
    assert files["obra.txt"]["suggestion"]["duplicate_of"] == files["obra.pdf"]["id"]
    assert files["obra.pdf"]["suggestion"]["action"] == "keep_text"
    assert files["obra.pdf"]["duplicates"]["group"] == "G1"
    assert "obra.txt" in out and "exclude (duplicate)" in out and "pending" in out
    assert "4 measured, 0 reused" in out and "trial: not run (--no-trial)" in out
    assert (layout["cat"] / "cache" / f"{files['obra.pdf']['id']}.npy").is_file()


def test_derived_working_file_by_stem_not_listed_separately(layout):
    shutil.copytree(layout["derived"], layout["A"] / "derived-inside")    # skipped by the walk
    assert cli.main(["survey", str(layout["A"]), "--catalogue", str(layout["cat"]),
                     "--derived", str(layout["derived"]), "--derived",
                     str(layout["A"] / "derived-inside"), "--no-trial"]) == 0
    files = by_rel(load(layout))
    assert not any("split_ocr" in rel for rel in files)
    e = files["libro.pdf"]
    wf = e["working_file"]
    assert wf["path"] == str(layout["derived"] / "libro_split_ocr.pdf")
    assert wf["sha256"] == sha(layout["derived"] / "libro_split_ocr.pdf")
    assert wf["ocr_pages"] == [1, 2] and wf["report"].endswith("libro_split_ocr.report.json")
    assert e["measured"]["for"] == wf["sha256"] and e["measured"]["score"]["median"] == 0.0
    assert e["source_layer"]["for"] == e["id"] and e["source_layer"]["score"]["poor"] == 3
    assert e["suggestion"]["action"] == "keep_text"
    assert any("working file libro_split_ocr.pdf" in r for r in e["suggestion"]["reasons"])
    assert any("source's own layer" in r and "3 poor" in r and "would trigger a rebuild trial"
               in r for r in e["suggestion"]["reasons"])
    m = dict(e["measured"], no_text={"blank": [], "ink": [2], "empty": []})
    s = survey.suggest(dict(e, measured=m, source_layer=dict(e["source_layer"], text_pages=0)),
                       None, {})
    assert s["action"] == "keep_text" and not survey.needs_trial(dict(e, measured=m))
    assert "the source itself has no text layer" in s["reasons"]
    assert any("already read by the OCR step" in r and "p. 2" in r for r in s["reasons"])


def test_without_derived_the_source_is_measured(layout):
    assert cli.main(["survey", str(layout["A"]), "--catalogue", str(layout["cat"]),
                     "--no-trial"]) == 0
    e = by_rel(load(layout))["libro.pdf"]
    assert "working_file" not in e and e["measured"]["for"] == e["id"]
    assert (e["suggestion"]["action"], e["suggestion"]["reason"]) == ("review", "trial not run")


# ---------------------------------------------------------------------------- merging
def test_second_root_leaves_the_first_roots_entries_unchanged(layout):
    assert survey_cli(layout, "A") == 0
    first = load(layout)
    time.sleep(1.1)                                       # a later timestamp
    assert survey_cli(layout, "B") == 0
    second = load(layout)
    for k, e in first["files"].items():
        assert second["files"][k] == e
    assert [r["path"] for r in second["roots"]] == [str(layout["A"]), str(layout["B"])]
    assert second["roots"][0] == first["roots"][0]
    assert set(by_rel(second)) == set(by_rel(first)) | {"b.pdf"}


def test_duplicates_are_recomputed_across_roots(layout):
    assert survey_cli(layout, "A") == 0
    shutil.copy2(layout["A"] / "sub" / "otra.pdf", layout["B"] / "otra copia.pdf")
    with open(layout["B"] / "otra copia.pdf", "ab") as f:   # same text, other bytes
        f.write(b"\n% copy\n")
    assert survey_cli(layout, "B") == 0
    files = by_rel(load(layout))
    a, b = files["sub/otra.pdf"], files["otra copia.pdf"]
    assert a["duplicates"]["group"] == b["duplicates"]["group"] is not None
    assert {x["id"] for x in a["duplicates"]["same_work"]} == {b["id"]}
    loser = a if a["suggestion"]["action"] == "exclude" else b
    assert loser["suggestion"]["duplicate_of"] in (a["id"], b["id"])


def test_rerun_reuses_measurements_and_remeasure_forces(layout, capsys):
    assert survey_cli(layout, "A") == 0
    capsys.readouterr()
    t0 = time.perf_counter()
    assert survey_cli(layout, "A") == 0
    fast = time.perf_counter() - t0
    out = capsys.readouterr().out
    assert "0 measured, 4 reused" in out and fast < 5
    res = survey.run([layout["A"]], layout["cat"], [layout["derived"]], trial=False)
    assert (res.measured, res.reused) == (0, 4)
    res = survey.run([layout["A"]], layout["cat"], [layout["derived"]], trial=False,
                     remeasure=True)
    assert (res.measured, res.reused) == (4, 0)


def test_missing_cache_is_recomputed(layout):
    assert survey_cli(layout, "A") == 0
    shutil.rmtree(layout["cat"] / "cache")
    res = survey.run([layout["A"]], layout["cat"], [layout["derived"]], trial=False)
    assert res.reused == 4 and res.shingles_recomputed == 4 and res.measured == 0
    assert len(os.listdir(layout["cat"] / "cache")) == 4


def test_moved_file_keeps_its_entry_and_decision(layout):
    assert survey_cli(layout, "A") == 0
    e = by_rel(load(layout))["sub/otra.pdf"]
    _, before = write_decisions(layout, {e["id"]: {
        "path": e["path"], "action": "keep_text", "notes": "mine", "confirmed": True,
        "suggestion_action": "keep_text"}})
    (layout["A"] / "nueva").mkdir()
    os.replace(layout["A"] / "sub" / "otra.pdf", layout["A"] / "nueva" / "Otra renombrada.pdf")
    assert survey_cli(layout, "A") == 0
    cat = load(layout)
    moved = cat["files"][e["id"]]
    assert moved["path"] == str(layout["A"] / "nueva" / "Otra renombrada.pdf")
    assert moved["status"] == "present" and moved["first_seen"] == e["first_seen"]
    assert moved["measured"] == e["measured"]
    dec = catalogue.load_decisions(layout["cat"])
    assert catalogue.decision_status(moved, dec, cat["files"])[0] == catalogue.CONFIRMED
    assert (layout["cat"] / catalogue.DECISIONS).read_bytes() == before


def test_changed_file_is_a_new_entry_that_needs_reconfirming(layout):
    assert survey_cli(layout, "A") == 0
    old = by_rel(load(layout))["sub/otra.pdf"]
    _, before = write_decisions(layout, {old["id"]: {
        "path": old["path"], "action": "keep_text", "confirmed": True,
        "suggestion_action": "keep_text"}})
    corpus.born_digital(layout["A"] / "sub" / "otra.pdf", [corpus.prose(900, 220)] * 2)
    assert survey_cli(layout, "A") == 0
    cat = load(layout)
    new = next(e for e in cat["files"].values()
               if e["rel"].endswith("otra.pdf") and e["status"] == "present")
    assert new["id"] != old["id"] and new["previous_id"] == old["id"]
    gone = cat["files"][old["id"]]
    assert gone["status"] == "missing" and gone["replaced_by"] == new["id"]
    dec = catalogue.load_decisions(layout["cat"])
    status, d, why = catalogue.decision_status(new, dec, cat["files"])
    assert status == catalogue.RECONFIRM and d["action"] == "keep_text" and "changed" in why
    assert (layout["cat"] / catalogue.DECISIONS).read_bytes() == before


def test_deleted_file_is_kept_as_missing(layout, capsys):
    assert survey_cli(layout, "A") == 0
    e = by_rel(load(layout))["obra.txt"]
    os.remove(layout["A"] / "obra.txt")
    assert survey_cli(layout, "A") == 0
    gone = load(layout)["files"][e["id"]]
    assert gone["status"] == "missing" and gone["measured"] == e["measured"]
    assert gone["suggestion"] == e["suggestion"]
    assert "missing" in capsys.readouterr().out
    obra = by_rel(load(layout))["obra.pdf"]
    assert obra["duplicates"]["group"] is None             # its duplicate is gone


def test_suggestion_change_after_confirming_needs_reconfirm(layout):
    assert survey_cli(layout, "A") == 0
    e = by_rel(load(layout))["obra.pdf"]
    write_decisions(layout, {e["id"]: {"action": "exclude", "confirmed": True,
                                       "suggestion_action": "rebuild_layer"}})
    dec = catalogue.load_decisions(layout["cat"])
    status, _, why = catalogue.decision_status(e, dec, {})
    assert status == catalogue.RECONFIRM and "suggestion changed" in why
    dec["files"][e["id"]]["confirmed"] = False
    assert catalogue.decision_status(e, dec, {})[0] == catalogue.PENDING


def test_no_dictionary_fixes_flag_does_not_change_the_status(layout):
    assert survey_cli(layout, "A") == 0
    e = by_rel(load(layout))["obra.pdf"]
    d = {"action": "keep_text", "confirmed": True, "suggestion_action": "keep_text"}
    write_decisions(layout, {e["id"]: d})
    dec = catalogue.load_decisions(layout["cat"])
    assert catalogue.decision_status(e, dec, {})[0] == catalogue.CONFIRMED
    assert not catalogue.no_dictionary_fixes(dec["files"][e["id"]])     # missing: False
    write_decisions(layout, {e["id"]: {**d, "no_dictionary_fixes": True}})
    dec = catalogue.load_decisions(layout["cat"])
    assert catalogue.decision_status(e, dec, {})[0] == catalogue.CONFIRMED
    assert catalogue.no_dictionary_fixes(dec["files"][e["id"]])
    assert not catalogue.no_dictionary_fixes(None)


@pytest.mark.parametrize("text", ["{not json", '{"schema": 2, "files": {}}', '{"schema": 1}',
                                  '{"schema": 1, "files": {"x": 3}}'])
def test_invalid_decisions_file_exits_1_and_is_not_touched(layout, text, capsys):
    layout["cat"].mkdir()
    path = layout["cat"] / catalogue.DECISIONS
    path.write_text(text, "utf-8")
    assert survey_cli(layout, "A") == 1
    assert "decisions" in capsys.readouterr().err
    assert path.read_text("utf-8") == text
    assert not (layout["cat"] / catalogue.CATALOGUE).exists()


def test_refusals(layout, capsys):
    assert cli.main(["survey", str(layout["tmp"] / "nope"), "--catalogue",
                     str(layout["cat"])]) == 1
    assert "no such folder" in capsys.readouterr().err
    assert cli.main(["survey", str(layout["A"]), "--catalogue",
                     str(layout["A"] / "cat")]) == 1
    assert "inside the source folder" in capsys.readouterr().err
    assert not (layout["A"] / "cat").exists()
    for argv in (["survey", "--catalogue", str(layout["cat"])],
                 ["survey", str(layout["A"]), "--catalogue", str(layout["cat"]), "--jobs", "0"]):
        with pytest.raises(SystemExit) as ex:                # usage errors
            cli.main(argv)
        assert ex.value.code == 1


def test_unreadable_file_is_recorded_and_exits_2(layout, capsys):
    (layout["A"] / "roto.pdf").write_bytes(b"%PDF-1.4 garbage, not a pdf")
    assert survey_cli(layout, "A") == 2
    e = by_rel(load(layout))["roto.pdf"]
    assert e["status"] == "error" and e["error"]
    assert (e["suggestion"]["action"], e["suggestion"]["reason"]) == ("review", "could not read")
    assert "could not read roto.pdf" in capsys.readouterr().out


# ---------------------------------------------------------------------------- read-only
def test_survey_is_read_only(layout, monkeypatch):
    import tempfile

    layout["cat"] = layout["derived"] / "catalogue"           # as the owner runs it
    temp = layout["tmp"] / "temp"
    temp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temp))
    roots = (layout["A"], layout["B"], layout["derived"])
    before = snapshot(*roots)
    assert survey_cli(layout, "A", "B") == 0
    assert survey_cli(layout, "A", "B", extra=("--remeasure",)) == 0
    assert snapshot(*roots, skip=layout["cat"]) == before
    assert sorted(os.listdir(layout["cat"])) == ["cache", catalogue.CATALOGUE, catalogue.SHEET]
    assert os.listdir(temp) == []

    def broken(*a, **k):
        raise RuntimeError("measure broke")

    monkeypatch.setattr(survey, "measure", broken)
    assert survey_cli(layout, "A", extra=("--remeasure",)) == 1
    assert snapshot(*roots, skip=layout["cat"]) == before
    assert os.listdir(temp) == []
    assert not [f for f in os.listdir(layout["cat"]) if f.endswith(".tmp")]


# ---------------------------------------------------------------------------- the sheet
DATA_RE = re.compile(r'<script type="application/json" id="gpdf-data">(.*?)</script>', re.S)


def sheet_data(text):
    (block,) = DATA_RE.findall(text)
    return block, json.loads(block)


def test_sheet_has_every_entry_and_is_self_contained(layout, capsys):
    import base64
    import io

    from PIL import Image

    assert survey_cli(layout, "A") == 0
    obra = by_rel(load(layout))["obra.pdf"]
    nasty = "see https://example.org/x and http://y </script><script>alert(1)</script>"
    write_decisions(layout, {obra["id"]: {"action": "keep_text", "notes": nasty,
                                          "confirmed": True, "suggestion_action": "keep_text"}})
    assert survey_cli(layout, "A") == 0
    assert "sheet:" in capsys.readouterr().out
    text = (layout["cat"] / catalogue.SHEET).read_text("utf-8")
    assert "http://" not in text and "https://" not in text
    assert not re.search(r"""(src|href)\s*=\s*["']?//""", text, re.I)
    assert "@import" not in text and text.count("</script>") == 2
    block, data = sheet_data(text)
    assert "</" not in block
    cat = load(layout)
    assert {e["id"] for e in data["entries"]} == set(cat["files"])
    assert data["decisions"]["files"][obra["id"]]["notes"] == nasty
    views = {e["rel"].replace("\\", "/"): e for e in data["entries"]}
    assert views["obra.pdf"]["decision_status"]["status"] == catalogue.CONFIRMED
    assert views["obra.txt"]["decision_status"]["status"] == catalogue.PENDING
    assert len(views["obra.pdf"]["rows"]) == 5 and views["obra.txt"]["images"] == []
    ims = views["obra.pdf"]["images"]
    assert [im["page"] for im in ims] == [1, 2, 3] and all(im["text"] for im in ims)
    for im in ims:
        head, b64 = im["src"].split(",", 1)
        assert head == "data:image/jpeg;base64"
        pic = Image.open(io.BytesIO(base64.b64decode(b64)))
        assert pic.size == (im["w"], im["h"]) == (round(420 * 60 / 72), round(600 * 60 / 72))
    assert data["folder"] == str(layout["cat"])


def test_sheet_only_rewrites_just_the_sheet(layout, capsys):
    assert survey_cli(layout, "A") == 0
    cat_bytes = (layout["cat"] / catalogue.CATALOGUE).read_bytes()
    obra = by_rel(load(layout))["obra.pdf"]
    _, dec_bytes = write_decisions(layout, {obra["id"]: {"action": "exclude", "reason": "off_topic",
                                                         "confirmed": True,
                                                         "suggestion_action": "keep_text"}})
    sheet = layout["cat"] / catalogue.SHEET
    sheet.write_text("old", "utf-8")
    capsys.readouterr()
    assert cli.main(["survey", "--sheet-only", "--catalogue", str(layout["cat"])]) == 0
    out = capsys.readouterr().out
    assert "sheet:" in out and "showing:   1 decision(s)" in out
    _, data = sheet_data(sheet.read_text("utf-8"))
    view = next(e for e in data["entries"] if e["id"] == obra["id"])
    assert view["decision_status"]["decision"]["action"] == "exclude"
    assert (layout["cat"] / catalogue.CATALOGUE).read_bytes() == cat_bytes
    assert (layout["cat"] / catalogue.DECISIONS).read_bytes() == dec_bytes
    with pytest.raises(SystemExit):
        cli.main(["survey", str(layout["A"]), "--sheet-only", "--catalogue", str(layout["cat"])])
    assert cli.main(["survey", "--sheet-only", "--catalogue", str(layout["tmp"] / "none")]) == 1
    assert "run the survey first" in capsys.readouterr().err
