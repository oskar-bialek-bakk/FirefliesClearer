"""Tests for GET /history — filters, pagination, URL round-trip."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from selectolax.parser import HTMLParser

from firefliesclearer.core.models import Meeting, MeetingState

NOW = datetime(2026, 4, 29, 12, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_meeting(
    manifest,
    *,
    meeting_id: str,
    title: str = "Test Meeting",
    meeting_date: datetime = NOW,
    state: MeetingState = MeetingState.ARCHIVED,
    archived_at: datetime | None = None,
    deleted_at: datetime | None = None,
) -> None:
    """Register a meeting and drive it to the desired state via legal transitions.

    Transition chain:
      - PENDING → ARCHIVED (if archived_at provided, or state needs it)
      - ARCHIVED → DELETED (if deleted_at provided)
      - Otherwise PENDING → target state directly (for failed states etc.)
    """
    meeting = Meeting(
        meeting_id=meeting_id,
        title=title,
        meeting_date=meeting_date,
        duration_minutes=30.0,
        host_email="h@x.com",
        participant_count=2,
    )
    manifest.register(meeting, at=NOW)

    if state == MeetingState.DELETED:
        # Must go PENDING → ARCHIVED → DELETED
        manifest.transition(meeting_id, to=MeetingState.ARCHIVED, at=archived_at or NOW)
        manifest.transition(meeting_id, to=MeetingState.DELETED, at=deleted_at or NOW)
    elif state == MeetingState.ARCHIVED:
        manifest.transition(meeting_id, to=MeetingState.ARCHIVED, at=archived_at or NOW)
    elif state != MeetingState.PENDING:
        # Failed states etc. — transition directly from PENDING
        manifest.transition(meeting_id, to=state, at=NOW)


@pytest.fixture
def configured_client(configured_app) -> TestClient:
    """Authenticated client against a configured app."""
    c = TestClient(configured_app)
    c.get("/?token=T")
    return c


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_history_renders_empty_state(configured_client: TestClient) -> None:
    """No archived/deleted meetings → 200, empty-state text, total=0."""
    r = configured_client.get("/history")
    assert r.status_code == 200
    doc = HTMLParser(r.text)
    empty = doc.css_first(".history-empty")
    assert empty is not None, "Expected empty-state element with class history-empty"
    total = doc.css_first(".history-total")
    assert total is not None
    assert "0 meetings" in total.text()


def test_history_renders_seeded_rows(configured_client: TestClient, configured_app) -> None:
    """3 archived meetings → 200, all 3 rows visible."""
    manifest = configured_app.state.deps.manifest
    for i in range(3):
        _seed_meeting(
            manifest,
            meeting_id=f"m{i}",
            title=f"Meeting {i}",
            state=MeetingState.ARCHIVED,
            archived_at=NOW - timedelta(days=i),
        )

    r = configured_client.get("/history?range=all-time")
    assert r.status_code == 200
    doc = HTMLParser(r.text)
    rows = doc.css(".history-table tbody tr")
    assert len(rows) == 3
    total = doc.css_first(".history-total")
    assert total is not None
    assert "3 meetings" in total.text()


def test_history_filter_by_state(configured_client: TestClient, configured_app) -> None:
    """?state=archived&state=deleted returns only rows with those states."""
    manifest = configured_app.state.deps.manifest
    _seed_meeting(manifest, meeting_id="a1", state=MeetingState.ARCHIVED, archived_at=NOW)
    _seed_meeting(
        manifest,
        meeting_id="d1",
        state=MeetingState.DELETED,
        archived_at=NOW - timedelta(hours=2),
        deleted_at=NOW - timedelta(hours=1),
    )
    # Seed a failed meeting — should NOT appear
    meeting = Meeting(
        meeting_id="f1",
        title="Failed Meeting",
        meeting_date=NOW,
        duration_minutes=10.0,
        host_email="h@x.com",
        participant_count=1,
    )
    manifest.register(meeting, at=NOW)
    manifest.transition("f1", to=MeetingState.FAILED_DOWNLOAD, at=NOW)

    r = configured_client.get("/history?range=all-time&state=archived&state=deleted")
    assert r.status_code == 200
    doc = HTMLParser(r.text)
    rows = doc.css(".history-table tbody tr")
    assert len(rows) == 2
    texts = [row.text() for row in rows]
    assert not any("Failed" in t for t in texts)


def test_history_filter_by_title_search(configured_client: TestClient, configured_app) -> None:
    """?q=foo returns only rows whose title contains 'foo' (case-insensitive)."""
    manifest = configured_app.state.deps.manifest
    _seed_meeting(
        manifest, meeting_id="m1", title="Foo Meeting", state=MeetingState.ARCHIVED, archived_at=NOW
    )
    _seed_meeting(
        manifest, meeting_id="m2", title="Bar Meeting", state=MeetingState.ARCHIVED, archived_at=NOW
    )
    _seed_meeting(
        manifest, meeting_id="m3", title="foobar sync", state=MeetingState.ARCHIVED, archived_at=NOW
    )

    r = configured_client.get("/history?range=all-time&q=foo")
    assert r.status_code == 200
    doc = HTMLParser(r.text)
    rows = doc.css(".history-table tbody tr")
    assert len(rows) == 2
    texts = " ".join(row.text() for row in rows)
    assert "Foo" in texts or "foo" in texts
    assert "Bar Meeting" not in texts


def test_history_filter_by_range_last_7d(configured_client: TestClient, configured_app) -> None:
    """Meetings with deleted_at older than 7 days are excluded from last-7d."""
    manifest = configured_app.state.deps.manifest
    recent_date = NOW - timedelta(days=3)
    old_date = NOW - timedelta(days=20)

    _seed_meeting(
        manifest,
        meeting_id="recent",
        title="Recent Meeting",
        state=MeetingState.DELETED,
        archived_at=recent_date - timedelta(hours=1),
        deleted_at=recent_date,
    )
    _seed_meeting(
        manifest,
        meeting_id="old",
        title="Old Meeting",
        state=MeetingState.DELETED,
        archived_at=old_date - timedelta(hours=1),
        deleted_at=old_date,
    )

    r = configured_client.get("/history?range=last-7d")
    assert r.status_code == 200
    doc = HTMLParser(r.text)
    rows = doc.css(".history-table tbody tr")
    assert len(rows) == 1
    assert "Recent Meeting" in rows[0].text()


def test_history_filter_by_custom_range(configured_client: TestClient, configured_app) -> None:
    """?range=custom&from=2026-01-01&to=2026-02-01 filters by deleted_at in that range."""
    manifest = configured_app.state.deps.manifest
    in_range_date = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
    out_of_range_date = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)

    _seed_meeting(
        manifest,
        meeting_id="in",
        title="In Range Meeting",
        state=MeetingState.DELETED,
        archived_at=in_range_date - timedelta(hours=1),
        deleted_at=in_range_date,
    )
    _seed_meeting(
        manifest,
        meeting_id="out",
        title="Out of Range Meeting",
        state=MeetingState.DELETED,
        archived_at=out_of_range_date - timedelta(hours=1),
        deleted_at=out_of_range_date,
    )

    r = configured_client.get("/history?range=custom&from=2026-01-01&to=2026-02-01")
    assert r.status_code == 200
    doc = HTMLParser(r.text)
    rows = doc.css(".history-table tbody tr")
    assert len(rows) == 1
    assert "In Range Meeting" in rows[0].text()


def test_history_pagination_page_2(configured_client: TestClient, configured_app) -> None:
    """Seed 75 rows; ?page=2 shows 25 rows with total_pages=2."""
    manifest = configured_app.state.deps.manifest
    for i in range(75):
        _seed_meeting(
            manifest,
            meeting_id=f"pm{i:03}",
            title=f"Paginated Meeting {i:03}",
            state=MeetingState.ARCHIVED,
            archived_at=NOW - timedelta(minutes=i),
        )

    r = configured_client.get("/history?range=all-time&page=2")
    assert r.status_code == 200
    doc = HTMLParser(r.text)
    rows = doc.css(".history-table tbody tr")
    assert len(rows) == 25
    total = doc.css_first(".history-total")
    assert "75 meetings" in total.text()
    pagination = doc.css_first(".pagination")
    assert pagination is not None
    assert "Page 2 of 2" in pagination.text()


def test_history_url_round_trip(configured_client: TestClient, configured_app) -> None:
    """Filters submitted via GET are preserved in pagination links."""
    manifest = configured_app.state.deps.manifest
    for i in range(75):
        _seed_meeting(
            manifest,
            meeting_id=f"rt{i:03}",
            title=f"RoundTrip Meeting {i:03}",
            state=MeetingState.ARCHIVED,
            archived_at=NOW - timedelta(minutes=i),
        )

    r = configured_client.get("/history?range=all-time&q=RoundTrip&page=1")
    assert r.status_code == 200
    doc = HTMLParser(r.text)
    pagination = doc.css_first(".pagination")
    assert pagination is not None
    next_link = pagination.css_first("a")
    assert next_link is not None
    href = next_link.attributes.get("href", "")
    # All filter params should be preserved in the next-page link
    assert "range=all-time" in href
    assert "q=RoundTrip" in href
    assert "page=2" in href


def test_history_invalid_state_value_silently_ignored(
    configured_client: TestClient, configured_app
) -> None:
    """?state=NOT_A_STATE doesn't 500; returns all rows without state filter."""
    manifest = configured_app.state.deps.manifest
    _seed_meeting(manifest, meeting_id="x1", state=MeetingState.ARCHIVED, archived_at=NOW)

    r = configured_client.get("/history?range=all-time&state=NOT_A_STATE")
    assert r.status_code == 200
    # Should still show results (no state filter applied for unknown value)
    doc = HTMLParser(r.text)
    rows = doc.css(".history-table tbody tr")
    assert len(rows) == 1


def test_history_invalid_page_clamped_to_1(configured_client: TestClient, configured_app) -> None:
    """?page=0 and ?page=-5 are clamped to page 1, no 500."""
    manifest = configured_app.state.deps.manifest
    _seed_meeting(manifest, meeting_id="y1", state=MeetingState.ARCHIVED, archived_at=NOW)

    for bad_page in ["0", "-5"]:
        r = configured_client.get(f"/history?range=all-time&page={bad_page}")
        assert r.status_code == 200, f"Expected 200 for page={bad_page}"
        doc = HTMLParser(r.text)
        pagination_or_total = doc.css_first(".history-total")
        assert pagination_or_total is not None
