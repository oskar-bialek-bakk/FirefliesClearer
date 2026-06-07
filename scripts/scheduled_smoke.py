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
import queue
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
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


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Disable automatic redirect following so probes assert the bare-path
    response. FastAPI middlewares (redirect-to-setup, the empty-selection
    bouncer, etc.) all use 30x; if we silently follow them the smoke would
    report 200 for a path that's actually broken or in the wrong state.
    """

    def http_error_301(self, *args: object, **kwargs: object) -> None:
        return None

    http_error_302 = http_error_303 = http_error_307 = http_error_308 = http_error_301


_NO_REDIRECT_OPENER = urllib.request.build_opener(_NoRedirectHandler())


def log(msg: str) -> None:
    """Timestamped stdout line so the CI log is grepable."""
    ts = datetime.now(tz=UTC).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


async def find_oldest_target_meeting(
    client: FirefliesClient,
    user_email: str,
) -> Meeting | None:
    """Walk the live API, return the oldest host-owned meeting matching TARGET_TITLE.

    The Fireflies list is paginated newest-first; we exhaust pages and pick
    the minimum by ``meeting_date``. For a daily-recurring meeting this is
    bounded (~365 hits/year).

    Filters out rows whose ``host_email`` differs from the authenticated user:
    Fireflies' ``deleteTranscript`` mutation rejects non-host calls (and
    surfaces the rejection as ``Transcript not found``, which would otherwise
    look like a real bug). ``Pipeline._archive`` skips these in production for
    the same reason; the smoke mirrors that policy. Rows with an empty
    ``host_email`` (resolver dropout) are kept — better to attempt and let
    the API decide than to silently drop everything when host metadata is
    unavailable.
    """
    user_lc = user_email.lower()
    candidates: list[Meeting] = []
    async for m in client.list_meetings(MeetingFilter()):
        if m.title.strip() != TARGET_TITLE:
            continue
        host_lc = m.host_email.lower()
        if host_lc and host_lc != user_lc:
            continue
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
    # Pump child stdout in a thread so the boot timeout is actually enforced
    # (proc.stdout.readline() blocks; a blocking read can't honour wall-clock
    # deadlines).
    line_queue: queue.Queue[str] = queue.Queue()

    def _pump() -> None:
        assert proc.stdout is not None
        for line in iter(proc.stdout.readline, ""):
            line_queue.put(line)
        line_queue.put("")  # sentinel: stdout closed

    pump_thread = threading.Thread(target=_pump, daemon=True)
    pump_thread.start()

    try:
        # Stage 1: wait for the URL banner (bounded by SERVE_BOOT_TIMEOUT).
        token: str | None = None
        deadline = time.monotonic() + SERVE_BOOT_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            try:
                line = line_queue.get(timeout=0.5)
            except queue.Empty:
                if proc.poll() is not None:
                    raise RuntimeError("serve exited before printing banner") from None
                continue
            if line == "":
                raise RuntimeError("serve closed stdout before printing banner")
            if "running at" in line and "?token=" in line:
                token = line.split("?token=", 1)[1].strip()
                log("serve printed banner; waiting for socket bind…")
                break
        if token is None:
            raise RuntimeError("serve did not print URL banner within timeout")

        # Stage 2: the banner is printed BEFORE uvicorn.Server.run() binds the
        # socket. Poll connect-to-port until success or the same deadline.
        url = f"http://127.0.0.1:{port}/?token={urllib.parse.quote(token)}"
        cookies_header = ""
        last_err: Exception | None = None
        while time.monotonic() < deadline:
            try:
                with _NO_REDIRECT_OPENER.open(url, timeout=2) as resp:
                    if resp.status != 200:
                        raise RuntimeError(
                            f"GET / handshake returned {resp.status} (expected 200, "
                            "no redirect should fire)"
                        )
                    cookie_jar: list[str] = []
                    for header, value in resp.headers.items():
                        if header.lower() == "set-cookie":
                            cookie_jar.append(value.split(";", 1)[0])
                    cookies_header = "; ".join(cookie_jar)
                    log("server bound; probing pages…")
                    break
            except (OSError, urllib.error.URLError) as exc:
                last_err = exc
                time.sleep(0.25)
        else:
            raise RuntimeError(
                f"serve never accepted connections on :{port} within timeout "
                f"(last err: {last_err!r})"
            )

        # Stage 3: probe each page with the session cookie. Redirects are
        # disabled — the smoke should report exactly the path's true status,
        # not the result of following a /setup-bounce or post-redirect target.
        for path in WEB_PROBE_PAGES:
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}{path}",
                headers={"Cookie": cookies_header},
            )
            try:
                with _NO_REDIRECT_OPENER.open(req, timeout=5) as resp:
                    if resp.status != 200:
                        raise RuntimeError(
                            f"GET {path} returned {resp.status} (expected 200, no redirect)"
                        )
            except urllib.error.HTTPError as exc:
                # _NoRedirectHandler returns None on 30x, which urllib turns
                # into HTTPError; surface that as a hard fail with the code.
                raise RuntimeError(f"GET {path} returned {exc.code}") from exc
            log(f"  {path}: 200")

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        pump_thread.join(timeout=2)


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

        log("resolving authenticated user…")
        user_email = await client.ping_user()
        log(f"authenticated as {user_email}")

        log("listing meetings to find oldest target…")
        meeting = await find_oldest_target_meeting(client, user_email)
        if meeting is None:
            log(f"no host-owned meeting titled {TARGET_TITLE!r} found — nothing to test against.")
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
