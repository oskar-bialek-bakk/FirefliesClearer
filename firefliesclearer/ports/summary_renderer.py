"""Summary renderer port: produces PDF bytes from a summary payload."""

from __future__ import annotations

from typing import Any, Protocol


class SummaryRenderer(Protocol):
    def render(self, summary_payload: dict[str, Any], *, meeting_title: str) -> bytes: ...
