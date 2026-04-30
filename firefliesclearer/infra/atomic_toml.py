"""Atomic TOML write helper: temp-file + fsync + rename pattern."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import tomli_w


def write_atomic_toml(path: Path, payload: dict[str, Any]) -> None:
    """Write *payload* to *path* atomically using a temp-file + fsync + rename.

    The write is atomic on POSIX (``os.rename`` is atomic when source and
    destination are on the same filesystem).  On Windows, ``Path.replace``
    falls back to a non-atomic copy-then-delete but still ensures no partial
    writes are visible.

    Parameters
    ----------
    path:
        Destination file path. Parent directory must already exist.
    payload:
        A ``dict`` compatible with ``tomli_w.dump``.  All values must be
        TOML-serialisable (str, int, float, bool, datetime, list, dict).

    Raises
    ------
    Exception
        Any I/O error from ``tomli_w.dump``, ``os.fsync``, or the rename step.
        The temp file is cleaned up before re-raising.
    """
    tmp = Path(str(path) + ".tmp")
    try:
        with open(tmp, "wb") as f:
            tomli_w.dump(payload, f)
            f.flush()
            os.fsync(f.fileno())
        tmp.replace(path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
