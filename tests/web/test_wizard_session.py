"""Unit tests for ``firefliesclearer.web.wizard_session``.

Covers the form-parsing logic and the ScanFilters dict round-trip used by
the cleanup wizard's persistent session state.
"""

from __future__ import annotations

from firefliesclearer.application.scan_service import ScanFilters
from firefliesclearer.web import wizard_session
from firefliesclearer.web.sessions import SessionStore

# ---------------------------------------------------------------------------
# parse_filter_form
# ---------------------------------------------------------------------------


def test_parse_filter_form_empty_returns_is_empty_filters():
    f = wizard_session.parse_filter_form({})
    assert isinstance(f, ScanFilters)
    assert f.is_empty()


def test_parse_filter_form_all_fields_populated():
    form = {
        "older_than_days": "30",
        "older_than_days_enabled": "on",
        "duration_below_minutes": "5.5",
        "duration_below_minutes_enabled": "on",
        "no_transcript": "on",
        "title_contains": "standup, review, retro",
        "title_regex": "^Daily.*",
        "host_email": "alice@example.com, bob@example.com",
        "participants_below": "2",
        "has_tag": "ad-hoc, internal",
    }
    f = wizard_session.parse_filter_form(form)
    assert f.older_than_days == 30
    assert f.duration_below_minutes == 5.5
    assert f.no_transcript is True
    assert f.title_contains == ("standup", "review", "retro")
    assert f.title_regex == "^Daily.*"
    assert f.host_email == ("alice@example.com", "bob@example.com")
    assert f.participants_below == 2
    assert f.has_tag == ("ad-hoc", "internal")
    assert not f.is_empty()


def test_parse_filter_form_strips_whitespace_and_drops_empty_csv_entries():
    form = {"title_contains": "  standup ,, review,  ,retro,"}
    f = wizard_session.parse_filter_form(form)
    assert f.title_contains == ("standup", "review", "retro")


def test_parse_filter_form_older_than_days_disabled_when_checkbox_missing():
    form = {"older_than_days": "30"}  # checkbox NOT present
    f = wizard_session.parse_filter_form(form)
    assert f.older_than_days is None


def test_parse_filter_form_duration_disabled_when_checkbox_missing():
    form = {"duration_below_minutes": "5"}  # checkbox NOT present
    f = wizard_session.parse_filter_form(form)
    assert f.duration_below_minutes is None


def test_parse_filter_form_blank_numeric_fields_are_none():
    form = {
        "older_than_days": "",
        "older_than_days_enabled": "on",
        "participants_below": "",
    }
    f = wizard_session.parse_filter_form(form)
    assert f.older_than_days is None
    assert f.participants_below is None


def test_parse_filter_form_no_transcript_checkbox_unchecked_is_false():
    f = wizard_session.parse_filter_form({})
    assert f.no_transcript is False


# ---------------------------------------------------------------------------
# filters_to_dict / filters_from_dict round trip
# ---------------------------------------------------------------------------


def test_filters_to_dict_lists_not_tuples():
    f = ScanFilters(
        older_than_days=30,
        title_contains=("a", "b"),
        host_email=("x@y.com",),
        has_tag=("tag1",),
    )
    d = wizard_session.filters_to_dict(f)
    assert isinstance(d["title_contains"], list)
    assert isinstance(d["host_email"], list)
    assert isinstance(d["has_tag"], list)
    assert d["older_than_days"] == 30


def test_filters_round_trip_preserves_values():
    f = ScanFilters(
        older_than_days=30,
        duration_below_minutes=5.5,
        no_transcript=True,
        title_contains=("standup", "review"),
        title_regex="^Daily.*",
        host_email=("a@b.com",),
        participants_below=2,
        has_tag=("internal",),
    )
    f2 = wizard_session.filters_from_dict(wizard_session.filters_to_dict(f))
    assert f2 == f


def test_filters_from_dict_empty_dict_is_empty_filters():
    f = wizard_session.filters_from_dict({})
    assert f.is_empty()


# ---------------------------------------------------------------------------
# SessionStore I/O
# ---------------------------------------------------------------------------


def test_get_state_returns_empty_when_absent():
    store = SessionStore()
    assert wizard_session.get_state(store, "sid-x") == {}


def test_set_state_preserves_other_session_keys():
    store = SessionStore()
    store.set("sid-x", {"api_key": "ff_test", "email": "a@b.com"})
    wizard_session.set_state(
        store,
        "sid-x",
        wizard_session.WizardState(
            step="review",
            filters={"older_than_days": 30},
            selected_ids=[],
            operation_id=None,
        ),
    )
    full = store.get("sid-x")
    assert full["api_key"] == "ff_test"
    assert full["email"] == "a@b.com"
    assert full["wizard"]["step"] == "review"
    assert full["wizard"]["filters"]["older_than_days"] == 30


# ---------------------------------------------------------------------------
# Selection helpers (Task 5.3)
# ---------------------------------------------------------------------------


def _seed_wizard(store: SessionStore, sid: str, *, selected: list[str] | None = None) -> None:
    wizard_session.set_state(
        store,
        sid,
        wizard_session.WizardState(
            step="review",
            filters={"older_than_days": 30},
            selected_ids=list(selected or []),
            operation_id=None,
        ),
    )


def test_get_selected_returns_set():
    store = SessionStore()
    _seed_wizard(store, "sid", selected=["a", "b", "c"])
    assert wizard_session.get_selected(store, "sid") == {"a", "b", "c"}


def test_get_selected_returns_empty_when_no_state():
    store = SessionStore()
    assert wizard_session.get_selected(store, "sid") == set()


def test_replace_selection_overwrites_entirely():
    store = SessionStore()
    _seed_wizard(store, "sid", selected=["a", "b"])
    wizard_session.replace_selection(store, "sid", ["x", "y", "z"])
    state = wizard_session.get_state(store, "sid")
    assert state["selected_ids"] == ["x", "y", "z"]


def test_replace_selection_preserves_other_wizard_fields():
    store = SessionStore()
    _seed_wizard(store, "sid", selected=["a"])
    wizard_session.replace_selection(store, "sid", ["x"])
    state = wizard_session.get_state(store, "sid")
    assert state["step"] == "review"
    assert state["filters"]["older_than_days"] == 30


def test_add_to_selection_is_idempotent():
    store = SessionStore()
    _seed_wizard(store, "sid", selected=["a"])
    wizard_session.add_to_selection(store, "sid", ["a", "b", "a"])
    state = wizard_session.get_state(store, "sid")
    # No duplicates, "a" appears once, "b" added once.
    assert state["selected_ids"] == ["a", "b"]


def test_add_to_selection_appends_in_order():
    store = SessionStore()
    _seed_wizard(store, "sid", selected=[])
    wizard_session.add_to_selection(store, "sid", ["c", "a", "b"])
    state = wizard_session.get_state(store, "sid")
    assert state["selected_ids"] == ["c", "a", "b"]


def test_remove_from_selection_strips_listed_ids():
    store = SessionStore()
    _seed_wizard(store, "sid", selected=["a", "b", "c"])
    wizard_session.remove_from_selection(store, "sid", ["a", "c"])
    state = wizard_session.get_state(store, "sid")
    assert state["selected_ids"] == ["b"]


def test_remove_from_selection_ignores_missing_ids():
    store = SessionStore()
    _seed_wizard(store, "sid", selected=["a", "b"])
    wizard_session.remove_from_selection(store, "sid", ["x", "y"])
    state = wizard_session.get_state(store, "sid")
    assert state["selected_ids"] == ["a", "b"]


def test_toggle_in_selection_adds_when_absent_and_returns_true():
    store = SessionStore()
    _seed_wizard(store, "sid", selected=["a"])
    added = wizard_session.toggle_in_selection(store, "sid", "b")
    assert added is True
    assert wizard_session.get_state(store, "sid")["selected_ids"] == ["a", "b"]


def test_toggle_in_selection_removes_when_present_and_returns_false():
    store = SessionStore()
    _seed_wizard(store, "sid", selected=["a", "b"])
    added = wizard_session.toggle_in_selection(store, "sid", "a")
    assert added is False
    assert wizard_session.get_state(store, "sid")["selected_ids"] == ["b"]
