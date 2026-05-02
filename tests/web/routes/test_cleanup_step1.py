"""Tests for cleanup wizard Step 1 — filter form, live preview-count, submit."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from selectolax.parser import HTMLParser

from firefliesclearer.core.models import Meeting
from tests.fakes.in_memory_repository import InMemoryMeetingRepository

NOW = datetime(2026, 4, 29, 12, 0, tzinfo=UTC)


@pytest.fixture
def configured_client(configured_app) -> TestClient:
    """Client where setup is already complete (configured_app from conftest)."""
    c = TestClient(configured_app)
    c.get("/?token=T")
    return c


def _seed_old_meetings(app, count: int, days_old: int = 90) -> None:
    """Replace the app's repo with one pre-seeded with *count* old meetings.

    Phase 6: also register meetings in the manifest so the cache-backed
    scan_repo (the only read path now) returns them.
    """
    when = NOW - timedelta(days=days_old)
    meetings = [
        Meeting(
            meeting_id=f"m{i}",
            title=f"Old Meeting {i}",
            meeting_date=when,
            duration_minutes=30.0,
            host_email="host@example.com",
            participant_count=5,
        )
        for i in range(count)
    ]
    fresh = InMemoryMeetingRepository(meetings=meetings, api_key="ff_test")
    fresh.set_user_email_for_key("ff_test", "oskar@example.com")
    app.state.deps.client = fresh
    for m in meetings:
        app.state.deps.manifest.upsert_known(m, at=NOW)


# ---------------------------------------------------------------------------
# GET /cleanup
# ---------------------------------------------------------------------------


def test_get_cleanup_renders_step1_form(configured_client: TestClient) -> None:
    r = configured_client.get("/cleanup")
    assert r.status_code == 200
    doc = HTMLParser(r.text)

    form = doc.css_first("form#cleanup-step1-form")
    assert form is not None

    inputs = doc.css(
        "form#cleanup-step1-form input, "
        "form#cleanup-step1-form select, "
        "form#cleanup-step1-form textarea"
    )
    names = {el.attributes.get("name") for el in inputs}
    expected = {
        "older_than_days",
        "duration_below_minutes",
        "no_transcript",
        "title_contains",
        "title_regex",
        "host_email",
        "participants_below",
        "has_tag",
    }
    assert expected <= names

    stepper = doc.css_first("nav.wizard-stepper li.step.active[data-step='filter']")
    assert stepper is not None

    preview = doc.css_first("#preview-count")
    assert preview is not None
    assert preview.attributes.get("hx-post") == "/cleanup/preview-count"


def test_get_cleanup_prepopulates_from_session(
    configured_client: TestClient, configured_app
) -> None:
    """After saving filters via POST /cleanup, GET /cleanup reflects them."""
    csrf = configured_client.cookies["ffc_csrf"]
    configured_client.post(
        "/cleanup",
        data={
            "_csrf": csrf,
            "older_than_days": "30",
            "older_than_days_enabled": "on",
            "title_contains": "standup, retro",
        },
        follow_redirects=False,
    )
    r = configured_client.get("/cleanup")
    assert r.status_code == 200
    doc = HTMLParser(r.text)
    older = doc.css_first("input[name='older_than_days']")
    assert older is not None
    assert older.attributes.get("value") == "30"
    titles = doc.css_first("input[name='title_contains']")
    assert titles is not None
    assert "standup" in (titles.attributes.get("value") or "")
    assert "retro" in (titles.attributes.get("value") or "")


# ---------------------------------------------------------------------------
# POST /cleanup/preview-count
# ---------------------------------------------------------------------------


def test_preview_count_empty_returns_message(configured_client: TestClient) -> None:
    csrf = configured_client.cookies["ffc_csrf"]
    r = configured_client.post(
        "/cleanup/preview-count",
        data={"_csrf": csrf},
    )
    assert r.status_code == 200
    assert "Add at least one filter" in r.text


def test_preview_count_with_active_filter_returns_count(
    configured_client: TestClient, configured_app
) -> None:
    _seed_old_meetings(configured_app, count=3, days_old=90)

    csrf = configured_client.cookies["ffc_csrf"]
    r = configured_client.post(
        "/cleanup/preview-count",
        data={
            "_csrf": csrf,
            "older_than_days": "30",
            "older_than_days_enabled": "on",
        },
    )
    assert r.status_code == 200
    assert "<strong>3</strong>" in r.text
    assert "meetings would match" in r.text


def test_preview_count_zero_matches_renders_zero(configured_client: TestClient) -> None:
    """Empty repository + active filter => count is 0 (not an error)."""
    csrf = configured_client.cookies["ffc_csrf"]
    r = configured_client.post(
        "/cleanup/preview-count",
        data={
            "_csrf": csrf,
            "older_than_days": "30",
            "older_than_days_enabled": "on",
        },
    )
    assert r.status_code == 200
    assert "<strong>0</strong>" in r.text


def test_preview_count_handles_repo_error(configured_client: TestClient, configured_app) -> None:
    """When the repo blows up, the fragment renders an inline error.

    Phase 6: scan goes through ``scan_repo`` (the cache adapter), so patch
    that — patching ``client.list_meetings`` no longer affects the read path.
    """

    repo = configured_app.state.deps.scan_repo

    async def _broken_list(_filter):
        raise RuntimeError("boom")
        yield  # pragma: no cover - generator marker

    repo.list_meetings = _broken_list  # type: ignore[method-assign]

    csrf = configured_client.cookies["ffc_csrf"]
    r = configured_client.post(
        "/cleanup/preview-count",
        data={
            "_csrf": csrf,
            "older_than_days": "30",
            "older_than_days_enabled": "on",
        },
    )
    assert r.status_code == 200
    # The fragment surfaces the underlying error message; previously prefixed
    # with "Could not preview count:", now uses the unified scan-error helper
    # which renders the descriptive message directly. RuntimeError isn't a
    # FirefliesError or RateLimitedError, so it falls through to the generic
    # "Could not reach Fireflies" branch with the raw message.
    assert "Could not reach Fireflies" in r.text or "boom" in r.text


def test_preview_count_with_invalid_regex_returns_inline_message(
    configured_client: TestClient,
) -> None:
    """Bad regex should fail fast in the fragment, not raise inside ScanService."""
    csrf = configured_client.cookies["ffc_csrf"]
    r = configured_client.post(
        "/cleanup/preview-count",
        data={
            "_csrf": csrf,
            "title_regex": "*",  # invalid: nothing to repeat
        },
    )
    assert r.status_code == 200
    assert "Invalid regex" in r.text


# ---------------------------------------------------------------------------
# POST /cleanup
# ---------------------------------------------------------------------------


def test_post_cleanup_with_no_filters_renders_error(configured_client: TestClient) -> None:
    csrf = configured_client.cookies["ffc_csrf"]
    r = configured_client.post(
        "/cleanup",
        data={"_csrf": csrf},
        follow_redirects=False,
    )
    assert r.status_code == 422
    assert "at least one filter" in r.text.lower()
    # Error is rendered inside the form template (still has the form)
    doc = HTMLParser(r.text)
    assert doc.css_first("form#cleanup-step1-form") is not None


def test_post_cleanup_with_valid_filter_redirects_and_saves_session(
    configured_client: TestClient, configured_app
) -> None:
    csrf = configured_client.cookies["ffc_csrf"]
    r = configured_client.post(
        "/cleanup",
        data={
            "_csrf": csrf,
            "older_than_days": "30",
            "older_than_days_enabled": "on",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/cleanup/review"

    sid = configured_client.cookies.get("ffc_session", "")
    state = configured_app.state.session_store.get(sid).get("wizard", {})
    assert state["step"] == "review"
    assert state["filters"]["older_than_days"] == 30
    assert state["selected_ids"] == []
    assert state["operation_id"] is None


def test_post_cleanup_with_invalid_regex_renders_error(configured_client: TestClient) -> None:
    """Invalid title_regex should re-render the form with an inline error."""
    csrf = configured_client.cookies["ffc_csrf"]
    r = configured_client.post(
        "/cleanup",
        data={
            "_csrf": csrf,
            "title_regex": "*",  # invalid: nothing to repeat
        },
        follow_redirects=False,
    )
    assert r.status_code == 422
    assert "Invalid regex" in r.text
    doc = HTMLParser(r.text)
    assert doc.css_first("form#cleanup-step1-form") is not None


# ---------------------------------------------------------------------------
# Regression: when [sync] enabled = true, wizard reads must hit the cache only
# ---------------------------------------------------------------------------


def test_wizard_step1_filter_does_not_call_live_api_when_sync_on(
    configured_app_sync_on,
) -> None:
    """The wizard's filter step reads from cache — zero ``list_meetings`` on
    the live FirefliesClient when ``[sync] enabled = true``."""
    # Pre-populate cache with one old meeting so the filter has something to match.
    manifest = configured_app_sync_on.state.deps.manifest
    manifest.upsert_known(
        Meeting(
            meeting_id="m1",
            title="Old standup",
            meeting_date=datetime(2025, 1, 1, tzinfo=UTC),
            duration_minutes=10.0,
            host_email="a@x",
            participant_count=2,
            tags=(),
            has_transcript=True,
        ),
        at=datetime(2026, 5, 2, tzinfo=UTC),
    )

    client = TestClient(configured_app_sync_on)
    client.get("/?token=T", follow_redirects=False)
    csrf = client.cookies.get("ffc_csrf", "") or ""

    # Step 1: submit the filter form (POST /cleanup) — older_than_days = 365.
    r = client.post(
        "/cleanup",
        data={
            "_csrf": csrf,
            "older_than_days": "365",
            "older_than_days_enabled": "on",
        },
        follow_redirects=False,
    )
    assert r.status_code in (200, 302, 303, 307), r.text

    # Step 2: load the review page — this would ordinarily call list_meetings.
    r = client.get("/cleanup/review")
    assert r.status_code == 200, r.text

    # The live client.list_meetings was NEVER called.
    assert configured_app_sync_on.state.tracking_repo.list_call_count == 0
