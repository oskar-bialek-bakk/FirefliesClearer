"""Tests for build tooling: Tailwind compile script and Lucide icon vendoring.

These tests verify that the tooling artefacts exist and have the correct shape.
They do NOT execute the Tailwind CLI binary (which requires network access on
first run and is not appropriate for CI unit tests).
"""

import stat
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent

BUILD_SCRIPT = REPO_ROOT / "tools" / "build_static.sh"
TAILWIND_INPUT = REPO_ROOT / "firefliesclearer" / "web" / "tailwind.input.css"
ICONS_DIR = REPO_ROOT / "firefliesclearer" / "web" / "static" / "icons"

REQUIRED_ICONS = [
    "home.svg",
    "trash-2.svg",
    "bookmark.svg",
    "clock.svg",
    "settings.svg",
    "play.svg",
    "pause.svg",
    "check.svg",
    "x.svg",
    "alert-triangle.svg",
    "info.svg",
    "chevron-right.svg",
    "chevron-down.svg",
    "external-link.svg",
    "loader-2.svg",
    "star.svg",
]


class TestBuildScript:
    def test_build_script_exists(self) -> None:
        assert BUILD_SCRIPT.exists(), f"Build script not found: {BUILD_SCRIPT}"

    def test_build_script_is_executable(self) -> None:
        # On POSIX systems check the OS-level execute bit.
        # On Windows (NTFS) the execute bit is not stored in file metadata, so
        # we verify instead via the Git index mode (100755 = executable).
        import platform
        import subprocess

        if platform.system() == "Windows":
            result = subprocess.run(
                ["git", "ls-files", "--stage", str(BUILD_SCRIPT)],
                capture_output=True,
                text=True,
                cwd=str(REPO_ROOT),
            )
            assert result.returncode == 0, "git ls-files failed"
            # Mode field is the first token; 100755 indicates executable
            assert result.stdout.startswith("100755"), (
                f"Build script does not have git executable bit (100755): {result.stdout!r}"
            )
        else:
            mode = BUILD_SCRIPT.stat().st_mode
            assert mode & stat.S_IXUSR, f"Build script is not executable (owner +x): {BUILD_SCRIPT}"

    def test_build_script_contains_tailwind_download_logic(self) -> None:
        content = BUILD_SCRIPT.read_text(encoding="utf-8")
        assert "tailwindcss" in content
        assert "curl" in content
        assert "chmod +x" in content

    def test_build_script_references_correct_input_output_paths(self) -> None:
        content = BUILD_SCRIPT.read_text(encoding="utf-8")
        assert "tailwind.input.css" in content
        assert "static/styles.css" in content
        assert "--minify" in content

    def test_build_script_handles_windows_platform(self) -> None:
        content = BUILD_SCRIPT.read_text(encoding="utf-8")
        assert "windows-x64.exe" in content
        assert "MINGW" in content or "MSYS" in content


class TestTailwindInputCss:
    def test_tailwind_input_exists(self) -> None:
        assert TAILWIND_INPUT.exists(), f"tailwind.input.css not found: {TAILWIND_INPUT}"

    def test_tailwind_input_contains_base_directive(self) -> None:
        content = TAILWIND_INPUT.read_text(encoding="utf-8")
        assert "@tailwind base" in content

    def test_tailwind_input_contains_components_directive(self) -> None:
        content = TAILWIND_INPUT.read_text(encoding="utf-8")
        assert "@tailwind components" in content

    def test_tailwind_input_contains_utilities_directive(self) -> None:
        content = TAILWIND_INPUT.read_text(encoding="utf-8")
        assert "@tailwind utilities" in content

    def test_tailwind_input_contains_project_overrides(self) -> None:
        content = TAILWIND_INPUT.read_text(encoding="utf-8")
        assert "@layer components" in content
        assert ".sidebar" in content
        assert ".error-banner" in content


class TestLucideIcons:
    def test_icons_directory_exists(self) -> None:
        assert ICONS_DIR.exists(), f"Icons directory not found: {ICONS_DIR}"
        assert ICONS_DIR.is_dir()

    @pytest.mark.parametrize("icon_name", REQUIRED_ICONS)
    def test_icon_file_exists(self, icon_name: str) -> None:
        icon_path = ICONS_DIR / icon_name
        assert icon_path.exists(), f"Icon file not found: {icon_path}"

    @pytest.mark.parametrize("icon_name", REQUIRED_ICONS)
    def test_icon_file_is_valid_svg(self, icon_name: str) -> None:
        icon_path = ICONS_DIR / icon_name
        content = icon_path.read_text(encoding="utf-8").strip()
        assert content.startswith("<svg"), f"{icon_name} does not start with <svg: {content[:50]!r}"
        assert content.endswith("</svg>"), (
            f"{icon_name} does not end with </svg>: {content[-50:]!r}"
        )

    @pytest.mark.parametrize("icon_name", REQUIRED_ICONS)
    def test_icon_uses_current_color(self, icon_name: str) -> None:
        icon_path = ICONS_DIR / icon_name
        content = icon_path.read_text(encoding="utf-8")
        assert "currentColor" in content, f"{icon_name} does not use currentColor stroke"

    @pytest.mark.parametrize("icon_name", REQUIRED_ICONS)
    def test_icon_has_24x24_dimensions(self, icon_name: str) -> None:
        icon_path = ICONS_DIR / icon_name
        content = icon_path.read_text(encoding="utf-8")
        assert 'width="24"' in content, f"{icon_name} missing width=24"
        assert 'height="24"' in content, f"{icon_name} missing height=24"

    def test_all_required_icons_present(self) -> None:
        present = {p.name for p in ICONS_DIR.glob("*.svg")}
        missing = set(REQUIRED_ICONS) - present
        assert not missing, f"Missing icon files: {sorted(missing)}"

    def test_no_zero_byte_icon_files(self) -> None:
        for icon_name in REQUIRED_ICONS:
            icon_path = ICONS_DIR / icon_name
            assert icon_path.stat().st_size > 0, f"{icon_name} is empty (0 bytes)"
