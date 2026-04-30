"""Tests for the first-run setup wizard."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

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


def test_api_key_step_handles_rate_limit_with_friendly_message(tmp_path: Path):
    """When Fireflies returns too_many_requests, render an inline message, not 500."""

    class _RateLimitedRepo:
        def __init__(self, api_key: str) -> None:
            self._api_key = api_key

        async def ping_user(self) -> str:
            raise RuntimeError(
                "graphql: [{'code': 'too_many_requests', 'message': 'retry tomorrow'}]"
            )

    from firefliesclearer.web.app import create_app

    app = create_app(
        session_token="T",
        csrf_secret="S",
        config_path=tmp_path / "config.toml",
        repo_factory=_RateLimitedRepo,  # type: ignore[arg-type]
    )
    c = TestClient(app)
    c.get("/?token=T", follow_redirects=False)
    csrf = c.cookies.get("ffc_csrf", "")

    r = c.post(
        "/setup/api-key",
        data={"_csrf": csrf, "api_key": "ff_anything"},
        follow_redirects=False,
    )
    assert r.status_code == 200, r.text  # NOT 500
    body = r.text.lower()
    assert "rate" in body or "too_many_requests" in body


def test_api_key_step_handles_network_error_with_friendly_message(tmp_path: Path):
    """A generic network exception → ApiUnavailable inline message, not 500."""

    class _NetworkBrokenRepo:
        def __init__(self, api_key: str) -> None:
            self._api_key = api_key

        async def ping_user(self) -> str:
            raise OSError("connection refused")

    from firefliesclearer.web.app import create_app

    app = create_app(
        session_token="T",
        csrf_secret="S",
        config_path=tmp_path / "config.toml",
        repo_factory=_NetworkBrokenRepo,  # type: ignore[arg-type]
    )
    c = TestClient(app)
    c.get("/?token=T", follow_redirects=False)
    csrf = c.cookies.get("ffc_csrf", "")

    r = c.post(
        "/setup/api-key",
        data={"_csrf": csrf, "api_key": "ff_anything"},
        follow_redirects=False,
    )
    assert r.status_code == 200, r.text  # NOT 500
    assert b"could not reach" in r.content.lower() or b"connection" in r.content.lower()


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


# ---------------------------------------------------------------------------
# Coverage gap tests — error branches in setup.py
# ---------------------------------------------------------------------------


def test_service_raises_500_when_repo_factory_is_none(tmp_path: Path):
    """_service() raises 500 when app.state.repo_factory is None."""
    from firefliesclearer.web.app import create_app

    app_no_factory = create_app(
        session_token="T",
        csrf_secret="S",
        config_path=tmp_path / "config.toml",
        repo_factory=None,
    )
    c = TestClient(app_no_factory)
    c.get("/?token=T", follow_redirects=False)
    csrf = c.cookies.get("ffc_csrf", "")

    r = c.post(
        "/setup/api-key",
        data={"_csrf": csrf, "api_key": "ff_any"},
        follow_redirects=False,
    )
    assert r.status_code == 500


def test_archive_root_does_not_exist_without_create_flag(client: TestClient, tmp_path: Path):
    """POST /setup/archive-root with non-existent path and create != 'yes' → error page."""
    csrf = client.cookies["ffc_csrf"]
    non_existent = tmp_path / "does_not_exist"

    r = client.post(
        "/setup/archive-root",
        data={"_csrf": csrf, "archive_root": str(non_existent), "create": ""},
        follow_redirects=False,
    )
    assert r.status_code == 200
    assert "does not exist" in r.text.lower() or "create" in r.text.lower()


def test_archive_root_mkdir_failure_shows_error(client: TestClient, tmp_path: Path):
    """POST /setup/archive-root with create=yes but mkdir raises OSError → error page."""
    csrf = client.cookies["ffc_csrf"]
    non_existent = tmp_path / "will_fail"

    with patch("pathlib.Path.mkdir", side_effect=OSError("permission denied")):
        r = client.post(
            "/setup/archive-root",
            data={"_csrf": csrf, "archive_root": str(non_existent), "create": "yes"},
            follow_redirects=False,
        )
    assert r.status_code == 200
    assert "cannot create" in r.text.lower() or "error" in r.text.lower()


def test_archive_root_file_not_directory_shows_error(client: TestClient, tmp_path: Path):
    """POST /setup/archive-root with a file path (not a dir) → error page."""
    csrf = client.cookies["ffc_csrf"]
    file_path = tmp_path / "a_file.txt"
    file_path.write_text("data")

    r = client.post(
        "/setup/archive-root",
        data={"_csrf": csrf, "archive_root": str(file_path), "create": ""},
        follow_redirects=False,
    )
    assert r.status_code == 200
    assert "not a directory" in r.text.lower() or "error" in r.text.lower()


def test_archive_root_non_writable_shows_error(client: TestClient, tmp_path: Path):
    """POST /setup/archive-root with non-writable dir → error page."""
    csrf = client.cookies["ffc_csrf"]
    new_root = tmp_path / "locked"
    new_root.mkdir()

    with patch(
        "firefliesclearer.web.routes.setup._is_writable",
        return_value=False,
    ):
        r = client.post(
            "/setup/archive-root",
            data={"_csrf": csrf, "archive_root": str(new_root), "create": ""},
            follow_redirects=False,
        )
    assert r.status_code == 200
    assert "writable" in r.text.lower() or "error" in r.text.lower()


def test_defaults_submit_missing_session_state_raises_400(client: TestClient, tmp_path: Path):
    """POST /setup/defaults without completing prior steps → 400 (session state lost)."""
    csrf = client.cookies["ffc_csrf"]

    r = client.post(
        "/setup/defaults",
        data={"_csrf": csrf, "age_days": "90", "concurrency": "3"},
        follow_redirects=False,
    )
    assert r.status_code == 400


def test_defaults_submit_missing_config_path_raises_500(tmp_path: Path):
    """POST /setup/defaults when app.state.config_path is None → 500."""
    from firefliesclearer.web.app import create_app

    def repo_factory(key: str) -> InMemoryMeetingRepository:
        repo = InMemoryMeetingRepository(api_key=key)
        repo.set_user_email_for_key("ff_good", "oskar@example.com")
        return repo

    app_no_cfg = create_app(
        session_token="T",
        csrf_secret="S",
        config_path=None,
        repo_factory=repo_factory,
    )
    c = TestClient(app_no_cfg)
    c.get("/?token=T", follow_redirects=False)
    csrf = c.cookies.get("ffc_csrf", "")

    # Complete api-key and archive-root steps to populate session
    c.post("/setup/api-key", data={"_csrf": csrf, "api_key": "ff_good"})
    c.post(
        "/setup/archive-root",
        data={"_csrf": csrf, "archive_root": str(tmp_path / "archive"), "create": "yes"},
    )
    r = c.post(
        "/setup/defaults",
        data={"_csrf": csrf, "age_days": "90", "concurrency": "3"},
        follow_redirects=False,
    )
    assert r.status_code == 500


def test_is_writable_returns_false_on_oserror(tmp_path: Path):
    """_is_writable in setup.py returns False when write_text raises OSError."""
    from firefliesclearer.web.routes.setup import _is_writable

    with patch("pathlib.Path.write_text", side_effect=OSError("read-only")):
        result = _is_writable(tmp_path)
    assert result is False


def test_api_key_form_renders_with_error_query_param(client: TestClient):
    """GET /setup/api-key?error=some+msg → 200 with error text in page (line 59)."""
    r = client.get("/setup/api-key?error=some+error+message")
    assert r.status_code == 200
    assert "some error message" in r.text.lower() or "error" in r.text.lower()
