"""Tests for the /presets CRUD UI (Task 6.4)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from firefliesclearer.application.preset_service import PresetService
from firefliesclearer.infra.config import ScanFiltersModel

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

NOW = datetime(2026, 4, 30, 12, 0, tzinfo=UTC)


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
# GET /presets — list page
# ---------------------------------------------------------------------------


def test_list_empty(configured_app) -> None:
    """No presets → 200, empty-state CTA rendered."""
    c = _make_client(configured_app)
    r = c.get("/presets")
    assert r.status_code == 200
    assert "first preset" in r.text.lower() or "no presets" in r.text.lower()


def test_list_renders_presets(configured_app) -> None:
    """Two seeded presets appear; default has star marker."""
    cfg_path: Path = configured_app.state.config_path
    _seed_preset(cfg_path, "Alpha", default=True)
    _seed_preset(cfg_path, "Beta", default=False)

    c = _make_client(configured_app)
    r = c.get("/presets")
    assert r.status_code == 200
    assert "Alpha" in r.text
    assert "Beta" in r.text
    # Default star marker
    assert "★" in r.text or "default" in r.text.lower()


# ---------------------------------------------------------------------------
# GET /presets/new
# ---------------------------------------------------------------------------


def test_get_new_form_renders(configured_app) -> None:
    """GET /presets/new → 200 with name/description inputs."""
    c = _make_client(configured_app)
    r = c.get("/presets/new")
    assert r.status_code == 200
    assert 'name="preset_name"' in r.text
    assert 'name="preset_description"' in r.text


# ---------------------------------------------------------------------------
# POST /presets — create
# ---------------------------------------------------------------------------


def test_post_create_redirects_to_list(configured_app) -> None:
    """Valid POST /presets → 303 redirect to /presets, preset persisted."""
    c = _make_client(configured_app)
    csrf = c.cookies["ffc_csrf"]
    r = c.post(
        "/presets",
        data={
            "_csrf": csrf,
            "preset_name": "My Preset",
            "preset_description": "A test preset",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/presets"

    svc = PresetService(configured_app.state.config_path)
    preset = svc.get("My Preset")
    assert preset.name == "My Preset"
    assert preset.description == "A test preset"


def test_post_create_duplicate_name_shows_error(configured_app) -> None:
    """Creating twice with same name → 200 with error message."""
    cfg_path: Path = configured_app.state.config_path
    _seed_preset(cfg_path, "DupName")

    c = _make_client(configured_app)
    csrf = c.cookies["ffc_csrf"]
    r = c.post(
        "/presets",
        data={"_csrf": csrf, "preset_name": "DupName"},
        follow_redirects=False,
    )
    assert r.status_code == 200
    assert "already exists" in r.text.lower() or "duplicate" in r.text.lower()


def test_post_create_invalid_name_too_short_shows_error(configured_app) -> None:
    """POST with empty name → 200 with validation error."""
    c = _make_client(configured_app)
    csrf = c.cookies["ffc_csrf"]
    r = c.post(
        "/presets",
        data={"_csrf": csrf, "preset_name": ""},
        follow_redirects=False,
    )
    assert r.status_code == 200
    # Should show an error about name being required/too short
    assert "name" in r.text.lower()


# ---------------------------------------------------------------------------
# GET /presets/{name}/edit
# ---------------------------------------------------------------------------


def test_get_edit_form_renders_with_values(configured_app) -> None:
    """Seeded preset edit form pre-populates the name."""
    cfg_path: Path = configured_app.state.config_path
    _seed_preset(cfg_path, "Foo", description="Foo desc")

    c = _make_client(configured_app)
    r = c.get("/presets/Foo/edit")
    assert r.status_code == 200
    assert "Foo" in r.text
    assert "Foo desc" in r.text


def test_get_edit_404_for_missing(configured_app) -> None:
    """GET /presets/Nope/edit → 404."""
    c = _make_client(configured_app)
    r = c.get("/presets/Nope/edit")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# POST /presets/{name} — update
# ---------------------------------------------------------------------------


def test_post_update_modifies_preset(configured_app) -> None:
    """POST /presets/Foo with new description → preset.description updated."""
    cfg_path: Path = configured_app.state.config_path
    _seed_preset(cfg_path, "Foo", description="original")

    c = _make_client(configured_app)
    csrf = c.cookies["ffc_csrf"]
    r = c.post(
        "/presets/Foo",
        data={"_csrf": csrf, "preset_description": "updated"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/presets"

    svc = PresetService(configured_app.state.config_path)
    preset = svc.get("Foo")
    assert preset.description == "updated"


def test_post_update_default_unsets_others(configured_app) -> None:
    """POST /presets/B with default=on → A.default=False, B.default=True."""
    cfg_path: Path = configured_app.state.config_path
    _seed_preset(cfg_path, "A", default=True)
    _seed_preset(cfg_path, "B", default=False)

    c = _make_client(configured_app)
    csrf = c.cookies["ffc_csrf"]
    c.post(
        "/presets/B",
        data={"_csrf": csrf, "preset_description": "", "preset_default": "on"},
        follow_redirects=False,
    )

    svc = PresetService(configured_app.state.config_path)
    assert svc.get("A").default is False
    assert svc.get("B").default is True


# ---------------------------------------------------------------------------
# POST /presets/{name}/delete
# ---------------------------------------------------------------------------


def test_post_delete_removes_preset(configured_app) -> None:
    """POST /presets/Foo/delete → 303 redirect, preset gone."""
    cfg_path: Path = configured_app.state.config_path
    _seed_preset(cfg_path, "Foo")

    c = _make_client(configured_app)
    csrf = c.cookies["ffc_csrf"]
    r = c.post(
        "/presets/Foo/delete",
        data={"_csrf": csrf},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/presets"

    svc = PresetService(configured_app.state.config_path)
    from firefliesclearer.application.preset_service import PresetNotFoundError

    with pytest.raises(PresetNotFoundError):
        svc.get("Foo")


def test_post_delete_404_for_missing(configured_app) -> None:
    """POST /presets/Nope/delete → 404."""
    c = _make_client(configured_app)
    csrf = c.cookies["ffc_csrf"]
    r = c.post(
        "/presets/Nope/delete",
        data={"_csrf": csrf},
        follow_redirects=False,
    )
    assert r.status_code == 404
