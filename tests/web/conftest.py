from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from firefliesclearer.web.app import create_app


@pytest.fixture
def web_token() -> str:
    return "TESTTOKEN"


@pytest.fixture
def app(web_token: str):
    return create_app(session_token=web_token, csrf_secret="csrfsecret")


@pytest.fixture
def client(app, web_token: str) -> TestClient:
    c = TestClient(app)
    # Establish session by hitting any GET with token
    c.get("/?token=" + web_token)
    return c
