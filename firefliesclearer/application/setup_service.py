"""SetupService — shared first-run configuration logic for CLI and web UI."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import tomli_w

from firefliesclearer.ports.meeting_repository import MeetingRepository


class InvalidApiKeyError(Exception):
    """Raised when an API key cannot be verified against the Fireflies API."""


# Backward-compatible alias used in tests and the plan docs.
InvalidApiKey = InvalidApiKeyError


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
            If the key is rejected by the Fireflies API.
        """
        repo = self._repo_factory(api_key)
        try:
            return await repo.ping_user()
        except PermissionError as exc:
            raise InvalidApiKeyError(f"API key rejected: {api_key!r}") from exc

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
