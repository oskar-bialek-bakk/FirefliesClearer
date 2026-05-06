"""Step 3a — Trash confirmation (typed-count gate, demote, non-host auto-mark)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from selectolax.parser import HTMLParser

from firefliesclearer.application.scan_service import ScanFilters
from firefliesclearer.core.models import Meeting
from firefliesclearer.web.wizard_session import (
    WizardState,
    filters_to_dict,
    set_state,
)
from tests.fakes.in_memory_repository import InMemoryMeetingRepository

NOW = datetime(2026, 5, 6, 12, 0, tzinfo=UTC)


def _seed(app, meetings: list[Meeting]) -> None:
    repo = InMemoryMeetingRepository(meetings=meetings, api_key="ff_test")
    repo.set_user_email_for_key("ff_test", "oskar@example.com")
    app.state.deps.client = repo
    for m in meetings:
        app.state.deps.manifest.upsert_known(m, at=NOW)


def _wizard(app, sid: str, *, selected: list[str], trash: list[str]) -> None:
    set_state(
        app.state.session_store,
        sid,
        WizardState(
            step="purge",
            filters=filters_to_dict(ScanFilters(older_than_days=30)),
            selected_ids=selected,
            operation_id=None,
            trash_ids=trash,
            trash_classifier_preset=None,
            trash_candidate_ids=[],
        ),
    )


@pytest.fixture
def configured_client(configured_app):
    with TestClient(configured_app) as c:
        c.get("/?token=T")
        yield c


def test_get_trash_confirm_redirects_to_archive_when_trash_ids_empty(
    configured_client: TestClient, configured_app
) -> None:
    sid = configured_client.cookies.get("ffc_session", "")
    _wizard(configured_app, sid, selected=["m1"], trash=[])
    r = configured_client.get("/cleanup/trash-confirm", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/cleanup/archive"


def test_get_trash_confirm_renders_sorted_oldest_first(
    configured_client: TestClient, configured_app
) -> None:
    meetings = [
        Meeting(
            meeting_id="m_new",
            title="Newest standup",
            meeting_date=NOW - timedelta(days=10),
            duration_minutes=15.0,
            host_email="oskar@example.com",
            participant_count=4,
        ),
        Meeting(
            meeting_id="m_old",
            title="Oldest standup",
            meeting_date=NOW - timedelta(days=200),
            duration_minutes=15.0,
            host_email="oskar@example.com",
            participant_count=4,
        ),
    ]
    _seed(configured_app, meetings)
    sid = configured_client.cookies.get("ffc_session", "")
    _wizard(configured_app, sid, selected=["m_new", "m_old"], trash=["m_new", "m_old"])
    r = configured_client.get("/cleanup/trash-confirm")
    assert r.status_code == 200
    doc = HTMLParser(r.text)
    items = doc.css(".trash-confirm-list li")
    assert len(items) == 2
    titles = [li.text(deep=True).strip() for li in items]
    assert titles[0].startswith("Oldest standup")
    assert titles[1].startswith("Newest standup")
    # Warning banner is present.
    assert doc.css_first(".trash-warning-banner") is not None
    # Typed-count gate is present.
    assert doc.css_first("input#confirm-count") is not None


def test_get_trash_confirm_renders_count_in_summary(
    configured_client: TestClient, configured_app
) -> None:
    meetings = [
        Meeting(
            meeting_id=f"m{i}",
            title=f"Standup {i}",
            meeting_date=NOW - timedelta(days=10 + i),
            duration_minutes=15.0,
            host_email="oskar@example.com",
            participant_count=4,
        )
        for i in range(3)
    ]
    _seed(configured_app, meetings)
    sid = configured_client.cookies.get("ffc_session", "")
    _wizard(
        configured_app,
        sid,
        selected=["m0", "m1", "m2"],
        trash=["m0", "m1", "m2"],
    )
    r = configured_client.get("/cleanup/trash-confirm")
    assert r.status_code == 200
    text = HTMLParser(r.text).text()
    assert "3 meeting" in text
