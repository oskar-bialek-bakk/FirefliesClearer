"""Tests for the single-instance lockfile."""

from __future__ import annotations

import sys
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
    if sys.platform == "win32":
        assert not (tmp_path / ".serve.lock.url").exists()


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


def test_failed_second_acquire_does_not_delete_first_instances_files(tmp_path: Path):
    """The defensive _locked flag must prevent the failed second acquire's
    cleanup from deleting the first instance's lockfile or sidecar."""
    lock1 = LockFile(tmp_path / ".serve.lock")
    lock2 = LockFile(tmp_path / ".serve.lock")
    with lock1.acquire(url="http://127.0.0.1:54231"):
        with pytest.raises(AnotherInstanceRunningError):  # noqa: SIM117
            with lock2.acquire(url="http://127.0.0.1:54232"):
                pass
        # First instance's lockfile must still exist after lock2's failed acquire.
        assert (tmp_path / ".serve.lock").exists()
        if sys.platform == "win32":
            assert (tmp_path / ".serve.lock.url").exists()
