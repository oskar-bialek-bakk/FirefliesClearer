"""Tests for app-level state initialisation."""

from __future__ import annotations

import asyncio

from firefliesclearer.web.app import create_app


def test_create_app_initialises_sync_state_holders() -> None:
    app = create_app(session_token="T", csrf_secret="S")
    assert hasattr(app.state, "sync_lock")
    assert isinstance(app.state.sync_lock, asyncio.Lock)
    assert hasattr(app.state, "current_sync")
    assert app.state.current_sync is None  # nothing running yet
