# SPDX-License-Identifier: AGPL-3.0-or-later
r"""gpdf-cli math-check-pages: structural checks on a folder of pNNN.md / pNNN.flags.json
(or a text.md), a reference transcription or pipeline output alike.

Per page: balanced `$$` and `\( \)`; in a multi-line environment (aligned, array, cases,
the matrices, ...) a line ending in a single `\` (or `\[2ex]` with one backslash) where a
`\\` row break was meant, and an environment of several lines with no row break at all;
balanced braces, `\begin`/`\end` and `\left`/`\right` in every display and inline span;
a flags file that parses as a JSON list of objects (or is missing). Anywhere on the page: a
control character (below U+0020, except the newline) and an orphaned escape fragment, the
rest of a command whose backslash and first letter a string escape ate (`\b`oldsymbol,
`\t`heta, `\f`rac{, `\v`arepsilon, `\r`ightarrow), not preceded by a backslash or a letter.

Problems carry the page and the kind; the formula itself goes only to the JSON output.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from gpdf.pipeline.manuals import write_atomic
from gpdf.pipeline.mathocr import latex as L

KINDS = ("display_delimiters", "inline_delimiters", "single_backslash_break", "no_row_breaks",
         "braces", "begin_end", "left_right", "control_character", "escape_fragment", "flags_json",
         "flags_missing", "duplicate_page")
MULTILINE = re.compile(r"aligned|align\*?|alignedat|eqnarray\*?|array|subarray|cases|"
                       r"gathered|gather\*?|split|multline\*?|[pbvBV]?matrix|smallmatrix")
ENV_RE = re.compile(r"\\(begin|end)\s*\{([A-Za-z*]+)\}")
DELIM_RE = re.compile(r"(\\+)([()])")
SPACING_BREAK_RE = re.compile(r"(\\+)\[\s*-?[\d.]+\s*(?:ex|em|pt|mm|cm|in|mu)\s*\]")
TRAILING_BS_RE = re.compile(r"(\\+)[ \t]*$")
CONTROL_RE = re.compile(r"[\x00-\x09\x0b-\x1f]")
# what is left of a command after a string escape (\a \b \f \n \r \t \v) took its backslash and
# first letter; each is checked not to follow a backslash, a letter or a control character
FRAGMENT_RE = re.compile(
    r"lpha|pprox|oldsymbol|egin\{|inom\{|ullet|rac\{|orall|abla|onumber|otin|ightarrow|"
    r"ight[)\]|.\\{]|heta|imes|ilde\{|ext\{|extbf|extrm|riangle|arepsilon|arphi|ec\{|"
    r"ath(?:bf|rm|cal|bb|it|sf)\{")


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


def _context(md: str, i: int, j: int) -> str:
    return repr(md[max(0, i - 20):j + 20])


def _characters(md: str) -> list[tuple[str, str]]:
    out = [("control_character", f"U+{ord(m.group()):04X} in {_context(md, m.start(), m.end())}")
           for m in CONTROL_RE.finditer(md)]
    for m in FRAGMENT_RE.finditer(md):
        before = md[m.start() - 1] if m.start() else " "
        if before == "\\" or before.isalpha() or CONTROL_RE.match(before):
            continue
        out.append(("escape_fragment", f"{m.group()!r} in {_context(md, m.start(), m.end())}"))
    return out


def check_md(md: str) -> list[tuple[str, str]]:
    """(kind, the formula or a note) for every structural problem of one page."""
    out = _characters(md)
    md = L.COMMENT_RE.sub("", md)
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
        from gpdf.pipeline.mathocr.score import MARKER_RE, page_stem

        text = folder.read_text("utf-8")
        marks = list(MARKER_RE.finditer(text))
        if not marks:
            raise CheckError(f"no page markers in {folder}")
        problems, lines = [], {}
        for k, m in enumerate(marks):       # every marker is a page: a repeated key is reported, not merged
            stem = page_stem(m)
            lines.setdefault(stem, []).append(text.count("\n", 0, m.start()) + 1)
            end = marks[k + 1].start() if k + 1 < len(marks) else len(text)
            problems += [{"page": stem, "kind": kind, "detail": detail}
                         for kind, detail in check_md(text[m.start():end].rstrip("\n") + "\n")]
        for stem, at in lines.items():
            if len(at) > 1:
                problems.append({"page": stem, "kind": "duplicate_page",
                                 "detail": "markers at lines " + ", ".join(map(str, at[:-1])) + f" and {at[-1]}"})
        return _result(folder, len(marks), problems)
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
