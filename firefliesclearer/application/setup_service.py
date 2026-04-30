"""SetupService — shared first-run configuration logic for CLI and web UI."""

from __future__ import annotations

import os
import shutil
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import tomli_w

from firefliesclearer.infra.atomic_toml import write_atomic_toml
from firefliesclearer.infra.config import AutoRulesConfig, Preset, ScanFiltersModel
from firefliesclearer.infra.system_clock import SystemClock
from firefliesclearer.ports.clock import Clock
from firefliesclearer.ports.meeting_repository import MeetingRepository


class InvalidApiKeyError(Exception):
    """Raised when an API key cannot be verified against the Fireflies API."""


# Backward-compatible alias used in tests and the plan docs.
InvalidApiKey = InvalidApiKeyError


class ApiUnavailableError(Exception):
    """Raised when verification cannot complete for reasons unrelated to the key.

    Examples: rate limiting, network failure, GraphQL server errors, malformed
    response. The key may still be valid; the user should retry later.
    """


class ConfigAlreadyExistsError(Exception):
    """Raised when write_config finds an existing config and force=False."""


# Backward-compatible alias — mirrors the InvalidApiKey pattern.
ConfigAlreadyExists = ConfigAlreadyExistsError


@dataclass(frozen=True, slots=True)
class SetupValues:
    """All the information needed to write a valid config file."""

    api_key: str
    archive_root: Path
    default_age_days: int
    concurrency: int


class SetupService:
    """Orchestrates API-key verification and atomic config-file writing.

    Parameters
    ----------
    repo_factory:
        A callable that accepts an API key string and returns a
        ``MeetingRepository`` instance bound to that key.  In production this
        will be ``FirefliesClient``; in tests it is typically a factory
        returning ``InMemoryMeetingRepository``.
    """

    def __init__(self, repo_factory: Callable[[str], MeetingRepository]) -> None:
        self._repo_factory = repo_factory

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def verify_api_key(self, api_key: str) -> str:
        """Ping the Fireflies API and return the user's email address.

        Parameters
        ----------
        api_key:
            The key to verify.

        Returns
        -------
        str
            The authenticated user's email address.

        Raises
        ------
        InvalidApiKey
            If the key is definitively rejected by the Fireflies API
            (HTTP 401 / 403).
        ApiUnavailableError
            If the API responds with a transient error (rate limit, network
            failure, GraphQL server error, malformed response). The key may
            still be valid.
        """
        repo = self._repo_factory(api_key)
        try:
            return await repo.ping_user()
        except PermissionError as exc:
            raise InvalidApiKeyError(f"API key rejected: {api_key!r}") from exc
        except Exception as exc:
            # Anything else (FirefliesError including rate limits / network
            # errors, OSError, etc.) is "API unavailable" — surface as a
            # distinct error so the caller can prompt the user to retry.
            raise ApiUnavailableError(str(exc)) from exc

    def write_config(
        self,
        config_path: Path,
        values: SetupValues,
        *,
        force: bool = False,
    ) -> None:
        """Write a TOML config file atomically.

        Parameters
        ----------
        config_path:
            Destination path for the config file.
        values:
            The setup values to persist.
        force:
            If ``True``, overwrite an existing file (keeping a ``.bak`` copy).
            If ``False`` (default) and the file already exists, raise
            ``ConfigAlreadyExistsError``.

        Raises
        ------
        ConfigAlreadyExistsError
            If the target file exists and *force* is ``False``.
        """
        config_path.parent.mkdir(parents=True, exist_ok=True)

        if config_path.exists():
            if not force:
                raise ConfigAlreadyExistsError(
                    f"Config file already exists: {config_path}. Pass force=True to overwrite."
                )
            # Keep a backup of the existing file before overwriting.
            bak = Path(str(config_path) + ".bak")
            config_path.replace(bak)

        payload = self._build_payload(values)
        tmp = Path(str(config_path) + ".tmp")
        try:
            with open(tmp, "wb") as f:
                tomli_w.dump(payload, f)
                f.flush()
                os.fsync(f.fileno())
            tmp.replace(config_path)
        except Exception:
            # Clean up temp file on failure to avoid leaving stale artefacts.
            tmp.unlink(missing_ok=True)
            raise

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_payload(values: SetupValues) -> dict[str, Any]:
        return {
            "fireflies": {"api_key": values.api_key},
            "archive": {
                "root_dir": str(values.archive_root),
                "summary_format": "pdf",
            },
            "run": {
                "concurrency": values.concurrency,
                "delete_confirmation_threshold": 10,
            },
            "defaults": {
                "age_days": values.default_age_days,
            },
            # Keep the v1 [rules.auto] block synthesised from default_age_days for
            # backward compatibility — Phase 6 migration converts it to a preset.
            "rules": {
                "auto": {
                    "older_than_days": values.default_age_days,
                    "delete_failed_transcripts": True,
                },
            },
        }


# ---------------------------------------------------------------------------
# One-shot v1 migration (module-level function, not on SetupService)
# ---------------------------------------------------------------------------


def migrate_v1_rules_auto(
    config_path: Path,
    *,
    clock: Clock | None = None,
) -> bool:
    """Migrate a v1 ``[rules.auto]`` config block to a default preset.

    This is a **one-shot migration** that runs on ``serve`` startup.  It:

    1. Checks all trigger conditions; if any are not met, returns ``False``
       immediately without modifying any file.
    2. Writes a ``<config_path>.v1.bak`` backup of the original file.
    3. Builds a single ``Preset`` named *"Auto cleanup"* that maps the v1
       ``older_than_days`` / ``delete_failed_transcripts`` fields to their
       v2 equivalents.
    4. Removes the ``rules.auto`` key from the TOML payload.
    5. Writes the new TOML atomically and returns ``True``.

    Parameters
    ----------
    config_path:
        Absolute path to the user config TOML.
    clock:
        Time source for ``created_at``; defaults to ``SystemClock`` (UTC).

    Returns
    -------
    bool
        ``True`` if migration ran, ``False`` if it was a no-op.
    """
    # --- Trigger condition 1: file must exist ---
    if not config_path.exists():
        return False

    with open(config_path, "rb") as f:
        payload: dict[str, Any] = tomllib.load(f)

    # --- Trigger condition 2: must have a non-empty [rules.auto] section ---
    rules_section: dict[str, Any] = payload.get("rules", {})
    if not rules_section.get("auto"):
        return False

    # --- Trigger condition 3: must NOT have a non-empty [[presets]] array ---
    if payload.get("presets"):
        return False

    # All conditions met — proceed with migration.
    _clock = clock or SystemClock()

    # Step 1: write the backup BEFORE modifying anything (crash-safe order).
    bak_path = Path(str(config_path) + ".v1.bak")
    shutil.copy2(config_path, bak_path)

    # Step 2: parse v1 rules.auto using AutoRulesConfig so defaults fill in.
    auto_rules = AutoRulesConfig.model_validate(rules_section["auto"])

    # Step 3: build the preset.
    preset = Preset(
        name="Auto cleanup",
        description="Migrated from v1 [rules.auto]",
        default=True,
        created_at=_clock.now(),
        filters=ScanFiltersModel(
            older_than_days=auto_rules.older_than_days,
            no_transcript=auto_rules.delete_failed_transcripts,
        ),
    )

    # Step 4: remove rules.auto from the payload; clean up empty rules dict.
    new_rules = {k: v for k, v in rules_section.items() if k != "auto"}
    new_payload: dict[str, Any] = {
        **payload,
        "rules": new_rules,
        "presets": [preset.model_dump(mode="json", exclude_none=True)],
    }
    if not new_rules:
        # Remove the key entirely when the rules section is now empty so the
        # TOML round-trips cleanly (AppConfig._coerce_rules handles absence).
        del new_payload["rules"]

    # Step 5: write atomically.
    write_atomic_toml(config_path, new_payload)

    return True
