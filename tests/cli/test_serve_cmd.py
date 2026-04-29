"""Smoke test for `firefliesclearer serve` — argv parsing only.

Actual server boot would require a real config + free port + uvicorn lifecycle,
which is verified manually (Task 2.7) and end-to-end-tested later.
"""

from __future__ import annotations

from typer.testing import CliRunner

from firefliesclearer.cli.app import app


def test_serve_help_lists_options():
    runner = CliRunner()
    r = runner.invoke(app, ["serve", "--help"])
    assert r.exit_code == 0
    assert "--host" in r.output
    assert "--port" in r.output
    assert "--no-open" in r.output


def test_serve_refuses_non_loopback_host_without_flag():
    runner = CliRunner()
    r = runner.invoke(app, ["serve", "--host", "0.0.0.0"])
    assert r.exit_code == 2
    assert "Refusing to bind a non-loopback host" in r.output
