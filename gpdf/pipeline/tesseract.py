# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Tesseract command line: find it, check it can make text-only PDFs in the wanted
languages, and run it on one page image.

One run writes both a text-only PDF (invisible GlyphLessFont text, no image) and a TSV of
the words with their confidences. Each process is limited to one OpenMP thread: several
single-threaded processes in parallel are much faster than multithreaded ones.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ENV_VAR = "GPDF_TESSERACT"
WINDOWS_DEFAULT = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
MIN_MAJOR = 5
TIMEOUT = 600              # seconds for one page
STDERR_TAIL = 400          # characters of stderr kept for a failed page
INSTALL_HINT = ("install Tesseract 5 (Windows: https://github.com/UB-Mannheim/tesseract/wiki, "
                "tick the languages you need) or pass --tesseract PATH")


class TesseractError(Exception):
    """Tesseract is missing or cannot do the job; the message is meant for the user."""


@dataclass
class Tesseract:
    exe: str
    version: str               # as Tesseract prints it, e.g. "5.5.3.20260724"
    tessdata: str
    langs: list[str]           # installed languages


def find(explicit=None) -> str:
    """``explicit``, else $GPDF_TESSERACT, else ``tesseract`` on PATH, else the default
    Windows install folder."""
    for where, path in (("--tesseract", explicit), (ENV_VAR, os.environ.get(ENV_VAR))):
        if path:
            if not Path(path).is_file():
                raise TesseractError(f"Tesseract not found at {path} (from {where})")
            return str(path)
    path = shutil.which("tesseract")
    if path:
        return path
    if sys.platform == "win32" and Path(WINDOWS_DEFAULT).is_file():
        return WINDOWS_DEFAULT
    raise TesseractError(f"Tesseract not found: {INSTALL_HINT}")


def _run(exe, *args) -> str:
    try:
        r = subprocess.run([exe, *args], capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise TesseractError(f"cannot run {exe}: {e}") from None
    out = (r.stdout + r.stderr).decode("utf-8", "replace")
    if r.returncode:
        raise TesseractError(f"{exe} {' '.join(args)} failed: {out.strip()[-STDERR_TAIL:]}")
    return out


def probe(exe) -> Tesseract:
    """Version, tessdata folder and installed languages of ``exe``; refuses Tesseract < 5."""
    m = re.search(r"tesseract\s+v?(\d+)\.(\d+)\S*", _run(exe, "--version"))
    if not m:
        raise TesseractError(f"{exe} does not look like Tesseract (no version found)")
    version = m.group(0).split()[-1].lstrip("v")
    if int(m.group(1)) < MIN_MAJOR:
        raise TesseractError(f"Tesseract {version} is too old: version {MIN_MAJOR} or "
                             f"later is needed ({INSTALL_HINT})")
    out = _run(exe, "--list-langs")
    lines = out.splitlines()
    head = next((i for i, s in enumerate(lines) if s.startswith("List of available languages")),
                None)
    m = re.search(r'"(.+)"', lines[head]) if head is not None else None
    if not m:
        raise TesseractError(f"cannot tell where {exe} keeps its language data (tessdata)")
    tessdata = os.path.normpath(m.group(1))
    langs = [s.strip() for s in lines[head + 1:] if s.strip()]
    return Tesseract(exe, version, tessdata, langs)


def check(t: Tesseract, langs: str) -> None:
    """``langs`` (e.g. "spa" or "eng+spa") installed, and the text-only PDF renderer's
    files present (without them Tesseract stops with "Can't open pdf")."""
    wanted = [s for s in langs.split("+") if s]
    if not wanted:
        raise TesseractError("no language given (e.g. --lang spa or --lang eng+spa)")
    missing = [s for s in wanted if s not in t.langs]
    if missing:
        raise TesseractError(f"Tesseract has no language data for {', '.join(missing)} "
                             f"(tessdata: {t.tessdata}); install it (UB Mannheim installer: "
                             "Additional language data) or pick from: "
                             f"{', '.join(s for s in t.langs if not s.startswith('script'))}")
    for need in (Path("configs") / "pdf", Path("pdf.ttf")):
        if not (Path(t.tessdata) / need).is_file():
            raise TesseractError(f"Tesseract cannot write PDFs: {Path(t.tessdata) / need} is "
                                 "missing (reinstall Tesseract)")


def prepare(langs: str, explicit=None) -> Tesseract:
    """find + probe + check."""
    t = probe(find(explicit))
    check(t, langs)
    return t


def recognize(t: Tesseract, image, outbase, langs: str, dpi: int,
              timeout: int = TIMEOUT) -> str:
    """OCR ``image`` into <outbase>.pdf (text only) and <outbase>.tsv. Returns "" on
    success, else the error (stderr tail)."""
    env = dict(os.environ, OMP_THREAD_LIMIT="1")
    cmd = [t.exe, str(image), str(outbase), "-l", langs, "--dpi", str(dpi),
           "-c", "textonly_pdf=1", "pdf", "tsv"]
    try:
        r = subprocess.run(cmd, capture_output=True, env=env, timeout=timeout)
    except subprocess.TimeoutExpired:
        return f"timed out after {timeout} s"
    except OSError as e:
        return f"cannot run {t.exe}: {e}"
    err = r.stderr.decode("utf-8", "replace").strip()[-STDERR_TAIL:]
    if r.returncode:
        return f"exit code {r.returncode}: {err}" if err else f"exit code {r.returncode}"
    for ext in (".pdf", ".tsv"):
        if not Path(f"{outbase}{ext}").is_file():
            return f"no {ext} written: {err}" if err else f"no {ext} written"
    return ""
