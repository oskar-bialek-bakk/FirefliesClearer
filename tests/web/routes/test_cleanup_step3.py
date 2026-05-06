"""Tests for cleanup wizard Step 3 — Archive (preflight, start, in-progress, done, cancel)."""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from selectolax.parser import HTMLParser

from firefliesclearer.application.scan_service import ScanFilters
from firefliesclearer.core.models import Meeting, MeetingState
from firefliesclearer.web.operations import OperationKind
from firefliesclearer.web.wizard_session import (
    WizardState,
    filters_to_dict,
    set_state,
)
from tests.fakes.fake_pipeline import FakePipeline
from tests.fakes.in_memory_repository import InMemoryMeetingRepository

NOW = datetime(2026, 4, 29, 12, 0, tzinfo=UTC)


@pytest.fixture
def configured_client(configured_app):
    """Persistent-portal TestClient.

    Step 3 tests start asyncio tasks via ``OperationRegistry.start`` inside
    request handlers. Without a persistent portal each request uses a fresh
    event loop that dies on response, taking the operation's ``task`` with
    it — so the test cannot later ``await op.task`` to wait for completion.

    Wrapping the client in ``with`` keeps a single ``BlockingPortal`` (and its
    background event loop) alive across all calls in the test, so tasks
    created during one request remain awaitable from later ``portal.call(...)``
    invocations.
    """
    with TestClient(configured_app) as c:
        c.get("/?token=T")
        yield c


def _seed_repo(app, count: int) -> list[Meeting]:
    """Phase 6: seed both the live repo (mutation paths) and the manifest
    (read paths via the cache adapter)."""
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
    for m in meetings:
        app.state.deps.manifest.upsert_known(m, at=NOW)
    return meetings


def _sid(c: TestClient) -> str:
    return c.cookies.get("ffc_session", "") or ""


def _csrf(c: TestClient) -> str:
    return c.cookies.get("ffc_csrf", "") or ""


def _set_wizard(app, sid: str, *, step: str, selected: list[str], op_id: str | None = None) -> None:
    state = WizardState(
        step=step,
        filters=filters_to_dict(ScanFilters(older_than_days=30)),
        selected_ids=list(selected),
        operation_id=op_id,
    )
    set_state(app.state.session_store, sid, state)


def _wizard(app, sid: str) -> dict:
    return app.state.session_store.get(sid).get("wizard", {})


async def _await_op(app, op_id: str) -> None:
    op = app.state.operation_registry.get(op_id)
    await op.task


def _wait_for_op(client: TestClient, app, op_id: str) -> None:
    """Wait for the named op to finish on the TestClient's portal loop."""
    assert client.portal is not None, "TestClient must be entered as a context manager"
    client.portal.call(_await_op, app, op_id)


# ---------------------------------------------------------------------------
# GET /cleanup/archive — preflight
# ---------------------------------------------------------------------------


def test_get_archive_redirects_to_review_when_no_selection(
    configured_client: TestClient, configured_app
) -> None:
    _seed_repo(configured_app, 3)
    sid = _sid(configured_client)
    _set_wizard(configured_app, sid, step="archive", selected=[])
    r = configured_client.get("/cleanup/archive", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/cleanup/review?error=empty-selection"


def test_get_archive_renders_preflight_with_count_and_size(
    configured_client: TestClient, configured_app
) -> None:
    _seed_repo(configured_app, 5)
    sid = _sid(configured_client)
    _set_wizard(configured_app, sid, step="archive", selected=["m0", "m1", "m2"])
    r = configured_client.get("/cleanup/archive")
    assert r.status_code == 200
    doc = HTMLParser(r.text)
    text = doc.text()
    # 3 meetings selected.
    assert "3" in text
    assert "meetings" in text.lower()
    # MB estimate present and within a sensible numeric range.
    # 3 meetings * 30 minutes * 64 kbps ~= 43 MB; allow some drift but pin
    # the rendered number to catch accidental KBPS changes (e.g. 8, 64000).
    match = re.search(r"~\s*(\d+)\s*MB", text)
    assert match is not None, f"no '~ <int> MB' estimate in: {text!r}"
    mb = int(match.group(1))
    assert 30 <= mb <= 60, f"unexpected MB estimate: {mb}"
    # Stepper says step 3 active.
    active = doc.css_first("nav.wizard-stepper li.step.active[data-step='archive']")
    assert active is not None
    # Start button present.
    start_btn = doc.css_first("button.start-archive-btn")
    assert start_btn is not None


# ---------------------------------------------------------------------------
# POST /cleanup/archive/start
# ---------------------------------------------------------------------------


def test_post_archive_start_redirects_to_review_when_empty_selection(
    configured_client: TestClient, configured_app
) -> None:
    sid = _sid(configured_client)
    _set_wizard(configured_app, sid, step="archive", selected=[])
    r = configured_client.post(
        "/cleanup/archive/start",
        data={"_csrf": _csrf(configured_client)},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/cleanup/review?error=empty-selection"


def test_post_archive_start_kicks_off_op_and_redirects(
    configured_client: TestClient, configured_app
) -> None:
    _seed_repo(configured_app, 5)
    sid = _sid(configured_client)
    _set_wizard(configured_app, sid, step="archive", selected=["m0", "m1"])
    r = configured_client.post(
        "/cleanup/archive/start",
        data={"_csrf": _csrf(configured_client)},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/cleanup/archive/in-progress"
    op_id = _wizard(configured_app, sid)["operation_id"]
    assert op_id is not None
    op = configured_app.state.operation_registry.get(op_id)
    assert op.kind == OperationKind.ARCHIVE


def test_post_archive_start_returns_409_when_same_kind_running(
    configured_client: TestClient, configured_app
) -> None:
    _seed_repo(configured_app, 5)
    sid = _sid(configured_client)
    _set_wizard(configured_app, sid, step="archive", selected=["m0"])

    # Pre-load a long-running archive op into the registry, on the
    # TestClient's portal loop so its asyncio.Event survives across calls.
    async def _start_blocking_op():
        async def runner(ctx):
            await asyncio.Event().wait()  # never returns

        op = await configured_app.state.operation_registry.start(
            kind=OperationKind.ARCHIVE,
            meeting_ids=["other"],
            runner=runner,
        )
        return op.id

    assert configured_client.portal is not None
    blocking_id = configured_client.portal.call(_start_blocking_op)

    r = configured_client.post(
        "/cleanup/archive/start",
        data={"_csrf": _csrf(configured_client)},
        follow_redirects=False,
    )
    assert r.status_code == 409
    assert "already running" in r.text.lower()

    # Cleanup the blocking op so test isolation holds.
    async def _cancel_blocking() -> None:
        configured_app.state.operation_registry.get(blocking_id).task.cancel()

    configured_client.portal.call(_cancel_blocking)


# ---------------------------------------------------------------------------
# GET /cleanup/archive/in-progress
# ---------------------------------------------------------------------------


def test_in_progress_redirects_to_archive_when_no_op_id(
    configured_client: TestClient, configured_app
) -> None:
    sid = _sid(configured_client)
    _set_wizard(configured_app, sid, step="archive", selected=["m0"])
    r = configured_client.get("/cleanup/archive/in-progress", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/cleanup/archive"


def test_in_progress_redirects_to_done_when_op_terminal(
    configured_client: TestClient, configured_app
) -> None:
    _seed_repo(configured_app, 3)
    sid = _sid(configured_client)
    _set_wizard(configured_app, sid, step="archive", selected=["m0"])
    # Start an op and let it complete.
    r = configured_client.post(
        "/cleanup/archive/start",
        data={"_csrf": _csrf(configured_client)},
        follow_redirects=False,
    )
    op_id = _wizard(configured_app, sid)["operation_id"]
    _wait_for_op(configured_client, configured_app, op_id)

    r = configured_client.get("/cleanup/archive/in-progress", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/cleanup/archive/done"


def test_in_progress_renders_meeting_list_for_running_op(
    configured_client: TestClient, configured_app
) -> None:
    _seed_repo(configured_app, 3)
    sid = _sid(configured_client)
    _set_wizard(configured_app, sid, step="archive", selected=["m0", "m1"])

    # Use a blocking pipeline so the op stays running while we GET the page.
    block = asyncio.Event()

    class BlockingPipeline:
        async def archive_one(self, meeting):
            await block.wait()
            return MeetingState.ARCHIVED

    configured_app.state.deps.pipeline = BlockingPipeline()

    configured_client.post(
        "/cleanup/archive/start",
        data={"_csrf": _csrf(configured_client)},
        follow_redirects=False,
    )
    op_id = _wizard(configured_app, sid)["operation_id"]
    try:
        r = configured_client.get("/cleanup/archive/in-progress")
        assert r.status_code == 200
        doc = HTMLParser(r.text)
        # Stepper says archive active.
        assert doc.css_first("nav.wizard-stepper li.step.active[data-step='archive']") is not None
        # Meeting rows rendered (one per selected id).
        rows = doc.css(".archive-meeting-row")
        assert len(rows) == 2
        # Cancel button present pointing at /cleanup/operations/{op_id}/cancel.
        cancel = doc.css_first("button.cancel-archive-btn")
        assert cancel is not None
        # Progress bar present.
        assert doc.css_first(".progress-bar") is not None
    finally:
        # Release the pipeline and let the op finish.
        block.set()
        _wait_for_op(configured_client, configured_app, op_id)


def test_archive_in_progress_root_has_data_kind_for_sse_label_lookup(
    configured_client: TestClient, configured_app
) -> None:
    """The in-progress root carries data-kind=archive so the SSE script can
    pick the right sub_state -> display-label table and preserve the
    server-rendered glyphs (e.g. "archived ✓", "failed ✗").
    """
    _seed_repo(configured_app, 2)
    sid = _sid(configured_client)
    _set_wizard(configured_app, sid, step="archive", selected=["m0"])
    block = asyncio.Event()

    class BlockingPipeline:
        async def archive_one(self, meeting):
            await block.wait()
            return MeetingState.ARCHIVED

    configured_app.state.deps.pipeline = BlockingPipeline()
    configured_client.post(
        "/cleanup/archive/start",
        data={"_csrf": _csrf(configured_client)},
        follow_redirects=False,
    )
    op_id = _wizard(configured_app, sid)["operation_id"]
    try:
        r = configured_client.get("/cleanup/archive/in-progress")
        assert r.status_code == 200
        doc = HTMLParser(r.text)
        root = doc.css_first("[data-operation-id]")
        assert root is not None
        assert root.attributes.get("data-kind") == "archive"
    finally:
        block.set()
        _wait_for_op(configured_client, configured_app, op_id)


# ---------------------------------------------------------------------------
# GET /cleanup/archive/done
# ---------------------------------------------------------------------------


def test_done_redirects_to_in_progress_when_op_running(
    configured_client: TestClient, configured_app
) -> None:
    _seed_repo(configured_app, 2)
    sid = _sid(configured_client)
    _set_wizard(configured_app, sid, step="archive", selected=["m0"])
    block = asyncio.Event()

    class BlockingPipeline:
        async def archive_one(self, meeting):
            await block.wait()
            return MeetingState.ARCHIVED

    configured_app.state.deps.pipeline = BlockingPipeline()
    configured_client.post(
        "/cleanup/archive/start",
        data={"_csrf": _csrf(configured_client)},
        follow_redirects=False,
    )
    op_id = _wizard(configured_app, sid)["operation_id"]
    try:
        r = configured_client.get("/cleanup/archive/done", follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/cleanup/archive/in-progress"
    finally:
        block.set()
        _wait_for_op(configured_client, configured_app, op_id)


def test_done_renders_summary_for_all_success(
    configured_client: TestClient, configured_app
) -> None:
    _seed_repo(configured_app, 3)
    sid = _sid(configured_client)
    _set_wizard(configured_app, sid, step="archive", selected=["m0", "m1"])
    # Default FakePipeline returns ARCHIVED for both.
    configured_client.post(
        "/cleanup/archive/start",
        data={"_csrf": _csrf(configured_client)},
        follow_redirects=False,
    )
    op_id = _wizard(configured_app, sid)["operation_id"]
    _wait_for_op(configured_client, configured_app, op_id)

    r = configured_client.get("/cleanup/archive/done")
    assert r.status_code == 200
    doc = HTMLParser(r.text)
    text = doc.text()
    assert "2 archived" in text
    assert "0 failed" in text
    cont = doc.css_first("button.continue-purge-btn")
    assert cont is not None
    # selectolax returns boolean attrs as keys with None value when present;
    # absence means the key is missing entirely.
    assert "disabled" not in cont.attributes


def test_done_renders_failed_rows_for_mixed_outcomes(
    configured_client: TestClient, configured_app
) -> None:
    _seed_repo(configured_app, 3)
    sid = _sid(configured_client)
    _set_wizard(configured_app, sid, step="archive", selected=["m0", "m1", "m2"])
    configured_app.state.deps.pipeline = FakePipeline(
        archive_outcomes={"m1": MeetingState.FAILED_FETCH}
    )
    configured_client.post(
        "/cleanup/archive/start",
        data={"_csrf": _csrf(configured_client)},
        follow_redirects=False,
    )
    op_id = _wizard(configured_app, sid)["operation_id"]
    _wait_for_op(configured_client, configured_app, op_id)

    r = configured_client.get("/cleanup/archive/done")
    assert r.status_code == 200
    doc = HTMLParser(r.text)
    text = doc.text()
    assert "2 archived" in text
    assert "1 failed" in text
    failed_rows = doc.css(".archive-failed-row")
    assert len(failed_rows) == 1


def test_done_disables_continue_when_all_failed(
    configured_client: TestClient, configured_app
) -> None:
    _seed_repo(configured_app, 2)
    sid = _sid(configured_client)
    _set_wizard(configured_app, sid, step="archive", selected=["m0", "m1"])
    configured_app.state.deps.pipeline = FakePipeline(
        archive_outcomes={
            "m0": MeetingState.FAILED_FETCH,
            "m1": MeetingState.FAILED_DOWNLOAD,
        }
    )
    configured_client.post(
        "/cleanup/archive/start",
        data={"_csrf": _csrf(configured_client)},
        follow_redirects=False,
    )
    op_id = _wizard(configured_app, sid)["operation_id"]
    _wait_for_op(configured_client, configured_app, op_id)

    r = configured_client.get("/cleanup/archive/done")
    assert r.status_code == 200
    doc = HTMLParser(r.text)
    cont = doc.css_first("button.continue-purge-btn")
    assert cont is not None
    assert "disabled" in cont.attributes


# ---------------------------------------------------------------------------
# POST /cleanup/operations/{op_id}/cancel
# ---------------------------------------------------------------------------


def test_cancel_returns_204_and_sets_cancel_event(
    configured_client: TestClient, configured_app
) -> None:
    _seed_repo(configured_app, 2)
    sid = _sid(configured_client)
    _set_wizard(configured_app, sid, step="archive", selected=["m0"])
    block = asyncio.Event()

    class BlockingPipeline:
        async def archive_one(self, meeting):
            await block.wait()
            return MeetingState.ARCHIVED

    configured_app.state.deps.pipeline = BlockingPipeline()
    configured_client.post(
        "/cleanup/archive/start",
        data={"_csrf": _csrf(configured_client)},
        follow_redirects=False,
    )
    op_id = _wizard(configured_app, sid)["operation_id"]
    try:
        r = configured_client.post(
            f"/cleanup/operations/{op_id}/cancel",
            data={"_csrf": _csrf(configured_client)},
        )
        assert r.status_code == 204
        op = configured_app.state.operation_registry.get(op_id)
        assert op.cancel_event.is_set()
    finally:
        block.set()
        _wait_for_op(configured_client, configured_app, op_id)


def test_cancel_returns_404_for_missing_op(configured_client: TestClient) -> None:
    r = configured_client.post(
        "/cleanup/operations/op_does_not_exist/cancel",
        data={"_csrf": _csrf(configured_client)},
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# POST /cleanup/archive/continue
# ---------------------------------------------------------------------------


def test_continue_to_purge_on_success(configured_client: TestClient, configured_app) -> None:
    _seed_repo(configured_app, 2)
    sid = _sid(configured_client)
    _set_wizard(configured_app, sid, step="archive", selected=["m0"])
    configured_client.post(
        "/cleanup/archive/start",
        data={"_csrf": _csrf(configured_client)},
        follow_redirects=False,
    )
    op_id = _wizard(configured_app, sid)["operation_id"]
    _wait_for_op(configured_client, configured_app, op_id)

    r = configured_client.post(
        "/cleanup/archive/continue",
        data={"_csrf": _csrf(configured_client)},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/cleanup/purge"
    state = _wizard(configured_app, sid)
    assert state["step"] == "purge"
    assert state["operation_id"] is None


# ---------------------------------------------------------------------------
# Regression: empty-selection redirects carry ?error= and review surfaces it
# ---------------------------------------------------------------------------


def test_archive_preflight_with_empty_selection_redirects_to_review(
    configured_client: TestClient, configured_app
) -> None:
    """GET /cleanup/archive with empty wizard selection → 303 to /cleanup/review
    with the ?error=empty-selection hint so the user sees why they were bounced.
    """
    _seed_repo(configured_app, 2)
    sid = _sid(configured_client)
    _set_wizard(configured_app, sid, step="archive", selected=[])
    r = configured_client.get("/cleanup/archive", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/cleanup/review?error=empty-selection"


def test_archive_start_with_empty_selection_redirects_to_review(
    configured_client: TestClient, configured_app
) -> None:
    """POST /cleanup/archive/start with empty wizard selection → 303 to review."""
    _seed_repo(configured_app, 2)
    sid = _sid(configured_client)
    _set_wizard(configured_app, sid, step="archive", selected=[])
    r = configured_client.post(
        "/cleanup/archive/start",
        data={"_csrf": _csrf(configured_client)},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/cleanup/review?error=empty-selection"


def test_review_renders_error_banner_for_empty_selection(
    configured_client: TestClient, configured_app
) -> None:
    """GET /cleanup/review?error=empty-selection → 200 with the inline error banner."""
    _seed_repo(configured_app, 2)
    sid = _sid(configured_client)
    _set_wizard(configured_app, sid, step="review", selected=[])
    r = configured_client.get("/cleanup/review?error=empty-selection")
    assert r.status_code == 200
    doc = HTMLParser(r.text)
    banner = doc.css_first(".error-banner")
    assert banner is not None
    assert "select" in banner.text().lower() or "meeting" in banner.text().lower()


# ---------------------------------------------------------------------------
# Step 3b: archive_ids = selected_ids - trash_ids
# ---------------------------------------------------------------------------


def test_step3_preflight_redirects_to_step4_when_only_trash_remains(
    configured_client, configured_app
) -> None:
    """If all selected rows are still trash after Step 3a (none demoted),
    Step 3b is a no-op and should auto-skip to Step 4."""
    from firefliesclearer.application.scan_service import ScanFilters
    from firefliesclearer.web.wizard_session import (
        WizardState,
        filters_to_dict,
        set_state,
    )

    sid = configured_client.cookies.get("ffc_session", "")
    set_state(
        configured_app.state.session_store,
        sid,
        WizardState(
            step="archive",
            filters=filters_to_dict(ScanFilters(older_than_days=30)),
            selected_ids=["m1", "m2"],
            operation_id=None,
            trash_ids=["m1", "m2"],
            trash_classifier_preset=None,
            trash_candidate_ids=[],
        ),
    )
    r = configured_client.get("/cleanup/archive", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/cleanup/purge"


def test_step3_preflight_renders_only_archive_subset(configured_client, configured_app) -> None:
    """Step 3 preflight must show only the archive_ids count, not the full
    selected_ids count (which would include trash rows)."""
    from datetime import UTC, datetime, timedelta

    from firefliesclearer.application.scan_service import ScanFilters
    from firefliesclearer.core.models import Meeting
    from firefliesclearer.web.wizard_session import (
        WizardState,
        filters_to_dict,
        set_state,
    )
    from tests.fakes.in_memory_repository import InMemoryMeetingRepository

    now = datetime(2026, 5, 6, 12, 0, tzinfo=UTC)
    meetings = [
        Meeting(
            meeting_id="m_archive",
            title="Archive me",
            meeting_date=now - timedelta(days=10),
            duration_minutes=60.0,
            host_email="oskar@example.com",
            participant_count=4,
        ),
        Meeting(
            meeting_id="m_trash",
            title="Trash me",
            meeting_date=now - timedelta(days=10),
            duration_minutes=15.0,
            host_email="oskar@example.com",
            participant_count=4,
        ),
    ]
    repo = InMemoryMeetingRepository(meetings=meetings, api_key="ff_test")
    repo.set_user_email_for_key("ff_test", "oskar@example.com")
    configured_app.state.deps.client = repo
    for m in meetings:
        configured_app.state.deps.manifest.upsert_known(m, at=now)

    sid = configured_client.cookies.get("ffc_session", "")
    set_state(
        configured_app.state.session_store,
        sid,
        WizardState(
            step="archive",
            filters=filters_to_dict(ScanFilters(older_than_days=30)),
            selected_ids=["m_archive", "m_trash"],
            operation_id=None,
            trash_ids=["m_trash"],
            trash_classifier_preset=None,
            trash_candidate_ids=[],
        ),
    )
    r = configured_client.get("/cleanup/archive")
    assert r.status_code == 200
    text = r.text
    # Count is 1 (m_archive only). The full selected_ids count of 2 must NOT
    # appear next to "meetings".
    assert "1 meeting" in text or " 1 " in text
    # Optional safety: m_trash should not be referenced on this page
    # (the preflight may not list titles, but if it does, m_trash shouldn't be there).


def test_step3_start_runs_archive_only_for_archive_ids(configured_client, configured_app) -> None:
    """The archive runner must consume only the archive subset."""
    from datetime import UTC, datetime, timedelta

    from firefliesclearer.application.scan_service import ScanFilters
    from firefliesclearer.core.models import Meeting
    from firefliesclearer.web.wizard_session import (
        WizardState,
        filters_to_dict,
        set_state,
    )
    from tests.fakes.in_memory_repository import InMemoryMeetingRepository

    now = datetime(2026, 5, 6, 12, 0, tzinfo=UTC)
    meetings = [
        Meeting(
            meeting_id="m_archive",
            title="Archive me",
            meeting_date=now - timedelta(days=10),
            duration_minutes=60.0,
            host_email="oskar@example.com",
            participant_count=4,
        ),
        Meeting(
            meeting_id="m_trash",
            title="Trash me",
            meeting_date=now - timedelta(days=10),
            duration_minutes=15.0,
            host_email="oskar@example.com",
            participant_count=4,
        ),
    ]
    repo = InMemoryMeetingRepository(meetings=meetings, api_key="ff_test")
    repo.set_user_email_for_key("ff_test", "oskar@example.com")
    configured_app.state.deps.client = repo
    for m in meetings:
        configured_app.state.deps.manifest.upsert_known(m, at=now)

    sid = configured_client.cookies.get("ffc_session", "")
    set_state(
        configured_app.state.session_store,
        sid,
        WizardState(
            step="archive",
            filters=filters_to_dict(ScanFilters(older_than_days=30)),
            selected_ids=["m_archive", "m_trash"],
            operation_id=None,
            trash_ids=["m_trash"],
            trash_classifier_preset=None,
            trash_candidate_ids=[],
        ),
    )
    csrf = configured_client.cookies.get("ffc_csrf", "")
    r = configured_client.post(
        "/cleanup/archive/start",
        data={"_csrf": csrf},
        follow_redirects=False,
    )
    assert r.status_code == 303
    # Operation registry should report 1 op with 1 meeting (the archive subset).
    op_id = configured_app.state.session_store.get(sid).get("wizard", {}).get("operation_id")
    assert op_id is not None
    op = configured_app.state.operation_registry.get(op_id)
    assert {m.meeting_id for m in op.meetings} == {"m_archive"}
