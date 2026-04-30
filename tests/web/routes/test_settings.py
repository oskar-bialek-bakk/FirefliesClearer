"""Tests for the /settings page (Phase 8 — Task 31)."""

from __future__ import annotations

import tomllib
from datetime import date
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from firefliesclearer.application.setup_service import InvalidApiKeyError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_client(configured_app: Any) -> TestClient:
    c = TestClient(configured_app)
    c.get("/?token=T")
    return c


def _csrf(client: TestClient) -> str:
    return client.cookies.get("ffc_csrf", "")


def _read_toml(path: Path) -> dict[str, Any]:
    with open(path, "rb") as f:
        return tomllib.load(f)


# ---------------------------------------------------------------------------
# Section visibility / structure
# ---------------------------------------------------------------------------


def test_get_settings_renders_all_sections(configured_app: Any) -> None:
    """GET /settings → 200, contains all 6 section headings."""
    c = _make_client(configured_app)
    r = c.get("/settings")
    assert r.status_code == 200
    body = r.text.lower()
    assert "connection" in body
    assert "archive" in body
    assert "defaults" in body
    assert "presets" in body
    assert "logs" in body
    assert "danger" in body


def test_get_settings_api_key_masked(configured_app: Any) -> None:
    """API key input is type=password and its value is not echoed in response."""
    c = _make_client(configured_app)
    r = c.get("/settings")
    assert r.status_code == 200
    assert 'type="password"' in r.text
    # Actual key value must not appear in the response text
    assert "ff_test" not in r.text


# ---------------------------------------------------------------------------
# Connection: test endpoint
# ---------------------------------------------------------------------------


def test_post_connection_test_success(configured_app: Any) -> None:
    """POST /settings/connection/test with a good key → fragment shows email."""
    c = _make_client(configured_app)
    csrf = _csrf(c)

    with patch(
        "firefliesclearer.web.routes.settings.SetupService.verify_api_key",
        new=AsyncMock(return_value="oskar@example.com"),
    ):
        r = c.post(
            "/settings/connection/test",
            data={"_csrf": csrf, "api_key": "ff_good"},
        )
    assert r.status_code == 200
    assert "oskar@example.com" in r.text
    assert "connected" in r.text.lower()


def test_post_connection_test_failure(configured_app: Any) -> None:
    """POST /settings/connection/test with bad key → fragment shows error."""
    c = _make_client(configured_app)
    csrf = _csrf(c)

    with patch(
        "firefliesclearer.web.routes.settings.SetupService.verify_api_key",
        new=AsyncMock(side_effect=InvalidApiKeyError("bad key")),
    ):
        r = c.post(
            "/settings/connection/test",
            data={"_csrf": csrf, "api_key": "ff_bad"},
        )
    assert r.status_code == 200
    assert "failed" in r.text.lower() or "error" in r.text.lower()


# ---------------------------------------------------------------------------
# Connection: save
# ---------------------------------------------------------------------------


def test_post_connection_saves_new_key(configured_app: Any) -> None:
    """POST /settings/connection with valid key → config.toml updated."""
    c = _make_client(configured_app)
    csrf = _csrf(c)
    cfg_path: Path = configured_app.state.config_path

    with patch(
        "firefliesclearer.web.routes.settings.SetupService.verify_api_key",
        new=AsyncMock(return_value="new@example.com"),
    ):
        r = c.post(
            "/settings/connection",
            data={"_csrf": csrf, "api_key": "ff_new_key"},
        )
    assert r.status_code == 200
    raw = _read_toml(cfg_path)
    assert raw["fireflies"]["api_key"] == "ff_new_key"


# ---------------------------------------------------------------------------
# Archive
# ---------------------------------------------------------------------------


def test_post_archive_changes_root(configured_app: Any, tmp_path: Path) -> None:
    """POST new path → config updated; old path with content triggers banner."""
    c = _make_client(configured_app)
    csrf = _csrf(c)
    cfg_path: Path = configured_app.state.config_path

    # Seed a file in the current archive root so move-warning fires
    raw = _read_toml(cfg_path)
    old_root = Path(raw["archive"]["root_dir"])
    (old_root / "some_file.txt").write_text("data")

    new_root = tmp_path / "new_archive"
    new_root.mkdir()

    r = c.post(
        "/settings/archive",
        data={"_csrf": csrf, "root_dir": str(new_root)},
    )
    assert r.status_code == 200
    updated = _read_toml(cfg_path)
    assert updated["archive"]["root_dir"] == str(new_root)
    assert "NOT moved" in r.text or "not moved" in r.text.lower()


def test_post_archive_invalid_path_shows_error(configured_app: Any, tmp_path: Path) -> None:
    """Non-existent path without create checkbox → 200 with inline error."""
    c = _make_client(configured_app)
    csrf = _csrf(c)

    non_existent = tmp_path / "does_not_exist"
    r = c.post(
        "/settings/archive",
        data={"_csrf": csrf, "root_dir": str(non_existent)},
    )
    assert r.status_code == 200
    assert "does not exist" in r.text.lower() or "error" in r.text.lower()


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------


def test_post_defaults_saves_concurrency_threshold(configured_app: Any) -> None:
    """POST valid defaults → saved to config."""
    c = _make_client(configured_app)
    csrf = _csrf(c)
    cfg_path: Path = configured_app.state.config_path

    r = c.post(
        "/settings/defaults",
        data={
            "_csrf": csrf,
            "concurrency": "5",
            "default_age_days": "90",
            "delete_confirmation_threshold": "20",
        },
    )
    assert r.status_code == 200
    raw = _read_toml(cfg_path)
    assert raw["run"]["concurrency"] == 5
    assert raw["run"]["default_age_days"] == 90
    assert raw["run"]["delete_confirmation_threshold"] == 20


def test_post_defaults_invalid_concurrency_rejected(configured_app: Any) -> None:
    """Concurrency=0 → 200 with validation error; config unchanged."""
    c = _make_client(configured_app)
    csrf = _csrf(c)
    cfg_path: Path = configured_app.state.config_path

    r = c.post(
        "/settings/defaults",
        data={
            "_csrf": csrf,
            "concurrency": "0",
            "default_age_days": "90",
            "delete_confirmation_threshold": "10",
        },
    )
    assert r.status_code == 200
    assert "concurrency" in r.text.lower() or "error" in r.text.lower()
    # Config concurrency should not have been updated to 0
    raw = _read_toml(cfg_path)
    assert raw["run"].get("concurrency", 3) != 0


def test_post_defaults_concurrency_11_rejected(configured_app: Any) -> None:
    """Concurrency=11 → 200 with validation error."""
    c = _make_client(configured_app)
    csrf = _csrf(c)

    r = c.post(
        "/settings/defaults",
        data={
            "_csrf": csrf,
            "concurrency": "11",
            "default_age_days": "90",
            "delete_confirmation_threshold": "10",
        },
    )
    assert r.status_code == 200
    assert "concurrency" in r.text.lower() or "1 and 10" in r.text.lower()


# ---------------------------------------------------------------------------
# Logs retention
# ---------------------------------------------------------------------------


def test_post_logs_retention_saves(configured_app: Any) -> None:
    """POST log_retention_days=14 → saved to config."""
    c = _make_client(configured_app)
    csrf = _csrf(c)
    cfg_path: Path = configured_app.state.config_path

    r = c.post(
        "/settings/logs/retention",
        data={"_csrf": csrf, "log_retention_days": "14"},
    )
    assert r.status_code == 200
    raw = _read_toml(cfg_path)
    assert raw["run"]["log_retention_days"] == 14


# ---------------------------------------------------------------------------
# Log viewer fragment
# ---------------------------------------------------------------------------


def test_get_logs_today_renders_existing_log(configured_app: Any, tmp_path: Path) -> None:
    """Seed today's log file → fragment renders its content."""
    cfg_path: Path = configured_app.state.config_path
    raw = _read_toml(cfg_path)
    archive_root = Path(raw["archive"]["root_dir"])
    logs_dir = archive_root / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    log_file = logs_dir / f"{today}.log"
    log_file.write_text('{"event":"test","level":"INFO"}\n', encoding="utf-8")

    c = _make_client(configured_app)
    r = c.get("/settings/logs/today")
    assert r.status_code == 200
    assert "test" in r.text


def test_get_logs_today_empty_state_when_missing(configured_app: Any) -> None:
    """No log file for today → fragment shows empty-state message."""
    c = _make_client(configured_app)
    r = c.get("/settings/logs/today")
    assert r.status_code == 200
    assert "no log" in r.text.lower()


# ---------------------------------------------------------------------------
# Open archive folder
# ---------------------------------------------------------------------------


def test_post_open_archive_folder_calls_subprocess(configured_app: Any) -> None:
    """open-archive-folder shells out with list args (no shell=True)."""
    c = _make_client(configured_app)
    csrf = _csrf(c)
    cfg_path: Path = configured_app.state.config_path
    raw = _read_toml(cfg_path)
    archive_root = Path(raw["archive"]["root_dir"])

    # Ensure the directory exists so the route actually calls open_folder
    archive_root.mkdir(parents=True, exist_ok=True)

    with patch("firefliesclearer.infra.open_folder.subprocess.run") as mock_run:
        r = c.post("/settings/open-archive-folder", data={"_csrf": csrf})
    assert r.status_code == 204

    mock_run.assert_called_once()
    call_args = mock_run.call_args
    # First positional arg must be a list (no shell=True)
    cmd_list = call_args[0][0]
    assert isinstance(cmd_list, list)
    assert str(archive_root) in cmd_list
    # shell=True must not be set
    assert call_args.kwargs.get("shell") is not True


# ---------------------------------------------------------------------------
# Danger zone / reset
# ---------------------------------------------------------------------------


def test_post_reset_with_correct_confirmation_deletes_config(
    configured_app: Any,
) -> None:
    """POST confirmation_text=RESET → config.toml deleted, 303 to /setup/welcome."""
    c = _make_client(configured_app)
    csrf = _csrf(c)
    cfg_path: Path = configured_app.state.config_path
    assert cfg_path.exists()

    r = c.post(
        "/settings/reset",
        data={"_csrf": csrf, "confirmation_text": "RESET"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "/setup/welcome" in r.headers["location"]
    assert not cfg_path.exists()


def test_post_reset_with_wrong_confirmation_rejected(configured_app: Any) -> None:
    """Wrong confirmation → 200 with error, config still present."""
    c = _make_client(configured_app)
    csrf = _csrf(c)
    cfg_path: Path = configured_app.state.config_path

    r = c.post(
        "/settings/reset",
        data={"_csrf": csrf, "confirmation_text": "reset"},
        follow_redirects=False,
    )
    assert r.status_code == 200
    assert cfg_path.exists()
    assert "reset" in r.text.lower() or "confirm" in r.text.lower()


def test_post_reset_archive_preserved(configured_app: Any, tmp_path: Path) -> None:
    """Reset deletes config but leaves archive folder intact."""
    c = _make_client(configured_app)
    csrf = _csrf(c)
    cfg_path: Path = configured_app.state.config_path
    raw = _read_toml(cfg_path)
    archive_root = Path(raw["archive"]["root_dir"])
    archive_root.mkdir(parents=True, exist_ok=True)
    sentinel = archive_root / "important.txt"
    sentinel.write_text("keep me")

    c.post(
        "/settings/reset",
        data={"_csrf": csrf, "confirmation_text": "RESET"},
        follow_redirects=False,
    )
    assert sentinel.exists()


# ---------------------------------------------------------------------------
# Coverage gap tests — error branches in settings.py
# ---------------------------------------------------------------------------


def test_post_connection_test_oserror_shows_fragment_error(configured_app: Any) -> None:
    """POST /settings/connection/test with OSError from verify_api_key → error fragment."""
    c = _make_client(configured_app)
    csrf = _csrf(c)

    with patch(
        "firefliesclearer.web.routes.settings.SetupService.verify_api_key",
        new=AsyncMock(side_effect=OSError("network unreachable")),
    ):
        r = c.post(
            "/settings/connection/test",
            data={"_csrf": csrf, "api_key": "ff_any"},
        )
    assert r.status_code == 200
    assert "failed" in r.text.lower() or "connection" in r.text.lower()
    assert "network unreachable" in r.text


def test_post_connection_save_rejected_key_shows_error(configured_app: Any) -> None:
    """POST /settings/connection with a key that verify_api_key rejects → 200 + error."""
    c = _make_client(configured_app)
    csrf = _csrf(c)

    with patch(
        "firefliesclearer.web.routes.settings.SetupService.verify_api_key",
        new=AsyncMock(side_effect=InvalidApiKeyError("bad")),
    ):
        r = c.post(
            "/settings/connection",
            data={"_csrf": csrf, "api_key": "ff_bad"},
        )
    assert r.status_code == 200
    assert "rejected" in r.text.lower() or "error" in r.text.lower()


def test_post_connection_save_empty_key_shows_error(configured_app: Any) -> None:
    """POST /settings/connection with empty api_key → 200 with inline error."""
    c = _make_client(configured_app)
    csrf = _csrf(c)

    r = c.post(
        "/settings/connection",
        data={"_csrf": csrf, "api_key": ""},
    )
    assert r.status_code == 200
    assert "empty" in r.text.lower() or "must not" in r.text.lower() or "error" in r.text.lower()


def test_post_archive_non_writable_shows_error(configured_app: Any, tmp_path: Path) -> None:
    """POST /settings/archive with a non-writable directory → 200 + writable error."""
    c = _make_client(configured_app)
    csrf = _csrf(c)

    new_root = tmp_path / "non_writable_dir"
    new_root.mkdir()

    with patch(
        "firefliesclearer.web.routes.settings._is_writable",
        return_value=False,
    ):
        r = c.post(
            "/settings/archive",
            data={"_csrf": csrf, "root_dir": str(new_root)},
        )
    assert r.status_code == 200
    assert "writable" in r.text.lower() or "error" in r.text.lower()


def test_post_archive_not_a_directory_shows_error(configured_app: Any, tmp_path: Path) -> None:
    """POST /settings/archive with a path that is a file, not a dir → 200 + error."""
    c = _make_client(configured_app)
    csrf = _csrf(c)

    file_path = tmp_path / "just_a_file.txt"
    file_path.write_text("hi")

    r = c.post(
        "/settings/archive",
        data={"_csrf": csrf, "root_dir": str(file_path)},
    )
    assert r.status_code == 200
    assert "not a directory" in r.text.lower() or "error" in r.text.lower()


def test_post_archive_cannot_create_dir_shows_error(configured_app: Any, tmp_path: Path) -> None:
    """POST /settings/archive with create=yes but mkdir fails → 200 + error."""
    c = _make_client(configured_app)
    csrf = _csrf(c)

    non_existent = tmp_path / "will_fail"

    with patch("pathlib.Path.mkdir", side_effect=OSError("permission denied")):
        r = c.post(
            "/settings/archive",
            data={"_csrf": csrf, "root_dir": str(non_existent), "create": "yes"},
        )
    assert r.status_code == 200
    assert "cannot create" in r.text.lower() or "error" in r.text.lower()


def test_post_defaults_concurrency_non_integer_rejected(configured_app: Any) -> None:
    """POST /settings/defaults with concurrency='abc' → 200 + validation error."""
    c = _make_client(configured_app)
    csrf = _csrf(c)

    r = c.post(
        "/settings/defaults",
        data={
            "_csrf": csrf,
            "concurrency": "abc",
            "default_age_days": "90",
            "delete_confirmation_threshold": "10",
        },
    )
    assert r.status_code == 200
    assert "integer" in r.text.lower() or "error" in r.text.lower()


def test_post_defaults_age_days_non_integer_rejected(configured_app: Any) -> None:
    """POST /settings/defaults with default_age_days='abc' → 200 + validation error."""
    c = _make_client(configured_app)
    csrf = _csrf(c)

    r = c.post(
        "/settings/defaults",
        data={
            "_csrf": csrf,
            "concurrency": "3",
            "default_age_days": "abc",
            "delete_confirmation_threshold": "10",
        },
    )
    assert r.status_code == 200
    assert "integer" in r.text.lower() or "error" in r.text.lower()


def test_post_defaults_age_days_zero_rejected(configured_app: Any) -> None:
    """POST /settings/defaults with default_age_days=0 → 200 + validation error."""
    c = _make_client(configured_app)
    csrf = _csrf(c)

    r = c.post(
        "/settings/defaults",
        data={
            "_csrf": csrf,
            "concurrency": "3",
            "default_age_days": "0",
            "delete_confirmation_threshold": "10",
        },
    )
    assert r.status_code == 200
    assert "at least 1" in r.text.lower() or "error" in r.text.lower()


def test_post_defaults_threshold_non_integer_rejected(configured_app: Any) -> None:
    """POST /settings/defaults with delete_confirmation_threshold='abc' → 200 + error."""
    c = _make_client(configured_app)
    csrf = _csrf(c)

    r = c.post(
        "/settings/defaults",
        data={
            "_csrf": csrf,
            "concurrency": "3",
            "default_age_days": "90",
            "delete_confirmation_threshold": "abc",
        },
    )
    assert r.status_code == 200
    assert "integer" in r.text.lower() or "error" in r.text.lower()


def test_post_defaults_threshold_negative_rejected(configured_app: Any) -> None:
    """POST /settings/defaults with delete_confirmation_threshold=-1 → 200 + error."""
    c = _make_client(configured_app)
    csrf = _csrf(c)

    r = c.post(
        "/settings/defaults",
        data={
            "_csrf": csrf,
            "concurrency": "3",
            "default_age_days": "90",
            "delete_confirmation_threshold": "-1",
        },
    )
    assert r.status_code == 200
    assert "non-negative" in r.text.lower() or "error" in r.text.lower()


def test_post_logs_retention_non_integer_rejected(configured_app: Any) -> None:
    """POST /settings/logs/retention with log_retention_days='abc' → 200 + error."""
    c = _make_client(configured_app)
    csrf = _csrf(c)

    r = c.post(
        "/settings/logs/retention",
        data={"_csrf": csrf, "log_retention_days": "abc"},
    )
    assert r.status_code == 200
    assert "integer" in r.text.lower() or "error" in r.text.lower()


def test_post_logs_retention_zero_rejected(configured_app: Any) -> None:
    """POST /settings/logs/retention with log_retention_days=0 → 200 + error."""
    c = _make_client(configured_app)
    csrf = _csrf(c)

    r = c.post(
        "/settings/logs/retention",
        data={"_csrf": csrf, "log_retention_days": "0"},
    )
    assert r.status_code == 200
    assert "at least 1" in r.text.lower() or "error" in r.text.lower()


def test_is_writable_returns_false_on_oserror(tmp_path: Path) -> None:
    """_is_writable returns False when the write probe raises OSError."""
    from firefliesclearer.web.routes.settings import _is_writable

    # Patch write_text on Path to raise OSError
    with patch("pathlib.Path.write_text", side_effect=OSError("read-only")):
        result = _is_writable(tmp_path)
    assert result is False


def test_archive_has_content_returns_false_for_nonexistent_root(tmp_path: Path) -> None:
    """_archive_has_content returns False when root does not exist (line 76)."""
    from firefliesclearer.web.routes.settings import _archive_has_content

    missing = tmp_path / "does_not_exist"
    assert not missing.exists()
    assert _archive_has_content(missing) is False


def test_post_archive_empty_root_dir_shows_error(configured_app: Any) -> None:
    """POST /settings/archive with empty root_dir → 200 + inline error (line 174)."""
    c = _make_client(configured_app)
    csrf = _csrf(c)

    r = c.post(
        "/settings/archive",
        data={"_csrf": csrf, "root_dir": ""},
    )
    assert r.status_code == 200
    assert "must not be empty" in r.text.lower() or "error" in r.text.lower()
