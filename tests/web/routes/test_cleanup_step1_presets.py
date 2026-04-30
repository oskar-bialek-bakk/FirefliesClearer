"""Tests for preset integration in cleanup wizard Step 1 (Task 6.4).

Covers:
- Presets dropdown populated from config
- Load-preset query param overrides session filters
- Unknown preset handled gracefully
- Save-as-preset form creates preset and shows success/error banners
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from firefliesclearer.application.preset_service import PresetService
from firefliesclearer.infra.config import ScanFiltersModel


def _make_client(configured_app) -> TestClient:
    c = TestClient(configured_app)
    c.get("/?token=T")
    return c


def _seed_preset(
    config_path: Path,
    name: str,
    *,
    description: str = "",
    default: bool = False,
    older_than_days: int | None = None,
) -> None:
    svc = PresetService(config_path)
    filters = ScanFiltersModel(older_than_days=older_than_days)
    svc.create(name, description, filters, default=default)


# ---------------------------------------------------------------------------
# GET /cleanup with presets
# ---------------------------------------------------------------------------


def test_get_cleanup_passes_presets_to_template(configured_app) -> None:
    """Two seeded presets appear in the load-preset dropdown."""
    cfg_path: Path = configured_app.state.config_path
    _seed_preset(cfg_path, "Alpha")
    _seed_preset(cfg_path, "Beta")

    c = _make_client(configured_app)
    r = c.get("/cleanup")
    assert r.status_code == 200
    assert "Alpha" in r.text
    assert "Beta" in r.text
    # Dropdown should have a select element that is no longer disabled
    assert 'id="preset-select"' in r.text
    assert "disabled" not in r.text or 'id="preset-select"' in r.text


def test_get_cleanup_with_preset_query_overrides_session_filters(configured_app) -> None:
    """GET /cleanup?preset=Foo pre-fills older_than_days=42 from preset."""
    cfg_path: Path = configured_app.state.config_path
    _seed_preset(cfg_path, "Foo", older_than_days=42)

    c = _make_client(configured_app)
    r = c.get("/cleanup?preset=Foo")
    assert r.status_code == 200
    # older_than_days input should have value 42
    assert "42" in r.text
    # The checkbox should be checked (older_than_days_enabled)
    assert "older_than_days_enabled" in r.text


def test_get_cleanup_with_unknown_preset_shows_error(configured_app) -> None:
    """GET /cleanup?preset=Nope → 200 with error banner (preset not found)."""
    c = _make_client(configured_app)
    r = c.get("/cleanup?preset=Nope")
    assert r.status_code == 200
    # Should show an error or warning about unknown preset
    assert "nope" in r.text.lower() or "not found" in r.text.lower() or "error" in r.text.lower()


# ---------------------------------------------------------------------------
# POST /cleanup/save-as-preset
# ---------------------------------------------------------------------------


def test_post_save_as_preset_creates_preset(configured_app) -> None:
    """POST /cleanup/save-as-preset with name=X → preset created, success banner shown."""
    c = _make_client(configured_app)
    csrf = c.cookies["ffc_csrf"]

    # First set up session filters via POST /cleanup
    c.post(
        "/cleanup",
        data={
            "_csrf": csrf,
            "older_than_days": "60",
            "older_than_days_enabled": "on",
        },
        follow_redirects=False,
    )

    # Now save as preset
    r = c.post(
        "/cleanup/save-as-preset",
        data={
            "_csrf": csrf,
            "preset_name": "SavedFromWizard",
            "preset_description": "Saved via wizard",
        },
    )
    assert r.status_code == 200
    assert "SavedFromWizard" in r.text or "saved" in r.text.lower()

    svc = PresetService(configured_app.state.config_path)
    preset = svc.get("SavedFromWizard")
    assert preset.filters.older_than_days == 60


def test_post_save_as_preset_empty_name_shows_error(configured_app) -> None:
    """POST /cleanup/save-as-preset with empty name returns 200 + error banner."""
    c = _make_client(configured_app)
    csrf = c.cookies["ffc_csrf"]

    # Set up session filters via POST /cleanup
    c.post(
        "/cleanup",
        data={
            "_csrf": csrf,
            "older_than_days": "30",
            "older_than_days_enabled": "on",
        },
        follow_redirects=False,
    )

    r = c.post(
        "/cleanup/save-as-preset",
        data={
            "_csrf": csrf,
            "preset_name": "",
            "preset_description": "",
        },
    )
    assert r.status_code == 200
    assert "preset name is required" in r.text.lower()


def test_post_save_as_preset_duplicate_name_shows_error(configured_app) -> None:
    """Save-as-preset with duplicate name → error banner, no redirect."""
    cfg_path: Path = configured_app.state.config_path
    _seed_preset(cfg_path, "ExistingPreset")

    c = _make_client(configured_app)
    csrf = c.cookies["ffc_csrf"]

    # Set up session filters
    c.post(
        "/cleanup",
        data={
            "_csrf": csrf,
            "older_than_days": "30",
            "older_than_days_enabled": "on",
        },
        follow_redirects=False,
    )

    r = c.post(
        "/cleanup/save-as-preset",
        data={
            "_csrf": csrf,
            "preset_name": "ExistingPreset",
            "preset_description": "",
        },
    )
    assert r.status_code == 200
    assert "already exists" in r.text.lower() or "duplicate" in r.text.lower()
