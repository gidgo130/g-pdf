# SPDX-License-Identifier: AGPL-3.0-or-later
"""gpdf-cli backup: only the owner's decision files are copied, with a verified manifest;
the source and earlier backups are never changed."""

import hashlib
import json
import os

from gpdf import cli
from gpdf.pipeline import backup

DECISIONS = ["a_split.decisions.json", "a_split.decisions.20261020-101010.bak.json",
             "catalogue/catalogue.decisions.json", "nested/deep/b_split.decisions.json"]
REGENERABLE = ["a.pdf", "a_split.pdf", "a_split.report.json", "a_split.review.html",
               "text/a.jsonl", "text/a.txt", "text/export.json", "catalogue/catalogue.json",
               "catalogue/applied.json", "catalogue/catalogue.review.html",
               "catalogue/cache/0f3a.json", "catalogue/cache/0f3a.png"]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot(root):
    return {p.relative_to(root).as_posix(): (sha(p), os.stat(p).st_mtime_ns)
            for p in sorted(root.rglob("*")) if p.is_file()}


def make_source(tmp_path):
    src = tmp_path / "book-prep"
    for k, rel in enumerate(DECISIONS + REGENERABLE):
        f = src / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(f"{rel} {k}\n".encode() * (k + 1))
    return src


def run(src, to):
    return cli.main(["backup", "--to", str(to), "--source", str(src)])


def test_only_decision_files_are_copied_with_their_paths_and_a_manifest(tmp_path, capsys):
    src = make_source(tmp_path)
    before = snapshot(src)
    to = tmp_path / "safe" / "backups"                     # created if missing
    assert run(src, to) == 0
    [dest] = list(to.iterdir())
    assert dest.name.startswith("g-pdf-backup-")
    assert sorted(snapshot(dest)) == sorted(DECISIONS + [backup.MANIFEST])
    man = json.loads((dest / backup.MANIFEST).read_text("utf-8"))
    assert man["version"] and man["created"] and len(man["files"]) == len(DECISIONS)
    for f in man["files"]:
        assert f["sha256"] == sha(dest / f["path"]) == sha(src / f["path"])
        assert f["size"] == (src / f["path"]).stat().st_size and f["mtime"]
    assert snapshot(src) == before
    out = capsys.readouterr().out
    assert f"copied {len(DECISIONS)} file(s)" in out and "catalogue/catalogue.decisions.json" in out
    assert str(dest) in out and "restore" in out


def test_a_second_backup_leaves_the_first_alone(tmp_path):
    src, to = make_source(tmp_path), tmp_path / "backups"
    assert run(src, to) == 0
    [first] = list(to.iterdir())
    kept = snapshot(first)
    assert run(src, to) == 0                               # same second: a new folder
    assert len(list(to.iterdir())) == 2 and snapshot(first) == kept


def test_a_folder_inside_the_source_is_refused(tmp_path, capsys):
    src = make_source(tmp_path)
    before = snapshot(src)
    assert run(src, src / "catalogue" / "backups") == 1
    assert run(src, src) == 1
    assert "inside the source" in capsys.readouterr().err
    assert snapshot(src) == before and not (src / "catalogue" / "backups").exists()


def test_missing_source_is_a_clear_error(tmp_path, capsys):
    assert run(tmp_path / "nowhere", tmp_path / "backups") == 1
    assert "nowhere" in capsys.readouterr().err
    assert not (tmp_path / "backups").exists()


def test_a_copy_that_differs_from_its_source_fails(tmp_path, monkeypatch, capsys):
    src, to = make_source(tmp_path), tmp_path / "backups"
    real = backup.shutil.copy2

    def bad_copy(a, b):
        real(a, b)
        if str(a).endswith("catalogue.decisions.json"):
            with open(b, "ab") as f:
                f.write(b"x")

    monkeypatch.setattr(backup.shutil, "copy2", bad_copy)
    assert run(src, to) == 1
    err = capsys.readouterr().err
    assert "catalogue/catalogue.decisions.json" in err and "differs" in err
