"""Tests for the first-run setup wizard."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from firefliesclearer.web.app import create_app
from tests.fakes.in_memory_repository import InMemoryMeetingRepository


@pytest.fixture
def app_no_config(tmp_path: Path):
    def repo_factory(key: str) -> InMemoryMeetingRepository:
        # Build a fresh repo bound to *key*; pre-register the email pair
        # so ping_user("ff_good") succeeds inside InMemoryMeetingRepository.
        repo = InMemoryMeetingRepository(api_key=key)
        repo.set_user_email_for_key("ff_good", "oskar@example.com")
        return repo

    return create_app(
        session_token="T",
        csrf_secret="S",
        config_path=tmp_path / "config.toml",
        repo_factory=repo_factory,
    )


@pytest.fixture
def client(app_no_config) -> TestClient:
    c = TestClient(app_no_config)
    # Establish session token + CSRF cookie. follow_redirects=False so the
    # 303 to /setup/welcome doesn't cascade into another request.
    c.get("/?token=T", follow_redirects=False)
    return c


def test_root_redirects_to_setup_when_no_config(client: TestClient):
    r = client.get("/", follow_redirects=False)
    assert r.status_code in (302, 303, 307)
    assert "/setup/welcome" in r.headers["location"]


def test_setup_welcome_renders(client: TestClient):
    r = client.get("/setup/welcome")
    assert r.status_code == 200
    assert b"FirefliesClearer needs three things" in r.content


def test_api_key_step_rejects_bad_key(client: TestClient):
    csrf = client.cookies["ffc_csrf"]
    r = client.post(
        "/setup/api-key",
        data={"_csrf": csrf, "api_key": "ff_bad"},
        follow_redirects=False,
    )
    assert r.status_code == 200  # re-renders form with error
    assert b"rejected" in r.content


def test_api_key_step_accepts_good_key_and_advances(client: TestClient):
    csrf = client.cookies["ffc_csrf"]
    r = client.post(
        "/setup/api-key",
        data={"_csrf": csrf, "api_key": "ff_good"},
        follow_redirects=False,
    )
    assert r.status_code in (302, 303, 307)
    assert "/setup/archive-root" in r.headers["location"]


def test_finish_writes_config_and_redirects_home(client: TestClient, tmp_path: Path):
    csrf = client.cookies["ffc_csrf"]
    client.post("/setup/api-key", data={"_csrf": csrf, "api_key": "ff_good"})
    client.post(
        "/setup/archive-root",
        data={"_csrf": csrf, "archive_root": str(tmp_path / "archive"), "create": "yes"},
    )
    r = client.post(
        "/setup/defaults",
        data={"_csrf": csrf, "age_days": "90", "concurrency": "3"},
        follow_redirects=False,
    )
    assert r.status_code in (302, 303, 307)
    assert r.headers["location"] == "/"
    assert (tmp_path / "config.toml").exists()
