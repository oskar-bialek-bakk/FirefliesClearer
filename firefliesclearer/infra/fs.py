"""Filesystem helpers: slug, canonical paths, atomic writes, hashing."""

from __future__ import annotations

import hashlib
import os
import re
import unicodedata
from datetime import datetime
from pathlib import Path

_NON_ALNUM = re.compile(r"[^a-z0-9]+")

# Latin characters that NFKD does not decompose (precomposed, no canonical
# decomposition). Map them to ASCII equivalents before NFKD folding.
_EXTRA_FOLDS = str.maketrans(
    {
        "ł": "l",
        "Ł": "L",
        "ø": "o",
        "Ø": "O",
        "æ": "ae",
        "Æ": "AE",
        "œ": "oe",
        "Œ": "OE",
        "ß": "ss",
        "đ": "d",
        "Đ": "D",
        "ð": "d",
        "Ð": "D",
        "þ": "th",
        "Þ": "Th",
    }
)


def slugify(text: str, *, max_length: int = 60) -> str:
    """ASCII-fold + lowercase + non-alnum->'-' + trim + truncate."""
    if not text:
        return "untitled"
    pre_folded = text.translate(_EXTRA_FOLDS)
    normalized = unicodedata.normalize("NFKD", pre_folded)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    lowered = ascii_only.lower()
    dashed = _NON_ALNUM.sub("-", lowered).strip("-")
    if not dashed:
        return "untitled"
    return dashed[:max_length].rstrip("-") or "untitled"


def canonical_meeting_dir(
    *,
    archive_root: Path,
    meeting_id: str,
    title: str,
    meeting_date: datetime,
) -> Path:
    yyyy = f"{meeting_date.year:04d}"
    mm = f"{meeting_date.month:02d}"
    day = meeting_date.strftime("%Y-%m-%d")
    name = f"{day}_{slugify(title)}_{meeting_id}"
    return archive_root / "archive" / yyyy / mm / name


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write to <path>.tmp then os.replace into place."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def sha256_file(path: Path, *, chunk_size: int = 65536) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(chunk_size):
            h.update(chunk)
    return h.hexdigest()
