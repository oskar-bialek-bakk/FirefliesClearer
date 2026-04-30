"""End-to-end full-run test for the v2 web UI.

Drives the cleanup wizard from filter -> review -> archive (which fails for
the seeded meeting) -> dashboard, then exercises the retry-from-Dashboard
path introduced in Task 5.7. Verifies the full operation-registry round-trip:
the failed meeting's manifest record returns to ARCHIVED after a successful
retry.

We use HTTP requests for the wizard half because the plan calls for an
end-to-end "Hits /cleanup/... end-to-end" flow. The retry half is also
HTTP — POST /retry/{id} -> wait for op -> GET / and verify the row is gone.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from selectolax.parser import HTMLParser

from firefliesclearer.core.models import Meeting, MeetingState
from tests.fakes.fake_pipeline import FakePipeline
from tests.fakes.in_memory_repository import InMemoryMeetingRepository

NOW = datetime(2026, 4, 29, 12, 0, tzinfo=UTC)


@pytest.fixture
def configured_client(configured_app):
    """Persistent-portal TestClient.

    The retry path schedules an asyncio task via OperationRegistry.start
    inside a request handler; without the persistent BlockingPortal the
    task would die with the per-request loop, defeating ``op.task`` waits.
    """
    with TestClient(configured_app) as c:
        c.get("/?token=T")
        yield c


def _csrf(c: TestClient) -> str:
    return c.cookies.get("ffc_csrf", "") or ""


async def _await_op(app, op_id: str) -> None:
    op = app.state.operation_registry.get(op_id)
    await op.task


def _wait_for_op(client: TestClient, app, op_id: str) -> None:
    assert client.portal is not None
    client.portal.call(_await_op, app, op_id)


def _seed_repo_one_meeting(app, *, meeting_id: str, title: str) -> Meeting:
    """Replace the configured app's client with an InMemoryRepo holding one meeting.

    The meeting is pre-dated 90 days so the wizard's ``older_than_days=30``
    filter matches it (and only it).
    """
    meeting = Meeting(
        meeting_id=meeting_id,
        title=title,
        meeting_date=NOW - timedelta(days=90),
        duration_minutes=20.0,
        host_email="h@x",
        participant_count=3,
    )
    repo = InMemoryMeetingRepository(meetings=[meeting], api_key="ff_test")
    repo.set_user_email_for_key("ff_test", "oskar@example.com")
    app.state.deps.client = repo
    return meeting


# ---------------------------------------------------------------------------
# E2E: cleanup -> archive fails -> dashboard -> retry -> success
# ---------------------------------------------------------------------------


def test_full_run_archive_fails_then_retry_from_dashboard_succeeds(
    configured_client: TestClient, configured_app
) -> None:
    """Drive the wizard end-to-end with a forced-failure pipeline, then retry.

    1. Seed a single meeting matching the older_than=30 filter.
    2. Configure FakePipeline to FAIL the first archive call but ARCHIVE the
       second (the retry).
    3. Drive the wizard: GET /cleanup -> POST /cleanup -> GET /cleanup/review
       -> POST /cleanup/review/select-all -> POST /cleanup/review -> GET
       /cleanup/archive -> POST /cleanup/archive/start.
    4. Wait for the archive op to finish; the meeting is now FAILED_DOWNLOAD.
    5. GET / dashboard; the meeting is in needs-attention with a Retry button.
    6. POST /retry/m1; receive an inline progress fragment with op id.
    7. Wait for the retry op to finish.
    8. Assert manifest's m1 record is now ARCHIVED.
    """
    meeting = _seed_repo_one_meeting(configured_app, meeting_id="m1", title="Stuck Standup")

    # The fake pipeline updates the manifest itself so the wizard's archive
    # step exercises the real state-machine transitions. (The default
    # FakePipeline doesn't touch the manifest — that's normally the real
    # Pipeline's job. Here we want to assert post-retry manifest state.)
    manifest = configured_app.state.deps.manifest
    # First call -> fail; second call (the retry) -> succeed.
    fake = FakePipeline()

    async def archive_one(m):
        fake.archive_calls.append(m.meeting_id)
        # First archive fails; second succeeds.
        if len(fake.archive_calls) == 1:
            # Walk the meeting through PENDING -> FAILED_DOWNLOAD so the
            # dashboard sees a real failed record.
            if manifest.get(m.meeting_id) is None:
                manifest.register(m, at=NOW)
            rec = manifest.get(m.meeting_id)
            if rec is not None and rec.state is MeetingState.PENDING:
                manifest.transition(
                    m.meeting_id,
                    to=MeetingState.FAILED_DOWNLOAD,
                    at=NOW,
                    last_error="Forced failure for E2E",
                )
            return MeetingState.FAILED_DOWNLOAD
        # Retry path.
        rec = manifest.get(m.meeting_id)
        if rec is not None and rec.state in {
            MeetingState.FAILED_FETCH,
            MeetingState.FAILED_DOWNLOAD,
            MeetingState.FAILED_RENDER,
            MeetingState.FAILED_VERIFY,
        }:
            manifest.transition(m.meeting_id, to=MeetingState.PENDING, at=NOW)
            manifest.transition(m.meeting_id, to=MeetingState.ARCHIVED, at=NOW)
        return MeetingState.ARCHIVED

    fake.archive_one = archive_one  # type: ignore[method-assign]
    configured_app.state.deps.pipeline = fake

    # ---- Wizard Step 1: filter ----
    r = configured_client.get("/cleanup")
    assert r.status_code == 200
    r = configured_client.post(
        "/cleanup",
        data={
            "_csrf": _csrf(configured_client),
            "older_than_days_enabled": "on",
            "older_than_days": "30",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text
    assert r.headers["location"] == "/cleanup/review"

    # ---- Wizard Step 2: review (select all -> continue) ----
    r = configured_client.get("/cleanup/review")
    assert r.status_code == 200
    r = configured_client.post(
        "/cleanup/review/select-all",
        data={"_csrf": _csrf(configured_client), "all": "true", "page": "1"},
    )
    assert r.status_code == 200, r.text
    r = configured_client.post(
        "/cleanup/review",
        data={"_csrf": _csrf(configured_client), "page": "1"},
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text
    assert r.headers["location"] == "/cleanup/archive"

    # ---- Wizard Step 3: archive (preflight -> start -> wait) ----
    r = configured_client.get("/cleanup/archive")
    assert r.status_code == 200
    r = configured_client.post(
        "/cleanup/archive/start",
        data={"_csrf": _csrf(configured_client)},
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text

    # The archive op is now running; wait for it.
    sid = configured_client.cookies.get("ffc_session", "")
    wizard_state = configured_app.state.session_store.get(sid).get("wizard", {})
    archive_op_id = wizard_state.get("operation_id")
    assert archive_op_id is not None
    _wait_for_op(configured_client, configured_app, archive_op_id)

    # Meeting m1 is now FAILED_DOWNLOAD in the manifest.
    rec = manifest.get(meeting.meeting_id)
    assert rec is not None
    assert rec.state is MeetingState.FAILED_DOWNLOAD

    # ---- Dashboard: confirm Needs Attention has m1 with a Retry button ----
    r = configured_client.get("/")
    assert r.status_code == 200
    doc = HTMLParser(r.text)
    rows = doc.css(".needs-attention-row")
    assert len(rows) == 1
    row = rows[0]
    assert row.attributes.get("data-meeting-id") == "m1"
    retry_form = row.css_first("form.needs-attention-row__retry")
    assert retry_form is not None
    assert retry_form.attributes.get("hx-post") == "/retry/m1"

    # ---- Retry: POST /retry/m1 ----
    r = configured_client.post(
        "/retry/m1",
        data={"_csrf": _csrf(configured_client)},
    )
    assert r.status_code == 200, r.text
    doc = HTMLParser(r.text)
    progress_root = doc.css_first("[data-operation-id]")
    assert progress_root is not None
    retry_op_id = progress_root.attributes.get("data-operation-id")
    assert retry_op_id is not None
    _wait_for_op(configured_client, configured_app, retry_op_id)

    # ---- Verify: m1 is now ARCHIVED in the manifest ----
    rec = manifest.get("m1")
    assert rec is not None
    assert rec.state is MeetingState.ARCHIVED

    # ---- Dashboard re-renders: m1 should no longer be in needs-attention ----
    r = configured_client.get("/")
    assert r.status_code == 200
    doc = HTMLParser(r.text)
    rows = doc.css(".needs-attention-row")
    assert len(rows) == 0
    empty = doc.css_first(".needs-attention-empty")
    assert empty is not None
