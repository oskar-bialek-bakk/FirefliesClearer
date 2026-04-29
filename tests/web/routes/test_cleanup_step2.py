"""Tests for cleanup wizard Step 2 — Review (table + selection + side panel)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from selectolax.parser import HTMLParser

from firefliesclearer.application.scan_service import ScanFilters
from firefliesclearer.core.models import Meeting
from firefliesclearer.web.wizard_session import (
    WizardState,
    add_to_selection,
    filters_to_dict,
    set_state,
)
from tests.fakes.in_memory_repository import InMemoryMeetingRepository

NOW = datetime(2026, 4, 29, 12, 0, tzinfo=UTC)


@pytest.fixture
def configured_client(configured_app) -> TestClient:
    c = TestClient(configured_app)
    c.get("/?token=T")
    return c


def _seed_repo_with_meetings(app, count: int) -> list[Meeting]:
    meetings = [
        Meeting(
            meeting_id=f"m{i}",
            title=f"Meeting {i}",
            meeting_date=NOW - timedelta(days=100 + i),
            duration_minutes=30.0,
            host_email="h@x",
            participant_count=4,
        )
        for i in range(count)
    ]
    repo = InMemoryMeetingRepository(meetings=meetings, api_key="ff_test")
    repo.set_user_email_for_key("ff_test", "oskar@example.com")
    app.state.deps.client = repo
    return meetings


def _sid_from_client(c: TestClient) -> str:
    return c.cookies.get("ffc_session", "") or ""


def _set_filters_in_session(app, sid: str, filters: ScanFilters) -> None:
    state = WizardState(
        step="review",
        filters=filters_to_dict(filters),
        selected_ids=[],
        operation_id=None,
    )
    set_state(app.state.session_store, sid, state)


def _csrf(c: TestClient) -> str:
    return c.cookies.get("ffc_csrf", "") or ""


# ---------------------------------------------------------------------------
# GET /cleanup/review
# ---------------------------------------------------------------------------


def test_get_review_redirects_to_step1_when_no_filters(
    configured_client: TestClient,
) -> None:
    r = configured_client.get("/cleanup/review", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/cleanup"


def test_get_review_renders_table_with_matches(
    configured_client: TestClient, configured_app
) -> None:
    _seed_repo_with_meetings(configured_app, 5)
    _set_filters_in_session(
        configured_app,
        _sid_from_client(configured_client),
        ScanFilters(older_than_days=30),
    )
    r = configured_client.get("/cleanup/review")
    assert r.status_code == 200
    doc = HTMLParser(r.text)
    rows = doc.css(".row[data-meeting-id]")
    assert len(rows) == 5
    # No "Select all N matches" banner when total <= 100.
    assert doc.css_first(".banner-select-all") is None
    # Stepper says step 2 is active.
    active = doc.css_first("nav.wizard-stepper li.step.active[data-step='review']")
    assert active is not None
    # Continue button is rendered.
    assert doc.css_first(".continue-btn") is not None


def test_get_review_paginates_at_100(configured_client: TestClient, configured_app) -> None:
    _seed_repo_with_meetings(configured_app, 250)
    _set_filters_in_session(
        configured_app,
        _sid_from_client(configured_client),
        ScanFilters(older_than_days=30),
    )
    r = configured_client.get("/cleanup/review")
    doc = HTMLParser(r.text)
    rows = doc.css(".row[data-meeting-id]")
    assert len(rows) == 100
    banner = doc.css_first(".banner-select-all")
    assert banner is not None
    assert "250" in banner.text()
    nav = doc.css_first(".pagination")
    assert nav is not None


def test_get_review_page_2_returns_next_slice(
    configured_client: TestClient, configured_app
) -> None:
    _seed_repo_with_meetings(configured_app, 250)
    _set_filters_in_session(
        configured_app,
        _sid_from_client(configured_client),
        ScanFilters(older_than_days=30),
    )
    r = configured_client.get("/cleanup/review?page=2")
    assert r.status_code == 200
    doc = HTMLParser(r.text)
    rows = doc.css(".row[data-meeting-id]")
    assert len(rows) == 100


def test_htmx_header_returns_table_only(configured_client: TestClient, configured_app) -> None:
    _seed_repo_with_meetings(configured_app, 5)
    _set_filters_in_session(
        configured_app,
        _sid_from_client(configured_client),
        ScanFilters(older_than_days=30),
    )
    r = configured_client.get("/cleanup/review", headers={"HX-Request": "true"})
    assert r.status_code == 200
    # No <html> root, just the table fragment.
    assert "<html" not in r.text.lower()
    doc = HTMLParser(r.text)
    assert doc.css_first(".review-table") is not None


# ---------------------------------------------------------------------------
# POST /cleanup/review/toggle/{meeting_id}
# ---------------------------------------------------------------------------


def test_toggle_adds_and_removes_from_selection(
    configured_client: TestClient, configured_app
) -> None:
    _seed_repo_with_meetings(configured_app, 5)
    sid = _sid_from_client(configured_client)
    _set_filters_in_session(configured_app, sid, ScanFilters(older_than_days=30))

    r = configured_client.post(
        "/cleanup/review/toggle/m1",
        data={"_csrf": _csrf(configured_client)},
    )
    assert r.status_code == 200
    selected = set(configured_app.state.session_store.get(sid)["wizard"]["selected_ids"])
    assert "m1" in selected

    r = configured_client.post(
        "/cleanup/review/toggle/m1",
        data={"_csrf": _csrf(configured_client)},
    )
    assert r.status_code == 200
    selected = set(configured_app.state.session_store.get(sid)["wizard"]["selected_ids"])
    assert "m1" not in selected


# ---------------------------------------------------------------------------
# POST /cleanup/review/select-all
# ---------------------------------------------------------------------------


def test_select_all_page_adds_current_page_ids(
    configured_client: TestClient, configured_app
) -> None:
    _seed_repo_with_meetings(configured_app, 250)
    sid = _sid_from_client(configured_client)
    _set_filters_in_session(configured_app, sid, ScanFilters(older_than_days=30))
    r = configured_client.post(
        "/cleanup/review/select-all",
        data={"_csrf": _csrf(configured_client), "page": "1"},
    )
    assert r.status_code == 200
    selected = set(configured_app.state.session_store.get(sid)["wizard"]["selected_ids"])
    assert len(selected) == 100  # only page 1


def test_select_all_with_all_flag_adds_every_match(
    configured_client: TestClient, configured_app
) -> None:
    _seed_repo_with_meetings(configured_app, 250)
    sid = _sid_from_client(configured_client)
    _set_filters_in_session(configured_app, sid, ScanFilters(older_than_days=30))
    r = configured_client.post(
        "/cleanup/review/select-all",
        data={"_csrf": _csrf(configured_client), "all": "true"},
    )
    assert r.status_code == 200
    selected = set(configured_app.state.session_store.get(sid)["wizard"]["selected_ids"])
    assert len(selected) == 250


# ---------------------------------------------------------------------------
# POST /cleanup/review/deselect-all
# ---------------------------------------------------------------------------


def test_deselect_all_page_removes_current_page_ids(
    configured_client: TestClient, configured_app
) -> None:
    meetings = _seed_repo_with_meetings(configured_app, 250)
    sid = _sid_from_client(configured_client)
    _set_filters_in_session(configured_app, sid, ScanFilters(older_than_days=30))
    # Pre-select all 250 ids.
    add_to_selection(
        configured_app.state.session_store,
        sid,
        [m.meeting_id for m in meetings],
    )

    r = configured_client.post(
        "/cleanup/review/deselect-all",
        data={"_csrf": _csrf(configured_client), "page": "1"},
    )
    assert r.status_code == 200
    selected = set(configured_app.state.session_store.get(sid)["wizard"]["selected_ids"])
    # Page 1 (100 ids) removed; pages 2-3 (150 ids) remain.
    assert len(selected) == 150


def test_deselect_all_with_all_flag_clears_selection(
    configured_client: TestClient, configured_app
) -> None:
    meetings = _seed_repo_with_meetings(configured_app, 250)
    sid = _sid_from_client(configured_client)
    _set_filters_in_session(configured_app, sid, ScanFilters(older_than_days=30))
    add_to_selection(
        configured_app.state.session_store,
        sid,
        [m.meeting_id for m in meetings],
    )

    r = configured_client.post(
        "/cleanup/review/deselect-all",
        data={"_csrf": _csrf(configured_client), "all": "true"},
    )
    assert r.status_code == 200
    selected = configured_app.state.session_store.get(sid)["wizard"]["selected_ids"]
    assert selected == []


# ---------------------------------------------------------------------------
# POST /cleanup/review/invert
# ---------------------------------------------------------------------------


def test_invert_flips_current_page_only(configured_client: TestClient, configured_app) -> None:
    meetings = _seed_repo_with_meetings(configured_app, 250)
    sid = _sid_from_client(configured_client)
    _set_filters_in_session(configured_app, sid, ScanFilters(older_than_days=30))
    # Pre-select the first 50 ids (subset of page 1) plus one off-page id.
    add_to_selection(
        configured_app.state.session_store,
        sid,
        [m.meeting_id for m in meetings[:50]] + [meetings[200].meeting_id],
    )

    r = configured_client.post(
        "/cleanup/review/invert",
        data={"_csrf": _csrf(configured_client), "page": "1"},
    )
    assert r.status_code == 200
    selected = set(configured_app.state.session_store.get(sid)["wizard"]["selected_ids"])
    # Page 1 had 100 rows; first 50 were selected. Inverting flips them all,
    # so the 50 that were selected become unselected, and the 50 unselected
    # become selected. Off-page id (m200) is untouched.
    page1_ids = {m.meeting_id for m in meetings[:100]}
    assert selected == (page1_ids - {m.meeting_id for m in meetings[:50]}) | {
        meetings[200].meeting_id
    }


# ---------------------------------------------------------------------------
# GET /cleanup/meeting/{id}/panel
# ---------------------------------------------------------------------------


def test_side_panel_renders_meeting_details(configured_client: TestClient, configured_app) -> None:
    _seed_repo_with_meetings(configured_app, 3)
    sid = _sid_from_client(configured_client)
    _set_filters_in_session(configured_app, sid, ScanFilters(older_than_days=30))
    r = configured_client.get("/cleanup/meeting/m1/panel")
    assert r.status_code == 200
    assert "Meeting 1" in r.text


def test_side_panel_404_when_meeting_not_in_matches(
    configured_client: TestClient, configured_app
) -> None:
    _seed_repo_with_meetings(configured_app, 3)
    sid = _sid_from_client(configured_client)
    _set_filters_in_session(configured_app, sid, ScanFilters(older_than_days=30))
    r = configured_client.get("/cleanup/meeting/missing/panel")
    assert r.status_code == 404


def test_side_panel_404_when_no_filters_in_session(
    configured_client: TestClient, configured_app
) -> None:
    _seed_repo_with_meetings(configured_app, 3)
    # Don't set filters in session.
    r = configured_client.get("/cleanup/meeting/m1/panel")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# POST /cleanup/review (Continue)
# ---------------------------------------------------------------------------


def test_post_review_with_empty_selection_renders_error(
    configured_client: TestClient, configured_app
) -> None:
    _seed_repo_with_meetings(configured_app, 5)
    sid = _sid_from_client(configured_client)
    _set_filters_in_session(configured_app, sid, ScanFilters(older_than_days=30))
    r = configured_client.post(
        "/cleanup/review",
        data={"_csrf": _csrf(configured_client)},
        follow_redirects=False,
    )
    assert r.status_code == 422
    assert "select at least one" in r.text.lower()


def test_post_review_with_selection_redirects_to_archive(
    configured_client: TestClient, configured_app
) -> None:
    _seed_repo_with_meetings(configured_app, 5)
    sid = _sid_from_client(configured_client)
    _set_filters_in_session(configured_app, sid, ScanFilters(older_than_days=30))
    add_to_selection(configured_app.state.session_store, sid, ["m1", "m2"])
    r = configured_client.post(
        "/cleanup/review",
        data={"_csrf": _csrf(configured_client)},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/cleanup/archive"
    state = configured_app.state.session_store.get(sid)["wizard"]
    assert state["step"] == "archive"
