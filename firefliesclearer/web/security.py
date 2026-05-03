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

The CSRF middleware reads the request body via ``await request.body()`` and parses
it as ``application/x-www-form-urlencoded`` to extract the ``_csrf`` field.
``request.body()`` caches the raw bytes in ``request._body`` so subsequent
``request.form()`` calls (or FastAPI ``Form(...)`` extractors in route handlers)
re-parse the same cached bytes — solving the body-stream consumption problem that
plagues ``BaseHTTPMiddleware`` + form parsing in Starlette.

Browser callers MUST send CSRF-protected POSTs as ``application/x-www-form-urlencoded``.
``app.js`` enforces this for ``sendBeacon`` heartbeats by wrapping the body in a
``Blob`` with the explicit Content-Type. Multipart bodies are not parsed for CSRF
extraction in this middleware (intentional — the wizard and HTMX forms all
submit urlencoded).
"""

from __future__ import annotations

import secrets
import urllib.parse
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

        # Detect a cookie signed under a stale secret (e.g. server restarted
        # and rolled the secret while a browser tab kept its old cookie). On
        # safe methods, we transparently mint a fresh one below. On unsafe
        # methods, we still need to short-circuit with 403 — the request can't
        # be authenticated — but the next GET will rotate the cookie so the
        # subsequent retry succeeds.
        cookie = request.cookies.get(CSRF_COOKIE)
        cookie_is_valid = False
        if cookie:
            try:
                self._serializer.loads(cookie)
                cookie_is_valid = True
            except BadData:
                cookie_is_valid = False

        # On safe methods (GET/HEAD/OPTIONS) with a missing or stale cookie,
        # mint the new token BEFORE the route handler runs and inject it into
        # the ASGI scope's ``Cookie`` header so the route's own Request (each
        # ``BaseHTTPMiddleware`` layer constructs its own ``Request`` from the
        # shared scope) parses the freshly-minted token. Without this, the
        # first GET would render forms with an empty _csrf, breaking any POST
        # the user clicked on the same page (e.g. the sync opt-in banner).
        new_token: str | None = None
        if request.method in self.SAFE_METHODS and (not cookie or not cookie_is_valid):
            new_token = self._serializer.dumps(secrets.token_urlsafe(16))
            self._inject_cookie_into_scope(request, new_token)

        if request.method not in self.SAFE_METHODS:
            if not cookie:
                return Response(status_code=403, content="CSRF cookie missing")
            if not cookie_is_valid:
                # Stale signature (typically: server restart). Rotate the
                # cookie on this 403 so the next retry-after-GET works.
                return self._rotate_csrf_cookie(
                    Response(
                        status_code=403,
                        content=(
                            "CSRF cookie expired (server may have restarted). "
                            "Refresh the page and try again."
                        ),
                    )
                )
            # Use request.body() rather than request.form() so the body bytes
            # are cached in request._body.  Downstream route handlers can then
            # re-parse via request.form() or Form(...) parameters; calling
            # request.form() here would consume the stream, leaving the body
            # empty for any handler that also reads form fields.
            body = await request.body()
            parsed = urllib.parse.parse_qs(body.decode(errors="replace"), keep_blank_values=True)
            csrf_values = parsed.get(CSRF_FIELD)
            csrf_field_value: str | None = csrf_values[0] if csrf_values else None
            if csrf_field_value != cookie:
                return Response(status_code=403, content="CSRF mismatch")

        response = await call_next(request)
        # Mint a fresh cookie when none exists OR when the existing one is
        # stale-signed. The latter case lets a stale browser tab self-heal on
        # any safe-method navigation, instead of requiring the user to clear
        # cookies manually.
        if new_token is not None:
            self._set_csrf_cookie(response, new_token)
        return response

    def _rotate_csrf_cookie(self, response: Response) -> Response:
        """Mint a fresh token and write it on *response*.

        Used by the unsafe-method path (after a stale-signature 403) where
        we don't need to expose the new value to a template on this request —
        the user will refresh and the new GET will go through the safe-method
        path. For the safe-method path, see :meth:`_set_csrf_cookie`, which
        receives the already-minted token.
        """
        new_token = self._serializer.dumps(secrets.token_urlsafe(16))
        return self._set_csrf_cookie(response, new_token)

    def _set_csrf_cookie(self, response: Response, token: str) -> Response:
        response.set_cookie(
            CSRF_COOKIE,
            token,
            httponly=False,  # JS must read this for fetch()-based POSTs
            samesite="strict",
            max_age=24 * 60 * 60,
        )
        return response

    @staticmethod
    def _inject_cookie_into_scope(request: Request, token: str) -> None:
        """Replace the ``Cookie`` header in ``request.scope`` with one that
        carries ``ffc_csrf=<token>``.

        Each ``BaseHTTPMiddleware`` layer constructs its own ``Request`` from
        the shared ASGI scope; mutating the local ``Request.cookies`` only
        affects this layer. To propagate the freshly-minted token to the
        downstream route handler's templates, the scope's raw header bytes
        must change so the next ``Request`` parses the new value.
        """
        existing = dict(request.cookies)
        existing[CSRF_COOKIE] = token
        new_cookie_header = "; ".join(f"{k}={v}" for k, v in existing.items())
        headers = [(k, v) for k, v in request.scope.get("headers", []) if k.lower() != b"cookie"]
        headers.append((b"cookie", new_cookie_header.encode("latin-1")))
        request.scope["headers"] = headers


def install_security(app: FastAPI, config: SecurityConfig) -> None:
    """Attach SessionTokenMiddleware and CSRFMiddleware to the app.

    Middleware ordering matters: in Starlette, middlewares added last wrap
    inner ones, so SessionTokenMiddleware is added LAST so it runs FIRST,
    short-circuiting the 401 before CSRF logic is reached.
    """
    app.add_middleware(CSRFMiddleware, secret=config.csrf_secret)
    app.add_middleware(SessionTokenMiddleware, token=config.session_token)
