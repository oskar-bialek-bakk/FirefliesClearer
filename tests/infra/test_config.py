"""Tests for config: schema validation and precedence chain."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from firefliesclearer.infra.config import (
    AppConfig,
    ConfigError,
    Preset,
    ScanFiltersModel,
    load_config,
    write_config,
)


def _write_user(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "user.toml"
    p.write_text(content, encoding="utf-8")
    return p


def test_minimal_valid_config_parses(tmp_path: Path) -> None:
    user = _write_user(
        tmp_path,
        """
        [fireflies]
        api_key = "ff_xyz"

        [archive]
        root_dir = "C:/tmp/arch"
        summary_format = "pdf"
        """,
    )
    cfg = load_config(user_config=user)
    assert cfg.fireflies.api_key == "ff_xyz"
    assert cfg.archive.root_dir == Path("C:/tmp/arch")
    assert cfg.archive.summary_format == "pdf"


def test_missing_api_key_raises_actionable_error(tmp_path: Path) -> None:
    user = _write_user(
        tmp_path,
        """
        [archive]
        root_dir = "C:/tmp/arch"
        summary_format = "pdf"
        """,
    )
    with pytest.raises(ConfigError) as exc:
        load_config(user_config=user)
    assert "firefliesclearer init" in str(exc.value)


def test_env_var_overrides_user_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    user = _write_user(
        tmp_path,
        """
        [fireflies]
        api_key = "from_user"
        [archive]
        root_dir = "C:/tmp/arch"
        summary_format = "pdf"
        """,
    )
    monkeypatch.setenv("FIREFLIES_API_KEY", "from_env")
    cfg = load_config(user_config=user)
    assert cfg.fireflies.api_key == "from_env"


def test_project_config_overrides_user_config(tmp_path: Path) -> None:
    user = _write_user(
        tmp_path,
        """
        [fireflies]
        api_key = "from_user"
        [archive]
        root_dir = "C:/user/arch"
        summary_format = "pdf"
        """,
    )
    project = tmp_path / "firefliesclearer.toml"
    project.write_text(
        """
        [archive]
        root_dir = "C:/proj/arch"
        """,
        encoding="utf-8",
    )
    cfg = load_config(user_config=user, project_config=project)
    assert cfg.archive.root_dir == Path("C:/proj/arch")
    assert cfg.fireflies.api_key == "from_user"


def test_cli_override_beats_everything(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    user = _write_user(
        tmp_path,
        """
        [fireflies]
        api_key = "from_user"
        [archive]
        root_dir = "C:/tmp/arch"
        summary_format = "pdf"
        """,
    )
    monkeypatch.setenv("FIREFLIES_API_KEY", "from_env")
    cfg = load_config(
        user_config=user,
        cli_overrides={"fireflies.api_key": "from_cli"},
    )
    assert cfg.fireflies.api_key == "from_cli"


def test_invalid_summary_format_rejected(tmp_path: Path) -> None:
    user = _write_user(
        tmp_path,
        """
        [fireflies]
        api_key = "x"
        [archive]
        root_dir = "C:/tmp"
        summary_format = "docx"
        """,
    )
    with pytest.raises(ConfigError):
        load_config(user_config=user)


def test_write_config_round_trip(tmp_path: Path) -> None:
    cfg = AppConfig.model_validate(
        {
            "fireflies": {"api_key": "ff_xyz"},
            "archive": {
                "root_dir": str(tmp_path / "arch"),
                "summary_format": "pdf",
            },
        }
    )
    target = tmp_path / "out.toml"
    write_config(cfg, target)
    loaded = load_config(user_config=target)
    assert loaded.fireflies.api_key == "ff_xyz"


# ---------------------------------------------------------------------------
# Preset model tests — Task 6.1
# ---------------------------------------------------------------------------

_BASE_TOML = """
[fireflies]
api_key = "ff_test"

[archive]
root_dir = "C:/tmp/arch"
"""

_TWO_PRESETS_TOML = (
    _BASE_TOML
    + """
[[presets]]
name = "Auto cleanup"
description = "Old meetings (>180d) and anything without a transcript"
default = true
created_at = "2026-04-29T10:14:00+02:00"

[presets.filters]
older_than_days = 180
no_transcript = true

[[presets]]
name = "Drafts and tests"
description = "Short meetings whose title contains test/draft/temp"
default = false
created_at = "2026-04-29T10:18:00+02:00"

[presets.filters]
duration_below_minutes = 5.0
title_contains = ["test", "draft", "temp"]
"""
)


def test_two_presets_load_correctly(tmp_path: Path) -> None:
    user = _write_user(tmp_path, _TWO_PRESETS_TOML)
    cfg = load_config(user_config=user)

    assert len(cfg.presets) == 2

    first = cfg.presets[0]
    assert first.name == "Auto cleanup"
    assert first.description == "Old meetings (>180d) and anything without a transcript"
    assert first.default is True
    assert first.filters.older_than_days == 180
    assert first.filters.no_transcript is True

    second = cfg.presets[1]
    assert second.name == "Drafts and tests"
    assert second.default is False
    assert second.filters.duration_below_minutes == 5.0
    assert second.filters.title_contains == ["test", "draft", "temp"]


def test_no_presets_section_defaults_to_empty_list(tmp_path: Path) -> None:
    user = _write_user(tmp_path, _BASE_TOML)
    cfg = load_config(user_config=user)
    assert cfg.presets == []


def test_preset_round_trip(tmp_path: Path) -> None:
    user = _write_user(tmp_path, _TWO_PRESETS_TOML)
    cfg = load_config(user_config=user)

    target = tmp_path / "out.toml"
    write_config(cfg, target)
    loaded = load_config(user_config=target)

    assert len(loaded.presets) == 2
    assert loaded.presets[0].name == "Auto cleanup"
    assert loaded.presets[0].default is True
    assert loaded.presets[0].filters.older_than_days == 180
    assert loaded.presets[0].filters.no_transcript is True
    assert loaded.presets[1].name == "Drafts and tests"
    assert loaded.presets[1].filters.title_contains == ["test", "draft", "temp"]


def test_preset_name_too_short_rejected() -> None:
    with pytest.raises(ValidationError):
        Preset(
            name="",
            created_at=datetime(2026, 4, 29, tzinfo=UTC),
        )


def test_preset_name_too_long_rejected() -> None:
    with pytest.raises(ValidationError):
        Preset(
            name="x" * 61,
            created_at=datetime(2026, 4, 29, tzinfo=UTC),
        )


def test_preset_description_too_long_rejected() -> None:
    with pytest.raises(ValidationError):
        Preset(
            name="Valid name",
            description="d" * 201,
            created_at=datetime(2026, 4, 29, tzinfo=UTC),
        )


def test_preset_filters_title_contains_round_trips(tmp_path: Path) -> None:
    created = datetime(2026, 4, 29, 10, 0, 0, tzinfo=UTC)
    preset = Preset(
        name="Filter test",
        created_at=created,
        filters=ScanFiltersModel(title_contains=["a", "b"]),
    )
    cfg = AppConfig.model_validate(
        {
            "fireflies": {"api_key": "ff_test"},
            "archive": {"root_dir": "C:/tmp/arch"},
            "presets": [preset.model_dump(mode="json")],
        }
    )
    target = tmp_path / "out.toml"
    write_config(cfg, target)
    loaded = load_config(user_config=target)

    assert len(loaded.presets) == 1
    assert loaded.presets[0].filters.title_contains == ["a", "b"]


def test_preset_name_at_max_length_accepted(tmp_path: Path) -> None:
    """A 60-char name (the max) is valid; ensures we test the boundary, not just past it."""
    # Round-trip a TOML with a 60-char name through load_config to confirm acceptance.
    name_60 = "x" * 60
    user = _write_user(
        tmp_path,
        f"""
        [fireflies]
        api_key = "k"
        [archive]
        root_dir = "C:/tmp/arch"
        summary_format = "pdf"

        [[presets]]
        name = "{name_60}"
        description = ""
        default = false
        created_at = 2026-04-29T10:00:00+00:00
        """,
    )
    cfg = load_config(user_config=user)
    assert len(cfg.presets) == 1
    assert cfg.presets[0].name == "x" * 60


def test_sync_config_defaults_when_section_missing(tmp_path):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        """
[fireflies]
api_key = "ff_test"
[archive]
root_dir = "/tmp/x"
""",
        encoding="utf-8",
    )
    cfg = load_config(user_config=cfg_path)
    assert cfg.sync.enabled is False
    assert cfg.sync.incremental_interval_hours == 6
    assert cfg.sync.full_interval_days == 7
    assert cfg.sync.full_run_hour_local == 3


def test_sync_config_explicit_values(tmp_path):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        """
[fireflies]
api_key = "ff_test"
[archive]
root_dir = "/tmp/x"
[sync]
enabled = true
incremental_interval_hours = 12
full_interval_days = 30
full_run_hour_local = 4
""",
        encoding="utf-8",
    )
    cfg = load_config(user_config=cfg_path)
    assert cfg.sync.enabled is True
    assert cfg.sync.incremental_interval_hours == 12
    assert cfg.sync.full_interval_days == 30
    assert cfg.sync.full_run_hour_local == 4


def test_sync_config_full_interval_days_zero_disables_full(tmp_path):
    """0 means 'never run full reconciliation' — incremental-only mode."""
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        """
[fireflies]
api_key = "ff_test"
[archive]
root_dir = "/tmp/x"
[sync]
full_interval_days = 0
""",
        encoding="utf-8",
    )
    cfg = load_config(user_config=cfg_path)
    assert cfg.sync.full_interval_days == 0


def test_sync_config_validates_hour_range(tmp_path):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        """
[fireflies]
api_key = "ff_test"
[archive]
root_dir = "/tmp/x"
[sync]
full_run_hour_local = 25
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        load_config(user_config=cfg_path)
