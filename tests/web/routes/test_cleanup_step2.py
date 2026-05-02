"""Tests for cleanup wizard Step 2 — Review (table + selection + side panel)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from selectolax.parser import HTMLParser

from firefliesclearer.application.scan_service import ScanFilters
from firefliesclearer.core.models import Meeting
from firefliesclearer.web.wizard_session import (
    WizardState,
    add_to_selection,
    filters_to_dict,
    set_state,
)
from tests.fakes.in_memory_repository import InMemoryMeetingRepository

NOW = datetime(2026, 4, 29, 12, 0, tzinfo=UTC)


@pytest.fixture
def configured_client(configured_app) -> TestClient:
    c = TestClient(configured_app)
    c.get("/?token=T")
    return c


def _seed_repo_with_meetings(app, count: int) -> list[Meeting]:
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


def _sid_from_client(c: TestClient) -> str:
    return c.cookies.get("ffc_session", "") or ""


def _set_filters_in_session(app, sid: str, filters: ScanFilters) -> None:
    state = WizardState(
        step="review",
        filters=filters_to_dict(filters),
        selected_ids=[],
        operation_id=None,
    )
    set_state(app.state.session_store, sid, state)


def _csrf(c: TestClient) -> str:
    return c.cookies.get("ffc_csrf", "") or ""


# ---------------------------------------------------------------------------
# GET /cleanup/review
# ---------------------------------------------------------------------------


def test_get_review_redirects_to_step1_when_no_filters(
    configured_client: TestClient,
) -> None:
    r = configured_client.get("/cleanup/review", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/cleanup"


def test_get_review_renders_table_with_matches(
    configured_client: TestClient, configured_app
) -> None:
    _seed_repo_with_meetings(configured_app, 5)
    _set_filters_in_session(
        configured_app,
        _sid_from_client(configured_client),
        ScanFilters(older_than_days=30),
    )
    r = configured_client.get("/cleanup/review")
    assert r.status_code == 200
    doc = HTMLParser(r.text)
    rows = doc.css(".row[data-meeting-id]")
    assert len(rows) == 5
    # No "Select all N matches" banner when total <= 100.
    assert doc.css_first(".banner-select-all") is None
    # Stepper says step 2 is active.
    active = doc.css_first("nav.wizard-stepper li.step.active[data-step='review']")
    assert active is not None
    # Continue button is rendered.
    assert doc.css_first(".continue-btn") is not None


def test_get_review_paginates_at_100(configured_client: TestClient, configured_app) -> None:
    _seed_repo_with_meetings(configured_app, 250)
    _set_filters_in_session(
        configured_app,
        _sid_from_client(configured_client),
        ScanFilters(older_than_days=30),
    )
    r = configured_client.get("/cleanup/review")
    doc = HTMLParser(r.text)
    rows = doc.css(".row[data-meeting-id]")
    assert len(rows) == 100
    banner = doc.css_first(".banner-select-all")
    assert banner is not None
    assert "250" in banner.text()
    nav = doc.css_first(".pagination")
    assert nav is not None


def test_get_review_page_2_returns_next_slice(
    configured_client: TestClient, configured_app
) -> None:
    _seed_repo_with_meetings(configured_app, 250)
    _set_filters_in_session(
        configured_app,
        _sid_from_client(configured_client),
        ScanFilters(older_than_days=30),
    )
    r = configured_client.get("/cleanup/review?page=2")
    assert r.status_code == 200
    doc = HTMLParser(r.text)
    rows = doc.css(".row[data-meeting-id]")
    assert len(rows) == 100


def test_htmx_header_returns_table_only(configured_client: TestClient, configured_app) -> None:
    _seed_repo_with_meetings(configured_app, 5)
    _set_filters_in_session(
        configured_app,
        _sid_from_client(configured_client),
        ScanFilters(older_than_days=30),
    )
    r = configured_client.get("/cleanup/review", headers={"HX-Request": "true"})
    assert r.status_code == 200
    # No <html> root, just the table fragment.
    assert "<html" not in r.text.lower()
    doc = HTMLParser(r.text)
    assert doc.css_first(".review-table") is not None


# ---------------------------------------------------------------------------
# POST /cleanup/review/toggle/{meeting_id}
# ---------------------------------------------------------------------------


def test_toggle_adds_and_removes_from_selection(
    configured_client: TestClient, configured_app
) -> None:
    _seed_repo_with_meetings(configured_app, 5)
    sid = _sid_from_client(configured_client)
    _set_filters_in_session(configured_app, sid, ScanFilters(older_than_days=30))

    r = configured_client.post(
        "/cleanup/review/toggle/m1",
        data={"_csrf": _csrf(configured_client)},
    )
    assert r.status_code == 200
    selected = set(configured_app.state.session_store.get(sid)["wizard"]["selected_ids"])
    assert "m1" in selected

    r = configured_client.post(
        "/cleanup/review/toggle/m1",
        data={"_csrf": _csrf(configured_client)},
    )
    assert r.status_code == 200
    selected = set(configured_app.state.session_store.get(sid)["wizard"]["selected_ids"])
    assert "m1" not in selected


# ---------------------------------------------------------------------------
# POST /cleanup/review/select-all
# ---------------------------------------------------------------------------


def test_select_all_page_adds_current_page_ids(
    configured_client: TestClient, configured_app
) -> None:
    _seed_repo_with_meetings(configured_app, 250)
    sid = _sid_from_client(configured_client)
    _set_filters_in_session(configured_app, sid, ScanFilters(older_than_days=30))
    r = configured_client.post(
        "/cleanup/review/select-all",
        data={"_csrf": _csrf(configured_client), "page": "1"},
    )
    assert r.status_code == 200
    selected = set(configured_app.state.session_store.get(sid)["wizard"]["selected_ids"])
    assert len(selected) == 100  # only page 1


def test_select_all_with_all_flag_adds_every_match(
    configured_client: TestClient, configured_app
) -> None:
    _seed_repo_with_meetings(configured_app, 250)
    sid = _sid_from_client(configured_client)
    _set_filters_in_session(configured_app, sid, ScanFilters(older_than_days=30))
    r = configured_client.post(
        "/cleanup/review/select-all",
        data={"_csrf": _csrf(configured_client), "all": "true"},
    )
    assert r.status_code == 200
    selected = set(configured_app.state.session_store.get(sid)["wizard"]["selected_ids"])
    assert len(selected) == 250


def test_select_all_response_includes_oob_toolbar(
    configured_client: TestClient, configured_app
) -> None:
    """Selection-mutating POSTs return both the table fragment AND an OOB toolbar.

    Regression for an issue where ``_render_table_fragment`` rendered only the
    table, so the toolbar's ``hx-swap-oob`` directive never fired and the
    selected-count counter went stale after toggle / select-all / etc.
    """
    _seed_repo_with_meetings(configured_app, 5)
    sid = _sid_from_client(configured_client)
    _set_filters_in_session(configured_app, sid, ScanFilters(older_than_days=30))
    r = configured_client.post(
        "/cleanup/review/select-all",
        data={"_csrf": _csrf(configured_client), "page": "1"},
    )
    assert r.status_code == 200
    # Toolbar partial is included as an OOB swap target.
    assert 'id="review-toolbar"' in r.text
    assert 'hx-swap-oob="true"' in r.text
    # Counter reflects the post-mutation selection (5 of 5).
    assert "<strong>5</strong> of <strong>5</strong> selected" in r.text


# ---------------------------------------------------------------------------
# POST /cleanup/review/deselect-all
# ---------------------------------------------------------------------------


def test_deselect_all_page_removes_current_page_ids(
    configured_client: TestClient, configured_app
) -> None:
    meetings = _seed_repo_with_meetings(configured_app, 250)
    sid = _sid_from_client(configured_client)
    _set_filters_in_session(configured_app, sid, ScanFilters(older_than_days=30))
    # Pre-select all 250 ids.
    add_to_selection(
        configured_app.state.session_store,
        sid,
        [m.meeting_id for m in meetings],
    )

    r = configured_client.post(
        "/cleanup/review/deselect-all",
        data={"_csrf": _csrf(configured_client), "page": "1"},
    )
    assert r.status_code == 200
    selected = set(configured_app.state.session_store.get(sid)["wizard"]["selected_ids"])
    # Page 1 (100 ids) removed; pages 2-3 (150 ids) remain.
    assert len(selected) == 150


def test_deselect_all_with_all_flag_clears_selection(
    configured_client: TestClient, configured_app
) -> None:
    meetings = _seed_repo_with_meetings(configured_app, 250)
    sid = _sid_from_client(configured_client)
    _set_filters_in_session(configured_app, sid, ScanFilters(older_than_days=30))
    add_to_selection(
        configured_app.state.session_store,
        sid,
        [m.meeting_id for m in meetings],
    )

    r = configured_client.post(
        "/cleanup/review/deselect-all",
        data={"_csrf": _csrf(configured_client), "all": "true"},
    )
    assert r.status_code == 200
    selected = configured_app.state.session_store.get(sid)["wizard"]["selected_ids"]
    assert selected == []


# ---------------------------------------------------------------------------
# POST /cleanup/review/invert
# ---------------------------------------------------------------------------


def test_invert_flips_current_page_only(configured_client: TestClient, configured_app) -> None:
    meetings = _seed_repo_with_meetings(configured_app, 250)
    sid = _sid_from_client(configured_client)
    _set_filters_in_session(configured_app, sid, ScanFilters(older_than_days=30))
    # Pre-select the first 50 ids (subset of page 1) plus one off-page id.
    add_to_selection(
        configured_app.state.session_store,
        sid,
        [m.meeting_id for m in meetings[:50]] + [meetings[200].meeting_id],
    )

    r = configured_client.post(
        "/cleanup/review/invert",
        data={"_csrf": _csrf(configured_client), "page": "1"},
    )
    assert r.status_code == 200
    selected = set(configured_app.state.session_store.get(sid)["wizard"]["selected_ids"])
    # Page 1 had 100 rows; first 50 were selected. Inverting flips them all,
    # so the 50 that were selected become unselected, and the 50 unselected
    # become selected. Off-page id (m200) is untouched.
    page1_ids = {m.meeting_id for m in meetings[:100]}
    assert selected == (page1_ids - {m.meeting_id for m in meetings[:50]}) | {
        meetings[200].meeting_id
    }


# ---------------------------------------------------------------------------
# GET /cleanup/meeting/{id}/panel
# ---------------------------------------------------------------------------


def test_side_panel_renders_meeting_details(configured_client: TestClient, configured_app) -> None:
    _seed_repo_with_meetings(configured_app, 3)
    sid = _sid_from_client(configured_client)
    _set_filters_in_session(configured_app, sid, ScanFilters(older_than_days=30))
    r = configured_client.get("/cleanup/meeting/m1/panel")
    assert r.status_code == 200
    assert "Meeting 1" in r.text


def test_side_panel_404_when_meeting_not_in_matches(
    configured_client: TestClient, configured_app
) -> None:
    _seed_repo_with_meetings(configured_app, 3)
    sid = _sid_from_client(configured_client)
    _set_filters_in_session(configured_app, sid, ScanFilters(older_than_days=30))
    r = configured_client.get("/cleanup/meeting/missing/panel")
    assert r.status_code == 404


def test_side_panel_404_when_no_filters_in_session(
    configured_client: TestClient, configured_app
) -> None:
    _seed_repo_with_meetings(configured_app, 3)
    # Don't set filters in session.
    r = configured_client.get("/cleanup/meeting/m1/panel")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# POST /cleanup/review (Continue)
# ---------------------------------------------------------------------------


def test_post_review_with_empty_selection_renders_error(
    configured_client: TestClient, configured_app
) -> None:
    _seed_repo_with_meetings(configured_app, 5)
    sid = _sid_from_client(configured_client)
    _set_filters_in_session(configured_app, sid, ScanFilters(older_than_days=30))
    r = configured_client.post(
        "/cleanup/review",
        data={"_csrf": _csrf(configured_client)},
        follow_redirects=False,
    )
    assert r.status_code == 422
    assert "select at least one" in r.text.lower()


def test_post_review_empty_selection_preserves_current_page(
    configured_client: TestClient, configured_app
) -> None:
    """422 re-render keeps the user on their current page (was hard-coded to 1)."""
    _seed_repo_with_meetings(configured_app, 250)
    sid = _sid_from_client(configured_client)
    _set_filters_in_session(configured_app, sid, ScanFilters(older_than_days=30))
    r = configured_client.post(
        "/cleanup/review",
        data={"_csrf": _csrf(configured_client), "page": "2"},
        follow_redirects=False,
    )
    assert r.status_code == 422
    doc = HTMLParser(r.text)
    # Page 2 = rows 100..199, so first row is m100 not m0.
    rows = doc.css(".row[data-meeting-id]")
    assert len(rows) == 100
    assert rows[0].attributes.get("data-meeting-id") == "m100"
    # Pagination indicator confirms page 2.
    indicator = doc.css_first(".page-indicator")
    assert indicator is not None
    assert "Page 2" in indicator.text()


def test_post_review_with_selection_redirects_to_archive(
    configured_client: TestClient, configured_app
) -> None:
    _seed_repo_with_meetings(configured_app, 5)
    sid = _sid_from_client(configured_client)
    _set_filters_in_session(configured_app, sid, ScanFilters(older_than_days=30))
    add_to_selection(configured_app.state.session_store, sid, ["m1", "m2"])
    r = configured_client.post(
        "/cleanup/review",
        data={"_csrf": _csrf(configured_client)},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/cleanup/archive"
    state = configured_app.state.session_store.get(sid)["wizard"]
    assert state["step"] == "archive"


# ---------------------------------------------------------------------------
# B1 — Toggle checkbox HTMX wiring regression
# ---------------------------------------------------------------------------


def test_toggle_form_uses_plain_change_trigger(
    configured_client: TestClient, configured_app
) -> None:
    """Regression for B1: ``hx-trigger="change from:find input"`` resolved to
    the FIRST descendant input, which is the hidden ``_csrf`` input. Hidden
    inputs don't bubble change events when the visible checkbox flips, so the
    trigger never fired and the toolbar count never updated. The fix uses a
    plain ``change`` trigger so the form catches the bubbling change event
    from the checkbox.
    """
    _seed_repo_with_meetings(configured_app, 3)
    _set_filters_in_session(
        configured_app,
        _sid_from_client(configured_client),
        ScanFilters(older_than_days=30),
    )
    r = configured_client.get("/cleanup/review")
    assert r.status_code == 200
    doc = HTMLParser(r.text)
    rows = doc.css(".row[data-meeting-id]")
    assert rows
    form = rows[0].css_first("td.col-check form")
    assert form is not None
    trigger = form.attributes.get("hx-trigger")
    assert trigger == "change", (
        f"hx-trigger should be 'change' (so the bubbling checkbox event fires "
        f"the form post), got {trigger!r}"
    )


# ---------------------------------------------------------------------------
# B2 — Bulk-action buttons must include form fields when posting via HTMX
# ---------------------------------------------------------------------------


def test_toolbar_buttons_use_closest_form_include(
    configured_client: TestClient, configured_app
) -> None:
    """Regression for B2: ``hx-include="this"`` on the form is inherited by
    buttons but resolved relative to the *requesting element* (the button), so
    button posts dropped ``_csrf`` and ``page`` fields and silently 403'd.
    Each button now declares ``hx-include="closest form"``.
    """
    _seed_repo_with_meetings(configured_app, 3)
    _set_filters_in_session(
        configured_app,
        _sid_from_client(configured_client),
        ScanFilters(older_than_days=30),
    )
    r = configured_client.get("/cleanup/review")
    assert r.status_code == 200
    doc = HTMLParser(r.text)
    toolbar_form = doc.css_first(".review-toolbar form.toolbar-actions")
    assert toolbar_form is not None
    # Form-level hx-include must NOT be "this" (it would resolve to the button).
    assert toolbar_form.attributes.get("hx-include") != "this"
    # Each bulk-action button must declare hx-include="closest form".
    for selector in (
        "button.btn-select-all",
        "button.btn-deselect-all",
        "button.btn-invert",
    ):
        btn = toolbar_form.css_first(selector)
        assert btn is not None, f"missing button {selector}"
        assert btn.attributes.get("hx-include") == "closest form", (
            f"{selector} must use hx-include='closest form'; "
            f"got {btn.attributes.get('hx-include')!r}"
        )


def test_select_all_posts_via_button_form_data(
    configured_client: TestClient, configured_app
) -> None:
    """Live integration: a submission containing the form fields the button's
    ``hx-include="closest form"`` would carry must pass CSRF and update the
    selection. This is the behavior the broken version silently failed to do.
    """
    _seed_repo_with_meetings(configured_app, 5)
    sid = _sid_from_client(configured_client)
    _set_filters_in_session(configured_app, sid, ScanFilters(older_than_days=30))
    r = configured_client.post(
        "/cleanup/review/select-all",
        data={"_csrf": _csrf(configured_client), "page": "1"},
    )
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# B3 — Spinner indicator for in-flight bulk operations
# ---------------------------------------------------------------------------


def test_toolbar_includes_htmx_indicator(configured_client: TestClient, configured_app) -> None:
    """Regression for B3: bulk-action buttons must reference an
    ``hx-indicator`` so users see progress while the slow ``_scan_or_none``
    call is in flight.
    """
    _seed_repo_with_meetings(configured_app, 3)
    _set_filters_in_session(
        configured_app,
        _sid_from_client(configured_client),
        ScanFilters(older_than_days=30),
    )
    r = configured_client.get("/cleanup/review")
    assert r.status_code == 200
    doc = HTMLParser(r.text)
    spinner = doc.css_first(".review-toolbar-spinner")
    assert spinner is not None, "expected an .review-toolbar-spinner element"
    assert "htmx-indicator" in (spinner.attributes.get("class") or "")
    for selector in (
        "button.btn-select-all",
        "button.btn-deselect-all",
        "button.btn-invert",
    ):
        btn = doc.css_first(f".review-toolbar form.toolbar-actions {selector}")
        assert btn is not None
        assert btn.attributes.get("hx-indicator") == ".review-toolbar-spinner"


# ---------------------------------------------------------------------------
# B4 — Sortable columns
# ---------------------------------------------------------------------------


def _seed_repo_with_varied_meetings(app) -> list[Meeting]:
    """Three meetings whose date / duration / title each yield distinct orderings."""
    meetings = [
        Meeting(
            meeting_id="ma",
            title="Charlie",
            meeting_date=datetime(2026, 1, 10, tzinfo=UTC),
            duration_minutes=60.0,
            host_email="h@x",
            participant_count=3,
        ),
        Meeting(
            meeting_id="mb",
            title="Alpha",
            meeting_date=datetime(2026, 3, 1, tzinfo=UTC),
            duration_minutes=15.0,
            host_email="h@x",
            participant_count=3,
        ),
        Meeting(
            meeting_id="mc",
            title="Bravo",
            meeting_date=datetime(2026, 2, 5, tzinfo=UTC),
            duration_minutes=45.0,
            host_email="h@x",
            participant_count=3,
        ),
    ]
    repo = InMemoryMeetingRepository(meetings=meetings, api_key="ff_test")
    repo.set_user_email_for_key("ff_test", "oskar@example.com")
    app.state.deps.client = repo
    return meetings


@pytest.mark.parametrize(
    ("sort", "direction", "expected"),
    [
        # Date defaults to descending (newest first).
        ("date", "desc", ["mb", "mc", "ma"]),
        ("date", "asc", ["ma", "mc", "mb"]),
        ("duration", "desc", ["ma", "mc", "mb"]),
        ("duration", "asc", ["mb", "mc", "ma"]),
        ("title", "asc", ["mb", "mc", "ma"]),  # Alpha, Bravo, Charlie
        ("title", "desc", ["ma", "mc", "mb"]),
    ],
)
def test_sort_matches_orders_by_axis(sort: str, direction: str, expected: list[str]) -> None:
    """Table-driven coverage for the ``_sort_matches`` helper.

    Each axis (date / duration / title) is exercised in both directions.
    """
    from firefliesclearer.application.scan_service import ScanMatch
    from firefliesclearer.web.routes.cleanup import _sort_matches

    base = [
        Meeting(
            meeting_id="ma",
            title="Charlie",
            meeting_date=datetime(2026, 1, 10, tzinfo=UTC),
            duration_minutes=60.0,
            host_email="h@x",
            participant_count=3,
        ),
        Meeting(
            meeting_id="mb",
            title="Alpha",
            meeting_date=datetime(2026, 3, 1, tzinfo=UTC),
            duration_minutes=15.0,
            host_email="h@x",
            participant_count=3,
        ),
        Meeting(
            meeting_id="mc",
            title="Bravo",
            meeting_date=datetime(2026, 2, 5, tzinfo=UTC),
            duration_minutes=45.0,
            host_email="h@x",
            participant_count=3,
        ),
    ]
    matches = tuple(ScanMatch(meeting=m, matched_rules=()) for m in base)
    out = _sort_matches(matches, sort=sort, direction=direction)
    assert [m.meeting.meeting_id for m in out] == expected


def test_sort_matches_unknown_axis_falls_back_to_date_desc() -> None:
    """Unknown sort/direction values must not blow up — fall back to date desc."""
    from firefliesclearer.application.scan_service import ScanMatch
    from firefliesclearer.web.routes.cleanup import _sort_matches

    meetings = [
        Meeting(
            meeting_id="m1",
            title="X",
            meeting_date=datetime(2026, 1, 1, tzinfo=UTC),
            duration_minutes=1.0,
            host_email="h@x",
            participant_count=1,
        ),
        Meeting(
            meeting_id="m2",
            title="Y",
            meeting_date=datetime(2026, 2, 1, tzinfo=UTC),
            duration_minutes=2.0,
            host_email="h@x",
            participant_count=1,
        ),
    ]
    matches = tuple(ScanMatch(meeting=m, matched_rules=()) for m in meetings)
    out = _sort_matches(matches, sort="bogus", direction="bogus")
    # Default = date desc => newest first.
    assert [m.meeting.meeting_id for m in out] == ["m2", "m1"]


def test_get_review_renders_sort_links(configured_client: TestClient, configured_app) -> None:
    """The review table headers must be HTMX sort links pointing at the route."""
    _seed_repo_with_varied_meetings(configured_app)
    _set_filters_in_session(
        configured_app,
        _sid_from_client(configured_client),
        ScanFilters(older_than_days=30),
    )
    r = configured_client.get("/cleanup/review")
    assert r.status_code == 200
    doc = HTMLParser(r.text)
    # Date column has a sort link.
    th_date = doc.css_first("th.col-date a")
    assert th_date is not None
    href = th_date.attributes.get("href") or ""
    assert "sort=date" in href


def test_get_review_applies_sort_query_param(configured_client: TestClient, configured_app) -> None:
    """``?sort=title&dir=asc`` reorders the rows by title ascending."""
    _seed_repo_with_varied_meetings(configured_app)
    _set_filters_in_session(
        configured_app,
        _sid_from_client(configured_client),
        ScanFilters(older_than_days=30),
    )
    r = configured_client.get("/cleanup/review?sort=title&dir=asc")
    assert r.status_code == 200
    doc = HTMLParser(r.text)
    rows = doc.css(".row[data-meeting-id]")
    ids = [row.attributes.get("data-meeting-id") for row in rows]
    # Alpha (mb), Bravo (mc), Charlie (ma)
    assert ids == ["mb", "mc", "ma"]


def test_get_review_default_sort_is_date_desc(
    configured_client: TestClient, configured_app
) -> None:
    """No sort query param => newest first (preserves prior behavior)."""
    _seed_repo_with_varied_meetings(configured_app)
    _set_filters_in_session(
        configured_app,
        _sid_from_client(configured_client),
        ScanFilters(older_than_days=30),
    )
    r = configured_client.get("/cleanup/review")
    assert r.status_code == 200
    doc = HTMLParser(r.text)
    rows = doc.css(".row[data-meeting-id]")
    ids = [row.attributes.get("data-meeting-id") for row in rows]
    # mb (March 1), mc (Feb 5), ma (Jan 10) — newest first
    assert ids == ["mb", "mc", "ma"]


def test_sort_persisted_in_toolbar_form(configured_client: TestClient, configured_app) -> None:
    """The toolbar form must include ``sort`` / ``dir`` as hidden inputs so
    bulk-action POSTs preserve the sort across selection mutations.
    """
    _seed_repo_with_varied_meetings(configured_app)
    _set_filters_in_session(
        configured_app,
        _sid_from_client(configured_client),
        ScanFilters(older_than_days=30),
    )
    r = configured_client.get("/cleanup/review?sort=title&dir=asc")
    assert r.status_code == 200
    doc = HTMLParser(r.text)
    form = doc.css_first(".review-toolbar form.toolbar-actions")
    assert form is not None
    sort_input = form.css_first('input[type="hidden"][name="sort"]')
    dir_input = form.css_first('input[type="hidden"][name="dir"]')
    assert sort_input is not None and sort_input.attributes.get("value") == "title"
    assert dir_input is not None and dir_input.attributes.get("value") == "asc"


def test_select_all_preserves_sort_in_table_render(
    configured_client: TestClient, configured_app
) -> None:
    """Bulk-action POST that carries ``sort``/``dir`` must reorder its
    re-rendered table fragment to match.
    """
    _seed_repo_with_varied_meetings(configured_app)
    sid = _sid_from_client(configured_client)
    _set_filters_in_session(configured_app, sid, ScanFilters(older_than_days=30))
    r = configured_client.post(
        "/cleanup/review/select-all",
        data={
            "_csrf": _csrf(configured_client),
            "page": "1",
            "sort": "title",
            "dir": "asc",
        },
    )
    assert r.status_code == 200
    doc = HTMLParser(r.text)
    rows = doc.css(".row[data-meeting-id]")
    ids = [row.attributes.get("data-meeting-id") for row in rows]
    assert ids == ["mb", "mc", "ma"]
