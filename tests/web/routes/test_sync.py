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


# ---------------------------------------------------------------------------
# POST /sync/now
# ---------------------------------------------------------------------------


def _csrf(client: TestClient) -> str:
    return client.cookies.get("ffc_csrf", "") or ""


def test_post_sync_now_returns_202_and_starts_a_run(configured_app_sync_on) -> None:
    """Happy path: POST /sync/now returns 202 with mode + trigger."""
    with TestClient(configured_app_sync_on) as client:
        client.get("/?token=T", follow_redirects=False)
        r = client.post(
            "/sync/now",
            data={
                "_csrf": _csrf(client),
                "mode": "incremental",
                "trigger": "manual_review",
            },
        )
        assert r.status_code == 202
        body = r.json()
        assert body["state"] == "running"
        assert body["mode"] == "incremental"


def test_post_sync_now_returns_409_when_already_running(configured_app_sync_on) -> None:
    """If sync_lock is already held, return 409."""
    from firefliesclearer.web.routes.sync import CurrentSyncSnapshot

    configured_app_sync_on.state.current_sync = CurrentSyncSnapshot(
        run_id=99,
        mode="full",
        trigger_source="scheduled",
        started_at=datetime(2026, 5, 2, 12, 0, tzinfo=UTC),
    )

    with TestClient(configured_app_sync_on) as client:
        client.get("/?token=T", follow_redirects=False)
        # Acquire the sync_lock on the portal's event loop so the route sees
        # ``locked() == True`` and short-circuits to 409.
        assert client.portal is not None
        client.portal.call(configured_app_sync_on.state.sync_lock.acquire)
        try:
            r = client.post(
                "/sync/now",
                data={
                    "_csrf": _csrf(client),
                    "mode": "incremental",
                    "trigger": "manual_review",
                },
            )
            assert r.status_code == 409
            body = r.json()
            assert body["current_run_id"] == 99
        finally:
            configured_app_sync_on.state.sync_lock.release()


def test_post_sync_now_rejects_invalid_mode(configured_app_sync_on) -> None:
    with TestClient(configured_app_sync_on) as client:
        client.get("/?token=T", follow_redirects=False)
        r = client.post(
            "/sync/now",
            data={
                "_csrf": _csrf(client),
                "mode": "bogus",
                "trigger": "manual_review",
            },
        )
        assert r.status_code == 422


def test_post_sync_now_rejects_invalid_trigger(configured_app_sync_on) -> None:
    with TestClient(configured_app_sync_on) as client:
        client.get("/?token=T", follow_redirects=False)
        r = client.post(
            "/sync/now",
            data={
                "_csrf": _csrf(client),
                "mode": "incremental",
                "trigger": "scheduled",
            },
        )
        # 'scheduled' is for scheduler-internal use; manual triggers must be
        # 'manual_review' or 'manual_settings'.
        assert r.status_code == 422
