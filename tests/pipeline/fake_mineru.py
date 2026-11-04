# SPDX-License-Identifier: AGPL-3.0-or-later
"""A stand-in ``mineru-kit`` for tests: answers ``--version`` and ``parse DIR -o OUT -f zip
--tier T ...`` by writing one OUT/<stem>.zip per PNG in DIR (markdown.md and
middle_json.json in the engine's shape), from a page script if one is given.

Environment (set by the tests):
  FAKE_MINERU_VERSION     the version it reports (default 4.1.7)
  FAKE_MINERU_LOG         a file every invocation's argv is appended to (one JSON line)
  FAKE_MINERU_STOP_AFTER  exit with code 1 after this many pages (a stopped run)
  FAKE_MINERU_PAGES       JSON file {stem: {tier: {"markdown": .., "blocks": [..]}}};
                          pages not in it get a one-paragraph placeholder
"""

import hashlib
from pathlib import Path

from .fake_exe import make_exe

CODE = r'''
import json, os, sys, zipfile
from pathlib import Path

argv = sys.argv[1:]
log = os.environ.get("FAKE_MINERU_LOG")
if log:
    inputs = sorted(p.name for p in Path(argv[1]).iterdir()) if argv[:1] == ["parse"] else []
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps({"argv": argv, "inputs": inputs}) + "\n")
if argv[:1] == ["--version"]:
    print("MinerU version: " + os.environ.get("FAKE_MINERU_VERSION", "4.1.7"))
    print("Python version: 3.12.16")
    sys.exit(0)
assert argv[0] == "parse", argv
src = Path(argv[1])
out = Path(argv[argv.index("-o") + 1])
tier = argv[argv.index("--tier") + 1]
assert argv[argv.index("-f") + 1] == "zip"
assert argv[argv.index("--ocr-mode") + 1] == "ocr"
pages = {}
if os.environ.get("FAKE_MINERU_PAGES"):
    pages = json.loads(Path(os.environ["FAKE_MINERU_PAGES"]).read_text("utf-8"))
stop = int(os.environ.get("FAKE_MINERU_STOP_AFTER", "0") or 0)
out.mkdir(parents=True, exist_ok=True)
files = sorted(p for p in src.iterdir() if p.suffix.lower() == ".png")
if not files:
    print("No input files found.", file=sys.stderr)
    sys.exit(2)
for n, png in enumerate(files, 1):
    if stop and n > stop:
        print("stopped", file=sys.stderr)
        sys.exit(1)
    spec = pages.get(png.stem, {}).get(tier)
    if spec is None:
        text = f"Page {png.stem} by the {tier} tier."
        spec = {"markdown": text + "\n",
                "blocks": [{"type": "text", "index": 0, "bbox": [0.1, 0.1, 0.9, 0.2],
                            "content": [{"type": "text", "content": text}]}]}
    middle = {"metadata": {"producer": {"name": "mineru", "version": "4.1.7"}},
              "extensions": {"mineru": {"tier": tier, "parse_mode": "ocr"}},
              "pages": [{"page_idx": 0, "blocks": spec["blocks"]}],
              "schema": "docvortex.middle", "schema_version": "2.0"}
    with zipfile.ZipFile(out / f"{png.stem}.zip", "w") as z:
        z.writestr("markdown.md", spec["markdown"])
        z.writestr("middle_json.json", json.dumps(middle))
print(f"Parsed {len(files)} input(s).")
'''

MODEL_FILES = ("MinerU2.5-Pro-2605-1.2B-Q8_0.gguf", "mmproj-MinerU2.5-Pro-2605-1.2B-Q8_0.gguf")


def make_fake_mineru(folder) -> str:
    """The fake executable's path."""
    return make_exe(folder, "mineru-kit", CODE)


def make_home(folder) -> tuple[Path, dict[str, str]]:
    """A fake MinerU home with the two model files; returns (home, their sha256 pins)."""
    home = Path(folder) / "mineru-home"
    d = home / "models" / "MinerU2.5-Pro-2605-1.2B-GGUF"
    d.mkdir(parents=True)
    pins = {}
    for i, name in enumerate(MODEL_FILES):
        data = bytes([i]) * 1000
        (d / name).write_bytes(data)
        pins[name] = hashlib.sha256(data).hexdigest()
    return home, pins


