"""Shared CLI helpers: load config, build dependencies."""

from __future__ import annotations

from dataclasses import dataclass
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
from firefliesclearer.infra.pdf_renderer import ReportlabSummaryRenderer
from firefliesclearer.infra.system_clock import SystemClock

console = Console()


@dataclass
class Deps:
    config: AppConfig
    pipeline: Pipeline
    manifest: Manifest
    client: FirefliesClient


def build_deps(*, config_override: Path | None = None) -> Deps:
    user_path = config_override or user_config_path()
    cfg = load_config(user_config=user_path)
    archive_root = cfg.archive.root_dir
    archive_root.mkdir(parents=True, exist_ok=True)
    manifest = Manifest.open(archive_root / "manifest.db")
    archiver = Archiver(archive_root=archive_root)
    renderer = ReportlabSummaryRenderer()
    client = FirefliesClient(api_key=cfg.fireflies.api_key)
    pipeline = Pipeline(
        repository=client,
        manifest=manifest,
        archiver=archiver,
        renderer=renderer,
        clock=SystemClock(),
    )
    return Deps(config=cfg, pipeline=pipeline, manifest=manifest, client=client)
