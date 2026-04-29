"""Tests for the in-process server-side session store."""

from __future__ import annotations

from firefliesclearer.web.sessions import SessionStore


def test_get_returns_empty_dict_for_new_session():
    store = SessionStore()
    assert store.get("sid1") == {}


def test_set_and_get_roundtrip():
    store = SessionStore()
    store.set("sid1", {"step": 2, "api_key": "ff_x"})
    assert store.get("sid1") == {"step": 2, "api_key": "ff_x"}


def test_update_merges():
    store = SessionStore()
    store.set("sid1", {"step": 1})
    store.update("sid1", {"api_key": "ff_x"})
    assert store.get("sid1") == {"step": 1, "api_key": "ff_x"}


def test_delete_removes_session():
    store = SessionStore()
    store.set("sid1", {"step": 1})
    store.delete("sid1")
    assert store.get("sid1") == {}


def test_get_returns_a_copy_so_caller_mutation_does_not_affect_store():
    store = SessionStore()
    store.set("sid1", {"step": 1})
    snapshot = store.get("sid1")
    snapshot["step"] = 999
    assert store.get("sid1") == {"step": 1}
