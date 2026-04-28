"""Tests for `firefliesclearer init`."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from firefliesclearer.cli.app import app
from firefliesclearer.infra.config import load_config

runner = CliRunner()


def test_init_writes_config_file(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.toml"
    archive_root = tmp_path / "arch"
    result = runner.invoke(
        app,
        ["init", "--config", str(cfg_path), "--no-ping"],
        input=f"ff_test_key\n{archive_root}\n180\ny\n",
    )
    assert result.exit_code == 0, result.stdout
    assert cfg_path.exists()
    cfg = load_config(user_config=cfg_path)
    assert cfg.fireflies.api_key == "ff_test_key"
    assert cfg.archive.root_dir == archive_root


def test_init_does_not_echo_api_key(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.toml"
    archive_root = tmp_path / "arch"
    result = runner.invoke(
        app,
        ["init", "--config", str(cfg_path), "--no-ping"],
        input=f"ff_secret_key\n{archive_root}\n180\ny\n",
    )
    assert "ff_secret_key" not in result.stdout
