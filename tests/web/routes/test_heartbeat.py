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


def test_alive_with_urlencoded_csrf_returns_204(app, client):
    """Heartbeat ping with application/x-www-form-urlencoded works (production path)."""
    csrf = client.cookies["ffc_csrf"]
    r = client.post(
        "/_alive",
        content=f"_csrf={csrf}",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert r.status_code == 204


def test_alive_with_multipart_csrf_returns_403(app, client):
    """Heartbeat ping with multipart/form-data is rejected — the CSRF middleware
    only parses urlencoded bodies. Documents the contract that app.js must
    send urlencoded (via Blob with explicit Content-Type), NOT FormData.
    """
    csrf = client.cookies["ffc_csrf"]
    r = client.post("/_alive", files={"_csrf": (None, csrf)})
    # files= triggers multipart/form-data in TestClient
    assert r.status_code == 403
