"""Smoke test for `firefliesclearer serve` — argv parsing only.

Actual server boot would require a real config + free port + uvicorn lifecycle,
which is verified manually (Task 2.7) and end-to-end-tested later.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import tomli_w
from typer.testing import CliRunner

from firefliesclearer.application.setup_service import migrate_v1_rules_auto
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


def test_serve_cmd_console_print_strings_are_ascii_safe() -> None:
    """Regression: every console.print / typer.echo string in serve_cmd.py must be ASCII.

    Windows default console encoding is cp1252; printing non-ASCII glyphs (e.g. an arrow
    "->") via Rich's legacy_windows renderer raises UnicodeEncodeError BEFORE uvicorn
    starts, so the server never binds and the user sees a confusing crash.
    """
    src = (
        Path(__file__).resolve().parents[2] / "firefliesclearer" / "cli" / "serve_cmd.py"
    ).read_text(encoding="utf-8")
    offenders: list[tuple[int, str]] = []
    for i, line in enumerate(src.splitlines(), 1):
        if "console.print" in line or "typer.echo" in line:
            for ch in line:
                if ord(ch) > 127:
                    offenders.append((i, line.rstrip()))
                    break
    assert not offenders, (
        f"non-ASCII characters in console.print/typer.echo lines (crashes Windows cp1252): {offenders}"
    )


# ---------------------------------------------------------------------------
# Wiring: migrate_v1_rules_auto is called by serve on startup
# ---------------------------------------------------------------------------


def test_serve_wiring_migration_function_is_imported_from_serve_cmd() -> None:
    """Verify that migrate_v1_rules_auto is wired into serve_cmd at module level."""
    import firefliesclearer.cli.serve_cmd as serve_module

    assert hasattr(serve_module, "migrate_v1_rules_auto"), (
        "migrate_v1_rules_auto must be imported and available in serve_cmd"
    )


def test_migrate_v1_rules_auto_converts_v1_config(tmp_path: Path) -> None:
    """Integration: calling migrate_v1_rules_auto directly on a v1 config produces a preset.

    This is the functional wiring check: the same function that serve_cmd calls
    works end-to-end — a v1 TOML gets a preset and loses [rules.auto].
    """
    cfg_path = tmp_path / "config.toml"
    v1_payload = {
        "fireflies": {"api_key": "ff_wiring_test"},
        "archive": {"root_dir": str(tmp_path / "arch")},
        "rules": {
            "auto": {
                "older_than_days": 120,
                "delete_failed_transcripts": True,
            }
        },
    }
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cfg_path, "wb") as f:
        tomli_w.dump(v1_payload, f)

    migrated = migrate_v1_rules_auto(cfg_path)

    assert migrated is True
    bak_path = Path(str(cfg_path) + ".v1.bak")
    assert bak_path.exists()
    with open(cfg_path, "rb") as f:
        data = tomllib.load(f)
    assert data["presets"][0]["name"] == "Auto cleanup"
    assert data["presets"][0]["filters"]["older_than_days"] == 120
    assert "auto" not in data.get("rules", {})


# ---------------------------------------------------------------------------
# build_deps: scan_repo selection based on [sync] enabled
# ---------------------------------------------------------------------------


def test_build_deps_uses_cache_repo_for_scan_when_sync_enabled(tmp_path: Path) -> None:
    """When [sync] enabled = true, build_deps assigns a scan_repo distinct from
    the live client - the cache adapter."""
    cfg_path = tmp_path / "config.toml"
    archive = tmp_path / "archive"
    archive.mkdir()
    cfg_path.write_text(
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
    from firefliesclearer.cli._common import build_deps
    from firefliesclearer.infra.manifest_backed_repo import ManifestBackedRepository

    deps = build_deps(config_override=cfg_path)
    assert hasattr(deps, "scan_repo")
    assert isinstance(deps.scan_repo, ManifestBackedRepository)
    # The mutation client is still the live FirefliesClient
    assert not isinstance(deps.client, ManifestBackedRepository)


def test_build_deps_always_uses_cache_adapter(tmp_path: Path) -> None:
    """After Phase 6 cleanup, scan_repo is always the cache adapter, regardless
    of sync.enabled. The flag now only controls scheduler startup."""
    cfg_path = tmp_path / "config.toml"
    archive = tmp_path / "archive"
    archive.mkdir()
    cfg_path.write_text(
        f"""
[fireflies]
api_key = "ff_test"
[archive]
root_dir = "{archive.as_posix()}"
""",  # no [sync] section -> default disabled
        encoding="utf-8",
    )
    from firefliesclearer.cli._common import build_deps
    from firefliesclearer.infra.manifest_backed_repo import ManifestBackedRepository

    deps = build_deps(config_override=cfg_path)
    assert isinstance(deps.scan_repo, ManifestBackedRepository)
    # The mutation client is still the live FirefliesClient
    assert not isinstance(deps.client, ManifestBackedRepository)
