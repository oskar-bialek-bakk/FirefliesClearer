"""Tests for the Windows ProactorEventLoop cosmetic-error filter."""

from __future__ import annotations

import asyncio
import sys
from typing import Any

import pytest

from firefliesclearer.web.proactor_fix import install_proactor_connection_reset_handler


@pytest.mark.skipif(sys.platform != "win32", reason="Proactor handler is Windows-only")
async def test_handler_swallows_proactor_connection_reset() -> None:
    install_proactor_connection_reset_handler()
    loop = asyncio.get_running_loop()
    handler = loop.get_exception_handler()
    assert handler is not None

    captured: list[dict[str, Any]] = []
    # The handler delegates to the previous handler (or default) for things
    # it doesn't suppress. Replace default by wrapping it via a sentinel-based
    # check — we just call our handler directly with synthetic contexts.
    cosmetic_ctx: dict[str, Any] = {
        "message": "Exception in callback _ProactorBasePipeTransport._call_connection_lost(None)",
        "exception": ConnectionResetError(10054, "abrupt close"),
    }
    real_ctx: dict[str, Any] = {
        "message": "Some other failure",
        "exception": RuntimeError("boom"),
    }

    # Wrap default to observe what reaches it.
    original_default = loop.default_exception_handler

    def _spy(context: dict[str, Any]) -> None:
        captured.append(context)

    loop.default_exception_handler = _spy  # type: ignore[method-assign]
    try:
        handler(loop, cosmetic_ctx)
        handler(loop, real_ctx)
    finally:
        loop.default_exception_handler = original_default  # type: ignore[method-assign]

    assert len(captured) == 1
    assert captured[0]["exception"] is real_ctx["exception"]


async def test_handler_noop_off_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    loop = asyncio.get_running_loop()
    before = loop.get_exception_handler()
    install_proactor_connection_reset_handler()
    after = loop.get_exception_handler()
    assert after is before
