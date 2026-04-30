"""Cross-platform helper to open a folder in the system file explorer."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def open_folder(path: Path) -> None:
    """Open *path* in the platform's default file explorer.

    Uses a list argument (no ``shell=True``) so no shell injection is
    possible.  The caller is responsible for only passing a trusted,
    config-derived path — never user input.

    Parameters
    ----------
    path:
        Directory to open. The path is converted to a string and passed as
        the sole argument to the platform command.
    """
    if sys.platform == "win32":
        subprocess.run(["explorer.exe", str(path)], check=False)
    elif sys.platform == "darwin":
        subprocess.run(["open", str(path)], check=False)
    else:
        subprocess.run(["xdg-open", str(path)], check=False)
