# SPDX-License-Identifier: AGPL-3.0-or-later
"""Keep a Windows PC awake during a long math-export run, and an awake-time clock.

A laptop that sleeps halfway through an engine run stretches its wall clock by hours
(KBJ: two sleeps of over 2 h during the Marker run). ``KeepAwake`` asks Windows not to
sleep (SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED); the display may still
turn off) and always clears the request on the way out, on errors too. Elsewhere it does
nothing. ``awake_clock`` counts only time the machine was awake, so timings stay honest
when it sleeps anyway.
"""

from __future__ import annotations

import sys
import time

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001


def _platform() -> str:
    return sys.platform


def _set_state(flags: int) -> int:
    import ctypes

    return ctypes.windll.kernel32.SetThreadExecutionState(ctypes.c_uint(flags))


class KeepAwake:
    """``with KeepAwake(enabled):`` holds the machine awake on Windows."""

    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self.held = False

    def __enter__(self) -> KeepAwake:
        if self.enabled and _platform() == "win32":
            try:
                self.held = bool(_set_state(ES_CONTINUOUS | ES_SYSTEM_REQUIRED))
            except (OSError, AttributeError):
                self.held = False
        return self

    def __exit__(self, *exc) -> None:
        if self.held:
            try:
                _set_state(ES_CONTINUOUS)
            except (OSError, AttributeError):
                pass
            self.held = False


def awake_clock() -> float:
    """Seconds of awake time from an arbitrary origin: the unbiased interrupt time on
    Windows (it stops during sleep and hibernation), CLOCK_MONOTONIC elsewhere (which
    stops during suspend on Linux)."""
    if _platform() == "win32":
        try:
            import ctypes

            t = ctypes.c_ulonglong()
            if ctypes.windll.kernel32.QueryUnbiasedInterruptTime(ctypes.byref(t)):
                return t.value / 1e7
        except (OSError, AttributeError):
            pass
    return time.monotonic()
