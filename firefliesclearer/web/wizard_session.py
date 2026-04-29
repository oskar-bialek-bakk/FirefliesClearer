"""Cleanup-wizard session-state helpers.

The cleanup wizard occupies the ``"wizard"`` slot inside ``SessionStore``:

    {
      "wizard": {
        "step": "filter" | "review" | "archive" | "purge" | "done",
        "filters": {...serialized ScanFilters...},
        "selected_ids": [str, ...],
        "operation_id": str | None,
      }
    }

Helpers in this module:

- ``get_state`` / ``set_state``: read/write the ``wizard`` slice atomically
  while preserving other session keys.
- ``filters_to_dict`` / ``filters_from_dict``: serialise a ``ScanFilters``
  to / from a JSON-friendly dict (tuples become lists and back).
- ``parse_filter_form``: map raw urlencoded form values from the Step 1
  filter form into a ``ScanFilters`` instance.
"""

from __future__ import annotations

from typing import Any, TypedDict, cast

from firefliesclearer.application.scan_service import ScanFilters
from firefliesclearer.web.sessions import SessionStore


class WizardState(TypedDict, total=False):
    """Typed slice of the session store dedicated to the cleanup wizard."""

    step: str
    filters: dict[str, Any]
    selected_ids: list[str]
    operation_id: str | None


# ---------------------------------------------------------------------------
# Session-store I/O
# ---------------------------------------------------------------------------


def get_state(store: SessionStore, sid: str) -> WizardState:
    """Return the wizard slice or ``{}`` if absent."""
    return cast("WizardState", store.get(sid).get("wizard", {}))


def set_state(store: SessionStore, sid: str, state: WizardState) -> None:
    """Atomically replace the wizard slice without clobbering other session keys.

    Delegates to :meth:`SessionStore.update`, whose shallow-merge under the
    internal lock overwrites the ``"wizard"`` key while preserving the rest of
    the session payload.
    """
    store.update(sid, {"wizard": dict(state)})


# ---------------------------------------------------------------------------
# ScanFilters <-> dict
# ---------------------------------------------------------------------------


def filters_to_dict(f: ScanFilters) -> dict[str, Any]:
    """Serialise ``ScanFilters`` to a JSON-friendly dict (tuples -> lists)."""
    return {
        "older_than_days": f.older_than_days,
        "duration_below_minutes": f.duration_below_minutes,
        "no_transcript": f.no_transcript,
        "title_contains": list(f.title_contains),
        "title_regex": f.title_regex,
        "host_email": list(f.host_email),
        "participants_below": f.participants_below,
        "has_tag": list(f.has_tag),
    }


def filters_from_dict(d: dict[str, Any]) -> ScanFilters:
    """Inverse of :func:`filters_to_dict`. Restores tuple sequences."""
    return ScanFilters(
        older_than_days=d.get("older_than_days"),
        duration_below_minutes=d.get("duration_below_minutes"),
        no_transcript=bool(d.get("no_transcript", False)),
        title_contains=tuple(d.get("title_contains") or ()),
        title_regex=d.get("title_regex"),
        host_email=tuple(d.get("host_email") or ()),
        participants_below=d.get("participants_below"),
        has_tag=tuple(d.get("has_tag") or ()),
    )


# ---------------------------------------------------------------------------
# Form parsing
# ---------------------------------------------------------------------------


def _split_csv(raw: str | None) -> tuple[str, ...]:
    """Parse a comma-separated string into a tuple, dropping blanks/whitespace."""
    if not raw:
        return ()
    parts = (p.strip() for p in raw.split(","))
    return tuple(p for p in parts if p)


def _opt_int(raw: str | None) -> int | None:
    """Return int(raw) or None when raw is missing/blank/non-numeric."""
    if raw is None:
        return None
    raw = raw.strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _opt_float(raw: str | None) -> float | None:
    """Return float(raw) or None when raw is missing/blank/non-numeric."""
    if raw is None:
        return None
    raw = raw.strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _opt_str(raw: str | None) -> str | None:
    """Return raw.strip() or None when raw is missing/blank."""
    if raw is None:
        return None
    raw = raw.strip()
    return raw or None


def _checkbox(raw: str | None) -> bool:
    """A checkbox is checked iff the field is present and not the empty string."""
    return raw is not None and raw != ""


def parse_filter_form(form: dict[str, Any]) -> ScanFilters:
    """Map the Step 1 form values to a ``ScanFilters`` instance.

    Conventions:

    - ``older_than_days_enabled`` and ``duration_below_minutes_enabled``
      checkboxes gate whether the corresponding numeric value is used.
    - ``no_transcript`` is a checkbox.
    - Comma-separated text fields parse into tuples; whitespace-stripped,
      empty values dropped.
    - Numeric inputs with empty/non-numeric values map to ``None``.
    """

    def _get(key: str) -> str | None:
        v = form.get(key)
        if v is None:
            return None
        return str(v)

    older_than_days = (
        _opt_int(_get("older_than_days")) if _checkbox(_get("older_than_days_enabled")) else None
    )
    duration_below_minutes = (
        _opt_float(_get("duration_below_minutes"))
        if _checkbox(_get("duration_below_minutes_enabled"))
        else None
    )

    return ScanFilters(
        older_than_days=older_than_days,
        duration_below_minutes=duration_below_minutes,
        no_transcript=_checkbox(_get("no_transcript")),
        title_contains=_split_csv(_get("title_contains")),
        title_regex=_opt_str(_get("title_regex")),
        host_email=_split_csv(_get("host_email")),
        participants_below=_opt_int(_get("participants_below")),
        has_tag=_split_csv(_get("has_tag")),
    )
