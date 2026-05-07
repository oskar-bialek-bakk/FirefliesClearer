# Trash classification — validated design

**Status:** approved 2026-05-06
**Author:** brainstorming session with @oskar.bialek, conducted via the superpowers brainstorming skill on 2026-05-06

---

## Goals

Not every meeting deserves an archive. Daily standups, ad-hoc calls, low-signal syncs are noise the user wants gone — but pushing every one of them through the archive pipeline wastes API quota (each archive burns one Fireflies `fetch_artifacts` call out of the 50/day cap), disk space, and time.

This design adds an explicit **"trash"** classification: meetings the user opts to delete in Fireflies *without* a local archive. Trash meetings skip Step 3 (Archive) entirely, surface in a dedicated Step 3a confirmation page where the user can demote any back to archive, then merge with archive successes on Step 4 for the existing Fireflies UI bulk-delete handoff.

Trash classification is **driven by reusable rules**: the user pairs the existing cleanup-filter preset with an optional **trash-classifier preset** (also a `Preset`, no schema change — distinguished only by user naming). Matching meetings auto-classify as trash; the user reviews and overrides per-row in Step 2 before committing.

## Non-goals

- **Multi-select trash presets.** v1 is single-select; users compose multiple rules into one preset. Multi-select can come later if it becomes ergonomic friction.
- **Tag-based auto-classification.** v1 uses presets only. Tags are already a `ScanFiltersModel` field, so they compose naturally if added later — no separate path needed.
- **Mass undo of a trash batch.** The Step 3a typed-count gate is the safety; once confirmed, the rows transition to DELETED and the only recovery is re-recording the meeting in Fireflies (out of scope here).
- **Settings UI for a "default trash classifier".** Step 1 asks every time. A future enhancement can pin a default per user.
- **API-driven trash purge.** Trash deletes go through the same Step 4 Fireflies UI handoff as archive deletes — no quota burn. Auto-purge via the trickle scheduler is out of scope (and conceptually conflicts with the user-explicit safety override below).

## Safety invariant clarification

CLAUDE.md currently states: *"Never delete a Fireflies meeting unless its archive is verified on disk."* This invariant remains **non-negotiable for API/agent-driven deletes** — the Pipeline.run path, the trickle purge scheduler, the non-host auto-mark logic. It does not apply to user-initiated UI handoffs where the user has explicitly classified the meeting as trash via Step 2 → Step 3a. CLAUDE.md gets a one-line scope clarifier as part of this change.

## User-facing flow

| Step | Behavior |
|------|----------|
| **1 Filter** | Existing cleanup-filter preset picker + filter overrides, plus a new optional **"Trash classifier"** preset picker. Both pickers read the same preset list. |
| **2 Review** | Each row has the existing **select** checkbox (left) and a new per-row **Archive** toggle (right). Default: Archive=checked. Rows matching the trash-classifier preset arrive with Archive=unchecked. User overrides per-row; bulk actions add **"Mark selected as Archive"** and **"Mark selected as Trash"**. |
| **3a Trash confirmation** *(new, conditional)* | Rendered only when `trash_ids` is non-empty after Step 2. Yellow warning banner: *"No backup will exist for these meetings."* Numbered list, sorted oldest-first. Each row has a checkbox to **demote back to Archive**. Typed-count gate at the bottom (matches the spirit of the original safety invariant). On submit: **non-host trash rows auto-transition `KNOWN → DELETED`** with reason `non_host_no_api_delete` (mirrors today's Pipeline non-host behavior — the user can't delete them in Fireflies UI, so we record them locally and drop them from `trash_ids`). **Host trash rows remain in KNOWN** and stay in `trash_ids`, carried forward to Step 4 for the bulk-delete. Continue → 3b. |
| **3b Archive** | Today's Step 3, processing `archive_ids` (originally-Archive rows + any rows demoted on 3a). Skipped entirely (auto-redirects to Step 4) if `archive_ids` is empty. |
| **4 Delete handoff** | List = archive successes (ARCHIVED) ∪ host trash rows (KNOWN), sorted oldest-first. Trash rows render a small `no archive` badge so the user can still tell at a glance which titles are about to vanish without backup. The non-host filter from PR #22 still applies (excludes non-host archive rows that auto-transitioned at Pipeline archive time, and non-host trash rows that auto-transitioned on Step 3a). One bulk-delete in Fireflies UI. One Mark-deleted POST handles **both** transitions in one pass: `ARCHIVED|DELETED_FAILED → DELETED` with reason `manual_external_delete_via_wizard` (existing), and `KNOWN → DELETED` with reason `manual_trash_via_wizard` (new). |

## Data model

### Presets

**No schema change.** The existing `Preset` model is reused as-is. The Step 1 form gains a second `<select>` reading the same preset list:

```html
<select name="trash_preset">
  <option value="">— none (no auto-classification) —</option>
  {% for p in presets %}<option value="{{ p.name }}">{{ p.name }}</option>{% endfor %}
</select>
```

Distinguished from cleanup-filter presets only by user naming convention (e.g. `Trash: Standups`, `Trash: 1-on-1s`).

### Wizard session

`WizardState` (defined in `web/wizard_session.py`) gains one new field:

```python
trash_ids: list[str] = []   # subset of selected_ids classified as trash
```

Invariant: `set(trash_ids) ⊆ set(selected_ids)`. The complementary set `set(selected_ids) - set(trash_ids)` is the archive batch.

Transitions:
- **Step 2 Continue:** the route reads the per-row Archive toggle for each selected row, splits `selected_ids` into archive and trash subsets, persists both. If `trash_ids` is empty, redirects to `/cleanup/archive` (Step 3b). Otherwise to `/cleanup/trash-confirm` (Step 3a).
- **Step 3a Continue:** demoted ids move from `trash_ids` back into `archive_ids` (which is computed as `selected_ids - trash_ids`); persists the new `trash_ids`. Redirects to `/cleanup/archive`.

### State machine

`core/manifest.py` FSM extension: **add `KNOWN → DELETED` as a legal transition.** Every other previously-illegal transition stays illegal. The trash flow short-circuits the existing `KNOWN → PENDING → ARCHIVED → DELETED` path because no archive is produced.

The new transition fires from **two places**:
1. Step 3a confirm POST — for non-host trash rows (the user can't delete them in Fireflies UI, so we auto-mark them locally — same semantic as the existing Pipeline non-host auto-mark for archived non-host rows).
2. Step 4 Mark-deleted POST — for host trash rows (after the user has bulk-deleted them in Fireflies UI alongside the archived rows).

### Provenance

`state_log.details` carries the reason. Reasons currently in use:
- `manual_external_delete_via_wizard` — existing Mark-deleted (archive flow), still used.
- `non_host_no_api_delete` — existing non-host auto-mark at Pipeline archive time. **Reused** by Step 3a's non-host trash auto-mark — semantically identical (user can't API-delete; record locally as best-effort). The `archive_path` column being NULL distinguishes the two cases at the row level.
- **`manual_trash_via_wizard`** — *new.* Set when a host trash row transitions `KNOWN → DELETED` at Step 4 Mark-deleted.

### Manifest record shape

Trash-deleted rows have `archive_path`, `verified_at`, `sha256s` all NULL — that null-archive shape is the only mechanical signal at the row level that distinguishes a trash deletion from an archived deletion. Audit/history reads `state_log` to recover the reason.

## Module changes

| File | Change |
|------|--------|
| `core/manifest.py` | FSM: allow `KNOWN → DELETED`. State_log records `manual_trash_via_wizard`. |
| `web/wizard_session.py` | Add `trash_ids` field to `WizardState`; helpers for split/demote/promote between archive and trash sets. |
| `web/routes/cleanup.py` | Step 2 POST splits selection by Archive toggle; new GET/POST `/cleanup/trash-confirm` (Step 3a); existing 3b/4 routes consume `archive_ids` ∪ demoted; `step4_mark_deleted` handles both `KNOWN → DELETED` (trash) and `ARCHIVED|DELETED_FAILED → DELETED` (archive) in one pass. |
| `web/templates/cleanup/step1_filter.html` | Second preset `<select>` ("Trash classifier — optional"). |
| `web/templates/cleanup/_review_row.html` | Per-row Archive toggle next to existing select checkbox. |
| `web/templates/cleanup/_review_toolbar.html` | Bulk actions: "Mark selected as Archive" / "Mark selected as Trash". |
| `web/templates/cleanup/step3a_trash_confirm.html` | **New.** Yellow warning banner, sorted-oldest-first list, per-row demote checkboxes, typed-count gate. |
| `web/templates/cleanup/step4_purge_preflight.html` | `no archive` badge on trash rows; combined list (archive + trash). |
| `web/templates/cleanup/_stepper.html` | 3a appears only when `trash_ids` is non-empty (visually skipped otherwise). |
| `web/routes/history.py` + `templates/history.html` | Filter chip (All / Archived / Trash); `no archive` badge on trash rows. |
| `CLAUDE.md` | One-line clarification: "this invariant applies to API/agent-driven deletes; user-confirmed trash via the wizard's Step 3a is an explicit override." |

Files NOT touched: `core/models.py`, `core/pipeline.py`, `application/preset_service.py`, `application/archive_service.py`, `application/scan_service.py`, `infra/config.py`, `ports/*`. The trash flow lives entirely in the wizard layer + the manifest FSM.

## Edge cases

| Case | Behavior |
|------|----------|
| User picks no trash classifier preset on Step 1 | Step 2 renders all rows with Archive=checked (today's behavior). User can still per-row toggle to trash; Step 3a appears if any get unchecked. |
| User trashes a non-host meeting | On Step 3a confirm, the non-host trash row auto-transitions `KNOWN → DELETED` with reason `non_host_no_api_delete` and is removed from `trash_ids`. Doesn't appear on Step 4 (already DELETED + non-host filter). No Fireflies UI action needed. |
| All selected rows are trash (host) | Step 3a renders. Step 3b auto-redirects to Step 4 (zero archive items). Step 4 lists the host trash rows. Mark-deleted transitions `KNOWN → DELETED`. |
| All selected rows are archive (none trash) | Step 3a is skipped entirely. Existing flow today, unchanged. |
| User crashes mid-Step-3a | `trash_ids` is in the wizard session, refresh-safe. User reloads, sees the same list, types the count, continues. |
| User demotes ALL rows on Step 3a back to archive | `trash_ids` becomes empty after submit. Step 4 will be the existing archive-only handoff. |
| Trash-classifier preset matches zero rows | Step 2 looks identical to today (all Archive=checked). Step 3a never appears. |
| Trash-classifier preset matches all rows | Step 2 shows every row Archive=unchecked; user can opt rows back in via per-row toggle or "Mark selected as Archive" bulk action. |
| User changes mind on Step 2 after auto-classification | Per-row Archive toggle is the override. Bulk actions cover "all back to Archive" / "all to Trash". |
| User reaches Step 4 and notices a trash row they didn't mean to trash | Out of scope: Step 4 only knows about already-final state. Recovery is out of band (re-record in Fireflies). The Step 3a typed-count gate is the safety net; if that's wrong, the user has typed agreement to delete without backup. |
| Sync runs mid-cleanup and reconciles a `KNOWN` row to `gone` | The row drops out of `selected_ids` resolution at Step 3a/3b/4 (existing `_selected_meetings` already handles vanished ids). |
| User runs `/cleanup` twice in parallel tabs | Existing wizard-session protection: each tab has its own session id. No cross-talk on `trash_ids`. |

## Testing strategy

TDD per project rules. ≥80% coverage overall, **100% on the FSM transition table in `core/manifest.py`**.

### New test files

- **`tests/core/test_manifest_fsm_trash.py`** — `KNOWN → DELETED` transition is legal; all previously-illegal transitions remain illegal; state_log entry has the correct `manual_trash_via_wizard` reason in `details`.
- **`tests/web/routes/test_cleanup_step3a.py`** — empty `trash_ids` redirects to Step 3b; non-empty renders sorted-oldest-first; demote checkbox moves id from `trash_ids` back into the archive set; typed-count mismatch returns 422 with banner; submit auto-transitions **non-host** trash rows `KNOWN → DELETED` with reason `non_host_no_api_delete` and drops them from `trash_ids`; **host** trash rows stay in KNOWN, stay in `trash_ids`, no transition yet (Step 4 handles those).
- **`tests/web/routes/test_history_trash_filter.py`** — filter chip filters correctly; `no archive` badge renders only on trash rows.

### Updated test files

- **`tests/web/test_wizard_session.py`** — round-trip of `trash_ids`; split/demote/promote helpers preserve invariant `trash_ids ⊆ selected_ids`.
- **`tests/web/routes/test_cleanup_step2.py`** — Continue with mixed Archive/Trash splits selection correctly; bulk actions update toggles; default Archive=checked; trash-classifier preset auto-unchecks matching rows.
- **`tests/web/routes/test_cleanup_step3.py`** — archive runner consumes the merged set (originally-Archive rows + demoted-from-Trash rows).
- **`tests/web/routes/test_cleanup_step4.py`** — preflight list combines archive successes + host trash rows (KNOWN), sorted oldest-first; `no archive` badge on trash rows; non-host trash rows excluded (already DELETED per Step 3a auto-mark); `mark_deleted` does both transitions in one pass — `ARCHIVED|DELETED_FAILED → DELETED` with reason `manual_external_delete_via_wizard` (existing) and `KNOWN → DELETED` with reason `manual_trash_via_wizard` (new).

### E2E happy path (manual)

Run a cleanup batch with mixed classifications, demote one trash to archive on Step 3a, complete archive on 3b, complete handoff on Step 4, audit on the history page. Verify counts match end-to-end.

## Open implementation choices

These are deliberately not pinned in the spec — implementation plan can decide based on what reads cleaner against the existing code:

- Whether the per-row Archive toggle is rendered as a checkbox or a switch (visual only).
- Whether the Step 3a typed-count gate uses the same exact wording as today's purge gate or a trash-specific phrase.
- Whether the Step 4 "no archive" badge is a colored pill or just a small icon with tooltip.
- Whether the trash-classifier `<select>` lives in the same fieldset as the cleanup-filter preset picker, or in its own.

## Out of scope (YAGNI — explicit)

- Multi-select trash presets.
- Tag-based auto-classification.
- Mass undo of a trash batch.
- Settings UI for a "default trash classifier" preset.
- API-driven trash purge / trickle scheduler integration.
- Per-row "why was this auto-classified?" tooltip showing which preset rule fired.
