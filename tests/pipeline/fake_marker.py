# SPDX-License-Identifier: AGPL-3.0-or-later
"""Stand-ins for Marker and llama-server in tests.

The fake ``marker IN --output_dir OUT --output_format json ...`` writes OUT/<stem>/<stem>.json
for each PDF in IN, one Page per PDF page, after checking that $SURYA_INFERENCE_URL answers
/health (the server g-pdf started). A page whose text says "Page N" gets the blocks given for
pNNN in a page script, else one text block.

Environment (set by the tests):
  FAKE_MARKER_LOG         a file every marker invocation's argv and env is appended to
  FAKE_MARKER_STOP_AFTER  exit with code 1 after writing this many documents
  FAKE_MARKER_PAGES       JSON file {stem: [Marker blocks]}
  FAKE_LLAMA_LOG          a file every llama-server invocation's argv is appended to
  FAKE_LLAMA_BUILD        the build number llama-server --version reports (default 11765)
"""

import hashlib
from pathlib import Path

from .fake_exe import make_exe

MARKER = r'''
import json, os, re, sys, urllib.request
from pathlib import Path
import pymupdf

argv = sys.argv[1:]
log = os.environ.get("FAKE_MARKER_LOG")
if log:
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps({"argv": argv, "url": os.environ.get("SURYA_INFERENCE_URL"),
                            "backend": os.environ.get("SURYA_INFERENCE_BACKEND"),
                            "inputs": sorted(p.name for p in Path(argv[0]).iterdir())}) + "\n")
url = os.environ["SURYA_INFERENCE_URL"].removesuffix("/v1")
assert urllib.request.urlopen(url + "/health", timeout=5).status == 200
assert "--use_llm" not in argv and "--force_ocr" in argv
out = Path(argv[argv.index("--output_dir") + 1])
assert argv[argv.index("--output_format") + 1] == "json"
pages = {}
if os.environ.get("FAKE_MARKER_PAGES"):
    pages = json.loads(Path(os.environ["FAKE_MARKER_PAGES"]).read_text("utf-8"))
stop = int(os.environ.get("FAKE_MARKER_STOP_AFTER", "0") or 0)
for n, pdf in enumerate(sorted(Path(argv[0]).glob("*.pdf")), 1):
    if stop and n > stop:
        print("stopped", file=sys.stderr)
        sys.exit(1)
    doc = pymupdf.open(str(pdf))
    kids = []
    for i, pg in enumerate(doc):
        m = re.search(r"Page (\d+)", pg.get_text())
        stem = f"p{int(m.group(1)):03d}" if m else f"x{i}"
        w, h = pg.rect.width, pg.rect.height
        blocks = pages.get(stem) or [{"block_type": "Text", "bbox": [10, 10, w - 10, 40],
                                      "html": f"<p>Marker read {stem}.</p>"}]
        for k, b in enumerate(blocks):
            b.setdefault("id", f"/page/{i}/{b['block_type']}/{k}")
        kids.append({"id": f"/page/{i}/Page/0", "block_type": "Page", "bbox": [0, 0, w, h],
                     "children": blocks})
    d = out / pdf.stem
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{pdf.stem}.json").write_text(json.dumps({"block_type": "Document",
                                                     "children": kids}), encoding="utf-8")
print("Inferenced", flush=True)
'''

SERVER = r'''
import json, os, sys
from http.server import BaseHTTPRequestHandler, HTTPServer

argv = sys.argv[1:]
log = os.environ.get("FAKE_LLAMA_LOG")
if log:
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps({"argv": argv}) + "\n")
if argv[:1] == ["--version"]:
    b = os.environ.get("FAKE_LLAMA_BUILD", "11765")
    print(f"version: 0.7.0-dev (build {b}, commit 9df0b73c7)", file=sys.stderr)
    sys.exit(0)


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200 if self.path == "/health" else 404)
        self.end_headers()
        self.wfile.write(b'{"status":"ok"}')

    def log_message(self, *a):
        pass


HTTPServer(("127.0.0.1", int(argv[argv.index("--port") + 1])), H).serve_forever()
'''

GGUF_FILES = ("surya-2.5.gguf", "surya-2.5-mmproj.gguf")


def make_fake_marker(folder, marker="2.1.0", surya="0.23.0") -> str:
    """A fake venv with a marker executable and the two packages' metadata; returns the
    executable's path."""
    venv = Path(folder) / "venv-marker"
    scripts = venv / "Scripts"
    scripts.mkdir(parents=True)
    for name, ver in (("marker_pdf", marker), ("surya_ocr", surya)):
        d = venv / "Lib" / "site-packages" / f"{name}-{ver}.dist-info"
        d.mkdir(parents=True)
        (d / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {name}\nVersion: {ver}\n")
    return make_exe(scripts, "marker", MARKER)


def make_fake_server(folder) -> str:
    d = Path(folder) / "llama"
    d.mkdir()
    return make_exe(d, "llama-server", SERVER)


def make_gguf(folder) -> tuple[Path, dict[str, str]]:
    d = Path(folder) / "gguf"
    d.mkdir()
    pins = {}
    for i, name in enumerate(GGUF_FILES):
        data = bytes([i + 7]) * 500
        (d / name).write_bytes(data)
        pins[name] = hashlib.sha256(data).hexdigest()
    return d, pins
