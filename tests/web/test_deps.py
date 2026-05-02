"""Tests for ``firefliesclearer.web.deps.get_deps`` — lazy build + error branches.

Exercises the cold path of the dependency provider:

* First call when ``app.state.deps`` is ``None`` builds a fresh ``SimpleNamespace``
  and caches it on ``app.state.deps``; subsequent calls reuse the cached object.
* Missing ``config_path`` raises ``HTTPException(500)``.
* Missing ``repo_factory`` raises ``HTTPException(500)``.

Tests call ``get_deps`` directly with a fabricated Starlette ``Request``
to keep the unit narrow and avoid going through the HTTP/middleware stack.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from starlette.requests import Request

from firefliesclearer.web.app import create_app
from firefliesclearer.web.deps import get_deps
from tests.fakes.in_memory_repository import InMemoryMeetingRepository


def _make_request(app: FastAPI) -> Request:
    """Build a minimal Starlette Request bound to *app* for direct provider calls."""
    return Request({"type": "http", "app": app, "headers": []})


def _write_config(config_path: Path, archive_root: Path) -> None:
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


async def test_get_deps_builds_lazily_then_reuses_cache(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    archive_root.mkdir()
    config_path = tmp_path / "config.toml"
    _write_config(config_path, archive_root)

    repo = InMemoryMeetingRepository(api_key="ff_test")
    app = create_app(
        session_token="T",
        csrf_secret="S",
        config_path=config_path,
        repo_factory=lambda _key: repo,
    )
    # Force the lazy path: configured_app fixture pre-populates deps; we want None.
    app.state.deps = None

    request = _make_request(app)
    built = await get_deps(request)

    assert isinstance(built, SimpleNamespace)
    assert built.config is not None
    assert built.manifest is not None
    assert built.client is repo
    assert built.clock is not None
    # Cached on app.state for subsequent requests.
    assert app.state.deps is built

    # Second call returns the SAME object (identity, not equality).
    again = await get_deps(_make_request(app))
    assert again is built


async def test_get_deps_raises_500_when_config_path_is_none() -> None:
    app = create_app(
        session_token="T",
        csrf_secret="S",
        config_path=None,
        repo_factory=lambda _key: InMemoryMeetingRepository(),
    )
    app.state.deps = None

    with pytest.raises(HTTPException) as exc_info:
        await get_deps(_make_request(app))

    assert exc_info.value.status_code == 500


async def test_get_deps_raises_500_when_config_path_does_not_exist(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist.toml"
    app = create_app(
        session_token="T",
        csrf_secret="S",
        config_path=missing,
        repo_factory=lambda _key: InMemoryMeetingRepository(),
    )
    app.state.deps = None

    with pytest.raises(HTTPException) as exc_info:
        await get_deps(_make_request(app))

    assert exc_info.value.status_code == 500


async def test_get_deps_starts_scheduler_when_sync_enabled(tmp_path: Path) -> None:
    """Lazy deps build with [sync] enabled creates a scheduler task."""
    archive_root = tmp_path / "archive"
    archive_root.mkdir()
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f"""
[fireflies]
api_key = "ff_test"
[archive]
root_dir = "{archive_root.as_posix()}"
[sync]
enabled = true
""",
        encoding="utf-8",
    )
    repo = InMemoryMeetingRepository(api_key="ff_test")
    app = create_app(
        session_token="T",
        csrf_secret="S",
        config_path=config_path,
        repo_factory=lambda _key: repo,
    )
    app.state.deps = None

    request = _make_request(app)
    await get_deps(request)

    # After lazy build, the scheduler task is on app.state and not done
    assert hasattr(app.state, "sync_scheduler_task")
    assert app.state.sync_scheduler_task is not None
    # Cleanup: signal shutdown so the task ends
    app.state.sync_shutdown_event.set()
    await app.state.sync_scheduler_task


async def test_lazy_deps_with_empty_cache_triggers_bootstrap_sync(tmp_path: Path) -> None:
    """End-to-end: empty cache + sync enabled → scheduler runs bootstrap."""
    import asyncio
    from collections.abc import AsyncIterator
    from datetime import UTC
    from datetime import datetime as _dt

    from firefliesclearer.core.models import Meeting

    archive_root = tmp_path / "archive"
    archive_root.mkdir()
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f"""
[fireflies]
api_key = "ff_test"
[archive]
root_dir = "{archive_root.as_posix()}"
[sync]
enabled = true
incremental_interval_hours = 24
""",
        encoding="utf-8",
    )
    repo = InMemoryMeetingRepository(
        meetings=[
            Meeting(
                meeting_id="m0",
                title="t",
                meeting_date=_dt(2026, 4, 1, tzinfo=UTC),
                duration_minutes=30.0,
                host_email="a@x",
                participant_count=2,
                tags=(),
                has_transcript=True,
            )
        ],
        api_key="ff_test",
    )
    repo.set_user_email_for_key("ff_test", "oskar@example.com")

    # InMemoryMeetingRepository's list_meetings doesn't match the
    # ControllableMeetingRepository.list_meetings_page contract that
    # SyncService uses. Monkey-patch a thin paged shim for this test.
    async def _list_page(
        *, skip: int, limit: int, to_date: _dt | None = None
    ) -> AsyncIterator[Meeting]:
        items = list(repo._meetings.values())
        for i, m in enumerate(items):
            if i < skip:
                continue
            if i >= skip + limit:
                break
            yield m

    repo.list_meetings_page = _list_page  # type: ignore[attr-defined]

    app = create_app(
        session_token="T",
        csrf_secret="S",
        config_path=config_path,
        repo_factory=lambda _key: repo,
    )
    app.state.deps = None

    request = _make_request(app)
    await get_deps(request)

    # Wait up to a few seconds for the scheduler to fire bootstrap
    last = None
    for _ in range(50):
        await asyncio.sleep(0.05)
        last = app.state.deps.manifest.get_last_sync_run()
        if last is not None and last.outcome == "success":
            break
    assert last is not None
    assert last.trigger_source == "bootstrap"
    assert last.outcome == "success"

    # Cleanup
    app.state.sync_shutdown_event.set()
    await app.state.sync_scheduler_task


async def test_get_deps_does_not_start_scheduler_when_sync_disabled(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    archive_root.mkdir()
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f"""
[fireflies]
api_key = "ff_test"
[archive]
root_dir = "{archive_root.as_posix()}"
""",
        encoding="utf-8",
    )
    repo = InMemoryMeetingRepository(api_key="ff_test")
    app = create_app(
        session_token="T",
        csrf_secret="S",
        config_path=config_path,
        repo_factory=lambda _key: repo,
    )
    app.state.deps = None

    request = _make_request(app)
    await get_deps(request)

    # Scheduler not started when flag is off
    assert getattr(app.state, "sync_scheduler_task", None) is None


async def test_get_deps_raises_500_when_repo_factory_is_none(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    archive_root.mkdir()
    config_path = tmp_path / "config.toml"
    _write_config(config_path, archive_root)

    app = create_app(
        session_token="T",
        csrf_secret="S",
        config_path=config_path,
        repo_factory=None,
    )
    app.state.deps = None

    with pytest.raises(HTTPException) as exc_info:
        await get_deps(_make_request(app))

    assert exc_info.value.status_code == 500
