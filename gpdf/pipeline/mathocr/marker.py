# SPDX-License-Identifier: AGPL-3.0-or-later
"""Marker (Surya 2.5) as a third opinion, run as an external program like MinerU.

Marker is installed by the user in its own environment and never imported or bundled:
its code is Apache-2.0, its weights a modified OpenRAIL-M (free for personal use,
research and organisations under US$5 M revenue). It serves the Surya VLM through the
official llama.cpp ``llama-server`` binary, which the user downloads from
github.com/ggml-org/llama.cpp/releases. g-pdf starts that server itself, with the GGUF
weights it has checked against the pins, points ``marker`` at it (SURYA_INFERENCE_URL,
the documented external-server mode) and stops it when the run ends, so the GPU is free
again before anything else runs.

Pins: the marker-pdf and surya-ocr versions (read from the venv's package metadata), the
llama.cpp build number (``llama-server --version``) and the SHA-256 of the two GGUF files.

Pages go to Marker in chunks: a small PDF made of the source's own pages (Marker renders
them itself), one ``marker`` call over the folder of chunks. Each finished chunk is split
into one cache zip per page as soon as it appears, so a stopped run loses at most the
chunk in progress. Cache key, as for MinerU: the source file's SHA-256, the page, the
engine version and Marker's OCR dpi.
"""

from __future__ import annotations

import html
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from gpdf.pipeline.mathocr.engine import EngineError, sha256_of

ENV_VAR = "GPDF_MARKER"                  # the marker executable or its venv folder
SERVER_ENV_VAR = "GPDF_LLAMA_SERVER"     # llama-server(.exe) or its folder
GGUF_ENV_VAR = "GPDF_MARKER_GGUF"        # the folder holding the two GGUF files
PINNED_PACKAGES = {"marker-pdf": "2.1.0", "surya-ocr": "0.23.0"}
PINNED_LLAMA_BUILD = "11765"             # llama.cpp release b11765 (commit 9df0b73c7)
GGUF_REPO = "datalab-to/surya-ocr-2.5-gguf"
MODEL_FILE = "surya-2.5.gguf"
MMPROJ_FILE = "surya-2.5-mmproj.gguf"
PINNED_GGUF = {
    MODEL_FILE: "45fa3e587cf5a475f06e949fbe40e67b759dbcee1830e7effbcb172445d4b6bf",
    MMPROJ_FILE: "f245dbf784cf73683a273a37fefae0d0d7983b083a5d2fecf9309476f861f659",
}
MODEL_ALIAS = "datalab-to/surya-ocr-2.5"   # the name marker asks the server for
DPI = 192                                  # Marker's OCR render (its highres_image_dpi)
CHUNK = 16                                 # pages per marker document
PARALLEL = 8                               # llama-server slots (surya's llama.cpp default)
CTX_PER_SLOT = 12288                       # surya's SURYA_INFERENCE_CTX_PER_SLOT
MEMBER = "marker.json"
STDERR_TAIL = 600
INSTALL_HINT = ("install Marker 2.1.0 in its own environment and the llama.cpp release "
                f"b{PINNED_LLAMA_BUILD} (docs/math-export.md), then pass --marker PATH and "
                f"--llama-server PATH or set ${ENV_VAR} and ${SERVER_ENV_VAR}")


@dataclass
class Marker:
    exe: str
    server: str
    packages: dict[str, str]
    llama_build: str
    gguf_dir: Path
    gguf_hashes: dict[str, str]
    unpinned: list[str] = field(default_factory=list)

    @property
    def version(self) -> str:
        return self.packages.get("marker-pdf", "unknown")

    def describe(self) -> dict:
        return {"exe": self.exe, "packages": dict(self.packages),
                "pinned_packages": dict(PINNED_PACKAGES), "llama_server": self.server,
                "llama_build": self.llama_build, "pinned_llama_build": PINNED_LLAMA_BUILD,
                "gguf_repo": GGUF_REPO, "gguf_dir": str(self.gguf_dir),
                "gguf": dict(self.gguf_hashes), "pinned_gguf": dict(PINNED_GGUF),
                "unpinned": list(self.unpinned)}


# ---------------------------------------------------------------- finding and pinning

def _first(folder: Path, names) -> Path | None:
    for rel in names:
        if (folder / rel).is_file():
            return folder / rel
    return None


def find(explicit=None) -> str:
    """``explicit`` (the executable or its venv folder), else $GPDF_MARKER, else
    ``marker`` on PATH."""
    names = ("Scripts/marker.exe", "bin/marker", "marker.exe", "marker", "marker.cmd")
    for where, path in (("--marker", explicit), (ENV_VAR, os.environ.get(ENV_VAR))):
        if path:
            p = Path(path)
            if p.is_file():
                return str(p)
            if p.is_dir() and (exe := _first(p, names)):
                return str(exe)
            raise EngineError(f"marker not found at {path} (from {where})")
    path = shutil.which("marker")
    if path:
        return path
    raise EngineError(f"marker not found: {INSTALL_HINT}")


def find_server(explicit=None) -> str:
    names = ("llama-server.exe", "llama-server", "llama-server.cmd")
    for where, path in (("--llama-server", explicit), (SERVER_ENV_VAR,
                                                       os.environ.get(SERVER_ENV_VAR))):
        if path:
            p = Path(path)
            if p.is_file():
                return str(p)
            if p.is_dir() and (exe := _first(p, names)):
                return str(exe)
            raise EngineError(f"llama-server not found at {path} (from {where})")
    path = shutil.which("llama-server")
    if path:
        return path
    raise EngineError(f"llama-server not found: {INSTALL_HINT}")


def _site_packages(exe: str) -> list[Path]:
    """The site-packages folders of the venv holding ``exe`` (Scripts/ or bin/ in it)."""
    venv = Path(exe).resolve().parent
    if venv.name.lower() in ("scripts", "bin"):
        venv = venv.parent
    return [p for p in [venv / "Lib" / "site-packages", *sorted((venv / "lib").glob(
        "python*/site-packages"))] if p.is_dir()]


def package_versions(exe: str) -> dict[str, str]:
    """The installed versions of the pinned packages, from their dist-info metadata."""
    out = {}
    for sp in _site_packages(exe):
        for name in PINNED_PACKAGES:
            norm = re.sub(r"[-_.]+", "_", name).lower()
            for meta in sp.glob("*.dist-info/METADATA"):
                dist = meta.parent.name.removesuffix(".dist-info").rsplit("-", 1)[0]
                if re.sub(r"[-_.]+", "_", dist).lower() != norm:
                    continue
                m = re.search(r"^Version:\s*(\S+)", meta.read_text("utf-8", "replace"), re.M)
                if m:
                    out[name] = m.group(1)
    missing = [n for n in PINNED_PACKAGES if n not in out]
    if missing:
        raise EngineError(f"cannot read the {', '.join(missing)} version next to {exe} "
                          f"(expected a venv with Scripts/ or bin/): {INSTALL_HINT}")
    return out


def llama_build(server: str) -> str:
    try:
        r = subprocess.run([server, "--version"], capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise EngineError(f"cannot run {server}: {e}") from None
    out = (r.stdout + r.stderr).decode("utf-8", "replace")
    m = re.search(r"build\s+(\d+)", out)
    if not m:
        raise EngineError(f"{server} does not look like llama-server (no build number in "
                          f"--version): {out.strip()[-STDERR_TAIL:]}")
    return m.group(1)


def hf_cache() -> Path:
    if os.environ.get("HF_HUB_CACHE"):
        return Path(os.environ["HF_HUB_CACHE"])
    return Path(os.environ.get("HF_HOME") or "~/.cache/huggingface").expanduser() / "hub"


def find_gguf(explicit=None) -> Path:
    """The folder holding both GGUF files: ``explicit``, $GPDF_MARKER_GGUF, else the
    newest Hugging Face cache snapshot of the GGUF repo (where surya downloads them)."""
    for where, path in (("--marker-gguf", explicit), (GGUF_ENV_VAR, os.environ.get(GGUF_ENV_VAR))):
        if path:
            p = Path(path)
            if all((p / f).is_file() for f in PINNED_GGUF):
                return p
            raise EngineError(f"{MODEL_FILE} and {MMPROJ_FILE} not both found in {path} "
                              f"(from {where})")
    snaps = hf_cache() / ("models--" + GGUF_REPO.replace("/", "--")) / "snapshots"
    found = [d for d in (snaps.iterdir() if snaps.is_dir() else [])
             if all((d / f).is_file() for f in PINNED_GGUF)]
    if not found:
        raise EngineError(f"the Surya GGUF files ({GGUF_REPO}) are not in {snaps}: run marker "
                          f"once so surya downloads them, or pass --marker-gguf DIR")
    return max(found, key=lambda d: d.stat().st_mtime)


def prepare(explicit=None, server=None, gguf=None, pins=None, allow_unpinned=False) -> Marker:
    """find + versions + llama.cpp build + GGUF hashes; a mismatch stops unless allowed."""
    pins = PINNED_GGUF if pins is None else pins
    exe = find(explicit)
    packages = package_versions(exe)
    srv = find_server(server)
    build = llama_build(srv)
    gdir = find_gguf(gguf)
    hashes = {name: sha256_of(gdir / name) for name in pins}
    unpinned = []
    for name, want in PINNED_PACKAGES.items():
        if packages[name] != want:
            unpinned.append(name)
            if not allow_unpinned:
                raise EngineError(f"{name} {packages[name]} found, {want} expected (the version "
                                  f"measured on KBJ); pass --allow-unpinned to run anyway")
    if build != PINNED_LLAMA_BUILD:
        unpinned.append("llama.cpp")
        if not allow_unpinned:
            raise EngineError(f"llama.cpp build {build} found, b{PINNED_LLAMA_BUILD} expected; "
                              f"pass --allow-unpinned to run anyway")
    for name, want in pins.items():
        if hashes[name] != want:
            unpinned.append(name)
            if not allow_unpinned:
                raise EngineError(f"{name} has sha256 {hashes[name][:16]}..., {want[:16]}... "
                                  f"expected; pass --allow-unpinned to run anyway")
    return Marker(exe, srv, packages, build, gdir, hashes, unpinned)


# ---------------------------------------------------------------- cache

class Cache:
    """Marker's JSON per page under
    ROOT/<source sha256[:16]>/marker-<version>/<dpi>/marker/pNNN.zip."""

    def __init__(self, root, source_sha: str, version: str, dpi: int = DPI):
        self.root = Path(root)
        self.source_sha = source_sha
        self.version = version
        self.dpi = dpi

    def dir(self) -> Path:
        return self.root / self.source_sha[:16] / f"marker-{self.version}" / str(self.dpi) / "marker"

    def path(self, page: int) -> Path:
        return self.dir() / f"p{page:03d}.zip"

    def has(self, page: int) -> bool:
        p = self.path(page)
        if not p.is_file() or not zipfile.is_zipfile(p):
            return False
        try:
            with zipfile.ZipFile(p) as z:
                return MEMBER in z.namelist() and z.testzip() is None
        except (zipfile.BadZipFile, OSError):
            return False

    def missing(self, pages) -> list[int]:
        return [p for p in pages if not self.has(p)]

    def read(self, page: int) -> dict:
        with zipfile.ZipFile(self.path(page)) as z:
            return json.loads(z.read(MEMBER).decode("utf-8", "replace"))

    def write(self, page: int, data: dict) -> None:
        self.dir().mkdir(parents=True, exist_ok=True)
        tmp = self.path(page).with_suffix(".tmp")
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr(MEMBER, json.dumps(data, ensure_ascii=False))
        os.replace(tmp, self.path(page))


# ---------------------------------------------------------------- running

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _kill(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if sys.platform == "win32":          # the whole tree (a wrapper may have children)
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
    else:
        proc.terminate()
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()


def _tail(path: Path) -> str:
    try:
        return path.read_text("utf-8", "replace").strip()[-STDERR_TAIL:]
    except OSError:
        return ""


def _popen(cmd, log: Path, env=None) -> subprocess.Popen:
    lf = open(log, "a", encoding="utf-8")
    lf.write(f"\n# {time.strftime('%Y-%m-%d %H:%M:%S')} {' '.join(map(str, cmd))}\n")
    lf.flush()
    try:
        return subprocess.Popen([str(c) for c in cmd], stdout=lf, stderr=subprocess.STDOUT,
                                env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
                                if sys.platform == "win32" else 0)
    except OSError as e:
        raise EngineError(f"cannot run {cmd[0]}: {e}") from None
    finally:
        lf.close()


def start_server(m: Marker, log: Path, timeout: float = 600) -> tuple[subprocess.Popen, str]:
    """llama-server with the checked GGUF files on a free local port; returns (process,
    base url) once /health answers."""
    port = _free_port()
    cmd = [m.server, "-m", m.gguf_dir / MODEL_FILE, "--mmproj", m.gguf_dir / MMPROJ_FILE,
           "-ngl", "99", "--host", "127.0.0.1", "--port", port, "--parallel", PARALLEL,
           "--ctx-size", max(16384, PARALLEL * CTX_PER_SLOT), "--alias", MODEL_ALIAS,
           "--jinja", "--special"]
    proc = _popen(cmd, log)
    url = f"http://127.0.0.1:{port}"
    t0 = time.time()
    while True:
        if proc.poll() is not None:
            raise EngineError(f"llama-server stopped while starting (exit code "
                              f"{proc.returncode}): {_tail(log)}")
        try:
            with urllib.request.urlopen(url + "/health", timeout=5) as r:
                if r.status == 200:
                    return proc, url
        except OSError:
            pass
        if time.time() - t0 > timeout:
            _kill(proc)
            raise EngineError(f"llama-server did not answer within {timeout:.0f} s; see {log}")
        time.sleep(0.5)


def _chunks(pages: list[int], size: int) -> list[list[int]]:
    return [pages[i:i + size] for i in range(0, len(pages), size)]


def _split(chunk_json: Path, pages: list[int], cache: Cache) -> bool:
    """Write one cache zip per page of a finished chunk; False while it is incomplete."""
    try:
        data = json.loads(chunk_json.read_text("utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return False
    kids = [c for c in data.get("children") or [] if c.get("block_type") == "Page"]
    if len(kids) != len(pages):
        raise EngineError(f"{chunk_json.name}: {len(kids)} pages in Marker's output, "
                          f"{len(pages)} sent")
    for p, kid in zip(pages, kids):
        cache.write(p, kid)
    return True


def run(m: Marker, pdf, pages, cache: Cache, progress=None, log_dir=None,
        chunk: int = CHUNK) -> int:
    """Marker over the given 1-based pages that the cache lacks; returns the number newly
    parsed. ``progress(done, total)`` is called as chunks finish."""
    import pymupdf

    pages = sorted(set(int(p) for p in pages))
    missing = cache.missing(pages)
    if not missing:
        return 0
    work = cache.dir().parent / "_work"
    shutil.rmtree(work, ignore_errors=True)
    in_dir, out_dir = work / "in", work / "out"
    in_dir.mkdir(parents=True)
    log_dir = Path(log_dir or work)
    log_dir.mkdir(parents=True, exist_ok=True)
    src = pymupdf.open(str(pdf))
    try:
        for p in missing:
            if not 1 <= p <= src.page_count:
                raise EngineError(f"page {p} is beyond the last page ({src.page_count}) of {pdf}")
        todo = {}
        for k, pp in enumerate(_chunks(missing, chunk), 1):
            name = f"c{k:03d}"
            doc = pymupdf.open()
            for p in pp:
                doc.insert_pdf(src, from_page=p - 1, to_page=p - 1)
            doc.save(str(in_dir / f"{name}.pdf"))
            doc.close()
            todo[name] = pp
    finally:
        src.close()
    server = None
    done = 0
    try:
        server, url = start_server(m, log_dir / "llama-server.log")
        env = dict(os.environ, SURYA_INFERENCE_BACKEND="llamacpp",
                   SURYA_INFERENCE_URL=url + "/v1", SURYA_INFERENCE_AUTOSTART="false",
                   PYTHONUTF8="1")
        cmd = [m.exe, in_dir, "--output_dir", out_dir, "--output_format", "json",
               "--force_ocr", "--disable_image_extraction", "--workers", "1",
               "--disable_tqdm"]
        log = log_dir / "marker.log"
        proc = _popen(cmd, log, env)
        try:
            while True:
                try:
                    code = proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    code = None
                for name in list(todo):
                    if _split(out_dir / name / f"{name}.json", todo[name], cache):
                        done += len(todo.pop(name))
                        if progress:
                            progress(done, len(missing))
                if code is not None:
                    break
        finally:
            _kill(proc)
        if code or todo:
            left = sum(len(v) for v in todo.values())
            raise EngineError(f"marker stopped with {left} of {len(missing)} pages left "
                              f"(exit code {code}): {_tail(log)}")
    finally:
        if server is not None:
            _kill(server)
        shutil.rmtree(work, ignore_errors=True)
    return done


# ---------------------------------------------------------------- Marker JSON -> blocks

DROP = {"PageHeader", "PageFooter"}
TEXT_TYPES = {"Text", "TextInlineMath", "ListItem", "ListGroup", "Caption", "Footnote",
              "SectionHeader", "Handwriting"}
CONTENT_REF = re.compile(r"<content-ref src='([^']+)'></content-ref>")
MATH_RE = re.compile(r"<math(?P<attrs>[^>]*)>(?P<tex>.*?)</math>", re.S)
TAG_RE = re.compile(r"<[^>]+>")


def _html(block: dict) -> str:
    """The block's HTML with its children's HTML filled in (headers and footers out)."""
    kids = {c.get("id"): c for c in block.get("children") or []}

    def sub(mo):
        c = kids.get(mo.group(1))
        return "" if c is None or c.get("block_type") in DROP else _html(c)
    return CONTENT_REF.sub(sub, block.get("html") or "")


def _fractions(bbox, w: float, h: float):
    if not bbox or not w or not h:
        return None
    return [round(bbox[0] / w, 4), round(bbox[1] / h, 4), round(bbox[2] / w, 4),
            round(bbox[3] / h, 4)]


def _text(s: str) -> str:
    return html.unescape(TAG_RE.sub("", s))


def blocks(page: dict) -> list[dict]:
    """A Marker page as MinerU-shaped blocks: ``equation`` (content = the display's LaTeX)
    and ``text`` (content = text and ``equation_inline`` parts), bboxes as page fractions,
    in Marker's reading order. Running heads and feet are dropped; tables and figures are
    kept only as their type."""
    bb = page.get("bbox") or [0, 0, 0, 0]
    w, h = bb[2] - bb[0], bb[3] - bb[1]
    out = []
    for i, b in enumerate(page.get("children") or []):
        kind = b.get("block_type")
        if kind in DROP:
            continue
        box = _fractions(b.get("bbox"), w, h)
        src = _html(b)
        if kind == "Equation":
            for mo in MATH_RE.finditer(src):
                tex = html.unescape(mo.group("tex")).strip()
                if tex:
                    out.append({"type": "equation", "index": i, "bbox": box, "content": tex})
            continue
        if kind not in TEXT_TYPES:
            out.append({"type": str(kind).lower(), "index": i, "bbox": box})
            continue
        parts, pos, inner = [], 0, []
        for mo in MATH_RE.finditer(src):
            parts.append({"type": "text", "content": _text(src[pos:mo.start()])})
            tex = html.unescape(mo.group("tex")).strip()
            if 'display="block"' in mo.group("attrs"):
                inner.append(tex)              # a display inside text: a display after it
            else:
                parts.append({"type": "equation_inline", "content": tex})
            pos = mo.end()
        parts.append({"type": "text", "content": _text(src[pos:])})
        parts = [p for p in parts if p["type"] != "text" or p["content"].strip()]
        out.append({"type": "text", "index": i, "bbox": box, "content": parts})
        out += [{"type": "equation", "index": i, "bbox": box, "content": t} for t in inner if t]
    return out


def has_math(blocks_: list[dict]) -> bool:
    """A page with a display or an inline formula (MinerU or Marker block shape)."""
    for b in blocks_:
        if b.get("type") == "equation":
            return True
        c = b.get("content")
        if isinstance(c, list) and any(isinstance(p, dict) and p.get("type") == "equation_inline"
                                       for p in c):
            return True
    return False
