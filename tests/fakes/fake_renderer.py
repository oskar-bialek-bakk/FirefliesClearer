"""FakeSummaryRenderer returns deterministic bytes."""

from __future__ import annotations

from typing import Any


class FakeSummaryRenderer:
    def __init__(self, output: bytes = b"%PDF-FAKE") -> None:
        self._output = output
        self.calls: list[dict[str, Any]] = []

    def render(self, summary_payload: dict[str, Any], *, meeting_title: str) -> bytes:
        self.calls.append({"payload": summary_payload, "title": meeting_title})
        return self._output
