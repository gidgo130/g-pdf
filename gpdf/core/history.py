# SPDX-License-Identifier: AGPL-3.0-or-later
"""Undo/redo history with "is the document clean?" tracking.

A command does its work in ``do`` and reverses it in ``undo``. ``byte_cost`` is
how much memory the command keeps around to be able to undo (snapshots of
deleted or edited pages); the history drops its oldest entries once the total
passes ``max_bytes`` so a long editing session can't eat all memory.

Cleanliness is positional: ``mark_clean`` remembers where in the history the
file was saved, so undoing back to that point (or redoing to it) reads as
"no unsaved changes" again.
"""

from __future__ import annotations

from typing import Sequence

DEFAULT_MAX_BYTES = 256 * 1024 * 1024


class Command:
    label: str = ""
    byte_cost: int = 0

    def do(self) -> None:
        raise NotImplementedError

    def undo(self) -> None:
        raise NotImplementedError


class CompositeCommand(Command):
    """Several commands that undo and redo as one step."""

    def __init__(self, label: str, commands: Sequence[Command]) -> None:
        self.label = label
        self._commands = list(commands)
        self.byte_cost = sum(c.byte_cost for c in self._commands)

    def do(self) -> None:
        done: list[Command] = []
        try:
            for command in self._commands:
                command.do()
                done.append(command)
        except Exception:
            for command in reversed(done):
                command.undo()
            raise

    def undo(self) -> None:
        for command in reversed(self._commands):
            command.undo()


class History:
    def __init__(self, max_bytes: int = DEFAULT_MAX_BYTES) -> None:
        self._commands: list[Command] = []
        self._pos = 0  # number of commands currently applied
        self._clean_pos: int | None = 0  # None: the saved state is unreachable
        self._max_bytes = max_bytes

    # -- state ----------------------------------------------------------------

    @property
    def can_undo(self) -> bool:
        return self._pos > 0

    @property
    def can_redo(self) -> bool:
        return self._pos < len(self._commands)

    @property
    def undo_label(self) -> str:
        return self._commands[self._pos - 1].label if self.can_undo else ""

    @property
    def redo_label(self) -> str:
        return self._commands[self._pos].label if self.can_redo else ""

    @property
    def is_clean(self) -> bool:
        return self._clean_pos is not None and self._pos == self._clean_pos

    @property
    def total_bytes(self) -> int:
        return sum(c.byte_cost for c in self._commands)

    # -- changing -------------------------------------------------------------

    def push(self, command: Command) -> None:
        """Run ``command`` and record it. If it raises, nothing is recorded."""
        command.do()
        # Anything that could have been redone is now gone.
        del self._commands[self._pos:]
        if self._clean_pos is not None and self._clean_pos > self._pos:
            self._clean_pos = None
        self._commands.append(command)
        self._pos += 1
        self._trim()

    def undo(self) -> Command | None:
        if not self.can_undo:
            return None
        command = self._commands[self._pos - 1]
        command.undo()
        self._pos -= 1
        return command

    def redo(self) -> Command | None:
        if not self.can_redo:
            return None
        command = self._commands[self._pos]
        command.do()
        self._pos += 1
        return command

    def mark_clean(self) -> None:
        self._clean_pos = self._pos

    # -- memory cap -----------------------------------------------------------

    def _trim(self) -> None:
        while self.total_bytes > self._max_bytes and len(self._commands) > 1:
            del self._commands[0]
            dropped_applied = self._pos > 0
            if dropped_applied:
                self._pos -= 1
            if self._clean_pos is not None:
                if self._clean_pos == 0 and dropped_applied:
                    self._clean_pos = None  # the state before that command is gone
                elif self._clean_pos > 0:
                    self._clean_pos -= 1
