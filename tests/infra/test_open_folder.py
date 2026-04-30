"""Tests for infra/open_folder.py."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from firefliesclearer.infra.open_folder import open_folder


@pytest.mark.parametrize(
    "platform,expected_cmd",
    [
        ("win32", "explorer.exe"),
        ("darwin", "open"),
        ("linux", "xdg-open"),
    ],
)
def test_open_folder_calls_correct_command_per_platform(
    platform: str, expected_cmd: str, tmp_path: Path
) -> None:
    """open_folder uses the correct command for each platform."""
    with (
        patch("firefliesclearer.infra.open_folder.sys.platform", platform),
        patch("firefliesclearer.infra.open_folder.subprocess.run") as mock_run,
    ):
        open_folder(tmp_path)

    mock_run.assert_called_once()
    cmd_list = mock_run.call_args[0][0]
    assert isinstance(cmd_list, list), "Must use list args, not a string"
    assert cmd_list[0] == expected_cmd
    assert cmd_list[1] == str(tmp_path)
    # Must NOT use shell=True
    assert mock_run.call_args.kwargs.get("shell") is not True


def test_open_folder_passes_path_as_string(tmp_path: Path) -> None:
    """The path argument is passed as a string, not a Path object."""
    with (
        patch("firefliesclearer.infra.open_folder.sys.platform", "linux"),
        patch("firefliesclearer.infra.open_folder.subprocess.run") as mock_run,
    ):
        open_folder(tmp_path)

    cmd_list = mock_run.call_args[0][0]
    assert isinstance(cmd_list[1], str)


def test_open_folder_uses_check_false(tmp_path: Path) -> None:
    """subprocess.run is called with check=False."""
    with (
        patch("firefliesclearer.infra.open_folder.sys.platform", "linux"),
        patch("firefliesclearer.infra.open_folder.subprocess.run") as mock_run,
    ):
        open_folder(tmp_path)

    assert mock_run.call_args.kwargs.get("check") is False
