"""Tests for SetupService — verify_api_key + atomic write_config."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from firefliesclearer.application.setup_service import (
    ConfigAlreadyExists,
    InvalidApiKey,
    SetupService,
    SetupValues,
)
from tests.fakes.in_memory_repository import InMemoryMeetingRepository

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_service(
    api_key: str | None = "ff_valid",
    registered_email: str = "user@example.com",
    *,
    config_path: Path | None = None,
) -> SetupService:
    """Build a SetupService whose repo factory consults an in-memory fake."""

    def factory(key: str) -> InMemoryMeetingRepository:
        repo = InMemoryMeetingRepository(api_key=key)
        if api_key is not None:
            repo.set_user_email_for_key(api_key, registered_email)
        return repo

    return SetupService(repo_factory=factory)


def _default_values(tmp_path: Path) -> SetupValues:
    return SetupValues(
        api_key="ff_valid",
        archive_root=tmp_path / "archive",
        default_age_days=180,
        concurrency=3,
    )


# ---------------------------------------------------------------------------
# verify_api_key
# ---------------------------------------------------------------------------


async def test_verify_api_key_happy_path() -> None:
    svc = _make_service(api_key="ff_valid", registered_email="alice@example.com")
    email = await svc.verify_api_key("ff_valid")
    assert email == "alice@example.com"


async def test_verify_api_key_bad_key_raises_invalid_api_key() -> None:
    svc = _make_service(api_key="ff_valid", registered_email="alice@example.com")
    with pytest.raises(InvalidApiKey):
        await svc.verify_api_key("ff_WRONG")


async def test_verify_api_key_propagates_key_in_exception() -> None:
    svc = _make_service(api_key="ff_valid")
    with pytest.raises(InvalidApiKey) as exc_info:
        await svc.verify_api_key("bad_key")
    assert "bad_key" in str(exc_info.value)


# ---------------------------------------------------------------------------
# write_config — atomic write
# ---------------------------------------------------------------------------


def test_write_config_creates_toml_file(tmp_path: Path) -> None:
    svc = _make_service()
    cfg_path = tmp_path / "config.toml"
    values = _default_values(tmp_path)
    svc.write_config(cfg_path, values)
    assert cfg_path.exists()
    with open(cfg_path, "rb") as f:
        data = tomllib.load(f)
    assert data["fireflies"]["api_key"] == "ff_valid"
    assert data["archive"]["root_dir"] == str(tmp_path / "archive")


def test_write_config_stores_rules_auto_block(tmp_path: Path) -> None:
    svc = _make_service()
    cfg_path = tmp_path / "config.toml"
    values = SetupValues(
        api_key="ff_key",
        archive_root=tmp_path / "arch",
        default_age_days=90,
        concurrency=5,
    )
    svc.write_config(cfg_path, values)
    with open(cfg_path, "rb") as f:
        data = tomllib.load(f)
    auto = data["rules"]["auto"]
    assert auto["older_than_days"] == 90
    assert auto["delete_failed_transcripts"] is True


def test_write_config_stores_run_and_defaults_blocks(tmp_path: Path) -> None:
    svc = _make_service()
    cfg_path = tmp_path / "config.toml"
    values = SetupValues(
        api_key="ff_key",
        archive_root=tmp_path / "arch",
        default_age_days=60,
        concurrency=8,
    )
    svc.write_config(cfg_path, values)
    with open(cfg_path, "rb") as f:
        data = tomllib.load(f)
    assert data["run"]["concurrency"] == 8
    assert data["run"]["delete_confirmation_threshold"] == 10
    assert data["defaults"]["age_days"] == 60


def test_write_config_no_tmp_file_left_behind(tmp_path: Path) -> None:
    svc = _make_service()
    cfg_path = tmp_path / "config.toml"
    values = _default_values(tmp_path)
    svc.write_config(cfg_path, values)
    tmp_file = Path(str(cfg_path) + ".tmp")
    assert not tmp_file.exists()


def test_write_config_refuses_overwrite_without_force(tmp_path: Path) -> None:
    svc = _make_service()
    cfg_path = tmp_path / "config.toml"
    values = _default_values(tmp_path)
    svc.write_config(cfg_path, values)  # first write
    with pytest.raises(ConfigAlreadyExists):
        svc.write_config(cfg_path, values)  # second without force


def test_write_config_force_overwrites_and_keeps_bak(tmp_path: Path) -> None:
    svc = _make_service()
    cfg_path = tmp_path / "config.toml"
    values = _default_values(tmp_path)
    svc.write_config(cfg_path, values)

    # Overwrite with different key
    values2 = SetupValues(
        api_key="ff_new",
        archive_root=tmp_path / "archive",
        default_age_days=180,
        concurrency=3,
    )
    svc.write_config(cfg_path, values2, force=True)

    # Backup must exist
    bak = Path(str(cfg_path) + ".bak")
    assert bak.exists()

    # Main file has updated key
    with open(cfg_path, "rb") as f:
        data = tomllib.load(f)
    assert data["fireflies"]["api_key"] == "ff_new"

    # Backup has old key
    with open(bak, "rb") as f:
        bak_data = tomllib.load(f)
    assert bak_data["fireflies"]["api_key"] == "ff_valid"


def test_write_config_creates_parent_dirs(tmp_path: Path) -> None:
    svc = _make_service()
    cfg_path = tmp_path / "nested" / "deep" / "config.toml"
    values = _default_values(tmp_path)
    svc.write_config(cfg_path, values)
    assert cfg_path.exists()
