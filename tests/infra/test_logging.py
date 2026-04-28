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
