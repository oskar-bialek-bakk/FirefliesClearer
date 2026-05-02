"""Shared CLI helpers: load config, build dependencies."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from rich.console import Console

from firefliesclearer.core.archiver import Archiver
from firefliesclearer.core.manifest import Manifest
from firefliesclearer.core.pipeline import Pipeline
from firefliesclearer.infra.config import (
    AppConfig,
    load_config,
    user_config_path,
)
from firefliesclearer.infra.fireflies_client import FirefliesClient
from firefliesclearer.infra.manifest_backed_repo import ManifestBackedRepository
from firefliesclearer.infra.pdf_renderer import ReportlabSummaryRenderer
from firefliesclearer.infra.system_clock import SystemClock
from firefliesclearer.ports.clock import Clock

console = Console()


@dataclass
class Deps:
    config: AppConfig
    pipeline: Pipeline
    manifest: Manifest
    client: FirefliesClient
    clock: Clock
    # MeetingRepository for read paths. After Phase 6 cleanup this is always
    # ``ManifestBackedRepository(manifest)`` — the cache adapter — regardless
    # of ``[sync] enabled``. The flag now only controls whether the scheduler
    # runs; the read path is unconditional.  When omitted, defaults to the
    # cache adapter so legacy callers stay forward-compatible.
    scan_repo: object = field(default=None)

    def __post_init__(self) -> None:
        if self.scan_repo is None:
            self.scan_repo = ManifestBackedRepository(self.manifest)


def build_deps(*, config_override: Path | None = None) -> Deps:
    user_path = config_override or user_config_path()
    cfg = load_config(user_config=user_path)
    archive_root = cfg.archive.root_dir
    archive_root.mkdir(parents=True, exist_ok=True)
    manifest = Manifest.open(archive_root / "manifest.db")
    archiver = Archiver(archive_root=archive_root)
    renderer = ReportlabSummaryRenderer()
    client = FirefliesClient(api_key=cfg.fireflies.api_key)
    clock = SystemClock()
    pipeline = Pipeline(
        repository=client,
        manifest=manifest,
        archiver=archiver,
        renderer=renderer,
        clock=clock,
    )
    # Phase 6: cache adapter is unconditional; the [sync] flag now only
    # controls whether the scheduler runs.
    scan_repo = ManifestBackedRepository(manifest)
    return Deps(
        config=cfg,
        pipeline=pipeline,
        manifest=manifest,
        client=client,
        clock=clock,
        scan_repo=scan_repo,
    )
