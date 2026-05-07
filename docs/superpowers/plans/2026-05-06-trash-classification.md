# Trash classification — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "trash" classification path to the cleanup wizard so meetings the user marks as trash skip Step 3 (Archive), get reviewed in a new Step 3a typed-count confirmation page, and merge with archive successes on Step 4 for the existing Fireflies UI bulk-delete handoff.

**Architecture:** Per-row Archive toggle on Step 2 (independent of the existing select checkbox), persisted as a `trash_ids` subset of `selected_ids` in the wizard session. New `KNOWN → DELETED` FSM transition lets trash rows skip the archive intermediate. A new optional "Trash classifier" preset on Step 1 auto-fills the toggle. Step 3a is the typed-count safety gate. Step 4 lists archive successes ∪ host trash rows, mark-deleted handles both transitions.

**Tech Stack:** Python 3.13, FastAPI, Jinja2, HTMX, SQLite (manifest), Pydantic, Pytest, Ruff, Mypy strict.

**Spec:** `docs/superpowers/specs/2026-05-06-trash-classification-design.md`

---

## File Structure

**Modify:**
- `firefliesclearer/core/manifest.py` — add `KNOWN → DELETED` transition.
- `firefliesclearer/web/wizard_session.py` — add `trash_ids`, `trash_classifier_preset`, `trash_candidate_ids` fields + helpers.
- `firefliesclearer/web/routes/cleanup.py` — Step 1/2/3a/3b/4 route changes.
- `firefliesclearer/web/templates/cleanup/step1_filter.html` — second preset picker.
- `firefliesclearer/web/templates/cleanup/_review_row.html` — per-row Archive toggle.
- `firefliesclearer/web/templates/cleanup/_review_toolbar.html` — bulk actions for trash.
- `firefliesclearer/web/templates/cleanup/_stepper.html` — conditional 3a step.
- `firefliesclearer/web/templates/cleanup/step4_purge_preflight.html` — `no archive` badge.
- `firefliesclearer/web/routes/history.py` + `firefliesclearer/web/templates/history.html` — filter chip + badge.
- `CLAUDE.md` — invariant scope clarification.

**Create:**
- `firefliesclearer/web/templates/cleanup/step3a_trash_confirm.html` — new template.
- `tests/core/test_manifest_fsm_trash.py` — FSM tests.
- `tests/web/routes/test_cleanup_step3a.py` — Step 3a route tests.
- `tests/web/routes/test_cleanup_step1_trash_preset.py` — Step 1 trash-classifier picker tests.
- `tests/web/routes/test_cleanup_step2_archive_toggle.py` — Step 2 toggle/bulk tests.
- `tests/web/routes/test_history_trash_filter.py` — history page filter tests.

**Update existing tests:**
- `tests/web/test_wizard_session.py` — `trash_ids` round-trip, helpers.
- `tests/web/routes/test_cleanup_step2.py` — Continue routes to `/cleanup/trash-confirm` when `trash_ids` non-empty.
- `tests/web/routes/test_cleanup_step3.py` — auto-redirect to Step 4 when `archive_ids` empty.
- `tests/web/routes/test_cleanup_step4.py` — combined list, badge, `KNOWN → DELETED` in mark-deleted.

---

## Task 1: FSM — allow `KNOWN → DELETED`

**Files:**
- Modify: `firefliesclearer/core/manifest.py:17-41`
- Test: `tests/core/test_manifest_fsm_trash.py`

- [ ] **Step 1: Write the failing test**

Create `tests/core/test_manifest_fsm_trash.py`:

```python
"""FSM tests for the trash flow's KNOWN -> DELETED short-circuit."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

import pytest

from firefliesclearer.core.manifest import SCHEMA, IllegalStateTransition, Manifest
from firefliesclearer.core.models import Meeting, MeetingState

NOW = datetime(2026, 5, 6, 12, 0, tzinfo=UTC)


def _manifest() -> Manifest:
    conn = sqlite3.connect(":memory:", isolation_level=None, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    return Manifest(conn)


def _meeting(mid: str = "m1") -> Meeting:
    return Meeting(
        meeting_id=mid,
        title="t",
        meeting_date=NOW,
        duration_minutes=10.0,
        host_email="oskar@example.com",
        participant_count=2,
    )


def test_known_to_deleted_is_legal_for_trash_flow() -> None:
    m = _manifest()
    m.upsert_known(_meeting(), at=NOW)
    m.transition(
        "m1",
        to=MeetingState.DELETED,
        at=NOW,
        details={"reason": "manual_trash_via_wizard"},
    )
    rec = m.get("m1")
    assert rec is not None
    assert rec.state is MeetingState.DELETED
    assert rec.archive_path is None
    last = m.state_log("m1")[-1]
    assert last.from_state is MeetingState.KNOWN
    assert last.to_state is MeetingState.DELETED
    assert last.details == {"reason": "manual_trash_via_wizard"}


def test_known_to_pending_still_legal() -> None:
    m = _manifest()
    m.upsert_known(_meeting(), at=NOW)
    m.transition("m1", to=MeetingState.PENDING, at=NOW)
    rec = m.get("m1")
    assert rec is not None
    assert rec.state is MeetingState.PENDING


def test_known_to_archived_still_illegal() -> None:
    m = _manifest()
    m.upsert_known(_meeting(), at=NOW)
    with pytest.raises(IllegalStateTransition):
        m.transition("m1", to=MeetingState.ARCHIVED, at=NOW)


def test_pending_to_deleted_still_illegal() -> None:
    m = _manifest()
    m.upsert_known(_meeting(), at=NOW)
    m.transition("m1", to=MeetingState.PENDING, at=NOW)
    with pytest.raises(IllegalStateTransition):
        m.transition("m1", to=MeetingState.DELETED, at=NOW)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/pytest.exe tests/core/test_manifest_fsm_trash.py -v`
Expected: `test_known_to_deleted_is_legal_for_trash_flow` FAILS with `IllegalStateTransition`.

- [ ] **Step 3: Add the transition**

Edit `firefliesclearer/core/manifest.py` line 22:

```python
    MeetingState.KNOWN: frozenset({MeetingState.PENDING, MeetingState.DELETED}),
```

(Was: `frozenset({MeetingState.PENDING})`.)

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/pytest.exe tests/core/test_manifest_fsm_trash.py -v`
Expected: 4 PASS.

Run full FSM suite to confirm no regressions:
`.venv/Scripts/pytest.exe tests/core/ -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add firefliesclearer/core/manifest.py tests/core/test_manifest_fsm_trash.py
git commit -m "feat(core/manifest): allow KNOWN -> DELETED transition for trash flow"
```

---

## Task 2: WizardState — `trash_ids` + helpers

**Files:**
- Modify: `firefliesclearer/web/wizard_session.py:32-39, 41+`
- Test: `tests/web/test_wizard_session.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/web/test_wizard_session.py`:

```python
def test_trash_ids_roundtrip() -> None:
    store = _store()
    set_state(
        store,
        "sid",
        WizardState(
            step="review",
            filters={},
            selected_ids=["a", "b", "c"],
            operation_id=None,
            trash_ids=["b", "c"],
        ),
    )
    state = get_state(store, "sid")
    assert state.get("trash_ids") == ["b", "c"]


def test_set_trash_ids_enforces_subset_invariant() -> None:
    store = _store()
    set_state(
        store,
        "sid",
        WizardState(
            step="review", filters={}, selected_ids=["a", "b"], operation_id=None, trash_ids=[]
        ),
    )
    # b is selected, c is not — c must be silently dropped.
    set_trash_ids(store, "sid", ["b", "c"])
    assert get_state(store, "sid").get("trash_ids") == ["b"]


def test_remove_from_selection_also_clears_trash() -> None:
    store = _store()
    set_state(
        store,
        "sid",
        WizardState(
            step="review",
            filters={},
            selected_ids=["a", "b", "c"],
            operation_id=None,
            trash_ids=["a", "b"],
        ),
    )
    remove_from_selection(store, "sid", ["a"])
    state = get_state(store, "sid")
    assert state.get("selected_ids") == ["b", "c"]
    assert state.get("trash_ids") == ["b"]


def test_demote_from_trash_moves_id_out() -> None:
    store = _store()
    set_state(
        store,
        "sid",
        WizardState(
            step="review",
            filters={},
            selected_ids=["a", "b", "c"],
            operation_id=None,
            trash_ids=["a", "b"],
        ),
    )
    demote_from_trash(store, "sid", ["a"])
    state = get_state(store, "sid")
    # a is back in archive set (still selected, no longer trash). b stays trash.
    assert state.get("selected_ids") == ["a", "b", "c"]
    assert state.get("trash_ids") == ["b"]
```

Imports at top of file (add if missing):

```python
from firefliesclearer.web.wizard_session import (
    WizardState,
    demote_from_trash,
    get_state,
    remove_from_selection,
    set_state,
    set_trash_ids,
)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/pytest.exe tests/web/test_wizard_session.py -v -k "trash or remove_from_selection_also"`
Expected: FAIL with ImportError on `set_trash_ids` / `demote_from_trash`.

- [ ] **Step 3: Implement helpers**

Edit `firefliesclearer/web/wizard_session.py`. Update the `WizardState` TypedDict (around line 32):

```python
class WizardState(TypedDict, total=False):
    """Typed slice of the session store dedicated to the cleanup wizard."""

    step: str
    filters: dict[str, Any]
    selected_ids: list[str]
    operation_id: str | None
    # Trash flow (Step 2 -> 3a -> 4):
    # ``trash_ids`` is the subset of ``selected_ids`` the user has classified
    # as trash (no archive). Invariant: set(trash_ids) <= set(selected_ids).
    # ``trash_classifier_preset`` is the optional preset name used to
    # auto-fill the per-row Archive toggle. ``trash_candidate_ids`` is the
    # cached set of ids matching that classifier against the current scan
    # result, computed at Step 1 submit and consulted by toggle handlers.
    trash_ids: list[str]
    trash_classifier_preset: str | None
    trash_candidate_ids: list[str]
```

Add helpers near the existing selection helpers (after `toggle_in_selection`, around line 141):

```python
# ---------------------------------------------------------------------------
# Trash classification helpers (Step 2 / 3a)
# ---------------------------------------------------------------------------


def get_trash_ids(store: SessionStore, sid: str) -> set[str]:
    """Return the current ``trash_ids`` slice as a set."""
    state = get_state(store, sid)
    return set(state.get("trash_ids", []) or [])


def set_trash_ids(store: SessionStore, sid: str, ids: list[str]) -> None:
    """Replace ``trash_ids`` while enforcing ``trash_ids <= selected_ids``.

    Ids not in the current selection are silently dropped (the only legal
    transition is "selected and classified" — classifying an unselected row
    is a no-op).
    """
    state = get_state(store, sid)
    selected = set(state.get("selected_ids", []) or [])
    new_state = WizardState(
        step=state.get("step", "review"),
        filters=state.get("filters", {}),
        selected_ids=list(state.get("selected_ids", []) or []),
        operation_id=state.get("operation_id"),
        trash_ids=[mid for mid in ids if mid in selected],
        trash_classifier_preset=state.get("trash_classifier_preset"),
        trash_candidate_ids=list(state.get("trash_candidate_ids", []) or []),
    )
    set_state(store, sid, new_state)


def add_to_trash(store: SessionStore, sid: str, ids: list[str]) -> None:
    """Add ids to ``trash_ids`` (must already be in ``selected_ids``)."""
    current = list(get_trash_ids(store, sid))
    seen = set(current)
    for mid in ids:
        if mid not in seen:
            current.append(mid)
            seen.add(mid)
    set_trash_ids(store, sid, current)


def remove_from_trash(store: SessionStore, sid: str, ids: list[str]) -> None:
    """Drop ids from ``trash_ids`` (does not affect ``selected_ids``)."""
    excl = set(ids)
    current = [mid for mid in (get_state(store, sid).get("trash_ids", []) or []) if mid not in excl]
    set_trash_ids(store, sid, current)


def toggle_in_trash(store: SessionStore, sid: str, mid: str) -> bool:
    """Toggle ``mid`` in ``trash_ids``. Returns True if added, False if removed."""
    current = get_trash_ids(store, sid)
    if mid in current:
        remove_from_trash(store, sid, [mid])
        return False
    add_to_trash(store, sid, [mid])
    return True


def demote_from_trash(store: SessionStore, sid: str, ids: list[str]) -> None:
    """Step 3a demote: move ids out of trash; they remain in ``selected_ids``.

    Equivalent to :func:`remove_from_trash` — wrapped for call-site clarity.
    """
    remove_from_trash(store, sid, ids)
```

Patch `replace_selection` and `remove_from_selection` to maintain the subset invariant. Find `replace_selection` (around line 72) and update it:

```python
def replace_selection(store: SessionStore, sid: str, ids: list[str]) -> None:
    """Replace ``selected_ids`` and prune ``trash_ids`` to maintain the
    ``trash_ids <= selected_ids`` invariant."""
    state = get_state(store, sid)
    new_selected = set(ids)
    pruned_trash = [mid for mid in (state.get("trash_ids", []) or []) if mid in new_selected]
    new_state = WizardState(
        step=state.get("step", "review"),
        filters=state.get("filters", {}),
        selected_ids=list(ids),
        operation_id=state.get("operation_id"),
        trash_ids=pruned_trash,
        trash_classifier_preset=state.get("trash_classifier_preset"),
        trash_candidate_ids=list(state.get("trash_candidate_ids", []) or []),
    )
    set_state(store, sid, new_state)
```

`remove_from_selection`, `add_to_selection`, `toggle_in_selection` already delegate to `replace_selection`, so the invariant is now maintained for free.

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/pytest.exe tests/web/test_wizard_session.py -v`
Expected: all PASS.

Run full route test suite to confirm no regressions:
`.venv/Scripts/pytest.exe tests/web/ -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add firefliesclearer/web/wizard_session.py tests/web/test_wizard_session.py
git commit -m "feat(web/wizard_session): add trash_ids field and helpers"
```

---

## Task 3: Step 1 — Trash classifier preset picker

**Files:**
- Modify: `firefliesclearer/web/routes/cleanup.py` (the GET `/cleanup` and POST `/cleanup` handlers)
- Modify: `firefliesclearer/web/templates/cleanup/step1_filter.html`
- Test: `tests/web/routes/test_cleanup_step1_trash_preset.py`

- [ ] **Step 1: Write the failing test**

Create `tests/web/routes/test_cleanup_step1_trash_preset.py`:

```python
"""Step 1 — second preset picker for trash classification."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient
from selectolax.parser import HTMLParser

from firefliesclearer.application.preset_service import PresetService
from firefliesclearer.infra.config import Preset, ScanFiltersModel


def _seed_preset(app, name: str, *, title_contains: list[str]) -> None:
    svc = PresetService(app.state.config_path)
    svc.create(
        Preset(
            name=name,
            description="",
            default=False,
            created_at=datetime(2026, 5, 6, tzinfo=UTC),
            filters=ScanFiltersModel(title_contains=title_contains),
        )
    )


def test_step1_renders_two_preset_pickers(configured_app) -> None:
    _seed_preset(configured_app, "old-meetings", title_contains=[])
    _seed_preset(configured_app, "Trash: Standups", title_contains=["standup"])
    with TestClient(configured_app) as c:
        c.get("/?token=T")
        r = c.get("/cleanup")
    assert r.status_code == 200
    doc = HTMLParser(r.text)
    cleanup_picker = doc.css_first("select[name='preset']")
    trash_picker = doc.css_first("select[name='trash_preset']")
    assert cleanup_picker is not None
    assert trash_picker is not None
    # Trash picker has an empty default option labelled "(none)".
    first_opt = trash_picker.css_first("option")
    assert first_opt is not None
    assert first_opt.attributes.get("value") == ""
    # Both presets appear in both pickers (distinguished by name only).
    cleanup_names = {o.attributes.get("value") for o in cleanup_picker.css("option")}
    trash_names = {o.attributes.get("value") for o in trash_picker.css("option")}
    assert "old-meetings" in cleanup_names and "Trash: Standups" in cleanup_names
    assert "old-meetings" in trash_names and "Trash: Standups" in trash_names


def test_step1_post_persists_trash_classifier(configured_app) -> None:
    _seed_preset(configured_app, "Trash: Standups", title_contains=["standup"])
    with TestClient(configured_app) as c:
        c.get("/?token=T")
        c.post(
            "/cleanup",
            data={
                "_csrf": c.cookies.get("ffc_csrf", ""),
                "older_than_days": "30",
                "older_than_days_enabled": "1",
                "trash_preset": "Trash: Standups",
            },
            follow_redirects=False,
        )
        sid = c.cookies.get("ffc_session", "")
    state = configured_app.state.session_store.get(sid).get("wizard", {})
    assert state.get("trash_classifier_preset") == "Trash: Standups"


def test_step1_post_with_no_trash_classifier_persists_none(configured_app) -> None:
    with TestClient(configured_app) as c:
        c.get("/?token=T")
        c.post(
            "/cleanup",
            data={
                "_csrf": c.cookies.get("ffc_csrf", ""),
                "older_than_days": "30",
                "older_than_days_enabled": "1",
                "trash_preset": "",
            },
            follow_redirects=False,
        )
        sid = c.cookies.get("ffc_session", "")
    state = configured_app.state.session_store.get(sid).get("wizard", {})
    assert state.get("trash_classifier_preset") in (None, "")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/pytest.exe tests/web/routes/test_cleanup_step1_trash_preset.py -v`
Expected: FAIL — selectors don't find `select[name='trash_preset']`.

- [ ] **Step 3: Add the trash picker to the template**

Edit `firefliesclearer/web/templates/cleanup/step1_filter.html`. Find the existing preset `<select name="preset">` block and add a sibling block immediately after it:

```html
{# Trash classifier preset (optional). Reads from the same preset list;
   distinguished from cleanup-filter presets only by user naming. #}
<div class="form-row">
  <label for="trash_preset">Trash classifier <span class="optional-hint">(optional)</span></label>
  <select name="trash_preset" id="trash_preset">
    <option value="">— none (no auto-classification) —</option>
    {% for p in presets %}
      <option value="{{ p.name }}"
        {% if loaded_trash_preset == p.name %}selected{% endif %}>
        {{ p.name }}
      </option>
    {% endfor %}
  </select>
  <p class="form-hint">
    Meetings matching this preset will be pre-classified as trash on Step 2
    (no archive). You can override per-row.
  </p>
</div>
```

- [ ] **Step 4: Wire it through the GET and POST handlers**

In `firefliesclearer/web/routes/cleanup.py`, find `step1_form` (around line 142) and `step1_submit` (the POST `/cleanup` handler around line 223).

Update the GET handler to expose `loaded_trash_preset`:

```python
@router.get("/cleanup")
async def step1_form(
    request: Request,
    deps: SimpleNamespace = Depends(get_deps),  # noqa: B008
) -> Response:
    state = wizard_session.get_state(_store(request), _sid(request))
    filters = wizard_session.filters_from_dict(state.get("filters", {}))
    loaded_trash_preset = state.get("trash_classifier_preset")

    config_path = request.app.state.config_path
    preset_svc = PresetService(config_path) if config_path else None
    presets = preset_svc.list() if preset_svc else []

    preset_name_param = request.query_params.get("preset")
    preset_error: str | None = None
    loaded_preset: str | None = None

    if preset_name_param and preset_svc:
        try:
            preset = preset_svc.get(preset_name_param)
            filters = wizard_session.filters_from_dict(preset.filters.model_dump())
            loaded_preset = preset.name
        except PresetNotFoundError:
            preset_error = f'Preset "{preset_name_param}" not found.'

    return _templates(request).TemplateResponse(
        request,
        "cleanup/step1_filter.html",
        {
            "filters": filters,
            "presets": presets,
            "loaded_preset": loaded_preset,
            "loaded_trash_preset": loaded_trash_preset,
            "step": "filter",
            "error": preset_error,
        },
    )
```

Update the POST handler to read `trash_preset` from the form and persist it. Find the section where it constructs/sets the wizard state and add the `trash_classifier_preset` field. Conceptually:

```python
form = await request.form()
trash_preset_raw = form.get("trash_preset")
trash_preset = str(trash_preset_raw).strip() if trash_preset_raw else None
trash_preset = trash_preset or None  # collapse empty string -> None
# ... existing filter parsing ...
new_state = WizardState(
    step="review",
    filters=wizard_session.filters_to_dict(filters),
    selected_ids=[],
    operation_id=None,
    trash_ids=[],
    trash_classifier_preset=trash_preset,
    trash_candidate_ids=[],  # populated lazily on first Step 2 render
)
wizard_session.set_state(_store(request), _sid(request), new_state)
```

(Read the actual current implementation of the POST handler before editing — it has CSRF / scan-error / regex-validation paths that must be preserved. The change is additive: read `trash_preset`, persist `trash_classifier_preset` alongside the existing fields.)

- [ ] **Step 5: Run tests**

Run: `.venv/Scripts/pytest.exe tests/web/routes/test_cleanup_step1_trash_preset.py -v`
Expected: 3 PASS.

Run: `.venv/Scripts/pytest.exe tests/web/routes/test_cleanup_step1.py -q`
Expected: existing Step 1 tests still PASS.

- [ ] **Step 6: Commit**

```bash
git add firefliesclearer/web/routes/cleanup.py firefliesclearer/web/templates/cleanup/step1_filter.html tests/web/routes/test_cleanup_step1_trash_preset.py
git commit -m "feat(web/cleanup): add Trash classifier preset picker on Step 1"
```

---

## Task 4: Step 2 — `trash_candidate_ids` computation on render

**Files:**
- Modify: `firefliesclearer/web/routes/cleanup.py` (the GET `/cleanup/review` handler)
- Test: `tests/web/routes/test_cleanup_step2_archive_toggle.py`

This task computes which scan results match the trash classifier preset and persists that set in the wizard session so toggle handlers can consult it cheaply. No UI yet.

- [ ] **Step 1: Write the failing test**

Create `tests/web/routes/test_cleanup_step2_archive_toggle.py`:

```python
"""Step 2 — Archive toggle, trash classifier auto-fill, bulk actions."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from firefliesclearer.application.preset_service import PresetService
from firefliesclearer.application.scan_service import ScanFilters
from firefliesclearer.core.models import Meeting
from firefliesclearer.infra.config import Preset, ScanFiltersModel
from firefliesclearer.web.wizard_session import (
    WizardState,
    filters_to_dict,
    set_state,
)
from tests.fakes.in_memory_repository import InMemoryMeetingRepository

NOW = datetime(2026, 5, 6, 12, 0, tzinfo=UTC)


def _seed_meetings(app) -> list[Meeting]:
    meetings = [
        Meeting(
            meeting_id="m_standup",
            title="Daily Standup 2026-05-06",
            meeting_date=NOW - timedelta(days=40),
            duration_minutes=15.0,
            host_email="oskar@example.com",
            participant_count=4,
        ),
        Meeting(
            meeting_id="m_design",
            title="Design Review",
            meeting_date=NOW - timedelta(days=40),
            duration_minutes=60.0,
            host_email="oskar@example.com",
            participant_count=4,
        ),
    ]
    repo = InMemoryMeetingRepository(meetings=meetings, api_key="ff_test")
    repo.set_user_email_for_key("ff_test", "oskar@example.com")
    app.state.deps.client = repo
    for m in meetings:
        app.state.deps.manifest.upsert_known(m, at=NOW)
    return meetings


def _seed_trash_preset(app) -> None:
    PresetService(app.state.config_path).create(
        Preset(
            name="Trash: Standups",
            description="",
            default=False,
            created_at=NOW,
            filters=ScanFiltersModel(title_contains=["standup"]),
        )
    )


def _set_wizard_with_trash_preset(app, sid: str) -> None:
    set_state(
        app.state.session_store,
        sid,
        WizardState(
            step="review",
            filters=filters_to_dict(ScanFilters(older_than_days=30)),
            selected_ids=[],
            operation_id=None,
            trash_ids=[],
            trash_classifier_preset="Trash: Standups",
            trash_candidate_ids=[],
        ),
    )


def test_review_render_populates_trash_candidate_ids(configured_app) -> None:
    _seed_meetings(configured_app)
    _seed_trash_preset(configured_app)
    with TestClient(configured_app) as c:
        c.get("/?token=T")
        sid = c.cookies.get("ffc_session", "")
        _set_wizard_with_trash_preset(configured_app, sid)
        r = c.get("/cleanup/review")
    assert r.status_code == 200
    state = configured_app.state.session_store.get(sid).get("wizard", {})
    assert state.get("trash_candidate_ids") == ["m_standup"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/pytest.exe tests/web/routes/test_cleanup_step2_archive_toggle.py::test_review_render_populates_trash_candidate_ids -v`
Expected: FAIL — `trash_candidate_ids` is `[]`.

- [ ] **Step 3: Compute candidates on Step 2 render**

In `firefliesclearer/web/routes/cleanup.py`, in `step2_review`, after computing `result`/`matches`, compute and persist `trash_candidate_ids`:

```python
# After: result, scan_error = await _scan_or_error(deps, filters)
trash_preset_name = wizard_session.get_state(_store(request), _sid(request)).get(
    "trash_classifier_preset"
)
trash_candidates: set[str] = set()
if trash_preset_name and result is not None:
    config_path = request.app.state.config_path
    preset_svc = PresetService(config_path) if config_path else None
    if preset_svc is not None:
        try:
            trash_preset = preset_svc.get(trash_preset_name)
        except PresetNotFoundError:
            trash_preset = None
        if trash_preset is not None:
            trash_filters = wizard_session.filters_from_dict(
                trash_preset.filters.model_dump()
            )
            # Re-use ScanService's match logic by running an in-memory
            # filter pass over the matched meetings. _classify_trash returns
            # the subset of *matches* that ALSO satisfy the trash filters.
            trash_candidates = _classify_trash(
                [m.meeting for m in result.matches], trash_filters
            )

# Persist for toggle handlers — they consult this set without re-running
# the scan. Updated every time Step 2 renders, so filter changes propagate.
_persist_trash_candidates(request, sorted(trash_candidates))
```

Add helpers near the existing helpers in `cleanup.py` (e.g. above `_selected_meetings`):

```python
def _classify_trash(meetings: list[Meeting], filters: ScanFilters) -> set[str]:
    """Return the subset of meeting ids that satisfy ``filters``.

    Uses the same rule engine that ScanService uses, but applied in-memory
    to a pre-filtered set so we don't re-hit the cache.
    """
    from firefliesclearer.core import rules as rule_mod

    rule_objs = rule_mod.rules_from_filters(filters)
    return {
        m.meeting_id
        for m in meetings
        if all(r.match(m).matched for r in rule_objs) if rule_objs
    }


def _persist_trash_candidates(request: Request, ids: list[str]) -> None:
    state = wizard_session.get_state(_store(request), _sid(request))
    new_state = wizard_session.WizardState(
        step=state.get("step", "review"),
        filters=state.get("filters", {}),
        selected_ids=list(state.get("selected_ids", []) or []),
        operation_id=state.get("operation_id"),
        trash_ids=list(state.get("trash_ids", []) or []),
        trash_classifier_preset=state.get("trash_classifier_preset"),
        trash_candidate_ids=list(ids),
    )
    wizard_session.set_state(_store(request), _sid(request), new_state)
```

(Verify that `rule_mod.rules_from_filters` exists — if not, build the rule list inline using the same construction `ScanService` uses; check `firefliesclearer/application/scan_service.py` for the existing pattern. The intent: convert `ScanFilters` → list of rule objects → conjunctive match. Reuse, don't reinvent.)

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/pytest.exe tests/web/routes/test_cleanup_step2_archive_toggle.py::test_review_render_populates_trash_candidate_ids -v`
Expected: PASS.

Run all Step 2 tests: `.venv/Scripts/pytest.exe tests/web/routes/test_cleanup_step2.py -q`
Expected: all PASS (no regressions).

- [ ] **Step 5: Commit**

```bash
git add firefliesclearer/web/routes/cleanup.py tests/web/routes/test_cleanup_step2_archive_toggle.py
git commit -m "feat(web/cleanup): compute trash_candidate_ids on Step 2 render"
```

---

## Task 5: Step 2 — Per-row Archive toggle UI + handler

**Files:**
- Modify: `firefliesclearer/web/templates/cleanup/_review_row.html`
- Modify: `firefliesclearer/web/routes/cleanup.py` (add `/cleanup/review/archive-toggle/{id}` POST handler)
- Test: `tests/web/routes/test_cleanup_step2_archive_toggle.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/web/routes/test_cleanup_step2_archive_toggle.py`:

```python
def test_review_row_renders_archive_toggle_unchecked_for_trash_candidate(
    configured_app,
) -> None:
    _seed_meetings(configured_app)
    _seed_trash_preset(configured_app)
    with TestClient(configured_app) as c:
        c.get("/?token=T")
        sid = c.cookies.get("ffc_session", "")
        _set_wizard_with_trash_preset(configured_app, sid)
        # Pre-select both rows so the Archive toggle is meaningful.
        from firefliesclearer.web.wizard_session import replace_selection, add_to_trash
        replace_selection(configured_app.state.session_store, sid, ["m_standup", "m_design"])
        # m_standup matched the trash preset on the previous Step 2 render;
        # the auto-classification adds it to trash_ids.
        add_to_trash(configured_app.state.session_store, sid, ["m_standup"])
        r = c.get("/cleanup/review")
    from selectolax.parser import HTMLParser
    doc = HTMLParser(r.text)
    standup_row = doc.css_first("tr[data-meeting-id='m_standup']")
    design_row = doc.css_first("tr[data-meeting-id='m_design']")
    assert standup_row is not None and design_row is not None
    standup_archive = standup_row.css_first("input[name='archive']")
    design_archive = design_row.css_first("input[name='archive']")
    assert standup_archive is not None and design_archive is not None
    # standup is in trash_ids -> Archive UNCHECKED
    assert standup_archive.attributes.get("checked") is None
    # design is selected but not in trash_ids -> Archive CHECKED (default)
    assert design_archive.attributes.get("checked") is not None


def test_post_archive_toggle_flips_trash_membership(configured_app) -> None:
    _seed_meetings(configured_app)
    with TestClient(configured_app) as c:
        c.get("/?token=T")
        sid = c.cookies.get("ffc_session", "")
        from firefliesclearer.web.wizard_session import replace_selection
        set_state(
            configured_app.state.session_store,
            sid,
            WizardState(
                step="review",
                filters=filters_to_dict(ScanFilters(older_than_days=30)),
                selected_ids=["m_design"],
                operation_id=None,
                trash_ids=[],
                trash_classifier_preset=None,
                trash_candidate_ids=[],
            ),
        )
        # Toggle: archive -> trash
        r = c.post(
            "/cleanup/review/archive-toggle/m_design",
            data={
                "_csrf": c.cookies.get("ffc_csrf", ""),
                "page": "1",
                "sort": "date",
                "dir": "desc",
            },
        )
    assert r.status_code == 200
    state = configured_app.state.session_store.get(sid).get("wizard", {})
    assert state.get("trash_ids") == ["m_design"]


def test_post_archive_toggle_unselected_row_is_noop(configured_app) -> None:
    _seed_meetings(configured_app)
    with TestClient(configured_app) as c:
        c.get("/?token=T")
        sid = c.cookies.get("ffc_session", "")
        set_state(
            configured_app.state.session_store,
            sid,
            WizardState(
                step="review",
                filters=filters_to_dict(ScanFilters(older_than_days=30)),
                selected_ids=[],
                operation_id=None,
                trash_ids=[],
                trash_classifier_preset=None,
                trash_candidate_ids=[],
            ),
        )
        r = c.post(
            "/cleanup/review/archive-toggle/m_design",
            data={
                "_csrf": c.cookies.get("ffc_csrf", ""),
                "page": "1",
                "sort": "date",
                "dir": "desc",
            },
        )
    state = configured_app.state.session_store.get(sid).get("wizard", {})
    assert state.get("trash_ids") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/pytest.exe tests/web/routes/test_cleanup_step2_archive_toggle.py -v -k "archive_toggle or archive_unchecked"`
Expected: FAIL on all three.

- [ ] **Step 3: Update the row template**

Edit `firefliesclearer/web/templates/cleanup/_review_row.html`. Add a new `<td>` after the existing `col-check` cell (before `col-title`):

```html
  <td class="col-archive">
    {% if match.meeting.meeting_id in selected_ids %}
      <form hx-post="/cleanup/review/archive-toggle/{{ match.meeting.meeting_id }}"
            hx-trigger="change"
            hx-target=".review-table"
            hx-swap="outerHTML"
            hx-include="this"
            style="display:inline">
        <input type="hidden" name="_csrf" value="{{ request.cookies.get('ffc_csrf', '') }}">
        <input type="hidden" name="page" value="{{ page }}">
        <input type="hidden" name="sort" value="{{ sort }}">
        <input type="hidden" name="dir" value="{{ dir }}">
        <input type="checkbox" name="archive"
          aria-label="Archive meeting {{ match.meeting.title }} (uncheck to trash without backup)"
          {% if match.meeting.meeting_id not in trash_ids %}checked{% endif %}>
      </form>
    {% endif %}
  </td>
```

Also update `_review_table.html` (the table header) to add a column header for "Archive". Find the `<thead>` row and add `<th class="col-archive">Archive</th>` after the `col-check` header.

- [ ] **Step 4: Pass `trash_ids` to the row template**

In `firefliesclearer/web/routes/cleanup.py`, find `_review_context` (or the context dict used by Step 2 templates) and add `trash_ids` to it:

```python
def _review_context(
    request: Request,
    *,
    matches_page,
    total: int,
    page: int,
    pages: int,
    selected_ids: set[str],
    error: str | None,
    sort: str,
    direction: str,
    sync_status,
) -> dict[str, object]:
    return {
        # ... existing fields ...
        "trash_ids": wizard_session.get_trash_ids(_store(request), _sid(request)),
    }
```

(Read the current `_review_context` body — keep all existing fields, add only `trash_ids`.)

- [ ] **Step 5: Add the POST handler**

In `firefliesclearer/web/routes/cleanup.py`, add a new route immediately after `review_toggle` (around line 573):

```python
@router.post("/cleanup/review/archive-toggle/{meeting_id}")
async def review_archive_toggle(
    request: Request,
    meeting_id: str,
    deps: SimpleNamespace = Depends(get_deps),  # noqa: B008
) -> Response:
    """Toggle a meeting's trash-classification (Archive checkbox).

    Only effective when the meeting is in ``selected_ids`` — otherwise a
    silent no-op (the helper enforces ``trash_ids <= selected_ids``).
    Returns the same table fragment as the per-row select toggle.
    """
    filters = _filters_from_session(request)
    if filters is None:
        return _redirect("/cleanup")

    form = await request.form()
    page = _safe_page(form.get("page"))
    sort = _normalize_sort(form.get("sort"))
    direction = _normalize_dir(form.get("dir"))

    wizard_session.toggle_in_trash(_store(request), _sid(request), meeting_id)
    return await _render_table_fragment(
        request, deps, filters, page=page, sort=sort, direction=direction
    )
```

- [ ] **Step 6: Auto-classification when a row is selected**

The `review_toggle` handler (per-row select) and `review_select_all` handler (bulk select) should auto-add newly-selected rows to `trash_ids` when those rows are in `trash_candidate_ids`. Update `review_toggle`:

```python
@router.post("/cleanup/review/toggle/{meeting_id}")
async def review_toggle(
    request: Request,
    meeting_id: str,
    deps: SimpleNamespace = Depends(get_deps),  # noqa: B008
) -> Response:
    filters = _filters_from_session(request)
    if filters is None:
        return _redirect("/cleanup")

    form = await request.form()
    page = _safe_page(form.get("page"))
    sort = _normalize_sort(form.get("sort"))
    direction = _normalize_dir(form.get("dir"))

    added = wizard_session.toggle_in_selection(_store(request), _sid(request), meeting_id)
    # Auto-classify newly-added rows that match the trash classifier.
    if added:
        candidates = set(
            wizard_session.get_state(_store(request), _sid(request)).get(
                "trash_candidate_ids", []
            ) or []
        )
        if meeting_id in candidates:
            wizard_session.add_to_trash(_store(request), _sid(request), [meeting_id])
    return await _render_table_fragment(
        request, deps, filters, page=page, sort=sort, direction=direction
    )
```

Mirror in `review_select_all`: after the bulk add, intersect newly-added ids with `trash_candidate_ids` and call `add_to_trash`. (Read the current handler body and apply the same pattern — find newly-added ids, intersect with candidates, add.)

- [ ] **Step 7: Run tests**

Run: `.venv/Scripts/pytest.exe tests/web/routes/test_cleanup_step2_archive_toggle.py -v`
Expected: all PASS.

Run full Step 2 suite: `.venv/Scripts/pytest.exe tests/web/routes/test_cleanup_step2.py -q`
Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add firefliesclearer/web/routes/cleanup.py firefliesclearer/web/templates/cleanup/_review_row.html firefliesclearer/web/templates/cleanup/_review_table.html tests/web/routes/test_cleanup_step2_archive_toggle.py
git commit -m "feat(web/cleanup): add per-row Archive toggle on Step 2 review"
```

---

## Task 6: Step 2 — Bulk actions (Mark as Archive / Mark as Trash)

**Files:**
- Modify: `firefliesclearer/web/templates/cleanup/_review_toolbar.html`
- Modify: `firefliesclearer/web/routes/cleanup.py` (add two POST handlers)
- Test: `tests/web/routes/test_cleanup_step2_archive_toggle.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/web/routes/test_cleanup_step2_archive_toggle.py`:

```python
def test_post_mark_selected_as_trash_adds_all_to_trash_ids(configured_app) -> None:
    _seed_meetings(configured_app)
    with TestClient(configured_app) as c:
        c.get("/?token=T")
        sid = c.cookies.get("ffc_session", "")
        set_state(
            configured_app.state.session_store,
            sid,
            WizardState(
                step="review",
                filters=filters_to_dict(ScanFilters(older_than_days=30)),
                selected_ids=["m_standup", "m_design"],
                operation_id=None,
                trash_ids=[],
                trash_classifier_preset=None,
                trash_candidate_ids=[],
            ),
        )
        c.post(
            "/cleanup/review/mark-trash",
            data={"_csrf": c.cookies.get("ffc_csrf", ""), "page": "1", "sort": "date", "dir": "desc"},
        )
    state = configured_app.state.session_store.get(sid).get("wizard", {})
    assert sorted(state.get("trash_ids", [])) == ["m_design", "m_standup"]


def test_post_mark_selected_as_archive_clears_trash_ids(configured_app) -> None:
    _seed_meetings(configured_app)
    with TestClient(configured_app) as c:
        c.get("/?token=T")
        sid = c.cookies.get("ffc_session", "")
        set_state(
            configured_app.state.session_store,
            sid,
            WizardState(
                step="review",
                filters=filters_to_dict(ScanFilters(older_than_days=30)),
                selected_ids=["m_standup", "m_design"],
                operation_id=None,
                trash_ids=["m_standup", "m_design"],
                trash_classifier_preset=None,
                trash_candidate_ids=[],
            ),
        )
        c.post(
            "/cleanup/review/mark-archive",
            data={"_csrf": c.cookies.get("ffc_csrf", ""), "page": "1", "sort": "date", "dir": "desc"},
        )
    state = configured_app.state.session_store.get(sid).get("wizard", {})
    assert state.get("trash_ids") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/pytest.exe tests/web/routes/test_cleanup_step2_archive_toggle.py -v -k "mark_selected"`
Expected: FAIL — handlers don't exist.

- [ ] **Step 3: Add handlers**

In `firefliesclearer/web/routes/cleanup.py`, add after `review_archive_toggle`:

```python
@router.post("/cleanup/review/mark-trash")
async def review_mark_trash(
    request: Request,
    deps: SimpleNamespace = Depends(get_deps),  # noqa: B008
) -> Response:
    """Bulk: classify all selected rows as trash (copy ``selected_ids`` -> ``trash_ids``)."""
    filters = _filters_from_session(request)
    if filters is None:
        return _redirect("/cleanup")

    form = await request.form()
    page = _safe_page(form.get("page"))
    sort = _normalize_sort(form.get("sort"))
    direction = _normalize_dir(form.get("dir"))

    state = wizard_session.get_state(_store(request), _sid(request))
    selected = list(state.get("selected_ids", []) or [])
    wizard_session.set_trash_ids(_store(request), _sid(request), selected)
    return await _render_table_fragment(
        request, deps, filters, page=page, sort=sort, direction=direction
    )


@router.post("/cleanup/review/mark-archive")
async def review_mark_archive(
    request: Request,
    deps: SimpleNamespace = Depends(get_deps),  # noqa: B008
) -> Response:
    """Bulk: classify all selected rows as archive (clear ``trash_ids``)."""
    filters = _filters_from_session(request)
    if filters is None:
        return _redirect("/cleanup")

    form = await request.form()
    page = _safe_page(form.get("page"))
    sort = _normalize_sort(form.get("sort"))
    direction = _normalize_dir(form.get("dir"))

    wizard_session.set_trash_ids(_store(request), _sid(request), [])
    return await _render_table_fragment(
        request, deps, filters, page=page, sort=sort, direction=direction
    )
```

- [ ] **Step 4: Add toolbar buttons**

Edit `firefliesclearer/web/templates/cleanup/_review_toolbar.html`. Find the existing bulk-action buttons block and add two new buttons inside the same form scope:

```html
<button type="submit" formaction="/cleanup/review/mark-trash" class="bulk-btn bulk-btn-trash">
  Mark selected as Trash
</button>
<button type="submit" formaction="/cleanup/review/mark-archive" class="bulk-btn bulk-btn-archive">
  Mark selected as Archive
</button>
```

(If the toolbar uses HTMX patterns rather than forms, mirror the existing button style — use `hx-post` rather than `formaction`. Read the file first.)

- [ ] **Step 5: Run tests**

Run: `.venv/Scripts/pytest.exe tests/web/routes/test_cleanup_step2_archive_toggle.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add firefliesclearer/web/routes/cleanup.py firefliesclearer/web/templates/cleanup/_review_toolbar.html tests/web/routes/test_cleanup_step2_archive_toggle.py
git commit -m "feat(web/cleanup): bulk Mark-as-Trash / Mark-as-Archive on Step 2"
```

---

## Task 7: Step 2 Continue — route to Step 3a when `trash_ids` non-empty

**Files:**
- Modify: `firefliesclearer/web/routes/cleanup.py` (`POST /cleanup/review`)
- Test: `tests/web/routes/test_cleanup_step2.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/web/routes/test_cleanup_step2.py`:

```python
def test_step2_continue_routes_to_trash_confirm_when_trash_ids_nonempty(
    configured_client, configured_app
) -> None:
    from firefliesclearer.web.wizard_session import (
        WizardState,
        filters_to_dict,
        set_state,
    )
    from firefliesclearer.application.scan_service import ScanFilters

    sid = configured_client.cookies.get("ffc_session", "")
    set_state(
        configured_app.state.session_store,
        sid,
        WizardState(
            step="review",
            filters=filters_to_dict(ScanFilters(older_than_days=30)),
            selected_ids=["m_standup", "m_design"],
            operation_id=None,
            trash_ids=["m_standup"],
            trash_classifier_preset=None,
            trash_candidate_ids=[],
        ),
    )
    r = configured_client.post(
        "/cleanup/review",
        data={"_csrf": configured_client.cookies.get("ffc_csrf", "")},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/cleanup/trash-confirm"


def test_step2_continue_routes_to_archive_when_no_trash(
    configured_client, configured_app
) -> None:
    from firefliesclearer.web.wizard_session import (
        WizardState,
        filters_to_dict,
        set_state,
    )
    from firefliesclearer.application.scan_service import ScanFilters

    sid = configured_client.cookies.get("ffc_session", "")
    set_state(
        configured_app.state.session_store,
        sid,
        WizardState(
            step="review",
            filters=filters_to_dict(ScanFilters(older_than_days=30)),
            selected_ids=["m_design"],
            operation_id=None,
            trash_ids=[],
            trash_classifier_preset=None,
            trash_candidate_ids=[],
        ),
    )
    r = configured_client.post(
        "/cleanup/review",
        data={"_csrf": configured_client.cookies.get("ffc_csrf", "")},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/cleanup/archive"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/pytest.exe tests/web/routes/test_cleanup_step2.py -v -k "routes_to_trash_confirm or routes_to_archive_when_no_trash"`
Expected: FAIL — both currently redirect to `/cleanup/archive`.

- [ ] **Step 3: Update the POST handler**

Find the `POST /cleanup/review` handler in `cleanup.py` (around line 700–730 — search for `@router.post("/cleanup/review")` not the toggle). At the redirect site:

```python
state = wizard_session.get_state(_store(request), _sid(request))
trash_ids = state.get("trash_ids", []) or []
if trash_ids:
    return _redirect("/cleanup/trash-confirm")
return _redirect("/cleanup/archive")
```

(Preserve any existing empty-selection guard before this branch.)

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/pytest.exe tests/web/routes/test_cleanup_step2.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add firefliesclearer/web/routes/cleanup.py tests/web/routes/test_cleanup_step2.py
git commit -m "feat(web/cleanup): Step 2 Continue routes to Step 3a when trash_ids non-empty"
```

---

## Task 8: Step 3a — GET preflight render

**Files:**
- Create: `firefliesclearer/web/templates/cleanup/step3a_trash_confirm.html`
- Modify: `firefliesclearer/web/routes/cleanup.py` (add GET handler)
- Test: `tests/web/routes/test_cleanup_step3a.py`

- [ ] **Step 1: Write the failing test**

Create `tests/web/routes/test_cleanup_step3a.py`:

```python
"""Step 3a — Trash confirmation (typed-count gate, demote, non-host auto-mark)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from selectolax.parser import HTMLParser

from firefliesclearer.application.scan_service import ScanFilters
from firefliesclearer.core.models import Meeting, MeetingState
from firefliesclearer.web.wizard_session import (
    WizardState,
    filters_to_dict,
    set_state,
)
from tests.fakes.in_memory_repository import InMemoryMeetingRepository

NOW = datetime(2026, 5, 6, 12, 0, tzinfo=UTC)


def _seed(app, meetings: list[Meeting]) -> None:
    repo = InMemoryMeetingRepository(meetings=meetings, api_key="ff_test")
    repo.set_user_email_for_key("ff_test", "oskar@example.com")
    app.state.deps.client = repo
    for m in meetings:
        app.state.deps.manifest.upsert_known(m, at=NOW)


def _wizard(app, sid: str, *, selected: list[str], trash: list[str]) -> None:
    set_state(
        app.state.session_store,
        sid,
        WizardState(
            step="purge",
            filters=filters_to_dict(ScanFilters(older_than_days=30)),
            selected_ids=selected,
            operation_id=None,
            trash_ids=trash,
            trash_classifier_preset=None,
            trash_candidate_ids=[],
        ),
    )


@pytest.fixture
def configured_client(configured_app):
    with TestClient(configured_app) as c:
        c.get("/?token=T")
        yield c


def test_get_trash_confirm_redirects_to_archive_when_trash_ids_empty(
    configured_client: TestClient, configured_app
) -> None:
    sid = configured_client.cookies.get("ffc_session", "")
    _wizard(configured_app, sid, selected=["m1"], trash=[])
    r = configured_client.get("/cleanup/trash-confirm", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/cleanup/archive"


def test_get_trash_confirm_renders_sorted_oldest_first(
    configured_client: TestClient, configured_app
) -> None:
    meetings = [
        Meeting(
            meeting_id="m_new",
            title="Newest standup",
            meeting_date=NOW - timedelta(days=10),
            duration_minutes=15.0,
            host_email="oskar@example.com",
            participant_count=4,
        ),
        Meeting(
            meeting_id="m_old",
            title="Oldest standup",
            meeting_date=NOW - timedelta(days=200),
            duration_minutes=15.0,
            host_email="oskar@example.com",
            participant_count=4,
        ),
    ]
    _seed(configured_app, meetings)
    sid = configured_client.cookies.get("ffc_session", "")
    _wizard(configured_app, sid, selected=["m_new", "m_old"], trash=["m_new", "m_old"])
    r = configured_client.get("/cleanup/trash-confirm")
    assert r.status_code == 200
    doc = HTMLParser(r.text)
    items = doc.css(".trash-confirm-list li")
    assert len(items) == 2
    titles = [li.text(deep=True).strip() for li in items]
    assert titles[0].startswith("Oldest standup")
    assert titles[1].startswith("Newest standup")
    # Warning banner is present.
    assert doc.css_first(".trash-warning-banner") is not None
    # Typed-count gate is present.
    assert doc.css_first("input#confirm-count") is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/pytest.exe tests/web/routes/test_cleanup_step3a.py -v`
Expected: FAIL — route returns 404.

- [ ] **Step 3: Create the template**

Create `firefliesclearer/web/templates/cleanup/step3a_trash_confirm.html`:

```html
{% extends "base.html" %}
{% block title %}Cleanup Step 3a — Trash confirmation — FirefliesClearer{% endblock %}
{% block page %}
{% include "cleanup/_stepper.html" %}

<section class="cleanup-step cleanup-step-trash cleanup-step-trash-confirm">
  <h1>Step 3a — Confirm delete without archive</h1>

  <p class="trash-warning-banner" role="alert">
    <strong>No backup will exist for these meetings.</strong>
    Once deleted in Fireflies, their transcripts, audio, and summaries are gone
    for good — there is no local archive to recover from.
    Uncheck any row to demote it back to the archive flow.
  </p>

  {% if error %}
    <p class="error-banner" role="alert">{{ error }}</p>
  {% endif %}

  <p class="trash-confirm-summary">
    <strong>{{ count }} meeting{% if count != 1 %}s{% endif %}</strong>
    will be deleted in Fireflies without a local archive.
  </p>

  <form method="post" action="/cleanup/trash-confirm" class="trash-confirm-form">
    <input type="hidden" name="_csrf" value="{{ request.cookies.get('ffc_csrf', '') }}">

    <ol class="trash-confirm-list">
      {% for m in meetings %}
        <li>
          <label>
            <input type="checkbox" name="trash_keep" value="{{ m.meeting_id }}" checked>
            <span class="trash-meeting-title">{{ m.title }}</span>
            <span class="meeting-date">{{ m.meeting_date.date().isoformat() }}</span>
          </label>
        </li>
      {% endfor %}
    </ol>

    <div class="trash-confirm-typed-count">
      <label for="confirm-count">
        Type <strong>{{ count }}</strong> to confirm:
      </label>
      <input type="text" id="confirm-count" name="confirmed_count"
             autocomplete="off" inputmode="numeric" pattern="[0-9]*" required>
    </div>

    <div class="form-actions trash-confirm-actions">
      <a href="/cleanup/review" class="cancel-link">&larr; Back</a>
      <button type="submit" class="trash-confirm-btn">
        Confirm delete without archive
      </button>
    </div>
  </form>
</section>
{% endblock %}
```

- [ ] **Step 4: Add the GET handler**

In `firefliesclearer/web/routes/cleanup.py`, add a new section before the existing Step 4 routes:

```python
# ---------------------------------------------------------------------------
# Step 3a — Trash confirmation (typed-count gate, demote, non-host auto-mark)
# ---------------------------------------------------------------------------


@router.get("/cleanup/trash-confirm")
async def step3a_preflight(
    request: Request,
    deps: SimpleNamespace = Depends(get_deps),  # noqa: B008
) -> Response:
    """Render the trash-confirmation preflight: warning banner, sorted list,
    per-row demote checkboxes, typed-count gate."""
    state = wizard_session.get_state(_store(request), _sid(request))
    trash_ids = list(state.get("trash_ids", []) or [])
    if not trash_ids:
        return _redirect("/cleanup/archive")

    meetings = await _selected_meetings(deps, trash_ids)
    if not meetings:
        return _redirect("/cleanup/review?error=empty-selection")
    meetings = sorted(meetings, key=lambda m: m.meeting_date)

    return _templates(request).TemplateResponse(
        request,
        "cleanup/step3a_trash_confirm.html",
        {
            "step": "trash",
            "count": len(meetings),
            "meetings": meetings,
            "error": None,
        },
    )
```

- [ ] **Step 5: Run tests**

Run: `.venv/Scripts/pytest.exe tests/web/routes/test_cleanup_step3a.py -v`
Expected: 2 PASS.

- [ ] **Step 6: Commit**

```bash
git add firefliesclearer/web/routes/cleanup.py firefliesclearer/web/templates/cleanup/step3a_trash_confirm.html tests/web/routes/test_cleanup_step3a.py
git commit -m "feat(web/cleanup): Step 3a trash-confirmation preflight"
```

---

## Task 9: Step 3a — POST handler (typed-count, demote, non-host auto-mark)

**Files:**
- Modify: `firefliesclearer/web/routes/cleanup.py` (add POST handler)
- Test: `tests/web/routes/test_cleanup_step3a.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/web/routes/test_cleanup_step3a.py`:

```python
def test_post_trash_confirm_typed_count_mismatch_returns_422(
    configured_client: TestClient, configured_app
) -> None:
    meetings = [
        Meeting(
            meeting_id=f"m{i}",
            title=f"Standup {i}",
            meeting_date=NOW - timedelta(days=10 + i),
            duration_minutes=15.0,
            host_email="oskar@example.com",
            participant_count=4,
        )
        for i in range(3)
    ]
    _seed(configured_app, meetings)
    sid = configured_client.cookies.get("ffc_session", "")
    _wizard(configured_app, sid, selected=["m0", "m1", "m2"], trash=["m0", "m1", "m2"])
    r = configured_client.post(
        "/cleanup/trash-confirm",
        data={
            "_csrf": configured_client.cookies.get("ffc_csrf", ""),
            "confirmed_count": "2",  # wrong (should be 3)
            "trash_keep": ["m0", "m1", "m2"],
        },
    )
    assert r.status_code == 422
    # State unchanged.
    state = configured_app.state.session_store.get(sid).get("wizard", {})
    assert sorted(state.get("trash_ids", [])) == ["m0", "m1", "m2"]


def test_post_trash_confirm_demotes_unchecked_rows_to_archive(
    configured_client: TestClient, configured_app
) -> None:
    meetings = [
        Meeting(
            meeting_id="m_keep",
            title="Standup keep",
            meeting_date=NOW - timedelta(days=10),
            duration_minutes=15.0,
            host_email="oskar@example.com",
            participant_count=4,
        ),
        Meeting(
            meeting_id="m_demote",
            title="Standup demote",
            meeting_date=NOW - timedelta(days=20),
            duration_minutes=15.0,
            host_email="oskar@example.com",
            participant_count=4,
        ),
    ]
    _seed(configured_app, meetings)
    sid = configured_client.cookies.get("ffc_session", "")
    _wizard(configured_app, sid, selected=["m_keep", "m_demote"], trash=["m_keep", "m_demote"])
    r = configured_client.post(
        "/cleanup/trash-confirm",
        data={
            "_csrf": configured_client.cookies.get("ffc_csrf", ""),
            "confirmed_count": "1",  # only m_keep stays trash
            "trash_keep": ["m_keep"],
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/cleanup/archive"
    state = configured_app.state.session_store.get(sid).get("wizard", {})
    assert state.get("trash_ids") == ["m_keep"]
    # m_demote is still selected (will be archived).
    assert sorted(state.get("selected_ids", [])) == ["m_demote", "m_keep"]


def test_post_trash_confirm_auto_marks_non_host_rows_deleted(
    configured_client: TestClient, configured_app
) -> None:
    configured_app.state.deps.config.fireflies.user_email = "oskar@example.com"
    meetings = [
        Meeting(
            meeting_id="m_host",
            title="My standup",
            meeting_date=NOW - timedelta(days=10),
            duration_minutes=15.0,
            host_email="oskar@example.com",
            participant_count=4,
        ),
        Meeting(
            meeting_id="m_other",
            title="Their standup",
            meeting_date=NOW - timedelta(days=10),
            duration_minutes=15.0,
            host_email="other@example.com",
            participant_count=4,
        ),
    ]
    _seed(configured_app, meetings)
    sid = configured_client.cookies.get("ffc_session", "")
    _wizard(configured_app, sid, selected=["m_host", "m_other"], trash=["m_host", "m_other"])
    configured_client.post(
        "/cleanup/trash-confirm",
        data={
            "_csrf": configured_client.cookies.get("ffc_csrf", ""),
            "confirmed_count": "2",
            "trash_keep": ["m_host", "m_other"],
        },
        follow_redirects=False,
    )
    manifest = configured_app.state.deps.manifest
    # Non-host trash auto-transitioned to DELETED with the existing reason.
    rec_other = manifest.get("m_other")
    assert rec_other is not None and rec_other.state is MeetingState.DELETED
    last = manifest.state_log("m_other")[-1]
    assert last.from_state is MeetingState.KNOWN
    assert last.to_state is MeetingState.DELETED
    assert last.details == {"reason": "non_host_no_api_delete"}
    # Host trash stays in KNOWN, stays in trash_ids.
    rec_host = manifest.get("m_host")
    assert rec_host is not None and rec_host.state is MeetingState.KNOWN
    state = configured_app.state.session_store.get(sid).get("wizard", {})
    assert state.get("trash_ids") == ["m_host"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/pytest.exe tests/web/routes/test_cleanup_step3a.py -v -k "trash_confirm"`
Expected: FAIL — POST handler doesn't exist.

- [ ] **Step 3: Add the POST handler**

Append to `firefliesclearer/web/routes/cleanup.py` after `step3a_preflight`:

```python
@router.post("/cleanup/trash-confirm")
async def step3a_confirm(
    request: Request,
    deps: SimpleNamespace = Depends(get_deps),  # noqa: B008
) -> Response:
    """Trash confirmation submit.

    Expected form:
      - ``confirmed_count``: must equal len(post-demote trash_ids).
      - ``trash_keep``: zero or more checked ids; unchecked ids are demoted
        back to the archive set.

    Behaviour:
      1. Demote unchecked rows out of ``trash_ids`` (they remain in
         ``selected_ids`` and will go through Step 3b).
      2. Validate typed-count: 422 + re-render preflight on mismatch.
      3. For non-host rows in the kept-trash set, transition KNOWN -> DELETED
         with reason ``non_host_no_api_delete`` and remove from ``trash_ids``.
      4. Redirect to /cleanup/archive (Step 3b will further redirect to
         Step 4 if archive_ids is empty).
    """
    state = wizard_session.get_state(_store(request), _sid(request))
    trash_ids = list(state.get("trash_ids", []) or [])
    if not trash_ids:
        return _redirect("/cleanup/archive")

    form = await request.form()
    keep_ids_raw = form.getlist("trash_keep") if hasattr(form, "getlist") else []
    keep_ids = [str(x) for x in keep_ids_raw]
    keep_set = set(keep_ids) & set(trash_ids)
    demote_ids = [mid for mid in trash_ids if mid not in keep_set]

    # 1. Demote unchecked rows.
    if demote_ids:
        wizard_session.demote_from_trash(_store(request), _sid(request), demote_ids)

    # 2. Validate typed-count against the kept set.
    expected_count = len(keep_set)
    confirmed_raw = form.get("confirmed_count")
    confirmed = str(confirmed_raw).strip() if confirmed_raw is not None else ""
    if confirmed != str(expected_count):
        # Re-render preflight with banner + 422.
        meetings = await _selected_meetings(deps, sorted(keep_set))
        meetings = sorted(meetings, key=lambda m: m.meeting_date)
        return _templates(request).TemplateResponse(
            request,
            "cleanup/step3a_trash_confirm.html",
            {
                "step": "trash",
                "count": len(meetings),
                "meetings": meetings,
                "error": "Type the count to confirm.",
            },
            status_code=422,
        )

    # 3. Auto-mark non-host trash as DELETED.
    user_email = deps.config.fireflies.user_email
    if user_email and keep_set:
        ue_lc = user_email.lower()
        kept_meetings = await _selected_meetings(deps, sorted(keep_set))
        non_host_ids = [
            m.meeting_id
            for m in kept_meetings
            if m.host_email and m.host_email.lower() != ue_lc
        ]
        now = deps.clock.now()
        for mid in non_host_ids:
            try:
                deps.manifest.transition(
                    mid,
                    to=MeetingState.DELETED,
                    at=now,
                    details={"reason": "non_host_no_api_delete"},
                )
            except IllegalStateTransition:
                logger.warning(
                    "step3a_confirm: non-host auto-mark skipped for %s "
                    "(state changed mid-run)",
                    mid,
                )
                continue
        if non_host_ids:
            wizard_session.remove_from_trash(_store(request), _sid(request), non_host_ids)

    # 4. Redirect to Step 3b. (3b will redirect to Step 4 if archive_ids empty.)
    return _redirect("/cleanup/archive")
```

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/pytest.exe tests/web/routes/test_cleanup_step3a.py -v`
Expected: all 5 PASS.

- [ ] **Step 5: Commit**

```bash
git add firefliesclearer/web/routes/cleanup.py tests/web/routes/test_cleanup_step3a.py
git commit -m "feat(web/cleanup): Step 3a confirm POST — demote, typed-count, non-host auto-mark"
```

---

## Task 10: Step 3b — auto-redirect to Step 4 when `archive_ids` is empty

**Files:**
- Modify: `firefliesclearer/web/routes/cleanup.py` (`step3_preflight`)
- Test: `tests/web/routes/test_cleanup_step3.py`

When all selected rows are trash, `selected_ids - trash_ids = ∅` after Step 3a. Step 3 (archive) should auto-redirect to Step 4 rather than rendering "0 meetings to archive".

- [ ] **Step 1: Write the failing test**

Append to `tests/web/routes/test_cleanup_step3.py`:

```python
def test_step3_preflight_redirects_to_step4_when_only_trash_remains(
    configured_client, configured_app
) -> None:
    """If all selected rows are still trash after Step 3a (none demoted),
    Step 3b is a no-op and should auto-skip to Step 4."""
    from firefliesclearer.web.wizard_session import (
        WizardState,
        filters_to_dict,
        set_state,
    )
    from firefliesclearer.application.scan_service import ScanFilters

    sid = configured_client.cookies.get("ffc_session", "")
    set_state(
        configured_app.state.session_store,
        sid,
        WizardState(
            step="archive",
            filters=filters_to_dict(ScanFilters(older_than_days=30)),
            selected_ids=["m1", "m2"],
            operation_id=None,
            trash_ids=["m1", "m2"],
            trash_classifier_preset=None,
            trash_candidate_ids=[],
        ),
    )
    r = configured_client.get("/cleanup/archive", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/cleanup/purge"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/pytest.exe tests/web/routes/test_cleanup_step3.py -v -k "redirects_to_step4"`
Expected: FAIL — currently lands on Step 3 preflight.

- [ ] **Step 3: Update `step3_preflight`**

In `firefliesclearer/web/routes/cleanup.py`, find `step3_preflight` (around line 1017 originally, may have shifted). Compute `archive_ids = selected_ids - trash_ids` and short-circuit:

```python
@router.get("/cleanup/archive")
async def step3_preflight(
    request: Request,
    deps: SimpleNamespace = Depends(get_deps),  # noqa: B008
) -> Response:
    state = wizard_session.get_state(_store(request), _sid(request))
    selected_ids = list(state.get("selected_ids") or [])
    if not selected_ids:
        return _redirect("/cleanup/review?error=empty-selection")

    trash_ids = set(state.get("trash_ids") or [])
    archive_ids = [mid for mid in selected_ids if mid not in trash_ids]
    if not archive_ids:
        # All selected rows are trash; nothing to archive. Skip to Step 4.
        return _redirect("/cleanup/purge")

    meetings = await _selected_meetings(deps, archive_ids)
    return _templates(request).TemplateResponse(
        request,
        "cleanup/step3_archive_preflight.html",
        {
            "step": "archive",
            "count": len(meetings),
            "size_mb": _estimate_size_mb(meetings),
            "error": None,
        },
    )
```

Update `step3_start` similarly — pass `archive_ids` (not full `selected_ids`) to the runner. Find:

```python
meetings = await _selected_meetings(deps, selected_ids)
runner = _make_archive_runner(deps=deps, meetings=meetings)
```

Replace with:

```python
trash_ids = set(state.get("trash_ids") or [])
archive_ids = [mid for mid in selected_ids if mid not in trash_ids]
meetings = await _selected_meetings(deps, archive_ids)
runner = _make_archive_runner(deps=deps, meetings=meetings)
```

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/pytest.exe tests/web/routes/test_cleanup_step3.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add firefliesclearer/web/routes/cleanup.py tests/web/routes/test_cleanup_step3.py
git commit -m "feat(web/cleanup): Step 3b consumes archive_ids; auto-skip to Step 4 when empty"
```

---

## Task 11: Step 4 — combined list, `no archive` badge

**Files:**
- Modify: `firefliesclearer/web/routes/cleanup.py` (`step4_preflight`)
- Modify: `firefliesclearer/web/templates/cleanup/step4_purge_preflight.html`
- Test: `tests/web/routes/test_cleanup_step4.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/web/routes/test_cleanup_step4.py`:

```python
def test_step4_preflight_combines_archive_and_host_trash_with_badge(
    configured_client: TestClient, configured_app
) -> None:
    configured_app.state.deps.config.fireflies.user_email = "oskar@example.com"
    meetings = [
        Meeting(
            meeting_id="m_arch",
            title="Archived design review",
            meeting_date=NOW - timedelta(days=200),
            duration_minutes=60.0,
            host_email="oskar@example.com",
            participant_count=4,
        ),
        Meeting(
            meeting_id="m_trash",
            title="Trash standup",
            meeting_date=NOW - timedelta(days=10),
            duration_minutes=15.0,
            host_email="oskar@example.com",
            participant_count=4,
        ),
    ]
    _seed_meetings(configured_app, meetings)
    manifest = configured_app.state.deps.manifest
    _walk_to_archived(manifest, "m_arch")
    # m_trash stays in KNOWN — it'll transition at Step 4 mark-deleted.

    sid = _sid(configured_client)
    from firefliesclearer.web.wizard_session import (
        WizardState,
        filters_to_dict,
        set_state,
    )
    set_state(
        configured_app.state.session_store,
        sid,
        WizardState(
            step="purge",
            filters=filters_to_dict(ScanFilters(older_than_days=30)),
            selected_ids=["m_arch", "m_trash"],
            operation_id=None,
            trash_ids=["m_trash"],
            trash_classifier_preset=None,
            trash_candidate_ids=[],
        ),
    )
    r = configured_client.get("/cleanup/purge")
    assert r.status_code == 200
    doc = HTMLParser(r.text)
    items = doc.css(".purge-meeting-list li")
    assert len(items) == 2
    # Sorted oldest first.
    titles = [li.text(deep=True).strip() for li in items]
    assert titles[0].startswith("Archived design review")
    assert titles[1].startswith("Trash standup")
    # Trash row has the no-archive badge.
    trash_li = items[1]
    badge = trash_li.css_first(".badge-no-archive")
    assert badge is not None
    # Archive row does NOT have the badge.
    arch_li = items[0]
    assert arch_li.css_first(".badge-no-archive") is None
```

(Use the existing helpers in `test_cleanup_step4.py`: `_seed_meetings`, `_walk_to_archived`, `_sid`. Import `Meeting`, `ScanFilters`, `HTMLParser`, `timedelta` if not already imported.)

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/pytest.exe tests/web/routes/test_cleanup_step4.py -v -k "combines_archive_and_host_trash"`
Expected: FAIL — only one row shown.

- [ ] **Step 3: Update `step4_preflight`**

In `firefliesclearer/web/routes/cleanup.py`, update `step4_preflight` to fetch both archive items (in ARCHIVED) and trash items (in KNOWN) and pass a `kind` discriminator to the template:

```python
@router.get("/cleanup/purge")
async def step4_preflight(
    request: Request,
    deps: SimpleNamespace = Depends(get_deps),  # noqa: B008
) -> Response:
    state = wizard_session.get_state(_store(request), _sid(request))
    selected_ids = list(state.get("selected_ids") or [])
    if not selected_ids:
        return _redirect("/cleanup/archive/done")

    trash_ids = set(state.get("trash_ids") or [])
    archive_ids = [mid for mid in selected_ids if mid not in trash_ids]
    archive_meetings = await _selected_meetings(deps, archive_ids)
    trash_meetings = await _selected_meetings(deps, sorted(trash_ids))
    if not archive_meetings and not trash_meetings:
        return _redirect("/cleanup/review?error=empty-selection")

    user_email = deps.config.fireflies.user_email
    archive_meetings = _meetings_for_step4(archive_meetings, user_email)
    trash_meetings = _meetings_for_step4(trash_meetings, user_email)

    # Combined list with kind discriminator. Sort oldest-first across both.
    combined = [(m, "archive") for m in archive_meetings] + [
        (m, "trash") for m in trash_meetings
    ]
    combined.sort(key=lambda pair: pair[0].meeting_date)

    return _templates(request).TemplateResponse(
        request,
        "cleanup/step4_purge_preflight.html",
        {
            "step": "purge",
            "count": len(combined),
            "rows": combined,
            "error": None,
        },
    )
```

(The template currently iterates `meetings`; we'll switch to `rows` which is a list of `(meeting, kind)` tuples.)

- [ ] **Step 4: Update the template**

Edit `firefliesclearer/web/templates/cleanup/step4_purge_preflight.html`. Replace the two `purge-meeting-list` blocks (collapsed and expanded) with:

```html
{% if count > 10 %}
  <details class="purge-meeting-list-collapse">
    <summary>Show {{ count }} meeting titles</summary>
    <ol class="purge-meeting-list">
      {% for m, kind in rows %}
        <li>
          {{ m.title }}
          <span class="meeting-date">{{ m.meeting_date.date().isoformat() }}</span>
          {% if kind == "trash" %}
            <span class="badge-no-archive" title="Deleted without local archive">no archive</span>
          {% endif %}
        </li>
      {% endfor %}
    </ol>
  </details>
{% else %}
  <ol class="purge-meeting-list">
    {% for m, kind in rows %}
      <li>
        {{ m.title }}
        <span class="meeting-date">{{ m.meeting_date.date().isoformat() }}</span>
        {% if kind == "trash" %}
          <span class="badge-no-archive" title="Deleted without local archive">no archive</span>
        {% endif %}
      </li>
    {% endfor %}
  </ol>
{% endif %}
```

- [ ] **Step 5: Run tests**

Run: `.venv/Scripts/pytest.exe tests/web/routes/test_cleanup_step4.py -q`
Expected: all PASS (existing tests use `meetings`, may need to update — see note below).

**Note:** the existing Step 4 tests assert `len(items) == N` against `.purge-meeting-list li` — they should still pass because the template change is internal (still produces `<li>` per row). But the existing tests pass `meetings` keyword in mocks; the route now passes `rows`. The template change above expects `rows`; existing tests calling the route directly are unaffected (they exercise the route, which builds `rows`). Re-run `tests/web/routes/test_cleanup_step4.py` and confirm — adjust any test that asserts on the template-context dict shape.

- [ ] **Step 6: Commit**

```bash
git add firefliesclearer/web/routes/cleanup.py firefliesclearer/web/templates/cleanup/step4_purge_preflight.html tests/web/routes/test_cleanup_step4.py
git commit -m "feat(web/cleanup): Step 4 combines archive + host trash with no-archive badge"
```

---

## Task 12: Step 4 — `mark_deleted` handles `KNOWN → DELETED` for trash

**Files:**
- Modify: `firefliesclearer/web/routes/cleanup.py` (`step4_mark_deleted`)
- Test: `tests/web/routes/test_cleanup_step4.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/web/routes/test_cleanup_step4.py`:

```python
def test_post_mark_deleted_transitions_known_trash_to_deleted(
    configured_client: TestClient, configured_app
) -> None:
    """KNOWN trash rows transition to DELETED via mark-deleted, with the
    new ``manual_trash_via_wizard`` reason."""
    configured_app.state.deps.config.fireflies.user_email = "oskar@example.com"
    meetings = [
        Meeting(
            meeting_id="m_arch",
            title="Archived",
            meeting_date=NOW - timedelta(days=200),
            duration_minutes=60.0,
            host_email="oskar@example.com",
            participant_count=4,
        ),
        Meeting(
            meeting_id="m_trash",
            title="Trash",
            meeting_date=NOW - timedelta(days=10),
            duration_minutes=15.0,
            host_email="oskar@example.com",
            participant_count=4,
        ),
    ]
    _seed_meetings(configured_app, meetings)
    manifest = configured_app.state.deps.manifest
    _walk_to_archived(manifest, "m_arch")
    # m_trash stays KNOWN.

    sid = _sid(configured_client)
    from firefliesclearer.web.wizard_session import (
        WizardState,
        filters_to_dict,
        set_state,
    )
    set_state(
        configured_app.state.session_store,
        sid,
        WizardState(
            step="purge",
            filters=filters_to_dict(ScanFilters(older_than_days=30)),
            selected_ids=["m_arch", "m_trash"],
            operation_id=None,
            trash_ids=["m_trash"],
            trash_classifier_preset=None,
            trash_candidate_ids=[],
        ),
    )

    r = configured_client.post(
        "/cleanup/mark-deleted",
        data={"_csrf": _csrf(configured_client)},
    )
    assert r.status_code == 200

    rec_arch = manifest.get("m_arch")
    assert rec_arch is not None and rec_arch.state is MeetingState.DELETED
    last_arch = manifest.state_log("m_arch")[-1]
    assert last_arch.details == {"reason": "manual_external_delete_via_wizard"}

    rec_trash = manifest.get("m_trash")
    assert rec_trash is not None and rec_trash.state is MeetingState.DELETED
    last_trash = manifest.state_log("m_trash")[-1]
    assert last_trash.from_state is MeetingState.KNOWN
    assert last_trash.to_state is MeetingState.DELETED
    assert last_trash.details == {"reason": "manual_trash_via_wizard"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/pytest.exe tests/web/routes/test_cleanup_step4.py -v -k "transitions_known_trash"`
Expected: FAIL — currently `m_trash` stays in KNOWN (mark-deleted only handles ARCHIVED|DELETED_FAILED).

- [ ] **Step 3: Update `step4_mark_deleted`**

In `firefliesclearer/web/routes/cleanup.py`, update `step4_mark_deleted` (around line 1459–1530). The current implementation iterates `meetings` and only transitions ARCHIVED|DELETED_FAILED. Add a parallel branch for KNOWN trash:

```python
@router.post("/cleanup/mark-deleted")
async def step4_mark_deleted(
    request: Request,
    deps: SimpleNamespace = Depends(get_deps),  # noqa: B008
) -> Response:
    state = wizard_session.get_state(_store(request), _sid(request))
    selected_ids = list(state.get("selected_ids") or [])
    if not selected_ids:
        return _redirect("/cleanup/archive/done")

    trash_ids = set(state.get("trash_ids") or [])
    archive_ids = [mid for mid in selected_ids if mid not in trash_ids]
    archive_meetings = await _selected_meetings(deps, archive_ids)
    trash_meetings = await _selected_meetings(deps, sorted(trash_ids))
    if not archive_meetings and not trash_meetings:
        return _redirect("/cleanup/review?error=empty-selection")

    user_email = deps.config.fireflies.user_email
    archive_meetings = _meetings_for_step4(archive_meetings, user_email)
    trash_meetings = _meetings_for_step4(trash_meetings, user_email)

    archive_eligible = {MeetingState.ARCHIVED, MeetingState.DELETED_FAILED}
    now = deps.clock.now()
    marked = 0
    skipped = 0
    total = len(archive_meetings) + len(trash_meetings)

    # Archive flow: ARCHIVED|DELETED_FAILED -> DELETED
    for meeting in archive_meetings:
        rec = deps.manifest.get(meeting.meeting_id)
        if rec is None or rec.state not in archive_eligible:
            skipped += 1
            continue
        try:
            deps.manifest.transition(
                meeting.meeting_id,
                to=MeetingState.DELETED,
                at=now,
                details={"reason": "manual_external_delete_via_wizard"},
            )
        except IllegalStateTransition:
            logger.warning(
                "step4_mark_deleted: skipped %s (state changed mid-run)",
                meeting.meeting_id,
            )
            skipped += 1
            continue
        marked += 1

    # Trash flow: KNOWN -> DELETED with new reason.
    for meeting in trash_meetings:
        rec = deps.manifest.get(meeting.meeting_id)
        if rec is None or rec.state is not MeetingState.KNOWN:
            skipped += 1
            continue
        try:
            deps.manifest.transition(
                meeting.meeting_id,
                to=MeetingState.DELETED,
                at=now,
                details={"reason": "manual_trash_via_wizard"},
            )
        except IllegalStateTransition:
            logger.warning(
                "step4_mark_deleted: trash transition skipped for %s "
                "(state changed mid-run)",
                meeting.meeting_id,
            )
            skipped += 1
            continue
        marked += 1

    return _templates(request).TemplateResponse(
        request,
        "cleanup/step4_mark_deleted_done.html",
        {
            "step": "purge",
            "marked_count": marked,
            "skipped_count": skipped,
            "total_count": total,
        },
    )
```

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/pytest.exe tests/web/routes/test_cleanup_step4.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add firefliesclearer/web/routes/cleanup.py tests/web/routes/test_cleanup_step4.py
git commit -m "feat(web/cleanup): mark-deleted handles KNOWN -> DELETED for host trash rows"
```

---

## Task 13: Stepper — conditional Step 3a entry

**Files:**
- Modify: `firefliesclearer/web/templates/cleanup/_stepper.html`

- [ ] **Step 1: Read the existing stepper template**

Read `firefliesclearer/web/templates/cleanup/_stepper.html` and identify the `<li>` elements per step.

- [ ] **Step 2: Add the conditional Step 3a entry**

Insert a new `<li class="step" data-step="trash">` between Steps 3 (archive) and 4 (purge), but only render it when `request.session_store` (or however the template accesses the wizard slice) reports a non-empty `trash_ids`. Cleanest approach: pass a `show_trash_step: bool` flag from each route that includes `_stepper.html`.

In `cleanup.py`, define a helper and call it where the stepper renders:

```python
def _show_trash_step(request: Request) -> bool:
    state = wizard_session.get_state(_store(request), _sid(request))
    return bool(state.get("trash_ids"))
```

Add `"show_trash_step": _show_trash_step(request)` to every `TemplateResponse` dict that includes `_stepper.html` (Step 1 form, Step 2 review, Step 3 preflight/in-progress/done, Step 3a, Step 4 preflight/done, mark-deleted-done).

In `_stepper.html`:

```html
<li class="step {% if step == 'trash' %}active{% elif show_trash_step %}{% else %}skipped{% endif %}"
    data-step="trash"
    {% if not show_trash_step %}aria-hidden="true" hidden{% endif %}>
  <a href="/cleanup/trash-confirm">3a. Trash</a>
</li>
```

(Adapt the active/skipped class names to whatever convention the existing `_stepper.html` uses.)

- [ ] **Step 3: Smoke-test rendering**

Run: `.venv/Scripts/pytest.exe tests/web/routes/test_cleanup_step3a.py tests/web/routes/test_cleanup_step2.py -q`
Expected: all PASS.

Manually verify in dev: start the server, run a cleanup batch with one trash row → Step 3a stepper item visible; run a batch with zero trash → Step 3a stepper item hidden. (Optional sanity check.)

- [ ] **Step 4: Commit**

```bash
git add firefliesclearer/web/routes/cleanup.py firefliesclearer/web/templates/cleanup/_stepper.html
git commit -m "feat(web/cleanup): conditional Step 3a stepper entry"
```

---

## Task 14: History — filter chip + `no archive` badge

**Files:**
- Modify: `firefliesclearer/web/routes/history.py`
- Modify: `firefliesclearer/web/templates/history.html`
- Test: `tests/web/routes/test_history_trash_filter.py`

- [ ] **Step 1: Write the failing test**

Create `tests/web/routes/test_history_trash_filter.py`:

```python
"""History page — Archived / Trash / All filter chip + no-archive badge."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from selectolax.parser import HTMLParser

from firefliesclearer.core.models import Meeting, MeetingState

NOW = datetime(2026, 5, 6, 12, 0, tzinfo=UTC)


def _seed_archived(app, mid: str, title: str) -> None:
    m = Meeting(
        meeting_id=mid,
        title=title,
        meeting_date=NOW - timedelta(days=10),
        duration_minutes=60.0,
        host_email="oskar@example.com",
        participant_count=4,
    )
    app.state.deps.manifest.upsert_known(m, at=NOW)
    app.state.deps.manifest.transition(mid, to=MeetingState.PENDING, at=NOW)
    app.state.deps.manifest.transition(
        mid,
        to=MeetingState.ARCHIVED,
        at=NOW,
        archive_path="/tmp/archive",
        verified_at=NOW,
        sha256s={"audio": "x", "summary": "y", "transcript": "z"},
    )
    app.state.deps.manifest.transition(
        mid,
        to=MeetingState.DELETED,
        at=NOW,
        details={"reason": "manual_external_delete_via_wizard"},
    )


def _seed_trash(app, mid: str, title: str) -> None:
    m = Meeting(
        meeting_id=mid,
        title=title,
        meeting_date=NOW - timedelta(days=10),
        duration_minutes=15.0,
        host_email="oskar@example.com",
        participant_count=4,
    )
    app.state.deps.manifest.upsert_known(m, at=NOW)
    app.state.deps.manifest.transition(
        mid,
        to=MeetingState.DELETED,
        at=NOW,
        details={"reason": "manual_trash_via_wizard"},
    )


def test_history_renders_filter_chip(configured_app) -> None:
    _seed_archived(configured_app, "m_arch", "Archived item")
    _seed_trash(configured_app, "m_trash", "Trash item")
    with TestClient(configured_app) as c:
        c.get("/?token=T")
        r = c.get("/history")
    assert r.status_code == 200
    doc = HTMLParser(r.text)
    chips = doc.css(".history-filter-chip")
    chip_labels = {c.text(deep=True).strip() for c in chips}
    assert chip_labels >= {"All", "Archived", "Trash"}


def test_history_filter_archived_only_excludes_trash(configured_app) -> None:
    _seed_archived(configured_app, "m_arch", "Archived item")
    _seed_trash(configured_app, "m_trash", "Trash item")
    with TestClient(configured_app) as c:
        c.get("/?token=T")
        r = c.get("/history?filter=archived")
    assert r.status_code == 200
    text = r.text
    assert "Archived item" in text
    assert "Trash item" not in text


def test_history_filter_trash_only_excludes_archived(configured_app) -> None:
    _seed_archived(configured_app, "m_arch", "Archived item")
    _seed_trash(configured_app, "m_trash", "Trash item")
    with TestClient(configured_app) as c:
        c.get("/?token=T")
        r = c.get("/history?filter=trash")
    assert r.status_code == 200
    text = r.text
    assert "Trash item" in text
    assert "Archived item" not in text


def test_history_trash_row_renders_no_archive_badge(configured_app) -> None:
    _seed_trash(configured_app, "m_trash", "Trash item")
    with TestClient(configured_app) as c:
        c.get("/?token=T")
        r = c.get("/history")
    doc = HTMLParser(r.text)
    row = doc.css_first("[data-meeting-id='m_trash']")
    assert row is not None
    assert row.css_first(".badge-no-archive") is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/pytest.exe tests/web/routes/test_history_trash_filter.py -v`
Expected: FAIL.

- [ ] **Step 3: Update the history route**

Read `firefliesclearer/web/routes/history.py` to find the existing list-render logic. Add a `filter` query param: values `all` (default), `archived`, `trash`. The discriminator is the latest `state_log` entry's reason — `manual_trash_via_wizard` ⇒ trash, anything else ⇒ archived.

```python
@router.get("/history")
async def history_index(
    request: Request,
    deps: SimpleNamespace = Depends(get_deps),
    filter: str = "all",
) -> Response:
    valid_filters = {"all", "archived", "trash"}
    filter_kind = filter if filter in valid_filters else "all"
    rows = list(deps.manifest.list_deleted())  # existing helper or equivalent
    classified = []
    for rec in rows:
        log = deps.manifest.state_log(rec.meeting_id)
        last = log[-1] if log else None
        is_trash = bool(last and last.details and last.details.get("reason") == "manual_trash_via_wizard")
        kind = "trash" if is_trash else "archived"
        if filter_kind == "all" or filter_kind == kind:
            classified.append((rec, kind))
    return _templates(request).TemplateResponse(
        request,
        "history.html",
        {
            "rows": classified,
            "filter_kind": filter_kind,
        },
    )
```

(Adapt to the existing route signature and helper names. The key additions: `filter` query param, per-row `kind` classification, pass to template.)

- [ ] **Step 4: Update the template**

Edit `firefliesclearer/web/templates/history.html`. Add the chip row near the top of the page:

```html
<div class="history-filter-chips">
  <a class="history-filter-chip {% if filter_kind == 'all' %}active{% endif %}" href="/history">All</a>
  <a class="history-filter-chip {% if filter_kind == 'archived' %}active{% endif %}" href="/history?filter=archived">Archived</a>
  <a class="history-filter-chip {% if filter_kind == 'trash' %}active{% endif %}" href="/history?filter=trash">Trash</a>
</div>
```

In the row loop, change `{% for rec in rows %}` to `{% for rec, kind in rows %}` and add the badge:

```html
<tr data-meeting-id="{{ rec.meeting_id }}">
  <td>{{ rec.title }}</td>
  <td>
    {% if kind == "trash" %}
      <span class="badge-no-archive" title="Deleted without local archive">no archive</span>
    {% endif %}
  </td>
  ...
</tr>
```

- [ ] **Step 5: Run tests**

Run: `.venv/Scripts/pytest.exe tests/web/routes/test_history_trash_filter.py -v`
Expected: all PASS.

Run full history suite: `.venv/Scripts/pytest.exe tests/web/routes/test_history*.py -q`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add firefliesclearer/web/routes/history.py firefliesclearer/web/templates/history.html tests/web/routes/test_history_trash_filter.py
git commit -m "feat(web/history): filter chip + no-archive badge"
```

---

## Task 15: CLAUDE.md — clarify safety invariant scope

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Update the invariant section**

Edit `CLAUDE.md`. Find the "Safety invariants (non-negotiable)" section. Update item 1:

```markdown
1. Never delete a Fireflies meeting via the API/agent path unless its archive is verified on disk (file existence + non-zero + checksum recorded). This applies to `Pipeline.run`, the API-purge trickle scheduler, and any agent-initiated mutation. **Exception:** the user-initiated trash flow (cleanup wizard Step 3a typed-count gate) is an explicit override — the user is taking responsibility for skipping the backup; non-host trash rows still auto-mark with reason `non_host_no_api_delete` because no API call is possible.
```

- [ ] **Step 2: Run the full test suite as a final smoke**

Run all tests + lint + types:

```bash
.venv/Scripts/pytest.exe -q
.venv/Scripts/mypy.exe firefliesclearer
.venv/Scripts/ruff.exe check firefliesclearer tests
.venv/Scripts/ruff.exe format --check firefliesclearer tests
```

Expected: all green.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(CLAUDE.md): clarify delete-without-archive invariant scope"
```

---

## Task 16: Manual E2E smoke

**No code change — verification only.**

- [ ] **Step 1: Start the dev server**

```bash
.venv/Scripts/python.exe -m firefliesclearer.cli serve
```

Open `http://localhost:5000` (or whatever the configured port is) in a browser.

- [ ] **Step 2: Create a "Trash: Standups" preset**

Settings → Presets → New. Name: `Trash: Standups`. Filter: title contains `standup`. Save.

- [ ] **Step 3: Run a mixed cleanup batch**

- `/cleanup` → cleanup-filter preset = "older than 90d", trash classifier = "Trash: Standups".
- On Step 2, scan should return both standups and non-standup meetings. Standup rows should arrive with Archive=unchecked.
- Select a mix of both; flip one standup back to Archive; flip one non-standup to Trash. Verify the per-row toggle and the bulk "Mark selected as Trash" / "Mark selected as Archive" buttons work.
- Click Continue.

- [ ] **Step 4: Confirm Step 3a behaviour**

- Step 3a should appear with the warning banner and the typed-count gate.
- Try submitting the wrong count → should see the 422 error banner.
- Type the correct count, demote one row by unchecking → submit.
- Verify the demoted row goes through Step 3b archive; the kept-trash rows skip archive.

- [ ] **Step 5: Confirm Step 4 combined list + badge**

- Step 4 should show archive successes + host trash rows in one list, sorted oldest-first.
- Trash rows should display the `no archive` badge.
- Bulk-delete the listed rows in Fireflies UI.
- Click "Mark these as deleted" → summary should show all rows marked.

- [ ] **Step 6: Confirm history filter + badge**

- Visit `/history`. The All / Archived / Trash chips should render.
- Trash filter shows only trash-deleted rows with the badge.
- Archived filter shows only archive-deleted rows.

- [ ] **Step 7: Push and open PR**

```bash
git push -u origin feat/trash-classification-spec
gh pr create --title "feat(web/cleanup): trash classification (skip-archive flow)" --body "$(cat <<'EOF'
## Summary

Implements the trash-classification design from
\`docs/superpowers/specs/2026-05-06-trash-classification-design.md\`.

- New optional "Trash classifier" preset on Step 1 auto-fills a per-row Archive toggle on Step 2.
- New Step 3a typed-count confirmation page with demote-back-to-archive checkboxes.
- KNOWN -> DELETED FSM transition; non-host trash auto-marks at Step 3a; host trash transitions at Step 4 mark-deleted.
- Step 4 combines archive successes + host trash rows in one list with a \`no archive\` badge.
- History page gains All / Archived / Trash filter chips and the \`no archive\` badge on trash rows.
- CLAUDE.md invariant clarified: API/agent-driven path stays non-negotiable; user-initiated trash via the wizard is an explicit override.

## Test plan

- [x] Unit tests on FSM transitions, wizard session helpers, all five Step 2/3a/4 routes
- [x] Existing Step 1/2/3/4 tests still pass
- [x] mypy strict + ruff check/format clean
- [ ] Manual E2E smoke: mixed batch, demote on Step 3a, combined Step 4, history filter

EOF
)"
```

---

## Self-Review

**Spec coverage:** Walked the spec section by section; every section maps to ≥1 task:
- Goals / non-goals → covered by overall plan structure.
- Safety invariant clarification → Task 15.
- User-facing flow Step 1 picker → Task 3.
- Step 2 Archive toggle + bulk → Tasks 4, 5, 6.
- Step 2 Continue routing → Task 7.
- Step 3a render + confirm → Tasks 8, 9.
- Step 3b auto-redirect → Task 10.
- Step 4 combined list + badge → Task 11.
- Step 4 mark-deleted dual transitions → Task 12.
- Stepper conditional 3a → Task 13.
- History filter + badge → Task 14.
- FSM extension → Task 1.
- Wizard session schema → Task 2.

**Type consistency:** `trash_ids: list[str]` field used consistently across `WizardState`, helpers, routes, and tests. `kind: "archive" | "trash"` discriminator used on Step 4 rows and history rows. State_log reasons used as bare strings: `manual_external_delete_via_wizard`, `manual_trash_via_wizard`, `non_host_no_api_delete`. All references to `selected_ids` and `trash_ids` retain the invariant `set(trash_ids) ⊆ set(selected_ids)` documented in `WizardState`.

**Placeholder scan:** All steps have concrete code or concrete instructions referencing specific lines / function names. The few "(read the file first; preserve existing X)" notes are deliberate — they call out spots where the existing code has more context than I can fully reproduce in the plan, and the engineer must integrate carefully rather than blind-replace.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-06-trash-classification.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
