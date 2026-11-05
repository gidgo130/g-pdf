# SPDX-License-Identifier: AGPL-3.0-or-later
"""DjVu input for math-export, through DjVuLibre's command-line tools (GPL, an external
program found on PATH or named by --ddjvu / $GPDF_DDJVU; never imported or bundled).

The selected pages are rendered by ``ddjvu -format=pnm -page=N -scale=DPI`` and cached
as PNG (key: the DjVu's SHA-256, the page, the dpi); a proxy PDF with the DjVu's page
count carries each rendered page as an image at its physical size and a blank page for
every other one. The rest of math-export reads the proxy, so file page N is DjVu page N
(1-based) everywhere: page markers, crops, Marker's chunks, the review pack's renders.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pymupdf

ENV_VAR = "GPDF_DDJVU"
WINDOWS_DIRS = (r"C:\Program Files (x86)\DjVuLibre", r"C:\Program Files\DjVuLibre")
INSTALL_HINT = ("DjVuLibre is needed to read a DjVu (an external program, not a Python "
                "package). On Windows: `winget install DjVuLibre.DjView` (the official "
                "DjVuLibre + DjView installer from djvu.sourceforge.net; it installs to "
                r"C:\Program Files (x86)\DjVuLibre), then put that folder on PATH or pass "
                "--ddjvu FOLDER (or set $GPDF_DDJVU). conda-forge has no Windows build.")


class DjvuError(Exception):
    """A refusal or failure with a message for the user."""


def is_djvu(path) -> bool:
    return Path(path).suffix.lower() in (".djvu", ".djv")


def _exe(folder: Path, name: str) -> Path | None:
    for cand in (folder / f"{name}.exe", folder / name, folder / f"{name}.cmd"):
        if cand.is_file():
            return cand
    return None


@dataclass
class Tools:
    ddjvu: str
    djvused: str

    def _run(self, *args, timeout=300) -> str:
        try:
            r = subprocess.run(list(args), capture_output=True, text=True, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise DjvuError(f"{Path(args[0]).name} failed: {e}") from None
        if r.returncode:
            raise DjvuError(f"{Path(args[0]).name} {' '.join(map(str, args[1:3]))} failed "
                            f"(exit {r.returncode}): {(r.stderr or r.stdout).strip()[-300:]}")
        return r.stdout

    def page_count(self, path) -> int:
        out = self._run(self.djvused, "-e", "n", str(path)).strip()
        if not out.isdigit():
            raise DjvuError(f"djvused gave no page count for {path}: {out[:80]!r}")
        return int(out)

    def version(self) -> str:
        try:
            r = subprocess.run([self.ddjvu, "--help"], capture_output=True, text=True,
                               timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            return ""
        text = (r.stdout + r.stderr).strip()
        return text.splitlines()[0].strip() if text else ""

    def render(self, path, page: int, dpi: int, png: Path) -> None:
        """DjVu page ``page`` (1-based) at ``dpi`` into ``png``."""
        png.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="gpdf-ddjvu-") as tmp:
            pnm = Path(tmp) / "page.pnm"
            self._run(self.ddjvu, "-format=pnm", f"-page={page}", f"-scale={dpi}", str(path),
                      str(pnm))
            pix = pymupdf.Pixmap(str(pnm))
            part = png.with_suffix(".part.png")
            pix.save(str(part))
            os.replace(part, png)

    def describe(self) -> dict:
        return {"ddjvu": self.ddjvu, "djvused": self.djvused, "version": self.version()}


def prepare(explicit=None) -> Tools:
    """ddjvu and djvused from ``explicit`` (either executable or their folder), else
    $GPDF_DDJVU, else PATH, else the Windows install folder."""
    given = explicit or os.environ.get(ENV_VAR)
    if given:
        p = Path(given)
        folders = [p if p.is_dir() else p.parent]
    else:
        found = shutil.which("ddjvu")
        folders = [Path(found).parent] if found else [Path(d) for d in WINDOWS_DIRS]
    for folder in folders:
        dd, ds = _exe(folder, "ddjvu"), _exe(folder, "djvused")
        if dd and ds:
            return Tools(str(dd), str(ds))
    where = given or "PATH"
    raise DjvuError(f"no ddjvu and djvused in {where}. {INSTALL_HINT}")


def proxy_pdf(tools: Tools, path, pages: list[int], dpi: int, cache_dir, out) -> Path:
    """The proxy PDF ``out``: as many pages as the DjVu, ``pages`` rendered at ``dpi``
    (from ``cache_dir`` when there), the others blank at the first rendered page's size."""
    path, cache_dir, out = Path(path), Path(cache_dir), Path(out)
    n = tools.page_count(path)
    bad = [p for p in pages if not 1 <= p <= n]
    if bad:
        raise DjvuError(f"page {bad[0]} is outside the DjVu (it has {n} pages)")
    rendered = {}
    for p in sorted(set(pages)):
        png = cache_dir / f"p{p:03d}.png"
        if not png.is_file():
            tools.render(path, p, dpi, png)
        rendered[p] = png
    doc = pymupdf.open()
    size = None
    sizes = {}
    for p, png in rendered.items():
        pix = pymupdf.Pixmap(str(png))
        sizes[p] = (pix.width * 72 / dpi, pix.height * 72 / dpi)
        size = size or sizes[p]
    size = size or (612, 792)
    for p in range(1, n + 1):
        w, h = sizes.get(p, size)
        pg = doc.new_page(width=w, height=h)
        if p in rendered:
            pg.insert_image(pg.rect, filename=str(rendered[p]))
    out.parent.mkdir(parents=True, exist_ok=True)
    part = out.with_suffix(".part.pdf")
    doc.save(str(part), garbage=3, deflate=True)
    doc.close()
    os.replace(part, out)
    return out
