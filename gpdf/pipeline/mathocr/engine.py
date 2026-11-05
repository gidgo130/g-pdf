# SPDX-License-Identifier: AGPL-3.0-or-later
"""The MinerU engine as an external program: find ``mineru-kit``, check its version and
its model files against the pins from the trial, run a tier over a batch of page images,
and cache the raw output per page.

MinerU is installed by the user in its own environment (see docs/math-export.md); it is
never imported or bundled. One ``mineru-kit parse`` call per tier takes a folder of page
PNGs and writes one zip per page (markdown.md, middle_json.json, the layout with
bounding boxes) into the cache folder as it goes, so a stopped run resumes where it left
off: the next run renders and submits only the pages whose zip is missing or unreadable.

Cache key: the source file's SHA-256, the page, the tier, the engine version and the
render dpi.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

ENV_VAR = "GPDF_MINERU"
HOME_ENV_VAR = "MINERU_HOME"
PINNED_VERSION = "4.1.7"
MODEL_REPO = "MinerU2.5-Pro-2605-1.2B-GGUF"      # the standard tier's quantized VLM
PINNED_MODELS = {                                 # file under <home>/models/<MODEL_REPO>/
    "MinerU2.5-Pro-2605-1.2B-Q8_0.gguf":
        "3f3523429c880d675c0330cb3de78240452bc40a961d037e24f91ab8488f03e7",
    "mmproj-MinerU2.5-Pro-2605-1.2B-Q8_0.gguf":
        "b6c08ab50352677f7396af45ee60682f353f55488e26911d8154bfc37514d3f8",
}
TIERS = ("standard", "basic")                     # primary text, second opinion
DPI = 300
OCR_MODES = ("ocr", "txt")                  # read the page render, or the PDF's text layer
MEMBERS = ("markdown.md", "middle_json.json")
STDERR_TAIL = 600
INSTALL_HINT = ("install MinerU 4.1.7 in its own environment (docs/math-export.md) and pass "
                "--mineru PATH (the mineru-kit executable or its venv folder) or set "
                f"${ENV_VAR}")


class EngineError(Exception):
    """The engine is missing, not the pinned one, or a run failed; the message is for
    the user."""


@dataclass
class Engine:
    exe: str
    version: str
    home: Path
    model_hashes: dict[str, str]
    unpinned: list[str] = field(default_factory=list)   # "version" or model file names

    def describe(self) -> dict:
        return {"exe": self.exe, "version": self.version, "pinned_version": PINNED_VERSION,
                "home": str(self.home), "model_repo": MODEL_REPO, "models": self.model_hashes,
                "pinned_models": dict(PINNED_MODELS), "unpinned": list(self.unpinned)}


@dataclass
class RawPage:
    page: int                      # 1-based
    tier: str
    markdown: str
    blocks: list[dict]             # middle_json page blocks (bbox as page fractions)
    zip_path: Path


# ---------------------------------------------------------------- finding and pinning

def _exe_in(folder: Path) -> Path | None:
    for rel in ("Scripts/mineru-kit.exe", "bin/mineru-kit", "mineru-kit.exe", "mineru-kit"):
        if (folder / rel).is_file():
            return folder / rel
    return None


def find(explicit=None) -> str:
    """``explicit`` (the executable or its venv folder), else $GPDF_MINERU, else
    ``mineru-kit`` on PATH."""
    for where, path in (("--mineru", explicit), (ENV_VAR, os.environ.get(ENV_VAR))):
        if path:
            p = Path(path)
            if p.is_file():
                return str(p)
            if p.is_dir() and (exe := _exe_in(p)):
                return str(exe)
            raise EngineError(f"mineru-kit not found at {path} (from {where})")
    path = shutil.which("mineru-kit")
    if path:
        return path
    raise EngineError(f"mineru-kit not found: {INSTALL_HINT}")


def _run(exe, *args, timeout=120) -> str:
    try:
        r = subprocess.run([exe, *args], capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise EngineError(f"cannot run {exe}: {e}") from None
    out = (r.stdout + r.stderr).decode("utf-8", "replace")
    if r.returncode:
        raise EngineError(f"{exe} {' '.join(args)} failed: {out.strip()[-STDERR_TAIL:]}")
    return out


def probe(exe) -> str:
    """The version ``mineru-kit --version`` reports."""
    m = re.search(r"MinerU version:\s*(\S+)", _run(exe, "--version"))
    if not m:
        raise EngineError(f"{exe} does not look like mineru-kit (no 'MinerU version' line)")
    return m.group(1)


def default_home() -> Path:
    return Path(os.environ.get(HOME_ENV_VAR) or "~/.mineru").expanduser()


def sha256_of(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def model_hashes(home: Path, pins: dict[str, str]) -> dict[str, str]:
    out = {}
    for name in pins:
        p = home / "models" / MODEL_REPO / name
        if not p.is_file():
            raise EngineError(f"model file {name} not found under {home / 'models' / MODEL_REPO} "
                              f"(run the engine once so it downloads its models, or set "
                              f"${HOME_ENV_VAR} / --mineru-home)")
        out[name] = sha256_of(p)
    return out


def prepare(explicit=None, home=None, pins=None, allow_unpinned=False) -> Engine:
    """find + version + model hashes; a mismatch with the pins stops unless allowed."""
    pins = PINNED_MODELS if pins is None else pins
    exe = find(explicit)
    version = probe(exe)
    home = Path(home) if home else default_home()
    hashes = model_hashes(home, pins)
    unpinned = []
    if version != PINNED_VERSION:
        unpinned.append("version")
        if not allow_unpinned:
            raise EngineError(f"MinerU {version} found, {PINNED_VERSION} expected (the version "
                              f"the trial measured); pass --allow-unpinned to run anyway")
    for name, want in pins.items():
        if hashes[name] != want:
            unpinned.append(name)
            if not allow_unpinned:
                raise EngineError(f"model file {name} has sha256 {hashes[name][:16]}..., "
                                  f"{want[:16]}... expected; pass --allow-unpinned to run anyway")
    return Engine(exe, version, home, hashes, unpinned)


# ---------------------------------------------------------------- cache

class Cache:
    """Raw engine output per page and tier under
    ROOT/<source sha256[:16]>/<engine version>/<dpi>/<tier>/pNNN.zip; with
    ``ocr_mode="txt"`` (the engine reads the PDF's own text layer from one-page PDFs, no
    render) .../txt/<tier>/pNNN.zip."""

    def __init__(self, root, source_sha: str, version: str, dpi: int, ocr_mode: str = "ocr"):
        if ocr_mode not in OCR_MODES:
            raise ValueError(f"ocr_mode must be one of {', '.join(OCR_MODES)}")
        self.root = Path(root)
        self.source_sha = source_sha
        self.version = version
        self.dpi = dpi
        self.ocr_mode = ocr_mode

    def dir(self, tier: str) -> Path:
        key = str(self.dpi) if self.ocr_mode == "ocr" else "txt"
        return self.root / self.source_sha[:16] / self.version / key / tier

    def path(self, tier: str, page: int) -> Path:
        return self.dir(tier) / f"p{page:03d}.zip"

    def has(self, tier: str, page: int) -> bool:
        return _valid_zip(self.path(tier, page))

    def missing(self, tier: str, pages) -> list[int]:
        return [p for p in pages if not self.has(tier, p)]

    def read(self, tier: str, page: int) -> RawPage:
        zp = self.path(tier, page)
        with zipfile.ZipFile(zp) as z:
            md = z.read("markdown.md").decode("utf-8", "replace")
            middle = json.loads(z.read("middle_json.json").decode("utf-8", "replace"))
        pages = middle.get("pages") or [{}]
        return RawPage(page, tier, md, list(pages[0].get("blocks") or []), zp)


def _valid_zip(path: Path) -> bool:
    if not path.is_file() or not zipfile.is_zipfile(path):
        return False
    try:
        with zipfile.ZipFile(path) as z:
            names = set(z.namelist())
            return all(m in names for m in MEMBERS) and z.testzip() is None
    except (zipfile.BadZipFile, OSError):
        return False


# ---------------------------------------------------------------- running

def render_page(doc, page: int, path: Path, dpi: int) -> None:
    import pymupdf

    pix = doc[page - 1].get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
    pix.save(str(path))


def run(engine: Engine, pdf, pages, cache: Cache, tiers=TIERS, progress=None,
        log_dir=None) -> dict[str, int]:
    """Run each tier over the given 1-based pages that the cache lacks; returns the pages
    newly parsed per tier. ``progress(tier, done, total)`` is called while a tier runs.
    Raises EngineError when a tier stops before all its pages are written."""
    import pymupdf

    pages = sorted(set(int(p) for p in pages))
    doc = pymupdf.open(str(pdf))
    try:
        for p in pages:
            if not 1 <= p <= doc.page_count:
                raise EngineError(f"page {p} is beyond the last page ({doc.page_count}) of {pdf}")
        stats = {}
        for tier in tiers:
            missing = cache.missing(tier, pages)
            stats[tier] = 0
            if not missing:
                continue
            tier_dir = cache.dir(tier)
            tier_dir.mkdir(parents=True, exist_ok=True)
            for p in missing:                         # half-written zips of a stopped run
                zp = cache.path(tier, p)
                if zp.exists():
                    zp.unlink()
            img_dir = tier_dir / "_img"
            shutil.rmtree(img_dir, ignore_errors=True)
            img_dir.mkdir()
            try:
                for p in missing:
                    if cache.ocr_mode == "txt":       # the page itself, text layer and all
                        one = pymupdf.open()
                        one.insert_pdf(doc, from_page=p - 1, to_page=p - 1)
                        one.save(str(img_dir / f"p{p:03d}.pdf"))
                        one.close()
                    else:
                        render_page(doc, p, img_dir / f"p{p:03d}.png", cache.dpi)
                log = Path(log_dir or tier_dir) / f"mineru-{tier}.log"
                code = _parse(engine, img_dir, tier_dir, tier, log, progress,
                              lambda: sum(cache.has(tier, p) for p in missing), len(missing),
                              cache.ocr_mode)
            finally:
                shutil.rmtree(img_dir, ignore_errors=True)
            done = sum(cache.has(tier, p) for p in missing)
            if code or done < len(missing):
                raise EngineError(f"{tier} tier stopped after {done} of {len(missing)} pages "
                                  f"(exit code {code}); see {log}")
            stats[tier] = done
    finally:
        doc.close()
    return stats


def _parse(engine, img_dir, out_dir, tier, log, progress, count_done, total,
           ocr_mode: str = "ocr") -> int:
    cmd = [engine.exe, "parse", str(img_dir), "-o", str(out_dir), "-f", "zip",
           "--tier", tier, "--ocr-mode", ocr_mode]
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "a", encoding="utf-8") as lf:
        lf.write(f"\n# {time.strftime('%Y-%m-%d %H:%M:%S')} {' '.join(cmd)}\n")
        lf.flush()
        try:
            proc = subprocess.Popen(cmd, stdout=lf, stderr=subprocess.STDOUT,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
                                    if sys.platform == "win32" else 0)
        except OSError as e:
            raise EngineError(f"cannot run {engine.exe}: {e}") from None
        last = -1
        while True:
            try:
                code = proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                code = None
            done = count_done()
            if progress and done != last:
                progress(tier, done, total)
                last = done
            if code is not None:
                return code
