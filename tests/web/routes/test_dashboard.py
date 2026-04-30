"""Tests for the Dashboard route + sidebar status fragment."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from selectolax.parser import HTMLParser

from firefliesclearer.core.models import Meeting, MeetingState

NOW = datetime(2026, 4, 29, 12, 0, tzinfo=UTC)


def _seed_failed_meeting(manifest, meeting_id: str = "m1", title: str = "Test Standup") -> None:
    """Register a meeting and transition it to FAILED_DOWNLOAD with an error."""
    meeting = Meeting(
        meeting_id=meeting_id,
        title=title,
        meeting_date=NOW,
        duration_minutes=10.0,
        host_email="u@x.com",
        participant_count=2,
    )
    manifest.register(meeting, at=NOW)
    manifest.transition(
        meeting_id,
        to=MeetingState.FAILED_DOWNLOAD,
        at=NOW,
        last_error="Reset by peer",
    )


@pytest.fixture
def configured_client(configured_app) -> TestClient:
    """A client where setup is already complete (configured_app from conftest)."""
    c = TestClient(configured_app)
    c.get("/?token=T")
    return c


def test_dashboard_renders_with_zero_counts(configured_client: TestClient) -> None:
    r = configured_client.get("/")
    assert r.status_code == 200
    doc = HTMLParser(r.text)
    cards = doc.css(".state-count-card")
    assert len(cards) == 4
    # Empty manifest -> all zero
    for card in cards:
        assert "0" in card.text()


def test_dashboard_full_request_includes_sidebar_chrome(
    configured_client: TestClient,
) -> None:
    """Plain GET (no HX-Request header) returns the full HTML shell with sidebar."""
    r = configured_client.get("/")
    assert r.status_code == 200
    assert "<!doctype html>" in r.text.lower()
    assert '<aside class="sidebar">' in r.text
    assert 'id="page"' in r.text


def test_dashboard_htmx_request_returns_partial_without_sidebar(
    configured_client: TestClient,
) -> None:
    """HX-Request: true → partial render, no <html>/<aside class=sidebar> chrome.

    Regression for the duplicated-sidebar bug: clicking a sidebar nav link
    fires an hx-get with hx-target="#page". If the response includes a full
    HTML shell (sidebar + main + #page), HTMX swaps that whole tree into
    the existing #page, nesting a duplicate sidebar inside the content area.
    """
    r = configured_client.get("/", headers={"HX-Request": "true"})
    assert r.status_code == 200
    text = r.text
    # Inner content still rendered.
    assert ".state-count-card" in text or "state-count-card" in text
    # But the outer chrome is NOT included.
    assert "<!doctype" not in text.lower()
    assert '<aside class="sidebar">' not in text
    assert "<html " not in text and "<html>" not in text


def test_dashboard_shows_failed_count_when_failures_exist(
    configured_client: TestClient, configured_app
) -> None:
    manifest = configured_app.state.deps.manifest
    _seed_failed_meeting(manifest)

    r = configured_client.get("/")
    doc = HTMLParser(r.text)
    failed_card = doc.css_first("[data-state='failed']")
    assert failed_card is not None
    assert "1" in failed_card.text()


def test_needs_attention_lists_failed_meetings(
    configured_client: TestClient, configured_app
) -> None:
    manifest = configured_app.state.deps.manifest
    _seed_failed_meeting(manifest, meeting_id="m1", title="Test Standup")

    r = configured_client.get("/")
    doc = HTMLParser(r.text)
    rows = doc.css(".needs-attention-row")
    assert len(rows) == 1
    assert "Test Standup" in rows[0].text()
    assert "Reset by peer" in rows[0].text()


def test_dashboard_empty_state_when_no_failures(
    configured_client: TestClient,
) -> None:
    r = configured_client.get("/")
    doc = HTMLParser(r.text)
    empty = doc.css_first(".needs-attention-empty")
    assert empty is not None
    assert "All clear" in empty.text()


def test_sidebar_status_renders(configured_client: TestClient) -> None:
    r = configured_client.get("/sidebar/status")
    assert r.status_code == 200
    doc = HTMLParser(r.text)
    fragment = doc.css_first(".sidebar-status")
    assert fragment is not None
