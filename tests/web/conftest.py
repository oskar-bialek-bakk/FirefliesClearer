from __future__ import annotations

import sqlite3
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from firefliesclearer.core.manifest import SCHEMA, Manifest
from firefliesclearer.core.models import ArtifactBundle, Meeting
from firefliesclearer.infra.config import load_config
from firefliesclearer.infra.system_clock import SystemClock
from firefliesclearer.ports.meeting_repository import MeetingFilter
from firefliesclearer.web.app import create_app
from tests.fakes.fake_pipeline import FakePipeline
from tests.fakes.in_memory_repository import InMemoryMeetingRepository


@pytest.fixture
def web_token() -> str:
    return "TESTTOKEN"


@pytest.fixture
def app(web_token: str):
    return create_app(session_token=web_token, csrf_secret="csrfsecret")


@pytest.fixture
def client(app, web_token: str) -> TestClient:
    c = TestClient(app)
    # Establish session by hitting any GET with token
    c.get("/?token=" + web_token)
    return c


@pytest.fixture
def archive_root(tmp_path: Path) -> Path:
    p = tmp_path / "archive"
    p.mkdir()
    return p


@pytest.fixture
def configured_app(tmp_path: Path, archive_root: Path):
    """An app with config already written, deps wired, and a fake repo.

    Mirrors the post-wizard state: ``app.state.deps`` is populated up-front
    so the lazy provider's "already populated" path is exercised by tests.
    """
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f"""
[fireflies]
api_key = "ff_test"
[archive]
root_dir = "{archive_root.as_posix()}"
summary_format = "pdf"
[run]
concurrency = 3
delete_confirmation_threshold = 10
""",
        encoding="utf-8",
    )
    repo = InMemoryMeetingRepository(api_key="ff_test")
    repo.set_user_email_for_key("ff_test", "oskar@example.com")
    # TestClient runs route handlers in a worker thread, so the manifest's
    # sqlite connection must be shareable across threads. Open it manually
    # with check_same_thread=False instead of via Manifest.open().
    db_path = archive_root / "manifest.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), isolation_level=None, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    manifest = Manifest(conn)
    # Phase 6: scan_repo is always the cache adapter; tests that seed
    # meetings via app.state.deps.client should also register them in the
    # manifest (see seed_meetings_in_manifest helper) so the cleanup
    # wizard's read path returns them.
    from firefliesclearer.infra.manifest_backed_repo import ManifestBackedRepository

    deps = SimpleNamespace(
        config=load_config(user_config=config_path),
        manifest=manifest,
        client=repo,
        scan_repo=ManifestBackedRepository(manifest),
        clock=SystemClock(),
        pipeline=FakePipeline(),
    )
    app = create_app(
        session_token="T",
        csrf_secret="S",
        config_path=config_path,
        repo_factory=lambda key: repo,
    )
    app.state.deps = deps
    return app


class _TrackingRepo:
    """Wraps an InMemoryMeetingRepository and counts ``list_meetings`` calls.

    Used to prove the wizard's read flows never hit the live API when
    ``[sync] enabled = true`` — a regression guard for the cache flip.
    """

    def __init__(self, inner: InMemoryMeetingRepository) -> None:
        self._inner = inner
        self.list_call_count = 0

    async def list_meetings(self, filter: MeetingFilter) -> AsyncIterator[Meeting]:
        self.list_call_count += 1
        async for m in self._inner.list_meetings(filter):
            yield m

    async def fetch_artifacts(self, meeting_id: str) -> ArtifactBundle:
        return await self._inner.fetch_artifacts(meeting_id)

    async def delete_meeting(self, meeting_id: str) -> None:
        await self._inner.delete_meeting(meeting_id)

    async def ping_user(self) -> str:
        return await self._inner.ping_user()


@pytest.fixture
def configured_app_sync_on(tmp_path: Path, archive_root: Path):
    """A configured_app with ``[sync] enabled = true`` and a tracking client.

    Tests using this fixture can assert that wizard reads never invoke
    ``client.list_meetings`` — proving the cache read path is wired up.
    """
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f"""
[fireflies]
api_key = "ff_test"
[archive]
root_dir = "{archive_root.as_posix()}"
summary_format = "pdf"
[run]
concurrency = 3
delete_confirmation_threshold = 10
[sync]
enabled = true
""",
        encoding="utf-8",
    )
    inner = InMemoryMeetingRepository(api_key="ff_test")
    inner.set_user_email_for_key("ff_test", "oskar@example.com")
    tracking = _TrackingRepo(inner)

    db_path = archive_root / "manifest.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), isolation_level=None, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    manifest = Manifest(conn)

    from firefliesclearer.infra.manifest_backed_repo import ManifestBackedRepository

    deps = SimpleNamespace(
        config=load_config(user_config=config_path),
        manifest=manifest,
        client=tracking,
        scan_repo=ManifestBackedRepository(manifest),
        clock=SystemClock(),
        pipeline=FakePipeline(),
    )

    app = create_app(
        session_token="T",
        csrf_secret="S",
        config_path=config_path,
        repo_factory=lambda key: tracking,
    )
    app.state.deps = deps
    app.state.tracking_repo = tracking
    return app
