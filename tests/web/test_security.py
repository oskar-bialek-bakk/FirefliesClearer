"""Tests for session-token + CSRF middleware."""

from __future__ import annotations

from fastapi import FastAPI, Response
from fastapi.testclient import TestClient

from firefliesclearer.web.security import SecurityConfig, install_security


def make_app(token: str = "TOKEN") -> FastAPI:
    app = FastAPI()
    install_security(app, SecurityConfig(session_token=token, csrf_secret="csrf-secret"))

    @app.get("/safe")
    def safe():
        return {"ok": True}

    @app.post("/mutate")
    def mutate():
        return {"ok": True}

    return app


def test_get_without_token_returns_401():
    client = TestClient(make_app(), raise_server_exceptions=False)
    response = client.get("/safe")
    assert response.status_code == 401


def test_get_with_query_token_sets_cookie_and_returns_200():
    client = TestClient(make_app(), raise_server_exceptions=False)
    response = client.get("/safe?token=TOKEN")
    assert response.status_code == 200
    assert "ffc_session" in response.cookies


def test_get_with_cookie_works_after_initial_handshake():
    client = TestClient(make_app(), raise_server_exceptions=False)
    # Initial handshake sets ffc_session cookie in client jar
    handshake = client.get("/safe?token=TOKEN")
    assert handshake.status_code == 200
    # Subsequent request uses cookie only
    response = client.get("/safe")
    assert response.status_code == 200


def test_post_without_csrf_returns_403():
    client = TestClient(make_app(), raise_server_exceptions=False)
    # Establish session first
    client.get("/safe?token=TOKEN")
    # POST with no _csrf field
    response = client.post("/mutate")
    assert response.status_code == 403


def test_post_with_csrf_cookie_and_form_field_returns_200():
    client = TestClient(make_app(), raise_server_exceptions=False)
    # Establish session; GET sets ffc_csrf cookie
    client.get("/safe?token=TOKEN")
    csrf_value = client.cookies["ffc_csrf"]
    # POST with matching _csrf field
    response = client.post("/mutate", data={"_csrf": csrf_value})
    assert response.status_code == 200


def test_post_with_mismatched_csrf_returns_403():
    client = TestClient(make_app(), raise_server_exceptions=False)
    # Establish session; GET sets ffc_csrf cookie
    client.get("/safe?token=TOKEN")
    # POST with wrong _csrf value
    response = client.post("/mutate", data={"_csrf": "wrong"})
    assert response.status_code == 403


def test_static_paths_bypass_session_and_csrf():
    """Both middlewares should let `/static/*` through without auth or CSRF."""
    app = make_app()

    @app.get("/static/test.css")
    def static_test():
        return Response(content="body { color: red; }", media_type="text/css")

    client = TestClient(app)
    # No token, no cookies — should not get 401/403
    r = client.get("/static/test.css")
    assert r.status_code == 200
    assert "color: red" in r.text


def test_post_with_tampered_csrf_cookie_returns_403():
    """A cookie with valid format but invalid signature must be rejected."""
    app = make_app()
    client = TestClient(app, raise_server_exceptions=False)
    # Establish session.
    client.get("/safe?token=TOKEN")
    # Replace the validly-signed CSRF cookie with a bogus one.
    client.cookies.set("ffc_csrf", "tampered.invalidsignature")
    r = client.post("/mutate", data={"_csrf": "tampered.invalidsignature"})
    assert r.status_code == 403


def test_stale_csrf_cookie_is_rotated_on_safe_get():
    """A cookie signed under an old secret must be replaced silently on GET.

    Regression: when the server restarts, csrf_secret rolls. A browser tab that
    still holds the previous cookie used to keep failing every POST with
    'CSRF cookie invalid' until the user manually cleared cookies. Now, any
    safe GET rotates the stale cookie so the next POST works.
    """
    # Server boots with secret "old", browser gets a cookie.
    app_old = make_app()
    client = TestClient(app_old, raise_server_exceptions=False)
    client.get("/safe?token=TOKEN")
    stale_cookie = client.cookies["ffc_csrf"]
    assert stale_cookie  # sanity

    # Server "restarts" with a new secret. The browser still has the old cookie.
    app_new = FastAPI()
    install_security(app_new, SecurityConfig(session_token="TOKEN", csrf_secret="DIFFERENT"))

    @app_new.get("/safe")
    def safe() -> dict[str, bool]:
        return {"ok": True}

    @app_new.post("/mutate")
    def mutate() -> dict[str, bool]:
        return {"ok": True}

    client_new = TestClient(app_new, raise_server_exceptions=False)
    client_new.cookies.set("ffc_csrf", stale_cookie)
    client_new.cookies.set("ffc_session", "TOKEN")

    # Plain GET should rotate the stale cookie automatically.
    r = client_new.get("/safe")
    assert r.status_code == 200
    # Read from the response (not client jar) — httpx may keep the stale
    # cookie alongside the new one in the jar, which raises CookieConflict.
    rotated = r.cookies["ffc_csrf"]
    assert rotated != stale_cookie  # rotated to a new signature

    # Subsequent POST with the rotated cookie + matching field works. Send
    # only the rotated cookie to avoid jar conflict.
    r = client_new.post(
        "/mutate",
        data={"_csrf": rotated},
        cookies={"ffc_csrf": rotated, "ffc_session": "TOKEN"},
    )
    assert r.status_code == 200


def test_stale_csrf_cookie_on_post_returns_403_with_friendly_message_and_rotates():
    """If the user POSTs with a stale cookie before any GET rotates it, return
    403 with a clear message AND set a fresh cookie so the retry works.
    """
    # Boot, get a cookie.
    app_old = make_app()
    client = TestClient(app_old, raise_server_exceptions=False)
    client.get("/safe?token=TOKEN")
    stale_cookie = client.cookies["ffc_csrf"]

    # Restart with new secret.
    app_new = FastAPI()
    install_security(app_new, SecurityConfig(session_token="TOKEN", csrf_secret="DIFFERENT"))

    @app_new.post("/mutate")
    def mutate() -> dict[str, bool]:
        return {"ok": True}

    client_new = TestClient(app_new, raise_server_exceptions=False)
    client_new.cookies.set("ffc_csrf", stale_cookie)
    client_new.cookies.set("ffc_session", "TOKEN")

    r = client_new.post("/mutate", data={"_csrf": stale_cookie})
    assert r.status_code == 403
    assert b"expired" in r.content.lower() or b"server may have restarted" in r.content.lower()
    # The 403 response carries a fresh cookie so the next attempt succeeds.
    rotated = r.cookies["ffc_csrf"]
    assert rotated != stale_cookie
