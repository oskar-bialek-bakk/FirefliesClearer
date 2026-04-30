"""Tests for infra/log_retention.py."""

from __future__ import annotations

import time
from pathlib import Path

from firefliesclearer.infra.log_retention import sweep_old_logs


def _mtime_days_ago(path: Path, days: float) -> None:
    """Set mtime of *path* to *days* ago."""
    t = time.time() - days * 86400
    import os

    os.utime(path, (t, t))


def test_sweep_deletes_old_logs(tmp_path: Path) -> None:
    """Files older than retention_days are deleted; recent files survive."""
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()

    old_log = logs_dir / "2020-01-01.log"
    old_log.write_text("old")
    _mtime_days_ago(old_log, 40)

    recent_log = logs_dir / "2024-12-31.log"
    recent_log.write_text("recent")
    _mtime_days_ago(recent_log, 5)

    deleted = sweep_old_logs(tmp_path, retention_days=30)

    assert deleted == 1
    assert not old_log.exists()
    assert recent_log.exists()


def test_sweep_handles_missing_logs_dir(tmp_path: Path) -> None:
    """No logs/ directory → returns 0, no error."""
    result = sweep_old_logs(tmp_path, retention_days=30)
    assert result == 0


def test_sweep_returns_zero_when_all_recent(tmp_path: Path) -> None:
    """All files recent → nothing deleted, returns 0."""
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    f = logs_dir / "today.log"
    f.write_text("fresh")
    _mtime_days_ago(f, 1)

    result = sweep_old_logs(tmp_path, retention_days=30)
    assert result == 0
    assert f.exists()


def test_sweep_ignores_non_log_files(tmp_path: Path) -> None:
    """Non-*.log files are not deleted even if old."""
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    txt = logs_dir / "2020-01-01.txt"
    txt.write_text("ignore me")
    _mtime_days_ago(txt, 100)

    result = sweep_old_logs(tmp_path, retention_days=30)
    assert result == 0
    assert txt.exists()


def test_sweep_multiple_old_logs(tmp_path: Path) -> None:
    """Multiple old logs are all deleted; returns correct count."""
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    for i in range(3):
        f = logs_dir / f"2020-0{i + 1}-01.log"
        f.write_text("old")
        _mtime_days_ago(f, 60)

    result = sweep_old_logs(tmp_path, retention_days=30)
    assert result == 3
