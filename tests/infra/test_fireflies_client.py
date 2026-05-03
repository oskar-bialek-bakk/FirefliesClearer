"""Tests for the Fireflies GraphQL client (using respx to mock httpx)."""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime

import httpx
import pytest
import respx

from firefliesclearer.infra.fireflies_client import (
    FirefliesClient,
    FirefliesError,
    RateLimitedError,
    TransientServerError,
)
from firefliesclearer.ports.meeting_repository import MeetingFilter

API_URL = "https://api.fireflies.ai/graphql"


@pytest.fixture
def client():
    return FirefliesClient(
        api_key="ff_secret_xyz",
        endpoint=API_URL,
        retry_max=3,
        retry_base_seconds=0.0,
    )


@respx.mock
async def test_request_sends_bearer_auth(client: FirefliesClient) -> None:
    route = respx.post(API_URL).mock(
        return_value=httpx.Response(200, json={"data": {"transcripts": []}})
    )
    async for _ in client.list_meetings(MeetingFilter()):
        pass
    sent = route.calls.last.request
    assert sent.headers["Authorization"] == "Bearer ff_secret_xyz"


@respx.mock
async def test_api_key_is_redacted_in_logs(
    client: FirefliesClient, caplog: pytest.LogCaptureFixture
) -> None:
    respx.post(API_URL).mock(return_value=httpx.Response(200, json={"data": {"transcripts": []}}))
    caplog.set_level(logging.INFO, logger="firefliesclearer.infra.fireflies_client")
    async for _ in client.list_meetings(MeetingFilter()):
        pass
    text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "ff_secret_xyz" not in text
    assert "[REDACTED]" in text or "Bearer" not in text


@respx.mock
async def test_4xx_error_not_retried_and_raises(
    client: FirefliesClient,
) -> None:
    route = respx.post(API_URL).mock(
        return_value=httpx.Response(403, json={"errors": ["forbidden"]})
    )
    with pytest.raises(FirefliesError):
        async for _ in client.list_meetings(MeetingFilter()):
            pass
    assert route.call_count == 1


@respx.mock
async def test_5xx_is_retried_once_then_succeeds(
    client: FirefliesClient,
) -> None:
    """A single 5xx blip is retried once; if the second attempt succeeds, fine.

    With ``retry_5xx_max=1`` (the new default) this is the happy path for a
    transient server hiccup. Fireflies serves persistent 504s in ~15s each,
    so retrying more than once burns clock for no real benefit.
    """
    route = respx.post(API_URL).mock(
        side_effect=[
            httpx.Response(503, text="boom"),
            httpx.Response(200, json={"data": {"transcripts": []}}),
        ]
    )
    async for _ in client.list_meetings(MeetingFilter()):
        pass
    assert route.call_count == 2


@respx.mock
async def test_5xx_gives_up_after_retry_5xx_max_attempts() -> None:
    """Persistent 5xx surfaces as ``TransientServerError`` after the
    retry_5xx_max cap, so the sync engine can mark the run partial fast
    instead of waiting through ~3 x 15s of pointless retries.
    """
    from firefliesclearer.infra.fireflies_client import TransientServerError

    fast_client = FirefliesClient(
        api_key="ff_secret_xyz",
        endpoint=API_URL,
        retry_5xx_max=1,
        retry_base_seconds=0.0,
    )
    route = respx.post(API_URL).mock(return_value=httpx.Response(504, text="gateway timeout"))
    with pytest.raises(TransientServerError):
        async for _ in fast_client.list_meetings(MeetingFilter()):
            pass
    # 1 initial + 1 retry = 2 attempts total.
    assert route.call_count == 2


@respx.mock
async def test_5xx_max_can_be_disabled() -> None:
    """``retry_5xx_max=0`` means: surface the first 5xx immediately."""
    from firefliesclearer.infra.fireflies_client import TransientServerError

    no_retry = FirefliesClient(
        api_key="ff_secret_xyz",
        endpoint=API_URL,
        retry_5xx_max=0,
        retry_base_seconds=0.0,
    )
    route = respx.post(API_URL).mock(return_value=httpx.Response(504, text="boom"))
    with pytest.raises(TransientServerError):
        async for _ in no_retry.list_meetings(MeetingFilter()):
            pass
    assert route.call_count == 1


@respx.mock
async def test_transient_server_error_carries_no_synthetic_retry_estimate() -> None:
    """Fireflies never tells us when a 5xx or transport timeout will clear,
    so ``TransientServerError.retry_after_seconds`` must default to ``None``.
    A non-None default would surface a fabricated 'Next retry around HH:MM'
    timestamp in the UI — admitting we're guessing while still committing to
    a number, which is worse UX than leaving the field blank."""
    err_5xx = TransientServerError("server 504")
    err_timeout = TransientServerError("Fireflies didn't respond...")
    assert err_5xx.retry_after_seconds is None
    assert err_timeout.retry_after_seconds is None


@respx.mock
async def test_timeout_error_message_explains_what_happened(
    client: FirefliesClient,
) -> None:
    """``str(httpx.ReadTimeout)`` is often empty; surfacing it raw in the UI
    produces 'Sync paused: timeout: ' which tells the user nothing about who's
    at fault or what state the cache is in. The exception message must be
    self-explanatory enough to render directly in the banner."""
    fast_client = FirefliesClient(
        api_key="ff_secret_xyz",
        endpoint=API_URL,
        retry_timeout_max=0,
        retry_base_seconds=0.0,
        timeout_seconds=30.0,
    )
    respx.post(API_URL).mock(side_effect=httpx.ReadTimeout(""))
    with pytest.raises(TransientServerError) as exc_info:
        async for _ in fast_client.list_meetings(MeetingFilter()):
            pass
    msg = str(exc_info.value)
    assert "Fireflies" in msg
    assert "30s" in msg
    assert "cache is unaffected" in msg


@respx.mock
async def test_5xx_error_message_explains_what_happened() -> None:
    """Same friendliness contract as the timeout case — the banner renders
    this string directly, so 'server 504' is too cryptic for end users."""
    fast_client = FirefliesClient(
        api_key="ff_secret_xyz",
        endpoint=API_URL,
        retry_5xx_max=0,
        retry_base_seconds=0.0,
    )
    respx.post(API_URL).mock(return_value=httpx.Response(503, text="boom"))
    with pytest.raises(TransientServerError) as exc_info:
        async for _ in fast_client.list_meetings(MeetingFilter()):
            pass
    msg = str(exc_info.value)
    assert "503" in msg
    assert "Fireflies" in msg
    assert "cache is unaffected" in msg


@respx.mock
async def test_read_timeout_raises_transient_server_error_after_retries() -> None:
    """A persistent transport timeout (httpx.ReadTimeout) must surface as
    ``TransientServerError``, not generic ``FirefliesError``. ``SyncService``
    only catches ``RateLimitedError`` / ``TransientServerError``; misclassifying
    transport timeouts as ``FirefliesError`` lets them escape to the scheduler
    as a hard "tick failed" instead of being recorded as a partial run with
    ``next_resume_at`` set."""
    fast_client = FirefliesClient(
        api_key="ff_secret_xyz",
        endpoint=API_URL,
        retry_timeout_max=1,
        retry_base_seconds=0.0,
    )
    route = respx.post(API_URL).mock(side_effect=httpx.ReadTimeout("read timeout"))
    with pytest.raises(TransientServerError):
        async for _ in fast_client.list_meetings(MeetingFilter()):
            pass
    # 1 initial + 1 retry = 2 attempts.
    assert route.call_count == 2


@respx.mock
async def test_read_timeout_retry_can_be_disabled() -> None:
    """``retry_timeout_max=0`` means: surface the first timeout immediately so
    the scheduler can move on rather than blocking on retries."""
    no_retry = FirefliesClient(
        api_key="ff_secret_xyz",
        endpoint=API_URL,
        retry_timeout_max=0,
        retry_base_seconds=0.0,
    )
    route = respx.post(API_URL).mock(side_effect=httpx.ReadTimeout("boom"))
    with pytest.raises(TransientServerError):
        async for _ in no_retry.list_meetings(MeetingFilter()):
            pass
    assert route.call_count == 1


@respx.mock
async def test_timeout_then_success_is_retried() -> None:
    """A single timeout blip is retried once; if the second attempt resolves,
    the sync proceeds normally without surfacing an error."""
    fast_client = FirefliesClient(
        api_key="ff_secret_xyz",
        endpoint=API_URL,
        retry_timeout_max=1,
        retry_base_seconds=0.0,
    )
    route = respx.post(API_URL).mock(
        side_effect=[
            httpx.ReadTimeout("blip"),
            httpx.Response(200, json={"data": {"transcripts": []}}),
        ]
    )
    async for _ in fast_client.list_meetings(MeetingFilter()):
        pass
    assert route.call_count == 2


@respx.mock
async def test_non_timeout_http_error_still_raises_fireflies_error() -> None:
    """SSL/DNS/conn-refused errors should still raise ``FirefliesError`` —
    those usually indicate a configuration problem the operator needs to see,
    not a transient backend hiccup. Distinguishing this from timeouts keeps
    the scheduler from silently swallowing real infra issues as 'partial'."""
    fast_client = FirefliesClient(
        api_key="ff_secret_xyz",
        endpoint=API_URL,
        retry_max=0,
        retry_base_seconds=0.0,
    )
    route = respx.post(API_URL).mock(side_effect=httpx.ConnectError("dns failed"))
    with pytest.raises(FirefliesError) as exc_info:
        async for _ in fast_client.list_meetings(MeetingFilter()):
            pass
    # Should NOT be a TransientServerError — we want the operator to see this.
    assert not isinstance(exc_info.value, TransientServerError)
    assert "Network error" in str(exc_info.value)
    assert route.call_count == 1


@respx.mock
async def test_429_with_retry_after_is_honored(
    client: FirefliesClient,
) -> None:
    route = respx.post(API_URL).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "0"}),
            httpx.Response(200, json={"data": {"transcripts": []}}),
        ]
    )
    async for _ in client.list_meetings(MeetingFilter()):
        pass
    assert route.call_count == 2


@respx.mock
async def test_429_with_far_future_retry_after_fails_fast(
    client: FirefliesClient,
) -> None:
    """Retry-After > 60s (e.g. daily-quota lockout until UTC midnight) should
    raise RateLimitedError immediately rather than burn through retries."""
    route = respx.post(API_URL).mock(
        return_value=httpx.Response(429, headers={"Retry-After": "36000"}),  # 10h
    )
    with pytest.raises(RateLimitedError) as exc_info:
        async for _ in client.list_meetings(MeetingFilter()):
            pass
    # Failed on the FIRST attempt — no exponential backoff burned.
    assert route.call_count == 1
    assert exc_info.value.retry_after_seconds is not None
    assert exc_info.value.retry_after_seconds >= 36000


@respx.mock
async def test_429_with_http_date_retry_after_parses_correctly(
    client: FirefliesClient,
) -> None:
    """Retry-After can be an HTTP-date (RFC 7231); a far-future date should
    also fail fast."""
    route = respx.post(API_URL).mock(
        return_value=httpx.Response(429, headers={"Retry-After": "Sun, 01 Jan 2099 00:00:00 GMT"}),
    )
    with pytest.raises(RateLimitedError) as exc_info:
        async for _ in client.list_meetings(MeetingFilter()):
            pass
    assert route.call_count == 1
    assert exc_info.value.retry_after_seconds is not None
    assert exc_info.value.retry_after_seconds > 60


@respx.mock
async def test_graphql_too_many_requests_error_raises_rate_limited(
    client: FirefliesClient,
) -> None:
    """Fireflies sometimes returns HTTP 200 with errors[].code='too_many_requests'.
    Detect that shape and surface RateLimitedError so callers can react."""
    route = respx.post(API_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "errors": [
                    {
                        "code": "too_many_requests",
                        "message": "Too many requests. Please retry after Fri, 01 May 2026 00:00:00 GMT (UTC)",
                        "extensions": {"code": "too_many_requests", "status": 429},
                    }
                ]
            },
        ),
    )
    with pytest.raises(RateLimitedError):
        async for _ in client.list_meetings(MeetingFilter()):
            pass
    assert route.call_count == 1


@respx.mock
async def test_list_meetings_paginates(client: FirefliesClient) -> None:
    page1 = {
        "data": {
            "transcripts": [
                {
                    "id": "a",
                    "title": "M1",
                    "date": "2026-01-01T10:00:00Z",
                    "duration": 12.5,
                    "host_email": "u@x.com",
                    "participants": ["u@x.com", "b@x.com"],
                    "tags": [],
                    "transcript_url": "https://x/t/a",
                }
            ]
        }
    }
    page2 = {
        "data": {
            "transcripts": [
                {
                    "id": "b",
                    "title": "M2",
                    "date": "2026-02-01T10:00:00Z",
                    "duration": 5.0,
                    "host_email": "u@x.com",
                    "participants": ["u@x.com"],
                    "tags": ["draft"],
                    "transcript_url": None,
                }
            ]
        }
    }
    page3 = {"data": {"transcripts": []}}
    respx.post(API_URL).mock(
        side_effect=[
            httpx.Response(200, json=page1),
            httpx.Response(200, json=page2),
            httpx.Response(200, json=page3),
        ]
    )
    ids: list[str] = []
    async for m in client.list_meetings(MeetingFilter()):
        ids.append(m.meeting_id)
    assert ids == ["a", "b"]


@respx.mock
async def test_list_meetings_applies_older_than_filter(
    client: FirefliesClient,
) -> None:
    respx.post(API_URL).mock(return_value=httpx.Response(200, json={"data": {"transcripts": []}}))
    cutoff = datetime(2026, 1, 1, tzinfo=UTC)
    async for _ in client.list_meetings(MeetingFilter(older_than=cutoff)):
        pass
    # Decode the JSON body and assert the variable was actually sent — checking
    # raw substring would also match the `toDate` literal in the GraphQL query
    # text, which is not a guarantee that the cutoff was bound.
    body = json.loads(respx.calls.last.request.content)
    assert "variables" in body
    assert body["variables"].get("toDate") == cutoff.isoformat()


@respx.mock
async def test_no_transcript_url_marks_meeting_has_transcript_false(
    client: FirefliesClient,
) -> None:
    respx.post(API_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "transcripts": [
                        {
                            "id": "x",
                            "title": "Y",
                            "date": "2026-04-01T10:00:00Z",
                            "duration": 1.0,
                            "host_email": "u@x.com",
                            "participants": [],
                            "tags": [],
                            "transcript_url": None,
                        }
                    ]
                }
            },
        )
    )
    async for m in client.list_meetings(MeetingFilter(limit=1)):
        assert m.has_transcript is False
        break


@respx.mock
async def test_delete_meeting_calls_mutation(
    client: FirefliesClient,
) -> None:
    route = respx.post(API_URL).mock(
        return_value=httpx.Response(200, json={"data": {"deleteTranscript": {"id": "x"}}})
    )
    await client.delete_meeting("x")
    assert route.call_count == 1
    body = route.calls.last.request.content.decode()
    assert "deleteTranscript" in body
    assert '"x"' in body


@respx.mock
async def test_delete_meeting_idempotent_on_404(
    client: FirefliesClient,
) -> None:
    respx.post(API_URL).mock(return_value=httpx.Response(404, json={"errors": ["not_found"]}))
    await client.delete_meeting("missing")


@respx.mock
async def test_fetch_artifacts_normalizes_action_items_string(
    client: FirefliesClient,
) -> None:
    """Fireflies returns Summary.action_items as a String (often newline-separated
    or markdown bullets). The renderer expects a list, so the client must split."""
    respx.post(API_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "transcript": {
                        "id": "x",
                        "title": "Sync",
                        "date": 1714300000000,
                        "duration": 30.0,
                        "host_email": "u@x.com",
                        "participants": [],
                        "transcript_url": None,
                        "audio_url": None,
                        "summary": {
                            "overview": "Did things.",
                            "action_items": "- Ship feature\n* Update docs\n  Send recap",
                            "keywords": ["ship", "docs"],
                        },
                        "sentences": [],
                    }
                }
            },
        )
    )
    bundle = await client.fetch_artifacts("x")
    assert bundle.summary_payload is not None
    assert bundle.summary_payload["action_items"] == [
        "Ship feature",
        "Update docs",
        "Send recap",
    ]
    assert bundle.summary_payload["overview"] == "Did things."
    assert bundle.summary_payload["keywords"] == ["ship", "docs"]


@respx.mock
async def test_fetch_artifacts_handles_missing_action_items(
    client: FirefliesClient,
) -> None:
    """When Fireflies omits action_items entirely, client emits an empty list."""
    respx.post(API_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "transcript": {
                        "id": "x",
                        "title": "Sync",
                        "date": 1714300000000,
                        "duration": 30.0,
                        "host_email": "u@x.com",
                        "participants": [],
                        "transcript_url": None,
                        "audio_url": None,
                        "summary": {"overview": "ov"},
                        "sentences": [],
                    }
                }
            },
        )
    )
    bundle = await client.fetch_artifacts("x")
    assert bundle.summary_payload is not None
    assert bundle.summary_payload["action_items"] == []


@respx.mock
async def test_host_email_uses_value_when_populated(client: FirefliesClient) -> None:
    """When host_email is populated, it wins over organizer_email (issue #2)."""
    respx.post(API_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "transcripts": [
                        {
                            "id": "a",
                            "title": "M",
                            "date": "2026-01-01T10:00:00Z",
                            "duration": 1.0,
                            "host_email": "host@x.com",
                            "organizer_email": "organizer@x.com",
                            "participants": [],
                            "transcript_url": None,
                        }
                    ]
                }
            },
        )
    )
    async for m in client.list_meetings(MeetingFilter(limit=1)):
        assert m.host_email == "host@x.com"
        break


@respx.mock
async def test_host_email_falls_back_to_organizer_email_when_empty(
    client: FirefliesClient,
) -> None:
    """When host_email is empty, organizer_email fills the host_email slot (issue #2)."""
    respx.post(API_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "transcripts": [
                        {
                            "id": "a",
                            "title": "M",
                            "date": "2026-01-01T10:00:00Z",
                            "duration": 1.0,
                            "host_email": "",
                            "organizer_email": "organizer@x.com",
                            "participants": [],
                            "transcript_url": None,
                        }
                    ]
                }
            },
        )
    )
    async for m in client.list_meetings(MeetingFilter(limit=1)):
        assert m.host_email == "organizer@x.com"
        break


@respx.mock
async def test_host_email_empty_when_both_missing(client: FirefliesClient) -> None:
    """When both host_email and organizer_email are missing/empty, fall back to '' (issue #2)."""
    respx.post(API_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "transcripts": [
                        {
                            "id": "a",
                            "title": "M",
                            "date": "2026-01-01T10:00:00Z",
                            "duration": 1.0,
                            "host_email": None,
                            "organizer_email": "",
                            "participants": [],
                            "transcript_url": None,
                        }
                    ]
                }
            },
        )
    )
    async for m in client.list_meetings(MeetingFilter(limit=1)):
        assert m.host_email == ""
        break


@respx.mock
async def test_ping_user_returns_email(client: FirefliesClient) -> None:
    """ping_user() returns the authenticated user's email on success."""
    respx.post(API_URL).mock(
        return_value=httpx.Response(
            200,
            json={"data": {"user": {"email": "alice@example.com"}}},
        )
    )
    email = await client.ping_user()
    assert email == "alice@example.com"


@respx.mock
async def test_ping_user_raises_permission_error_on_401(client: FirefliesClient) -> None:
    """ping_user() converts a 401 HTTP response to PermissionError."""
    respx.post(API_URL).mock(return_value=httpx.Response(401, text="Unauthorized"))
    with pytest.raises(PermissionError):
        await client.ping_user()


@respx.mock
async def test_ping_user_raises_permission_error_on_403(client: FirefliesClient) -> None:
    """ping_user() converts a 403 HTTP response to PermissionError."""
    respx.post(API_URL).mock(return_value=httpx.Response(403, text="Forbidden"))
    with pytest.raises(PermissionError):
        await client.ping_user()


@respx.mock
async def test_list_meetings_page_with_skip_limit_to_date(client: FirefliesClient) -> None:
    """list_meetings_page sends the GraphQL query with skip + limit + toDate."""
    route = respx.post(API_URL).mock(
        return_value=httpx.Response(200, json={"data": {"transcripts": []}})
    )
    to_date = datetime(2026, 5, 2, tzinfo=UTC)

    page = [m async for m in client.list_meetings_page(skip=100, limit=25, to_date=to_date)]
    assert page == []
    assert route.call_count == 1
    sent = json.loads(route.calls.last.request.content)
    assert sent["variables"]["skip"] == 100
    assert sent["variables"]["limit"] == 25
    assert sent["variables"]["toDate"] == "2026-05-02T00:00:00+00:00"


@respx.mock
async def test_list_meetings_page_with_no_to_date(client: FirefliesClient) -> None:
    """list_meetings_page sends toDate=None when caller omits it (incremental)."""
    route = respx.post(API_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "transcripts": [
                        {
                            "id": "m1",
                            "title": "Meeting m1",
                            "date": 1714579200000,
                            "duration": 30,
                            "host_email": "a@x.com",
                            "participants": ["a@x.com", "b@y.com"],
                            "transcript_url": "https://...",
                        }
                    ]
                }
            },
        )
    )

    page = [m async for m in client.list_meetings_page(skip=0, limit=50)]
    assert len(page) == 1
    assert page[0].meeting_id == "m1"
    sent = json.loads(route.calls.last.request.content)
    assert sent["variables"]["toDate"] is None


@respx.mock
async def test_list_query_omits_audio_url_and_summary_to_reduce_resolver_load(
    client: FirefliesClient,
) -> None:
    """LIST_QUERY must not request `audio_url` or `summary` — those fields are
    only consumed by DETAIL_QUERY, and Fireflies' resolvers for them have been
    the chief source of per-row 408 timeouts on /list (probe 2026-05-03).

    Locked in via the request body so we don't silently re-introduce the bloat
    while refactoring."""
    route = respx.post(API_URL).mock(
        return_value=httpx.Response(200, json={"data": {"transcripts": []}})
    )
    async for _ in client.list_meetings(MeetingFilter()):
        pass
    sent = json.loads(route.calls.last.request.content)
    query_text: str = sent["query"]
    assert "audio_url" not in query_text
    assert "summary" not in query_text
    # Sanity: the trim didn't accidentally remove fields we *do* still need.
    for required in ("id", "title", "date", "duration", "participants", "transcript_url"):
        assert required in query_text


@respx.mock
async def test_list_meetings_tolerates_field_level_resolver_timeouts(
    client: FirefliesClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Fireflies' nested resolvers (notably participants and audio_url)
    intermittently return per-row 408s while the rest of the response resolves
    fine. The HTTP body comes back as `{data: {...rows...}, errors: [...]}`.
    The client must emit the rows we got — discarding them turns a partial
    upstream into a total sync outage. participant_count falls back to 0 when
    `participants` is null/absent."""
    respx.post(API_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "transcripts": [
                        {
                            "id": "a",
                            "title": "M1",
                            "date": "2026-01-01T10:00:00Z",
                            "duration": 12.5,
                            "host_email": "u@x.com",
                            "participants": None,
                            "transcript_url": "https://x/t/a",
                        }
                    ]
                },
                "errors": [
                    {
                        "message": "Request timed out",
                        "path": ["transcripts", 0, "participants"],
                        "code": "request_timeout",
                        "extensions": {"code": "request_timeout", "status": 408},
                    }
                ],
            },
        )
    )
    caplog.set_level(logging.WARNING, logger="firefliesclearer.infra.fireflies_client")
    meetings = [m async for m in client.list_meetings(MeetingFilter(limit=1))]
    assert len(meetings) == 1
    assert meetings[0].meeting_id == "a"
    assert meetings[0].participant_count == 0
    assert any("tolerated" in rec.getMessage() for rec in caplog.records)


@respx.mock
async def test_field_level_error_without_timeout_flavor_is_not_tolerated(
    client: FirefliesClient,
) -> None:
    """The tolerance branch only applies when the error looks timeout-flavored
    (``code: 'request_timeout'`` or ``extensions.status: 408``). An upstream
    schema or data regression that happens to land on a tolerated leaf field
    must surface as ``FirefliesError`` so we don't silently mask it
    (Copilot review on PR #19)."""
    respx.post(API_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "transcripts": [
                        {
                            "id": "a",
                            "title": "M",
                            "date": "2026-01-01T10:00:00Z",
                            "duration": 1.0,
                            "host_email": "u@x.com",
                            "participants": None,
                            "transcript_url": None,
                        }
                    ]
                },
                "errors": [
                    {
                        "message": "Resolver crashed",
                        "path": ["transcripts", 0, "participants"],
                        "code": "INTERNAL_ERROR",
                    }
                ],
            },
        )
    )
    with pytest.raises(FirefliesError):
        async for _ in client.list_meetings(MeetingFilter()):
            pass


@respx.mock
async def test_list_meetings_continues_paginating_after_all_null_page(
    client: FirefliesClient,
) -> None:
    """A page where every row's required fields nulled-out (Fireflies bubbles
    the null up to row root) used to terminate pagination prematurely.
    Pagination must only stop when the items list itself is empty so that
    later pages with valid rows are still reached (Copilot review on PR #19)."""
    page1 = {"data": {"transcripts": [None, None]}}  # all rows nulled
    page2 = {
        "data": {
            "transcripts": [
                {
                    "id": "z",
                    "title": "M",
                    "date": "2026-01-01T10:00:00Z",
                    "duration": 1.0,
                    "host_email": "u@x.com",
                    "participants": [],
                    "transcript_url": None,
                }
            ]
        }
    }
    page3 = {"data": {"transcripts": []}}
    respx.post(API_URL).mock(
        side_effect=[
            httpx.Response(200, json=page1),
            httpx.Response(200, json=page2),
            httpx.Response(200, json=page3),
        ]
    )
    ids = [m.meeting_id async for m in client.list_meetings(MeetingFilter())]
    assert ids == ["z"]


@respx.mock
async def test_list_meetings_tolerates_per_row_errors_on_full_page(
    client: FirefliesClient,
) -> None:
    """The probe 2026-05-03 hit 50 rows x 2 fields = 100 errors, all on
    `participants` and `audio_url`. The page must still come back populated."""
    transcripts = [
        {
            "id": f"m{i}",
            "title": f"M{i}",
            "date": "2026-01-01T10:00:00Z",
            "duration": 1.0,
            "host_email": "u@x.com",
            "participants": None,
            "transcript_url": None,
        }
        for i in range(3)
    ]
    errors = []
    for i in range(3):
        for field in ("participants", "audio_url"):
            errors.append(
                {
                    "message": "Request timed out",
                    "path": ["transcripts", i, field],
                    "code": "request_timeout",
                    "extensions": {"code": "request_timeout", "status": 408},
                }
            )
    respx.post(API_URL).mock(
        side_effect=[
            httpx.Response(200, json={"data": {"transcripts": transcripts}, "errors": errors}),
            httpx.Response(200, json={"data": {"transcripts": []}}),
        ]
    )
    ids = [m.meeting_id async for m in client.list_meetings(MeetingFilter())]
    assert ids == ["m0", "m1", "m2"]


@respx.mock
async def test_list_meetings_raises_on_response_level_graphql_error(
    client: FirefliesClient,
) -> None:
    """Errors without a `path` are response-level (e.g. invalid query, schema
    drift) and must NOT be silently tolerated — those signal a structural
    problem the caller needs to see."""
    respx.post(API_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": None,
                "errors": [{"message": "Cannot query field 'foo' on type 'Transcript'."}],
            },
        )
    )
    with pytest.raises(FirefliesError):
        async for _ in client.list_meetings(MeetingFilter()):
            pass


@respx.mock
async def test_list_meetings_raises_on_required_field_error(
    client: FirefliesClient,
) -> None:
    """Errors targeting required fields (id, date) are not tolerable — Meeting
    can't be constructed without them, and silently dropping rows here would
    hide a real upstream regression."""
    respx.post(API_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {"transcripts": [None]},
                "errors": [
                    {
                        "message": "boom",
                        "path": ["transcripts", 0, "id"],
                        "code": "internal_error",
                    }
                ],
            },
        )
    )
    with pytest.raises(FirefliesError):
        async for _ in client.list_meetings(MeetingFilter()):
            pass


@respx.mock
async def test_list_meetings_skips_null_rows(client: FirefliesClient) -> None:
    """When a row's required field nulls bubble up to the row root, Fireflies
    returns ``null`` in the array. Skip — `_meeting_from_raw(None)` would
    crash, and crashing the page over one bad row would defeat the whole
    point of the field-level tolerance."""
    respx.post(API_URL).mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "data": {
                        "transcripts": [
                            None,
                            {
                                "id": "b",
                                "title": "M",
                                "date": "2026-01-01T10:00:00Z",
                                "duration": 1.0,
                                "host_email": "u@x.com",
                                "participants": [],
                                "transcript_url": None,
                            },
                        ]
                    }
                },
            ),
            httpx.Response(200, json={"data": {"transcripts": []}}),
        ]
    )
    meetings = [m async for m in client.list_meetings(MeetingFilter())]
    assert len(meetings) == 1
    assert meetings[0].meeting_id == "b"


@respx.mock
async def test_list_meetings_page_skips_null_rows(client: FirefliesClient) -> None:
    """SyncService drives list_meetings_page directly; the null-row guard must
    apply there too."""
    respx.post(API_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "transcripts": [
                        None,
                        {
                            "id": "c",
                            "title": "M",
                            "date": "2026-01-01T10:00:00Z",
                            "duration": 1.0,
                            "host_email": "u@x.com",
                            "participants": [],
                            "transcript_url": None,
                        },
                    ]
                }
            },
        )
    )
    page = [m async for m in client.list_meetings_page(skip=0, limit=50)]
    assert [m.meeting_id for m in page] == ["c"]


@respx.mock
async def test_graphql_internal_server_error_retries_then_transient() -> None:
    """Fireflies sometimes returns INTERNAL_SERVER_ERROR as a GraphQL-level
    error (HTTP 200 + ``errors`` array with ``extensions.status: 500``).
    Observed on deleteTranscript during the 2026-05-03 incident — 3 of 4
    deletes failed with this shape. The client must treat this exactly like
    an HTTP 5xx: retry once, then surface ``TransientServerError`` so the
    purge runner records a friendly per-meeting failure rather than dumping
    the raw error dict to the user."""
    fast_client = FirefliesClient(
        api_key="ff_secret_xyz",
        endpoint=API_URL,
        retry_5xx_max=1,
        retry_base_seconds=0.0,
    )
    err_payload = {
        "data": {"deleteTranscript": None},
        "errors": [
            {
                "friendly": False,
                "message": "Internal Server Error",
                "path": ["deleteTranscript"],
                "code": "INTERNAL_SERVER_ERROR",
                "extensions": {
                    "code": "INTERNAL_SERVER_ERROR",
                    "status": 500,
                    "correlationId": "abc",
                },
            }
        ],
    }
    route = respx.post(API_URL).mock(return_value=httpx.Response(200, json=err_payload))
    with pytest.raises(TransientServerError) as exc_info:
        await fast_client.delete_meeting("m1")
    # Initial attempt + one retry = 2 calls.
    assert route.call_count == 2
    msg = str(exc_info.value)
    assert "internal server error" in msg.lower()


@respx.mock
async def test_graphql_500_retry_succeeds_when_transient_clears(client: FirefliesClient) -> None:
    """A single 500 blip on a delete should be invisible to the caller —
    the retry succeeds and the operation proceeds normally."""
    err = {
        "data": {"deleteTranscript": None},
        "errors": [
            {
                "code": "INTERNAL_SERVER_ERROR",
                "message": "Internal Server Error",
                "path": ["deleteTranscript"],
                "extensions": {"code": "INTERNAL_SERVER_ERROR", "status": 500},
            }
        ],
    }
    ok = {"data": {"deleteTranscript": {"id": "m1"}}}
    route = respx.post(API_URL).mock(
        side_effect=[httpx.Response(200, json=err), httpx.Response(200, json=ok)]
    )
    await client.delete_meeting("m1")
    assert route.call_count == 2


@respx.mock
async def test_non_transient_graphql_error_uses_friendly_message_format(
    client: FirefliesClient,
) -> None:
    """Non-tolerable, non-rate-limit, non-5xx GraphQL errors surface as
    ``FirefliesError`` with the first error's ``message`` (prefixed by the
    operation path leaf when available) instead of the raw dict-list dump.
    Without this the UI showed walls of Python repr like
    ``graphql: [{'friendly': False, ...}]`` (user-reported 2026-05-03)."""
    respx.post(API_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": None,
                "errors": [
                    {
                        "message": "Cannot query field 'foo' on type 'Transcript'.",
                        "path": ["transcript", "foo"],
                    }
                ],
            },
        )
    )
    with pytest.raises(FirefliesError) as exc_info:
        async for _ in client.list_meetings(MeetingFilter()):
            pass
    msg = str(exc_info.value)
    assert "Cannot query field 'foo'" in msg
    assert "{'message'" not in msg  # raw dict format is gone
    assert "[{" not in msg  # raw list format is gone


@respx.mock
async def test_rate_limit_in_partial_response_still_raises_rate_limited(
    client: FirefliesClient,
) -> None:
    """Rate-limit detection must run *before* the field-tolerance branch.
    Otherwise a too_many_requests entry mixed with a path-scoped error would
    incorrectly downgrade to a tolerated warning."""
    respx.post(API_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {"transcripts": []},
                "errors": [
                    {
                        "code": "too_many_requests",
                        "message": "Too many requests",
                        "extensions": {"code": "too_many_requests", "status": 429},
                    }
                ],
            },
        )
    )
    with pytest.raises(RateLimitedError):
        async for _ in client.list_meetings(MeetingFilter()):
            pass


@pytest.mark.contract
@pytest.mark.skipif(
    not os.environ.get("FIREFLIES_TEST_API_KEY"),
    reason="FIREFLIES_TEST_API_KEY not set",
)
async def test_contract_list_one_real_meeting() -> None:
    """Hit live API and confirm schema fields parse into a Meeting."""
    real_client = FirefliesClient(api_key=os.environ["FIREFLIES_TEST_API_KEY"], page_size=1)
    count = 0
    async for m in real_client.list_meetings(MeetingFilter(limit=1)):
        assert m.meeting_id
        assert m.title
        count += 1
        break
    assert count <= 1
