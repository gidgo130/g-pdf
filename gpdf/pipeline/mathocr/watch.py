# SPDX-License-Identifier: AGPL-3.0-or-later
"""Stall watching for the long steps of a math-export run.

Two tools, both made after Batch B-2's j21b hang (an identity check stuck inside mpmath for
7.5 hours while its driver waited only for a "done" mark):

- ``hard_timeout(seconds)``: a per-item limit for numeric and cross-checks. A checker's own
  deadline is only read between function evaluations; this one raises ``ItemTimeout`` in
  the checking thread from a timer, at the next Python bytecode, and keeps raising it until
  the block ends, so a broad ``except Exception`` cannot swallow it (it is a BaseException).
- ``run_watched(cmd, ...)``: runs a step as a subprocess, writes a heartbeat file, and stops
  the step (with its child processes) when its log, its watched paths (cache folders) and
  any files they hold stop growing for ``stall`` seconds. The result says ``done``,
  ``failed`` or ``stalled`` with the reason, so a driver can mark the step ``retry_later``
  and move on instead of waiting. With ``eta_log`` it also appends a line of timing and
  counts (pages done of total, age of the last heartbeat, a rate from awake time) every
  ``eta_every`` seconds, and notes a sleep or pause when the clock jumps.
"""

from __future__ import annotations

import ctypes
import json
import os
import re
import subprocess
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

STALL_STEP = 600.0         # seconds without growth: engine groups, assembly
STALL_CHECK = 300.0        # numeric checks and cross-checks
ITEM_TIMEOUT = 20.0        # seconds per checked item
EXIT_STALLED = 3


class ItemTimeout(BaseException):
    """Raised in the checking thread when an item runs past its hard limit."""


def _async_raise(tid: int, exc) -> None:
    ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_ulong(tid),
                                               ctypes.py_object(exc) if exc else None)


@contextmanager
def hard_timeout(seconds: float | None):
    """Raise ItemTimeout in this thread if the block runs longer than ``seconds``."""
    if not seconds or seconds <= 0:
        yield
        return
    tid = threading.get_ident()
    lock = threading.Lock()
    state = {"armed": True, "fired": False}
    done = threading.Event()

    def fire():
        if done.wait(seconds):
            return
        while True:                       # again every 0.5 s, in case a handler ate it
            with lock:
                if not state["armed"]:
                    return
                state["fired"] = True
                _async_raise(tid, ItemTimeout)
            if done.wait(0.5):
                return

    t = threading.Thread(target=fire, daemon=True)
    t.start()
    try:
        yield
    finally:
        with lock:
            state["armed"] = False
            if state["fired"]:
                _async_raise(tid, None)    # drop one still pending, if any
        done.set()


def _signature(paths: list[Path]) -> tuple:
    """Total size, file count and newest mtime of the paths (folders walked)."""
    size = count = 0
    newest = 0.0
    for p in paths:
        try:
            if p.is_file():
                files = [p]
            elif p.is_dir():
                files = [f for f in p.rglob("*") if f.is_file()]
            else:
                continue
        except OSError:
            continue
        for f in files:
            try:
                st = f.stat()
            except OSError:
                continue
            size += st.st_size
            count += 1
            newest = max(newest, st.st_mtime)
    return size, count, newest


def _kill_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        try:
            os.killpg(proc.pid, 9)
        except (ProcessLookupError, PermissionError):
            proc.kill()
    try:
        proc.wait(30)
    except subprocess.TimeoutExpired:
        proc.kill()


# ---------------------------------------------------------------- the ETA timer

PAGES_RE = re.compile(r"(\d+)/(\d+) pages")
SLEEP_GAP = 120.0          # seconds a tick may be late before it is a sleep or pause
ETA_EVERY = 600.0


class EtaLog:
    """Appends one stamped line per ``tick``: pages done of total, the age of the last
    heartbeat, a rate from awake time and an estimate. Awake time is the elapsed wall time
    less the sleeps and pauses: a tick later than ``every`` + 2 minutes means the machine
    slept (the excess is noted as ``sleep/pause Xm`` and left out). Counts and timing only,
    never page text. ``now`` is the clock (seconds), replaceable for tests."""

    def __init__(self, path: Path, every: float = ETA_EVERY, step: str = "", now=time.time):
        self.path, self.every, self.step, self.now = Path(path), every, step, now
        self.t0 = self.last = now()
        self.paused = 0.0
        self.first = None              # (done, awake seconds) of the first count seen

    def awake(self) -> float:
        return self.now() - self.t0 - self.paused

    def tick(self, done: int | None, total: int | None, beat_age: float | None,
             ended: str = "") -> str:
        now = self.now()
        gap = now - self.last - self.every
        note = ""
        if not ended and gap > SLEEP_GAP:
            self.paused += gap
            note = f"; sleep/pause {round(gap / 60)}m"
        self.last = now
        awake = self.awake()
        parts = [f"step {self.step or '-'}"]
        if done is not None and total:
            parts.append(f"pages {done}/{total} ({100 * done / total:.0f}%)")
            if self.first is None:
                self.first = (done, awake)
            d, a = done - self.first[0], awake - self.first[1]
            if d > 0 and a > 0:
                rate = d / (a / 60)
                parts.append(f"rate {rate:.1f} pages/awake-min")
                parts.append(f"ETA ~{(total - done) / rate:.0f} min awake")
        else:
            parts.append("pages ?")
        parts.append("last heartbeat " + (f"{beat_age:.0f} s ago" if beat_age is not None else "?"))
        parts.append(f"awake {awake / 60:.0f} min")
        line = (datetime.fromtimestamp(now).strftime("%Y-%m-%d %H:%M") + " " + ", ".join(parts)
                + note + (f"; ended: {ended}" if ended else ""))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        return line


def _progress(log: Path, watch: list[Path]) -> tuple[int | None, int | None]:
    """(pages done, total) from a watched JSON file with done/total (or pages_done/pages_total)
    keys, else from the last "N/M pages" line at the end of the log; None when unknown."""
    for p in watch:
        try:
            if p.is_file() and p.suffix == ".json":
                d = json.loads(p.read_text("utf-8"))
                if isinstance(d, dict):
                    done, total = d.get("done", d.get("pages_done")), d.get("total", d.get("pages_total"))
                    if isinstance(done, int) and isinstance(total, int):
                        return done, total
        except (OSError, ValueError):
            continue
    try:
        with open(log, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - 8192))
            tail = f.read().decode("utf-8", "replace")
    except OSError:
        return None, None
    m = PAGES_RE.findall(tail)
    return (int(m[-1][0]), int(m[-1][1])) if m else (None, None)


@dataclass
class WatchResult:
    status: str               # done | failed | stalled
    returncode: int | None
    reason: str
    seconds: float


def _beat(path: Path | None, data: dict) -> None:
    if path is None:
        return
    tmp = path.with_name(path.name + ".tmp")
    for attempt in range(5):
        try:
            tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
            os.replace(tmp, path)
            return
        except PermissionError:           # a reader holds it on Windows: try again shortly
            time.sleep(0.2 * (attempt + 1))


def run_watched(cmd: list[str], watch: list[Path], log: Path, heartbeat: Path | None = None,
                stall: float = STALL_STEP, poll: float = 15.0, step: str = "",
                env: dict | None = None, eta_log: Path | None = None,
                eta_every: float = ETA_EVERY, stop_file: Path | None = None) -> WatchResult:
    """Run ``cmd`` (stdout and stderr to ``log``) until it ends or stalls. With ``eta_log``,
    an ETA line every ``eta_every`` seconds (and one when the step ends), until ``stop_file``
    exists: then the lines stop (the step and its stall watch go on)."""
    log = Path(log)
    log.parent.mkdir(parents=True, exist_ok=True)
    paths = [log] + [Path(p) for p in watch]
    eta = EtaLog(eta_log, eta_every, step) if eta_log else None

    def eta_line(ended: str = "") -> None:
        if eta is None or (stop_file is not None and Path(stop_file).exists()):
            return
        done, total = _progress(log, [Path(p) for p in watch])
        newest = _signature(paths)[2]
        eta.tick(done, total, max(0.0, time.time() - newest) if newest else None, ended)

    t0 = time.monotonic()
    started = datetime.now().isoformat(timespec="seconds")
    kw = {"start_new_session": True} if os.name != "nt" else {}
    with open(log, "w", encoding="utf-8") as out:
        proc = subprocess.Popen(cmd, stdout=out, stderr=subprocess.STDOUT, env=env, **kw)
        sig, grew = _signature(paths), time.monotonic()
        info = {"step": step, "pid": proc.pid, "cmd": cmd, "started": started,
                "stall_seconds": stall, "status": "running"}
        while True:
            try:
                proc.wait(poll)
                break
            except subprocess.TimeoutExpired:
                pass
            now = time.monotonic()
            s = _signature(paths)
            if s != sig:
                sig, grew = s, now
            idle = now - grew
            if eta is not None and time.time() - eta.last >= eta.every:
                eta_line()
            _beat(heartbeat, {**info, "beat": datetime.now().isoformat(timespec="seconds"),
                              "idle_seconds": round(idle, 1), "bytes": s[0], "files": s[1]})
            if idle >= stall:
                _kill_tree(proc)
                reason = (f"no growth of the log or watched paths for {stall:g} s "
                          f"(stopped after {now - t0:.0f} s)")
                _beat(heartbeat, {**info, "status": "stalled", "reason": reason,
                                  "ended": datetime.now().isoformat(timespec="seconds")})
                eta_line("stalled")
                return WatchResult("stalled", None, reason, round(now - t0, 1))
    rc = proc.returncode
    status = "done" if rc == 0 else "failed"
    reason = "" if rc == 0 else f"exit code {rc}"
    eta_line(status)
    _beat(heartbeat, {**info, "status": status, "returncode": rc, "reason": reason,
                      "ended": datetime.now().isoformat(timespec="seconds")})
    return WatchResult(status, rc, reason, round(time.monotonic() - t0, 1))
