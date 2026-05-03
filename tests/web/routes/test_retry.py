"""Tests for POST /retry/{meeting_id} — single-meeting retry from the Dashboard."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from selectolax.parser import HTMLParser

from firefliesclearer.core.archiver import Archiver
from firefliesclearer.core.models import ArtifactBundle, Meeting, MeetingState
from firefliesclearer.core.pipeline import Pipeline
from firefliesclearer.web.operations import OperationKind
from tests.fakes.fake_pipeline import FakePipeline
from tests.fakes.fake_renderer import FakeSummaryRenderer
from tests.fakes.frozen_clock import FrozenClock

NOW = datetime(2026, 4, 29, 12, 0, tzinfo=UTC)


@pytest.fixture
def configured_client(configured_app):
    """Persistent-portal TestClient.

    Like the Step 3/4 fixtures, retry tests start asyncio tasks via
    ``OperationRegistry.start`` inside request handlers; without a persistent
    portal the operation's ``task`` would be torn down with the per-request
    event loop, defeating ``op.task`` waits in later assertions.
    """
    with TestClient(configured_app) as c:
        c.get("/?token=T")
        yield c


def _csrf(c: TestClient) -> str:
    return c.cookies.get("ffc_csrf", "") or ""


def _seed_meeting(
    manifest,
    *,
    meeting_id: str = "m1",
    title: str = "Stuck Meeting",
    state: MeetingState = MeetingState.FAILED_DOWNLOAD,
    last_error: str | None = "Reset by peer",
    repo=None,
    duration_minutes: float = 10.0,
    host_email: str = "u@x.com",
    participant_count: int = 2,
    tags: tuple[str, ...] = (),
) -> Meeting:
    """Register a meeting and walk it into *state* through legal transitions.

    Currently supports any FAILED_* state plus DELETED_FAILED (via ARCHIVED)
    and ARCHIVED itself — the union the retry tests need.

    If *repo* is provided, the same Meeting is also registered with the
    in-memory repository so the retry handler can re-fetch it from "Fireflies".
    """
    meeting = Meeting(
        meeting_id=meeting_id,
        title=title,
        meeting_date=NOW,
        duration_minutes=duration_minutes,
        host_email=host_email,
        participant_count=participant_count,
        tags=tags,
    )
    # Seed via ``upsert_known`` so the manifest carries the full snapshot
    # fields (duration / host / participants / tags). The retry handler now
    # reads the Meeting from the local cache rather than re-fetching from
    # the live API; without snapshot fields, ``list_known`` would skip the
    # row and the retry runner would write a metadata.json with neutral
    # defaults — a regression silently caught by
    # ``test_retry_writes_full_metadata_to_archive_directory``.
    manifest.upsert_known(meeting, at=NOW)
    if repo is not None:
        repo._meetings[meeting_id] = meeting
    if state in {
        MeetingState.FAILED_FETCH,
        MeetingState.FAILED_DOWNLOAD,
        MeetingState.FAILED_RENDER,
        MeetingState.FAILED_VERIFY,
    }:
        manifest.transition(meeting_id, to=MeetingState.PENDING, at=NOW)
        manifest.transition(meeting_id, to=state, at=NOW, last_error=last_error)
    elif state is MeetingState.ARCHIVED:
        manifest.transition(meeting_id, to=MeetingState.PENDING, at=NOW)
        manifest.transition(meeting_id, to=MeetingState.ARCHIVED, at=NOW)
    elif state is MeetingState.DELETED_FAILED:
        manifest.transition(meeting_id, to=MeetingState.PENDING, at=NOW)
        manifest.transition(meeting_id, to=MeetingState.ARCHIVED, at=NOW)
        manifest.transition(
            meeting_id,
            to=MeetingState.DELETED_FAILED,
            at=NOW,
            last_error=last_error,
        )
    else:  # pragma: no cover — defensive: extend if a test needs another state
        raise ValueError(f"unsupported seed state: {state!r}")


async def _await_op(app, op_id: str) -> None:
    op = app.state.operation_registry.get(op_id)
    await op.task


def _wait_for_op(client: TestClient, app, op_id: str) -> None:
    """Wait for the named op to finish on the TestClient's portal loop."""
    assert client.portal is not None, "TestClient must be entered as a context manager"
    client.portal.call(_await_op, app, op_id)


# ---------------------------------------------------------------------------
# Dashboard partial: Retry button presence
# ---------------------------------------------------------------------------


def test_dashboard_renders_retry_button_for_each_failed_row(
    configured_client: TestClient, configured_app
) -> None:
    manifest = configured_app.state.deps.manifest
    _seed_meeting(manifest, meeting_id="m1", state=MeetingState.FAILED_DOWNLOAD)
    _seed_meeting(manifest, meeting_id="m2", state=MeetingState.FAILED_RENDER)

    r = configured_client.get("/")
    assert r.status_code == 200
    doc = HTMLParser(r.text)
    rows = doc.css(".needs-attention-row")
    assert len(rows) == 2
    # Each row has a Retry button targeting POST /retry/{id} via HTMX.
    for row in rows:
        form = row.css_first("form.needs-attention-row__retry")
        assert form is not None
        meeting_id = row.attributes.get("data-meeting-id")
        assert form.attributes.get("hx-post") == f"/retry/{meeting_id}"
        assert form.attributes.get("hx-target") == "closest .needs-attention-row"
        assert form.attributes.get("hx-swap") == "outerHTML"
        # CSRF hidden input is present so a non-HTMX submit works too.
        csrf_input = form.css_first("input[name='_csrf']")
        assert csrf_input is not None
        button = form.css_first("button.retry-btn")
        assert button is not None


# ---------------------------------------------------------------------------
# POST /retry/{meeting_id} — error paths
# ---------------------------------------------------------------------------


def test_retry_returns_404_for_missing_meeting(configured_client: TestClient) -> None:
    r = configured_client.post(
        "/retry/no-such-id",
        data={"_csrf": _csrf(configured_client)},
    )
    assert r.status_code == 404


def test_retry_returns_409_for_archived_meeting(
    configured_client: TestClient, configured_app
) -> None:
    manifest = configured_app.state.deps.manifest
    _seed_meeting(manifest, meeting_id="ok", state=MeetingState.ARCHIVED)

    r = configured_client.post(
        "/retry/ok",
        data={"_csrf": _csrf(configured_client)},
    )
    assert r.status_code == 409
    # Pretend the operator might read the message; smoke-check the wording.
    assert "retry-able" in r.text.lower()


def test_retry_returns_409_when_same_kind_already_running(
    configured_client: TestClient, configured_app
) -> None:
    """A live RETRY_ARCHIVE op blocks a second RETRY_ARCHIVE start."""
    manifest = configured_app.state.deps.manifest
    repo = configured_app.state.deps.client
    _seed_meeting(manifest, meeting_id="m1", state=MeetingState.FAILED_DOWNLOAD, repo=repo)

    # Start a long-running RETRY_ARCHIVE op directly via the registry, on the
    # TestClient's portal loop so the asyncio.Event survives the subsequent
    # request's event-loop boundary (mirrors test_cleanup_step3 pattern).
    async def _start_blocking_op() -> str:
        async def runner(ctx):
            await asyncio.Event().wait()  # never returns

        op = await configured_app.state.operation_registry.start(
            kind=OperationKind.RETRY_ARCHIVE,
            meeting_ids=["other"],
            runner=runner,
        )
        return op.id

    assert configured_client.portal is not None
    blocking_id = configured_client.portal.call(_start_blocking_op)

    r = configured_client.post(
        "/retry/m1",
        data={"_csrf": _csrf(configured_client)},
    )
    assert r.status_code == 409

    async def _cancel_blocking() -> None:
        configured_app.state.operation_registry.get(blocking_id).task.cancel()

    configured_client.portal.call(_cancel_blocking)


# ---------------------------------------------------------------------------
# POST /retry/{meeting_id} — happy paths
# ---------------------------------------------------------------------------


def test_retry_failed_archive_meeting_starts_retry_archive_op(
    configured_client: TestClient, configured_app
) -> None:
    manifest = configured_app.state.deps.manifest
    repo = configured_app.state.deps.client
    _seed_meeting(manifest, meeting_id="m1", state=MeetingState.FAILED_DOWNLOAD, repo=repo)

    r = configured_client.post(
        "/retry/m1",
        data={"_csrf": _csrf(configured_client)},
    )
    assert r.status_code == 200

    # Fragment carries data-operation-id for the SSE script to latch onto.
    doc = HTMLParser(r.text)
    root = doc.css_first("[data-operation-id]")
    assert root is not None
    op_id = root.attributes.get("data-operation-id")
    assert op_id is not None
    assert root.attributes.get("data-kind") == "archive"
    assert root.attributes.get("data-meeting-id") == "m1"

    # Registry has a running RETRY_ARCHIVE op.
    op = configured_app.state.operation_registry.get(op_id)
    assert op.kind == OperationKind.RETRY_ARCHIVE
    # Wait for completion to keep test isolation tidy.
    _wait_for_op(configured_client, configured_app, op_id)


def test_retry_deleted_failed_meeting_starts_retry_purge_op(
    configured_client: TestClient, configured_app
) -> None:
    manifest = configured_app.state.deps.manifest
    repo = configured_app.state.deps.client
    _seed_meeting(manifest, meeting_id="m2", state=MeetingState.DELETED_FAILED, repo=repo)

    r = configured_client.post(
        "/retry/m2",
        data={"_csrf": _csrf(configured_client)},
    )
    assert r.status_code == 200

    doc = HTMLParser(r.text)
    root = doc.css_first("[data-operation-id]")
    assert root is not None
    op_id = root.attributes.get("data-operation-id")
    assert op_id is not None
    assert root.attributes.get("data-kind") == "purge"

    op = configured_app.state.operation_registry.get(op_id)
    assert op.kind == OperationKind.RETRY_PURGE
    _wait_for_op(configured_client, configured_app, op_id)


def test_retry_archive_success_transitions_manifest_to_archived(
    configured_client: TestClient, configured_app
) -> None:
    """End-to-end through the runner: a successful retry updates the manifest."""
    manifest = configured_app.state.deps.manifest
    repo = configured_app.state.deps.client
    _seed_meeting(manifest, meeting_id="m1", state=MeetingState.FAILED_DOWNLOAD, repo=repo)
    # Default FakePipeline returns ARCHIVED, which the pipeline-less FakePipeline
    # uses directly — but ArchiveService.archive_meetings goes through
    # pipeline.archive_one which writes the manifest transition. Replace the
    # pipeline with one whose archive_one updates the manifest itself.

    class ManifestUpdatingPipeline:
        async def archive_one(self, meeting):
            manifest.transition(
                meeting.meeting_id,
                to=MeetingState.PENDING,
                at=NOW,
            )
            manifest.transition(
                meeting.meeting_id,
                to=MeetingState.ARCHIVED,
                at=NOW,
            )
            return MeetingState.ARCHIVED

    configured_app.state.deps.pipeline = ManifestUpdatingPipeline()

    r = configured_client.post(
        "/retry/m1",
        data={"_csrf": _csrf(configured_client)},
    )
    assert r.status_code == 200
    doc = HTMLParser(r.text)
    op_id = doc.css_first("[data-operation-id]").attributes["data-operation-id"]
    assert op_id is not None
    _wait_for_op(configured_client, configured_app, op_id)

    rec = manifest.get("m1")
    assert rec is not None
    assert rec.state is MeetingState.ARCHIVED


def test_retry_archive_failure_keeps_meeting_failed(
    configured_client: TestClient, configured_app
) -> None:
    manifest = configured_app.state.deps.manifest
    repo = configured_app.state.deps.client
    _seed_meeting(manifest, meeting_id="m1", state=MeetingState.FAILED_DOWNLOAD, repo=repo)

    class StillFailingPipeline:
        async def archive_one(self, meeting):
            # No manifest mutation: the meeting stays FAILED_DOWNLOAD.
            return MeetingState.FAILED_DOWNLOAD

    configured_app.state.deps.pipeline = StillFailingPipeline()

    r = configured_client.post(
        "/retry/m1",
        data={"_csrf": _csrf(configured_client)},
    )
    assert r.status_code == 200
    doc = HTMLParser(r.text)
    op_id = doc.css_first("[data-operation-id]").attributes["data-operation-id"]
    assert op_id is not None
    _wait_for_op(configured_client, configured_app, op_id)

    rec = manifest.get("m1")
    assert rec is not None
    # State is unchanged — pipeline returned a failed state without writing
    # the manifest. The op itself completed (succeeded) — it's the meeting
    # outcome, not the op, that "failed".
    assert rec.state is MeetingState.FAILED_DOWNLOAD


def test_retry_purge_success_transitions_manifest_to_deleted(
    configured_client: TestClient, configured_app
) -> None:
    manifest = configured_app.state.deps.manifest
    repo = configured_app.state.deps.client
    _seed_meeting(manifest, meeting_id="m2", state=MeetingState.DELETED_FAILED, repo=repo)

    class PurgingPipeline:
        async def purge_one(self, meeting):
            manifest.transition(
                meeting.meeting_id,
                to=MeetingState.DELETED,
                at=NOW,
            )
            return MeetingState.DELETED

    # Use a generic SimpleNamespace-like swap that supports both archive_one
    # and purge_one calls, even though only purge is exercised here. Plain
    # FakePipeline already exposes both methods; pre-configure DELETED.
    pipeline = FakePipeline(purge_outcomes={"m2": MeetingState.DELETED})
    # Wrap purge_one so the manifest transitions too.
    real_purge = PurgingPipeline().purge_one

    async def purge_one(meeting):
        pipeline.purge_calls.append(meeting.meeting_id)
        return await real_purge(meeting)

    pipeline.purge_one = purge_one  # type: ignore[method-assign]
    configured_app.state.deps.pipeline = pipeline

    r = configured_client.post(
        "/retry/m2",
        data={"_csrf": _csrf(configured_client)},
    )
    assert r.status_code == 200
    doc = HTMLParser(r.text)
    op_id = doc.css_first("[data-operation-id]").attributes["data-operation-id"]
    assert op_id is not None
    _wait_for_op(configured_client, configured_app, op_id)

    rec = manifest.get("m2")
    assert rec is not None
    assert rec.state is MeetingState.DELETED


# ---------------------------------------------------------------------------
# Metadata fidelity: retry must re-fetch live Meeting from the source
# ---------------------------------------------------------------------------


def test_retry_returns_404_when_meeting_marked_gone_in_cache(
    configured_client: TestClient, configured_app
) -> None:
    """If the last full sync flagged the meeting as gone-from-source (the
    user deleted it in Fireflies and a sync run noticed), the retry button
    must 404 rather than starting a runner that will inevitably 404 against
    the live API.

    Behaviour change (2026-05-03): this previously detected absence by
    walking the live API. The retry path now reads the local cache
    (avoiding the slow + rate-limit-prone API lookup that made the button
    appear inert under load), so absence detection moves to the cache's
    ``source_state='gone'`` flag — which sync maintains."""
    manifest = configured_app.state.deps.manifest
    _seed_meeting(
        manifest,
        meeting_id="m-deleted-upstream",
        state=MeetingState.FAILED_DOWNLOAD,
    )
    # Mark the row as gone-from-source — this is what a full sync run sets
    # when the meeting disappears from the upstream listing.
    manifest.set_source_state("m-deleted-upstream", "gone")

    r = configured_client.post(
        "/retry/m-deleted-upstream",
        data={"_csrf": _csrf(configured_client)},
    )
    assert r.status_code == 404
    # Distinct error message vs the absent-from-cache case — pointing the
    # user at "run a sync first" would be misleading here because sync
    # was the very thing that learned the meeting was gone.
    body = r.text.lower()
    assert "no longer" in body
    assert "run a sync" not in body


def test_retry_returns_distinct_404_when_meeting_absent_from_cache(
    configured_client: TestClient, configured_app
) -> None:
    """The other 404 path: the meeting id wasn't found at all. Tells the
    user to sync — that's the action that fixes it. Pairs with the
    gone-from-source test to lock down the missing-vs-gone distinction
    Copilot flagged on PR #19."""
    r = configured_client.post(
        "/retry/never-seen-this-id",
        data={"_csrf": _csrf(configured_client)},
    )
    assert r.status_code == 404
    body = r.text.lower()
    # Falls through the kind/state checks first since manifest.get returns
    # None — message comes from the "Meeting not found in manifest" path.
    assert "not found" in body or "not in local cache" in body


def test_retry_writes_full_metadata_to_archive_directory(
    configured_client: TestClient, configured_app
) -> None:
    """A successful retry persists the live Meeting metadata to metadata.json.

    Regression for the silent-fidelity-loss bug where ``_meeting_from_manifest``
    handed neutral defaults (duration=0, host="", participants=0, tags=()) to
    the pipeline and the archiver wrote those zeros into ``metadata.json``.
    """
    manifest = configured_app.state.deps.manifest
    repo = configured_app.state.deps.client
    archive_root = configured_app.state.deps.config.archive.root_dir

    # Seed with full metadata. The repo holds the live Meeting so the retry
    # handler can re-fetch it; the manifest holds the failed record.
    _seed_meeting(
        manifest,
        meeting_id="m1",
        title="Standup",
        state=MeetingState.FAILED_DOWNLOAD,
        repo=repo,
        duration_minutes=45.0,
        host_email="alice@x",
        participant_count=6,
        tags=("standup",),
    )
    # Provide artifacts so the real Archiver has bytes to write.
    repo._artifacts["m1"] = ArtifactBundle(
        audio_bytes=b"AUDIO",
        transcript_markdown="# T",
        summary_payload={"overview": "ov"},
    )

    # Swap in a real Pipeline + Archiver so the retry actually writes the
    # archive directory (FakePipeline only mutates a state outcome — it
    # doesn't touch disk, which is exactly what the bug was hiding behind).
    archiver = Archiver(archive_root=archive_root)
    renderer = FakeSummaryRenderer()
    clock = FrozenClock(NOW)
    configured_app.state.deps.pipeline = Pipeline(
        repository=repo,
        manifest=manifest,
        archiver=archiver,
        renderer=renderer,
        clock=clock,
    )

    r = configured_client.post(
        "/retry/m1",
        data={"_csrf": _csrf(configured_client)},
    )
    assert r.status_code == 200, r.text
    doc = HTMLParser(r.text)
    op_id = doc.css_first("[data-operation-id]").attributes["data-operation-id"]
    assert op_id is not None
    _wait_for_op(configured_client, configured_app, op_id)

    rec = manifest.get("m1")
    assert rec is not None
    assert rec.state is MeetingState.ARCHIVED
    assert rec.archive_path is not None

    # Discover the canonical archive directory from the manifest record —
    # the slugified directory name is built from title + date and isn't
    # something tests should re-compute.
    from pathlib import Path as _Path

    metadata_path = _Path(rec.archive_path) / "metadata.json"
    assert metadata_path.exists(), f"missing metadata.json at {metadata_path}"
    meta = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert meta["duration_minutes"] == 45.0
    assert meta["host_email"] == "alice@x"
    assert meta["participant_count"] == 6
    assert meta["tags"] == ["standup"]
