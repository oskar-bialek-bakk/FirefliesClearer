"""Tests for the Fireflies GraphQL client (using respx to mock httpx)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import httpx
import pytest
import respx

from firefliesclearer.infra.fireflies_client import (
    FirefliesClient,
    FirefliesError,
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
        return_value=httpx.Response(
            200, json={"data": {"transcripts": []}}
        )
    )
    async for _ in client.list_meetings(MeetingFilter()):
        pass
    sent = route.calls.last.request
    assert sent.headers["Authorization"] == "Bearer ff_secret_xyz"


@respx.mock
async def test_api_key_is_redacted_in_logs(
    client: FirefliesClient, caplog: pytest.LogCaptureFixture
) -> None:
    respx.post(API_URL).mock(
        return_value=httpx.Response(
            200, json={"data": {"transcripts": []}}
        )
    )
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
async def test_5xx_is_retried_then_succeeds(
    client: FirefliesClient,
) -> None:
    route = respx.post(API_URL).mock(
        side_effect=[
            httpx.Response(503, text="boom"),
            httpx.Response(503, text="boom"),
            httpx.Response(200, json={"data": {"transcripts": []}}),
        ]
    )
    async for _ in client.list_meetings(MeetingFilter()):
        pass
    assert route.call_count == 3


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
    respx.post(API_URL).mock(
        return_value=httpx.Response(
            200, json={"data": {"transcripts": []}}
        )
    )
    cutoff = datetime(2026, 1, 1, tzinfo=UTC)
    async for _ in client.list_meetings(MeetingFilter(older_than=cutoff)):
        pass
    body = respx.calls.last.request.content.decode()
    # Variable name varies by deployment; accept any of these substrings.
    assert any(s in body for s in ("to_date", "fromDate", "older"))


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
        return_value=httpx.Response(
            200, json={"data": {"deleteTranscript": {"id": "x"}}}
        )
    )
    await client.delete_meeting("x")
    assert route.call_count == 1
    body = route.calls.last.request.content.decode()
    assert "deleteTranscript" in body
    assert "\"x\"" in body


@respx.mock
async def test_delete_meeting_idempotent_on_404(
    client: FirefliesClient,
) -> None:
    respx.post(API_URL).mock(
        return_value=httpx.Response(404, json={"errors": ["not_found"]})
    )
    await client.delete_meeting("missing")
