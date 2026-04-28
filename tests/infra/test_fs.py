"""Tests for filesystem helpers (slug, paths, atomic write, hashing)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from firefliesclearer.infra.fs import (
    atomic_write_bytes,
    canonical_meeting_dir,
    sha256_file,
    slugify,
)


@pytest.mark.parametrize(
    "title,expected_prefix",
    [
        ("Weekly Standup", "weekly-standup"),
        ("[Draft] Q2 / Plans", "draft-q2-plans"),
        ("  Spaces   everywhere  ", "spaces-everywhere"),
        ("Zażółć gęślą jaźń", "zazolc-gesla-jazn"),
        ("", "untitled"),
        ("---", "untitled"),
    ],
)
def test_slugify(title: str, expected_prefix: str) -> None:
    assert slugify(title) == expected_prefix


def test_slugify_truncates_to_60_chars() -> None:
    long = "a" * 200
    assert len(slugify(long)) == 60


def test_canonical_meeting_dir_layout(tmp_path: Path) -> None:
    date = datetime(2026, 4, 12, 9, 0, tzinfo=UTC)
    path = canonical_meeting_dir(
        archive_root=tmp_path,
        meeting_id="01HW",
        title="Kickoff Marketing Q2",
        meeting_date=date,
    )
    assert path == tmp_path / "archive" / "2026" / "04" / (
        "2026-04-12_kickoff-marketing-q2_01HW"
    )


def test_atomic_write_bytes_writes_file(tmp_path: Path) -> None:
    target = tmp_path / "out.bin"
    atomic_write_bytes(target, b"hello")
    assert target.read_bytes() == b"hello"


def test_atomic_write_bytes_replaces_existing(tmp_path: Path) -> None:
    target = tmp_path / "out.bin"
    target.write_bytes(b"old")
    atomic_write_bytes(target, b"new")
    assert target.read_bytes() == b"new"


def test_atomic_write_bytes_does_not_leave_tmp_on_success(tmp_path: Path) -> None:
    target = tmp_path / "sub" / "out.bin"
    atomic_write_bytes(target, b"x")
    leftover = list(tmp_path.rglob("*.tmp"))
    assert leftover == []


def test_sha256_file(tmp_path: Path) -> None:
    f = tmp_path / "x.bin"
    f.write_bytes(b"hello")
    assert sha256_file(f) == (
        "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
    )
