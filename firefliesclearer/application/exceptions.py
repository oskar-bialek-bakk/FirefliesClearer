"""Shared application-layer exceptions for FirefliesClearer."""

from __future__ import annotations


class SelectionMissing(FileNotFoundError):  # noqa: N818
    """Selection file does not exist."""
