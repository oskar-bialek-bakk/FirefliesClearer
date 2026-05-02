"""SQLite-backed state machine and audit log."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from firefliesclearer.core.models import Meeting, MeetingState

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


class IllegalStateTransition(Exception):  # noqa: N818
    pass


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


@dataclass(frozen=True, slots=True)
class StateLogEntry:
    meeting_id: str
    from_state: MeetingState | None
    to_state: MeetingState
    at: datetime
    details: dict[str, Any] | None


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


def _parse_iso(text: str | None) -> datetime | None:
    return datetime.fromisoformat(text) if text else None


class Manifest:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

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
        # Migrate columns BEFORE executescript so the new indexes in SCHEMA
        # (idx_meetings_source_state, etc.) can reference columns that exist.
        # On a fresh DB the meetings table doesn't yet exist, so the helper
        # is a no-op (PRAGMA table_info returns no rows; ALTER TABLE skipped).
        # SCHEMA then creates the table with the new columns inline.
        if _has_column(conn, "meetings", "meeting_id"):
            _migrate_meetings_columns(conn)
        conn.executescript(SCHEMA)
        _migrate_meetings_columns(conn)
        return cls(conn)

    def close(self) -> None:
        self._conn.close()

    def register(self, meeting: Meeting, *, at: datetime) -> None:
        if self.get(meeting.meeting_id) is not None:
            return
        self._conn.execute(
            "INSERT INTO meetings (meeting_id, title, meeting_date, state) VALUES (?, ?, ?, ?)",
            (
                meeting.meeting_id,
                meeting.title,
                meeting.meeting_date.isoformat(),
                MeetingState.PENDING.value,
            ),
        )
        self._log(meeting.meeting_id, None, MeetingState.PENDING, at, None)

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

    def transition(
        self,
        meeting_id: str,
        *,
        to: MeetingState,
        at: datetime,
        archive_path: str | None = None,
        verified_at: datetime | None = None,
        sha256s: Mapping[str, str] | None = None,
        last_error: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        rec = self.get(meeting_id)
        from_state = rec.state if rec else None
        if to not in LEGAL_TRANSITIONS.get(from_state, frozenset()):
            raise IllegalStateTransition(f"{meeting_id}: cannot transition {from_state} -> {to}")
        sets: list[str] = ["state = ?"]
        vals: list[Any] = [to.value]
        if archive_path is not None:
            sets.append("archive_path = ?")
            vals.append(archive_path)
        if verified_at is not None:
            sets.append("verified_at = ?")
            vals.append(_iso(verified_at))
        if sha256s:
            for kind in ("audio", "summary", "transcript"):
                if kind in sha256s:
                    sets.append(f"{kind}_sha256 = ?")
                    vals.append(sha256s[kind])
        if to is MeetingState.ARCHIVED:
            sets.append("archived_at = ?")
            vals.append(_iso(at))
        if to is MeetingState.DELETED:
            sets.append("deleted_at = ?")
            vals.append(_iso(at))
        if last_error is not None:
            sets.append("last_error = ?")
            vals.append(last_error)
        vals.append(meeting_id)
        self._conn.execute(f"UPDATE meetings SET {', '.join(sets)} WHERE meeting_id = ?", vals)
        self._log(meeting_id, from_state, to, at, details)

    def state_log(self, meeting_id: str) -> list[StateLogEntry]:
        rows = self._conn.execute(
            "SELECT meeting_id, from_state, to_state, at, details "
            "FROM state_log WHERE meeting_id = ? ORDER BY id ASC",
            (meeting_id,),
        ).fetchall()
        return [
            StateLogEntry(
                meeting_id=mid,
                from_state=MeetingState(frm) if frm else None,
                to_state=MeetingState(to),
                at=datetime.fromisoformat(at),
                details=json.loads(details) if details else None,
            )
            for mid, frm, to, at, details in rows
        ]

    def history(self, *, year: int, month: int) -> list[MeetingRecord]:
        prefix = f"{year:04d}-{month:02d}"
        rows = self._conn.execute(
            "SELECT meeting_id FROM meetings WHERE state = 'deleted' AND deleted_at LIKE ?",
            (f"{prefix}%",),
        ).fetchall()
        result: list[MeetingRecord] = []
        for (mid,) in rows:
            rec = self.get(mid)
            if rec is not None:
                result.append(rec)
        return result

    def counts_by_state(self) -> dict[MeetingState, int]:
        rows = self._conn.execute("SELECT state, COUNT(*) FROM meetings GROUP BY state").fetchall()
        return {MeetingState(s): c for s, c in rows}

    def last_state_change_at(self) -> datetime | None:
        """Return the timestamp of the most recent state_log entry, or None."""
        row = self._conn.execute("SELECT MAX(at) FROM state_log").fetchone()
        if row is None or row[0] is None:
            return None
        return datetime.fromisoformat(row[0])

    def meeting_ids_in_states(self, states: Iterable[MeetingState]) -> list[str]:
        """Return meeting IDs whose current state is in *states*, ordered by meeting_id ASC."""
        state_list = list(states)
        if not state_list:
            return []
        placeholders = ", ".join("?" * len(state_list))
        rows = self._conn.execute(
            f"SELECT meeting_id FROM meetings WHERE state IN ({placeholders}) ORDER BY meeting_id ASC",
            [s.value for s in state_list],
        ).fetchall()
        return [row[0] for row in rows]

    def _build_history_where(
        self,
        *,
        states: list[MeetingState] | None,
        date_from: datetime | None,
        date_to: datetime | None,
        title_contains: str | None,
    ) -> tuple[str, list[Any]]:
        """Build a parameterized WHERE clause for history queries.

        Returns ``(clause, params)`` where ``clause`` is either an empty
        string or starts with ``WHERE``. Empty/None values are skipped so
        callers can pass any subset of filters.
        """
        fragments: list[str] = []
        params: list[Any] = []
        if states:
            placeholders = ", ".join("?" * len(states))
            fragments.append(f"state IN ({placeholders})")
            params.extend(s.value for s in states)
        if date_from is not None:
            fragments.append("deleted_at >= ?")
            params.append(_iso(date_from))
        if date_to is not None:
            fragments.append("deleted_at < ?")
            params.append(_iso(date_to))
        if title_contains:
            fragments.append("title LIKE ?")
            params.append(f"%{title_contains}%")
        clause = f"WHERE {' AND '.join(fragments)}" if fragments else ""
        return clause, params

    def query_history(
        self,
        *,
        states: Iterable[MeetingState] | None,
        date_from: datetime | None,
        date_to: datetime | None,
        title_contains: str | None,
        limit: int,
        offset: int,
    ) -> list[MeetingRecord]:
        """Flexible filtered query over meetings, ordered by deleted_at DESC."""
        state_list = list(states) if states is not None else None
        where_clause, params = self._build_history_where(
            states=state_list,
            date_from=date_from,
            date_to=date_to,
            title_contains=title_contains,
        )
        sql = (
            f"SELECT meeting_id FROM meetings {where_clause} "
            f"ORDER BY deleted_at DESC NULLS LAST, meeting_id ASC "
            f"LIMIT ? OFFSET ?"
        )
        params.extend([limit, offset])

        rows = self._conn.execute(sql, params).fetchall()
        result: list[MeetingRecord] = []
        for (mid,) in rows:
            rec = self.get(mid)
            if rec is not None:
                result.append(rec)
        return result

    def count_history(
        self,
        *,
        states: Iterable[MeetingState] | None,
        date_from: datetime | None,
        date_to: datetime | None,
        title_contains: str | None,
    ) -> int:
        """Return count of meetings matching the same WHERE conditions as query_history."""
        state_list = list(states) if states is not None else None
        where_clause, params = self._build_history_where(
            states=state_list,
            date_from=date_from,
            date_to=date_to,
            title_contains=title_contains,
        )
        sql = f"SELECT COUNT(*) FROM meetings {where_clause}"

        row = self._conn.execute(sql, params).fetchone()
        return int(row[0]) if row else 0

    def _log(
        self,
        meeting_id: str,
        from_state: MeetingState | None,
        to_state: MeetingState,
        at: datetime,
        details: dict[str, Any] | None,
    ) -> None:
        self._conn.execute(
            "INSERT INTO state_log (meeting_id, from_state, to_state, at, details) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                meeting_id,
                from_state.value if from_state else None,
                to_state.value,
                _iso(at),
                json.dumps(details) if details else None,
            ),
        )
