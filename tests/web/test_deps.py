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
