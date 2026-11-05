# SPDX-License-Identifier: AGPL-3.0-or-later
r"""gpdf-cli math-check-pages: structural checks on a folder of pNNN.md / pNNN.flags.json
(or a text.md), a reference transcription or pipeline output alike.

Per page: balanced `$$` and `\( \)`; in a multi-line environment (aligned, array, cases,
the matrices, ...) a line ending in a single `\` (or `\[2ex]` with one backslash) where a
`\\` row break was meant, and an environment of several lines with no row break at all;
balanced braces, `\begin`/`\end` and `\left`/`\right` in every display and inline span;
a flags file that parses as a JSON list of objects (or is missing).

Problems carry the page and the kind; the formula itself goes only to the JSON output.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from gpdf.pipeline.manuals import write_atomic
from gpdf.pipeline.mathocr import latex as L

KINDS = ("display_delimiters", "inline_delimiters", "single_backslash_break", "no_row_breaks",
         "braces", "begin_end", "left_right", "flags_json", "flags_missing")
MULTILINE = re.compile(r"aligned|align\*?|alignedat|eqnarray\*?|array|subarray|cases|"
                       r"gathered|gather\*?|split|multline\*?|[pbvBV]?matrix|smallmatrix")
ENV_RE = re.compile(r"\\(begin|end)\s*\{([A-Za-z*]+)\}")
DELIM_RE = re.compile(r"(\\+)([()])")
SPACING_BREAK_RE = re.compile(r"(\\+)\[\s*-?[\d.]+\s*(?:ex|em|pt|mm|cm|in|mu)\s*\]")
TRAILING_BS_RE = re.compile(r"(\\+)[ \t]*$")


class CheckError(Exception):
    pass


def _balance_kind(msg: str) -> str:
    if "brace" in msg:
        return "braces"
    if "left" in msg or "right" in msg:
        return "left_right"
    return "begin_end"


def _envs(tex: str) -> list[tuple[str, int, int]]:
    """(name, body start, body end) of each multi-line environment, by a begin/end stack."""
    out, stack = [], []
    for m in ENV_RE.finditer(tex):
        if m.group(1) == "begin":
            stack.append((m.group(2), m.end()))
        elif stack and stack[-1][0] == m.group(2):
            name, start = stack.pop()
            if MULTILINE.fullmatch(name):
                out.append((name, start, m.start()))
    return out


def _rows(tex: str) -> list[tuple[str, str]]:
    """(kind, detail) problems of the row breaks in one display."""
    problems = []
    for name, a, b in _envs(tex):
        body = tex[a:b]
        lonely = 0
        for line in body.split("\n"):
            m = TRAILING_BS_RE.search(line)
            if m and len(m.group(1)) % 2 == 1:
                lonely += 1
        lonely += sum(1 for m in SPACING_BREAK_RE.finditer(body) if len(m.group(1)) % 2 == 1)
        problems += [("single_backslash_break", name)] * lonely
        lines = [s for s in body.split("\n") if s.strip() and not ENV_RE.fullmatch(s.strip())]
        if not lonely and len(lines) >= 2 and "\\\\" not in body:
            problems.append(("no_row_breaks", name))
    return problems


def check_md(md: str) -> list[tuple[str, str]]:
    """(kind, the formula or a note) for every structural problem of one page."""
    md = L.COMMENT_RE.sub("", md)
    out = []
    if md.count("$$") % 2:
        out.append(("display_delimiters", f"{md.count('$$')} $$"))
    displays = [m.group("a") or m.group("b") or m.group("c") for m in L.DISPLAY_RE.finditer(md)]
    prose = L.DISPLAY_RE.sub(" ", md)
    depth, bad = 0, False
    for m in DELIM_RE.finditer(prose):
        if len(m.group(1)) % 2 == 0:            # \\( is a row break and a parenthesis
            continue
        depth += 1 if m.group(2) == "(" else -1
        if depth not in (0, 1):
            bad = True
            depth = max(0, min(1, depth))
    if bad or depth:
        out.append(("inline_delimiters", "unbalanced \\( \\)"))
    for tex in displays:
        out += [(_balance_kind(p), tex) for p in L.balance_problems(tex)]
        out += [(k, tex) for k, _ in _rows(tex)]
    for a, b in L.INLINE_RE.findall(prose):
        tex = a or b
        out += [(_balance_kind(p), tex) for p in L.balance_problems(tex)]
    return out


def _flags_problem(path: Path) -> str | None:
    if not path.is_file():
        return "no flags file"
    try:
        data = json.loads(path.read_text("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        return f"not readable as JSON ({e})"
    if not isinstance(data, list) or not all(isinstance(f, dict) for f in data):
        return "not a JSON list of objects"
    return None


def check_pages(folder) -> dict:
    """Every page of a pages folder (flags files too), an export folder or a text.md (split
    by its page markers; its flags are comment lines, not checked)."""
    folder = Path(folder)
    if folder.is_file():
        from gpdf.pipeline.mathocr.score import _pages_from_text

        mds = _pages_from_text(folder.read_text("utf-8"))
        if not mds:
            raise CheckError(f"no page markers in {folder}")
        problems = [{"page": stem, "kind": kind, "detail": detail}
                    for stem, md in mds.items() for kind, detail in check_md(md)]
        return _result(folder, len(mds), problems)
    if not folder.is_dir():
        raise CheckError(f"{folder} not found")
    if (folder / "pages").is_dir() and not list(folder.glob("p[0-9][0-9][0-9].md")):
        folder = folder / "pages"
    pages = sorted(folder.glob("p[0-9][0-9][0-9].md"))
    if not pages:
        raise CheckError(f"no pNNN.md pages in {folder}")
    problems = []
    for p in pages:
        for kind, detail in check_md(p.read_text("utf-8")):
            problems.append({"page": p.stem, "kind": kind, "detail": detail})
        fp = _flags_problem(p.with_name(p.stem + ".flags.json"))
        if fp:
            problems.append({"page": p.stem, "kind": "flags_missing" if fp == "no flags file"
                             else "flags_json", "detail": fp})
    return _result(folder, len(pages), problems)


def _result(folder: Path, pages: int, problems: list[dict]) -> dict:
    counts = {k: sum(1 for x in problems if x["kind"] == k) for k in KINDS}
    return {"folder": str(folder), "pages": pages, "counts": counts, "problems": problems}


def write(res: dict, out) -> None:
    write_atomic(Path(out), json.dumps(res, ensure_ascii=False, indent=1) + "\n")


def report(res: dict) -> str:
    """Counts by kind and the pages with each; no formulas."""
    lines = [f"{res['pages']} pages, {len(res['problems'])} problem(s)"]
    for k, n in res["counts"].items():
        if n:
            where = sorted({x["page"] for x in res["problems"] if x["kind"] == k})
            lines.append(f"{k}: {n} ({', '.join(where)})")
    return "\n".join(lines)
