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


def test_dashboard_includes_sync_banner(configured_app_sync_on) -> None:
    """Dashboard renders the sync banner partial when sync is enabled."""
    with TestClient(configured_app_sync_on) as client:
        client.get("/?token=T", follow_redirects=False)
        r = client.get("/")
        assert r.status_code == 200
        assert "sync-banner" in r.text


def test_dashboard_shows_opt_in_banner_when_sync_disabled(configured_app) -> None:
    """Existing users with [sync] enabled = false see the opt-in banner."""
    with TestClient(configured_app) as client:
        client.get("/?token=T", follow_redirects=False)
        r = client.get("/")
        assert r.status_code == 200
        assert "Enable local cache" in r.text


def test_dashboard_hides_opt_in_banner_when_sync_enabled(configured_app_sync_on) -> None:
    """Once sync is enabled, the opt-in banner does not appear."""
    with TestClient(configured_app_sync_on) as client:
        client.get("/?token=T", follow_redirects=False)
        r = client.get("/")
        assert r.status_code == 200
        assert "Enable local cache" not in r.text


def test_dashboard_hides_opt_in_banner_after_dismiss(configured_app) -> None:
    """After dismissal, the banner stays hidden even though sync is disabled."""
    with TestClient(configured_app) as client:
        client.get("/?token=T", follow_redirects=False)
        csrf = client.cookies.get("ffc_csrf", "") or ""
        # Dismiss the banner
        client.post(
            "/sync/enable",
            data={"_csrf": csrf, "action": "dismiss"},
            follow_redirects=False,
        )
        # Drop cached deps so the next dashboard request reloads the config
        configured_app.state.deps = None
        r = client.get("/")
        assert r.status_code == 200
        assert "Enable local cache" not in r.text


def test_dashboard_renders_with_zero_counts(configured_client: TestClient) -> None:
    r = configured_client.get("/")
    assert r.status_code == 200
    doc = HTMLParser(r.text)
    cards = doc.css(".state-count-card")
    # Total + Archived + Pending + Failed + Deleted = 5 cards.
    assert len(cards) == 5
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


def test_dashboard_includes_total_cached_card(
    configured_client: TestClient, configured_app
) -> None:
    """The full dashboard renders the new "Total cached" card so the user has
    a single number for everything in the manifest after a sync (the bulk of
    which is in KNOWN state, invisible in the action-oriented cards)."""
    manifest = configured_app.state.deps.manifest
    # Two synced rows + one failed row = 3 total.
    for mid in ("k1", "k2"):
        manifest.upsert_known(
            Meeting(
                meeting_id=mid,
                title="Synced",
                meeting_date=NOW,
                duration_minutes=10.0,
                host_email="u@x.com",
                participant_count=1,
            ),
            at=NOW,
        )
    _seed_failed_meeting(manifest, meeting_id="f1")

    r = configured_client.get("/")
    assert r.status_code == 200
    doc = HTMLParser(r.text)
    total_card = doc.css_first('[data-state="total"]')
    assert total_card is not None, "Total card missing from dashboard"
    value = total_card.css_first(".state-count-card__value")
    assert value is not None
    assert value.text().strip() == "3"


def test_dashboard_state_counts_endpoint_returns_polling_fragment(
    configured_client: TestClient, configured_app
) -> None:
    """``GET /dashboard/state-counts`` returns the cards section as a standalone
    fragment with the self-polling attributes intact, so HTMX keeps refreshing
    the counters as the sync scheduler adds rows in the background."""
    manifest = configured_app.state.deps.manifest
    manifest.upsert_known(
        Meeting(
            meeting_id="k1",
            title="m",
            meeting_date=NOW,
            duration_minutes=1.0,
            host_email="u@x.com",
            participant_count=0,
        ),
        at=NOW,
    )
    r = configured_client.get("/dashboard/state-counts")
    assert r.status_code == 200
    # Polling attributes survive the round-trip — without them, the swap
    # would replace the section with a static copy and stop polling.
    assert 'hx-trigger="every 10s"' in r.text
    assert 'hx-get="/dashboard/state-counts"' in r.text
    assert 'data-state="total"' in r.text
    # Reflects the seeded row.
    doc = HTMLParser(r.text)
    total = doc.css_first('[data-state="total"] .state-count-card__value')
    assert total is not None and total.text().strip() == "1"
