"""Session token + CSRF protection for the local web UI.

Two middlewares are installed via install_security():

1. SessionTokenMiddleware — every non-static request requires either a
   ``?token=<TOKEN>`` query param (initial handshake) or the ``ffc_session``
   cookie.  On a successful handshake the cookie is set so the browser can
   omit the query param on subsequent requests.

2. CSRFMiddleware — every non-safe-method (non-GET/HEAD/OPTIONS) request must
   carry a signed ``ffc_csrf`` cookie AND include the same value as ``_csrf``
   in the form body (cookie double-submit pattern).  The cookie is set on the
   first GET response so the browser has it ready for later POSTs.

Known issue: BaseHTTPMiddleware reads (and buffers) the request body when
``await request.form()`` is called.  In modern Starlette (≥0.20) the body is
properly buffered, so downstream route handlers can read form data again.
However, older Starlette versions may leave the body stream consumed, causing
route handlers that also call ``await request.form()`` to hang or see empty
data.  Routes that need form access should be tested after any Starlette
upgrade.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass

from fastapi import FastAPI, Request, Response
from itsdangerous import BadData, URLSafeSerializer
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.types import ASGIApp

SESSION_COOKIE = "ffc_session"
CSRF_COOKIE = "ffc_csrf"
CSRF_FIELD = "_csrf"


@dataclass(frozen=True)
class SecurityConfig:
    session_token: str
    csrf_secret: str


class SessionTokenMiddleware(BaseHTTPMiddleware):
    """Gate every non-static request behind a session token."""

    def __init__(self, app: ASGIApp, *, token: str) -> None:
        super().__init__(app)
        self._token = token

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.url.path.startswith("/static/"):
            return await call_next(request)

        cookie = request.cookies.get(SESSION_COOKIE)
        query = request.query_params.get("token")
        valid = cookie == self._token or query == self._token
        if not valid:
            return Response(status_code=401, content="Session token required")

        response = await call_next(request)
        if cookie != self._token:
            response.set_cookie(
                SESSION_COOKIE,
                self._token,
                httponly=True,
                samesite="strict",
                max_age=24 * 60 * 60,
            )
        return response


class CSRFMiddleware(BaseHTTPMiddleware):
    """Cookie double-submit CSRF protection for mutating requests."""

    SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

    def __init__(self, app: ASGIApp, *, secret: str) -> None:
        super().__init__(app)
        self._serializer = URLSafeSerializer(secret, salt="ffc-csrf")

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.url.path.startswith("/static/"):
            return await call_next(request)

        if request.method not in self.SAFE_METHODS:
            cookie = request.cookies.get(CSRF_COOKIE)
            if not cookie:
                return Response(status_code=403, content="CSRF cookie missing")
            try:
                self._serializer.loads(cookie)
            except BadData:
                return Response(status_code=403, content="CSRF cookie invalid")
            form = await request.form()
            if form.get(CSRF_FIELD) != cookie:
                return Response(status_code=403, content="CSRF mismatch")

        response = await call_next(request)
        if not request.cookies.get(CSRF_COOKIE):
            new_token = self._serializer.dumps(secrets.token_urlsafe(16))
            response.set_cookie(
                CSRF_COOKIE,
                new_token,
                httponly=False,  # JS must read this for fetch()-based POSTs
                samesite="strict",
                max_age=24 * 60 * 60,
            )
        return response


def install_security(app: FastAPI, config: SecurityConfig) -> None:
    """Attach SessionTokenMiddleware and CSRFMiddleware to the app.

    Middleware ordering matters: in Starlette, middlewares added last wrap
    inner ones, so SessionTokenMiddleware is added LAST so it runs FIRST,
    short-circuiting the 401 before CSRF logic is reached.
    """
    app.add_middleware(CSRFMiddleware, secret=config.csrf_secret)
    app.add_middleware(SessionTokenMiddleware, token=config.session_token)
