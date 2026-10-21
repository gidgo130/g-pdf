# SPDX-License-Identifier: AGPL-3.0-or-later
"""Synthetic books, generated once per test session (no binary fixtures in the repo)."""

import pytest


@pytest.fixture(scope="session")
def synth():
    pytest.importorskip("numpy")
    pytest.importorskip("PIL")
    from . import synth

    return synth


@pytest.fixture(scope="session")
def book(synth, tmp_path_factory):
    """(path, truth) of the main fixture book; tests must not modify the file."""
    path = tmp_path_factory.mktemp("synth") / "book.pdf"
    return path, synth.build_book(path)


@pytest.fixture(scope="session")
def mini(synth, tmp_path_factory):
    """(path, truth) of a short book for CLI and decisions tests; copy before changing it."""
    path = tmp_path_factory.mktemp("synth") / "mini.pdf"
    return path, synth.build_mini(path)
