# SPDX-License-Identifier: AGPL-3.0-or-later
"""gpdf-cli export: which entries are exported from which file (applied output, working
file, source), the refusals (pending, needs re-confirm, missing, changed bytes, existing
output), --dry-run, --only/--pages, --jobs, the outputs and export.json, and that sources,
working files and the catalogue folder are only read."""

import hashlib
import json
import os
import shutil

import pytest

pytest.importorskip("numpy")
pytest.importorskip("PIL")
pytest.importorskip("wordfreq")

from gpdf import cli  # noqa: E402
from gpdf.pipeline import catalogue, export, survey  # noqa: E402

from . import corpus  # noqa: E402
from .test_export_pages import ROOSEVELT  # noqa: E402


def md5(path):
    return hashlib.md5(open(path, "rb").read()).hexdigest()


def state(path):
    return md5(path), os.stat(path).st_mtime_ns


def pages(seed, n=3):
    return [corpus.prose(seed + i, 160) for i in range(n)]


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    d = tmp_path_factory.mktemp("export_base")
    for k, name in enumerate(("libro", "excluido", "pendiente", "reconfirmar", "cambiado",
                              "borrado", "escaneado", "aplicado", "tocado")):
        corpus.born_digital(d / f"{name}.pdf", pages(1000 + 10 * k))
    corpus.born_digital(d / "escaneado_split_ocr.pdf", pages(1060))       # its working file
    corpus.born_digital(d / "aplicado_rebuilt.pdf", pages(1070))          # apply's output
    corpus.born_digital(d / "tocado_rebuilt.pdf", pages(1080))
    (d / "poema.txt").write_bytes(ROOSEVELT.encode("utf-8"))
    return d


@pytest.fixture
def lay(base, tmp_path):
    root, der, cat, out = (tmp_path / n for n in ("root", "derived", "cat", "out"))
    root.mkdir()
    der.mkdir()
    for p in base.iterdir():
        shutil.copy2(p, (der if "_" in p.stem else root) / p.name)
    os.replace(der / "aplicado_rebuilt.pdf", tmp_path / "aplicado_rebuilt.pdf")
    os.replace(der / "tocado_rebuilt.pdf", tmp_path / "tocado_rebuilt.pdf")
    survey.run([root], cat, derived=[der], trial=False)
    files = {e["rel"]: e for e in catalogue.load(cat)["files"].values()}

    def dec(rel, action="keep_text", confirmed=True, suggestion=None, **kw):
        e = files[rel]
        return e["id"], {"path": e["path"], "action": action, "pages": None,
                         "confirmed": confirmed,
                         "suggestion_action": suggestion or e["suggestion"]["action"], **kw}

    decisions = dict([dec("libro.pdf"), dec("excluido.pdf", "exclude"),
                      dec("reconfirmar.pdf", suggestion="rebuild_layer"),
                      dec("cambiado.pdf"), dec("borrado.pdf"), dec("escaneado.pdf"),
                      dec("aplicado.pdf", "rebuild_layer"), dec("tocado.pdf", "rebuild_layer"),
                      dec("poema.txt")])
    (cat / catalogue.DECISIONS).write_text(json.dumps({"schema": 1, "files": decisions}),
                                           "utf-8")
    applied = {"schema": 1, "applied": [
        {"id": files[r]["id"], "source": files[r]["path"], "action": "rebuild_layer",
         "pages": "all", "output": str(tmp_path / f"{r[:-4]}_rebuilt.pdf"),
         "output_sha256": survey.sha256(tmp_path / f"{r[:-4]}_rebuilt.pdf")}
        for r in ("aplicado.pdf", "tocado.pdf")]}
    (cat / catalogue.APPLIED_LOG).write_text(json.dumps(applied), "utf-8")
    with open(tmp_path / "tocado_rebuilt.pdf", "ab") as f:              # changed after apply
        f.write(b"\n% touched\n")
    os.remove(root / "borrado.pdf")
    survey.run([root], cat, derived=[der], trial=False)                 # now "missing"
    with open(root / "cambiado.pdf", "ab") as f:                        # changed after survey
        f.write(b"\n% changed\n")
    watched = [p for p in (*root.iterdir(), *der.iterdir()) if p.is_file()]
    watched += [tmp_path / "aplicado_rebuilt.pdf", tmp_path / "tocado_rebuilt.pdf",
                cat / catalogue.CATALOGUE, cat / catalogue.DECISIONS, cat / catalogue.APPLIED_LOG]
    return {"root": root, "der": der, "cat": cat, "out": out, "tmp": tmp_path,
            "files": files, "watched": watched}


def run(lay, *extra):
    return cli.main(["export", "--catalogue", str(lay["cat"]), "--out", str(lay["out"]), *extra])


def test_dry_run_plan_gates_and_nothing_written(lay, capsys):
    before = {p: state(p) for p in lay["watched"]}
    assert run(lay, "--dry-run") == 2
    out = capsys.readouterr().out
    assert "would export libro.pdf from the source libro.pdf" in out
    assert "would export escaneado.pdf from the working file escaneado_split_ocr.pdf" in out
    assert "would export aplicado.pdf from the applied output aplicado_rebuilt.pdf" in out
    assert "would export poema.txt from the source poema.txt" in out
    assert "excluded excluido.pdf" in out
    assert "refused  pendiente.pdf: pending: no decision" in out
    assert "refused  reconfirmar.pdf: needs re-confirm: suggestion changed" in out
    assert "refused  cambiado.pdf: the source changed since it was recorded" in out
    assert "refused  borrado.pdf: missing" in out
    assert "refused  tocado.pdf: the applied output changed since it was recorded" in out
    assert "dry run: 4 file(s) would be exported, 1 excluded, 5 refused; nothing written" in out
    assert not lay["out"].exists()
    assert {p: state(p) for p in lay["watched"]} == before


def test_export_writes_new_files_only(lay, capsys):
    before = {p: state(p) for p in lay["watched"]}
    assert run(lay, "--jobs", "1") == 2                 # refusals: 2 at the end
    out = capsys.readouterr().out
    assert "exported 4 file(s), 1 excluded, 5 refused, 0 error(s)" in out
    names = sorted(p.name for p in lay["out"].iterdir())
    for stem in ("libro", "escaneado_split_ocr", "aplicado_rebuilt", "poema"):
        for suffix in export.SUFFIXES:
            assert f"{stem}{suffix}" in names
    assert "export.json" in names and not any(n.startswith(".gpdf-") for n in names)
    assert {p: state(p) for p in lay["watched"]} == before

    log = json.loads((lay["out"] / "export.json").read_text("utf-8"))
    e = log["entries"][lay["files"]["aplicado.pdf"]["id"]]
    assert e["input_from"] == "applied output"
    assert e["input_sha256"] == survey.sha256(lay["tmp"] / "aplicado_rebuilt.pdf")
    assert e["flags"]["no_dictionary_fixes"] is False and e["counts"]["pages"] == 3
    assert log["tool_version"]
    recs = [json.loads(s) for s in (lay["out"] / "libro.jsonl").read_text("utf-8").splitlines()]
    assert recs and all(r["entry"] == lay["files"]["libro.pdf"]["id"] for r in recs)
    assert all(r["kind"] in export.KINDS and r["pages"][0] >= 1 for r in recs)
    txt = [json.loads(s) for s in (lay["out"] / "poema.jsonl").read_text("utf-8").splitlines()]
    assert txt[0]["kind"] == "heading" and txt[1]["lines"] == [12, 14]
    rows = [json.loads(s) for s in (lay["out"] / "libro.pages.jsonl").read_text(
        "utf-8").splitlines()]
    assert [r["page"] for r in rows] == [1, 2, 3]


def test_existing_output_refused_without_force(lay, capsys):
    assert run(lay, "--only", "libro") == 0
    stamp = state(lay["out"] / "libro.jsonl")
    capsys.readouterr()
    assert run(lay, "--only", "libro") == 2
    assert "libro.jsonl already exists (pass --force to replace it)" in capsys.readouterr().out
    assert state(lay["out"] / "libro.jsonl") == stamp
    assert run(lay, "--only", "libro", "--force") == 0


def test_pages_needs_only_and_limits_the_pages(lay, capsys):
    with pytest.raises(SystemExit) as e:
        run(lay, "--pages", "2")
    assert e.value.code == 1 and "--pages needs --only" in capsys.readouterr().err
    assert run(lay, "--only", "libro", "--pages", "2") == 0
    rows = (lay["out"] / "libro.pages.jsonl").read_text("utf-8").splitlines()
    assert [json.loads(r)["page"] for r in rows] == [2]
    assert not (lay["out"] / "libro_p2.jsonl").exists()


def test_no_dictionary_fixes_flag_is_used_and_listed(lay, capsys):
    path = lay["cat"] / catalogue.DECISIONS
    d = json.loads(path.read_text("utf-8"))
    d["files"][lay["files"]["libro.pdf"]["id"]]["no_dictionary_fixes"] = True
    path.write_text(json.dumps(d), "utf-8")
    assert run(lay, "--only", "libro") == 0
    out = capsys.readouterr().out
    assert "no dictionary fixes: libro.pdf" in out
    log = json.loads((lay["out"] / "export.json").read_text("utf-8"))
    assert log["entries"][lay["files"]["libro.pdf"]["id"]]["flags"]["no_dictionary_fixes"]


def test_jobs_run_in_parallel_processes(lay, capsys):
    assert run(lay, "--only", "libro", "poema", "--jobs", "2") == 0
    assert "exported 2 file(s)" in capsys.readouterr().out
    log = json.loads((lay["out"] / "export.json").read_text("utf-8"))
    assert len(log["entries"]) == 2


def test_output_inside_a_source_folder_refused(lay, capsys):
    rc = cli.main(["export", "--catalogue", str(lay["cat"]), "--out",
                   str(lay["root"] / "text"), "--dry-run"])
    assert rc == 1 and "inside the source folder" in capsys.readouterr().err
