# SPDX-License-Identifier: AGPL-3.0-or-later
"""gpdf-cli split: dry run, decisions file, exit codes, refusals, odd paths."""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("PIL")
import pymupdf  # noqa: E402

from gpdf import cli  # noqa: E402

# mini book: 1 cover | 2 solid | 3 dithered | 4 plain | 5 blank_side (flagged spread)
#            6 very_narrow (flagged, called single) | 7 landscape_picture (flagged single)
FLAGGED = [5, 6, 7]


def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


@pytest.fixture
def src(mini, tmp_path):
    path = tmp_path / "mini.pdf"
    shutil.copy(mini[0], path)
    return path


@pytest.fixture
def dry(src, tmp_path):
    """Dry run once; returns (outdir, decisions dict)."""
    outdir = tmp_path / "out"
    assert cli.main(["split", str(src), "-o", str(outdir), "--dry-run"]) == 0
    return outdir, json.loads((outdir / "mini_split.decisions.json").read_text("utf-8"))


def run(src, outdir, *extra):
    return cli.main(["split", str(src), "-o", str(outdir), *extra])


def test_dry_run_writes_report_decisions_and_sheet_only(src, tmp_path, capsys):
    before = sha(src)
    outdir = tmp_path / "out"
    assert run(src, outdir, "--dry-run") == 0
    assert sorted(os.listdir(outdir)) == ["mini_split.decisions.json", "mini_split.report.json",
                                          "mini_split.review.html"]
    assert sha(src) == before
    dec = json.loads((outdir / "mini_split.decisions.json").read_text("utf-8"))
    assert dec["schema"] == 1 and dec["source_size"] == os.path.getsize(src)
    assert dec["source_mtime_ns"] == os.stat(src).st_mtime_ns
    assert [p["page"] for p in dec["pages"]] == [1, 2, 3, 4, 5, 6, 7]
    assert [p["page"] for p in dec["pages"] if not p["reviewed"]] == FLAGGED
    assert all(p["detected"]["flagged"] == (p["page"] in FLAGGED) for p in dec["pages"])
    assert [p["action"] for p in dec["pages"]] == ["keep"] + ["split"] * 4 + ["keep"] * 2
    assert dec["pages"][1]["cut"] == dec["pages"][1]["detected"]["cut"]
    assert dec["pages"][0]["cut"] is None
    rep = json.loads((outdir / "mini_split.report.json").read_text("utf-8"))
    assert rep["schema"] == 1 and rep["dry_run"] and rep["summary"]["flagged"] == FLAGGED
    out = capsys.readouterr().out
    assert "7 pages, 4 spreads, 3 singles, 3 flagged" in out
    for n in FLAGGED:
        assert f"p.    {n}" in out


def test_narrow_gutter_called_single_is_flagged_keep_unreviewed(src, tmp_path, capsys):
    """A spread with no shadow and a gutter too narrow to find is called single (0.6): it
    is still flagged, pre-filled as keep, and listed for review."""
    assert run(src, tmp_path / "out", "--dry-run") == 0
    dec = json.loads((tmp_path / "out" / "mini_split.decisions.json").read_text("utf-8"))
    p6 = dec["pages"][5]
    assert p6["detected"]["is_spread"] is False and p6["detected"]["flagged"] is True
    assert p6["detected"]["confidence"] < 0.65
    assert (p6["action"], p6["cut"], p6["reviewed"]) == ("keep", None, False)
    assert "p.    6  0.60  single: landscape page: no seam or gutter found" in capsys.readouterr().out


def test_real_run_leaves_unreviewed_flagged_pages_whole(src, tmp_path, capsys):
    outdir = tmp_path / "out"
    assert run(src, outdir, "--report", str(tmp_path / "r.json")) == 2
    assert len(pymupdf.open(outdir / "mini_split.pdf")) == 1 + 3 * 2 + 3
    rep = json.loads((tmp_path / "r.json").read_text("utf-8"))
    assert rep["summary"]["unreviewed_left_whole"] == FLAGGED
    assert "flagged, not reviewed, left whole: p. 5, 6, 7" in capsys.readouterr().out


def test_accept_flagged_follows_the_detector(src, tmp_path):
    outdir = tmp_path / "out"
    assert run(src, outdir, "--accept-flagged") == 0
    # blank side split (guess: spread); very narrow and the picture kept (guess: single)
    assert len(pymupdf.open(outdir / "mini_split.pdf")) == 1 + 4 * 2 + 2


def test_reviewed_decisions_are_applied_exactly(src, dry, tmp_path):
    outdir, dec = dry
    pages = dec["pages"]
    pages[1].update(action="keep")                                  # 2: a spread kept whole
    pages[4].update(reviewed=True)                                  # 5: accept split guess
    pages[5].update(action="split", cut=[0.5, 0.5], reviewed=True)  # 6: narrow gutter by hand
    pages[6].update(action="split", cut=[0.3, 0.32], reviewed=True)  # 7: custom slanted cut
    path = outdir / "mini_split.decisions.json"
    path.write_text(json.dumps(dec), "utf-8")
    assert run(src, outdir, "--decisions", str(path), "--report", str(tmp_path / "r.json")) == 0
    rep = json.loads((tmp_path / "r.json").read_text("utf-8"))
    actions = [p["action"] for p in rep["pages"]]
    assert actions == ["keep", "keep", "split", "split", "split", "split", "split"]
    with pymupdf.open(src) as s, pymupdf.open(outdir / "mini_split.pdf") as out:
        assert len(out) == 2 + 5 * 2
        p7 = rep["pages"][6]
        assert p7["cut"] == [0.3, 0.32] and p7["encoding"] == "flate"
        w = s[6].rect.width
        left, right = (out[o - 1] for o in p7["out_pages"])
        assert left.rect.width == pytest.approx(0.32 * w, abs=0.5)
        assert right.rect.width == pytest.approx(0.70 * w, abs=0.5)
        # 8-bit grey halves are lossless: equal to the source outside the wedge
        sx = s[6].get_images()[0][0]
        pix = pymupdf.Pixmap(s, sx)
        a = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width)
        x = left.get_images()[0][0]
        assert out.xref_get_key(x, "Filter")[1] == "/FlateDecode"
        lp = pymupdf.Pixmap(out, x)
        lh = np.frombuffer(lp.samples, np.uint8).reshape(lp.height, lp.width)
        wedge_free = int(0.3 * a.shape[1]) - 1
        assert np.array_equal(lh[:, :wedge_free], a[:, :wedge_free])
        assert p7["whitened_px"] > 0


def test_stale_decisions_file_is_refused_unless_forced(src, dry, capsys):
    outdir, _ = dry
    path = outdir / "mini_split.decisions.json"
    st = os.stat(src)
    os.utime(src, ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))
    assert run(src, outdir, "--decisions", str(path)) == 1
    assert "source changed" in capsys.readouterr().err
    assert not (outdir / "mini_split.pdf").exists()
    assert run(src, outdir, "--decisions", str(path), "--force") == 2


def test_refuses_existing_outputs_without_force(src, dry, capsys):
    outdir, _ = dry
    assert run(src, outdir, "--dry-run") == 1                  # would clobber the review
    assert "may hold your review" in capsys.readouterr().err
    (outdir / "mini_split.pdf").write_bytes(b"old")
    assert run(src, outdir) == 1
    assert "already exists" in capsys.readouterr().err
    assert (outdir / "mini_split.pdf").read_bytes() == b"old"
    assert run(src, outdir, "--force") == 2


def test_bad_decisions_and_usage_errors_exit_1(src, dry, tmp_path, capsys):
    outdir, dec = dry
    dec["pages"][2]["action"] = "cut"
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(dec), "utf-8")
    assert run(src, outdir, "--decisions", str(bad)) == 1
    assert "page 3: action must be" in capsys.readouterr().err
    assert run(tmp_path / "missing.pdf", outdir) == 1
    with pytest.raises(SystemExit) as e:
        cli.main(["split", str(src)])                           # no -o
    assert e.value.code == 1


def test_non_ascii_path_with_spaces_via_module_entry_point(mini, tmp_path):
    folder = tmp_path / "carpeta con espacios"
    folder.mkdir()
    src = folder / "Vasconcelos, José. prueba.pdf"
    shutil.copy(mini[0], src)
    before = sha(src)
    outdir = folder / "salida ñ"
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    cmd = [sys.executable, "-m", "gpdf.cli", "split", str(src), "-o", str(outdir)]
    r = subprocess.run(cmd + ["--dry-run"], capture_output=True, text=True, encoding="utf-8",
                       env=env)
    assert r.returncode == 0, r.stderr
    assert "Vasconcelos, José. prueba.pdf: 7 pages" in r.stdout
    r = subprocess.run(cmd + ["--decisions", str(outdir / "Vasconcelos, José. prueba_split.decisions.json")],
                       capture_output=True, text=True, encoding="utf-8", env=env)
    assert r.returncode == 2, r.stderr
    assert len(pymupdf.open(outdir / "Vasconcelos, José. prueba_split.pdf")) == 10
    assert sha(src) == before


def test_missing_prep_extra_gives_install_hint(tmp_path):
    code = ("import sys; sys.modules['numpy'] = None\n"
            "from gpdf.cli import main\n"
            f"sys.exit(main(['split', {str(tmp_path / 'x.pdf')!r}, '-o', {str(tmp_path)!r}]))\n")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 1
    assert 'pip install "g-pdf[prep]"' in r.stderr


def sheet_decisions(path):
    text = path.read_text("utf-8")
    (block,) = re.findall(r'<script type="application/json" id="gpdf-data">(.*?)</script>',
                          text, re.S)
    return json.loads(block)


def fingerprint(*paths):
    return [(sha(p), os.stat(p).st_mtime_ns) for p in paths]


def test_sheet_only_writes_only_the_sheet_showing_the_saved_review(src, dry, capsys):
    outdir, dec = dry
    dec_path = outdir / "mini_split.decisions.json"
    rep_path = outdir / "mini_split.report.json"
    sheet = outdir / "mini_split.review.html"
    prefilled = dec_path.read_text("utf-8")
    pages = dec["pages"]                                       # a saved review
    pages[1].update(action="keep")
    pages[4].update(reviewed=True, cut=[0.48, 0.5])
    pages[6].update(action="split", cut=[0.3, 0.32], reviewed=True)
    dec_path.write_text(json.dumps(dec, indent=1), "utf-8")
    before = fingerprint(dec_path, rep_path)
    sheet.unlink()
    capsys.readouterr()

    assert run(src, outdir, "--sheet-only", "--decisions", str(dec_path)) == 0
    assert fingerprint(dec_path, rep_path) == before
    assert sorted(os.listdir(outdir)) == ["mini_split.decisions.json", "mini_split.report.json",
                                          "mini_split.review.html"]
    data = sheet_decisions(sheet)
    assert data["decisions"] == dec                            # incl. detector and stamp
    assert data["source_mtime_ns_text"] == str(dec["source_mtime_ns"])
    out = capsys.readouterr().out
    assert "showing:" in out and "2 / 3 flagged reviewed" in out and "sheet:" in out

    assert run(src, outdir, "--sheet-only") == 0               # no file: the detector's guess
    assert fingerprint(dec_path, rep_path) == before
    assert sheet_decisions(sheet)["decisions"] == json.loads(prefilled)


def test_sheet_only_refuses_stale_or_bad_decisions_and_dry_run(src, dry, tmp_path, capsys):
    outdir, dec = dry
    dec_path = outdir / "mini_split.decisions.json"
    sheet = outdir / "mini_split.review.html"
    before = fingerprint(dec_path, sheet)
    st = os.stat(src)
    os.utime(src, ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))
    assert run(src, outdir, "--sheet-only", "--decisions", str(dec_path)) == 1
    assert "source changed" in capsys.readouterr().err
    dec["pages"].pop()
    short = tmp_path / "short.json"
    short.write_text(json.dumps(dec), "utf-8")
    assert run(src, outdir, "--sheet-only", "--decisions", str(short), "--force") == 1
    assert "decisions file has 6 pages, the source has 7" in capsys.readouterr().err
    assert fingerprint(dec_path, sheet) == before              # nothing written
    for other in ("--dry-run", "--no-sheet", "--accept-flagged"):
        with pytest.raises(SystemExit) as e:
            run(src, outdir, "--sheet-only", other)
        assert e.value.code == 1
        assert f"--sheet-only cannot be used with {other}" in capsys.readouterr().err
    assert fingerprint(dec_path, sheet) == before


def test_forced_dry_run_backs_up_a_reviewed_decisions_file(src, dry, capsys):
    outdir, dec = dry
    dec_path = outdir / "mini_split.decisions.json"
    prefilled = dec_path.read_bytes()
    capsys.readouterr()
    assert run(src, outdir, "--dry-run", "--force") == 0       # not reviewed: no backup
    assert not list(outdir.glob("*.bak.json")) and "backup:" not in capsys.readouterr().out

    dec["pages"][4]["reviewed"] = True                         # one reviewed flag is enough
    dec_path.write_text(json.dumps(dec, indent=1), "utf-8")
    reviewed = dec_path.read_bytes()
    assert run(src, outdir, "--dry-run", "--force") == 0
    (backup,) = outdir.glob("*.bak.json")
    assert re.fullmatch(r"mini_split\.decisions\.\d{8}-\d{6}\.bak\.json", backup.name)
    assert backup.read_bytes() == reviewed
    assert f"backup:    {backup}" in capsys.readouterr().out
    assert dec_path.read_bytes() == prefilled                  # replaced by the fresh guess

    dec_path.write_text("{not json", "utf-8")                  # unreadable: kept as well
    assert run(src, outdir, "--dry-run", "--force") == 0
    assert len(list(outdir.glob("*.bak.json"))) == 2
