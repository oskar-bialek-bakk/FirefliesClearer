# firefliesclearer/web/lockfile.py
"""Single-instance enforcement via a platform-appropriate lockfile.

Uses fcntl.flock on POSIX, msvcrt.locking on Windows.

On Windows, msvcrt.locking prevents reads of locked byte ranges, so the
served URL is stored in a companion sidecar file (<path>.url) that is
written after acquiring the lock and deleted on release.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path


class AnotherInstanceRunningError(RuntimeError):
    """Another `serve` process holds the lockfile."""


class LockFile:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._fd: int | None = None
        self._locked: bool = False

    @property
    def _url_path(self) -> Path:
        return self._path.with_suffix(self._path.suffix + ".url")

    @contextmanager
    def acquire(self, *, url: str) -> Iterator[None]:
        existing_url = self._read_existing_url()
        try:
            self._fd = os.open(self._path, os.O_CREAT | os.O_RDWR, 0o600)
        except OSError as exc:
            raise AnotherInstanceRunningError(
                f"Cannot create lockfile at {self._path}: {exc}"
            ) from exc

        try:
            self._lock_exclusive_or_raise(existing_url)
            self._locked = True
            self._url_path.write_text(url, encoding="utf-8")
            yield
        finally:
            self._release()

    def _lock_exclusive_or_raise(self, existing_url: str | None) -> None:
        assert self._fd is not None
        if sys.platform == "win32":
            import msvcrt

            try:
                msvcrt.locking(self._fd, msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise AnotherInstanceRunningError(
                    f"Another instance is running. {self._hint(existing_url)}"
                ) from exc
        else:
            import fcntl

            try:
                fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise AnotherInstanceRunningError(
                    f"Another instance is running. {self._hint(existing_url)}"
                ) from exc

    def _read_existing_url(self) -> str | None:
        if sys.platform == "win32":
            # The lockfile itself is unreadable while locked on Windows;
            # read from the companion sidecar file instead.
            url_file = self._url_path
            if not url_file.exists():
                return None
            try:
                return url_file.read_text(encoding="utf-8").strip() or None
            except OSError:
                return None
        else:
            if not self._path.exists():
                return None
            try:
                return self._path.read_text(encoding="utf-8").strip() or None
            except OSError:
                return None

    @staticmethod
    def _hint(url: str | None) -> str:
        if url:
            return f"Open {url} or stop the running instance first."
        return "Stop the running instance first."

    def _release(self) -> None:
        if self._fd is None:
            return
        locked = self._locked
        self._locked = False
        try:
            if sys.platform == "win32":
                import msvcrt

                if locked:
                    with suppress(OSError):
                        os.lseek(self._fd, 0, os.SEEK_SET)
                        msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                with suppress(OSError):
                    fcntl.flock(self._fd, fcntl.LOCK_UN)
        finally:
            with suppress(OSError):
                os.close(self._fd)
            self._fd = None
            if locked:
                with suppress(FileNotFoundError, PermissionError):
                    self._url_path.unlink()
                with suppress(FileNotFoundError, PermissionError):
                    self._path.unlink()
