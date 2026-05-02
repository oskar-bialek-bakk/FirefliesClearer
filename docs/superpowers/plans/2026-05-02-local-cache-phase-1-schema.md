# Local-cache Phase 1: Schema + State Machine — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the schema and state-machine groundwork for the local-cache architecture (`docs/superpowers/specs/2026-05-02-local-cache-design.md`) — strictly additive, no behavior change. Existing v2 functionality is unaffected; only new manifest methods + new SQL columns + new state machine entries are added, ready to be consumed by Phase 2 (sync engine).

**Architecture:** Extend the existing `meetings` table with seven snapshot/source-state columns, add three indexes, add a `sync_runs` table, add `MeetingState.KNOWN`, add four idempotent manifest methods (`upsert_known`, `set_source_state`, `update_cache_fields`, `list_known`). Migration is performed in `Manifest.open()` itself: `CREATE TABLE IF NOT EXISTS` handles fresh DBs; `PRAGMA table_info` + conditional `ALTER TABLE ADD COLUMN` handles existing DBs. No flag, no config, no UI — Phase 2 will build on top.

**Tech Stack:** Python 3.13, SQLite (stdlib `sqlite3` module), pytest, pytest-asyncio (auto mode), mypy strict, ruff.

**Spec reference:** `docs/superpowers/specs/2026-05-02-local-cache-design.md` — sections "Schema" and "Phased rollout / Phase 1".

---

## File Structure

| File | Purpose | Change type |
|------|---------|-------------|
| `firefliesclearer/core/models.py` | Domain types — adds `MeetingState.KNOWN` | Modify (one-line enum addition) |
| `firefliesclearer/core/manifest.py` | Schema + manifest API — schema constants, transitions, new methods, migration helpers | Modify (~250 LOC added) |
| `tests/core/test_manifest.py` | Tests — covers new state, schema migration, four new methods | Modify (~250 LOC added) |

No new files. The manifest module grows but stays under 800 LOC after this phase, well under CLAUDE.md's "<=400 lines, single responsibility" guidance for new files (existing file already grandfathered above 400). If we need to split it later, that's a Phase 6 cleanup concern.

---

## Tasks

### Task 1: Add `MeetingState.KNOWN` + new `LEGAL_TRANSITIONS` entries

**Files:**
- Modify: `firefliesclearer/core/models.py:11-19`
- Modify: `firefliesclearer/core/manifest.py:15-33`
- Test: `tests/core/test_manifest.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/core/test_manifest.py` after the existing `_meeting()` helper:

```python
def test_meeting_state_has_known_variant():
    assert MeetingState.KNOWN.value == "known"


def test_legal_transitions_include_none_to_known():
    from firefliesclearer.core.manifest import LEGAL_TRANSITIONS

    assert MeetingState.KNOWN in LEGAL_TRANSITIONS[None]


def test_legal_transitions_include_known_to_pending():
    from firefliesclearer.core.manifest import LEGAL_TRANSITIONS

    assert MeetingState.PENDING in LEGAL_TRANSITIONS[MeetingState.KNOWN]


def test_legal_transitions_preserve_existing_none_to_pending():
    """register() still works — Phase 1 is strictly additive."""
    from firefliesclearer.core.manifest import LEGAL_TRANSITIONS

    assert MeetingState.PENDING in LEGAL_TRANSITIONS[None]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/pytest.exe tests/core/test_manifest.py::test_meeting_state_has_known_variant tests/core/test_manifest.py::test_legal_transitions_include_none_to_known tests/core/test_manifest.py::test_legal_transitions_include_known_to_pending tests/core/test_manifest.py::test_legal_transitions_preserve_existing_none_to_pending --no-cov -v`
Expected: 4 FAILs — `AttributeError: KNOWN` on the first; the rest fail because the dict entries don't exist.

- [ ] **Step 3: Add `KNOWN` to the enum**

In `firefliesclearer/core/models.py`, change:

```python
class MeetingState(StrEnum):
    PENDING = "pending"
    ARCHIVED = "archived"
    DELETED = "deleted"
    FAILED_FETCH = "failed_fetch"
    FAILED_DOWNLOAD = "failed_download"
    FAILED_RENDER = "failed_render"
    FAILED_VERIFY = "failed_verify"
    DELETED_FAILED = "deleted_failed"
```

To:

```python
class MeetingState(StrEnum):
    KNOWN = "known"
    PENDING = "pending"
    ARCHIVED = "archived"
    DELETED = "deleted"
    FAILED_FETCH = "failed_fetch"
    FAILED_DOWNLOAD = "failed_download"
    FAILED_RENDER = "failed_render"
    FAILED_VERIFY = "failed_verify"
    DELETED_FAILED = "deleted_failed"
```

In `firefliesclearer/core/manifest.py`, change the `LEGAL_TRANSITIONS` dict from:

```python
LEGAL_TRANSITIONS: Mapping[MeetingState | None, frozenset[MeetingState]] = {
    None: frozenset({MeetingState.PENDING}),
    MeetingState.PENDING: frozenset(
        {
            MeetingState.ARCHIVED,
            MeetingState.FAILED_FETCH,
            MeetingState.FAILED_DOWNLOAD,
            MeetingState.FAILED_RENDER,
            MeetingState.FAILED_VERIFY,
        }
    ),
    MeetingState.ARCHIVED: frozenset({MeetingState.DELETED, MeetingState.DELETED_FAILED}),
    MeetingState.DELETED_FAILED: frozenset({MeetingState.DELETED}),
    MeetingState.FAILED_FETCH: frozenset({MeetingState.PENDING}),
    MeetingState.FAILED_DOWNLOAD: frozenset({MeetingState.PENDING}),
    MeetingState.FAILED_RENDER: frozenset({MeetingState.PENDING}),
    MeetingState.FAILED_VERIFY: frozenset({MeetingState.PENDING}),
    MeetingState.DELETED: frozenset(),
}
```

To:

```python
LEGAL_TRANSITIONS: Mapping[MeetingState | None, frozenset[MeetingState]] = {
    # None → KNOWN is the new sync-driven first-touch path.
    # None → PENDING is preserved for Manifest.register() backward compat;
    # both will collapse to None → KNOWN only in Phase 6.
    None: frozenset({MeetingState.KNOWN, MeetingState.PENDING}),
    MeetingState.KNOWN: frozenset({MeetingState.PENDING}),
    MeetingState.PENDING: frozenset(
        {
            MeetingState.ARCHIVED,
            MeetingState.FAILED_FETCH,
            MeetingState.FAILED_DOWNLOAD,
            MeetingState.FAILED_RENDER,
            MeetingState.FAILED_VERIFY,
        }
    ),
    MeetingState.ARCHIVED: frozenset({MeetingState.DELETED, MeetingState.DELETED_FAILED}),
    MeetingState.DELETED_FAILED: frozenset({MeetingState.DELETED}),
    MeetingState.FAILED_FETCH: frozenset({MeetingState.PENDING}),
    MeetingState.FAILED_DOWNLOAD: frozenset({MeetingState.PENDING}),
    MeetingState.FAILED_RENDER: frozenset({MeetingState.PENDING}),
    MeetingState.FAILED_VERIFY: frozenset({MeetingState.PENDING}),
    MeetingState.DELETED: frozenset(),
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/pytest.exe tests/core/test_manifest.py --no-cov -v`
Expected: all tests PASS (the four new ones plus all existing ones).

- [ ] **Step 5: Commit**

```bash
git add firefliesclearer/core/models.py firefliesclearer/core/manifest.py tests/core/test_manifest.py
git commit -m "feat(manifest): add MeetingState.KNOWN and KNOWN->PENDING transition

Phase 1 of local-cache rollout. Strictly additive: existing
None->PENDING transition preserved so Manifest.register() keeps
working. The new None->KNOWN path is consumed by upsert_known()
in a later task, KNOWN->PENDING by queue_for_archive() in Phase 3."
```

---

### Task 2: Extend `meetings` table SCHEMA with snapshot columns + indexes

**Files:**
- Modify: `firefliesclearer/core/manifest.py:35-61` (the `SCHEMA` constant)
- Test: `tests/core/test_manifest.py`

The SCHEMA constant is run by `Manifest.open()` via `executescript()`. New columns added here apply to fresh DBs only — existing DBs are migrated in Task 3.

- [ ] **Step 1: Write the failing tests**

Add to `tests/core/test_manifest.py`:

```python
def test_fresh_manifest_has_snapshot_columns(tmp_path):
    """A newly-opened DB has all Phase 1 snapshot columns + source_state."""
    import sqlite3

    Manifest.open(tmp_path / "manifest.db")
    conn = sqlite3.connect(str(tmp_path / "manifest.db"))
    cols = {row[1] for row in conn.execute("PRAGMA table_info(meetings)")}
    conn.close()

    expected_new = {
        "duration_minutes",
        "host_email",
        "participant_count",
        "has_transcript",
        "tags_json",
        "source_state",
        "cached_at",
    }
    assert expected_new <= cols, f"missing: {expected_new - cols}"


def test_fresh_manifest_source_state_defaults_to_live(tmp_path):
    """source_state column defaults to 'live' so existing-row migration is one-step."""
    import sqlite3

    Manifest.open(tmp_path / "manifest.db")
    conn = sqlite3.connect(str(tmp_path / "manifest.db"))
    row = conn.execute(
        "SELECT dflt_value FROM pragma_table_info('meetings') WHERE name = 'source_state'"
    ).fetchone()
    conn.close()
    assert row is not None
    # SQLite returns the literal default expression; quoted string in our case.
    assert row[0] in ("'live'", "live")


def test_fresh_manifest_has_new_indexes(tmp_path):
    import sqlite3

    Manifest.open(tmp_path / "manifest.db")
    conn = sqlite3.connect(str(tmp_path / "manifest.db"))
    indexes = {
        row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
    }
    conn.close()
    expected = {
        "idx_meetings_source_state",
        "idx_meetings_meeting_date",
        "idx_meetings_host_email",
    }
    assert expected <= indexes, f"missing: {expected - indexes}"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/pytest.exe tests/core/test_manifest.py::test_fresh_manifest_has_snapshot_columns tests/core/test_manifest.py::test_fresh_manifest_source_state_defaults_to_live tests/core/test_manifest.py::test_fresh_manifest_has_new_indexes --no-cov -v`
Expected: 3 FAILs — columns/indexes not yet present in SCHEMA.

- [ ] **Step 3: Extend the SCHEMA constant**

In `firefliesclearer/core/manifest.py`, replace the existing `SCHEMA` block:

```python
SCHEMA = """
CREATE TABLE IF NOT EXISTS meetings (
  meeting_id        TEXT PRIMARY KEY,
  title             TEXT NOT NULL,
  meeting_date      TEXT NOT NULL,
  state             TEXT NOT NULL,
  archive_path      TEXT,
  audio_sha256      TEXT,
  summary_sha256    TEXT,
  transcript_sha256 TEXT,
  archived_at       TEXT,
  verified_at       TEXT,
  deleted_at        TEXT,
  last_error        TEXT
);
CREATE TABLE IF NOT EXISTS state_log (
  id            INTEGER PRIMARY KEY,
  meeting_id    TEXT NOT NULL,
  from_state    TEXT,
  to_state      TEXT NOT NULL,
  at            TEXT NOT NULL,
  details       TEXT,
  FOREIGN KEY (meeting_id) REFERENCES meetings(meeting_id)
);
CREATE INDEX IF NOT EXISTS idx_meetings_state ON meetings(state);
CREATE INDEX IF NOT EXISTS idx_state_log_meeting ON state_log(meeting_id);
"""
```

With:

```python
SCHEMA = """
CREATE TABLE IF NOT EXISTS meetings (
  meeting_id        TEXT PRIMARY KEY,
  title             TEXT NOT NULL,
  meeting_date      TEXT NOT NULL,
  state             TEXT NOT NULL,
  archive_path      TEXT,
  audio_sha256      TEXT,
  summary_sha256    TEXT,
  transcript_sha256 TEXT,
  archived_at       TEXT,
  verified_at       TEXT,
  deleted_at        TEXT,
  last_error        TEXT,
  duration_minutes  REAL,
  host_email        TEXT,
  participant_count INTEGER,
  has_transcript    INTEGER,
  tags_json         TEXT,
  source_state      TEXT NOT NULL DEFAULT 'live',
  cached_at         TEXT
);
CREATE TABLE IF NOT EXISTS state_log (
  id            INTEGER PRIMARY KEY,
  meeting_id    TEXT NOT NULL,
  from_state    TEXT,
  to_state      TEXT NOT NULL,
  at            TEXT NOT NULL,
  details       TEXT,
  FOREIGN KEY (meeting_id) REFERENCES meetings(meeting_id)
);
CREATE INDEX IF NOT EXISTS idx_meetings_state         ON meetings(state);
CREATE INDEX IF NOT EXISTS idx_meetings_source_state  ON meetings(source_state);
CREATE INDEX IF NOT EXISTS idx_meetings_meeting_date  ON meetings(meeting_date);
CREATE INDEX IF NOT EXISTS idx_meetings_host_email    ON meetings(host_email);
CREATE INDEX IF NOT EXISTS idx_state_log_meeting      ON state_log(meeting_id);
"""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/pytest.exe tests/core/test_manifest.py --no-cov -v`
Expected: all tests PASS, including all 3 new tests AND every pre-existing test (regression check — adding columns must not break existing functionality).

- [ ] **Step 5: Commit**

```bash
git add firefliesclearer/core/manifest.py tests/core/test_manifest.py
git commit -m "feat(manifest): extend meetings schema with snapshot columns

Adds duration_minutes, host_email, participant_count, has_transcript,
tags_json, source_state ('live'/'gone'), and cached_at columns to the
meetings table. Adds three new indexes (source_state, meeting_date,
host_email). Fresh manifests created via Manifest.open() get the new
shape; existing manifests are migrated in the next task."
```

---

### Task 3: Idempotent migration for existing manifests (ALTER TABLE ADD COLUMN)

**Files:**
- Modify: `firefliesclearer/core/manifest.py` — extend `Manifest.open()` and add a private `_migrate_meetings_columns()` helper
- Test: `tests/core/test_manifest.py`

Existing v2 installs have a `meetings` table without the new columns. SQLite's `ALTER TABLE ADD COLUMN` has no `IF NOT EXISTS` clause, so we must check `PRAGMA table_info` first.

- [ ] **Step 1: Write the failing tests**

Add to `tests/core/test_manifest.py`:

```python
def _legacy_v2_schema() -> str:
    """The pre-Phase-1 SCHEMA, used to simulate an existing manifest."""
    return """
    CREATE TABLE meetings (
      meeting_id        TEXT PRIMARY KEY,
      title             TEXT NOT NULL,
      meeting_date      TEXT NOT NULL,
      state             TEXT NOT NULL,
      archive_path      TEXT,
      audio_sha256      TEXT,
      summary_sha256    TEXT,
      transcript_sha256 TEXT,
      archived_at       TEXT,
      verified_at       TEXT,
      deleted_at        TEXT,
      last_error        TEXT
    );
    CREATE TABLE state_log (
      id            INTEGER PRIMARY KEY,
      meeting_id    TEXT NOT NULL,
      from_state    TEXT,
      to_state      TEXT NOT NULL,
      at            TEXT NOT NULL,
      details       TEXT
    );
    """


def test_migration_adds_snapshot_columns_to_existing_manifest(tmp_path):
    import sqlite3

    db_path = tmp_path / "manifest.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_legacy_v2_schema())
    conn.execute(
        "INSERT INTO meetings (meeting_id, title, meeting_date, state) VALUES (?, ?, ?, ?)",
        ("legacy-1", "Old Standup", "2026-01-01T00:00:00+00:00", "archived"),
    )
    conn.commit()
    conn.close()

    Manifest.open(db_path)  # should migrate, not raise

    conn = sqlite3.connect(str(db_path))
    cols = {row[1] for row in conn.execute("PRAGMA table_info(meetings)")}
    row = conn.execute(
        "SELECT title, source_state, duration_minutes FROM meetings WHERE meeting_id = ?",
        ("legacy-1",),
    ).fetchone()
    conn.close()

    assert {"duration_minutes", "host_email", "source_state", "cached_at"} <= cols
    assert row[0] == "Old Standup"        # legacy data preserved
    assert row[1] == "live"               # source_state backfilled by DEFAULT clause
    assert row[2] is None                 # legacy row has no duration


def test_migration_is_idempotent(tmp_path):
    """Running Manifest.open twice on the same DB does not error."""
    db_path = tmp_path / "manifest.db"
    Manifest.open(db_path)
    Manifest.open(db_path)  # second open must succeed without raising
    Manifest.open(db_path)  # and a third


def test_migration_preserves_existing_indexes(tmp_path):
    """The legacy idx_meetings_state index must survive migration."""
    import sqlite3

    db_path = tmp_path / "manifest.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_legacy_v2_schema())
    conn.execute("CREATE INDEX idx_meetings_state ON meetings(state)")
    conn.commit()
    conn.close()

    Manifest.open(db_path)

    conn = sqlite3.connect(str(db_path))
    indexes = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    conn.close()
    assert "idx_meetings_state" in indexes
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/pytest.exe tests/core/test_manifest.py::test_migration_adds_snapshot_columns_to_existing_manifest tests/core/test_manifest.py::test_migration_is_idempotent tests/core/test_manifest.py::test_migration_preserves_existing_indexes --no-cov -v`
Expected: at least the migration test FAILs because `Manifest.open()` calls `executescript(SCHEMA)` which contains `CREATE TABLE IF NOT EXISTS` — the legacy table already exists, so the new columns are NEVER added by SCHEMA alone. The migration helper has to bridge the gap.

- [ ] **Step 3: Add migration helper + invoke from `Manifest.open()`**

In `firefliesclearer/core/manifest.py`, add a private module-level helper after the `SCHEMA` constant and before the `IllegalStateTransition` class:

```python
def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(row[1] == column for row in rows)


def _migrate_meetings_columns(conn: sqlite3.Connection) -> None:
    """Add Phase 1 snapshot columns to a legacy meetings table.

    Idempotent: safe to call on a fresh DB (where columns already exist
    via SCHEMA) or on an existing DB without the columns.
    """
    additions = [
        ("duration_minutes",  "REAL"),
        ("host_email",        "TEXT"),
        ("participant_count", "INTEGER"),
        ("has_transcript",    "INTEGER"),
        ("tags_json",         "TEXT"),
        ("source_state",      "TEXT NOT NULL DEFAULT 'live'"),
        ("cached_at",         "TEXT"),
    ]
    for col_name, col_def in additions:
        if not _has_column(conn, "meetings", col_name):
            conn.execute(f"ALTER TABLE meetings ADD COLUMN {col_name} {col_def}")
    # Indexes use IF NOT EXISTS so they are safe to re-run, but SCHEMA
    # only runs them on fresh DBs (executescript on existing DBs is a
    # no-op for existing CREATEs but we want explicit idempotency here).
    conn.execute("CREATE INDEX IF NOT EXISTS idx_meetings_source_state ON meetings(source_state)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_meetings_meeting_date ON meetings(meeting_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_meetings_host_email   ON meetings(host_email)")
```

In the same file, modify the `Manifest.open()` classmethod from:

```python
@classmethod
def open(cls, path: Path) -> Manifest:
    path.parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False: required because the connection is cached on
    # app.state and reused across requests. Under uvicorn this is the same
    # event-loop thread, but background tasks (SSE op runners) and the
    # FastAPI threadpool can dispatch sync work from a different thread.
    # Async route handlers and ops are serialized by the event loop, so
    # there is no concurrent access to guard against; WAL mode covers any
    # incidental reader/writer overlap.
    conn = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    return cls(conn)
```

To:

```python
@classmethod
def open(cls, path: Path) -> Manifest:
    path.parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False: required because the connection is cached on
    # app.state and reused across requests. Under uvicorn this is the same
    # event-loop thread, but background tasks (SSE op runners) and the
    # FastAPI threadpool can dispatch sync work from a different thread.
    # Async route handlers and ops are serialized by the event loop, so
    # there is no concurrent access to guard against; WAL mode covers any
    # incidental reader/writer overlap.
    conn = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    _migrate_meetings_columns(conn)
    return cls(conn)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/pytest.exe tests/core/test_manifest.py --no-cov -v`
Expected: all tests PASS, including the new migration tests AND every pre-existing test.

- [ ] **Step 5: Commit**

```bash
git add firefliesclearer/core/manifest.py tests/core/test_manifest.py
git commit -m "feat(manifest): idempotent migration for snapshot columns

Adds _has_column / _migrate_meetings_columns helpers and wires them
into Manifest.open(). Existing v2 installs get the new columns via
ALTER TABLE without losing any data; the source_state column DEFAULT
clause backfills 'live' for every legacy row in one step."
```

---

### Task 4: Extend `MeetingRecord` dataclass + update `Manifest.get()` to read new columns

**Files:**
- Modify: `firefliesclearer/core/manifest.py` (the `MeetingRecord` dataclass + `Manifest.get()` method)
- Test: `tests/core/test_manifest.py`

`MeetingRecord` currently has 12 fields. We add the snapshot fields and `source_state` / `cached_at`. All new fields are nullable so legacy rows still construct.

- [ ] **Step 1: Write the failing tests**

Add to `tests/core/test_manifest.py`:

```python
def test_meeting_record_has_snapshot_fields(manifest):
    """MeetingRecord exposes snapshot + source_state fields read from the row."""
    manifest.register(_meeting(), at=NOW)
    rec = manifest.get("01HW")
    assert rec is not None
    # New attributes exist
    assert hasattr(rec, "duration_minutes")
    assert hasattr(rec, "host_email")
    assert hasattr(rec, "participant_count")
    assert hasattr(rec, "has_transcript")
    assert hasattr(rec, "tags")
    assert hasattr(rec, "source_state")
    assert hasattr(rec, "cached_at")


def test_meeting_record_legacy_register_has_null_snapshot(manifest):
    """register() still inserts only legacy columns; snapshot fields are None."""
    manifest.register(_meeting(), at=NOW)
    rec = manifest.get("01HW")
    assert rec is not None
    assert rec.duration_minutes is None
    assert rec.host_email is None
    assert rec.tags is None
    # source_state defaults to 'live' via the column DEFAULT clause
    assert rec.source_state == "live"
    assert rec.cached_at is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/pytest.exe tests/core/test_manifest.py::test_meeting_record_has_snapshot_fields tests/core/test_manifest.py::test_meeting_record_legacy_register_has_null_snapshot --no-cov -v`
Expected: 2 FAILs — `MeetingRecord` doesn't have the new fields yet.

- [ ] **Step 3: Extend `MeetingRecord` dataclass and `Manifest.get()`**

In `firefliesclearer/core/manifest.py`, replace the existing `MeetingRecord` definition:

```python
@dataclass(frozen=True, slots=True)
class MeetingRecord:
    meeting_id: str
    title: str
    meeting_date: datetime
    state: MeetingState
    archive_path: str | None
    audio_sha256: str | None
    summary_sha256: str | None
    transcript_sha256: str | None
    archived_at: datetime | None
    verified_at: datetime | None
    deleted_at: datetime | None
    last_error: str | None
```

With:

```python
@dataclass(frozen=True, slots=True)
class MeetingRecord:
    meeting_id: str
    title: str
    meeting_date: datetime
    state: MeetingState
    archive_path: str | None
    audio_sha256: str | None
    summary_sha256: str | None
    transcript_sha256: str | None
    archived_at: datetime | None
    verified_at: datetime | None
    deleted_at: datetime | None
    last_error: str | None
    # Phase 1 snapshot fields — populated by sync (upsert_known); None for
    # rows inserted via the legacy register() path until a sync touches them.
    duration_minutes: float | None = None
    host_email: str | None = None
    participant_count: int | None = None
    has_transcript: bool | None = None
    tags: tuple[str, ...] | None = None
    source_state: str = "live"
    cached_at: datetime | None = None
```

Also in `firefliesclearer/core/manifest.py`, replace the `Manifest.get()` method body. The current SQL is:

```python
def get(self, meeting_id: str) -> MeetingRecord | None:
    cur = self._conn.execute(
        "SELECT meeting_id, title, meeting_date, state, archive_path, "
        "audio_sha256, summary_sha256, transcript_sha256, archived_at, "
        "verified_at, deleted_at, last_error FROM meetings WHERE meeting_id = ?",
        (meeting_id,),
    )
    row = cur.fetchone()
    if row is None:
        return None
    meeting_date = _parse_iso(row[2])
    assert meeting_date is not None
    return MeetingRecord(
        meeting_id=row[0],
        title=row[1],
        meeting_date=meeting_date,
        state=MeetingState(row[3]),
        archive_path=row[4],
        audio_sha256=row[5],
        summary_sha256=row[6],
        transcript_sha256=row[7],
        archived_at=_parse_iso(row[8]),
        verified_at=_parse_iso(row[9]),
        deleted_at=_parse_iso(row[10]),
        last_error=row[11],
    )
```

Replace with:

```python
def get(self, meeting_id: str) -> MeetingRecord | None:
    cur = self._conn.execute(
        "SELECT meeting_id, title, meeting_date, state, archive_path, "
        "audio_sha256, summary_sha256, transcript_sha256, archived_at, "
        "verified_at, deleted_at, last_error, "
        "duration_minutes, host_email, participant_count, has_transcript, "
        "tags_json, source_state, cached_at "
        "FROM meetings WHERE meeting_id = ?",
        (meeting_id,),
    )
    row = cur.fetchone()
    if row is None:
        return None
    meeting_date = _parse_iso(row[2])
    assert meeting_date is not None
    tags_raw = row[16]
    tags = tuple(json.loads(tags_raw)) if tags_raw else None
    return MeetingRecord(
        meeting_id=row[0],
        title=row[1],
        meeting_date=meeting_date,
        state=MeetingState(row[3]),
        archive_path=row[4],
        audio_sha256=row[5],
        summary_sha256=row[6],
        transcript_sha256=row[7],
        archived_at=_parse_iso(row[8]),
        verified_at=_parse_iso(row[9]),
        deleted_at=_parse_iso(row[10]),
        last_error=row[11],
        duration_minutes=row[12],
        host_email=row[13],
        participant_count=row[14],
        has_transcript=bool(row[15]) if row[15] is not None else None,
        tags=tags,
        source_state=row[17] if row[17] is not None else "live",
        cached_at=_parse_iso(row[18]),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/pytest.exe tests/core/test_manifest.py --no-cov -v`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add firefliesclearer/core/manifest.py tests/core/test_manifest.py
git commit -m "feat(manifest): MeetingRecord exposes snapshot fields

Extends the MeetingRecord dataclass with duration_minutes, host_email,
participant_count, has_transcript, tags, source_state, cached_at — all
optional with sensible defaults so legacy rows still construct. Manifest.get
reads the new columns; tags_json deserialised back to a tuple."
```

---

### Task 5: Add `sync_runs` table to SCHEMA

**Files:**
- Modify: `firefliesclearer/core/manifest.py` (extend `SCHEMA` constant)
- Test: `tests/core/test_manifest.py`

The `sync_runs` table tracks each sync invocation. Phase 1 just creates it; Phase 2 populates it.

- [ ] **Step 1: Write the failing tests**

Add to `tests/core/test_manifest.py`:

```python
def test_sync_runs_table_exists(tmp_path):
    import sqlite3

    Manifest.open(tmp_path / "manifest.db")
    conn = sqlite3.connect(str(tmp_path / "manifest.db"))
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='sync_runs'"
    ).fetchone()
    conn.close()
    assert row is not None


def test_sync_runs_columns(tmp_path):
    import sqlite3

    Manifest.open(tmp_path / "manifest.db")
    conn = sqlite3.connect(str(tmp_path / "manifest.db"))
    cols = {row[1] for row in conn.execute("PRAGMA table_info(sync_runs)")}
    conn.close()
    expected = {
        "id", "mode", "trigger_source", "started_at", "finished_at", "outcome",
        "meetings_seen", "meetings_added", "meetings_updated", "meetings_gone",
        "cursor_skip", "seen_ids_json", "next_resume_at", "error_message",
    }
    assert expected <= cols, f"missing: {expected - cols}"


def test_sync_runs_started_at_index_exists(tmp_path):
    import sqlite3

    Manifest.open(tmp_path / "manifest.db")
    conn = sqlite3.connect(str(tmp_path / "manifest.db"))
    indexes = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    conn.close()
    assert "idx_sync_runs_started_at" in indexes


def test_sync_runs_table_created_on_existing_manifest(tmp_path):
    """Migration creates sync_runs even when meetings table predates Phase 1."""
    import sqlite3

    db_path = tmp_path / "manifest.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_legacy_v2_schema())
    conn.commit()
    conn.close()

    Manifest.open(db_path)

    conn = sqlite3.connect(str(db_path))
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='sync_runs'"
    ).fetchone()
    conn.close()
    assert row is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/pytest.exe tests/core/test_manifest.py::test_sync_runs_table_exists tests/core/test_manifest.py::test_sync_runs_columns tests/core/test_manifest.py::test_sync_runs_started_at_index_exists tests/core/test_manifest.py::test_sync_runs_table_created_on_existing_manifest --no-cov -v`
Expected: 4 FAILs — table doesn't exist yet.

- [ ] **Step 3: Add `sync_runs` to SCHEMA**

In `firefliesclearer/core/manifest.py`, append the new table + index to the `SCHEMA` constant. Replace:

```python
SCHEMA = """
CREATE TABLE IF NOT EXISTS meetings (
  ...
);
CREATE TABLE IF NOT EXISTS state_log (
  ...
);
CREATE INDEX IF NOT EXISTS idx_meetings_state         ON meetings(state);
CREATE INDEX IF NOT EXISTS idx_meetings_source_state  ON meetings(source_state);
CREATE INDEX IF NOT EXISTS idx_meetings_meeting_date  ON meetings(meeting_date);
CREATE INDEX IF NOT EXISTS idx_meetings_host_email    ON meetings(host_email);
CREATE INDEX IF NOT EXISTS idx_state_log_meeting      ON state_log(meeting_id);
"""
```

With (only the trailing block changes — keep the `meetings` and `state_log` definitions exactly as they are after Task 2):

```python
SCHEMA = """
CREATE TABLE IF NOT EXISTS meetings (
  meeting_id        TEXT PRIMARY KEY,
  title             TEXT NOT NULL,
  meeting_date      TEXT NOT NULL,
  state             TEXT NOT NULL,
  archive_path      TEXT,
  audio_sha256      TEXT,
  summary_sha256    TEXT,
  transcript_sha256 TEXT,
  archived_at       TEXT,
  verified_at       TEXT,
  deleted_at        TEXT,
  last_error        TEXT,
  duration_minutes  REAL,
  host_email        TEXT,
  participant_count INTEGER,
  has_transcript    INTEGER,
  tags_json         TEXT,
  source_state      TEXT NOT NULL DEFAULT 'live',
  cached_at         TEXT
);
CREATE TABLE IF NOT EXISTS state_log (
  id            INTEGER PRIMARY KEY,
  meeting_id    TEXT NOT NULL,
  from_state    TEXT,
  to_state      TEXT NOT NULL,
  at            TEXT NOT NULL,
  details       TEXT,
  FOREIGN KEY (meeting_id) REFERENCES meetings(meeting_id)
);
CREATE TABLE IF NOT EXISTS sync_runs (
  id                INTEGER PRIMARY KEY,
  mode              TEXT    NOT NULL,
  trigger_source    TEXT    NOT NULL,
  started_at        TEXT    NOT NULL,
  finished_at       TEXT,
  outcome           TEXT    NOT NULL,
  meetings_seen     INTEGER NOT NULL DEFAULT 0,
  meetings_added    INTEGER NOT NULL DEFAULT 0,
  meetings_updated  INTEGER NOT NULL DEFAULT 0,
  meetings_gone     INTEGER NOT NULL DEFAULT 0,
  cursor_skip       INTEGER,
  seen_ids_json     TEXT,
  next_resume_at    TEXT,
  error_message     TEXT
);
CREATE INDEX IF NOT EXISTS idx_meetings_state         ON meetings(state);
CREATE INDEX IF NOT EXISTS idx_meetings_source_state  ON meetings(source_state);
CREATE INDEX IF NOT EXISTS idx_meetings_meeting_date  ON meetings(meeting_date);
CREATE INDEX IF NOT EXISTS idx_meetings_host_email    ON meetings(host_email);
CREATE INDEX IF NOT EXISTS idx_state_log_meeting      ON state_log(meeting_id);
CREATE INDEX IF NOT EXISTS idx_sync_runs_started_at   ON sync_runs(started_at DESC);
"""
```

The `executescript(SCHEMA)` call in `Manifest.open()` runs this on fresh AND existing DBs; `CREATE TABLE IF NOT EXISTS` and `CREATE INDEX IF NOT EXISTS` are idempotent so existing manifests get the new table without a separate migration step.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/pytest.exe tests/core/test_manifest.py --no-cov -v`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add firefliesclearer/core/manifest.py tests/core/test_manifest.py
git commit -m "feat(manifest): add sync_runs table for sync bookkeeping

Tracks each sync invocation with mode, trigger, timing, counts, and
resume cursor for partial runs. Created via SCHEMA executescript so
fresh and existing manifests both get the table on next open."
```

---

### Task 6: Add `Manifest.upsert_known()`

**Files:**
- Modify: `firefliesclearer/core/manifest.py` (new method on `Manifest`)
- Test: `tests/core/test_manifest.py`

Sync calls this for every meeting it sees. Idempotent: creates the row if absent (`state=KNOWN`); if present, refreshes snapshot fields and `cached_at` but does NOT touch `state`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/core/test_manifest.py`:

```python
def _meeting_with_full_snapshot(meeting_id: str = "01HW") -> Meeting:
    return Meeting(
        meeting_id=meeting_id,
        title="Q4 Planning",
        meeting_date=NOW,
        duration_minutes=45.5,
        host_email="alice@example.com",
        participant_count=6,
        tags=("planning", "q4"),
        has_transcript=True,
    )


def test_upsert_known_inserts_new_row_in_known_state(manifest):
    manifest.upsert_known(_meeting_with_full_snapshot(), at=NOW)
    rec = manifest.get("01HW")
    assert rec is not None
    assert rec.state is MeetingState.KNOWN
    assert rec.title == "Q4 Planning"
    assert rec.duration_minutes == 45.5
    assert rec.host_email == "alice@example.com"
    assert rec.participant_count == 6
    assert rec.has_transcript is True
    assert rec.tags == ("planning", "q4")
    assert rec.source_state == "live"
    assert rec.cached_at == NOW


def test_upsert_known_writes_state_log_entry_for_new_row(manifest):
    manifest.upsert_known(_meeting_with_full_snapshot(), at=NOW)
    log = manifest.state_log("01HW")
    assert len(log) == 1
    assert log[0].from_state is None
    assert log[0].to_state is MeetingState.KNOWN


def test_upsert_known_refreshes_snapshot_on_existing_row_without_touching_state(manifest):
    """If the row already exists in any state, snapshot fields update; state does not."""
    # Initial insert as KNOWN
    manifest.upsert_known(_meeting_with_full_snapshot(), at=NOW)
    # User then archives — moves to PENDING then ARCHIVED
    manifest.transition("01HW", to=MeetingState.PENDING, at=NOW)
    manifest.transition("01HW", to=MeetingState.ARCHIVED, at=NOW, archive_path="/tmp/x")

    # Sync runs again, title was edited in Fireflies
    later = datetime(2026, 5, 10, 8, 0, tzinfo=UTC)
    edited = Meeting(
        meeting_id="01HW",
        title="Q4 Planning (rescheduled)",
        meeting_date=NOW,
        duration_minutes=60.0,
        host_email="alice@example.com",
        participant_count=8,
        tags=("planning", "q4", "rescheduled"),
        has_transcript=True,
    )
    manifest.upsert_known(edited, at=later)

    rec = manifest.get("01HW")
    assert rec is not None
    assert rec.state is MeetingState.ARCHIVED          # state preserved
    assert rec.title == "Q4 Planning (rescheduled)"    # snapshot updated
    assert rec.duration_minutes == 60.0
    assert rec.participant_count == 8
    assert rec.tags == ("planning", "q4", "rescheduled")
    assert rec.cached_at == later                      # cached_at refreshed


def test_upsert_known_does_not_log_for_existing_row(manifest):
    """No state transition happens when refreshing an existing row."""
    manifest.upsert_known(_meeting_with_full_snapshot(), at=NOW)
    later = datetime(2026, 5, 10, 8, 0, tzinfo=UTC)
    manifest.upsert_known(_meeting_with_full_snapshot(), at=later)

    log = manifest.state_log("01HW")
    assert len(log) == 1                                # still just the initial KNOWN log
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/pytest.exe tests/core/test_manifest.py::test_upsert_known_inserts_new_row_in_known_state tests/core/test_manifest.py::test_upsert_known_writes_state_log_entry_for_new_row tests/core/test_manifest.py::test_upsert_known_refreshes_snapshot_on_existing_row_without_touching_state tests/core/test_manifest.py::test_upsert_known_does_not_log_for_existing_row --no-cov -v`
Expected: 4 FAILs — `Manifest` has no `upsert_known` method.

- [ ] **Step 3: Implement `Manifest.upsert_known()`**

In `firefliesclearer/core/manifest.py`, add the new method to the `Manifest` class. Place it directly after the existing `register()` method (around line 130):

```python
def upsert_known(self, meeting: Meeting, *, at: datetime) -> None:
    """Cache a meeting from the live API.

    If the row does not exist, INSERT with state=KNOWN and a state_log
    entry. If it exists, refresh snapshot fields and ``cached_at`` but
    leave ``state`` untouched (so an already-archived meeting whose title
    was edited in Fireflies retains state=ARCHIVED while the cached title
    updates).
    """
    tags_json = json.dumps(list(meeting.tags))
    has_transcript = 1 if meeting.has_transcript else 0
    cached_at_iso = _iso(at)

    existing = self.get(meeting.meeting_id)
    if existing is None:
        self._conn.execute(
            "INSERT INTO meetings ("
            "  meeting_id, title, meeting_date, state, "
            "  duration_minutes, host_email, participant_count, has_transcript, "
            "  tags_json, source_state, cached_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                meeting.meeting_id,
                meeting.title,
                meeting.meeting_date.isoformat(),
                MeetingState.KNOWN.value,
                meeting.duration_minutes,
                meeting.host_email,
                meeting.participant_count,
                has_transcript,
                tags_json,
                "live",
                cached_at_iso,
            ),
        )
        self._log(meeting.meeting_id, None, MeetingState.KNOWN, at, None)
        return

    # Existing row — refresh snapshot fields, do NOT touch state.
    self._conn.execute(
        "UPDATE meetings SET "
        "  title = ?, meeting_date = ?, "
        "  duration_minutes = ?, host_email = ?, participant_count = ?, "
        "  has_transcript = ?, tags_json = ?, cached_at = ? "
        "WHERE meeting_id = ?",
        (
            meeting.title,
            meeting.meeting_date.isoformat(),
            meeting.duration_minutes,
            meeting.host_email,
            meeting.participant_count,
            has_transcript,
            tags_json,
            cached_at_iso,
            meeting.meeting_id,
        ),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/pytest.exe tests/core/test_manifest.py --no-cov -v`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add firefliesclearer/core/manifest.py tests/core/test_manifest.py
git commit -m "feat(manifest): Manifest.upsert_known for sync ingestion

Idempotent insert/update for sync passes. New rows land in KNOWN with
a state_log entry; existing rows refresh snapshot + cached_at without
touching state, so archived meetings whose Fireflies metadata was
edited keep their op_state while the cache reflects the latest title."
```

---

### Task 7: Add `Manifest.set_source_state()`

**Files:**
- Modify: `firefliesclearer/core/manifest.py`
- Test: `tests/core/test_manifest.py`

Used by sync (full reconciliation) when a row's meeting is missing from the API response, and by `purge_one` after a successful API delete.

- [ ] **Step 1: Write the failing tests**

Add to `tests/core/test_manifest.py`:

```python
def test_set_source_state_flips_live_to_gone(manifest):
    manifest.upsert_known(_meeting_with_full_snapshot(), at=NOW)
    manifest.set_source_state("01HW", "gone")
    rec = manifest.get("01HW")
    assert rec is not None
    assert rec.source_state == "gone"


def test_set_source_state_flips_gone_to_live(manifest):
    """Resurrection edge case: a 'gone' meeting reappears in Fireflies."""
    manifest.upsert_known(_meeting_with_full_snapshot(), at=NOW)
    manifest.set_source_state("01HW", "gone")
    manifest.set_source_state("01HW", "live")
    rec = manifest.get("01HW")
    assert rec is not None
    assert rec.source_state == "live"


def test_set_source_state_leaves_op_state_unchanged(manifest):
    """source_state and op_state are independent axes."""
    manifest.upsert_known(_meeting_with_full_snapshot(), at=NOW)
    manifest.transition("01HW", to=MeetingState.PENDING, at=NOW)
    manifest.transition("01HW", to=MeetingState.ARCHIVED, at=NOW, archive_path="/tmp/x")
    manifest.set_source_state("01HW", "gone")
    rec = manifest.get("01HW")
    assert rec is not None
    assert rec.state is MeetingState.ARCHIVED
    assert rec.source_state == "gone"


def test_set_source_state_unknown_id_is_noop(manifest):
    """Setting source_state on a non-existent row is silently ignored."""
    manifest.set_source_state("does-not-exist", "gone")
    assert manifest.get("does-not-exist") is None


def test_set_source_state_rejects_invalid_value(manifest):
    manifest.upsert_known(_meeting_with_full_snapshot(), at=NOW)
    with pytest.raises(ValueError, match="source_state"):
        manifest.set_source_state("01HW", "bogus")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/pytest.exe tests/core/test_manifest.py::test_set_source_state_flips_live_to_gone tests/core/test_manifest.py::test_set_source_state_flips_gone_to_live tests/core/test_manifest.py::test_set_source_state_leaves_op_state_unchanged tests/core/test_manifest.py::test_set_source_state_unknown_id_is_noop tests/core/test_manifest.py::test_set_source_state_rejects_invalid_value --no-cov -v`
Expected: 5 FAILs — method does not exist.

- [ ] **Step 3: Implement `Manifest.set_source_state()`**

In `firefliesclearer/core/manifest.py`, add after `upsert_known()`:

```python
_VALID_SOURCE_STATES: frozenset[str] = frozenset({"live", "gone"})


def set_source_state(self, meeting_id: str, source_state: str) -> None:
    """Flip a row's source_state. No-op for unknown meeting_ids.

    source_state must be 'live' or 'gone' (the two values reachable from
    the sync engine's reconciliation pass and the purge pipeline).
    """
    if source_state not in _VALID_SOURCE_STATES:
        raise ValueError(
            f"source_state must be one of {sorted(_VALID_SOURCE_STATES)}; got {source_state!r}"
        )
    self._conn.execute(
        "UPDATE meetings SET source_state = ? WHERE meeting_id = ?",
        (source_state, meeting_id),
    )
```

Note: `_VALID_SOURCE_STATES` is module-level (placed near `LEGAL_TRANSITIONS` constant — adjust the file order accordingly so the constant is defined before it's referenced inside the method).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/pytest.exe tests/core/test_manifest.py --no-cov -v`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add firefliesclearer/core/manifest.py tests/core/test_manifest.py
git commit -m "feat(manifest): Manifest.set_source_state for live/gone flip

Used by full reconciliation (mark missing rows 'gone') and the purge
pipeline (mark just-deleted meetings 'gone' in the same transaction
as the op_state -> DELETED transition). Independent of op_state."
```

---

### Task 8: Add `Manifest.update_cache_fields()`

**Files:**
- Modify: `firefliesclearer/core/manifest.py`
- Test: `tests/core/test_manifest.py`

Returns `True` if any cached field actually changed — used by full reconciliation to count `meetings_updated`. Distinguishes "we touched the row" from "we observed a real change".

- [ ] **Step 1: Write the failing tests**

Add to `tests/core/test_manifest.py`:

```python
def test_update_cache_fields_returns_false_when_nothing_changed(manifest):
    manifest.upsert_known(_meeting_with_full_snapshot(), at=NOW)
    later = datetime(2026, 5, 10, 8, 0, tzinfo=UTC)
    changed = manifest.update_cache_fields(_meeting_with_full_snapshot(), at=later)
    assert changed is False


def test_update_cache_fields_refreshes_cached_at_even_when_no_change(manifest):
    manifest.upsert_known(_meeting_with_full_snapshot(), at=NOW)
    later = datetime(2026, 5, 10, 8, 0, tzinfo=UTC)
    manifest.update_cache_fields(_meeting_with_full_snapshot(), at=later)
    rec = manifest.get("01HW")
    assert rec is not None
    assert rec.cached_at == later


def test_update_cache_fields_returns_true_when_title_changed(manifest):
    manifest.upsert_known(_meeting_with_full_snapshot(), at=NOW)
    edited = Meeting(
        meeting_id="01HW",
        title="Q4 Planning (rescheduled)",
        meeting_date=NOW,
        duration_minutes=45.5,
        host_email="alice@example.com",
        participant_count=6,
        tags=("planning", "q4"),
        has_transcript=True,
    )
    later = datetime(2026, 5, 10, 8, 0, tzinfo=UTC)
    changed = manifest.update_cache_fields(edited, at=later)
    assert changed is True
    rec = manifest.get("01HW")
    assert rec is not None
    assert rec.title == "Q4 Planning (rescheduled)"


def test_update_cache_fields_returns_true_when_tags_changed(manifest):
    manifest.upsert_known(_meeting_with_full_snapshot(), at=NOW)
    edited = Meeting(
        meeting_id="01HW",
        title="Q4 Planning",
        meeting_date=NOW,
        duration_minutes=45.5,
        host_email="alice@example.com",
        participant_count=6,
        tags=("planning", "q4", "added-tag"),
        has_transcript=True,
    )
    later = datetime(2026, 5, 10, 8, 0, tzinfo=UTC)
    assert manifest.update_cache_fields(edited, at=later) is True


def test_update_cache_fields_does_not_touch_op_state(manifest):
    manifest.upsert_known(_meeting_with_full_snapshot(), at=NOW)
    manifest.transition("01HW", to=MeetingState.PENDING, at=NOW)
    edited = Meeting(
        meeting_id="01HW",
        title="renamed",
        meeting_date=NOW,
        duration_minutes=45.5,
        host_email="alice@example.com",
        participant_count=6,
        tags=("planning", "q4"),
        has_transcript=True,
    )
    manifest.update_cache_fields(edited, at=NOW)
    rec = manifest.get("01HW")
    assert rec is not None
    assert rec.state is MeetingState.PENDING


def test_update_cache_fields_unknown_id_returns_false(manifest):
    edited = _meeting_with_full_snapshot("not-in-db")
    assert manifest.update_cache_fields(edited, at=NOW) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/pytest.exe tests/core/test_manifest.py::test_update_cache_fields_returns_false_when_nothing_changed tests/core/test_manifest.py::test_update_cache_fields_refreshes_cached_at_even_when_no_change tests/core/test_manifest.py::test_update_cache_fields_returns_true_when_title_changed tests/core/test_manifest.py::test_update_cache_fields_returns_true_when_tags_changed tests/core/test_manifest.py::test_update_cache_fields_does_not_touch_op_state tests/core/test_manifest.py::test_update_cache_fields_unknown_id_returns_false --no-cov -v`
Expected: 6 FAILs — method does not exist.

- [ ] **Step 3: Implement `Manifest.update_cache_fields()`**

In `firefliesclearer/core/manifest.py`, add after `set_source_state()`:

```python
def update_cache_fields(self, meeting: Meeting, *, at: datetime) -> bool:
    """Refresh a row's snapshot columns. Returns True if any field changed.

    Always updates ``cached_at`` to *at*, even when no other field changed,
    so the freshness timestamp reflects the most recent sync touch. The
    returned bool tells full-reconciliation callers whether the row counts
    as ``meetings_updated`` (real change observed) or just ``meetings_seen``.

    Returns False if the meeting_id does not exist in the manifest.
    """
    existing = self.get(meeting.meeting_id)
    if existing is None:
        return False

    new_tags_json = json.dumps(list(meeting.tags))
    new_has_transcript = 1 if meeting.has_transcript else 0
    existing_tags_tuple = existing.tags or ()

    changed = (
        existing.title != meeting.title
        or existing.meeting_date != meeting.meeting_date
        or existing.duration_minutes != meeting.duration_minutes
        or existing.host_email != meeting.host_email
        or existing.participant_count != meeting.participant_count
        or (existing.has_transcript if existing.has_transcript is not None else None)
            != meeting.has_transcript
        or tuple(existing_tags_tuple) != tuple(meeting.tags)
    )

    self._conn.execute(
        "UPDATE meetings SET "
        "  title = ?, meeting_date = ?, "
        "  duration_minutes = ?, host_email = ?, participant_count = ?, "
        "  has_transcript = ?, tags_json = ?, cached_at = ? "
        "WHERE meeting_id = ?",
        (
            meeting.title,
            meeting.meeting_date.isoformat(),
            meeting.duration_minutes,
            meeting.host_email,
            meeting.participant_count,
            new_has_transcript,
            new_tags_json,
            _iso(at),
            meeting.meeting_id,
        ),
    )
    return changed
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/pytest.exe tests/core/test_manifest.py --no-cov -v`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add firefliesclearer/core/manifest.py tests/core/test_manifest.py
git commit -m "feat(manifest): Manifest.update_cache_fields with change detection

Refreshes snapshot columns + cached_at; returns True iff any tracked
field actually changed. Full reconciliation uses the bool to count
meetings_updated separately from meetings_seen."
```

---

### Task 9: Add `Manifest.list_known()`

**Files:**
- Modify: `firefliesclearer/core/manifest.py`
- Test: `tests/core/test_manifest.py`

Yields `Meeting` objects from cached rows for `ScanService` to filter (Phase 3 will switch the wizard to this). Phase 1 just lands the method; nothing else uses it yet.

- [ ] **Step 1: Write the failing tests**

Add to `tests/core/test_manifest.py`:

```python
def test_list_known_yields_known_rows(manifest):
    manifest.upsert_known(_meeting_with_full_snapshot("a"), at=NOW)
    manifest.upsert_known(_meeting_with_full_snapshot("b"), at=NOW)

    ids = sorted(m.meeting_id for m in manifest.list_known())
    assert ids == ["a", "b"]


def test_list_known_skips_archived_by_default(manifest):
    """Archived meetings are excluded unless explicitly requested."""
    manifest.upsert_known(_meeting_with_full_snapshot("a"), at=NOW)
    manifest.upsert_known(_meeting_with_full_snapshot("b"), at=NOW)
    manifest.transition("b", to=MeetingState.PENDING, at=NOW)
    manifest.transition("b", to=MeetingState.ARCHIVED, at=NOW)

    ids = sorted(m.meeting_id for m in manifest.list_known())
    assert ids == ["a"]


def test_list_known_includes_archived_when_flag_true(manifest):
    manifest.upsert_known(_meeting_with_full_snapshot("a"), at=NOW)
    manifest.upsert_known(_meeting_with_full_snapshot("b"), at=NOW)
    manifest.transition("b", to=MeetingState.PENDING, at=NOW)
    manifest.transition("b", to=MeetingState.ARCHIVED, at=NOW)

    ids = sorted(m.meeting_id for m in manifest.list_known(include_archived=True))
    assert ids == ["a", "b"]


def test_list_known_skips_gone_by_default(manifest):
    manifest.upsert_known(_meeting_with_full_snapshot("a"), at=NOW)
    manifest.upsert_known(_meeting_with_full_snapshot("b"), at=NOW)
    manifest.set_source_state("b", "gone")

    ids = sorted(m.meeting_id for m in manifest.list_known())
    assert ids == ["a"]


def test_list_known_includes_gone_when_flag_true(manifest):
    manifest.upsert_known(_meeting_with_full_snapshot("a"), at=NOW)
    manifest.upsert_known(_meeting_with_full_snapshot("b"), at=NOW)
    manifest.set_source_state("b", "gone")

    ids = sorted(m.meeting_id for m in manifest.list_known(include_gone=True))
    assert ids == ["a", "b"]


def test_list_known_filters_older_than(manifest):
    """older_than=cutoff yields rows whose meeting_date < cutoff."""
    older_dt = datetime(2025, 1, 1, tzinfo=UTC)
    newer_dt = datetime(2026, 6, 1, tzinfo=UTC)
    manifest.upsert_known(
        Meeting("old", "old", older_dt, 30.0, "a@x", 2, (), True), at=NOW
    )
    manifest.upsert_known(
        Meeting("new", "new", newer_dt, 30.0, "a@x", 2, (), True), at=NOW
    )

    cutoff = datetime(2026, 1, 1, tzinfo=UTC)
    ids = sorted(m.meeting_id for m in manifest.list_known(older_than=cutoff))
    assert ids == ["old"]


def test_list_known_returns_meeting_with_all_fields(manifest):
    manifest.upsert_known(_meeting_with_full_snapshot("a"), at=NOW)
    meetings = list(manifest.list_known())
    assert len(meetings) == 1
    m = meetings[0]
    assert m.meeting_id == "a"
    assert m.title == "Q4 Planning"
    assert m.duration_minutes == 45.5
    assert m.host_email == "alice@example.com"
    assert m.participant_count == 6
    assert m.has_transcript is True
    assert m.tags == ("planning", "q4")


def test_list_known_skips_legacy_rows_without_snapshot(manifest):
    """Rows from the legacy register() path lack snapshot columns.
    list_known cannot reconstruct a valid Meeting, so it skips them.
    Phase 3 deployment will rely on a full sync to populate these.
    """
    manifest.register(_meeting(), at=NOW)              # legacy entry
    manifest.upsert_known(_meeting_with_full_snapshot("synced"), at=NOW)

    ids = sorted(m.meeting_id for m in manifest.list_known())
    assert ids == ["synced"]


def test_list_known_empty_manifest_yields_nothing(manifest):
    assert list(manifest.list_known()) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/pytest.exe tests/core/test_manifest.py::test_list_known_yields_known_rows tests/core/test_manifest.py::test_list_known_skips_archived_by_default tests/core/test_manifest.py::test_list_known_includes_archived_when_flag_true tests/core/test_manifest.py::test_list_known_skips_gone_by_default tests/core/test_manifest.py::test_list_known_includes_gone_when_flag_true tests/core/test_manifest.py::test_list_known_filters_older_than tests/core/test_manifest.py::test_list_known_returns_meeting_with_all_fields tests/core/test_manifest.py::test_list_known_skips_legacy_rows_without_snapshot tests/core/test_manifest.py::test_list_known_empty_manifest_yields_nothing --no-cov -v`
Expected: 9 FAILs — method does not exist.

- [ ] **Step 3: Implement `Manifest.list_known()`**

In `firefliesclearer/core/manifest.py`, add the new method to the `Manifest` class. Place after `update_cache_fields()`:

```python
def list_known(
    self,
    *,
    older_than: datetime | None = None,
    include_archived: bool = False,
    include_gone: bool = False,
) -> Iterable[Meeting]:
    """Yield Meeting objects for cached rows matching the predicates.

    Default scope: live (source_state='live'), not-yet-archived rows. This
    is what the cleanup wizard wants — meetings still in Fireflies that
    we haven't already operated on.

    Predicates:
      - older_than: yield only rows with meeting_date < this datetime.
      - include_archived: also yield rows in op_state ARCHIVED / DELETED /
        DELETED_FAILED / FAILED_*.
      - include_gone: also yield rows with source_state='gone'.

    Rows lacking snapshot columns (legacy register() entries) are skipped:
    Meeting requires non-None duration_minutes, host_email, participant_count,
    has_transcript. Phase 3 deployments rely on a full sync to backfill
    snapshot fields for any such rows.
    """
    sql = (
        "SELECT meeting_id, title, meeting_date, duration_minutes, host_email, "
        "       participant_count, has_transcript, tags_json, state, source_state "
        "FROM meetings "
        "WHERE duration_minutes IS NOT NULL "
        "  AND host_email IS NOT NULL "
        "  AND participant_count IS NOT NULL "
        "  AND has_transcript IS NOT NULL"
    )
    params: list[Any] = []
    if older_than is not None:
        sql += " AND meeting_date < ?"
        params.append(_iso(older_than))
    if not include_archived:
        sql += (
            " AND state NOT IN ('archived', 'deleted', 'deleted_failed', "
            "'failed_fetch', 'failed_download', 'failed_render', 'failed_verify')"
        )
    if not include_gone:
        sql += " AND source_state = 'live'"
    sql += " ORDER BY meeting_date DESC"

    rows = self._conn.execute(sql, params).fetchall()
    for row in rows:
        meeting_date = _parse_iso(row[2])
        assert meeting_date is not None
        tags_raw = row[7]
        tags = tuple(json.loads(tags_raw)) if tags_raw else ()
        yield Meeting(
            meeting_id=row[0],
            title=row[1],
            meeting_date=meeting_date,
            duration_minutes=float(row[3]),
            host_email=row[4],
            participant_count=int(row[5]),
            tags=tags,
            has_transcript=bool(row[6]),
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/pytest.exe tests/core/test_manifest.py --no-cov -v`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add firefliesclearer/core/manifest.py tests/core/test_manifest.py
git commit -m "feat(manifest): Manifest.list_known iterator over cached rows

Yields Meeting objects for cached rows matching the predicates.
Default scope (live + not-yet-archived) is what the wizard wants.
Legacy rows without snapshot columns are skipped — Phase 3 sync
backfills them. Phase 1 closes here; Phase 2 builds SyncService
on top of these primitives."
```

---

### Task 10: Final verification

**Files:** none (read-only commands)

- [ ] **Step 1: Full pytest suite (regression check)**

Run: `.venv/Scripts/pytest.exe --no-cov -q`
Expected: every existing test still passes (the previous green count from `dd070f8 / 21f4cfd / 366feef` was 608 passed, 1 skipped). After Phase 1 the count grows by ~40 new tests (the ones added across tasks 1-9), so expect ~648 passed, 1 skipped.

If anything is RED beyond the new tests, do NOT push. Investigate, fix, then re-run.

- [ ] **Step 2: mypy strict**

Run: `.venv/Scripts/mypy.exe firefliesclearer`
Expected: `Success: no issues found in 59 source files` (or 59+ if a module count changed; the success line is what matters).

- [ ] **Step 3: ruff lint**

Run: `.venv/Scripts/ruff.exe check firefliesclearer tests`
Expected: `All checks passed!`

- [ ] **Step 4: ruff format check**

Run: `.venv/Scripts/ruff.exe format --check firefliesclearer tests`
Expected: `122 files already formatted` (count may vary; the absence of "Would reformat" lines is what matters).

- [ ] **Step 5: Coverage spot-check on `core/manifest.py`**

Run: `.venv/Scripts/pytest.exe tests/core/test_manifest.py --cov=firefliesclearer.core.manifest --cov-report=term-missing -q`
Expected: 100% coverage on `firefliesclearer/core/manifest.py` (per CLAUDE.md hard target). If lines are missing, write tests for them — don't ship below 100% on this file.

- [ ] **Step 6: No commit needed; this is the end-of-phase sign-off**

After all four checks are green, Phase 1 is complete. The next plan in this series is `2026-05-02-local-cache-phase-2-sync-engine.md` (to be written when Phase 1 lands).

---

## Self-review notes

**Spec coverage check:** Phase 1 of the spec calls for: ✅ schema columns + indexes (Tasks 2, 3, 5), ✅ `sync_runs` table (Task 5), ✅ `KNOWN` state + transitions (Task 1), ✅ `upsert_known` / `set_source_state` / `update_cache_fields` / `list_known` methods (Tasks 6-9), ✅ idempotent migration (Task 3), ✅ tests including idempotency, state-pair independence, and "no row ever DELETEd" implicit (the design uses no DROPs). All covered.

**Deviations from spec — noted intentionally:**
1. The spec lists `organizer_email` and `audio_url` as cached columns. Phase 1 omits them: the `Meeting` domain object has neither, and they aren't used by the rule engine. Re-add later if a use case appears; harmless to leave out now.
2. The spec mentions a Python-side rename of `state` → `op_state` on `MeetingRecord`. Phase 1 keeps `state` as the field name to avoid cascading rename pressure on every existing caller. Phase 6 (cleanup) can do this rename as a single PR if desired.
3. The "no row ever DELETEd" invariant is enforced architecturally (no `DELETE FROM meetings` exists in any added code path) rather than via a property test in Phase 1. A property-style test can land in Phase 2 once SyncService exists to drive the assertion meaningfully.

**Type / signature consistency check:** `upsert_known(meeting: Meeting, *, at: datetime)`, `set_source_state(meeting_id: str, source_state: str)`, `update_cache_fields(meeting: Meeting, *, at: datetime) -> bool`, `list_known(*, older_than=None, include_archived=False, include_gone=False) -> Iterable[Meeting]`. All four exactly match the spec's "Schema → state machine → new manifest methods" section. Tests use these signatures consistently.

**Placeholder scan:** No "TBD", "TODO", "implement later" — every step has full code. Some imports (e.g., `import sqlite3` inside test bodies) are intentional to keep the diffs minimal in `tests/core/test_manifest.py`.
