"""One-shot Fireflies API probe.

Measures how long a single ``list_meetings_page`` call takes against the
real API. No retries, generous timeout, so we can tell whether requests
are *slow* (eventually succeed) or *genuinely failing* (504/timeout).

Usage (from repo root, venv activated)::

    .venv\\Scripts\\python.exe scripts\\probe_fireflies.py

Reads the API key from the same config the app uses
(``%APPDATA%\\firefliesclearer\\config.toml`` on Windows). Override with
``--config <path>`` or ``FIREFLIES_API_KEY`` env var.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import time
from pathlib import Path

from firefliesclearer.infra.config import load_config, user_config_path
from firefliesclearer.infra.fireflies_client import (
    FirefliesClient,
    FirefliesError,
    RateLimitedError,
    TransientServerError,
)


async def _probe(api_key: str, *, timeout_seconds: float, page_size: int) -> None:
    client = FirefliesClient(
        api_key=api_key,
        retry_max=0,  # no retries — we want raw, single-attempt timing
        retry_base_seconds=0.0,
        page_size=page_size,
        timeout_seconds=timeout_seconds,
    )
    print(
        f"Probing Fireflies list_meetings_page(skip=0, limit={page_size}) "
        f"with timeout={timeout_seconds}s, retries=0..."
    )
    started = time.monotonic()
    try:
        count = 0
        async for _ in client.list_meetings_page(skip=0, limit=page_size):
            count += 1
        elapsed = time.monotonic() - started
        print(f"OK in {elapsed:.2f}s — {count} meeting(s) returned")
    except RateLimitedError as e:
        elapsed = time.monotonic() - started
        ra = getattr(e, "retry_after_seconds", None)
        print(f"RATE LIMITED after {elapsed:.2f}s (retry_after={ra}s): {e}")
    except TransientServerError as e:
        elapsed = time.monotonic() - started
        print(f"5xx after {elapsed:.2f}s: {e}")
    except FirefliesError as e:
        elapsed = time.monotonic() - started
        print(f"FirefliesError after {elapsed:.2f}s: {e}")
    except Exception as e:
        elapsed = time.monotonic() - started
        print(f"Unexpected error after {elapsed:.2f}s: {type(e).__name__}: {e}")


def _load_api_key(config_override: Path | None) -> str:
    env = os.environ.get("FIREFLIES_API_KEY")
    if env:
        return env
    cfg_path = config_override or user_config_path()
    if not cfg_path.exists():
        raise SystemExit(
            f"No API key. Set FIREFLIES_API_KEY env var or pass --config "
            f"pointing at an existing config (looked at {cfg_path})."
        )
    cfg = load_config(user_config=cfg_path)
    return cfg.fireflies.api_key


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument(
        "--timeout", type=float, default=600.0, help="HTTP timeout in seconds (default 600)"
    )
    parser.add_argument(
        "--page-size", type=int, default=50, help="Pagination page size (default 50)"
    )
    args = parser.parse_args()

    api_key = _load_api_key(args.config)
    asyncio.run(_probe(api_key, timeout_seconds=args.timeout, page_size=args.page_size))


if __name__ == "__main__":
    main()
