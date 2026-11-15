# SPDX-License-Identifier: AGPL-3.0-or-later
r"""gpdf-cli math-crosscheck: the transcribed formulas of a book against SciPy.

A spec (JSON, kept in the run's private folder: it quotes the book's notation) says where
the formulas are and how the book parametrises each distribution:

    {"pages": "...\\text.md",               the final pages: a text.md, pages folder or export
     "tol": 1e-8,
     "notation": [["(?<![A-Za-z\\])Z\\s*\\(", "\\phi("]],   regex rewrites before parsing
     "defs": {"q": "1 - p"},                 constants the book defines once
     "dists": {"gamma": {"scipy": "gamma",   the explicit parametrisation table: SciPy's
                         "args": {"a": "alpha", "scale": "beta", "loc": "gamma"},
                         "points": [{"alpha": 2.5, "beta": 1.5, "gamma": 0, "@x": 2.2,
                                     "@t": 0.2}, {...}, {...}]}},
     "entries": [{"id": "gamma pdf (17.1)", "where": {"tag": "17.1"}, "eq": "rhs",
                  "qty": "pdf", "dist": "gamma", "var": "x"}],
     "scan": [{"pages": "357-380", "dist": "gamma"}]}

``args`` are Python expressions in the book's parameter names (``"mu / lam"``), so a
parametrisation mismatch is a fix to this table, never a failure. Each point gives the
book's parameters and the argument of each quantity: ``@x`` (pdf, pmf, cdf, sf, hazard),
``@t`` (mgf, cgf, cf), ``@z`` (pgf), ``@p`` (quantile); moments take r = 1, 2, 3 at the
three points unless the entry fixes ``r``. ``scan`` harvests every display row of those
pages whose left-hand side names a quantity (f(x), F(x), M(t), E[X], \mu'_r, \beta_2, ...).

Each entry is evaluated at every point (the book's formula through the identity checks'
LaTeX parser, mpmath; the reference by SciPy in an external Python, ``--scipy-python``,
crosscheck_ref.py) and compared at a relative tolerance of 1e-8, loosened only to the
reference's own quadrature error (recorded). ``inconclusive``: it cannot be evaluated (no
parse, symbols the table does not give, SciPy has no value). Every entry has a hard limit of
ITEM_TIMEOUT seconds. Results go to crosscheck.json and REPORT.md (formulas); the screen gets
counts. ``mark_formulas`` gives the formulas that passed status ``crosschecked`` in a
formulas.jsonl (the earlier status kept as ``was``; a flagged formula stays flagged).
"""

from __future__ import annotations

import json
import math
import os
import queue
import re
import subprocess
import sys
import threading
from pathlib import Path

from gpdf.pipeline.manuals import ManualsError, parse_pages, write_atomic
from gpdf.pipeline.mathocr import latex as L
from gpdf.pipeline.mathocr import watch

TOL = 1e-8
ITEM_TIMEOUT = watch.ITEM_TIMEOUT
REF_SCRIPT = Path(__file__).with_name("crosscheck_ref.py")
ENV_VAR = "GPDF_SCIPY_PYTHON"
ARG_OF = {"pdf": "@x", "pmf": "@x", "cdf": "@x", "sf": "@x", "hazard": "@x", "quantile": "@p",
          "mgf": "@t", "cgf": "@t", "cf": "@t", "pgf": "@z", "isf": "@a", "cum_hazard": "@x",
          "laplace": "@s", "log_cf": "@t"}
DEFAULT_ARGS = {"@p": 0.3, "@a": 0.2, "@s": 0.4, "@z": 0.6, "@t": 0.25}
MOMENTS = ("raw_moment", "central_moment", "cumulant")
SCALARS = ("mean", "var", "sd", "skew", "skew2", "kurt", "excess_kurt", "entropy", "mode",
           "median", "cv")
QUANTITIES = tuple(ARG_OF) + MOMENTS + SCALARS
CF_UNIT = "i"


class CrosscheckError(Exception):
    """The spec or the pages cannot be used; the message is for the user."""


# ---------------------------------------------------------------- the reference worker

class RefWorker:
    """crosscheck_ref.py in an external Python with SciPy, one JSON line per request; a
    request past its timeout kills the worker (a fresh one starts for the next)."""

    def __init__(self, python: str, script: Path | None = None, timeout: float | None = None):
        self.python = python
        self.script = Path(script or REF_SCRIPT)
        self.timeout = timeout or ITEM_TIMEOUT
        self.proc = None

    def _start(self):
        self.proc = subprocess.Popen([self.python, "-I", str(self.script)], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                     encoding="utf-8", bufsize=1)
        self.lines: queue.Queue = queue.Queue()
        self.err: list[str] = []
        out, q, err = self.proc.stdout, self.lines, self.proc.stderr

        def read():
            for s in out:
                q.put(s)
            q.put(None)                       # the worker ended
        threading.Thread(target=read, daemon=True).start()
        threading.Thread(target=lambda: [self.err.append(s) for s in err], daemon=True).start()

    def ask(self, req: dict) -> dict:
        """One answer, matched to its request by id: a late answer to an abandoned request
        is dropped, and any interruption (the entry's own time limit) resets the worker."""
        if self.proc is None or self.proc.poll() is not None:
            self._start()
        self.n = getattr(self, "n", 0) + 1
        try:
            self.proc.stdin.write(json.dumps({**req, "id": self.n}) + "\n")
            self.proc.stdin.flush()
            while True:
                line = self.lines.get(timeout=self.timeout)
                if line is None:
                    last = next((s.strip() for s in reversed(self.err) if s.strip()),
                                "no message")
                    self.close()
                    return {"error": f"the reference worker ended: {last}"}
                ans = json.loads(line)
                if ans.get("id", self.n) == self.n:
                    return ans
        except queue.Empty:
            self.close()
            return {"error": f"the reference took over {self.timeout:g} s"}
        except (OSError, ValueError) as e:
            self.close()
            return {"error": f"reference worker: {e}"}
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.proc is not None:
            try:
                self.proc.kill()
                self.proc.wait(10)
            except (OSError, subprocess.TimeoutExpired):
                pass
            self.proc = None


# ---------------------------------------------------------------- locating formulas

def load_pages(src) -> dict[int, str]:
    from gpdf.pipeline.mathocr.score import ScoreError, read_pages
    try:
        mds, _, _ = read_pages(src)
    except ScoreError as e:
        raise CrosscheckError(str(e)) from None
    # entries address file pages; pages from a supplementary source (a `src` marker) are left out
    return {int(k[1:]): v for k, v in mds.items() if "#" not in k}


def _displays(md: str) -> list[tuple[str, str | None]]:
    return [L.split_tag(body, trailing) for body, trailing in L.extract(md)[0]]


TABLE_HTML = re.compile(r"<table\b.*?</table>", re.S | re.I)
TABLE_MD = re.compile(r"(?:^[ \t]*\|.*\|[ \t]*(?:\n|$)){2,}", re.M)
CELL_MATH = re.compile(r"<eq>(.*?)</eq>|\\\((.*?)\\\)", re.S)
TAGS = re.compile(r"<[^>]+>")


def _html_rows(text: str) -> list[list[tuple[int, int, str]]]:
    """[(rowspan, colspan, inner text with <eq> kept)] per <tr>; entities unescaped."""
    from html.parser import HTMLParser

    class P(HTMLParser):
        def __init__(self):
            super().__init__()
            self.rows, self.cell = [], None

        def handle_starttag(self, tag, attrs):
            a = dict(attrs)
            if tag == "tr":
                self.rows.append([])
            elif tag in ("td", "th"):
                self.cell = [int(a.get("rowspan") or 1), int(a.get("colspan") or 1), ""]
            elif self.cell is not None:
                self.cell[2] += f"<{tag}>"

        def handle_endtag(self, tag):
            if tag in ("td", "th") and self.cell is not None and self.rows:
                self.rows[-1].append(tuple(self.cell))
                self.cell = None
            elif self.cell is not None:
                self.cell[2] += f"</{tag}>"

        def handle_data(self, d):
            if self.cell is not None:
                self.cell[2] += d
    p = P()
    p.feed(text)
    return p.rows


def tables(md: str) -> list[tuple[list[str], list[list[str]]]]:
    """The page's tables in order: (column headings, body rows of cell texts). An HTML table's
    rowspan / colspan fill every cell they cover; its heading rows are the leading rows without
    math, a column's heading their texts joined by " / ". A Markdown table's heading is its
    first row."""
    found = [(m.start(), "h", m.group(0)) for m in TABLE_HTML.finditer(md)]
    found += [(m.start(), "m", m.group(0)) for m in TABLE_MD.finditer(md)]
    out = []
    for _, kind, text in sorted(found):
        if kind == "h":
            grid, pending = [], {}
            for r, row in enumerate(_html_rows(text)):
                cells, c, it = [], 0, iter(row)
                while True:
                    while (r, c) in pending:
                        cells.append(pending.pop((r, c)))
                        c += 1
                    cell = next(it, None)
                    if cell is None:
                        break
                    rs, cs, txt = cell
                    for _ in range(cs):
                        for dr in range(1, rs):
                            pending[(r + dr, c)] = txt
                        cells.append(txt)
                        c += 1
                grid.append(cells)
            nh = 0
            while nh < len(grid) and not any(CELL_MATH.search(x) for x in grid[nh]):
                nh += 1
            heads, body = grid[:nh], grid[nh:]
        else:
            lines = [ln.strip().strip("|") for ln in text.strip().splitlines()]
            grid = [[x.strip() for x in ln.split("|")] for ln in lines
                    if not re.fullmatch(r"[\s|:\-]*", ln)]
            heads, body = grid[:1], grid[1:]
        ncol = max((len(r) for r in grid), default=0)
        names = []
        for c in range(ncol):
            parts = []
            for h in heads:
                t = " ".join(TAGS.sub(" ", h[c]).split()) if c < len(h) else ""
                if t and t not in parts:
                    parts.append(t)
            names.append(" / ".join(parts))
        out.append((names, body))
    return out


def _table_cell(pages, where):
    f = int(where["page"])
    ts = tables(pages.get(f, ""))
    ti = int(where["table"])
    if not 1 <= ti <= len(ts):
        raise CrosscheckError(f"page {f} has {len(ts)} tables, not {ti}")
    heads, body = ts[ti - 1]
    want = where.get("row")
    if isinstance(want, int):
        ri = want - 1 if 1 <= want <= len(body) else None
    else:
        def first(r):
            return " ".join(TAGS.sub(" ", r[0]).split()).lower() if r else ""
        w = str(want).strip().lower()
        ri = next((i for i, r in enumerate(body) if first(r) == w), None)
        if ri is None:
            ri = next((i for i, r in enumerate(body) if first(r).startswith(w)), None)
    if ri is None:
        raise CrosscheckError(f"table {ti} on page {f} has no row {want!r}")
    col = where.get("col")
    if isinstance(col, int):
        ci = col - 1 if 1 <= col <= len(heads) else None
    else:
        ci = next((i for i, h in enumerate(heads) if str(col).lower() in h.lower()), None)
    if ci is None or ci >= len(body[ri]):
        raise CrosscheckError(f"table {ti} on page {f} has no column {col!r} (headings: {heads})")
    cell = body[ri][ci]
    m = CELL_MATH.search(cell)
    latex = (m.group(1) if m.group(1) is not None else m.group(2)) if m else TAGS.sub(" ", cell).strip()
    return f, f"t{ti}r{ri + 1}c", ci + 1, latex


def table_entries(specs: list[dict]) -> list[dict]:
    """A spec's ``tables``: one entry per named row of a table column, the row's cell under the
    heading ``col`` checked as ``qty`` of the row's distribution."""
    out = []
    for t in specs:
        rest = {k: v for k, v in t.items() if k not in ("page", "table", "col", "qty", "rows")}
        for row, dist in t["rows"].items():
            out.append({"id": f"table p{t['page']}.{t['table']} {row}: {t['col']}",
                        "where": {"page": t["page"], "table": t["table"], "row": row, "col": t["col"]},
                        "qty": t["qty"], "dist": dist, **rest})
    return out


def locate(pages: dict[int, str], where: dict) -> tuple[int, str, int, str | None]:
    """(file, kind 'd' or 'i' (or 'tTrRc' for a table cell), 1-based position, LaTeX) of a
    spec's ``where``."""
    if "table" in where:
        return _table_cell(pages, where)
    if "tag" in where:
        want = re.sub(r"\s", "", str(where["tag"]))
        files = [int(where["page"])] if "page" in where else sorted(pages)
        for f in files:
            for k, (body, tag) in enumerate(_displays(pages.get(f, "")), 1):
                if tag == want:
                    return f, "d", k, body
        raise CrosscheckError(f"no display tagged ({want})")
    f = int(where["page"])
    if f not in pages:
        raise CrosscheckError(f"page {f} not in the pages")
    if "display" in where:
        ds = _displays(pages[f])
        k = int(where["display"])
        if not 1 <= k <= len(ds):
            raise CrosscheckError(f"page {f} has {len(ds)} displays, not {k}")
        return f, "d", k, ds[k - 1][0]
    if "inline_n" in where:
        spans = L.extract(pages[f])[1]
        k = int(where["inline_n"])
        if not 1 <= k <= len(spans):
            raise CrosscheckError(f"page {f} has {len(spans)} inline spans, not {k}")
        return f, "i", k, spans[k - 1]
    if "inline" in where:
        spans = L.extract(pages[f])[1]
        for k, s in enumerate(spans, 1):
            if re.search(where["inline"], s):
                return f, "i", k, s
        raise CrosscheckError(f"no inline span on page {f} matches {where['inline']!r}")
    raise CrosscheckError("where needs a tag, a display or an inline pattern")


ROW_SPLIT = re.compile(r"\\\\(?:\[[^\]]*\])?")


def rows(latex: str) -> list[str]:
    from gpdf.pipeline.mathocr import identities as I
    body = I.ALIGN_ENV.sub(" ", latex)
    return [r.replace("&", " ").strip() for r in ROW_SPLIT.split(body) if r.strip()]


def piece(latex: str, row=None, eq=None) -> str:
    """The part of a display an entry checks: a row, then the right-hand side (``eq``
    "rhs": after the last top-level "="), equation ``eq`` (identities.equations), or the
    whole (side conditions dropped)."""
    return cut(latex, row, eq)[0]


def cut(latex: str, row=None, eq=None) -> tuple[str, str]:
    """(piece, its side condition): the condition is what followed the piece on its row
    after a comma, \\quad or a word ("" if nothing)."""
    from gpdf.pipeline.mathocr import identities as I
    s = rows(latex)[int(row)] if row is not None else latex
    if isinstance(eq, int):
        eqs = I.equations(s, conditions=True)
        if not 0 <= eq < len(eqs):
            raise CrosscheckError(f"equation {eq} not found ({len(eqs)} in the piece)")
        return eqs[eq][1], eqs[eq][2]
    if eq in ("rhs", None, "full"):      # a whole display: no alignment left over
        s = I._clean(I._pre(I.ALIGN_ENV.sub(" ", s).replace("&", " ")))
    if eq == "rhs":
        s = I._split_top(s, ["="])[-1]
    rest = [p.strip() for p in I._split_top(s, I.SEPARATORS)[1:] if p.strip()]
    return I._strip_conditions(s), " , ".join(rest)


# ---------------------------------------------------------------- branches and conditions

PIECE_OPEN = re.compile(r"\\left\s*\\\{\s*\\begin\{(array|aligned)\}(?:\{[^{}]*\})?"
                        r"|\\begin\{(cases)\}")
PIECE_TAIL = re.compile(r"\s*(?:\\right\s*\.)?\s*[.,;]?\s*")
COND_START = re.compile(r",|\\q?quad(?![A-Za-z])|\\(?:text|mbox|textrm)\s*\{")
CONTINUES = re.compile(r"\s*(?:\\q?quad\s*)*(?:\\times|\\cdot|[+\-])")
TEXT_CMD = re.compile(r"\\(?:text|mbox|textrm|mathrm)\s*\{([^{}]*)\}")
COND_WORDS = re.compile(r"(?<![A-Za-z\\])(?:if|for|when|where|and|with)(?![A-Za-z])")
ELSE_WORDS = re.compile(r"(?:otherwise|elsewhere|else)", re.I)
SUPPORT_LIST = re.compile(r"(.+?)\s*=\s*(-?\d+)\s*,(?:\s*-?\d+\s*,)*\s*(?:\\[lc]?dots|\\dotsc|\.\.\.)"
                          r"\s*(?:,\s*(.+?))?\s*,?")
REL_TOKEN = re.compile(r"(\\(?:leqslant|geqslant|leq|geq|neq|le|ge|ne|lt|gt)(?![A-Za-z])|<|>|=)")
_EPS = 1e-12
REL_OK = {"<": lambda a, b: a < b, ">": lambda a, b: a > b,
          "\\lt": lambda a, b: a < b, "\\gt": lambda a, b: a > b,
          "=": lambda a, b: abs(a - b) <= _EPS * (1 + abs(a)),
          "\\ne": lambda a, b: abs(a - b) > _EPS * (1 + abs(a))}
for _k in ("\\le", "\\leq", "\\leqslant"):
    REL_OK[_k] = lambda a, b: a <= b + _EPS * (1 + abs(b))
for _k in ("\\ge", "\\geq", "\\geqslant"):
    REL_OK[_k] = lambda a, b: a >= b - _EPS * (1 + abs(b))
REL_OK["\\neq"] = REL_OK["\\ne"]


def _first_top(s: str, rx) -> int | None:
    """Where rx first matches at brace/bracket depth 0."""
    depth = 0
    for i, c in enumerate(s):
        if c in "{([":
            depth += 1
        elif c in "})]":
            depth -= 1
        elif depth == 0 and rx.match(s, i):
            return i
    return None


def piecewise(latex: str) -> tuple[str, list[tuple[str, str]]] | None:
    """(lhs, [(value, condition)]) of a display ``lhs = {value & condition \\\\ ...}`` (a cases
    environment, or a left brace and an array or aligned), or None. A row without ``&``
    takes its condition after a comma, \\quad or a word; a row that starts with an operator
    continues the previous value when that one had no condition."""
    from gpdf.pipeline.mathocr import identities as I
    body, _ = L.split_tag(latex)
    m = PIECE_OPEN.search(body)
    if not m:
        return None
    name = m.group(1) or m.group(2)
    depth, end = 1, None
    for t in re.finditer(r"\\(begin|end)\{" + name + r"\}", body[m.end():]):
        depth += 1 if t.group(1) == "begin" else -1
        if depth == 0:
            end = (m.end() + t.start(), m.end() + t.end())
            break
    if end is None or not PIECE_TAIL.fullmatch(body[end[1]:]):
        return None
    head = I._split_top(body[:m.start()], ["="])
    if len(head) != 2 or not head[0].strip() or head[1].strip():
        return None
    out: list[tuple[str, str]] = []
    for row in ROW_SPLIT.split(body[m.end():end[0]]):
        if not row.strip():
            continue
        cells = I._split_top(row, ["&"])
        value, cond = cells[0], " ".join(cells[1:]).strip()
        if not cond:
            i = _first_top(value, COND_START)
            if i:
                value, cond = value[:i], value[i:]
        value = value.strip().rstrip(",.;").strip()
        if out and not out[-1][1] and CONTINUES.match(value):
            out[-1] = (out[-1][0] + " " + value, cond)
        elif value:
            out.append((value, cond))
    return (head[0].strip(), out) if len(out) >= 2 else None


def condition(text: str, defs=None):
    """A side condition read for evaluation: None (none), "else" (otherwise), "unknown"
    (cannot be read: kept as no condition), or a list of tests, each ("chain", sides,
    relations) or ("parity", expr, "even" | "odd"), all of which must hold."""
    s = TEXT_CMD.sub(r" \1 ", text or "")
    from gpdf.pipeline.mathocr import identities as I
    s = COND_WORDS.sub(" , ", I._clean(s)).strip().strip(".,;").strip()
    if s.startswith("(") and s.endswith(")"):
        s = s[1:-1].strip()
    if not s:
        return None
    if ELSE_WORDS.fullmatch(s):
        return "else"
    m = SUPPORT_LIST.fullmatch(s)
    if m:                                  # x = 0, 1, ..., n: integers from 0 (to n)
        var = book_expr(m.group(1), (), defs)
        hi = book_expr(m.group(3), (), defs) if m.group(3) else None
        if var is None or (m.group(3) and hi is None):
            return "unknown"
        return [("support", var, int(m.group(2)), hi)]
    tests = []
    for c in I._split_top(s, [",", ";"]):
        c = c.strip().strip("()").strip()
        if not c:
            continue
        m = re.fullmatch(r"(.+?)\s+(?:is\s+)?(even|odd)", c)
        if m:
            ex = book_expr(m.group(1), (), defs)
            if ex is None:
                return "unknown"
            tests.append(("parity", ex, m.group(2)))
            continue
        parts = REL_TOKEN.split(c)
        sides, rels = parts[0::2], parts[1::2]
        if not rels:
            return "unknown"
        exprs = []
        for sd in sides:
            sd = sd.strip()
            if sd.lstrip("+-").strip() == "\\infty":
                exprs.append(-math.inf if sd.startswith("-") else math.inf)
                continue
            ex = book_expr(sd, (), defs) if sd else None
            if ex is None:
                return "unknown"
            exprs.append(ex)
        tests.append(("chain", exprs, rels))
    return tests or None


def holds(tests, values: dict) -> bool | None:
    """Whether every test holds at these values; None if one cannot be evaluated (and none
    fails)."""
    unknown = False
    for t in tests:
        if t[0] == "support":
            v, _ = evaluate(t[1], values)
            top = evaluate(t[3], values)[0] if t[3] is not None else None
            if v is None or (t[3] is not None and top is None):
                unknown = True
                continue
            if abs(v.real - round(v.real)) > 1e-9 or v.real < t[2] - 1e-9 or (
                    top is not None and v.real > top.real + 1e-9):
                return False
            continue
        if t[0] == "parity":
            v, _ = evaluate(t[1], values)
            if v is None:
                unknown = True
                continue
            if abs(v.real - round(v.real)) > 1e-9 or (round(v.real) % 2 == 0) != (t[2] == "even"):
                return False
            continue
        vals = []
        for ex in t[1]:
            if isinstance(ex, float):
                vals.append(ex)
                continue
            v, _ = evaluate(ex, values)
            if v is None or abs(v.imag) > _EPS:
                vals = None
                break
            vals.append(v.real)
        if vals is None:
            unknown = True
            continue
        if not all(REL_OK[op](a, b) for a, op, b in zip(vals, t[2], vals[1:])):
            return False
    return None if unknown else True


def pick(branches: list[dict], values: dict) -> int | None:
    """The branch to evaluate at these values: the first whose condition holds; else the
    "otherwise" branch when every other condition fails; else the one branch whose condition
    cannot be read (or that has none) when every other fails; else None (outside)."""
    states = []
    for b in branches:
        t = b["tests"]
        states.append(None if t in (None, "unknown") else "else" if t == "else" else holds(t, values))
    for i, s in enumerate(states):
        if s is True:
            return i
    if "else" in states and all(s is False or s == "else" for s in states):
        return states.index("else")
    rest = [i for i, s in enumerate(states) if s is None]
    if len(rest) == 1 and all(s is False or s is None for s in states):
        return rest[0]
    return None


# ---------------------------------------------------------------- the book's value

def _brackets(s: str) -> str:
    r"""\Gamma[...] and \exp[...]: square brackets as the call's parentheses."""
    out, i = [], 0
    for m in re.finditer(r"\\(Gamma|exp|log)\s*(\\[bB]ig[lr]?\s*|\\left\s*)?\[", s):
        if m.start() < i:
            continue
        depth, j = 1, m.end()
        while j < len(s) and depth:
            depth += {"[": 1, "]": -1}.get(s[j], 0)
            j += 1
        inner = re.sub(r"\\([bB]ig[lr]?|right)\s*$", "", s[m.end():j - 1])
        out.append(s[i:m.start()] + "\\" + m.group(1) + " (" + inner + ")")
        i = j
    return "".join(out) + s[i:]


def book_expr(text: str, notation=(), defs=None):
    """A sympy expression of a formula piece, or None."""
    from gpdf.pipeline.mathocr import identities as I
    s = text
    for rx, rep in notation:
        s = re.sub(rx, rep, s)
    s = re.sub(r"\\ln\b", r"\\log", _brackets(I._clean(s)))
    return I._parse(s, defs)


def evaluate(expr, values: dict):
    """complex value of expr with the named values put in; (None, reason) if it can't."""
    import sympy
    from gpdf.pipeline.mathocr import identities as I
    names = {}
    for sym in expr.free_symbols:
        names.setdefault(I._canon(sym.name), []).append(sym)
    missing = sorted(n for n in names if n not in values)
    if missing:
        return None, "symbols the table does not give: " + ", ".join(missing)
    sub = {sym: sympy.nsimplify(values[n]) if isinstance(values[n], int) else values[n]
           for n, syms in names.items() for sym in syms}
    try:
        v = complex(sympy.N(expr.xreplace(sub), 25))      # xreplace: see identities._parse
    except (TypeError, ValueError, ZeroDivisionError, OverflowError) as e:
        return None, f"not a number ({type(e).__name__})"
    if math.isnan(v.real) or math.isnan(v.imag) or math.isinf(abs(v)):
        return None, "not a finite number"
    return v, None


# ---------------------------------------------------------------- harvesting

_P = r"(?:\^\{?\\prime\}?|')"
MOMENT_LHS = [
    (re.compile(r"^\\mu\s*(?:" + _P + r"\s*_\s*\{?\s*(\w)\s*\}?|_\s*\{?\s*(\w)\s*\}?\s*" + _P + r")"
                r"\s*(?:\(\s*X\s*\))?$"), "raw_moment"),
    (re.compile(r"^E\s*[\[(]\s*X\s*\^\s*\{?\s*(\w)\s*\}?\s*[\])]$"), "raw_moment"),
    (re.compile(r"^\\mu\s*_\s*\{?\s*(\w)\s*\}?\s*(?:\(\s*X\s*\))?$"), "central_moment"),
    (re.compile(r"^\\kappa\s*_\s*\{?\s*(\w)\s*\}?\s*(?:\(\s*X\s*\))?$"), "cumulant"),
]
SCALAR_LHS = [
    (re.compile(r"^\\sqrt\s*\{\s*\\beta\s*_\s*\{?1\}?\s*\}\s*(?:\(\s*X\s*\))?$"), "skew"),
    (re.compile(r"^\\beta\s*_\s*\{?1\}?\s*(?:\(\s*X\s*\))?$"), "skew2"),
    (re.compile(r"^\\beta\s*_\s*\{?2\}?\s*(?:\(\s*X\s*\))?$"), "kurt"),
    (re.compile(r"^\\alpha\s*_\s*\{?3\}?\s*\(\s*X\s*\)$"), "skew"),
    (re.compile(r"^\\alpha\s*_\s*\{?4\}?\s*\(\s*X\s*\)$"), "kurt"),
    (re.compile(r"^\\sigma\s*\(\s*X\s*\)$"), "sd"),
    (re.compile(r"^\\sigma\s*\^\s*\{?2\}?\s*\(\s*X\s*\)$"), "var"),
    (re.compile(r"^H\s*\(\s*X\s*\)$"), "entropy"),
    (re.compile(r"^(?:\\operatorname\{median\}|\\mathrm\{median\}|\\text\{median\})"
                r"\s*(?:\(\s*X\s*\))?$", re.I), "median"),
    (re.compile(r"^(?:\\operatorname\{mode\}|\\mathrm\{mode\}|\\text\{mode\})"
                r"\s*(?:\(\s*X\s*\))?$", re.I), "mode"),
]
FROM_IDENTITY = {"density": "pdf", "cdf": "cdf", "survival": "sf", "hazard": "hazard",
                 "mgf": "mgf", "cf": "cf", "pgf": "pgf", "mean": "mean", "variance": "var",
                 "pmf": "pmf"}
PMF_LHS = re.compile(r"^(?:\\Pr|P)\s*[\[(]\s*X\s*=\s*([a-z])\s*[\])]$")


def quantity(lhs: str, before: str = "") -> tuple[str | None, str | None, int | str | None]:
    """(quantity, variable, moment order) a left-hand side names, or (None, None, None)."""
    from gpdf.pipeline.mathocr import identities as I
    s = I._clean(lhs).strip()
    m = PMF_LHS.match(s)
    if m:
        return "pmf", m.group(1), None
    for rx, q in MOMENT_LHS:
        m = rx.match(s)
        if m:
            r = next(g for g in m.groups() if g)
            if q == "central_moment" and r == "1":
                return None, None, None
            return q, None, int(r) if r.isdigit() else r
    for rx, q in SCALAR_LHS:
        if rx.match(s):
            return q, None, None
    kind, variables, _ = I.classify(lhs, before)
    q = FROM_IDENTITY.get(kind or "")
    if q is None or len(variables) > 1:
        return None, None, None
    if q == "mgf" and re.search(r"\\log|\\ln", s):
        return None, None, None                      # E[e^{t log X}]: a Mellin transform
    if q == "mgf" and re.search(r"e\s*\^\s*\{?\s*\(?\s*i\s*t", s):
        q = "cf"                                     # E[e^{itX}]
    return q, (variables[0] if variables else None), None


def harvest(pages: dict[int, str], scan: list[dict], dists: dict) -> list[dict]:
    from gpdf.pipeline.mathocr import identities as I
    out = []
    for sc in scan:
        try:
            files = {i + 1 for i in parse_pages(sc["pages"], 10 ** 6)}
        except ManualsError as e:
            raise CrosscheckError(str(e)) from None
        dist = sc["dist"]
        discrete = bool(dists.get(dist, {}).get("discrete"))
        for f in sorted(files & set(pages)):
            md = pages[f]
            for k, (body, tag) in enumerate(_displays(md), 1):
                pw = piecewise(body)
                eqs = [(pw[0], None)] if pw else I.equations(body)
                for i, (lhs, rhs) in enumerate(eqs):
                    q, var, r = quantity(lhs)
                    if q is None:
                        continue
                    if q == "pdf" and discrete:
                        q = "pmf"
                    e = {"id": f"p{f:03d}-d{k:02d}#{i} {q}", "where": {"page": f, "display": k},
                         "eq": "piecewise" if pw else i, "qty": q, "dist": dist,
                         "harvested": True}
                    if var:
                        e["var"] = var
                    if isinstance(r, int):
                        e["r"] = r
                    elif r:
                        e["r_symbol"] = r
                    out.append(e)
    return out


# ---------------------------------------------------------------- one entry

def _eval_all(exprs: dict, point: dict) -> dict:
    """{name: value} of Python expressions in the book's parameter names; a name that is a
    Python keyword (lambda) is written with a trailing underscore (lambda_)."""
    import keyword
    params = {k: v for k, v in point.items() if not k.startswith("@")}
    env = {**params, **{k + "_": v for k, v in params.items() if keyword.iskeyword(k)},
           "math": math, "sqrt": math.sqrt, "exp": math.exp, "log": math.log, "pi": math.pi,
           "gamma": math.gamma, "round": round, "abs": abs} | params
    return {k: (eval(v, {"__builtins__": {}}, env) if isinstance(v, str) else v)  # noqa: S307
            for k, v in exprs.items()}


def _args(dist: dict, point: dict) -> dict:
    """SciPy's arguments from the book's parameters."""
    return _eval_all(dist.get("args", {}), point)


def _close(b: complex, s: complex, tol: float) -> tuple[bool, float]:
    d = abs(b - s)
    if abs(s) < 1e-12:
        return d <= tol, d
    rel = d / abs(s)
    return rel <= tol, rel


def check_entry(e: dict, pages: dict[int, str], spec: dict, ref) -> dict:
    """One entry; an ``identify`` entry (no quantity named) is tried against every quantity
    its free variable allows, and passes under the first that matches. A multivariate table
    row (one with ``k``) goes to crosscheck_mv."""
    row = spec.get("dists", {}).get(e.get("dist")) or {}
    if row.get("k") or row.get("matrix") or row.get("matrices") or row.get("vectors"):
        from gpdf.pipeline.mathocr import crosscheck_mv
        return crosscheck_mv.check(e, pages, spec, ref)
    res = {"id": e.get("id"), "qty": e.get("qty"), "dist": e["dist"],
           "harvested": bool(e.get("harvested")), "status": "inconclusive", "points": []}
    try:
        with watch.hard_timeout(ITEM_TIMEOUT):
            prep = _prepare(e, pages, spec, res)
    except watch.ItemTimeout:
        res["reason"] = f"over {ITEM_TIMEOUT:g} s (parsing)"
        return res
    if prep is None:
        return res
    expr, dist = prep                      # the branches (one, for a formula without cases)
    cands = [e["qty"]] if e.get("qty") else _candidates(expr, e, dist)
    if not cands:
        res["reason"] = res.get("reason") or "no quantity fits its variables"
        return res
    tried = []
    for q in cands:
        sub = {"status": "inconclusive", "points": []}
        try:
            with watch.hard_timeout(ITEM_TIMEOUT):
                _compare(expr, e, q, dist, spec, ref, sub)
        except watch.ItemTimeout:
            sub["reason"] = f"over {ITEM_TIMEOUT:g} s"
        tried.append((q, sub))
        if sub["status"] == "passed":
            break
    if e.get("qty"):
        res.update(tried[0][1])
        return res
    res["identify"] = True
    passed = [(q, s) for q, s in tried if s["status"] == "passed"]
    if passed:
        res.update(passed[0][1])
        res["qty"] = passed[0][0]
        return res
    failed = [(q, s) for q, s in tried if s["status"] == "failed"]
    res["qty"] = None
    res["status"] = "inconclusive"
    res["unidentified"] = True
    res["reason"] = "matches none of " + ", ".join(cands)
    if failed:
        q, s = min(failed, key=lambda x: x[1].get("worst", 1e300))
        res["closest"] = {"qty": q, "worst": s.get("worst"), "points": s["points"]}
    return res


def _prepare(e, pages, spec, res):
    """Locate, cut and parse the entry's formula: (sympy expression, table row) or None."""
    try:
        f, kind, k, latex = locate(pages, e["where"])
    except CrosscheckError as ex:
        res["reason"] = str(ex)
        return None
    m = re.search(r'<!--\s*page\s+printed="([^"]*)"', pages[f])
    res.update(file=f, printed=m.group(1) if m else None, formula=f"p{f:03d}-{kind}{k:02d}",
               latex=latex)
    if e.get("qty") and e["qty"] not in QUANTITIES:
        res["reason"] = f"unknown quantity {e['qty']}"
        return None
    dist = spec["dists"].get(e["dist"])
    if dist is None:
        res["reason"] = f"distribution {e['dist']} not in the table"
        return None
    # the rewrites go on the raw LaTeX, before the piece is cut (an entry may join rows)
    src = latex
    for rx, rep in spec.get("notation", []) + e.get("notation", []):
        src = re.sub(rx, rep, src)
    eq = e.get("eq", "rhs" if kind == "d" else "full")
    pw = piecewise(src) if kind == "d" and e.get("row") is None and eq in (
        "rhs", "full", "piecewise") else None
    if eq == "piecewise" and pw is None:
        res["reason"] = "not a piecewise display"
        return None
    try:
        parts = [(piece(v, None, "full"), c) for v, c in pw[1]] if pw else \
            [cut(src, e.get("row"), eq)]
    except (CrosscheckError, IndexError) as ex:
        res["reason"] = str(ex)
        return None
    res["piece"] = " ; ".join(t for t, _ in parts)
    defs = {**spec.get("defs", {}), **e.get("defs", {})}
    branches = []
    for text, cond in parts:
        expr = book_expr(text, (), defs)
        if expr is None and not pw:
            res["reason"] = "the formula does not parse"
            return None
        branches.append({"expr": expr, "tests": condition(cond, defs), "piece": text,
                         "condition": cond, "points": 0})
    if all(b["expr"] is None for b in branches):
        res["reason"] = "the formula does not parse"
        return None
    return branches, dist


VAR_CLASSES = {   # the quantities a formula in this free variable may be (identify mode)
    "x": ["pdf", "cdf", "sf", "hazard", "cum_hazard"], "y": ["pdf", "cdf", "sf", "hazard"],
    "t": ["mgf", "cf", "log_cf", "cgf"], "s": ["laplace"], "r": ["raw_moment", "cumulant",
                                                                 "central_moment"],
    "alpha": ["isf", "quantile"], "p": ["quantile", "isf"], "u": ["quantile", "isf"]}
DISCRETE_CLASSES = {"x": ["pmf", "cdf", "sf", "hazard"], "y": ["pmf", "cdf", "sf"],
                    "k": ["pmf", "cdf", "sf"], "i": ["pmf", "cdf", "sf"],
                    "t": ["pgf", "mgf", "cf", "log_cf", "cgf"], "z": ["pgf"], "s": ["pgf"],
                    "r": ["raw_moment", "cumulant", "central_moment"]}
SCALAR_CLASS = ["mean", "var", "sd", "skew", "kurt", "excess_kurt", "mode", "median", "entropy",
                "cv", "raw_moment@2", "raw_moment@3", "raw_moment@4", "central_moment@3",
                "central_moment@4", "cumulant@3", "cumulant@4", "pdf"]       # pdf: a constant one


def _symbols(branches) -> set[str]:
    from gpdf.pipeline.mathocr import identities as I
    return {I._canon(s.name) for b in branches if b["expr"] is not None
            for s in b["expr"].free_symbols}


def _candidates(branches, e, dist) -> list[str]:
    known = set()
    for p in dist["points"]:
        known |= {k for k in p if not k.startswith("@")}
    known |= set(dist.get("derived", {})) | set(dist.get("standard", {})) | set(e.get("fixed", {}))
    free = sorted(_symbols(branches) - known)
    if not free:
        return list(SCALAR_CLASS)
    if len(free) > 1:
        return []
    classes = DISCRETE_CLASSES if dist.get("discrete") else VAR_CLASSES
    out = classes.get(free[0], [])
    e.setdefault("var", free[0])
    if free[0] == "r":
        e["r_symbol"] = "r"
    return out


def _cases(dist: dict, e: dict, q: str, branches: list[dict]):
    """(n, point) to evaluate: the table's points, then each "probe" argument (the dist's or
    the entry's, e.g. {"@x": [-0.7]}) at every point, for an entry whose branches carry a
    condition (a probe reaches a branch the points miss: x < 0 of a piecewise cdf)."""
    pts = list(enumerate(dist["points"]))
    key = e.get("arg", ARG_OF.get(q))
    probe = {**dist.get("probe", {}), **e.get("probe", {})}.get(key or "", [])
    if not any(b["tests"] not in (None, "unknown") for b in branches):
        probe = []
    return pts + [(n, {**p, key: v}) for v in probe for n, p in pts]


def _compare(branches, e, q, dist, spec, ref, res):
    """Compare the branches as quantity q at the table's points; fills res (status, points,
    ...). At each point only the branch whose condition holds is evaluated (``pick``); a point
    outside every condition is counted (``outside``), not compared. ``q@n`` is a moment of
    fixed order n (identify mode)."""
    if "@" in q:
        q, fixed_r = q.split("@")
        e = {**e, "r": int(fixed_r)}
    if q == "pdf" and dist.get("discrete"):
        q = "pmf"
    tol = float(e.get("tol", spec.get("tol", TOL)))
    worst, failed, why = 0.0, False, None
    for b in branches:
        b["points"] = 0
    present = _symbols(branches)
    # a standardized form (a density without sigma): the table's standard values
    standard = {} if e.get("no_standard") else {
        k2: v for k2, v in dist.get("standard", {}).items() if k2 not in present}
    if standard:
        res["standardized"] = standard
    outside = 0
    for n, point in _cases(dist, e, q, branches):
        point = {**DEFAULT_ARGS, **point, **standard, **e.get("fixed", {})}
        # derived parameters (theta = alpha * beta, phi = lambda / mu), after the standard values
        point.update(_eval_all(dist.get("derived", {}), point))
        values = {k2: v for k2, v in point.items() if not k2.startswith("@")}
        arg = None
        if q in ARG_OF:
            arg = point.get(e.get("arg", ARG_OF[q]))
            values[e.get("var") or ARG_OF[q][1:]] = arg
        r = None
        if q in MOMENTS:
            r = e.get("r") or [1, 2, 3][n % 3] + (1 if q == "central_moment" else 0)
            if isinstance(r, list):              # one order per point (even orders only, ...)
                r = r[n % len(r)]
            if e.get("r_symbol"):
                values[e["r_symbol"]] = r
        k = pick(branches, values)
        if k is None:
            outside += 1
            res["points"].append({"values": values, "outside": True})
            continue
        if branches[k]["expr"] is None:      # a branch that does not parse: not compared
            res["points"].append({"values": values, "branch": k, "book": None})
            continue
        expr = branches[k]["expr"]
        branches[k]["points"] += 1
        b, why = evaluate(expr, values)
        if b is None:
            res["points"].append({"values": values, "book": None})
            res["reason"] = why
            return res
        args = _args(dist, point)
        a = ref.ask({"dist": dist["scipy"], "args": args, "qty": "skew" if q == "skew2" else q,
                     "x": arg, "r": r, **({"expr": dist["expr"]} if dist.get("expr") else {})})
        if "error" in a:
            res["points"].append({"values": values, "book": [b.real, b.imag]})
            res["reason"] = "reference: " + a["error"]
            return res
        s = complex(a["value"], a.get("imag") or 0.0)
        if q == "skew2":
            s = s * s
        t = tol
        if a.get("tol_floor", 0) * 10 > t:
            t = a["tol_floor"] * 10
            res["tol_reason"] = f"loosened to {t:.1e}: {a['how']} error estimate"
        if not (math.isfinite(s.real) and math.isfinite(s.imag)):
            res["points"].append({"values": values, "book": [b.real, b.imag]})
            res["reason"] = "reference: not a finite number here"
            return res
        if q == "log_cf":            # a complex log is defined up to 2*pi*i: same branch first
            b = complex(b.real, b.imag - 2 * math.pi * round((b.imag - s.imag) / (2 * math.pi)))
        ok, err = _close(b, s, t)
        worst = max(worst, err)
        failed |= not ok
        res["points"].append({"values": values, "args": args, "book": [b.real, b.imag],
                              "ref": [s.real, s.imag], "err": err, "ok": ok, "tol": t,
                              **({"branch": k} if len(branches) > 1 else {})})
    if len(branches) > 1:
        res["branches"] = [{"piece": b["piece"], "condition": b["condition"], "points": b["points"],
                            **({} if b["expr"] is not None else {"parsed": False})}
                           for b in branches]
    elif branches[0]["condition"]:
        res["condition"] = branches[0]["condition"]
    if outside:
        res["outside"] = outside
    if not any(b["points"] for b in branches):
        res["reason"] = "no point inside its condition" if outside else "no point evaluated"
        return res
    res["status"] = "failed" if failed else "passed"
    res["worst"] = worst
    return res


def identify_entries(pages: dict[int, str], specs: list[dict]) -> list[dict]:
    """Every display row and every inline span with an operation in it, on the given pages:
    entries without a quantity, tried against what their variable allows (a book whose
    summary tables lost their row labels)."""
    out = []
    for sp in specs:
        try:
            files = {i + 1 for i in parse_pages(sp["pages"], 10 ** 6)}
        except ManualsError as ex:
            raise CrosscheckError(str(ex)) from None
        for f in sorted(files & set(pages)):
            md = pages[f]
            for k, (body, tag) in enumerate(_displays(md), 1):
                if piecewise(body):              # one entry, branch by branch
                    out.append({"id": f"p{f:03d}-d{k:02d}pw ?", "where": {"page": f, "display": k},
                                "eq": "piecewise", "dist": sp["dist"], "identify": True,
                                "harvested": True})
                    continue
                n_rows = len(rows(body))
                for rw in range(n_rows):
                    out.append({"id": f"p{f:03d}-d{k:02d}r{rw} ?", "where": {"page": f, "display": k},
                                "row": rw if n_rows > 1 else None, "eq": "rhs",
                                "dist": sp["dist"], "identify": True, "harvested": True})
            if sp.get("inline", True):
                for k, s in enumerate(L.extract(md)[1], 1):
                    if not INLINE_OPERATION.search(s) or INLINE_RELATION.search(s):
                        continue
                    out.append({"id": f"p{f:03d}-i{k:02d} ?", "where": {"page": f, "inline_n": k},
                                "eq": "rhs", "dist": sp["dist"], "identify": True,
                                "harvested": True})
    return out


INLINE_OPERATION = re.compile(r"\\frac|\\exp|\\Gamma|\\log|\\sqrt|\^|/|\\binom|[+\-]|\\cdot|\(")
INLINE_RELATION = re.compile(r"<|>|\\le|\\ge|\\in\b|\\to\b|\\sim|,|\\ne")


# ---------------------------------------------------------------- the run

def run(spec_path, out, python: str | None = None, only=None, ref=None) -> dict:
    spec_path = Path(spec_path)
    try:
        spec = json.loads(spec_path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise CrosscheckError(f"{spec_path}: {e}") from None
    src = Path(spec["pages"])
    if not src.is_absolute():
        src = spec_path.parent / src
    pages = load_pages(src)
    entries = list(spec.get("entries", [])) + table_entries(spec.get("tables", []))
    seen = set()                 # (file, kind, position, quantity) of the hand entries
    for e in entries:
        try:
            f, kind, k, _ = locate(pages, e["where"])
            seen.add((f, kind, k, e["qty"]))
        except (CrosscheckError, KeyError):
            pass                 # reported when the entry is checked
    covered = {(f, kind, k) for f, kind, k, _ in seen}
    for h in identify_entries(pages, spec.get("identify", [])):
        try:
            f, kind, k, _ = locate(pages, h["where"])
        except CrosscheckError:
            continue
        if (f, kind, k) not in covered and h["id"] not in set(spec.get("skip", [])):
            h.update(spec.get("overrides", {}).get(h["id"], {}))
            entries.append(h)
    skip = set(spec.get("skip", []))
    over = spec.get("overrides", {})          # harvested id -> fields to set (r, dist, fixed, ...)
    for h in harvest(pages, spec.get("scan", []), spec.get("dists", {})):
        key = (h["where"]["page"], "d", h["where"]["display"], h["qty"])
        if key not in seen and h["id"] not in skip:
            h.update(over.get(h["id"], {}))
            entries.append(h)
    if only:
        entries = [e for e in entries if e.get("id") in set(only)]
    own = ref is None
    if own:
        python = python or os.environ.get(ENV_VAR)
        if not python:
            raise CrosscheckError(f"no Python with SciPy: give --scipy-python or set ${ENV_VAR}")
        ref = RefWorker(python)
    try:
        results = [check_entry(e, pages, spec, ref) for e in entries]
        for r in results:
            if r.get("identify") and r.get("qty") and r["id"].endswith("?"):
                r["id"] = r["id"][:-1] + r["qty"]
    finally:
        if own:
            ref.close()
    counts = {s: sum(1 for r in results if r["status"] == s)
              for s in ("passed", "failed", "inconclusive")}
    counts["run"] = len(results)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    res = {"spec": str(spec_path), "pages": str(src), "counts": counts, "results": results}
    write_atomic(out / "crosscheck.json", json.dumps(res, ensure_ascii=False, indent=1) + "\n")
    write_atomic(out / "REPORT.md", report_md(res))
    return res


def report_md(res: dict) -> str:
    c = res["counts"]
    lines = ["# Cross-check against SciPy (private: quotes the book's formulas)", "",
             f"Spec `{res['spec']}`; {c['run']} entries: {c['passed']} passed, {c['failed']} "
             f"failed, {c['inconclusive']} inconclusive.", ""]
    for st in ("failed", "inconclusive", "passed"):
        rs = [r for r in res["results"] if r["status"] == st]
        if not rs:
            continue
        lines += [f"## {st} ({len(rs)})", ""]
        for r in rs:
            where = f"file {r.get('file')} (p. {r.get('printed')}) {r.get('formula', '')}"
            extra = (f" worst {r['worst']:.2e}" if "worst" in r else "") + (
                f"; {r['reason']}" if r.get("reason") else "") + (
                f"; {r['tol_reason']}" if r.get("tol_reason") else "")
            if r.get("closest"):
                extra += f"; closest {r['closest']['qty']} (worst {r['closest']['worst']:.2e})"
            lines.append(f"- **{r['id']}** [{r['qty']}, {r['dist']}] {where}:{extra}")
            if r.get("piece"):
                lines.append(f"  `{r['piece']}`")
        lines.append("")
    return "\n".join(lines)


def report(res: dict) -> str:
    c = res["counts"]
    by = {}
    for r in res["results"]:
        q = r["qty"] or ("unidentified" if r.get("unidentified") else "?")
        by.setdefault(q, [0, 0, 0])
        by[q][("passed", "failed", "inconclusive").index(r["status"])] += 1
    return "\n".join([f"{c['run']} entries: {c['passed']} passed, {c['failed']} failed, "
                      f"{c['inconclusive']} inconclusive"]
                     + [f"  {q}: {p} passed, {f} failed, {i} inconclusive"
                        for q, (p, f, i) in sorted(by.items())])


# ---------------------------------------------------------------- formulas.jsonl

def mark_formulas(formulas, results, meta=None) -> dict:
    """Status ``crosschecked`` for the formulas whose every entry passed (none failed);
    the earlier status is kept as ``was``, a flagged formula stays flagged. Returns counts,
    with how many of those had not been reviewed."""
    formulas = Path(formulas)
    recs = [json.loads(s) for s in formulas.read_text("utf-8").splitlines() if s.strip()]
    per: dict[str, list] = {}
    for r in results:
        if r.get("formula") and r["status"] in ("passed", "failed"):
            per.setdefault(r["formula"], []).append(r)
    marked = unreviewed = 0
    for rec in recs:
        rs = per.get(rec["id"])
        if not rs:
            continue
        rec["crosscheck"] = {"passed": sum(r["status"] == "passed" for r in rs),
                             "failed": sum(r["status"] == "failed" for r in rs),
                             "entries": [r["id"] for r in rs]}
        # a branch that got no point (unparsed, or outside every point) leaves it unchecked
        partial = any(b.get("points") == 0 for r in rs for b in r.get("branches") or [])
        if partial:
            rec["crosscheck"]["partial"] = True
        if all(r["status"] == "passed" for r in rs) and rec["status"] != "flagged" and not partial:
            if rec["status"] != "crosschecked":
                rec["was"] = rec["status"]
                rec["status"] = "crosschecked"
            marked += 1
            if rec.get("was") not in ("replaced", "reviewed_accept", "flagged"):
                unreviewed += 1
    write_atomic(formulas, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recs))
    by_status: dict[str, int] = {}
    for r in recs:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
    if meta and Path(meta).is_file():
        m = json.loads(Path(meta).read_text("utf-8"))
        m.setdefault("method", {})["formulas_by_status"] = by_status
        write_atomic(Path(meta), json.dumps(m, ensure_ascii=False, indent=1) + "\n")
    return {"crosschecked": marked, "never_reviewed": unreviewed, "by_status": by_status}


def _main_python() -> str:              # for tests: this interpreter
    return sys.executable
