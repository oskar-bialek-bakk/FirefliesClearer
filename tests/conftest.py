"""Shared pytest fixtures."""

from __future__ import annotations

import pytest


@pytest.fixture
def tmp_archive_root(tmp_path):
    """An archive root inside tmp_path."""
    root = tmp_path / "archive"
    root.mkdir()
    return root
