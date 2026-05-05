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


def test_status_includes_estimated_total_during_running(configured_app_sync_on) -> None:
    """Bootstrap-aware status surfaces estimated_total + is_bootstrap flag."""
    from firefliesclearer.web.routes.sync import CurrentSyncSnapshot

    configured_app_sync_on.state.current_sync = CurrentSyncSnapshot(
        run_id=1,
        mode="full",
        trigger_source="bootstrap",
        started_at=datetime(2026, 5, 2, 12, 0, tzinfo=UTC),
        meetings_seen=100,
        meetings_added=100,
        last_page_size=50,
    )
    with TestClient(configured_app_sync_on) as client:
        client.get("/?token=T", follow_redirects=False)
        body = client.get("/sync/status").json()
        assert body["estimated_total"] == 150
        assert body["is_bootstrap"] is True


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


def test_post_sync_now_wires_snapshot_callback_even_when_service_preset(
    configured_app_sync_on, monkeypatch
) -> None:
    """Regression: when ``app.state.sync_service`` is already populated (the
    normal case once the scheduler has booted), /sync/now must still construct
    a fresh SyncService with the route's ``_update_snapshot`` callback so the
    banner reflects per-page progress mid-run. Previously the route reused
    the pre-set service without the callback, leaving the banner at 0/0.
    """
    from firefliesclearer.application.sync_service import SyncOutcome
    from firefliesclearer.web.routes import sync as sync_module

    captured: dict[str, object] = {}
    real_cls = sync_module.SyncService

    class _Probe(real_cls):  # type: ignore[misc, valid-type]
        def __init__(self, *args: object, **kwargs: object) -> None:
            captured["snapshot_callback"] = kwargs.get("snapshot_callback")
            super().__init__(*args, **kwargs)  # type: ignore[arg-type]

        async def run(
            self, *, mode: object, trigger: object, resume_run_id: int | None = None
        ) -> SyncOutcome:
            return SyncOutcome.success(
                run_id=1,
                meetings_seen=0,
                meetings_added=0,
                meetings_updated=0,
                meetings_gone=0,
            )

    monkeypatch.setattr(sync_module, "SyncService", _Probe)
    configured_app_sync_on.state.sync_service = object()

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

    assert "snapshot_callback" in captured, "route did not construct a fresh SyncService"
    assert captured["snapshot_callback"] is not None, (
        "snapshot_callback must be wired so the banner publishes mid-run progress"
    )


def test_status_dict_carries_local_time_strings_for_template(
    configured_app_sync_on,
) -> None:
    """Regression: the banner displayed UTC ISO strings ("2026-05-03T07:48:43...")
    that confused users running in non-UTC timezones. The status dict must
    expose ``next_resume_at_local`` / ``finished_at_local`` rendered in the
    server's local timezone (which equals the user's, since serve is
    loopback-only) so templates can show a friendly value.
    """
    from firefliesclearer.core.manifest import Manifest

    # Seed a partial run with a known UTC next_resume_at.
    manifest: Manifest = configured_app_sync_on.state.deps.manifest
    started = datetime(2026, 5, 3, 7, 0, tzinfo=UTC)
    finished = datetime(2026, 5, 3, 7, 30, tzinfo=UTC)
    next_resume = datetime(2026, 5, 3, 7, 48, 43, tzinfo=UTC)
    rid = manifest.start_sync_run(mode="incremental", trigger="scheduled", at=started)
    manifest.mark_sync_run_partial(
        rid,
        at=finished,
        next_resume_at=next_resume,
        error_message="rate limited",
    )

    with TestClient(configured_app_sync_on) as client:
        client.get("/?token=T", follow_redirects=False)
        body = client.get("/sync/status").json()

    last = body["last_run"]
    assert last is not None
    assert last["next_resume_at"] == "2026-05-03T07:48:43+00:00"  # ISO unchanged
    assert last["next_resume_at_local"] is not None
    # The local rendering must NOT contain the "+00:00" UTC suffix nor the "T"
    # separator — that is the whole point of the friendly format.
    assert "+00:00" not in last["next_resume_at_local"]
    assert "T" not in last["next_resume_at_local"]
    assert last["finished_at_local"] is not None


def test_post_sync_now_returns_running_banner_html_for_htmx_client(
    configured_app_sync_on,
) -> None:
    """Regression: when the dashboard / review-toolbar Sync now form (HTMX)
    POSTs to /sync/now, the response must be the banner partial — not JSON —
    so HTMX can swap it into ``#sync-banner``. Without this, the form swap
    inserted ``{"state":"running",...}`` text into the page and the user
    saw nothing change until they manually refreshed.
    """
    with TestClient(configured_app_sync_on) as client:
        client.get("/?token=T", follow_redirects=False)
        r = client.post(
            "/sync/now",
            data={
                "_csrf": _csrf(client),
                "mode": "incremental",
                "trigger": "manual_review",
            },
            headers={"HX-Request": "true"},
        )
        assert r.status_code == 200, r.text
        assert "text/html" in r.headers.get("content-type", "")
        # Banner must reflect the running state (id + class wiring is what
        # HTMX targets / styles).
        assert 'id="sync-banner"' in r.text
        assert "sync-banner--running" in r.text


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


# ---------------------------------------------------------------------------
# Phase 5: manual sync cooldown
# ---------------------------------------------------------------------------


def test_post_sync_now_returns_429_during_cooldown(configured_app_sync_on) -> None:
    """When the most recent sync finished less than MANUAL_SYNC_COOLDOWN ago,
    POST /sync/now is rejected with 429 + Retry-After. Without this gate a
    user click-spamming the Sync now button would burn pages of API quota
    for the same answer."""
    manifest = configured_app_sync_on.state.deps.manifest
    rid = manifest.start_sync_run(
        mode="incremental",
        trigger="manual_review",
        at=datetime(2026, 5, 2, 12, 0, tzinfo=UTC),
    )
    manifest.finalize_sync_run(
        rid,
        outcome="success",
        # Finished 30 seconds ago — well within the 5-minute cooldown.
        at=datetime.now(UTC),
    )

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
        assert r.status_code == 429
        assert "Retry-After" in r.headers
        body = r.json()
        assert body["error"] == "cooldown"
        assert body["retry_after_seconds"] > 0


def test_post_sync_now_proceeds_after_cooldown_window(configured_app_sync_on) -> None:
    """A sync that finished long enough ago (well past the 5-minute cooldown)
    is allowed through normally."""
    manifest = configured_app_sync_on.state.deps.manifest
    rid = manifest.start_sync_run(
        mode="incremental",
        trigger="manual_review",
        at=datetime(2026, 5, 2, 12, 0, tzinfo=UTC),
    )
    manifest.finalize_sync_run(
        rid,
        outcome="success",
        at=datetime(2026, 5, 2, 12, 5, tzinfo=UTC),  # ~ a year ago vs SystemClock.now()
    )

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
        # 202 = run started; the cooldown gate didn't trip.
        assert r.status_code == 202


# ---------------------------------------------------------------------------
# GET /sync/status/banner — HTML partial render
# ---------------------------------------------------------------------------


def test_status_banner_renders_idle_html(configured_app_sync_on) -> None:
    with TestClient(configured_app_sync_on) as client:
        client.get("/?token=T", follow_redirects=False)
        r = client.get("/sync/status/banner")
        assert r.status_code == 200
        assert "sync-banner--idle" in r.text
        assert "Sync now" in r.text or "No sync run yet" in r.text


def test_status_banner_renders_running_html(configured_app_sync_on) -> None:
    from firefliesclearer.web.routes.sync import CurrentSyncSnapshot

    configured_app_sync_on.state.current_sync = CurrentSyncSnapshot(
        run_id=1,
        mode="incremental",
        trigger_source="manual_review",
        started_at=datetime(2026, 5, 2, 12, 0, tzinfo=UTC),
        meetings_seen=10,
        meetings_added=5,
    )

    with TestClient(configured_app_sync_on) as client:
        client.get("/?token=T", follow_redirects=False)
        r = client.get("/sync/status/banner")
        assert r.status_code == 200
        assert "sync-banner--running" in r.text
        assert "10" in r.text  # meetings_seen rendered


# ---------------------------------------------------------------------------
# POST /sync/enable — opt-in / dismiss banner
# ---------------------------------------------------------------------------


def test_post_sync_enable_writes_flag_and_redirects(configured_app) -> None:
    """POST /sync/enable with action=enable persists [sync] enabled=true."""
    import tomllib

    with TestClient(configured_app) as client:
        client.get("/?token=T", follow_redirects=False)
        csrf = client.cookies.get("ffc_csrf", "") or ""
        r = client.post(
            "/sync/enable",
            data={"_csrf": csrf, "action": "enable"},
            follow_redirects=False,
        )
        assert r.status_code in (302, 303, 307)
        cfg_path = configured_app.state.config_path
        with open(cfg_path, "rb") as f:
            data = tomllib.load(f)
        assert data["sync"]["enabled"] is True


def test_post_sync_enable_dismiss_writes_marker(configured_app) -> None:
    """POST /sync/enable with action=dismiss persists opt_in_dismissed=true."""
    import tomllib

    with TestClient(configured_app) as client:
        client.get("/?token=T", follow_redirects=False)
        csrf = client.cookies.get("ffc_csrf", "") or ""
        r = client.post(
            "/sync/enable",
            data={"_csrf": csrf, "action": "dismiss"},
            follow_redirects=False,
        )
        assert r.status_code in (302, 303, 307)
        cfg_path = configured_app.state.config_path
        with open(cfg_path, "rb") as f:
            data = tomllib.load(f)
        assert data.get("sync", {}).get("opt_in_dismissed") is True


def test_post_sync_enable_rejects_invalid_action(configured_app) -> None:
    with TestClient(configured_app) as client:
        client.get("/?token=T", follow_redirects=False)
        csrf = client.cookies.get("ffc_csrf", "") or ""
        r = client.post(
            "/sync/enable",
            data={"_csrf": csrf, "action": "bogus"},
            follow_redirects=False,
        )
        assert r.status_code == 422
