"""Tests for `firefliesclearer sync` CLI command."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from firefliesclearer.cli.app import app

runner = CliRunner()


def _write_minimal_config(tmp_path: Path) -> Path:
    cfg = tmp_path / "config.toml"
    archive = tmp_path / "arch"
    archive.mkdir()
    cfg.write_text(
        f"""
[fireflies]
api_key = "ff_test"
[archive]
root_dir = "{archive.as_posix()}"
[sync]
enabled = true
""",
        encoding="utf-8",
    )
    return cfg


def test_sync_command_is_registered() -> None:
    """`firefliesclearer sync --help` prints the command help."""
    r = runner.invoke(app, ["sync", "--help"])
    assert r.exit_code == 0


def test_sync_command_runs_incremental_by_default(tmp_path: Path) -> None:
    cfg = _write_minimal_config(tmp_path)
    result = runner.invoke(app, ["sync", "--config", str(cfg), "--dry-run"])
    assert result.exit_code == 0
    assert "incremental" in result.output.lower()


def test_sync_command_full_flag_runs_full(tmp_path: Path) -> None:
    cfg = _write_minimal_config(tmp_path)
    result = runner.invoke(app, ["sync", "--config", str(cfg), "--full", "--dry-run"])
    assert result.exit_code == 0
    assert "full" in result.output.lower()
