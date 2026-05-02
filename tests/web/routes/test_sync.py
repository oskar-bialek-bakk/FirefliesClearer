"""Tests for /sync/now and /sync/status endpoints."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient


def test_status_endpoint_idle_when_no_runs(configured_app_sync_on) -> None:
    with TestClient(configured_app_sync_on) as client:
        client.get("/?token=T", follow_redirects=False)
        r = client.get("/sync/status")
        assert r.status_code == 200
        body = r.json()
        assert body["state"] == "idle"
        assert body["last_run"] is None


def test_status_endpoint_returns_running_state_when_in_flight(configured_app_sync_on) -> None:
    """When a sync is in flight, status returns state=running with progress."""
    from firefliesclearer.web.routes.sync import CurrentSyncSnapshot

    configured_app_sync_on.state.current_sync = CurrentSyncSnapshot(
        run_id=1,
        mode="incremental",
        trigger_source="manual_review",
        started_at=datetime(2026, 5, 2, 12, 0, tzinfo=UTC),
        meetings_seen=10,
        meetings_added=5,
        meetings_updated=0,
        meetings_gone=0,
    )

    with TestClient(configured_app_sync_on) as client:
        client.get("/?token=T", follow_redirects=False)
        r = client.get("/sync/status")
        assert r.status_code == 200
        body = r.json()
        assert body["state"] == "running"
        assert body["mode"] == "incremental"
        assert body["meetings_added"] == 5


def test_status_endpoint_returns_last_completed_when_idle(configured_app_sync_on) -> None:
    manifest = configured_app_sync_on.state.deps.manifest
    rid = manifest.start_sync_run(
        mode="incremental",
        trigger="scheduled",
        at=datetime(2026, 5, 2, 12, 0, tzinfo=UTC),
    )
    manifest.finalize_sync_run(
        rid,
        outcome="success",
        at=datetime(2026, 5, 2, 12, 5, tzinfo=UTC),
    )

    with TestClient(configured_app_sync_on) as client:
        client.get("/?token=T", follow_redirects=False)
        r = client.get("/sync/status")
        assert r.status_code == 200
        body = r.json()
        assert body["state"] == "idle"
        assert body["last_run"] is not None
        assert body["last_run"]["outcome"] == "success"
