"""Weekly destructive smoke test against the live Fireflies account.

Run via GitHub Actions on a cron schedule. Reads ``FIREFLIES_API_KEY`` from the
environment (injected from a repo secret). Picks the OLDEST meeting titled
"Daily Team OB" and walks it through archive + purge end-to-end, then verifies
the meeting is gone from Fireflies. Also boots ``firefliesclearer serve``
briefly and probes every page for HTTP 200 to catch web-layer regressions.

Exits 0 on success, 1 on any failure. Stdout is the log; CI captures it and
posts to the failure issue if anything blew up.

Run manually:

    FIREFLIES_API_KEY=ff_xxx python scripts/scheduled_smoke.py

The target-meeting heuristic ("oldest Daily Team OB"): the user (oskar) has
this meeting daily, so there's always at least one candidate. Picking the
oldest means we never delete anything recent or actively useful.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

import tomli_w

from firefliesclearer.core.archiver import Archiver
from firefliesclearer.core.manifest import Manifest
from firefliesclearer.core.models import Meeting, MeetingState
from firefliesclearer.core.pipeline import Pipeline
from firefliesclearer.infra.fireflies_client import FirefliesClient
from firefliesclearer.infra.pdf_renderer import ReportlabSummaryRenderer
from firefliesclearer.infra.system_clock import SystemClock
from firefliesclearer.ports.meeting_repository import MeetingFilter

TARGET_TITLE = "Daily Team OB"
WEB_PROBE_PAGES = ("/", "/cleanup", "/presets", "/history", "/settings")
SERVE_BOOT_TIMEOUT_SECONDS = 15


def log(msg: str) -> None:
    """Timestamped stdout line so the CI log is grepable."""
    ts = datetime.now(tz=UTC).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


async def find_oldest_target_meeting(
    client: FirefliesClient,
) -> Meeting | None:
    """Walk the live API, return the oldest meeting matching TARGET_TITLE.

    The Fireflies list is paginated newest-first; we exhaust pages and pick
    the minimum by ``meeting_date``. For a daily-recurring meeting this is
    bounded (~365 hits/year).
    """
    candidates: list[Meeting] = []
    async for m in client.list_meetings(MeetingFilter()):
        if m.title.strip() == TARGET_TITLE:
            candidates.append(m)
    if not candidates:
        return None
    return min(candidates, key=lambda m: m.meeting_date)


async def run_archive_purge(client: FirefliesClient, meeting: Meeting, archive_root: Path) -> None:
    """Archive then purge ``meeting`` against the real API. Verify on disk."""
    archive_root.mkdir(parents=True, exist_ok=True)
    manifest = Manifest.open(archive_root / "manifest.db")
    pipeline = Pipeline(
        repository=client,
        manifest=manifest,
        archiver=Archiver(archive_root=archive_root),
        renderer=ReportlabSummaryRenderer(),
        clock=SystemClock(),
    )

    log(f"archive_one({meeting.meeting_id}) starting…")
    state = await pipeline.archive_one(meeting)
    log(f"archive_one → {state}")
    if state is not MeetingState.ARCHIVED:
        rec = manifest.get(meeting.meeting_id)
        last_err = rec.last_error if rec else "(no manifest record)"
        raise RuntimeError(f"archive failed (state={state}, last_error={last_err})")

    # Sanity check on disk: at least metadata.json must exist for an archived
    # meeting. summary.pdf and transcript.md should also be present in the
    # canonical layout.
    rec = manifest.get(meeting.meeting_id)
    if rec is None or not rec.archive_path:
        raise RuntimeError("archived meeting has no archive_path in manifest")
    folder = Path(rec.archive_path)
    if not (folder / "metadata.json").exists():
        raise RuntimeError(f"metadata.json missing under {folder}")
    log(f"on-disk verified: {folder}")

    log(f"purge_one({meeting.meeting_id}) starting…")
    state = await pipeline.purge_one(meeting)
    log(f"purge_one → {state}")
    if state is not MeetingState.DELETED:
        rec = manifest.get(meeting.meeting_id)
        last_err = rec.last_error if rec else "(no manifest record)"
        raise RuntimeError(f"purge failed (state={state}, last_error={last_err})")


async def verify_meeting_gone(client: FirefliesClient, meeting_id: str) -> None:
    """Re-list meetings; the deleted ID must no longer appear."""
    async for m in client.list_meetings(MeetingFilter()):
        if m.meeting_id == meeting_id:
            raise RuntimeError(f"meeting {meeting_id!r} still appears in Fireflies after purge")
    log(f"verified deletion: {meeting_id} no longer in Fireflies list")


def _pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        assert isinstance(port, int)
        return port


def probe_web_routes(api_key: str, archive_root: Path) -> None:
    """Boot ``firefliesclearer serve`` and probe each page for HTTP 200.

    Smokes the web layer separately from the data plane. A bad template, a
    broken route registration, or a regression in the security middleware will
    show up here as a non-200 response.
    """
    port = _pick_free_port()
    config_path = archive_root / "config.toml"
    payload = {
        "fireflies": {"api_key": api_key},
        "archive": {"root_dir": str(archive_root), "summary_format": "pdf"},
        "run": {
            "concurrency": 3,
            "delete_confirmation_threshold": 10,
            "default_age_days": 180,
            "log_retention_days": 30,
        },
    }
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with config_path.open("wb") as f:
        tomli_w.dump(payload, f)

    log(f"booting serve on port {port}…")
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    # Use the console-script binary on PATH (placed there by `pip install`) —
    # `python -m firefliesclearer.cli.app` has a known circular-import bug
    # because the side-effect command imports loop back through `cli.app`.
    binary = shutil.which("firefliesclearer")
    if binary is None:  # pragma: no cover - CI always has it on PATH
        raise RuntimeError("firefliesclearer not on PATH; did pip install -e . run?")
    proc = subprocess.Popen(
        [
            binary,
            "serve",
            "--port",
            str(port),
            "--no-open",
            "--config",
            str(config_path),
        ],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        # Wait for either the URL banner or the boot timeout.
        token: str | None = None
        deadline = time.monotonic() + SERVE_BOOT_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            assert proc.stdout is not None
            line = proc.stdout.readline()
            if not line:
                if proc.poll() is not None:
                    raise RuntimeError("serve exited before binding")
                continue
            if "running at" in line and "?token=" in line:
                token = line.split("?token=", 1)[1].strip()
                log("serve up; probing pages with session token…")
                break
        if token is None:
            raise RuntimeError("serve did not print URL banner within timeout")

        # Establish session cookie + CSRF cookie via initial handshake.
        cookie_jar: list[str] = []
        url = f"http://127.0.0.1:{port}/?token={urllib.parse.quote(token)}"
        with urllib.request.urlopen(url, timeout=5) as resp:
            for header, value in resp.headers.items():
                if header.lower() == "set-cookie":
                    cookie_jar.append(value.split(";", 1)[0])
            if resp.status != 200:
                raise RuntimeError(f"GET / handshake returned {resp.status}")
        cookies_header = "; ".join(cookie_jar)

        for path in WEB_PROBE_PAGES:
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}{path}",
                headers={"Cookie": cookies_header},
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"GET {path} returned {resp.status}")
            log(f"  {path}: 200")

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


async def amain() -> int:
    api_key = os.environ.get("FIREFLIES_API_KEY", "").strip()
    if not api_key:
        log("FIREFLIES_API_KEY not set — refusing to run.")
        return 1

    log("=== FirefliesClearer scheduled smoke ===")
    log(f"target title: {TARGET_TITLE!r}")

    work_dir = Path(tempfile.mkdtemp(prefix="ffc-smoke-"))
    log(f"work dir: {work_dir}")

    try:
        client = FirefliesClient(api_key=api_key)

        log("listing meetings to find oldest target…")
        meeting = await find_oldest_target_meeting(client)
        if meeting is None:
            log(f"no meeting titled {TARGET_TITLE!r} found — nothing to test against.")
            return 0
        log(
            f"target: id={meeting.meeting_id!r} "
            f"date={meeting.meeting_date.isoformat()} "
            f"duration={meeting.duration_minutes:.1f}min"
        )

        await run_archive_purge(client, meeting, work_dir / "archive")
        await verify_meeting_gone(client, meeting.meeting_id)

        # Use a fresh archive root for the web probe so the manifest doesn't
        # collide with the deleted meeting record.
        probe_web_routes(api_key, work_dir / "web")

        log("=== smoke OK ===")
        return 0

    except Exception as exc:
        log(f"=== smoke FAILED: {exc!r} ===")
        import traceback

        traceback.print_exc()
        return 1
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def main() -> int:
    return asyncio.run(amain())


if __name__ == "__main__":
    sys.exit(main())
