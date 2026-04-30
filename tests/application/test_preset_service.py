"""Tests for PresetService — CRUD with atomic writes, TDD order."""

from __future__ import annotations

import tomllib
from datetime import UTC, datetime
from pathlib import Path

import pytest

from firefliesclearer.application.preset_service import (
    PresetAlreadyExistsError,
    PresetNotFoundError,
    PresetService,
)
from firefliesclearer.infra.config import ScanFiltersModel
from tests.fakes.frozen_clock import FrozenClock

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FROZEN_NOW = datetime(2026, 4, 30, 12, 0, 0, tzinfo=UTC)

_BASE_TOML = """\
[fireflies]
api_key = "ff_test"

[archive]
root_dir = "C:/tmp/arch"
"""


def _write_config(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _make_service(path: Path, *, now: datetime = _FROZEN_NOW) -> PresetService:
    clock = FrozenClock(now)
    return PresetService(path, clock=clock)


def _base_config(tmp_path: Path) -> Path:
    p = tmp_path / "config.toml"
    _write_config(p, _BASE_TOML)
    return p


def _simple_filters() -> ScanFiltersModel:
    return ScanFiltersModel(older_than_days=90)


# ---------------------------------------------------------------------------
# 1. list — empty when no presets
# ---------------------------------------------------------------------------


def test_list_empty_when_no_presets(tmp_path: Path) -> None:
    cfg = _base_config(tmp_path)
    svc = _make_service(cfg)
    assert svc.list() == []


# ---------------------------------------------------------------------------
# 2. create — appends preset
# ---------------------------------------------------------------------------


def test_create_appends_preset(tmp_path: Path) -> None:
    cfg = _base_config(tmp_path)
    svc = _make_service(cfg)
    preset = svc.create("Auto", "Old meetings", _simple_filters())
    result = svc.list()
    assert len(result) == 1
    assert result[0].name == "Auto"
    assert result[0].description == "Old meetings"
    assert result[0].filters.older_than_days == 90
    assert preset.name == "Auto"


# ---------------------------------------------------------------------------
# 3. create — persists to disk (new service instance reads it back)
# ---------------------------------------------------------------------------


def test_create_persists_to_disk(tmp_path: Path) -> None:
    cfg = _base_config(tmp_path)
    svc1 = _make_service(cfg)
    svc1.create("Auto", "Old meetings", _simple_filters())

    svc2 = _make_service(cfg)
    result = svc2.list()
    assert len(result) == 1
    assert result[0].name == "Auto"


# ---------------------------------------------------------------------------
# 4. create — sets created_at from clock
# ---------------------------------------------------------------------------


def test_create_sets_created_at_from_clock(tmp_path: Path) -> None:
    cfg = _base_config(tmp_path)
    clock = FrozenClock(_FROZEN_NOW)
    svc = PresetService(cfg, clock=clock)
    preset = svc.create("Auto", "", _simple_filters())
    assert preset.created_at == _FROZEN_NOW


# ---------------------------------------------------------------------------
# 5. create — duplicate name (case-insensitive) raises
# ---------------------------------------------------------------------------


def test_create_duplicate_name_case_insensitive_raises(tmp_path: Path) -> None:
    cfg = _base_config(tmp_path)
    svc = _make_service(cfg)
    svc.create("Auto", "", _simple_filters())
    with pytest.raises(PresetAlreadyExistsError):
        svc.create("AUTO", "", _simple_filters())


def test_create_duplicate_name_mixed_case_raises(tmp_path: Path) -> None:
    cfg = _base_config(tmp_path)
    svc = _make_service(cfg)
    svc.create("My Preset", "", _simple_filters())
    with pytest.raises(PresetAlreadyExistsError):
        svc.create("my preset", "", _simple_filters())


# ---------------------------------------------------------------------------
# 6. create — default=True unsets all other defaults
# ---------------------------------------------------------------------------


def test_create_with_default_true_when_other_default_exists_unsets_other(
    tmp_path: Path,
) -> None:
    cfg = _base_config(tmp_path)
    svc = _make_service(cfg)
    svc.create("A", "", _simple_filters(), default=True)
    svc.create("B", "", _simple_filters(), default=True)

    presets = svc.list()
    a = next(p for p in presets if p.name == "A")
    b = next(p for p in presets if p.name == "B")
    assert a.default is False
    assert b.default is True


# ---------------------------------------------------------------------------
# 7. get — returns preset by exact name
# ---------------------------------------------------------------------------


def test_get_returns_preset_by_exact_name(tmp_path: Path) -> None:
    cfg = _base_config(tmp_path)
    svc = _make_service(cfg)
    svc.create("Auto", "desc", _simple_filters())
    found = svc.get("Auto")
    assert found.name == "Auto"
    assert found.description == "desc"


# ---------------------------------------------------------------------------
# 8. get — case-insensitive lookup
# ---------------------------------------------------------------------------


def test_get_is_case_insensitive(tmp_path: Path) -> None:
    cfg = _base_config(tmp_path)
    svc = _make_service(cfg)
    svc.create("Auto", "", _simple_filters())
    found = svc.get("auto")
    assert found.name == "Auto"


# ---------------------------------------------------------------------------
# 9. get — missing raises PresetNotFoundError
# ---------------------------------------------------------------------------


def test_get_missing_raises(tmp_path: Path) -> None:
    cfg = _base_config(tmp_path)
    svc = _make_service(cfg)
    with pytest.raises(PresetNotFoundError):
        svc.get("Nonexistent")


# ---------------------------------------------------------------------------
# 10. update — partial update: only description
# ---------------------------------------------------------------------------


def test_update_partial_description(tmp_path: Path) -> None:
    cfg = _base_config(tmp_path)
    svc = _make_service(cfg)
    svc.create("Auto", "original desc", _simple_filters())
    updated = svc.update("Auto", description="new desc")
    assert updated.description == "new desc"
    assert updated.filters.older_than_days == 90  # unchanged


# ---------------------------------------------------------------------------
# 11. update — filters replaces filters
# ---------------------------------------------------------------------------


def test_update_filters_replaces_filters(tmp_path: Path) -> None:
    cfg = _base_config(tmp_path)
    svc = _make_service(cfg)
    svc.create("Auto", "", _simple_filters())
    new_filters = ScanFiltersModel(no_transcript=True)
    updated = svc.update("Auto", filters=new_filters)
    assert updated.filters.no_transcript is True
    assert updated.filters.older_than_days is None


# ---------------------------------------------------------------------------
# 12. update — default=True unsets other defaults
# ---------------------------------------------------------------------------


def test_update_default_true_unsets_others(tmp_path: Path) -> None:
    cfg = _base_config(tmp_path)
    svc = _make_service(cfg)
    svc.create("A", "", _simple_filters(), default=True)
    svc.create("B", "", _simple_filters(), default=False)
    svc.update("B", default=True)

    presets = svc.list()
    a = next(p for p in presets if p.name == "A")
    b = next(p for p in presets if p.name == "B")
    assert a.default is False
    assert b.default is True


# ---------------------------------------------------------------------------
# 13. update — preserves created_at
# ---------------------------------------------------------------------------


def test_update_preserves_created_at(tmp_path: Path) -> None:
    cfg = _base_config(tmp_path)
    clock = FrozenClock(_FROZEN_NOW)
    svc = PresetService(cfg, clock=clock)
    original = svc.create("Auto", "", _simple_filters())

    # Advance the clock
    later = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)
    clock.set(later)

    updated = svc.update("Auto", description="updated")
    assert updated.created_at == original.created_at


# ---------------------------------------------------------------------------
# 14. update — missing raises PresetNotFoundError
# ---------------------------------------------------------------------------


def test_update_missing_raises(tmp_path: Path) -> None:
    cfg = _base_config(tmp_path)
    svc = _make_service(cfg)
    with pytest.raises(PresetNotFoundError):
        svc.update("Nonexistent", description="x")


# ---------------------------------------------------------------------------
# 15. delete — removes preset from list
# ---------------------------------------------------------------------------


def test_delete_removes_preset(tmp_path: Path) -> None:
    cfg = _base_config(tmp_path)
    svc = _make_service(cfg)
    svc.create("Auto", "", _simple_filters())
    svc.delete("Auto")
    assert svc.list() == []


# ---------------------------------------------------------------------------
# 16. delete — default preset does not auto-promote replacement
# ---------------------------------------------------------------------------


def test_delete_default_does_not_promote_replacement(tmp_path: Path) -> None:
    cfg = _base_config(tmp_path)
    svc = _make_service(cfg)
    svc.create("A", "", _simple_filters(), default=True)
    svc.create("B", "", _simple_filters(), default=False)
    svc.delete("A")
    assert svc.get_default() is None


# ---------------------------------------------------------------------------
# 17. delete — missing raises PresetNotFoundError
# ---------------------------------------------------------------------------


def test_delete_missing_raises(tmp_path: Path) -> None:
    cfg = _base_config(tmp_path)
    svc = _make_service(cfg)
    with pytest.raises(PresetNotFoundError):
        svc.delete("Nonexistent")


# ---------------------------------------------------------------------------
# 18. get_default — returns None when no default
# ---------------------------------------------------------------------------


def test_get_default_returns_none_when_no_default(tmp_path: Path) -> None:
    cfg = _base_config(tmp_path)
    svc = _make_service(cfg)
    assert svc.get_default() is None


def test_get_default_returns_none_when_all_non_default(tmp_path: Path) -> None:
    cfg = _base_config(tmp_path)
    svc = _make_service(cfg)
    svc.create("A", "", _simple_filters(), default=False)
    svc.create("B", "", _simple_filters(), default=False)
    assert svc.get_default() is None


# ---------------------------------------------------------------------------
# 19. get_default — returns the one default preset
# ---------------------------------------------------------------------------


def test_get_default_returns_the_default(tmp_path: Path) -> None:
    cfg = _base_config(tmp_path)
    svc = _make_service(cfg)
    svc.create("A", "", _simple_filters(), default=True)
    svc.create("B", "", _simple_filters(), default=False)
    default = svc.get_default()
    assert default is not None
    assert default.name == "A"


# ---------------------------------------------------------------------------
# 20. atomic write — other config sections are preserved
# ---------------------------------------------------------------------------


def test_atomic_write_preserves_other_config_sections(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.toml"
    content = """\
[fireflies]
api_key = "ff_test"

[archive]
root_dir = "C:/tmp/arch"

[run]
concurrency = 5
delete_confirmation_threshold = 20

[rules.auto]
older_than_days = 180
delete_failed_transcripts = true

[[presets]]
name = "Existing"
description = "pre-existing preset"
default = false
created_at = 2026-04-01T10:00:00+00:00

[presets.filters]
older_than_days = 60
"""
    _write_config(cfg_path, content)
    svc = _make_service(cfg_path)
    svc.create("New Preset", "added via service", ScanFiltersModel(no_transcript=True))

    with open(cfg_path, "rb") as f:
        data = tomllib.load(f)

    assert data["fireflies"]["api_key"] == "ff_test"
    assert data["archive"]["root_dir"] == "C:/tmp/arch"
    assert data["run"]["concurrency"] == 5
    assert data["run"]["delete_confirmation_threshold"] == 20
    assert data["rules"]["auto"]["older_than_days"] == 180
    assert data["rules"]["auto"]["delete_failed_transcripts"] is True
    assert len(data["presets"]) == 2
    assert data["presets"][0]["name"] == "Existing"
    assert data["presets"][1]["name"] == "New Preset"


# ---------------------------------------------------------------------------
# Additional: empty presets list stays valid TOML after delete
# ---------------------------------------------------------------------------


def test_delete_last_preset_results_in_empty_list(tmp_path: Path) -> None:
    cfg = _base_config(tmp_path)
    svc = _make_service(cfg)
    svc.create("Solo", "", _simple_filters())
    svc.delete("Solo")

    with open(cfg, "rb") as f:
        data = tomllib.load(f)
    assert data["presets"] == []


# ---------------------------------------------------------------------------
# Additional: create without default=True leaves default=False
# ---------------------------------------------------------------------------


def test_create_default_false_by_default(tmp_path: Path) -> None:
    cfg = _base_config(tmp_path)
    svc = _make_service(cfg)
    preset = svc.create("X", "", _simple_filters())
    assert preset.default is False


# ---------------------------------------------------------------------------
# Additional: list when config file does not exist returns empty list
# ---------------------------------------------------------------------------


def test_list_when_config_file_missing_returns_empty(tmp_path: Path) -> None:
    cfg = tmp_path / "nonexistent.toml"
    svc = _make_service(cfg)
    assert svc.list() == []


# ---------------------------------------------------------------------------
# Additional: atomic_toml error path — temp file cleaned up on failure
# ---------------------------------------------------------------------------


def test_atomic_write_cleans_up_tmp_on_error(tmp_path: Path) -> None:
    """If writing fails, no .tmp file is left behind."""
    from unittest.mock import patch

    from firefliesclearer.infra.atomic_toml import write_atomic_toml

    cfg = tmp_path / "out.toml"
    tmp = Path(str(cfg) + ".tmp")

    with patch("tomli_w.dump", side_effect=OSError("disk full")), pytest.raises(OSError):
        write_atomic_toml(cfg, {"key": "value"})

    assert not tmp.exists()
