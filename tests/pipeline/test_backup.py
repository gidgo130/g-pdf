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


def run(src, to, *extra):
    return cli.main(["backup", "--to", str(to), "--source", str(src), *extra])


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
    assert run(src, to, "--always") == 0                   # same second: a new folder
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


def test_an_unchanged_second_backup_makes_no_folder(tmp_path, capsys):
    src, to = make_source(tmp_path), tmp_path / "backups"
    assert run(src, to) == 0
    [first] = list(to.iterdir())
    capsys.readouterr()
    assert run(src, to) == 0
    assert list(to.iterdir()) == [first]
    assert f"unchanged since {first.name}" in capsys.readouterr().out


def test_a_changed_or_added_file_makes_a_new_folder(tmp_path):
    src, to = make_source(tmp_path), tmp_path / "backups"
    assert run(src, to) == 0
    (src / "catalogue" / "catalogue.decisions.json").write_text("{}", "utf-8")
    assert run(src, to) == 0
    assert len(list(to.iterdir())) == 2
    (src / "structure").mkdir()
    (src / "structure" / "structure.decisions.json").write_text("{}", "utf-8")
    assert run(src, to) == 0
    newest = sorted(to.iterdir(), key=backup._order)[-1]
    assert (newest / "structure" / "structure.decisions.json").is_file()
    assert len(list(to.iterdir())) == 3


def test_always_makes_a_new_folder(tmp_path):
    src, to = make_source(tmp_path), tmp_path / "backups"
    assert run(src, to) == 0
    assert run(src, to, "--always") == 0
    assert len(list(to.iterdir())) == 2


def test_a_corrupt_or_missing_earlier_manifest_counts_as_changed(tmp_path):
    src, to = make_source(tmp_path), tmp_path / "backups"
    assert run(src, to) == 0
    [first] = list(to.iterdir())
    (first / backup.MANIFEST).write_text("{not json", "utf-8")
    assert run(src, to) == 0
    assert len(list(to.iterdir())) == 2
    newest = sorted(to.iterdir(), key=backup._order)[-1]
    (newest / backup.MANIFEST).unlink()
    assert run(src, to) == 0
    assert len(list(to.iterdir())) == 3


def test_the_newest_folder_is_compared_even_within_one_second(tmp_path):
    to = tmp_path / "backups"
    for name in ("g-pdf-backup-20261022-101010", "g-pdf-backup-20261022-101010-2",
                 "g-pdf-backup-20261022-101010-10", "g-pdf-backup-20261021-235959-3"):
        (to / name).mkdir(parents=True)
    assert backup.latest(to).name == "g-pdf-backup-20261022-101010-10"


# Automatic backup before the commands that read the owner's decisions.

def auto_env(tmp_path, monkeypatch, backup_dir=None):
    """A repo-like cwd with private/book-prep holding decisions; the env as the owner sets
    it. Returns the backup folder."""
    monkeypatch.chdir(tmp_path)
    src = tmp_path / "private" / "book-prep"
    src.mkdir(parents=True)
    (src / "a_split.decisions.json").write_text('{"pages": []}', "utf-8")
    (src / "catalogue").mkdir()
    monkeypatch.delenv("GPDF_NO_AUTO_BACKUP")
    to = backup_dir or tmp_path / "onedrive" / "g-pdf-backups"
    monkeypatch.setenv("GPDF_BACKUP_DIR", str(to))
    return to


def sheet_only(tmp_path):
    """A cheap pipeline command: its own error shows it ran."""
    return cli.main(["survey", "--sheet-only", "--catalogue",
                     str(tmp_path / "private" / "book-prep" / "catalogue")])


def test_a_pipeline_command_backs_up_once_then_reports_unchanged(tmp_path, monkeypatch, capsys):
    to = auto_env(tmp_path, monkeypatch)
    assert sheet_only(tmp_path) == 1
    [dest] = list(to.iterdir())
    assert (dest / "a_split.decisions.json").is_file()
    err = capsys.readouterr().err
    assert f"backup: 1 files -> {dest}" in err and "run the survey first" in err
    assert sheet_only(tmp_path) == 1
    assert list(to.iterdir()) == [dest]
    err = capsys.readouterr().err
    assert f"backup: unchanged since {dest.name}" in err and "run the survey first" in err


def test_without_the_env_only_the_tip_is_printed(tmp_path, monkeypatch, capsys):
    auto_env(tmp_path, monkeypatch)
    monkeypatch.delenv("GPDF_BACKUP_DIR")
    assert sheet_only(tmp_path) == 1
    err = capsys.readouterr().err
    assert err.count("tip: set GPDF_BACKUP_DIR") == 1
    assert "README: Backing up your review work" in err and "backup:" not in err


def test_a_failing_backup_warns_and_the_command_still_runs(tmp_path, monkeypatch, capsys):
    not_a_dir = tmp_path / "file"
    not_a_dir.write_text("x", "utf-8")
    auto_env(tmp_path, monkeypatch, backup_dir=not_a_dir)
    assert sheet_only(tmp_path) == 1
    err = capsys.readouterr().err
    assert "warning: automatic backup failed" in err and "run the survey first" in err

    monkeypatch.setenv("GPDF_BACKUP_DIR", str(tmp_path / "backups"))
    real = backup.shutil.copy2

    def bad_copy(a, b):
        real(a, b)
        with open(b, "ab") as f:
            f.write(b"x")

    monkeypatch.setattr(backup.shutil, "copy2", bad_copy)
    assert sheet_only(tmp_path) == 1
    err = capsys.readouterr().err
    assert "warning: automatic backup failed" in err and "differs" in err
    assert "run the survey first" in err


def test_no_auto_backup_turns_it_off(tmp_path, monkeypatch, capsys):
    to = auto_env(tmp_path, monkeypatch)
    monkeypatch.setenv("GPDF_NO_AUTO_BACKUP", "1")
    assert sheet_only(tmp_path) == 1
    assert not to.exists()
    err = capsys.readouterr().err
    assert "backup:" not in err and "tip:" not in err


def test_only_commands_that_read_decisions_back_up(tmp_path, monkeypatch, capsys):
    to = auto_env(tmp_path, monkeypatch)
    assert cli.main(["ocr", "nowhere.pdf", "-o", "out", "--lang", "spa"]) == 1
    assert cli.main(["split", "nowhere.pdf", "-o", "out", "--dry-run"]) == 1
    assert not to.exists() and "backup:" not in capsys.readouterr().err
    assert cli.main(["split", "nowhere.pdf", "-o", "out", "--decisions", "d.json"]) == 1
    assert len(list(to.iterdir())) == 1


def test_no_book_prep_folder_skips_quietly(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GPDF_NO_AUTO_BACKUP")
    monkeypatch.setenv("GPDF_BACKUP_DIR", str(tmp_path / "backups"))
    assert sheet_only(tmp_path) == 1
    err = capsys.readouterr().err
    assert "backup:" not in err and "tip:" not in err
    assert not (tmp_path / "backups").exists()
