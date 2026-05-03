"""Async GraphQL client for Fireflies AI."""

from __future__ import annotations

import asyncio
import email.utils
import logging
import random
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import httpx

from firefliesclearer.core.models import ArtifactBundle, Meeting
from firefliesclearer.ports.meeting_repository import MeetingFilter

logger = logging.getLogger(__name__)

# Maximum time we'll voluntarily sleep on a Retry-After before giving up and
# raising. The Fireflies daily-quota retry-after is typically hours-to-half-a-day
# in the future; sleeping that long blocks the request handler with no
# user-visible feedback. Fail fast and let the caller render a friendly message.
_MAX_RETRY_AFTER_SECONDS = 60.0

LIST_QUERY = """
query Transcripts($limit: Int, $skip: Int, $toDate: DateTime) {
  transcripts(limit: $limit, skip: $skip, toDate: $toDate) {
    id
    title
    date
    duration
    host_email
    organizer_email
    participants
    transcript_url
  }
}
"""
# `audio_url` and `summary { ... }` are intentionally absent: only
# DETAIL_QUERY (fetch_artifacts) needs them, and Fireflies' nested resolvers
# for those two fields have been the chief source of per-row 408 timeouts
# observed against /list when the cluster is under load (probe 2026-05-03).
# Trimming halves the resolver fan-out on every list call.

DELETE_MUTATION = """
mutation DeleteTranscript($id: String!) {
  deleteTranscript(id: $id) { id }
}
"""

DETAIL_QUERY = """
query TranscriptDetail($id: String!) {
  transcript(id: $id) {
    id
    title
    date
    duration
    host_email
    organizer_email
    participants
    transcript_url
    audio_url
    summary { overview action_items keywords }
    sentences { speaker_name text }
  }
}
"""

USER_QUERY = """
query {
  user {
    email
  }
}
"""


class FirefliesError(Exception):
    pass


class RateLimitedError(FirefliesError):
    """Raised when Fireflies signals rate-limiting (429 or GraphQL too_many_requests).

    Callers (web routes, CLI) should render this to the user as 'try again later'
    rather than a generic 500, since the key may be perfectly valid.
    """

    def __init__(self, message: str, *, retry_after_seconds: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class TransientServerError(FirefliesError):
    """Raised after retries are exhausted on a 5xx response from Fireflies.

    Distinct from RateLimitedError so callers can pick a different retry
    horizon. The sync engine treats both alike — finalize the run as
    ``partial`` so the scheduler picks it up on its next regular tick
    instead of leaving the user staring at a 'failed' banner because
    Fireflies' edge had a flaky minute.

    ``retry_after_seconds`` defaults to ``None`` because Fireflies never
    actually tells us when a 5xx or transport timeout will clear — any
    fixed number we'd default to is a fabrication, and surfacing a
    fabricated retry timestamp in the UI ("Next retry around HH:MM
    (estimated)") is worse UX than admitting we don't know. Real estimates
    only come from ``Retry-After`` on 429s, which is what RateLimitedError
    is for.
    """

    def __init__(self, message: str, *, retry_after_seconds: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class FirefliesClient:
    def __init__(
        self,
        *,
        api_key: str,
        endpoint: str = "https://api.fireflies.ai/graphql",
        retry_max: int = 3,
        retry_5xx_max: int = 1,
        retry_timeout_max: int = 1,
        retry_base_seconds: float = 1.0,
        page_size: int = 50,
        timeout_seconds: float = 90.0,
    ) -> None:
        # ``retry_5xx_max`` is intentionally smaller than ``retry_max``:
        # Fireflies' edge serves 504s in ~15s when their backend is sluggish
        # (measured), so each 5xx retry burns ~15s for negligible value when
        # the outage is sustained. One retry covers the single-blip case,
        # then we surface as ``TransientServerError`` and the sync engine
        # finalises the run as ``partial`` so the scheduler retries later.
        #
        # ``retry_timeout_max`` applies the same logic to ``httpx.TimeoutException``:
        # each attempt blocks for up to ``timeout_seconds`` (default 30s), so
        # retrying 3 times burns ~120s for a sustained outage. One retry, then
        # surface as ``TransientServerError`` so the scheduler can mark the run
        # partial and back off — instead of every tick logging
        # "Scheduler tick failed: network:" until the user notices.
        self._api_key = api_key
        self._endpoint = endpoint
        self._retry_max = retry_max
        self._retry_5xx_max = retry_5xx_max
        self._retry_timeout_max = retry_timeout_max
        self._retry_base = retry_base_seconds
        self._page_size = page_size
        self._timeout = timeout_seconds

    async def list_meetings(self, filter: MeetingFilter) -> AsyncIterator[Meeting]:
        skip = 0
        emitted = 0
        async with self._http() as client:
            while True:
                variables: dict[str, Any] = {
                    "limit": self._page_size,
                    "skip": skip,
                    "toDate": filter.older_than.isoformat() if filter.older_than else None,
                }
                payload = await self._request(client, LIST_QUERY, variables, op="list_meetings")
                items = payload.get("data", {}).get("transcripts", []) or []
                if not items:
                    return
                # Items can contain `null` rows when Fireflies bubbles a
                # required-field resolver failure up to the row root. Skip
                # them: an unparseable row is never recoverable, and the
                # alternative — calling `_meeting_from_raw(None)` — would
                # crash the whole sync over a single bad row.
                page_yielded = 0
                for raw in items:
                    if raw is None:
                        continue
                    yield _meeting_from_raw(raw)
                    page_yielded += 1
                    emitted += 1
                    if filter.limit and emitted >= filter.limit:
                        return
                if page_yielded == 0:
                    return
                skip += len(items)

    async def list_meetings_page(
        self,
        *,
        skip: int,
        limit: int,
        to_date: datetime | None = None,
    ) -> AsyncIterator[Meeting]:
        """Single-page list — used by SyncService for explicit pagination control.

        Differs from :meth:`list_meetings` (which auto-paginates internally):
        the caller drives ``skip`` and ``limit``, so SyncService can persist a
        cursor and resume after a rate-limit interruption.
        """
        async with self._http() as client:
            variables: dict[str, Any] = {
                "limit": limit,
                "skip": skip,
                "toDate": to_date.isoformat() if to_date is not None else None,
            }
            payload = await self._request(client, LIST_QUERY, variables, op="list_meetings_page")
            for raw in payload.get("data", {}).get("transcripts", []) or []:
                if raw is None:
                    continue
                yield _meeting_from_raw(raw)

    async def fetch_artifacts(self, meeting_id: str) -> ArtifactBundle:
        async with self._http() as client:
            payload = await self._request(
                client,
                DETAIL_QUERY,
                {"id": meeting_id},
                op="fetch_artifacts",
            )
            t = payload.get("data", {}).get("transcript")
            if t is None:
                raise FirefliesError(f"transcript not found: {meeting_id}")
            audio_url = t.get("audio_url")
            audio_bytes = await self._download_audio(client, audio_url) if audio_url else None
            return ArtifactBundle(
                audio_bytes=audio_bytes,
                transcript_markdown=_render_transcript_md(t),
                summary_payload=_normalize_summary(t.get("summary") or {}),
                metadata={
                    "source_url": f"https://app.fireflies.ai/view/{meeting_id}",
                    "audio_url": audio_url,
                },
            )

    async def delete_meeting(self, meeting_id: str) -> None:
        async with self._http() as client:
            try:
                await self._request(
                    client,
                    DELETE_MUTATION,
                    {"id": meeting_id},
                    op="delete_meeting",
                )
            except FirefliesError as e:
                if "404" in str(e):
                    return  # idempotent
                raise

    async def ping_user(self) -> str:
        """Return the email of the authenticated user.

        Raises:
            PermissionError: if the API returns 401 or 403.
            FirefliesError: for other HTTP/GraphQL errors.
        """
        async with self._http() as client:
            try:
                payload = await self._request(client, USER_QUERY, {}, op="ping_user")
            except FirefliesError as e:
                msg = str(e)
                if msg.startswith("401") or msg.startswith("403"):
                    raise PermissionError(f"Invalid API key: {msg}") from e
                raise
            user = payload.get("data", {}).get("user") or {}
            email: str = user.get("email") or ""
            if not email:
                raise FirefliesError("ping_user: no email in response")
            return email

    def _http(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            headers={"Authorization": f"Bearer {self._api_key}"},
            timeout=self._timeout,
        )

    async def _request(
        self,
        client: httpx.AsyncClient,
        query: str,
        variables: dict[str, Any],
        *,
        op: str,
    ) -> dict[str, Any]:
        body = {"query": query, "variables": variables}
        for attempt in range(self._retry_max + 1):
            logger.info(
                "fireflies_request op=%s attempt=%d auth=[REDACTED]",
                op,
                attempt,
            )
            try:
                resp = await client.post(self._endpoint, json=body)
            except httpx.TimeoutException as e:
                # Transport-level timeouts (ReadTimeout, ConnectTimeout, ...).
                # Reclassify as ``TransientServerError`` so ``SyncService``
                # finalises the run as ``partial`` instead of letting the
                # exception escape to the scheduler as a hard "tick failed".
                # Cap retries at ``retry_timeout_max`` to avoid burning ~timeout
                # x retries when Fireflies is sustained-slow.
                #
                # Build a self-explanatory message: ``str(e)`` from httpx is
                # often empty for ReadTimeout, and "timeout: " in the UI tells
                # the user nothing about who's at fault or what state things
                # are in.
                if attempt >= self._retry_timeout_max:
                    raise TransientServerError(
                        f"Fireflies didn't respond within {self._timeout:.0f}s. "
                        "Their service may be slow or unavailable; your local "
                        "cache is unaffected and the next scheduled sync will "
                        "retry automatically."
                    ) from e
                await self._sleep(attempt, retry_after_seconds=None)
                continue
            except httpx.HTTPError as e:
                # Non-timeout transport failures (SSL, DNS, conn refused).
                # These usually indicate a configuration or infra problem
                # rather than a transient blip — surface as ``FirefliesError``
                # so the operator sees it instead of silently retrying.
                if attempt >= self._retry_max:
                    raise FirefliesError(
                        f"Network error reaching Fireflies: {e or type(e).__name__}. "
                        "Check your internet connection or proxy settings."
                    ) from e
                await self._sleep(attempt, retry_after_seconds=None)
                continue

            if resp.status_code == 429:
                ra_seconds = _parse_retry_after(resp.headers.get("Retry-After"))
                # Fail fast: if Fireflies tells us to wait longer than the cap
                # (typical for daily-quota lockouts that span until UTC
                # midnight), don't burn through retries — surface immediately.
                if ra_seconds is not None and ra_seconds > _MAX_RETRY_AFTER_SECONDS:
                    raise RateLimitedError(
                        f"rate limited; retry after {ra_seconds:.0f}s",
                        retry_after_seconds=ra_seconds,
                    )
                if attempt >= self._retry_max:
                    raise RateLimitedError("rate limited", retry_after_seconds=ra_seconds)
                await self._sleep(attempt, retry_after_seconds=ra_seconds)
                continue
            if 500 <= resp.status_code < 600:
                if attempt >= self._retry_5xx_max:
                    raise TransientServerError(
                        f"Fireflies returned HTTP {resp.status_code} (server error). "
                        "This is on their side; your local cache is unaffected and "
                        "the next scheduled sync will retry automatically."
                    )
                await self._sleep(attempt, retry_after_seconds=None)
                continue
            if 400 <= resp.status_code < 500:
                raise FirefliesError(f"{resp.status_code}: {resp.text[:200]}")

            data: dict[str, Any] = resp.json()
            if data.get("errors"):
                # Some Fireflies errors are HTTP 200 with an `errors` array —
                # detect rate-limit there too so the caller can react.
                if _errors_indicate_rate_limit(data["errors"]):
                    raise RateLimitedError(f"graphql: {data['errors']}")
                # Field-level resolver timeouts (Fireflies' `participants` and
                # `audio_url` resolvers intermittently return per-row 408s)
                # come back as a populated `data` payload alongside the errors
                # array. Discarding the whole page costs us every successful
                # row; instead, log + return so downstream sees the reduced
                # fields. Any error without a recognised tolerable path —
                # response-level errors, errors targeting required fields —
                # still surfaces as FirefliesError.
                if data.get("data") is not None and _errors_are_tolerable_field_failures(
                    data["errors"]
                ):
                    logger.warning(
                        "fireflies_request op=%s tolerated=%d field-level errors (fields=%s)",
                        op,
                        len(data["errors"]),
                        sorted(_tolerable_error_field_names(data["errors"])),
                    )
                    return data
                # Fireflies sometimes wraps a 5xx as a GraphQL error rather
                # than an HTTP-level 5xx (observed on deleteTranscript:
                # ``code: INTERNAL_SERVER_ERROR, extensions.status: 500``).
                # Treat those exactly like an HTTP 5xx — retry once, then
                # surface as TransientServerError so the runner can log a
                # friendly per-meeting failure instead of dumping the raw
                # dict ``graphql: [{...}]`` to the user.
                if _errors_indicate_transient_server_error(data["errors"]):
                    if attempt >= self._retry_5xx_max:
                        raise TransientServerError(
                            "Fireflies returned an internal server error. "
                            "This is on their side and usually clears within "
                            "a few minutes; retry the operation later."
                        )
                    await self._sleep(attempt, retry_after_seconds=None)
                    continue
                # Last resort: hoist the first ``message`` for the UI rather
                # than dumping the whole errors-list dict, which produces
                # the wall-of-Python-repr the user reported (2026-05-03).
                raise FirefliesError(_format_graphql_errors(data["errors"]))
            return data

        raise FirefliesError("retries exhausted")

    async def _sleep(self, attempt: int, *, retry_after_seconds: float | None) -> None:
        if retry_after_seconds is not None:
            await asyncio.sleep(min(retry_after_seconds, _MAX_RETRY_AFTER_SECONDS))
            return
        delay = self._retry_base * (4**attempt)
        delay *= 1 + random.uniform(-0.25, 0.25)
        await asyncio.sleep(max(0.0, delay))

    @staticmethod
    async def _download_audio(client: httpx.AsyncClient, url: str) -> bytes:
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            chunks: list[bytes] = []
            async for chunk in resp.aiter_bytes():
                chunks.append(chunk)
            return b"".join(chunks)


def _normalize_summary(summary: dict[str, Any]) -> dict[str, Any]:
    """Fireflies' `Summary.action_items` is a String (often newline-separated
    or markdown bullets). The renderer expects a list. Split if needed; leave
    other fields untouched."""
    out = dict(summary)
    raw = summary.get("action_items")
    if isinstance(raw, str):
        items = [line.lstrip("-* ").strip() for line in raw.splitlines()]
        out["action_items"] = [i for i in items if i]
    elif raw is None:
        out["action_items"] = []
    return out


def _parse_date(raw_date: Any) -> datetime:
    """Fireflies returns `date` as a Float (Unix epoch ms). Be permissive
    in case a deployment ever returns an ISO string."""
    if isinstance(raw_date, (int, float)):
        # Heuristic: epoch ms if value is large, otherwise epoch seconds.
        seconds = raw_date / 1000.0 if raw_date > 1e12 else float(raw_date)
        return datetime.fromtimestamp(seconds, tz=UTC)
    if isinstance(raw_date, str):
        return datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
    raise FirefliesError(f"unexpected date type: {type(raw_date).__name__}")


def _meeting_from_raw(raw: dict[str, Any]) -> Meeting:
    participants = raw.get("participants") or []
    return Meeting(
        meeting_id=raw["id"],
        title=raw.get("title") or "(untitled)",
        meeting_date=_parse_date(raw["date"]),
        duration_minutes=float(raw.get("duration") or 0.0),
        host_email=raw.get("host_email") or raw.get("organizer_email") or "",
        participant_count=len(participants),
        tags=(),  # Fireflies' Transcript type does not expose tags as of 2026-04
        has_transcript=bool(raw.get("transcript_url")),
    )


def _render_transcript_md(t: dict[str, Any]) -> str:
    sentences = t.get("sentences") or []
    if not sentences:
        return f"# {t.get('title') or '(untitled)'}\n\n_(no transcript content)_\n"
    lines: list[str] = [f"# {t.get('title') or '(untitled)'}", ""]
    last_speaker: str | None = None
    buf: list[str] = []
    for s in sentences:
        speaker = s.get("speaker_name") or "Unknown"
        text = (s.get("text") or "").strip()
        if not text:
            continue
        if speaker != last_speaker and buf:
            lines.append(f"**{last_speaker}:** {' '.join(buf)}")
            lines.append("")
            buf = []
        last_speaker = speaker
        buf.append(text)
    if buf and last_speaker is not None:
        lines.append(f"**{last_speaker}:** {' '.join(buf)}")
        lines.append("")
    return "\n".join(lines)


def _parse_retry_after(header_value: str | None) -> float | None:
    """Parse a Retry-After header into seconds.

    Per RFC 7231 §7.1.3 the value is either an HTTP-date or a positive integer
    number of seconds. Returns ``None`` when the header is missing or
    unparseable. Negative deltas are clamped to 0.
    """
    if header_value is None:
        return None
    raw = header_value.strip()
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        pass
    parsed_dt = email.utils.parsedate_to_datetime(raw)
    if parsed_dt is None:
        return None
    if parsed_dt.tzinfo is None:
        parsed_dt = parsed_dt.replace(tzinfo=UTC)
    delta = (parsed_dt - datetime.now(tz=UTC)).total_seconds()
    return max(0.0, delta)


def _errors_indicate_rate_limit(errors: Any) -> bool:
    """True iff a GraphQL `errors` array contains a too_many_requests entry.

    Fireflies sometimes returns HTTP 200 with this shape:
        {"errors": [{"code": "too_many_requests", "extensions": {"status": 429}}]}
    """
    if not isinstance(errors, list):
        return False
    for entry in errors:
        if not isinstance(entry, dict):
            continue
        if entry.get("code") == "too_many_requests":
            return True
        ext = entry.get("extensions")
        if isinstance(ext, dict) and (
            ext.get("code") == "too_many_requests" or ext.get("status") == 429
        ):
            return True
    return False


# Leaf field names whose absence the Meeting domain model survives. `_meeting_from_raw`
# defaults each of these to a safe value (None / 0 / empty), so a per-row resolver
# failure on these fields degrades the row instead of poisoning the page.
# `id` and `date` are deliberately *excluded* — Meeting cannot be constructed
# without them, so any error pointing there must surface.
_TOLERABLE_LEAF_FIELDS = frozenset(
    {
        "audio_url",
        "duration",
        "host_email",
        "meeting_attendance",
        "meeting_attendees",
        "organizer_email",
        "participants",
        "summary",
        "tags",
        "title",
        "transcript_url",
    }
)


def _errors_are_tolerable_field_failures(errors: Any) -> bool:
    """True iff every error in the array targets a tolerable leaf field.

    A "tolerable" error is one with a non-empty ``path`` whose final element is a
    known non-required field. Response-level errors (no path), schema-drift
    errors, or errors targeting required fields (id/date) all return False — those
    must surface as :class:`FirefliesError` so we don't silently swallow a
    structural problem.
    """
    if not isinstance(errors, list) or not errors:
        return False
    for entry in errors:
        if not isinstance(entry, dict):
            return False
        path = entry.get("path")
        if not isinstance(path, list) or not path:
            return False
        leaf = path[-1]
        if not isinstance(leaf, str) or leaf not in _TOLERABLE_LEAF_FIELDS:
            return False
    return True


def _tolerable_error_field_names(errors: Any) -> set[str]:
    """Return the set of leaf field names mentioned in ``errors`` for logging."""
    names: set[str] = set()
    if not isinstance(errors, list):
        return names
    for entry in errors:
        if not isinstance(entry, dict):
            continue
        path = entry.get("path")
        if isinstance(path, list) and path and isinstance(path[-1], str):
            names.add(path[-1])
    return names


def _errors_indicate_transient_server_error(errors: Any) -> bool:
    """True iff any GraphQL error carries a 5xx status / INTERNAL_SERVER_ERROR.

    Fireflies sometimes wraps backend 5xx as a GraphQL error rather than an
    HTTP-level 5xx — observed payload shape::

        {"errors": [{"code": "INTERNAL_SERVER_ERROR",
                     "extensions": {"code": "INTERNAL_SERVER_ERROR",
                                    "status": 500, "correlationId": "..."}}]}

    We want those handled by the same retry-then-classify-as-transient flow
    as actual HTTP 5xx responses.
    """
    if not isinstance(errors, list):
        return False
    for entry in errors:
        if not isinstance(entry, dict):
            continue
        if entry.get("code") == "INTERNAL_SERVER_ERROR":
            return True
        ext = entry.get("extensions")
        if isinstance(ext, dict):
            if ext.get("code") == "INTERNAL_SERVER_ERROR":
                return True
            status = ext.get("status")
            if isinstance(status, int) and 500 <= status < 600:
                return True
    return False


def _format_graphql_errors(errors: Any) -> str:
    """Build a human-readable message from a GraphQL ``errors`` array.

    The previous behaviour stringified the whole list, which surfaced
    ``graphql: [{'friendly': False, 'message': '...', ...}]`` to the user.
    Lifting the first error's ``message`` (and its operation ``path`` when
    available) gives a much shorter, action-oriented string that the runner
    can route into per-meeting failure rows without further processing.
    """
    if not isinstance(errors, list) or not errors:
        return "graphql: <empty errors>"
    first = errors[0] if isinstance(errors[0], dict) else None
    if first is None:
        return f"graphql: {errors[0]!r}"
    message = first.get("message")
    if not isinstance(message, str) or not message:
        return f"graphql: {first!r}"
    path = first.get("path")
    if isinstance(path, list) and path:
        leaf = path[-1] if isinstance(path[-1], str) else None
        if leaf:
            return f"{leaf}: {message}"
    return message
