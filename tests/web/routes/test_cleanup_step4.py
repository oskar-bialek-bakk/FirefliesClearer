"""Tests for cleanup wizard Step 4 — Purge (preflight, start, in-progress, done)."""

from __future__ import annotations

import asyncio
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
    """Persistent-portal TestClient (mirrors Step 3 fixture).

    See ``tests/web/routes/test_cleanup_step3.py`` for the rationale: the
    operation registry spawns asyncio tasks inside request handlers and the
    portal must survive across calls so we can later ``await op.task``.
    """
    with TestClient(configured_app) as c:
        c.get("/?token=T")
        yield c


def _seed_repo(app, count: int) -> list[Meeting]:
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
    assert client.portal is not None, "TestClient must be entered as a context manager"
    client.portal.call(_await_op, app, op_id)


# ---------------------------------------------------------------------------
# GET /cleanup/purge — preflight
# ---------------------------------------------------------------------------


def test_get_purge_redirects_to_archive_done_when_no_selection(
    configured_client: TestClient, configured_app
) -> None:
    _seed_repo(configured_app, 3)
    sid = _sid(configured_client)
    _set_wizard(configured_app, sid, step="purge", selected=[])
    r = configured_client.get("/cleanup/purge", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/cleanup/archive/done"


def test_get_purge_renders_preflight_with_count_and_short_list(
    configured_client: TestClient, configured_app
) -> None:
    _seed_repo(configured_app, 5)
    sid = _sid(configured_client)
    _set_wizard(configured_app, sid, step="purge", selected=["m0", "m1", "m2"])
    r = configured_client.get("/cleanup/purge")
    assert r.status_code == 200
    doc = HTMLParser(r.text)
    text = doc.text()
    # Destructive headline + count.
    assert "permanently delete" in text.lower()
    assert "3" in text
    # Stepper says step 4 (purge) is active.
    active = doc.css_first("nav.wizard-stepper li.step.active[data-step='purge']")
    assert active is not None
    # Numbered list of titles is visible (not collapsed).
    items = doc.css(".purge-meeting-list li")
    assert len(items) == 3
    # No <details> wrapper for short lists (≤10).
    details = doc.css_first("details.purge-meeting-list-collapse")
    assert details is None
    # Yellow/destructive notice present.
    assert "cannot be undone" in text.lower()
    # Typed-count input + Purge button present.
    assert doc.css_first("input#confirm-count") is not None
    btn = doc.css_first("button#purge-btn")
    assert btn is not None
    assert "disabled" in btn.attributes


def test_get_purge_renders_collapsible_list_when_more_than_ten(
    configured_client: TestClient, configured_app
) -> None:
    _seed_repo(configured_app, 15)
    sid = _sid(configured_client)
    selected = [f"m{i}" for i in range(11)]
    _set_wizard(configured_app, sid, step="purge", selected=selected)
    r = configured_client.get("/cleanup/purge")
    assert r.status_code == 200
    doc = HTMLParser(r.text)
    # The list is wrapped in <details> for 11+ items.
    details = doc.css_first("details.purge-meeting-list-collapse")
    assert details is not None
    items = doc.css(".purge-meeting-list li")
    assert len(items) == 11


# ---------------------------------------------------------------------------
# POST /cleanup/purge/start
# ---------------------------------------------------------------------------


def test_post_purge_start_redirects_when_empty_selection(
    configured_client: TestClient, configured_app
) -> None:
    sid = _sid(configured_client)
    _set_wizard(configured_app, sid, step="purge", selected=[])
    r = configured_client.post(
        "/cleanup/purge/start",
        data={"_csrf": _csrf(configured_client), "confirmed_count": "0"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/cleanup/archive/done"


def test_post_purge_start_returns_422_when_confirmation_missing(
    configured_client: TestClient, configured_app
) -> None:
    _seed_repo(configured_app, 3)
    sid = _sid(configured_client)
    _set_wizard(configured_app, sid, step="purge", selected=["m0", "m1"])
    r = configured_client.post(
        "/cleanup/purge/start",
        data={"_csrf": _csrf(configured_client)},  # no confirmed_count
        follow_redirects=False,
    )
    assert r.status_code == 422
    assert "type the count" in r.text.lower()


def test_post_purge_start_returns_422_when_confirmation_mismatches(
    configured_client: TestClient, configured_app
) -> None:
    _seed_repo(configured_app, 3)
    sid = _sid(configured_client)
    _set_wizard(configured_app, sid, step="purge", selected=["m0", "m1"])
    r = configured_client.post(
        "/cleanup/purge/start",
        data={"_csrf": _csrf(configured_client), "confirmed_count": "5"},
        follow_redirects=False,
    )
    assert r.status_code == 422
    assert "type the count" in r.text.lower()


def test_post_purge_start_kicks_off_op_when_count_matches(
    configured_client: TestClient, configured_app
) -> None:
    _seed_repo(configured_app, 5)
    sid = _sid(configured_client)
    _set_wizard(configured_app, sid, step="purge", selected=["m0", "m1"])
    r = configured_client.post(
        "/cleanup/purge/start",
        data={"_csrf": _csrf(configured_client), "confirmed_count": "2"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/cleanup/purge/in-progress"
    op_id = _wizard(configured_app, sid)["operation_id"]
    assert op_id is not None
    op = configured_app.state.operation_registry.get(op_id)
    assert op.kind == OperationKind.PURGE


def test_post_purge_start_returns_409_when_same_kind_running(
    configured_client: TestClient, configured_app
) -> None:
    _seed_repo(configured_app, 5)
    sid = _sid(configured_client)
    _set_wizard(configured_app, sid, step="purge", selected=["m0"])

    async def _start_blocking_op():
        async def runner(ctx):
            await asyncio.Event().wait()  # never returns

        op = await configured_app.state.operation_registry.start(
            kind=OperationKind.PURGE,
            meeting_ids=["other"],
            runner=runner,
        )
        return op.id

    assert configured_client.portal is not None
    blocking_id = configured_client.portal.call(_start_blocking_op)

    r = configured_client.post(
        "/cleanup/purge/start",
        data={"_csrf": _csrf(configured_client), "confirmed_count": "1"},
        follow_redirects=False,
    )
    assert r.status_code == 409
    assert "already running" in r.text.lower()

    async def _cancel_blocking() -> None:
        configured_app.state.operation_registry.get(blocking_id).task.cancel()

    configured_client.portal.call(_cancel_blocking)


# ---------------------------------------------------------------------------
# GET /cleanup/purge/in-progress
# ---------------------------------------------------------------------------


def test_purge_in_progress_redirects_when_no_op_id(
    configured_client: TestClient, configured_app
) -> None:
    sid = _sid(configured_client)
    _set_wizard(configured_app, sid, step="purge", selected=["m0"])
    r = configured_client.get("/cleanup/purge/in-progress", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/cleanup/purge"


def test_purge_in_progress_redirects_to_done_when_op_terminal(
    configured_client: TestClient, configured_app
) -> None:
    _seed_repo(configured_app, 3)
    sid = _sid(configured_client)
    _set_wizard(configured_app, sid, step="purge", selected=["m0"])
    configured_client.post(
        "/cleanup/purge/start",
        data={"_csrf": _csrf(configured_client), "confirmed_count": "1"},
        follow_redirects=False,
    )
    op_id = _wizard(configured_app, sid)["operation_id"]
    _wait_for_op(configured_client, configured_app, op_id)
    r = configured_client.get("/cleanup/purge/in-progress", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/cleanup/purge/done"


def test_purge_in_progress_renders_meeting_list_for_running_op(
    configured_client: TestClient, configured_app
) -> None:
    _seed_repo(configured_app, 3)
    sid = _sid(configured_client)
    _set_wizard(configured_app, sid, step="purge", selected=["m0", "m1"])

    block = asyncio.Event()

    class BlockingPipeline:
        async def purge_one(self, meeting):
            await block.wait()
            return MeetingState.DELETED

    configured_app.state.deps.pipeline = BlockingPipeline()

    configured_client.post(
        "/cleanup/purge/start",
        data={"_csrf": _csrf(configured_client), "confirmed_count": "2"},
        follow_redirects=False,
    )
    op_id = _wizard(configured_app, sid)["operation_id"]
    try:
        r = configured_client.get("/cleanup/purge/in-progress")
        assert r.status_code == 200
        doc = HTMLParser(r.text)
        assert doc.css_first("nav.wizard-stepper li.step.active[data-step='purge']") is not None
        rows = doc.css(".purge-meeting-row")
        assert len(rows) == 2
        cancel = doc.css_first("button.cancel-purge-btn")
        assert cancel is not None
        assert doc.css_first(".progress-bar") is not None
    finally:
        block.set()
        _wait_for_op(configured_client, configured_app, op_id)


# ---------------------------------------------------------------------------
# GET /cleanup/purge/done
# ---------------------------------------------------------------------------


def test_purge_done_redirects_to_in_progress_when_running(
    configured_client: TestClient, configured_app
) -> None:
    _seed_repo(configured_app, 2)
    sid = _sid(configured_client)
    _set_wizard(configured_app, sid, step="purge", selected=["m0"])
    block = asyncio.Event()

    class BlockingPipeline:
        async def purge_one(self, meeting):
            await block.wait()
            return MeetingState.DELETED

    configured_app.state.deps.pipeline = BlockingPipeline()
    configured_client.post(
        "/cleanup/purge/start",
        data={"_csrf": _csrf(configured_client), "confirmed_count": "1"},
        follow_redirects=False,
    )
    op_id = _wizard(configured_app, sid)["operation_id"]
    try:
        r = configured_client.get("/cleanup/purge/done", follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/cleanup/purge/in-progress"
    finally:
        block.set()
        _wait_for_op(configured_client, configured_app, op_id)


def test_purge_done_renders_summary_for_all_success(
    configured_client: TestClient, configured_app
) -> None:
    _seed_repo(configured_app, 3)
    sid = _sid(configured_client)
    _set_wizard(configured_app, sid, step="purge", selected=["m0", "m1"])
    configured_client.post(
        "/cleanup/purge/start",
        data={"_csrf": _csrf(configured_client), "confirmed_count": "2"},
        follow_redirects=False,
    )
    op_id = _wizard(configured_app, sid)["operation_id"]
    _wait_for_op(configured_client, configured_app, op_id)

    r = configured_client.get("/cleanup/purge/done")
    assert r.status_code == 200
    doc = HTMLParser(r.text)
    text = doc.text()
    assert "2 deleted" in text.lower()
    # No "X failed" copy when zero failures.
    done_btn = doc.css_first("button.purge-done-btn")
    assert done_btn is not None
    restart_btn = doc.css_first("button.purge-restart-btn")
    assert restart_btn is not None


def test_purge_done_renders_failed_rows_for_mixed_outcomes(
    configured_client: TestClient, configured_app
) -> None:
    _seed_repo(configured_app, 3)
    sid = _sid(configured_client)
    _set_wizard(configured_app, sid, step="purge", selected=["m0", "m1", "m2"])
    configured_app.state.deps.pipeline = FakePipeline(
        purge_outcomes={"m1": MeetingState.DELETED_FAILED}
    )
    configured_client.post(
        "/cleanup/purge/start",
        data={"_csrf": _csrf(configured_client), "confirmed_count": "3"},
        follow_redirects=False,
    )
    op_id = _wizard(configured_app, sid)["operation_id"]
    _wait_for_op(configured_client, configured_app, op_id)

    r = configured_client.get("/cleanup/purge/done")
    assert r.status_code == 200
    doc = HTMLParser(r.text)
    text = doc.text().lower()
    assert "2 deleted" in text
    assert "1 failed" in text
    failed_rows = doc.css(".purge-failed-row")
    assert len(failed_rows) == 1


# ---------------------------------------------------------------------------
# POST /cleanup/purge/finalize
# ---------------------------------------------------------------------------


def test_post_purge_finalize_clears_wizard_and_redirects_to_dashboard(
    configured_client: TestClient, configured_app
) -> None:
    _seed_repo(configured_app, 2)
    sid = _sid(configured_client)
    _set_wizard(configured_app, sid, step="purge", selected=["m0"], op_id="op_x")
    r = configured_client.post(
        "/cleanup/purge/finalize",
        data={"_csrf": _csrf(configured_client)},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/"
    assert _wizard(configured_app, sid) == {}


# ---------------------------------------------------------------------------
# POST /cleanup/purge/restart
# ---------------------------------------------------------------------------


def test_post_purge_restart_preserves_filters_and_redirects_to_cleanup(
    configured_client: TestClient, configured_app
) -> None:
    _seed_repo(configured_app, 2)
    sid = _sid(configured_client)
    # Pre-set wizard with filters + selection + op_id.
    state = WizardState(
        step="purge",
        filters=filters_to_dict(ScanFilters(older_than_days=42)),
        selected_ids=["m0", "m1"],
        operation_id="op_x",
    )
    set_state(configured_app.state.session_store, sid, state)

    r = configured_client.post(
        "/cleanup/purge/restart",
        data={"_csrf": _csrf(configured_client)},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/cleanup"
    new_state = _wizard(configured_app, sid)
    assert new_state.get("step") == "filter"
    assert new_state.get("selected_ids") == []
    assert new_state.get("operation_id") is None
    # Filters preserved.
    assert new_state.get("filters", {}).get("older_than_days") == 42


# ---------------------------------------------------------------------------
# step3_continue filter behaviour (Step 3 → Step 4 carry-over)
# ---------------------------------------------------------------------------


def test_step3_continue_filters_selection_to_archive_successes(
    configured_client: TestClient, configured_app
) -> None:
    """After archiving 3 meetings (1 fails), step3_continue must drop the
    failed one from ``selected_ids`` so Step 4 only sees archived successes.
    """
    _seed_repo(configured_app, 3)
    sid = _sid(configured_client)
    _set_wizard(configured_app, sid, step="archive", selected=["m0", "m1", "m2"])
    configured_app.state.deps.pipeline = FakePipeline(
        purge_outcomes={},  # not used here
        archive_outcomes={"m1": MeetingState.FAILED_FETCH},
    )
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
    # Only the two archived successes survive; "m1" (FAILED_FETCH) is dropped.
    assert sorted(state["selected_ids"]) == ["m0", "m2"]
    assert state["step"] == "purge"
    assert state["operation_id"] is None
