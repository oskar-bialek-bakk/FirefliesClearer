"""Step 2 — Archive toggle, trash classifier auto-fill, bulk actions."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from firefliesclearer.application.preset_service import PresetService
from firefliesclearer.application.scan_service import ScanFilters
from firefliesclearer.core.models import Meeting
from firefliesclearer.infra.config import ScanFiltersModel
from firefliesclearer.web.wizard_session import (
    WizardState,
    filters_to_dict,
    set_state,
)
from tests.fakes.in_memory_repository import InMemoryMeetingRepository

NOW = datetime(2026, 5, 6, 12, 0, tzinfo=UTC)


def _seed_meetings(app) -> list[Meeting]:
    meetings = [
        Meeting(
            meeting_id="m_standup",
            title="Daily Standup 2026-05-06",
            meeting_date=NOW - timedelta(days=40),
            duration_minutes=15.0,
            host_email="oskar@example.com",
            participant_count=4,
        ),
        Meeting(
            meeting_id="m_design",
            title="Design Review",
            meeting_date=NOW - timedelta(days=40),
            duration_minutes=60.0,
            host_email="oskar@example.com",
            participant_count=4,
        ),
    ]
    repo = InMemoryMeetingRepository(meetings=meetings, api_key="ff_test")
    repo.set_user_email_for_key("ff_test", "oskar@example.com")
    app.state.deps.client = repo
    for m in meetings:
        app.state.deps.manifest.upsert_known(m, at=NOW)
    return meetings


def _seed_trash_preset(app) -> None:
    """Create a trash preset matching only meetings whose title contains 'standup'."""
    PresetService(app.state.config_path).create(
        name="Trash: Standups",
        description="",
        filters=ScanFiltersModel(title_contains=["standup"]),
    )


def _set_wizard_with_trash_preset(app, sid: str) -> None:
    set_state(
        app.state.session_store,
        sid,
        WizardState(
            step="review",
            filters=filters_to_dict(ScanFilters(older_than_days=30)),
            selected_ids=[],
            operation_id=None,
            trash_ids=[],
            trash_classifier_preset="Trash: Standups",
            trash_candidate_ids=[],
        ),
    )


def test_review_render_populates_trash_candidate_ids(configured_app) -> None:
    _seed_meetings(configured_app)
    _seed_trash_preset(configured_app)
    with TestClient(configured_app) as c:
        c.get("/?token=T")
        sid = c.cookies.get("ffc_session", "")
        _set_wizard_with_trash_preset(configured_app, sid)
        r = c.get("/cleanup/review")
    assert r.status_code == 200
    state = configured_app.state.session_store.get(sid).get("wizard", {})
    assert state.get("trash_candidate_ids") == ["m_standup"]


def test_review_render_with_no_trash_preset_leaves_candidates_empty(configured_app) -> None:
    _seed_meetings(configured_app)
    with TestClient(configured_app) as c:
        c.get("/?token=T")
        sid = c.cookies.get("ffc_session", "")
        set_state(
            configured_app.state.session_store,
            sid,
            WizardState(
                step="review",
                filters=filters_to_dict(ScanFilters(older_than_days=30)),
                selected_ids=[],
                operation_id=None,
                trash_ids=[],
                trash_classifier_preset=None,
                trash_candidate_ids=[],
            ),
        )
        r = c.get("/cleanup/review")
    assert r.status_code == 200
    state = configured_app.state.session_store.get(sid).get("wizard", {})
    # No trash preset → candidates stay empty.
    assert state.get("trash_candidate_ids", []) == []
