"""Tests for the single-instance lockfile."""

from __future__ import annotations

from pathlib import Path

import pytest

from firefliesclearer.web.lockfile import (
    AnotherInstanceRunningError,
    LockFile,
)


def test_acquire_creates_lockfile(tmp_path: Path):
    lock = LockFile(tmp_path / ".serve.lock")
    with lock.acquire(url="http://127.0.0.1:54231"):
        assert (tmp_path / ".serve.lock").exists()
    assert not (tmp_path / ".serve.lock").exists()


def test_second_acquire_raises(tmp_path: Path):
    lock1 = LockFile(tmp_path / ".serve.lock")
    lock2 = LockFile(tmp_path / ".serve.lock")
    with lock1.acquire(url="http://127.0.0.1:54231"):
        with pytest.raises(AnotherInstanceRunningError) as exc_info:  # noqa: SIM117
            with lock2.acquire(url="http://127.0.0.1:54232"):
                pass
        assert "http://127.0.0.1:54231" in str(exc_info.value)


def test_acquire_after_release_succeeds(tmp_path: Path):
    lock = LockFile(tmp_path / ".serve.lock")
    with lock.acquire(url="http://127.0.0.1:54231"):
        pass
    with lock.acquire(url="http://127.0.0.1:54232"):
        pass  # should not raise
