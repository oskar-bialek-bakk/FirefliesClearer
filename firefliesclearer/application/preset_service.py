"""PresetService — CRUD for named presets stored in user config TOML."""

from __future__ import annotations

import builtins
import tomllib
from pathlib import Path
from typing import Any

from firefliesclearer.infra.atomic_toml import write_atomic_toml
from firefliesclearer.infra.config import Preset, ScanFiltersModel
from firefliesclearer.infra.system_clock import SystemClock
from firefliesclearer.ports.clock import Clock

# Alias to avoid shadowing the builtin ``list`` with the public method of
# the same name (mypy resolves annotations inside the class scope and would
# otherwise confuse the two).
_List = builtins.list


class PresetNotFoundError(Exception):
    """Raised when get/update/delete is called for a name that does not exist."""


# Spec uses the bare form in some places — provide an alias for downstream tasks.
PresetNotFound = PresetNotFoundError


class PresetAlreadyExistsError(Exception):
    """Raised when create is called for a name that already exists (case-insensitive)."""


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class PresetService:
    """CRUD operations on ``[[presets]]`` entries in a TOML config file.

    All methods read the file fresh each call so they are safe to use across
    multiple service instances pointing at the same path (e.g. web UI and
    CLI running concurrently — though they will still race without an
    external lock).

    Parameters
    ----------
    config_path:
        Absolute path to the user config TOML.
    clock:
        Time source; injected so tests can freeze time.  Defaults to
        ``SystemClock`` (UTC wall clock).
    """

    def __init__(self, config_path: Path, *, clock: Clock | None = None) -> None:
        self._config_path = config_path
        self._clock = clock or SystemClock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def list(self) -> _List[Preset]:
        """Return all presets in the order they appear in the config file."""
        payload = self._load_raw()
        return self._parse_presets(payload)

    def get(self, name: str) -> Preset:
        """Return the preset whose name matches *name* (case-insensitive).

        Raises
        ------
        PresetNotFoundError
            If no preset with that name exists.
        """
        return self._find_or_raise(name, self._load_raw())

    def create(
        self,
        name: str,
        description: str,
        filters: ScanFiltersModel,
        *,
        default: bool = False,
    ) -> Preset:
        """Append a new preset to the config file and return it.

        Parameters
        ----------
        name:
            Unique display name (1-60 chars).  Case-insensitive uniqueness is
            enforced; raises ``PresetAlreadyExistsError`` on collision.
        description:
            Optional human-readable description (0-200 chars).
        filters:
            The filter configuration to store.
        default:
            If ``True``, the new preset becomes the default and all existing
            presets have their ``default`` flag cleared.

        Raises
        ------
        PresetAlreadyExistsError
            If a preset with the same name already exists (case-insensitive).
        """
        payload = self._load_raw()
        presets = self._parse_presets(payload)

        self._assert_name_free(name, presets)

        if default:
            presets = self._clear_defaults(presets)

        new_preset = Preset(
            name=name,
            description=description,
            filters=filters,
            default=default,
            created_at=self._clock.now(),
        )
        presets = [*presets, new_preset]
        self._save(payload, presets)
        return new_preset

    def update(
        self,
        name: str,
        *,
        description: str | None = None,
        filters: ScanFiltersModel | None = None,
        default: bool | None = None,
    ) -> Preset:
        """Apply a partial update to an existing preset and return the new value.

        Only the keyword arguments explicitly passed (non-``None``) are
        changed; others keep their current values.  ``name`` and
        ``created_at`` are always preserved.

        Raises
        ------
        PresetNotFoundError
            If no preset with that name exists (case-insensitive lookup).
        """
        payload = self._load_raw()
        presets = self._parse_presets(payload)
        existing = self._find_or_raise(name, payload)

        # Build the updated preset immutably.
        new_description = description if description is not None else existing.description
        new_filters = filters if filters is not None else existing.filters
        new_default = default if default is not None else existing.default

        updated = Preset(
            name=existing.name,
            description=new_description,
            filters=new_filters,
            default=new_default,
            created_at=existing.created_at,
        )

        # If setting default=True, clear it from all others first.
        if new_default:
            presets = self._clear_defaults(presets)

        # Replace the preset in the list (match by original name, case-insensitive).
        presets = [updated if p.name.lower() == name.lower() else p for p in presets]
        self._save(payload, presets)
        return updated

    def delete(self, name: str) -> None:
        """Remove the preset whose name matches *name* (case-insensitive).

        Deleting the default preset does **not** auto-promote any other preset.

        Raises
        ------
        PresetNotFoundError
            If no preset with that name exists.
        """
        payload = self._load_raw()
        presets = self._parse_presets(payload)
        # Ensure it exists first.
        self._find_or_raise(name, payload)

        remaining = [p for p in presets if p.name.lower() != name.lower()]
        self._save(payload, remaining)

    def get_default(self) -> Preset | None:
        """Return the preset marked as default, or ``None`` if there is none."""
        for preset in self.list():
            if preset.default:
                return preset
        return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_raw(self) -> dict[str, Any]:
        """Load the TOML file and return the raw dict.

        Does NOT round-trip through ``AppConfig`` so unknown sections written
        by future versions are preserved verbatim.
        """
        if not self._config_path.exists():
            return {}
        with open(self._config_path, "rb") as f:
            return tomllib.load(f)

    @staticmethod
    def _parse_presets(payload: dict[str, Any]) -> _List[Preset]:
        """Parse the ``presets`` list from a raw TOML payload."""
        raw_list: _List[dict[str, Any]] = payload.get("presets", [])
        return [Preset.model_validate(item) for item in raw_list]

    def _find_or_raise(self, name: str, payload: dict[str, Any]) -> Preset:
        """Return the preset matching *name* case-insensitively, or raise."""
        for preset in self._parse_presets(payload):
            if preset.name.lower() == name.lower():
                return preset
        raise PresetNotFoundError(f"No preset named {name!r}")

    @staticmethod
    def _assert_name_free(name: str, presets: _List[Preset]) -> None:
        for p in presets:
            if p.name.lower() == name.lower():
                raise PresetAlreadyExistsError(
                    f"A preset named {name!r} already exists (case-insensitive match: {p.name!r})"
                )

    @staticmethod
    def _clear_defaults(presets: _List[Preset]) -> _List[Preset]:
        """Return a new list with all ``default`` flags set to ``False``."""
        return [
            Preset(
                name=p.name,
                description=p.description,
                filters=p.filters,
                default=False,
                created_at=p.created_at,
            )
            for p in presets
        ]

    def _save(self, payload: dict[str, Any], presets: _List[Preset]) -> None:
        """Serialise *presets* back into *payload* and write atomically."""
        # Dump each preset using mode="json" + exclude_none to match the
        # existing write_config behaviour from Task 6.1.
        new_payload = {
            **payload,
            "presets": [p.model_dump(mode="json", exclude_none=True) for p in presets],
        }
        self._config_path.parent.mkdir(parents=True, exist_ok=True)
        write_atomic_toml(self._config_path, new_payload)
