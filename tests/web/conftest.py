from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from firefliesclearer.core.manifest import SCHEMA, Manifest
from firefliesclearer.infra.config import load_config
from firefliesclearer.infra.system_clock import SystemClock
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
    deps = SimpleNamespace(
        config=load_config(user_config=config_path),
        manifest=manifest,
        client=repo,
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
