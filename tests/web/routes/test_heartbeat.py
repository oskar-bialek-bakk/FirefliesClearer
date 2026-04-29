"""Tests for POST /_alive and POST /_quit routes."""

from __future__ import annotations


def test_alive_pings_tracker(app, client):
    csrf = client.cookies.get("ffc_csrf")
    initial = app.state.tracker.last_seen()

    r = client.post("/_alive", data={"_csrf": csrf})

    assert r.status_code == 204
    # tracker.last_seen() should be >= initial; with SystemClock it bumps forward.
    assert app.state.tracker.last_seen() >= initial


def test_quit_requests_shutdown(app, client):
    csrf = client.cookies.get("ffc_csrf")
    r = client.post("/_quit", data={"_csrf": csrf})
    assert r.status_code == 204
    # Internal flag set
    assert app.state.shutdown_coordinator._quit_requested is True
