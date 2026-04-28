"""Async GraphQL client for Fireflies AI."""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import httpx

from firefliesclearer.core.models import ArtifactBundle, Meeting
from firefliesclearer.ports.meeting_repository import MeetingFilter

logger = logging.getLogger(__name__)

LIST_QUERY = """
query Transcripts($limit: Int, $skip: Int, $toDate: DateTime) {
  transcripts(limit: $limit, skip: $skip, toDate: $toDate) {
    id
    title
    date
    duration
    host_email
    participants
    transcript_url
    summary { overview action_items keywords }
    audio_url
  }
}
"""

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
    participants
    transcript_url
    audio_url
    summary { overview action_items keywords }
    sentences { speaker_name text }
  }
}
"""


class FirefliesError(Exception):
    pass


class FirefliesClient:
    def __init__(
        self,
        *,
        api_key: str,
        endpoint: str = "https://api.fireflies.ai/graphql",
        retry_max: int = 3,
        retry_base_seconds: float = 1.0,
        page_size: int = 50,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._api_key = api_key
        self._endpoint = endpoint
        self._retry_max = retry_max
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
                for raw in items:
                    yield _meeting_from_raw(raw)
                    emitted += 1
                    if filter.limit and emitted >= filter.limit:
                        return
                skip += len(items)

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
            except httpx.HTTPError as e:
                if attempt >= self._retry_max:
                    raise FirefliesError(f"network: {e}") from e
                await self._sleep(attempt, retry_after=None)
                continue

            if resp.status_code == 429:
                if attempt >= self._retry_max:
                    raise FirefliesError("rate limited")
                ra = resp.headers.get("Retry-After")
                await self._sleep(attempt, retry_after=ra)
                continue
            if 500 <= resp.status_code < 600:
                if attempt >= self._retry_max:
                    raise FirefliesError(f"server {resp.status_code}")
                await self._sleep(attempt, retry_after=None)
                continue
            if 400 <= resp.status_code < 500:
                raise FirefliesError(f"{resp.status_code}: {resp.text[:200]}")

            data: dict[str, Any] = resp.json()
            if data.get("errors"):
                raise FirefliesError(f"graphql: {data['errors']}")
            return data

        raise FirefliesError("retries exhausted")

    async def _sleep(self, attempt: int, *, retry_after: str | None) -> None:
        if retry_after is not None:
            try:
                await asyncio.sleep(min(float(retry_after), 60.0))
                return
            except ValueError:
                pass
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
        host_email=raw.get("host_email") or "",
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
