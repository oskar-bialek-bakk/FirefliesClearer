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


def test_post_trash_confirm_typed_count_mismatch_returns_422(
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
    _wizard(configured_app, sid, selected=["m0", "m1", "m2"], trash=["m0", "m1", "m2"])
    r = configured_client.post(
        "/cleanup/trash-confirm",
        data={
            "_csrf": configured_client.cookies.get("ffc_csrf", ""),
            "confirmed_count": "2",  # wrong (should be 3)
            "trash_keep": ["m0", "m1", "m2"],
        },
    )
    assert r.status_code == 422
    # State unchanged.
    state = configured_app.state.session_store.get(sid).get("wizard", {})
    assert sorted(state.get("trash_ids", [])) == ["m0", "m1", "m2"]


def test_post_trash_confirm_demotes_unchecked_rows_to_archive(
    configured_client: TestClient, configured_app
) -> None:
    meetings = [
        Meeting(
            meeting_id="m_keep",
            title="Standup keep",
            meeting_date=NOW - timedelta(days=10),
            duration_minutes=15.0,
            host_email="oskar@example.com",
            participant_count=4,
        ),
        Meeting(
            meeting_id="m_demote",
            title="Standup demote",
            meeting_date=NOW - timedelta(days=20),
            duration_minutes=15.0,
            host_email="oskar@example.com",
            participant_count=4,
        ),
    ]
    _seed(configured_app, meetings)
    sid = configured_client.cookies.get("ffc_session", "")
    _wizard(configured_app, sid, selected=["m_keep", "m_demote"], trash=["m_keep", "m_demote"])
    r = configured_client.post(
        "/cleanup/trash-confirm",
        data={
            "_csrf": configured_client.cookies.get("ffc_csrf", ""),
            "confirmed_count": "1",  # only m_keep stays trash
            "trash_keep": ["m_keep"],
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/cleanup/archive"
    state = configured_app.state.session_store.get(sid).get("wizard", {})
    assert state.get("trash_ids") == ["m_keep"]
    # m_demote is still selected (will be archived).
    assert sorted(state.get("selected_ids", [])) == ["m_demote", "m_keep"]


def test_post_trash_confirm_auto_marks_non_host_rows_deleted(
    configured_client: TestClient, configured_app
) -> None:
    from firefliesclearer.core.models import MeetingState

    configured_app.state.deps.config.fireflies.user_email = "oskar@example.com"
    meetings = [
        Meeting(
            meeting_id="m_host",
            title="My standup",
            meeting_date=NOW - timedelta(days=10),
            duration_minutes=15.0,
            host_email="oskar@example.com",
            participant_count=4,
        ),
        Meeting(
            meeting_id="m_other",
            title="Their standup",
            meeting_date=NOW - timedelta(days=10),
            duration_minutes=15.0,
            host_email="other@example.com",
            participant_count=4,
        ),
    ]
    _seed(configured_app, meetings)
    sid = configured_client.cookies.get("ffc_session", "")
    _wizard(configured_app, sid, selected=["m_host", "m_other"], trash=["m_host", "m_other"])
    configured_client.post(
        "/cleanup/trash-confirm",
        data={
            "_csrf": configured_client.cookies.get("ffc_csrf", ""),
            "confirmed_count": "2",
            "trash_keep": ["m_host", "m_other"],
        },
        follow_redirects=False,
    )
    manifest = configured_app.state.deps.manifest
    # Non-host trash auto-transitioned to DELETED with the existing reason.
    rec_other = manifest.get("m_other")
    assert rec_other is not None and rec_other.state is MeetingState.DELETED
    last = manifest.state_log("m_other")[-1]
    assert last.from_state is MeetingState.KNOWN
    assert last.to_state is MeetingState.DELETED
    assert last.details == {"reason": "non_host_no_api_delete"}
    # Host trash stays in KNOWN, stays in trash_ids.
    rec_host = manifest.get("m_host")
    assert rec_host is not None and rec_host.state is MeetingState.KNOWN
    state = configured_app.state.session_store.get(sid).get("wizard", {})
    assert state.get("trash_ids") == ["m_host"]


def test_post_trash_confirm_redirects_to_archive_when_no_trash_ids(
    configured_client: TestClient, configured_app
) -> None:
    sid = configured_client.cookies.get("ffc_session", "")
    _wizard(configured_app, sid, selected=["m1"], trash=[])
    r = configured_client.post(
        "/cleanup/trash-confirm",
        data={"_csrf": configured_client.cookies.get("ffc_csrf", "")},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/cleanup/archive"
