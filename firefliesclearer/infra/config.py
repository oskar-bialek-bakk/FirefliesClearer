"""Config: TOML loader, precedence chain, Pydantic validation."""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import tomli_w
from pydantic import BaseModel, Field, ValidationError, field_validator


class ConfigError(Exception):
    pass


class FirefliesConfig(BaseModel):
    api_key: str = Field(min_length=1)
    # Authenticated user's email address (the value of ``user.email`` from
    # Fireflies' GraphQL API). Used by the pipeline to skip API delete on
    # meetings the user is not the host of — Fireflies' deleteTranscript
    # mutation rejects non-host calls, so attempting them just burns daily
    # quota for no benefit. Optional + nullable so existing configs keep
    # working; SetupService writes it after a successful ``ping_user`` and
    # web/deps lazy-resolves it on first build for upgrades.
    user_email: str | None = None


class ArchiveConfig(BaseModel):
    root_dir: Path
    summary_format: Literal["pdf"] = "pdf"


class ScanFiltersModel(BaseModel):
    """Pydantic mirror of ScanFilters dataclass — TOML/JSON-serializable."""

    older_than_days: int | None = None
    duration_below_minutes: float | None = None
    no_transcript: bool = False
    title_contains: list[str] = Field(default_factory=list)
    title_regex: str | None = None
    host_email: list[str] = Field(default_factory=list)
    participants_below: int | None = None
    has_tag: list[str] = Field(default_factory=list)


class Preset(BaseModel):
    """A named, reusable filter configuration stored in user config TOML."""

    name: str = Field(..., min_length=1, max_length=60)
    description: str = Field(default="", max_length=200)
    default: bool = False
    created_at: datetime
    filters: ScanFiltersModel = Field(default_factory=ScanFiltersModel)


class AutoRulesConfig(BaseModel):
    older_than_days: int = 180
    delete_failed_transcripts: bool = True


class RunConfig(BaseModel):
    concurrency: int = Field(default=3, ge=1, le=20)
    delete_confirmation_threshold: int = Field(default=10, ge=0)
    default_age_days: int = Field(default=180, ge=1, le=3650)
    log_retention_days: int = Field(default=30, ge=1, le=3650)
    # Daily cap for the background API-purge trickle. 0 disables the trickle
    # entirely (recommended for users who exclusively bulk-delete via the
    # Fireflies web UI). Capped at 50 because Fireflies' Pro plan limits
    # total daily GraphQL ops to 50 — leaving zero headroom for sync would
    # be self-defeating. The default 5 keeps comfortable headroom for sync,
    # archive, and a few wizard scans on a Pro account.
    api_purge_per_day: int = Field(default=5, ge=0, le=50)


class SyncConfig(BaseModel):
    """Configuration for the local-cache sync engine.

    enabled: master flag — when False, the scheduler does not start. Phase 6
        flipped the default to True for fresh installs; existing users see a
        one-time opt-in banner on the dashboard.
    incremental_interval_hours: cadence for cheap "find new meetings" passes.
    full_interval_days: cadence for full reconciliation. 0 disables it
        (incremental-only mode).
    full_run_hour_local: local hour-of-day to align full reconciliations
        to so they don't compete with daytime use.
    opt_in_dismissed: True once the existing user has chosen "Not now" on
        the dashboard opt-in banner; suppresses the banner thereafter.
    """

    enabled: bool = False
    incremental_interval_hours: int = Field(default=6, ge=1, le=168)
    full_interval_days: int = Field(default=7, ge=0, le=365)
    full_run_hour_local: int = Field(default=3, ge=0, le=23)
    opt_in_dismissed: bool = False


class AppConfig(BaseModel):
    fireflies: FirefliesConfig
    archive: ArchiveConfig
    rules: dict[str, Any] = Field(default_factory=dict)
    run: RunConfig = Field(default_factory=RunConfig)
    presets: list[Preset] = Field(default_factory=list)
    sync: SyncConfig = Field(default_factory=SyncConfig)

    @field_validator("rules")
    @classmethod
    def _coerce_rules(cls, v: dict[str, Any]) -> dict[str, Any]:
        out = {**v}
        if "auto" in out:
            out["auto"] = AutoRulesConfig.model_validate(out["auto"]).model_dump()
        else:
            out["auto"] = AutoRulesConfig().model_dump()
        return out

    def auto_rules(self) -> AutoRulesConfig:
        return AutoRulesConfig.model_validate(self.rules.get("auto", {}))


def _read_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with open(path, "rb") as f:
        return tomllib.load(f)


def _deep_merge(a: Mapping[str, Any], b: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {**a}
    for k, v in b.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _apply_dotted(target: dict[str, Any], dotted_key: str, value: Any) -> None:
    parts = dotted_key.split(".")
    cur: dict[str, Any] = target
    for p in parts[:-1]:
        cur = cur.setdefault(p, {})
    cur[parts[-1]] = value


def load_config(
    *,
    user_config: Path,
    project_config: Path | None = None,
    cli_overrides: Mapping[str, Any] | None = None,
) -> AppConfig:
    """Precedence (highest wins): cli_overrides > env > project > user."""
    merged = _read_toml(user_config)
    if project_config is not None:
        merged = _deep_merge(merged, _read_toml(project_config))

    if env_key := os.environ.get("FIREFLIES_API_KEY"):
        merged = _deep_merge(merged, {"fireflies": {"api_key": env_key}})

    if cli_overrides:
        for k, v in cli_overrides.items():
            _apply_dotted(merged, k, v)

    if "fireflies" not in merged or not merged["fireflies"].get("api_key"):
        raise ConfigError(
            "Missing Fireflies API key. Run `firefliesclearer init` to set it, "
            "or export FIREFLIES_API_KEY."
        )

    try:
        return AppConfig.model_validate(merged)
    except ValidationError as e:
        raise ConfigError(str(e)) from e


def write_config(cfg: AppConfig, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # ``exclude_none`` is mandatory: tomli_w cannot serialize Python None
    # (see CLAUDE.md "Common gotchas"). Phase 6 added a nullable
    # ``[fireflies] user_email`` which is the immediate trigger, but the
    # rule applies to any current or future Optional field.
    payload: dict[str, Any] = cfg.model_dump(mode="json", exclude_none=True)
    payload["archive"]["root_dir"] = str(payload["archive"]["root_dir"])
    if payload.get("presets"):
        payload["presets"] = [p.model_dump(mode="json", exclude_none=True) for p in cfg.presets]
    with open(path, "wb") as f:
        tomli_w.dump(payload, f)


def user_config_path() -> Path:
    """Cross-platform user config location via platformdirs."""
    from platformdirs import user_config_dir

    return Path(user_config_dir("firefliesclearer", appauthor=False)) / "config.toml"
