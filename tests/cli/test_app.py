"""Tests for top-level Typer app."""

from __future__ import annotations

from typer.testing import CliRunner

from firefliesclearer import __version__
from firefliesclearer.cli.app import app

runner = CliRunner()


def test_version_flag_prints_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_help_runs() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
