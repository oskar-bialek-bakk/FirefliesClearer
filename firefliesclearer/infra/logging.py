"""Structured JSON-lines logging with daily rotation and key redaction."""

from __future__ import annotations

import contextlib
import json
import logging
import re
from datetime import UTC, datetime
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any

_BEARER_PATTERN = re.compile(r"Bearer\s+[A-Za-z0-9._\-]+", re.IGNORECASE)
_API_KEY_PATTERN = re.compile(r"ff_[A-Za-z0-9._\-]+")

_LOG_RECORD_FIELDS = frozenset(
    {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "message",
        "asctime",
        "taskName",
    }
)


def _redact(value: Any) -> Any:
    """Recursively redact bearer tokens and ff_ API keys in any value."""
    if isinstance(value, str):
        v = _BEARER_PATTERN.sub("Bearer [REDACTED]", value)
        v = _API_KEY_PATTERN.sub("[REDACTED]", v)
        return v
    if isinstance(value, dict):
        return {k: _redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(v) for v in value]
    if isinstance(value, tuple):
        return tuple(_redact(v) for v in value)
    return value


class JsonLineFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        msg = _redact(record.getMessage())
        out: dict[str, Any] = {
            "ts": datetime.now(tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": msg,
        }
        for key, value in record.__dict__.items():
            if key in _LOG_RECORD_FIELDS:
                continue
            redacted = _redact(value)
            try:
                json.dumps(redacted)
            except (TypeError, ValueError):
                redacted = str(redacted)
            out[key] = redacted
        return json.dumps(out, ensure_ascii=False)


def setup_logging(*, log_dir: Path, level: str = "INFO") -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now(tz=UTC).strftime("%Y-%m-%d")
    log_file = log_dir / f"{today}.log"
    handler = TimedRotatingFileHandler(log_file, when="midnight", backupCount=30, encoding="utf-8")
    handler.setFormatter(JsonLineFormatter())
    root = logging.getLogger()
    # Close existing handlers so repeated setup_logging() calls (tests, REPLs,
    # embedding) don't leak file descriptors or rotation threads.
    for existing in list(root.handlers):
        root.removeHandler(existing)
        with contextlib.suppress(Exception):
            existing.close()
    root.addHandler(handler)
    root.setLevel(level)
