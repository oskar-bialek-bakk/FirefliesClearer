"""Tests for config: schema validation and precedence chain."""

from __future__ import annotations

from pathlib import Path

import pytest

from firefliesclearer.infra.config import (
    AppConfig,
    ConfigError,
    load_config,
    write_config,
)


def _write_user(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "user.toml"
    p.write_text(content, encoding="utf-8")
    return p


def test_minimal_valid_config_parses(tmp_path: Path) -> None:
    user = _write_user(
        tmp_path,
        """
        [fireflies]
        api_key = "ff_xyz"

        [archive]
        root_dir = "C:/tmp/arch"
        summary_format = "pdf"
        """,
    )
    cfg = load_config(user_config=user)
    assert cfg.fireflies.api_key == "ff_xyz"
    assert cfg.archive.root_dir == Path("C:/tmp/arch")
    assert cfg.archive.summary_format == "pdf"


def test_missing_api_key_raises_actionable_error(tmp_path: Path) -> None:
    user = _write_user(
        tmp_path,
        """
        [archive]
        root_dir = "C:/tmp/arch"
        summary_format = "pdf"
        """,
    )
    with pytest.raises(ConfigError) as exc:
        load_config(user_config=user)
    assert "firefliesclearer init" in str(exc.value)


def test_env_var_overrides_user_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _write_user(
        tmp_path,
        """
        [fireflies]
        api_key = "from_user"
        [archive]
        root_dir = "C:/tmp/arch"
        summary_format = "pdf"
        """,
    )
    monkeypatch.setenv("FIREFLIES_API_KEY", "from_env")
    cfg = load_config(user_config=user)
    assert cfg.fireflies.api_key == "from_env"


def test_project_config_overrides_user_config(tmp_path: Path) -> None:
    user = _write_user(
        tmp_path,
        """
        [fireflies]
        api_key = "from_user"
        [archive]
        root_dir = "C:/user/arch"
        summary_format = "pdf"
        """,
    )
    project = tmp_path / "firefliesclearer.toml"
    project.write_text(
        """
        [archive]
        root_dir = "C:/proj/arch"
        """,
        encoding="utf-8",
    )
    cfg = load_config(user_config=user, project_config=project)
    assert cfg.archive.root_dir == Path("C:/proj/arch")
    assert cfg.fireflies.api_key == "from_user"


def test_cli_override_beats_everything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _write_user(
        tmp_path,
        """
        [fireflies]
        api_key = "from_user"
        [archive]
        root_dir = "C:/tmp/arch"
        summary_format = "pdf"
        """,
    )
    monkeypatch.setenv("FIREFLIES_API_KEY", "from_env")
    cfg = load_config(
        user_config=user,
        cli_overrides={"fireflies.api_key": "from_cli"},
    )
    assert cfg.fireflies.api_key == "from_cli"


def test_invalid_summary_format_rejected(tmp_path: Path) -> None:
    user = _write_user(
        tmp_path,
        """
        [fireflies]
        api_key = "x"
        [archive]
        root_dir = "C:/tmp"
        summary_format = "docx"
        """,
    )
    with pytest.raises(ConfigError):
        load_config(user_config=user)


def test_write_config_round_trip(tmp_path: Path) -> None:
    cfg = AppConfig.model_validate(
        {
            "fireflies": {"api_key": "ff_xyz"},
            "archive": {
                "root_dir": str(tmp_path / "arch"),
                "summary_format": "pdf",
            },
        }
    )
    target = tmp_path / "out.toml"
    write_config(cfg, target)
    loaded = load_config(user_config=target)
    assert loaded.fireflies.api_key == "ff_xyz"
