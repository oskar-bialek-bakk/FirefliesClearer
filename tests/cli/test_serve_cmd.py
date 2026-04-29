"""Smoke test for `firefliesclearer serve` — argv parsing only.

Actual server boot would require a real config + free port + uvicorn lifecycle,
which is verified manually (Task 2.7) and end-to-end-tested later.
"""

from __future__ import annotations

import re

from typer.testing import CliRunner

from firefliesclearer.cli.app import app

# Typer/Click renders help via Rich on Linux CI with ANSI styling that can
# fragment option names across escape sequences (e.g. `\x1b[36m--\x1b[0m\x1b[1mhost\x1b[0m`),
# while local Windows runs render plain text. Strip ANSI before substring checks.
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(s: str) -> str:
    return _ANSI_RE.sub("", s)


def test_serve_help_lists_options():
    runner = CliRunner()
    r = runner.invoke(app, ["serve", "--help"])
    assert r.exit_code == 0
    output = _strip_ansi(r.output)
    assert "--host" in output
    assert "--port" in output
    assert "--no-open" in output


def test_serve_refuses_non_loopback_host_without_flag():
    runner = CliRunner()
    r = runner.invoke(app, ["serve", "--host", "0.0.0.0"])
    assert r.exit_code == 2
    assert "Refusing to bind a non-loopback host" in _strip_ansi(r.output)
