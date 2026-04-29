"""Tests for cleanup wizard Step 1 — filter form, live preview-count, submit."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from selectolax.parser import HTMLParser

from firefliesclearer.core.models import Meeting

NOW = datetime(2026, 4, 29, 12, 0, tzinfo=UTC)


@pytest.fixture
def configured_client(configured_app) -> TestClient:
    """Client where setup is already complete (configured_app from conftest)."""
    c = TestClient(configured_app)
    c.get("/?token=T")
    return c


def _seed_old_meetings(repo, count: int, days_old: int = 90) -> None:
    """Inject *count* meetings older than *days_old* into the in-memory repo."""
    when = NOW - timedelta(days=days_old)
    for i in range(count):
        m = Meeting(
            meeting_id=f"m{i}",
            title=f"Old Meeting {i}",
            meeting_date=when,
            duration_minutes=30.0,
            host_email="host@example.com",
            participant_count=5,
        )
        repo._meetings[m.meeting_id] = m


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
    repo = configured_app.state.deps.client
    _seed_old_meetings(repo, count=3, days_old=90)

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
    """When the repo blows up, the fragment renders an inline error."""

    repo = configured_app.state.deps.client

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
    assert "Could not preview count" in r.text


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
