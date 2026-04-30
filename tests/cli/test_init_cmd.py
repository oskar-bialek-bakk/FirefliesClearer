"""Tests for the deprecated `init` command stub."""

from __future__ import annotations

from typer.testing import CliRunner

from firefliesclearer.cli.app import app


def test_init_prints_redirect_and_exits_zero() -> None:
    r = CliRunner().invoke(app, ["init"])
    assert r.exit_code == 0
    assert "serve" in r.output
