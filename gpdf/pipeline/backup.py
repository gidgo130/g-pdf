# SPDX-License-Identifier: AGPL-3.0-or-later
"""gpdf-cli backup: copy the owner's review work, the files nothing can regenerate, to
DIR/g-pdf-backup-YYYYmmdd-HHMMSS/<path under the source>, with manifest.json (sizes,
sha256, source mtimes). Each copy is hashed again against its source. Nothing in the
source or in earlier backups is changed. When the files and their hashes match the newest
earlier backup's manifest, no folder is made (unless ``always``). PDFs, exports, caches, catalogue.json,
applied.json and reports are left out: the pipeline writes them again."""

from __future__ import annotations

import hashlib
import json
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from gpdf import __version__

# Owner-made files only. Later steps add their own decision files to this list.
PATTERNS = ("*.decisions.json",    # split review, catalogue and structure sheets
            "*.bak.json")          # decision backups (split --dry-run --force)
DEFAULT_SOURCE = Path("private/book-prep")
MANIFEST = "manifest.json"
PREFIX = "g-pdf-backup-"


class BackupError(Exception):
    pass


@dataclass
class Backup:
    source: Path
    dest: Path
    files: list[dict] = field(default_factory=list)     # as in the manifest
    differs: list[str] = field(default_factory=list)    # copies that fail the re-hash
    unchanged: bool = False                              # dest is the earlier backup

    @property
    def total(self) -> int:
        return sum(f["size"] for f in self.files)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def find(source: Path) -> list[Path]:
    """The files under ``source`` that match PATTERNS, sorted by relative path."""
    found = {p for pat in PATTERNS for p in source.rglob(pat) if p.is_file()}
    return sorted(found, key=lambda p: p.relative_to(source).as_posix().lower())


def _order(folder: Path):
    """Sort key for backup folders: their stamp, then the -k suffix as a number."""
    rest = folder.name.removeprefix(PREFIX)            # YYYYmmdd-HHMMSS[-k]
    stamp, k = rest[:15], rest[16:]
    return stamp, int(k) if k.isdigit() else 1


def latest(to: Path) -> Path | None:
    """The newest g-pdf-backup-* folder in ``to``, or None."""
    if not to.is_dir():
        return None
    found = [p for p in to.glob(PREFIX + "*") if p.is_dir()]
    return max(found, key=_order, default=None)


def _recorded(folder: Path) -> set[tuple[str, str]] | None:
    """(path, sha256) of every file in the folder's manifest; None if it is missing,
    unreadable or recorded a copy that differed."""
    try:
        man = json.loads((folder / MANIFEST).read_text("utf-8"))
        if man.get("differs"):
            return None
        return {(f["path"], f["sha256"]) for f in man["files"]}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def run(source: Path, to: Path, always: bool = False) -> Backup:
    if not source.is_dir():
        raise BackupError(f"no such folder: {source} (run from the repo root, or pass "
                          "--source)")
    src, target = source.resolve(), to.resolve()
    if target == src or src in target.parents:
        raise BackupError(f"{to} is inside the source {source}: back up somewhere else")
    files = find(src)
    if not files:
        raise BackupError(f"no decision files under {source} ({', '.join(PATTERNS)})")
    digests = {f.relative_to(src).as_posix(): sha256(f) for f in files}
    if not always and (prev := latest(to)) is not None             and _recorded(prev) == set(digests.items()):
        return Backup(source, prev, unchanged=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest, k = to / f"{PREFIX}{stamp}", 2
    to.mkdir(parents=True, exist_ok=True)
    while True:                            # never into an earlier backup, even the same second
        try:
            dest.mkdir()
            break
        except FileExistsError:
            dest, k = to / f"{PREFIX}{stamp}-{k}", k + 1
    result = Backup(source, dest)
    for f in files:
        rel = f.relative_to(src)
        st, digest = f.stat(), digests[rel.as_posix()]
        copy = dest / rel
        copy.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, copy)
        result.files.append({"path": rel.as_posix(), "size": st.st_size, "sha256": digest,
                             "mtime": time.strftime("%Y-%m-%dT%H:%M:%S",
                                                    time.localtime(st.st_mtime))})
    for entry in result.files:
        if not sha256(dest / entry["path"]) == entry["sha256"] == sha256(src / entry["path"]):
            result.differs.append(entry["path"])
    manifest = {"tool": "g-pdf", "version": __version__,
                "created": time.strftime("%Y-%m-%dT%H:%M:%S"), "source": str(src),
                "files": result.files, "differs": result.differs}
    (dest / MANIFEST).write_text(json.dumps(manifest, indent=1, ensure_ascii=False) + "\n",
                                 encoding="utf-8")
    return result
