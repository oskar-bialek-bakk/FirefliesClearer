"""Tests for the Dashboard route + sidebar status fragment."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from selectolax.parser import HTMLParser

from firefliesclearer.core.models import Meeting, MeetingState

NOW = datetime(2026, 4, 29, 12, 0, tzinfo=UTC)


def _seed_failed_meeting(manifest, meeting_id: str = "m1", title: str = "Test Standup") -> None:
    """Register a meeting and transition it to FAILED_DOWNLOAD with an error."""
    meeting = Meeting(
        meeting_id=meeting_id,
        title=title,
        meeting_date=NOW,
        duration_minutes=10.0,
        host_email="u@x.com",
        participant_count=2,
    )
    manifest.register(meeting, at=NOW)
    manifest.transition(
        meeting_id,
        to=MeetingState.FAILED_DOWNLOAD,
        at=NOW,
        last_error="Reset by peer",
    )


def _seed_meeting_in_state(
    manifest,
    *,
    meeting_id: str,
    state: MeetingState,
    title: str = "Test",
) -> None:
    """Seed a meeting and walk it through the FSM to *state*.

    Supports ARCHIVED and DELETED_FAILED — the two terminal-ish states the
    "Mark as deleted in Fireflies" action operates on. Other states are
    rejected to keep the helper small and intent-clear.
    """
    meeting = Meeting(
        meeting_id=meeting_id,
        title=title,
        meeting_date=NOW,
        duration_minutes=15.0,
        host_email="u@x.com",
        participant_count=2,
    )
    manifest.register(meeting, at=NOW)
    if state is MeetingState.PENDING:
        return
    manifest.transition(meeting_id, to=MeetingState.ARCHIVED, at=NOW)
    if state is MeetingState.ARCHIVED:
        return
    if state is MeetingState.DELETED_FAILED:
        manifest.transition(
            meeting_id,
            to=MeetingState.DELETED_FAILED,
            at=NOW,
            last_error="Too many requests",
        )
        return
    raise ValueError(f"_seed_meeting_in_state does not support {state}")


@pytest.fixture
def configured_client(configured_app) -> TestClient:
    """A client where setup is already complete (configured_app from conftest)."""
    c = TestClient(configured_app)
    c.get("/?token=T")
    return c


def test_dashboard_includes_sync_banner(configured_app_sync_on) -> None:
    """Dashboard renders the sync banner partial when sync is enabled."""
    with TestClient(configured_app_sync_on) as client:
        client.get("/?token=T", follow_redirects=False)
        r = client.get("/")
        assert r.status_code == 200
        assert "sync-banner" in r.text


def test_dashboard_shows_opt_in_banner_when_sync_disabled(configured_app) -> None:
    """Existing users with [sync] enabled = false see the opt-in banner."""
    with TestClient(configured_app) as client:
        client.get("/?token=T", follow_redirects=False)
        r = client.get("/")
        assert r.status_code == 200
        assert "Enable local cache" in r.text


def test_dashboard_hides_opt_in_banner_when_sync_enabled(configured_app_sync_on) -> None:
    """Once sync is enabled, the opt-in banner does not appear."""
    with TestClient(configured_app_sync_on) as client:
        client.get("/?token=T", follow_redirects=False)
        r = client.get("/")
        assert r.status_code == 200
        assert "Enable local cache" not in r.text


def test_dashboard_hides_opt_in_banner_after_dismiss(configured_app) -> None:
    """After dismissal, the banner stays hidden even though sync is disabled."""
    with TestClient(configured_app) as client:
        client.get("/?token=T", follow_redirects=False)
        csrf = client.cookies.get("ffc_csrf", "") or ""
        # Dismiss the banner
        client.post(
            "/sync/enable",
            data={"_csrf": csrf, "action": "dismiss"},
            follow_redirects=False,
        )
        # Drop cached deps so the next dashboard request reloads the config
        configured_app.state.deps = None
        r = client.get("/")
        assert r.status_code == 200
        assert "Enable local cache" not in r.text


def test_dashboard_renders_with_zero_counts(configured_client: TestClient) -> None:
    r = configured_client.get("/")
    assert r.status_code == 200
    doc = HTMLParser(r.text)
    cards = doc.css(".state-count-card")
    # Total + Archived + Pending + Failed + Deleted = 5 cards.
    assert len(cards) == 5
    # Empty manifest -> all zero
    for card in cards:
        assert "0" in card.text()


def test_dashboard_full_request_includes_sidebar_chrome(
    configured_client: TestClient,
) -> None:
    """Plain GET (no HX-Request header) returns the full HTML shell with sidebar."""
    r = configured_client.get("/")
    assert r.status_code == 200
    assert "<!doctype html>" in r.text.lower()
    assert '<aside class="sidebar">' in r.text
    assert 'id="page"' in r.text


def test_dashboard_htmx_request_returns_partial_without_sidebar(
    configured_client: TestClient,
) -> None:
    """HX-Request: true → partial render, no <html>/<aside class=sidebar> chrome.

    Regression for the duplicated-sidebar bug: clicking a sidebar nav link
    fires an hx-get with hx-target="#page". If the response includes a full
    HTML shell (sidebar + main + #page), HTMX swaps that whole tree into
    the existing #page, nesting a duplicate sidebar inside the content area.
    """
    r = configured_client.get("/", headers={"HX-Request": "true"})
    assert r.status_code == 200
    text = r.text
    # Inner content still rendered.
    assert ".state-count-card" in text or "state-count-card" in text
    # But the outer chrome is NOT included.
    assert "<!doctype" not in text.lower()
    assert '<aside class="sidebar">' not in text
    assert "<html " not in text and "<html>" not in text


def test_dashboard_shows_failed_count_when_failures_exist(
    configured_client: TestClient, configured_app
) -> None:
    manifest = configured_app.state.deps.manifest
    _seed_failed_meeting(manifest)

    r = configured_client.get("/")
    doc = HTMLParser(r.text)
    failed_card = doc.css_first("[data-state='failed']")
    assert failed_card is not None
    assert "1" in failed_card.text()


def test_needs_attention_lists_failed_meetings(
    configured_client: TestClient, configured_app
) -> None:
    manifest = configured_app.state.deps.manifest
    _seed_failed_meeting(manifest, meeting_id="m1", title="Test Standup")

    r = configured_client.get("/")
    doc = HTMLParser(r.text)
    rows = doc.css(".needs-attention-row")
    assert len(rows) == 1
    assert "Test Standup" in rows[0].text()
    assert "Reset by peer" in rows[0].text()


def test_dashboard_empty_state_when_no_failures(
    configured_client: TestClient,
) -> None:
    r = configured_client.get("/")
    doc = HTMLParser(r.text)
    empty = doc.css_first(".needs-attention-empty")
    assert empty is not None
    assert "All clear" in empty.text()


def test_sidebar_status_renders(configured_client: TestClient) -> None:
    r = configured_client.get("/sidebar/status")
    assert r.status_code == 200
    doc = HTMLParser(r.text)
    fragment = doc.css_first(".sidebar-status")
    assert fragment is not None


def test_dashboard_includes_total_cached_card(
    configured_client: TestClient, configured_app
) -> None:
    """The full dashboard renders the new "Total cached" card so the user has
    a single number for everything in the manifest after a sync (the bulk of
    which is in KNOWN state, invisible in the action-oriented cards)."""
    manifest = configured_app.state.deps.manifest
    # Two synced rows + one failed row = 3 total.
    for mid in ("k1", "k2"):
        manifest.upsert_known(
            Meeting(
                meeting_id=mid,
                title="Synced",
                meeting_date=NOW,
                duration_minutes=10.0,
                host_email="u@x.com",
                participant_count=1,
            ),
            at=NOW,
        )
    _seed_failed_meeting(manifest, meeting_id="f1")

    r = configured_client.get("/")
    assert r.status_code == 200
    doc = HTMLParser(r.text)
    total_card = doc.css_first('[data-state="total"]')
    assert total_card is not None, "Total card missing from dashboard"
    value = total_card.css_first(".state-count-card__value")
    assert value is not None
    assert value.text().strip() == "3"


def test_dashboard_state_counts_endpoint_returns_polling_fragment(
    configured_client: TestClient, configured_app
) -> None:
    """``GET /dashboard/state-counts`` returns the cards section as a standalone
    fragment with the self-polling attributes intact, so HTMX keeps refreshing
    the counters as the sync scheduler adds rows in the background."""
    manifest = configured_app.state.deps.manifest
    manifest.upsert_known(
        Meeting(
            meeting_id="k1",
            title="m",
            meeting_date=NOW,
            duration_minutes=1.0,
            host_email="u@x.com",
            participant_count=0,
        ),
        at=NOW,
    )
    r = configured_client.get("/dashboard/state-counts")
    assert r.status_code == 200
    # Polling attributes survive the round-trip — without them, the swap
    # would replace the section with a static copy and stop polling.
    assert 'hx-trigger="every 10s"' in r.text
    assert 'hx-get="/dashboard/state-counts"' in r.text
    assert 'data-state="total"' in r.text
    # Reflects the seeded row.
    doc = HTMLParser(r.text)
    total = doc.css_first('[data-state="total"] .state-count-card__value')
    assert total is not None and total.text().strip() == "1"


def test_state_count_cards_link_to_filtered_history(
    configured_client: TestClient,
) -> None:
    """Each card is an anchor into /history pre-filtered by the matching state.

    Locks down C5 from the PR #20 review: a typo in any of these hrefs would
    otherwise ship unnoticed because the existing tests only check counts."""
    r = configured_client.get("/")
    assert r.status_code == 200
    doc = HTMLParser(r.text)

    expected: dict[str, str] = {
        "total": "/history?range=all-time",
        "archived": "/history?range=all-time&state=archived",
        "pending": "/history?range=all-time&state=pending",
        "deleted": "/history?range=all-time&state=deleted",
    }
    for state, href in expected.items():
        card = doc.css_first(f'a.state-count-card[data-state="{state}"]')
        assert card is not None, f"missing {state} card"
        assert card.attributes.get("href") == href, (
            f"{state} card href = {card.attributes.get('href')!r} (expected {href!r})"
        )

    failed_card = doc.css_first('a.state-count-card[data-state="failed"]')
    assert failed_card is not None
    failed_href = failed_card.attributes.get("href") or ""
    # Failed link uses the canonical FAILED_STATES tuple from audit_service.
    # Each state must appear as its own state= param so /history's
    # multi-select filter accepts them all.
    for s in (
        "failed_fetch",
        "failed_download",
        "failed_render",
        "failed_verify",
        "deleted_failed",
    ):
        assert f"state={s}" in failed_href, f"failed card href missing state={s}: {failed_href!r}"


def test_retry_all_button_hidden_when_only_gone_from_source_rows_remain(
    configured_client: TestClient, configured_app
) -> None:
    """The Retry-all button must not render when every needs-attention row
    is flagged gone-from-source — the runner would just 409 and the user
    is left with an actionable button whose only outcome is an alert.

    Locks down C9 from the PR #20 review."""
    manifest = configured_app.state.deps.manifest
    _seed_failed_meeting(manifest, meeting_id="m-gone-1")
    _seed_failed_meeting(manifest, meeting_id="m-gone-2")
    manifest.set_source_state("m-gone-1", "gone")
    manifest.set_source_state("m-gone-2", "gone")

    r = configured_client.get("/")
    assert r.status_code == 200
    doc = HTMLParser(r.text)
    # Section still renders the rows (so the user sees the failure history),
    # but the bulk action is hidden.
    rows = doc.css(".needs-attention-row")
    assert len(rows) == 2
    assert doc.css_first("form.needs-attention__retry-all") is None


def test_retry_all_button_count_excludes_gone_from_source_rows(
    configured_client: TestClient, configured_app
) -> None:
    """Mixed case — the button shows, but its (N) reflects only the rows
    that the runner will actually attempt."""
    manifest = configured_app.state.deps.manifest
    _seed_failed_meeting(manifest, meeting_id="m-live")
    _seed_failed_meeting(manifest, meeting_id="m-gone")
    manifest.set_source_state("m-gone", "gone")

    r = configured_client.get("/")
    doc = HTMLParser(r.text)
    button = doc.css_first("button.retry-btn--all")
    assert button is not None
    # Live row counts; gone-from-source row does not.
    assert "(1)" in button.text(strip=True)


# ---------------------------------------------------------------------------
# Mark-as-deleted-in-Fireflies action (Phase 1)
#
# Two new POST routes let the user reconcile the manifest after they bulk-
# delete meetings in the Fireflies web UI (which the API quota makes the
# cheapest path on Pro plan):
#   - POST /mark-deleted/{meeting_id}: single row, ARCHIVED|DELETED_FAILED -> DELETED.
#   - POST /mark-deleted/all: every row currently in those two states.
# Both record the transition with details={"reason": "manual_external_delete"}
# in state_log so the audit trail keeps the manual provenance.
# ---------------------------------------------------------------------------


def _csrf(client: TestClient) -> str:
    return client.cookies.get("ffc_csrf", "") or ""


def test_mark_deleted_single_archived_transitions_to_deleted(
    configured_client: TestClient, configured_app
) -> None:
    manifest = configured_app.state.deps.manifest
    _seed_meeting_in_state(manifest, meeting_id="a1", state=MeetingState.ARCHIVED)

    r = configured_client.post(
        "/mark-deleted/a1",
        data={"_csrf": _csrf(configured_client)},
    )
    assert r.status_code == 200, r.text
    rec = manifest.get("a1")
    assert rec is not None
    assert rec.state is MeetingState.DELETED


def test_mark_deleted_single_failed_transitions_to_deleted(
    configured_client: TestClient, configured_app
) -> None:
    manifest = configured_app.state.deps.manifest
    _seed_meeting_in_state(manifest, meeting_id="f1", state=MeetingState.DELETED_FAILED)

    r = configured_client.post(
        "/mark-deleted/f1",
        data={"_csrf": _csrf(configured_client)},
    )
    assert r.status_code == 200, r.text
    rec = manifest.get("f1")
    assert rec is not None
    assert rec.state is MeetingState.DELETED


def test_mark_deleted_single_records_reason_in_state_log(
    configured_client: TestClient, configured_app
) -> None:
    manifest = configured_app.state.deps.manifest
    _seed_meeting_in_state(manifest, meeting_id="a1", state=MeetingState.ARCHIVED)

    configured_client.post("/mark-deleted/a1", data={"_csrf": _csrf(configured_client)})

    log = manifest.state_log("a1")
    last = log[-1]
    assert last.from_state is MeetingState.ARCHIVED
    assert last.to_state is MeetingState.DELETED
    assert last.details == {"reason": "manual_external_delete"}


def test_mark_deleted_single_rejects_pending(configured_client: TestClient, configured_app) -> None:
    """A meeting that hasn't been archived locally must not be markable as
    externally-deleted — the manifest would lose its claim that we have a
    verified copy on disk."""
    manifest = configured_app.state.deps.manifest
    _seed_meeting_in_state(manifest, meeting_id="p1", state=MeetingState.PENDING)

    r = configured_client.post(
        "/mark-deleted/p1",
        data={"_csrf": _csrf(configured_client)},
    )
    assert r.status_code == 409
    rec = manifest.get("p1")
    assert rec is not None
    assert rec.state is MeetingState.PENDING


def test_mark_deleted_single_rejects_already_deleted(
    configured_client: TestClient, configured_app
) -> None:
    manifest = configured_app.state.deps.manifest
    _seed_meeting_in_state(manifest, meeting_id="a1", state=MeetingState.ARCHIVED)
    manifest.transition("a1", to=MeetingState.DELETED, at=NOW)

    r = configured_client.post(
        "/mark-deleted/a1",
        data={"_csrf": _csrf(configured_client)},
    )
    assert r.status_code == 409


def test_mark_deleted_single_unknown_id_returns_404(
    configured_client: TestClient,
) -> None:
    r = configured_client.post(
        "/mark-deleted/missing",
        data={"_csrf": _csrf(configured_client)},
    )
    assert r.status_code == 404


def test_mark_deleted_all_transitions_archived_and_failed_only(
    configured_client: TestClient, configured_app
) -> None:
    manifest = configured_app.state.deps.manifest
    _seed_meeting_in_state(manifest, meeting_id="a1", state=MeetingState.ARCHIVED)
    _seed_meeting_in_state(manifest, meeting_id="a2", state=MeetingState.ARCHIVED)
    _seed_meeting_in_state(manifest, meeting_id="f1", state=MeetingState.DELETED_FAILED)
    _seed_meeting_in_state(manifest, meeting_id="p1", state=MeetingState.PENDING)
    _seed_failed_meeting(manifest, meeting_id="dl1")  # FAILED_DOWNLOAD — not eligible

    r = configured_client.post(
        "/mark-deleted/all",
        data={"_csrf": _csrf(configured_client)},
    )
    assert r.status_code == 200, r.text

    expected = {
        "a1": MeetingState.DELETED,
        "a2": MeetingState.DELETED,
        "f1": MeetingState.DELETED,
        "p1": MeetingState.PENDING,
        "dl1": MeetingState.FAILED_DOWNLOAD,
    }
    for mid, state in expected.items():
        rec = manifest.get(mid)
        assert rec is not None and rec.state is state, f"{mid} = {rec and rec.state}"


def test_mark_deleted_all_returns_409_when_nothing_eligible(
    configured_client: TestClient, configured_app
) -> None:
    manifest = configured_app.state.deps.manifest
    _seed_failed_meeting(manifest, meeting_id="dl1")  # FAILED_DOWNLOAD — not eligible

    r = configured_client.post(
        "/mark-deleted/all",
        data={"_csrf": _csrf(configured_client)},
    )
    assert r.status_code == 409


def test_mark_deleted_all_writes_reason_to_state_log(
    configured_client: TestClient, configured_app
) -> None:
    manifest = configured_app.state.deps.manifest
    _seed_meeting_in_state(manifest, meeting_id="a1", state=MeetingState.ARCHIVED)

    configured_client.post("/mark-deleted/all", data={"_csrf": _csrf(configured_client)})

    last = manifest.state_log("a1")[-1]
    assert last.to_state is MeetingState.DELETED
    assert last.details == {"reason": "manual_external_delete"}


def test_dashboard_shows_awaiting_external_deletion_section(
    configured_client: TestClient, configured_app
) -> None:
    """The dashboard renders a dedicated 'awaiting external deletion' panel
    with a single bulk button when at least one meeting is in ARCHIVED or
    DELETED_FAILED. The button POSTs to /mark-deleted/all."""
    manifest = configured_app.state.deps.manifest
    _seed_meeting_in_state(manifest, meeting_id="a1", state=MeetingState.ARCHIVED)
    _seed_meeting_in_state(manifest, meeting_id="f1", state=MeetingState.DELETED_FAILED)

    r = configured_client.get("/")
    assert r.status_code == 200
    doc = HTMLParser(r.text)
    section = doc.css_first("section.awaiting-external-deletion")
    assert section is not None, "awaiting-external-deletion section missing"
    button = section.css_first("button.mark-deleted-btn--all")
    assert button is not None
    # Count reflects archived + deleted_failed total.
    assert "(2)" in button.text(strip=True)


def test_dashboard_hides_awaiting_external_deletion_when_empty(
    configured_client: TestClient,
) -> None:
    r = configured_client.get("/")
    doc = HTMLParser(r.text)
    assert doc.css_first("section.awaiting-external-deletion") is None


def test_needs_attention_row_has_per_row_mark_deleted_button_for_failed(
    configured_client: TestClient, configured_app
) -> None:
    """DELETED_FAILED rows show a secondary 'Mark deleted in Fireflies' action
    next to Retry — used after the user did the bulk-delete in FF web."""
    manifest = configured_app.state.deps.manifest
    _seed_meeting_in_state(manifest, meeting_id="f1", state=MeetingState.DELETED_FAILED)

    r = configured_client.get("/")
    doc = HTMLParser(r.text)
    row = doc.css_first('.needs-attention-row[data-meeting-id="f1"]')
    assert row is not None
    form = row.css_first("form.needs-attention-row__mark-deleted")
    assert form is not None
    assert form.attributes.get("hx-post") == "/mark-deleted/f1"


def test_needs_attention_row_buttons_are_split_left_and_right(
    configured_client: TestClient, configured_app
) -> None:
    """Mis-click safety: 'I deleted in Fireflies' must come BEFORE the row
    title in source order (so it renders at the left edge), and 'Retry'
    must come AFTER the title (rendered at the right edge). The two
    actions are different in consequence — Retry burns one API call,
    mark-deleted promotes the row to DELETED — so the layout must keep
    them physically separated rather than stacked together where a
    fast click could hit the wrong one."""
    manifest = configured_app.state.deps.manifest
    _seed_meeting_in_state(manifest, meeting_id="f1", state=MeetingState.DELETED_FAILED)

    r = configured_client.get("/")
    doc = HTMLParser(r.text)
    row = doc.css_first('.needs-attention-row[data-meeting-id="f1"]')
    assert row is not None

    # Walk direct children and capture the source order of (mark-deleted,
    # title, retry) — we only care about their relative ordering.
    children = list(row.iter())
    order: list[str] = []
    for child in children:
        cls = (child.attributes.get("class") or "").split()
        if "needs-attention-row__mark-deleted" in cls:
            order.append("mark-deleted")
        elif "needs-attention-row__title" in cls:
            order.append("title")
        elif "needs-attention-row__retry" in cls:
            order.append("retry")
    assert order == ["mark-deleted", "title", "retry"], (
        f"expected mark-deleted before title before retry, got {order}"
    )


def test_needs_attention_row_mark_deleted_uses_confirm_modal(
    configured_client: TestClient, configured_app
) -> None:
    """The per-row mark-deleted action goes through hx-confirm — second
    safety net beyond the left/right separation in case the user does
    misclick."""
    manifest = configured_app.state.deps.manifest
    _seed_meeting_in_state(manifest, meeting_id="f1", state=MeetingState.DELETED_FAILED)

    r = configured_client.get("/")
    doc = HTMLParser(r.text)
    form = doc.css_first(
        '.needs-attention-row[data-meeting-id="f1"] form.needs-attention-row__mark-deleted'
    )
    assert form is not None
    confirm = form.attributes.get("hx-confirm") or ""
    assert confirm  # non-empty
    # Mention Fireflies so the user understands this is the manifest
    # reconciliation action, not an API delete retry.
    assert "Fireflies" in confirm


def test_mark_deleted_all_returns_dashboard_partial_with_updated_state_counts(
    configured_client: TestClient, configured_app
) -> None:
    """After a successful bulk mark-deleted, the response is the refreshed
    state-counts fragment so HTMX can swap the dashboard cards in place
    (deleted counter goes up, archived/failed go down)."""
    manifest = configured_app.state.deps.manifest
    _seed_meeting_in_state(manifest, meeting_id="a1", state=MeetingState.ARCHIVED)
    _seed_meeting_in_state(manifest, meeting_id="f1", state=MeetingState.DELETED_FAILED)

    r = configured_client.post(
        "/mark-deleted/all",
        data={"_csrf": _csrf(configured_client)},
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 200
    # The bulk action returns the dashboard "main" fragment so the cards,
    # awaiting-external-deletion section, and needs-attention all refresh
    # together. Smoke-check that the deleted counter is now 2.
    doc = HTMLParser(r.text)
    deleted_card = doc.css_first('[data-state="deleted"] .state-count-card__value')
    assert deleted_card is not None
    assert deleted_card.text().strip() == "2"
