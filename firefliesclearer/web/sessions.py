"""In-process session store keyed by the session cookie value.

Thread-safe enough for FastAPI's single-process deployment; not multi-worker
compatible (we run one uvicorn worker by design).
"""

from __future__ import annotations

import threading
from typing import Any


class SessionStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: dict[str, dict[str, Any]] = {}

    def get(self, sid: str) -> dict[str, Any]:
        with self._lock:
            return dict(self._data.get(sid, {}))

    def set(self, sid: str, payload: dict[str, Any]) -> None:
        with self._lock:
            self._data[sid] = dict(payload)

    def update(self, sid: str, patch: dict[str, Any]) -> None:
        with self._lock:
            current = dict(self._data.get(sid, {}))
            current.update(patch)
            self._data[sid] = current

    def delete(self, sid: str) -> None:
        with self._lock:
            self._data.pop(sid, None)
