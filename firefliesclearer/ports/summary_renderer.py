"""Summary renderer port: produces PDF bytes from a summary payload."""

from __future__ import annotations

from typing import Any, Protocol

from firefliesclearer.core.models import Meeting


class SummaryRenderer(Protocol):
    def render(
        self,
        summary_payload: dict[str, Any],
        *,
        meeting_title: str,
        meeting: Meeting | None = None,
        source_url: str | None = None,
    ) -> bytes: ...
