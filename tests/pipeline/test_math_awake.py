# SPDX-License-Identifier: AGPL-3.0-or-later
"""math-export keeps a Windows PC awake while it runs (SetThreadExecutionState), always
releases it, does nothing elsewhere or with --allow-sleep, and records awake time apart
from wall time in run.json."""

import json

import pytest

from gpdf.pipeline.mathocr import awake

from .test_math_run import run, setup  # noqa: F401  (the fixture)


@pytest.fixture
def calls(monkeypatch):
    seen = []
    monkeypatch.setattr(awake, "_platform", lambda: "win32")
    monkeypatch.setattr(awake, "_set_state", lambda flags: seen.append(flags) or 1)
    return seen


def test_keep_awake_holds_and_releases(calls):
    with awake.KeepAwake() as k:
        assert calls == [awake.ES_CONTINUOUS | awake.ES_SYSTEM_REQUIRED] and k.held
    assert calls[-1] == awake.ES_CONTINUOUS and len(calls) == 2


def test_keep_awake_releases_on_errors(calls):
    with pytest.raises(RuntimeError):
        with awake.KeepAwake():
            raise RuntimeError("engine failed")
    assert calls == [awake.ES_CONTINUOUS | awake.ES_SYSTEM_REQUIRED, awake.ES_CONTINUOUS]


def test_keep_awake_does_nothing_off_windows_or_when_allowed_to_sleep(calls, monkeypatch):
    with awake.KeepAwake(enabled=False) as k:
        assert not k.held
    monkeypatch.setattr(awake, "_platform", lambda: "linux")
    with awake.KeepAwake() as k:
        assert not k.held
    assert calls == []


def test_awake_clock_moves_forward():
    a = awake.awake_clock()
    b = awake.awake_clock()
    assert b >= a


def test_math_export_keeps_awake_and_records_awake_time(setup, tmp_path, calls):  # noqa: F811
    exe, home, pdf, log = setup
    out = tmp_path / "out"
    assert run(exe, home, pdf, out, "--pages", "1-2") == 0
    assert calls == [awake.ES_CONTINUOUS | awake.ES_SYSTEM_REQUIRED, awake.ES_CONTINUOUS]
    rec = json.loads((out / "book" / "run.json").read_text("utf-8"))
    assert rec["awake_seconds"] <= rec["seconds"] + 1
    assert "total_awake" in rec["engine_seconds"]
    meta = json.loads((out / "book" / "meta.json").read_text("utf-8"))
    assert meta["options"]["keep_awake"] is True
    calls.clear()
    assert run(exe, home, pdf, out, "--pages", "1-2", "--allow-sleep") == 0
    assert calls == []
    meta = json.loads((out / "book" / "meta.json").read_text("utf-8"))
    assert meta["options"]["keep_awake"] is False
