"""Log retention sweep: delete *.log files older than N days."""

from __future__ import annotations

import time
from pathlib import Path


def sweep_old_logs(archive_root: Path, retention_days: int) -> int:
    """Delete ``*.log`` files in ``<archive_root>/logs/`` older than *retention_days*.

    Parameters
    ----------
    archive_root:
        Root of the archive directory (the logs sub-folder is ``logs/``).
    retention_days:
        Files whose mtime is older than this many days are deleted.

    Returns
    -------
    int
        Number of files deleted.  Returns 0 without error if the logs
        directory does not exist.
    """
    logs_dir = archive_root / "logs"
    if not logs_dir.is_dir():
        return 0

    cutoff = time.time() - retention_days * 86400
    deleted = 0
    for log_file in logs_dir.glob("*.log"):
        try:
            if log_file.stat().st_mtime < cutoff:
                log_file.unlink(missing_ok=True)
                deleted += 1
        except OSError:
            # Best-effort: skip files we can't stat or delete.
            pass
    return deleted
