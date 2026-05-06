"""Step 1 — second preset picker for trash classification."""

from __future__ import annotations

from fastapi.testclient import TestClient
from selectolax.parser import HTMLParser

from firefliesclearer.application.preset_service import PresetService
from firefliesclearer.infra.config import ScanFiltersModel


def _seed_preset(app, name: str, *, title_contains: list[str]) -> None:
    svc = PresetService(app.state.config_path)
    svc.create(
        name,
        "",
        ScanFiltersModel(title_contains=title_contains),
    )


def test_step1_renders_two_preset_pickers(configured_app) -> None:
    _seed_preset(configured_app, "old-meetings", title_contains=[])
    _seed_preset(configured_app, "Trash: Standups", title_contains=["standup"])
    with TestClient(configured_app) as c:
        c.get("/?token=T")
        r = c.get("/cleanup")
    assert r.status_code == 200
    doc = HTMLParser(r.text)
    cleanup_picker = doc.css_first("select[name='preset']")
    trash_picker = doc.css_first("select[name='trash_preset']")
    assert cleanup_picker is not None
    assert trash_picker is not None
    # Trash picker has an empty default option (the "(none)" choice).
    # selectolax represents value="" as None in the attributes dict.
    first_opt = trash_picker.css_first("option")
    assert first_opt is not None
    assert first_opt.attributes.get("value") in ("", None)
    # Both presets appear in both pickers (distinguished by name only).
    cleanup_names = {o.attributes.get("value") for o in cleanup_picker.css("option")}
    trash_names = {o.attributes.get("value") for o in trash_picker.css("option")}
    assert "old-meetings" in cleanup_names and "Trash: Standups" in cleanup_names
    assert "old-meetings" in trash_names and "Trash: Standups" in trash_names


def test_step1_post_persists_trash_classifier(configured_app) -> None:
    _seed_preset(configured_app, "Trash: Standups", title_contains=["standup"])
    with TestClient(configured_app) as c:
        c.get("/?token=T")
        c.post(
            "/cleanup",
            data={
                "_csrf": c.cookies.get("ffc_csrf", ""),
                "older_than_days": "30",
                "older_than_days_enabled": "1",
                "trash_preset": "Trash: Standups",
            },
            follow_redirects=False,
        )
        sid = c.cookies.get("ffc_session", "")
    state = configured_app.state.session_store.get(sid).get("wizard", {})
    assert state.get("trash_classifier_preset") == "Trash: Standups"


def test_step1_post_with_no_trash_classifier_persists_none(configured_app) -> None:
    with TestClient(configured_app) as c:
        c.get("/?token=T")
        c.post(
            "/cleanup",
            data={
                "_csrf": c.cookies.get("ffc_csrf", ""),
                "older_than_days": "30",
                "older_than_days_enabled": "1",
                "trash_preset": "",
            },
            follow_redirects=False,
        )
        sid = c.cookies.get("ffc_session", "")
    state = configured_app.state.session_store.get(sid).get("wizard", {})
    assert state.get("trash_classifier_preset") in (None, "")
