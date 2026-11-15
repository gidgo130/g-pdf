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


# the ETA timer: a stamped line of timing and counts every N seconds while a step runs

def _stamps(path):
    return [x for x in path.read_text("utf-8").splitlines() if x.strip()]


def test_eta_lines_come_from_a_progress_file_and_never_hold_page_text(tmp_path):
    prog = tmp_path / "heartbeat.json"
    eta = tmp_path / "eta.log"
    code = ("import sys, time, json, pathlib\n"
            "p = pathlib.Path(sys.argv[1])\n"
            "for i in range(1, 7):\n"
            "    p.write_text(json.dumps({'stage': 'secret page text', 'done': i, 'total': 10}))\n"
            "    time.sleep(0.5)\n")
    r = watch.run_watched(_py(code) + [str(prog)], watch=[prog], log=tmp_path / "s.log",
                          stall=30.0, poll=0.1, step="g01", eta_log=eta, eta_every=1.0)
    assert r.status == "done"
    lines = _stamps(eta)
    assert len(lines) >= 2
    assert any("pages" in x and "/10" in x for x in lines)
    assert all("g01" in x and "heartbeat" in x for x in lines)
    assert not any("secret page text" in x for x in lines)
    assert "done" in lines[-1]                       # the last line says the step ended


def test_progress_is_also_read_from_the_log(tmp_path):
    eta = tmp_path / "eta.log"
    code = "import time\nprint('  standard: 3/12 pages', flush=True)\ntime.sleep(1.6)\n"
    watch.run_watched(_py(code), watch=[], log=tmp_path / "s.log", stall=30.0, poll=0.1,
                      eta_log=eta, eta_every=1.0)
    assert any("pages 3/12" in x for x in _stamps(eta))


def test_a_clock_jump_is_noted_as_a_sleep_and_left_out_of_the_awake_time(tmp_path):
    clock = [1000.0]
    eta = watch.EtaLog(tmp_path / "eta.log", every=600, step="s", now=lambda: clock[0])
    eta.tick(done=2, total=10, beat_age=5.0)
    clock[0] += 600
    eta.tick(done=4, total=10, beat_age=5.0)
    clock[0] += 600 + 45 * 60                        # the machine slept 45 minutes
    eta.tick(done=5, total=10, beat_age=2700.0)
    lines = _stamps(tmp_path / "eta.log")
    assert len(lines) == 3
    assert "sleep/pause" not in lines[1]
    assert "sleep/pause 45m" in lines[2]
    assert eta.paused == 45 * 60 and eta.awake() == 1200   # awake time: 20 min, not 65


def test_a_small_clock_drift_is_no_sleep(tmp_path):
    clock = [0.0]
    eta = watch.EtaLog(tmp_path / "eta.log", every=600, now=lambda: clock[0])
    eta.tick(done=1, total=2, beat_age=0)
    clock[0] += 600 + 100                            # under 2 minutes late
    eta.tick(done=2, total=2, beat_age=0)
    assert "sleep/pause" not in _stamps(tmp_path / "eta.log")[1]


def test_the_stop_file_ends_the_eta_lines_and_the_cli_takes_the_options(tmp_path):
    from gpdf.cli import main
    stop = tmp_path / "STOP"
    stop.write_text("")
    rc = main(["math-watch", "--stall", "30", "--poll", "0.1", "--log", str(tmp_path / "a.log"),
               "--eta-log", str(tmp_path / "eta.log"), "--every", "1", "--stop-file", str(stop),
               "--step", "x", "--", *_py("import time; time.sleep(1.5)")])
    assert rc == 0
    eta = tmp_path / "eta.log"
    assert not (eta.exists() and _stamps(eta))          # stopped before the first line
