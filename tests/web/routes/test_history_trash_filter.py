"""History page — Archived / Trash / All filter chip + no-archive badge."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from selectolax.parser import HTMLParser

from firefliesclearer.core.models import Meeting, MeetingState

NOW = datetime(2026, 5, 6, 12, 0, tzinfo=UTC)


def _seed_archived(app, mid: str, title: str) -> None:
    m = Meeting(
        meeting_id=mid,
        title=title,
        meeting_date=NOW - timedelta(days=10),
        duration_minutes=60.0,
        host_email="oskar@example.com",
        participant_count=4,
    )
    app.state.deps.manifest.upsert_known(m, at=NOW)
    app.state.deps.manifest.transition(mid, to=MeetingState.PENDING, at=NOW)
    app.state.deps.manifest.transition(
        mid,
        to=MeetingState.ARCHIVED,
        at=NOW,
        archive_path="/tmp/archive",
        verified_at=NOW,
        sha256s={"audio": "x", "summary": "y", "transcript": "z"},
    )
    app.state.deps.manifest.transition(
        mid,
        to=MeetingState.DELETED,
        at=NOW,
        details={"reason": "manual_external_delete_via_wizard"},
    )


def _seed_trash(app, mid: str, title: str) -> None:
    m = Meeting(
        meeting_id=mid,
        title=title,
        meeting_date=NOW - timedelta(days=10),
        duration_minutes=15.0,
        host_email="oskar@example.com",
        participant_count=4,
    )
    app.state.deps.manifest.upsert_known(m, at=NOW)
    app.state.deps.manifest.transition(
        mid,
        to=MeetingState.DELETED,
        at=NOW,
        details={"reason": "manual_trash_via_wizard"},
    )


def test_history_renders_filter_chips(configured_app) -> None:
    _seed_archived(configured_app, "m_arch", "Archived item")
    _seed_trash(configured_app, "m_trash", "Trash item")
    with TestClient(configured_app) as c:
        c.get("/?token=T")
        r = c.get("/history?range=all-time")
    assert r.status_code == 200
    doc = HTMLParser(r.text)
    chips = doc.css(".history-filter-chip")
    chip_labels = {c.text(deep=True).strip() for c in chips}
    assert chip_labels >= {"All", "Archived", "Trash"}


def test_history_filter_archived_only_excludes_trash(configured_app) -> None:
    _seed_archived(configured_app, "m_arch", "Archived item")
    _seed_trash(configured_app, "m_trash", "Trash item")
    with TestClient(configured_app) as c:
        c.get("/?token=T")
        r = c.get("/history?range=all-time&kind=archived")
    assert r.status_code == 200
    text = r.text
    assert "Archived item" in text
    assert "Trash item" not in text


def test_history_filter_trash_only_excludes_archived(configured_app) -> None:
    _seed_archived(configured_app, "m_arch", "Archived item")
    _seed_trash(configured_app, "m_trash", "Trash item")
    with TestClient(configured_app) as c:
        c.get("/?token=T")
        r = c.get("/history?range=all-time&kind=trash")
    assert r.status_code == 200
    text = r.text
    assert "Trash item" in text
    assert "Archived item" not in text


def test_history_trash_row_renders_no_archive_badge(configured_app) -> None:
    _seed_trash(configured_app, "m_trash", "Trash item")
    with TestClient(configured_app) as c:
        c.get("/?token=T")
        r = c.get("/history?range=all-time")
    doc = HTMLParser(r.text)
    row = doc.css_first("tr[data-meeting-id='m_trash']")
    assert row is not None
    assert row.css_first(".badge-no-archive") is not None


def test_history_archive_row_does_not_render_badge(configured_app) -> None:
    _seed_archived(configured_app, "m_arch", "Archived item")
    with TestClient(configured_app) as c:
        c.get("/?token=T")
        r = c.get("/history?range=all-time")
    doc = HTMLParser(r.text)
    row = doc.css_first("tr[data-meeting-id='m_arch']")
    assert row is not None
    assert row.css_first(".badge-no-archive") is None
