"""Windows ProactorEventLoop cosmetic-error suppression.

On Windows, ``asyncio.ProactorEventLoop`` logs a ``ConnectionResetError``
(WinError 10054) whenever an SSE socket is torn down abruptly by the client
— for example when ``window.location.reload()`` runs after a terminal
operation_state event in the dashboard's retry progress card. The kernel
sends RST, ``_ProactorBasePipeTransport._call_connection_lost`` notices,
and the default exception handler dumps a stack trace to stderr.

The connection is already gone, so the trace is harmless but very noisy
during normal use. See bpo-39010 / bpo-43253. We swallow this specific
case via a custom asyncio exception handler installed at app startup,
delegating anything else to the default handler.
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any

_LOST_MARKER = "_call_connection_lost"

# Windows ECONNRESET — the WinError code raised when the remote half abruptly
# tears the socket down, exactly what we want to suppress. Any other
# ConnectionResetError (different errno, different message) keeps flowing
# through the default handler so we don't accidentally hide unrelated
# transport problems that an operator should see.
_WSAECONNRESET = 10054


def install_proactor_connection_reset_handler() -> None:
    """Install the cosmetic-error filter on the running event loop.

    No-op on non-Windows platforms (other event loops do not log this).
    Must be called from inside a running asyncio loop — typically from a
    FastAPI ``startup`` event handler.
    """
    if sys.platform != "win32":
        return

    loop = asyncio.get_running_loop()
    previous = loop.get_exception_handler()

    def _handler(loop_obj: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
        exc = context.get("exception")
        message = context.get("message", "")
        if (
            isinstance(exc, ConnectionResetError)
            and _LOST_MARKER in message
            and getattr(exc, "winerror", None) == _WSAECONNRESET
        ):
            return
        if previous is not None:
            previous(loop_obj, context)
        else:
            loop_obj.default_exception_handler(context)

    loop.set_exception_handler(_handler)
