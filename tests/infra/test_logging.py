"""Tests for structured JSON logging with API-key redaction."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from firefliesclearer.infra.logging import setup_logging


def test_json_lines_emitted_to_file(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    setup_logging(log_dir=log_dir, level="INFO")
    logging.getLogger("test").info("hello", extra={"event": "x", "n": 1})
    files = list(log_dir.glob("*.log"))
    assert files, "log file not created"
    line = files[0].read_text(encoding="utf-8").strip().splitlines()[-1]
    parsed = json.loads(line)
    assert parsed["msg"] == "hello"
    assert parsed["event"] == "x"
    assert parsed["n"] == 1


def test_authorization_header_redacted(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    setup_logging(log_dir=log_dir, level="INFO")
    logging.getLogger("test").info("request: Authorization: Bearer ff_super_secret")
    line = next(iter(log_dir.glob("*.log"))).read_text(encoding="utf-8")
    assert "ff_super_secret" not in line
    assert "[REDACTED]" in line


def test_redaction_applies_to_extra_fields(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    setup_logging(log_dir=log_dir, level="INFO")
    logging.getLogger("test").info(
        "ok",
        extra={
            "auth_header": "Bearer ff_extra_secret",
            "nested": {"token": "ff_inside_dict"},
            "tokens": ["ff_in_list", "plain"],
        },
    )
    line = next(iter(log_dir.glob("*.log"))).read_text(encoding="utf-8")
    parsed = json.loads(line.strip().splitlines()[-1])
    assert "ff_extra_secret" not in line
    assert "ff_inside_dict" not in line
    assert "ff_in_list" not in line
    assert parsed["auth_header"] == "Bearer [REDACTED]"
    assert parsed["nested"] == {"token": "[REDACTED]"}
    assert parsed["tokens"] == ["[REDACTED]", "plain"]


def test_setup_logging_is_idempotent_and_closes_old_handlers(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    setup_logging(log_dir=log_dir, level="INFO")
    first = list(logging.getLogger().handlers)
    assert len(first) == 1

    setup_logging(log_dir=log_dir, level="INFO")
    second = list(logging.getLogger().handlers)
    # Still exactly one handler — old one was closed and replaced.
    assert len(second) == 1
    assert second[0] is not first[0]
    # Old handler's stream must be closed (rolling/rotation thread released).
    assert getattr(first[0], "stream", None) is None or first[0].stream.closed
