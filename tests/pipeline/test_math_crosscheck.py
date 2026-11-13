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


PW = ('<!-- page printed="20" file="1" -->\n\n'
      # 1: a piecewise cdf (left brace + array), commas before the conditions
      '$$\nF(x) = \\left\\{ \\begin{array}{ll} 0, & x < 0, \\\\ 1 - e^{-x/\\theta}, & x \\geq 0, '
      '\\end{array} \\right.\n\\tag{3.1}\n$$\n\n'
      # 2: cases with "if" and "otherwise"
      '$$\nf(x) = \\begin{cases} \\frac{1}{\\theta} e^{-x/\\theta} & \\text{if } x \\geq 0 \\\\ '
      '0 & \\text{otherwise} \\end{cases}\n\\tag{3.2}\n$$\n\n'
      # 3: a printed error in the zero branch, seen only below 0
      '$$\nF(x) = \\begin{cases} 1 & x < 0 \\\\ 1 - e^{-x/\\theta} & x \\ge 0 \\end{cases}\n'
      '\\tag{3.3}\n$$\n\n'
      # 4: one row per branch, each with its condition (harvested)
      '$$\n\\begin{aligned} F(x) &= 0, \\quad x < 0 \\\\ F(x) &= 1 - e^{-x/\\theta}, \\quad '
      'x \\geq 0 \\end{aligned}\n$$\n\n'
      # 5: a condition on a parameter; 6: one that is no relation (kept as before)
      '$$\nE[X] = \\theta, \\quad \\theta > 1\n\\tag{3.5}\n$$\n\n'
      '$$\nE[X] = \\theta, \\quad x \\in \\mathbb{N}\n\\tag{3.6}\n$$\n\n'
      # 7: no point inside; 8: parity (the odd branch is wrong)
      '$$\nF(x) = 5, \\quad x < -10\n\\tag{3.7}\n$$\n\n'
      "$$\n\\mu_r^{\\prime} = \\left\\{ \\begin{array}{ll} r! \\, \\theta^{r} & (r \\text{ even}) "
      "\\\\ 0 & (r \\text{ odd}) \\end{array} \\right.\n\\tag{3.8}\n$$\n")


def test_piecewise_branches_and_row_conditions(tmp_path):
    # each branch is evaluated only at the points inside its condition; a "probe" argument
    # reaches a branch the table's points miss (x < 0); a point outside every branch is
    # skipped, not compared; a condition that is no relation leaves the points as they were
    (tmp_path / "text.md").write_text(PW, "utf-8")
    dists = {"expon": {**DISTS["expon"], "probe": {"@x": [-0.7]}}}
    e = [{"id": f"({t})", "where": {"tag": t}, "qty": q, "dist": "expon"}
         for t, q in (("3.1", "cdf"), ("3.2", "pdf"), ("3.3", "cdf"), ("3.5", "mean"),
                      ("3.6", "mean"), ("3.7", "cdf"))]
    e.append({"id": "(3.8)", "where": {"tag": "3.8"}, "qty": "raw_moment", "dist": "expon",
              "r_symbol": "r", "r": [1, 2, 3]})
    res = run(tmp_path, dists=dists, entries=e, scan=[{"pages": "1", "dist": "expon"}])
    r = by_id(res)
    assert r["(3.1)"]["status"] == "passed"
    assert [b["points"] for b in r["(3.1)"]["branches"]] == [3, 3]      # probes; table points
    assert r["(3.2)"]["status"] == "passed"
    assert r["(3.2)"]["branches"][1]["condition"] == "\\text{otherwise}"
    assert [b["points"] for b in r["(3.2)"]["branches"]] == [3, 3]
    assert r["(3.3)"]["status"] == "failed"                             # 1 for x < 0
    rows = {x["id"]: x for x in res["results"] if x["harvested"]}
    assert rows["p001-d04#0 cdf"]["status"] == "passed"                 # only the probe point
    assert rows["p001-d04#1 cdf"]["status"] == "passed"
    assert r["(3.5)"]["status"] == "passed" and r["(3.5)"]["outside"] == 1     # theta = 0.5
    assert r["(3.6)"]["status"] == "passed" and not r["(3.6)"].get("outside")
    assert r["(3.7)"]["status"] == "inconclusive" and "no point" in r["(3.7)"]["reason"]
    assert r["(3.8)"]["status"] == "failed"                             # r = 1, 3 give 0
    assert [b["points"] for b in r["(3.8)"]["branches"]] == [1, 2]
    # a formula is crosschecked only when every branch was compared somewhere
    fj = tmp_path / "formulas.jsonl"
    fj.write_text("".join(json.dumps({"id": f"p001-d0{k}", "status": "engines_agree"}) + "\n"
                          for k in (1, 3)), "utf-8")
    C.mark_formulas(fj, [r["(3.1)"], {**r["(3.1)"], "formula": "p001-d03", "branches": [
        {"points": 3}, {"points": 0}]}])
    recs = [json.loads(s) for s in fj.read_text("utf-8").splitlines()]
    assert [x["status"] for x in recs] == ["crosschecked", "engines_agree"]
    assert recs[1]["crosscheck"]["partial"] is True
    # a support list is integers from its first value (to its last, when one is printed)
    t = C.condition("x = 1, 2, \\dots ,")
    assert C.holds(t, {"x": 2}) is True and C.holds(t, {"x": 0}) is False
    assert C.holds(t, {"x": 2.5}) is False
    t = C.condition("\\text{for } k = 0, 1, \\ldots, n")
    assert C.holds(t, {"k": 3, "n": 5}) is True and C.holds(t, {"k": 6, "n": 5}) is False
    # an entry without a condition never gets the probe argument
    plain = run(tmp_path, dists=dists, scan=[],
                entries=[{"id": "m", "where": {"tag": "3.5"}, "eq": "rhs", "qty": "mean",
                          "dist": "expon", "row": None, "notation": [[r",\s*\\quad.*", ""]]}])
    assert len(by_id(plain)["m"]["points"]) == 3


def _val(tex, **vals):
    expr = C.book_expr(tex)
    assert expr is not None, tex
    v, why = C.evaluate(expr, vals)
    assert v is not None, (tex, why)
    return v.real


def test_modified_bessel_i_is_read_and_an_indicator_still_is_one():
    # I_0(1), I_1(1), I_{2.5}(1.5) (mpmath besseli); the von Mises density and A(kappa) = I_1/I_0
    assert _val(r"I_{0}(\kappa)", kappa=1.0) == pytest.approx(1.2660658777520082)
    assert _val(r"I_1(\kappa)", kappa=1.0) == pytest.approx(0.5651591039924851)
    assert _val(r"I_{\nu}(x)", nu=2.5, x=1.5) == pytest.approx(0.171662022188296)
    assert _val(r"I_{p/2 - 1}(\kappa)", p=3, kappa=2.0) == pytest.approx(2.04623686308905)
    vm = _val(r"\frac{1}{2 \pi I_{0}(\kappa)} e^{\kappa \cos (\theta - \mu)}", kappa=2.0, theta=0.3, mu=0.0)
    assert vm == pytest.approx(0.471801171182427)
    assert _val(r"\frac{I_{1}(\kappa)}{I_{0}(\kappa)}", kappa=2.0) == pytest.approx(0.6977746579640081)
    # an indicator keeps its meaning: a set as the subscript
    assert _val(r"I_{(0, \infty)}(x) e^{-x}", x=1.0) == pytest.approx(0.36787944)
    assert _val(r"I_{[0,1]}(x) x", x=0.5) == pytest.approx(0.5)
