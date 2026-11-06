# SPDX-License-Identifier: AGPL-3.0-or-later
"""gpdf-cli math-crosscheck on hand-written pages of our own formulas, with a fake SciPy
reference (tests/pipeline/fake_scipy_ref.py): located by tag, display or inline pattern,
harvested by their left-hand sides, compared at three points through the book's own
parametrisation; failures, inconclusive entries, the per-item limit, formulas.jsonl."""

import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("sympy")
pytest.importorskip("antlr4")

from gpdf import cli  # noqa: E402
from gpdf.pipeline.mathocr import crosscheck as C  # noqa: E402

FAKE = Path(__file__).with_name("fake_scipy_ref.py")

P1 = ('<!-- page printed="10" file="1" -->\n\n# 1 Exponential\n\nThe density is\n\n$$\n'
      'f(x) = \\frac{1}{\\theta} e^{-x/\\theta}, \\quad x > 0\n\\tag{1.1}\n$$\n\nand\n\n$$\n'
      'F(x) = 1 - \\exp[-x/\\theta]\n\\tag{1.2}\n$$\n\nwith mean \\(E[X] = \\theta\\) and\n\n$$\n'
      '\\begin{aligned} \\mu_r^{\\prime} &= r! \\, \\theta^{r} \\\\ \\operatorname{var}(X) &= '
      '\\theta \\end{aligned}\n$$\n')
P2 = ('<!-- page printed="11" file="2" -->\n\n$$\nM(t) = (1 - \\theta t)^{-1}\n\\tag{1.3}\n$$\n\n'
      '$$\n\\beta_2 = 9\n$$\n\n$$\nf(x) = \\frac{1}{\\eta} e^{-x/\\eta}\n$$\n')
DISTS = {"expon": {"scipy": "expon", "args": {"scale": "theta"},
                   "points": [{"theta": 2.0, "@x": 1.3, "@t": 0.2},
                              {"theta": 0.5, "@x": 0.4, "@t": -1.0},
                              {"theta": 1.7, "@x": 3.1, "@t": 0.3}]}}


@pytest.fixture
def book(tmp_path):
    (tmp_path / "text.md").write_text(P1 + "\n" + P2, "utf-8")
    return tmp_path


def spec(book, **kw):
    s = {"pages": "text.md", "dists": DISTS,
         "entries": [{"id": "pdf (1.1)", "where": {"tag": "1.1"}, "qty": "pdf", "dist": "expon"},
                     {"id": "cdf (1.2)", "where": {"tag": "1.2"}, "qty": "cdf", "dist": "expon"},
                     {"id": "mean", "where": {"page": 1, "inline": r"E\[X\]"}, "eq": "rhs",
                      "qty": "mean", "dist": "expon"}],
         "scan": [{"pages": "1-2", "dist": "expon"}], **kw}
    p = book / "spec.json"
    p.write_text(json.dumps(s), "utf-8")
    return p


def run(book, **kw):
    ref = C.RefWorker(sys.executable, FAKE)
    try:
        return C.run(spec(book, **kw), book / "out", ref=ref)
    finally:
        ref.close()


def by_id(res):
    return {r["id"]: r for r in res["results"]}


def test_located_and_harvested_formulas_are_compared_at_three_points(book):
    res = run(book)
    r = by_id(res)
    assert r["pdf (1.1)"]["status"] == "passed" and r["pdf (1.1)"]["formula"] == "p001-d01"
    assert len(r["pdf (1.1)"]["points"]) == 3 and r["pdf (1.1)"]["printed"] == "10"
    assert r["cdf (1.2)"]["status"] == "passed"                 # \exp[...] read as exp(...)
    assert r["mean"]["status"] == "passed" and r["mean"]["formula"] == "p001-i01"
    # harvested: the moment row (r = 1, 2, 3), the printed variance error, M(t), beta_2
    harvested = {x["id"]: x for x in res["results"] if x["harvested"]}
    assert harvested["p001-d03#0 raw_moment"]["status"] == "passed"
    assert harvested["p001-d03#1 var"]["status"] == "failed"
    assert harvested["p002-d01#0 mgf"]["status"] == "passed"
    assert harvested["p002-d02#0 kurt"]["status"] == "passed"
    # tagged (1.1) and (1.2) were not harvested a second time
    assert not any(x["formula"] in ("p001-d01", "p001-d02") for x in harvested.values())
    # eta is not in the table: inconclusive, not failed
    eta = harvested["p002-d03#0 pdf"]
    assert eta["status"] == "inconclusive" and "eta" in eta["reason"]
    assert res["counts"] == {"passed": 6, "failed": 1, "inconclusive": 1, "run": 8}
    assert "theta" in (book / "out" / "REPORT.md").read_text("utf-8")


def test_identify_names_unlabelled_formulas_by_what_they_match(tmp_path):
    # a summary table whose row labels were lost: each formula is tried against what its
    # variable allows; one that matches nothing is left for the page image
    md = ('<!-- page printed="1" file="1" -->\n\nThe cdf \\(1 - e^{-x/\\theta}\\) and\n\n'
          '$$\n(1 - \\theta t)^{-1}, \\quad t < 1/\\theta\n$$\n\n$$\n\\theta^{2}\n$$\n\n'
          '$$\n\\theta^{3}\n$$\n')
    (tmp_path / "text.md").write_text(md, "utf-8")
    p = tmp_path / "spec.json"
    p.write_text(json.dumps({"pages": "text.md", "dists": DISTS,
                             "identify": [{"pages": "1", "dist": "expon"}]}), "utf-8")
    ref = C.RefWorker(sys.executable, FAKE)
    try:
        res = C.run(p, tmp_path / "out", ref=ref)
    finally:
        ref.close()
    got = {r["formula"]: r for r in res["results"]}
    assert (got["p001-i01"]["status"], got["p001-i01"]["qty"]) == ("passed", "cdf")
    assert (got["p001-d01"]["status"], got["p001-d01"]["qty"]) == ("passed", "mgf")
    assert got["p001-d01"]["id"] == "p001-d01r0 mgf"
    assert (got["p001-d02"]["status"], got["p001-d02"]["qty"]) == ("passed", "var")
    bad = got["p001-d03"]
    assert bad["status"] == "inconclusive" and bad["unidentified"] and bad["qty"] is None
    assert "matches none" in bad["reason"] and bad["closest"]["qty"] in C.SCALAR_CLASS
    assert "unidentified" in C.report(res)


def test_identify_knows_fixed_order_moments_and_the_coefficient_of_variation(tmp_path):
    md = ('<!-- page printed="1" file="1" -->\n\nRows \\(2\\theta^{2}\\) and \\(\\theta / \\theta\\)'
          ' and\n\n$$\n\\begin{array}{l} \\mu_{2}^{\\prime} = 2 \\theta^{2} \\\\ \\qquad + 0\n'
          '\\end{array}\n\\tag{9.1}\n$$\n')
    (tmp_path / "text.md").write_text(md, "utf-8")
    p = tmp_path / "spec.json"
    p.write_text(json.dumps({"pages": "text.md", "dists": DISTS,
                             "identify": [{"pages": "1", "dist": "expon", "inline": True}],
                             "entries": [{"id": "rows joined", "where": {"tag": "9.1"}, "qty": "raw_moment",
                                          "r": 2, "dist": "expon",
                                          "notation": [[r"\\qquad", " "], [r"\\\\", " "]]}]}), "utf-8")
    ref = C.RefWorker(sys.executable, FAKE)
    try:
        res = C.run(p, tmp_path / "out", ref=ref)
    finally:
        ref.close()
    got = {r["id"]: r for r in res["results"]}
    assert got["p001-i01 raw_moment@2"]["status"] == "passed"
    assert got["p001-i02 cv"]["status"] == "passed"
    # the rewrites go on before the piece is cut: the continuation row is joined, and no
    # \end{array} is left over in the right-hand side
    assert got["rows joined"]["status"] == "passed" and "array" not in got["rows joined"]["piece"]


def test_a_log_cf_is_compared_on_the_same_branch(tmp_path):
    md = '<!-- page printed="1" file="1" -->\n\n$$\ni t - \\frac{1}{2} t^{2}\n\\tag{2.1}\n$$\n'
    (tmp_path / "text.md").write_text(md, "utf-8")
    p = tmp_path / "spec.json"
    p.write_text(json.dumps({"pages": "text.md",
                             "dists": {"branch": {"scipy": "branch",
                                                  "points": [{"@t": 0.5}, {"@t": -1.2}, {"@t": 2.0}]}},
                             "entries": [{"id": "lcf", "where": {"tag": "2.1"}, "eq": "full",
                                          "qty": "log_cf", "dist": "branch"}]}), "utf-8")
    ref = C.RefWorker(sys.executable, FAKE)
    try:
        res = C.run(p, tmp_path / "out", ref=ref)
    finally:
        ref.close()
    assert res["results"][0]["status"] == "passed"


def test_a_parametrisation_is_a_table_fix_not_a_failure(book):
    # the same book read with the rate in the table: everything still agrees
    # (a book name that is a Python keyword is written with a trailing _ in "args")
    rate = {"expon": {**DISTS["expon"], "args": {"scale": "1 / lambda_"},
                      "points": [{"lambda": 0.5, "@x": 1.3, "@t": 0.2}, {"lambda": 2.0, "@x": 0.4,
                                 "@t": -1.0}, {"lambda": 1 / 1.7, "@x": 3.1, "@t": 0.3}]}}
    res = run(book, dists=rate, notation=[[r"\\theta", r"\\frac{1}{\\lambda}"]],
              entries=[{"id": "pdf", "where": {"tag": "1.1"}, "qty": "pdf", "dist": "expon"}],
              scan=[])
    assert by_id(res)["pdf"]["status"] == "passed"


def test_the_item_limit_and_the_reference_timeout_make_entries_inconclusive(book, monkeypatch):
    import time as _t
    monkeypatch.setattr(C, "ITEM_TIMEOUT", 0.5)

    def stuck(expr, values):
        while True:
            _t.sleep(0.01)
    monkeypatch.setattr(C, "evaluate", stuck)
    t = _t.monotonic()
    res = run(book, scan=[])
    assert all(r["status"] == "inconclusive" and "over" in r["reason"] for r in res["results"])
    assert _t.monotonic() - t < 15
    monkeypatch.undo()
    monkeypatch.setenv("FAKE_REF_SLEEP", "30")
    hang = {"hang": {"scipy": "hang", "points": [{"theta": 1.0, "@x": 1.0}] * 3}}
    ref = C.RefWorker(sys.executable, FAKE, timeout=1.0)
    try:
        r = C.check_entry({"id": "h", "where": {"tag": "1.1"}, "qty": "pdf", "dist": "hang"},
                          C.load_pages(book / "text.md"), {"dists": hang}, ref)
    finally:
        ref.close()
    assert r["status"] == "inconclusive" and "took over" in r["reason"]


def test_a_worker_without_scipy_answers_at_once_with_its_error(tmp_path):
    import time as _t
    dead = tmp_path / "dead.py"
    dead.write_text("import no_such_module_here\n", "utf-8")
    ref = C.RefWorker(sys.executable, dead, timeout=30)
    t = _t.monotonic()
    a = ref.ask({"dist": "expon", "args": {"scale": 1.0}, "qty": "pdf", "x": 1.0})
    ref.close()
    assert "ended" in a["error"] and "no_such_module_here" in a["error"]
    assert _t.monotonic() - t < 10


def test_mark_formulas_gives_crosschecked_and_keeps_flags(book, tmp_path):
    res = run(book)
    f = tmp_path / "formulas.jsonl"
    recs = [{"id": "p001-d01", "file": 1, "kind": "display", "status": "engines_agree"},
            {"id": "p001-d02", "file": 1, "kind": "display", "status": "reviewed_accept"},
            {"id": "p001-d03", "file": 1, "kind": "display", "status": "flagged"},
            {"id": "p001-i01", "file": 1, "kind": "inline", "status": "unreviewed"},
            {"id": "p002-d03", "file": 2, "kind": "display", "status": "engines_agree"}]
    f.write_text("".join(json.dumps(r) + "\n" for r in recs), "utf-8")
    meta = tmp_path / "meta.json"
    meta.write_text(json.dumps({"method": {"formulas_by_status": {}}}), "utf-8")
    c = C.mark_formulas(f, res["results"], meta)
    got = {json.loads(s)["id"]: json.loads(s) for s in f.read_text("utf-8").splitlines()}
    assert got["p001-d01"]["status"] == "crosschecked" and got["p001-d01"]["was"] == "engines_agree"
    assert got["p001-d02"]["status"] == "crosschecked"
    assert got["p001-d03"]["status"] == "flagged"                # one entry failed there too
    assert got["p001-d03"]["crosscheck"] == {"passed": 1, "failed": 1, "entries": [
        "p001-d03#0 raw_moment", "p001-d03#1 var"]}
    assert got["p001-i01"]["status"] == "crosschecked"
    assert got["p002-d03"]["status"] == "engines_agree"          # inconclusive: unchanged
    assert c == {"crosschecked": 3, "never_reviewed": 2,
                 "by_status": {"crosschecked": 3, "flagged": 1, "engines_agree": 1}}
    assert json.loads(meta.read_text("utf-8"))["method"]["formulas_by_status"] == c["by_status"]


def test_cli(book, monkeypatch, capsys):
    monkeypatch.setattr(C, "REF_SCRIPT", FAKE)
    assert cli.main(["math-crosscheck", "--spec", str(spec(book)), "--out", str(book / "o"),
                     "--scipy-python", sys.executable]) == 2      # a failure: exit code 2
    out = capsys.readouterr().out
    assert "8 entries: 6 passed, 1 failed, 1 inconclusive" in out and "theta" not in out
    assert json.loads((book / "o" / "crosscheck.json").read_text("utf-8"))["counts"]["run"] == 8


def test_real_scipy_when_configured(book):
    import os
    py = os.environ.get(C.ENV_VAR)
    if not py:
        pytest.skip("set GPDF_SCIPY_PYTHON to a Python with SciPy")
    res = C.run(spec(book), book / "out", python=py)
    assert res["counts"]["passed"] == 6 and res["counts"]["failed"] == 1
