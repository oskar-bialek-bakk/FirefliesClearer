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
