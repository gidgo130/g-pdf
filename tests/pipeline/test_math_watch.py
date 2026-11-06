# SPDX-License-Identifier: AGPL-3.0-or-later
"""The stall watcher: a long step (engine group, assembly, checks) is stopped when its log,
cache and heartbeat stop growing, instead of waiting for a "done" that never comes."""

import json
import sys
import time

from gpdf.pipeline.mathocr import watch


def _py(code: str) -> list[str]:
    return [sys.executable, "-c", code]


def test_a_silent_step_is_stopped_as_stalled(tmp_path):
    log = tmp_path / "step.log"
    hb = tmp_path / "heartbeat.json"
    t = time.monotonic()
    r = watch.run_watched(_py("print('start', flush=True)\nimport time\ntime.sleep(120)"),
                          watch=[tmp_path / "cache"], log=log, heartbeat=hb, stall=1.0,
                          poll=0.1, step="g01")
    assert r.status == "stalled"
    assert "no growth" in r.reason and "1 s" in r.reason
    assert time.monotonic() - t < 30
    beat = json.loads(hb.read_text("utf-8"))
    assert beat["step"] == "g01" and beat["status"] == "stalled"
    assert "start" in log.read_text("utf-8")


def test_a_step_that_keeps_growing_runs_to_the_end(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    code = ("import sys, time, pathlib\n"
            "d = pathlib.Path(sys.argv[1])\n"
            "for i in range(8):\n"
            "    (d / f'p{i}').write_text('x' * (i + 1))\n"
            "    time.sleep(0.3)\n")
    r = watch.run_watched(_py(code) + [str(cache)], watch=[cache], log=tmp_path / "s.log",
                          heartbeat=tmp_path / "hb.json", stall=1.0, poll=0.1)
    assert (r.status, r.returncode) == ("done", 0)
    assert json.loads((tmp_path / "hb.json").read_text("utf-8"))["status"] == "done"


def test_a_failing_step_reports_its_code(tmp_path):
    r = watch.run_watched(_py("import sys; sys.exit(4)"), watch=[], log=tmp_path / "s.log",
                          heartbeat=tmp_path / "hb.json", stall=5.0, poll=0.1)
    assert (r.status, r.returncode) == ("failed", 4)


def test_the_stalled_step_is_killed_with_its_children(tmp_path):
    marker = tmp_path / "child_alive"
    child = ("import time, pathlib, sys\n"
             "time.sleep(3)\n"
             "pathlib.Path(sys.argv[1]).write_text('still here')\n")
    parent = ("import subprocess, sys, time\n"
              f"subprocess.Popen([sys.executable, '-c', {child!r}, {str(marker)!r}])\n"
              "time.sleep(120)\n")
    r = watch.run_watched(_py(parent), watch=[], log=tmp_path / "s.log",
                          heartbeat=tmp_path / "hb.json", stall=0.5, poll=0.1)
    assert r.status == "stalled"
    time.sleep(4)
    assert not marker.exists()


def test_math_watch_cli_exit_codes(tmp_path):
    from gpdf.cli import main
    rc = main(["math-watch", "--stall", "1", "--poll", "0.1", "--log", str(tmp_path / "a.log"),
               "--heartbeat", str(tmp_path / "a.json"), "--step", "x", "--",
               *_py("import time; time.sleep(60)")])
    assert rc == 3
    rc = main(["math-watch", "--stall", "5", "--log", str(tmp_path / "b.log"),
               "--heartbeat", str(tmp_path / "b.json"), "--", *_py("print('ok')")])
    assert rc == 0
