"""Tests for SetupService — verify_api_key + atomic write_config + migrate_v1_rules_auto."""

from __future__ import annotations

import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import tomli_w

from firefliesclearer.application.setup_service import (
    ConfigAlreadyExists,
    InvalidApiKey,
    SetupService,
    SetupValues,
    migrate_v1_rules_auto,
)
from firefliesclearer.infra.config import load_config
from tests.fakes.in_memory_repository import InMemoryMeetingRepository

# ---------------------------------------------------------------------------
# Helpers for migration tests
# ---------------------------------------------------------------------------


class _FrozenClock:
    """Deterministic clock for testing."""

    def __init__(self, dt: datetime) -> None:
        self._dt = dt

    def now(self) -> datetime:
        return self._dt


_FROZEN_DT = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)

# Minimal v1-style TOML payload that triggers migration.
_V1_TOML_FULL = {
    "fireflies": {"api_key": "ff_test"},
    "archive": {"root_dir": "/tmp/archive"},
    "rules": {
        "auto": {
            "older_than_days": 90,
            "delete_failed_transcripts": True,
        }
    },
}

_V1_TOML_NO_DELETE_FAILED = {
    "fireflies": {"api_key": "ff_test"},
    "archive": {"root_dir": "/tmp/archive"},
    "rules": {
        "auto": {
            "older_than_days": 42,
            "delete_failed_transcripts": False,
        }
    },
}

_V1_TOML_PARTIAL = {
    "fireflies": {"api_key": "ff_test"},
    "archive": {"root_dir": "/tmp/archive"},
    "rules": {
        "auto": {
            "older_than_days": 42,
            # no delete_failed_transcripts — defaults to True per AutoRulesConfig
        }
    },
}

_V1_TOML_WITH_PRESETS = {
    "fireflies": {"api_key": "ff_test"},
    "archive": {"root_dir": "/tmp/archive"},
    "rules": {
        "auto": {
            "older_than_days": 90,
            "delete_failed_transcripts": True,
        }
    },
    "presets": [
        {
            "name": "Existing Preset",
            "default": True,
            "created_at": "2026-01-01T00:00:00+00:00",
        }
    ],
}

_NO_RULES_AUTO_TOML = {
    "fireflies": {"api_key": "ff_test"},
    "archive": {"root_dir": "/tmp/archive"},
}


def _write_toml(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        tomli_w.dump(payload, f)


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


# ---------------------------------------------------------------------------
# migrate_v1_rules_auto
# ---------------------------------------------------------------------------


def test_migrate_creates_default_preset_from_rules_auto(tmp_path: Path) -> None:
    """v1 TOML with [rules.auto] and no [[presets]] → 1 preset created."""
    cfg_path = tmp_path / "config.toml"
    _write_toml(cfg_path, _V1_TOML_FULL)

    result = migrate_v1_rules_auto(cfg_path, clock=_FrozenClock(_FROZEN_DT))

    assert result is True
    with open(cfg_path, "rb") as f:
        data = tomllib.load(f)
    presets = data["presets"]
    assert len(presets) == 1
    p = presets[0]
    assert p["name"] == "Auto cleanup"
    assert p["default"] is True
    assert p["filters"]["older_than_days"] == 90
    assert p["filters"]["no_transcript"] is True


def test_migrate_writes_v1_bak_with_original_content(tmp_path: Path) -> None:
    """Backup file is written before migration with the original bytes."""
    cfg_path = tmp_path / "config.toml"
    _write_toml(cfg_path, _V1_TOML_FULL)
    original_bytes = cfg_path.read_bytes()

    migrate_v1_rules_auto(cfg_path, clock=_FrozenClock(_FROZEN_DT))

    bak_path = Path(str(cfg_path) + ".v1.bak")
    assert bak_path.exists()
    # Backup must re-parse to the original structure.
    with open(bak_path, "rb") as f:
        bak_data = tomllib.load(f)
    assert bak_data["rules"]["auto"]["older_than_days"] == 90
    # Byte-for-byte match (shutil.copy2 preserves exact content).
    assert bak_path.read_bytes() == original_bytes


def test_migrate_removes_rules_auto_section(tmp_path: Path) -> None:
    """After migration, rules.auto must not exist in the TOML."""
    cfg_path = tmp_path / "config.toml"
    _write_toml(cfg_path, _V1_TOML_FULL)

    migrate_v1_rules_auto(cfg_path, clock=_FrozenClock(_FROZEN_DT))

    with open(cfg_path, "rb") as f:
        data = tomllib.load(f)
    rules = data.get("rules", {})
    assert "auto" not in rules

    # Also confirm load_config does not error.
    cfg = load_config(user_config=cfg_path)
    assert cfg is not None


def test_migrate_is_noop_when_presets_already_present(tmp_path: Path) -> None:
    """TOML with both [rules.auto] and [[presets]] → no migration."""
    cfg_path = tmp_path / "config.toml"
    _write_toml(cfg_path, _V1_TOML_WITH_PRESETS)
    original_bytes = cfg_path.read_bytes()

    result = migrate_v1_rules_auto(cfg_path, clock=_FrozenClock(_FROZEN_DT))

    assert result is False
    bak_path = Path(str(cfg_path) + ".v1.bak")
    assert not bak_path.exists()
    assert cfg_path.read_bytes() == original_bytes


def test_migrate_is_noop_when_rules_auto_absent(tmp_path: Path) -> None:
    """TOML with no [rules.auto] → no migration."""
    cfg_path = tmp_path / "config.toml"
    _write_toml(cfg_path, _NO_RULES_AUTO_TOML)
    original_bytes = cfg_path.read_bytes()

    result = migrate_v1_rules_auto(cfg_path, clock=_FrozenClock(_FROZEN_DT))

    assert result is False
    bak_path = Path(str(cfg_path) + ".v1.bak")
    assert not bak_path.exists()
    assert cfg_path.read_bytes() == original_bytes


def test_migrate_uses_injected_clock_for_created_at(tmp_path: Path) -> None:
    """The preset's created_at reflects the injected clock value."""
    cfg_path = tmp_path / "config.toml"
    _write_toml(cfg_path, _V1_TOML_FULL)
    frozen_dt = datetime(2025, 6, 1, 9, 30, 0, tzinfo=UTC)

    migrate_v1_rules_auto(cfg_path, clock=_FrozenClock(frozen_dt))

    with open(cfg_path, "rb") as f:
        data = tomllib.load(f)
    # model_dump(mode="json") serialises datetime to ISO string; tomli_w writes
    # it as a TOML string literal (not a TOML datetime), so tomllib reads it
    # back as a str.  Compare via Preset.model_validate round-trip instead.
    from firefliesclearer.infra.config import Preset

    parsed_preset = Preset.model_validate(data["presets"][0])
    assert parsed_preset.created_at == frozen_dt


def test_migrate_maps_delete_failed_transcripts_false_to_no_transcript_false(
    tmp_path: Path,
) -> None:
    """delete_failed_transcripts=False → no_transcript=False on the preset."""
    cfg_path = tmp_path / "config.toml"
    _write_toml(cfg_path, _V1_TOML_NO_DELETE_FAILED)

    migrate_v1_rules_auto(cfg_path, clock=_FrozenClock(_FROZEN_DT))

    with open(cfg_path, "rb") as f:
        data = tomllib.load(f)
    assert data["presets"][0]["filters"]["no_transcript"] is False


def test_migrate_handles_partial_rules_auto(tmp_path: Path) -> None:
    """[rules.auto] with only older_than_days → defaults fill in; no_transcript=True."""
    cfg_path = tmp_path / "config.toml"
    _write_toml(cfg_path, _V1_TOML_PARTIAL)

    migrate_v1_rules_auto(cfg_path, clock=_FrozenClock(_FROZEN_DT))

    with open(cfg_path, "rb") as f:
        data = tomllib.load(f)
    p = data["presets"][0]
    assert p["filters"]["older_than_days"] == 42
    # AutoRulesConfig default for delete_failed_transcripts is True → no_transcript=True
    assert p["filters"]["no_transcript"] is True


def test_migrate_idempotent(tmp_path: Path) -> None:
    """Second call is a no-op (presets present); .v1.bak is not overwritten."""
    cfg_path = tmp_path / "config.toml"
    _write_toml(cfg_path, _V1_TOML_FULL)

    first = migrate_v1_rules_auto(cfg_path, clock=_FrozenClock(_FROZEN_DT))
    bak_path = Path(str(cfg_path) + ".v1.bak")
    bak_mtime_after_first = bak_path.stat().st_mtime

    second = migrate_v1_rules_auto(cfg_path, clock=_FrozenClock(_FROZEN_DT))

    assert first is True
    assert second is False
    # Backup was NOT touched on the second call.
    assert bak_path.stat().st_mtime == bak_mtime_after_first


def test_migrate_returns_true_when_migration_runs(tmp_path: Path) -> None:
    """migrate_v1_rules_auto returns True when it performs migration."""
    cfg_path = tmp_path / "config.toml"
    _write_toml(cfg_path, _V1_TOML_FULL)
    assert migrate_v1_rules_auto(cfg_path, clock=_FrozenClock(_FROZEN_DT)) is True


def test_migrate_returns_false_when_skipped(tmp_path: Path) -> None:
    """migrate_v1_rules_auto returns False when nothing to migrate."""
    cfg_path = tmp_path / "config.toml"
    _write_toml(cfg_path, _NO_RULES_AUTO_TOML)
    assert migrate_v1_rules_auto(cfg_path) is False


def test_migrate_is_noop_when_config_does_not_exist(tmp_path: Path) -> None:
    """If the config file doesn't exist, migration is silently skipped."""
    cfg_path = tmp_path / "nonexistent.toml"
    result = migrate_v1_rules_auto(cfg_path)
    assert result is False
