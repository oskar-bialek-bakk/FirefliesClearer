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
