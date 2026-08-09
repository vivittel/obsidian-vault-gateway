"""Access logging (IMPLEMENTATION_PLAN sections 14 and 17).

One thing must never reach the log, per section 14: the bearer token. The
access log below only ever writes method, route, status and duration — never
a note path or note content (REST is health-only now, docs/adr/0010-*.md, so
there is no route left that could set either).

uvicorn's own access log is disabled (see ``scripts`` invocation / Dockerfile
CMD, ``--no-access-log``) because it logs the raw query string; MCP's own
request log (app/mcp_server.py) is what section 14 actually relies on for
search-query-length and result-count reporting now.

``AccessLogMiddleware`` below is a plain ASGI callable, not a
``BaseHTTPMiddleware`` subclass, and passes a request straight through —
without touching ``receive``/``send`` at all — for anything that isn't an
HTTP request under ``/api/v1``. Two independent reasons forced this once
``/mcp`` existed alongside ``/api/v1`` (MCP_IMPLEMENTATION_PLAN section 15):

1. ``BaseHTTPMiddleware`` buffers the whole response before handing it back,
   which is incompatible with the Streamable HTTP transport's SSE responses.
2. Scoping by ``scope["path"]`` keeps this REST-only logging logic from ever
   touching MCP traffic, whose own access log (transport=mcp) is written by
   the MCP tool wrapper instead — see app/mcp_server.py.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from starlette.requests import Request

from app.config import API_PREFIX

if TYPE_CHECKING:
    from starlette.types import Receive, Scope, Send

access_logger = logging.getLogger("obsidian_gateway.access")

# Logged at DEBUG rather than INFO: Dockerfile's HEALTHCHECK hits this every 30
# seconds, which at INFO drowns out every real request — the log this
# middleware exists to produce was ~90% health checks before this. DEBUG keeps
# the line available under LOG_LEVEL=DEBUG without making it the default noise
# floor.
_HEALTH_ROUTE = f"{API_PREFIX}/health"


def _is_rest_http_request(scope: Scope) -> bool:
    return scope["type"] == "http" and scope["path"].startswith(API_PREFIX)


class AccessLogMiddleware:
    """One log line per REST request: transport, method, route, status, duration."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not _is_rest_http_request(scope):
            await self.app(scope, receive, send)
            return

        request = Request(scope)

        start = time.monotonic()
        status_code = 0

        async def send_wrapper(message: dict) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        unhandled_exception = False
        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            # Deliberately `Exception`, not `BaseException`: an exception
            # that is not a GatewayError/StarletteHTTPException/
            # RequestValidationError (i.e. anything app/main.py's
            # handle_unexpected_error converts) is never seen by this
            # middleware as a response: Starlette's own
            # Starlette.build_middleware_stack() pulls the `Exception`/`500`
            # handler key out to the *outermost* ServerErrorMiddleware,
            # outside every piece of user middleware including this one
            # (verified against the installed version:
            # starlette/applications.py's build_middleware_stack — "key in
            # (500, Exception)" is routed to `error_handler`, never into the
            # inner ExceptionMiddleware's own `handlers` dict). Without this
            # except/finally, that exception would propagate straight
            # through `await self.app(...)` above and leave the request
            # entirely unlogged — the one case (an unhandled 500) most worth
            # an access log line. Every registered GatewayError/
            # HTTPException/validation handler still runs inside the inner
            # ExceptionMiddleware, further down this same stack, and always
            # sends `http.response.start` before returning control here — so
            # `status_code` is only ever still 0 below for one of two
            # reasons: this branch (a real crash), or the connection closing
            # before any handler ever responded (no crash at all). Only the
            # first should render as a fabricated 500. `ServerErrorMiddleware`
            # itself only ever converts `Exception` (verified: `except
            # Exception as exc` in starlette/middleware/errors.py), never a
            # bare `BaseException` such as `asyncio.CancelledError` — a
            # cancelled request (client disconnect, server shutdown) is not
            # a crash and must not be recorded as a fabricated 500 either,
            # so this deliberately lets it propagate through the `finally`
            # below unmarked, with `status_code` staying whatever it already
            # was (0 if the response never started).
            unhandled_exception = True
            raise
        finally:
            duration_ms = round((time.monotonic() - start) * 1000, 1)
            route_path = self._route_path(scope, request)

            extra = {
                # IMPLEMENTATION_PLAN section 14 lists transport among the
                # things to record; only the MCP side was setting it before.
                "transport": "rest",
                "method": request.method,
                "route": route_path,
                "status_code": 500 if unhandled_exception and status_code == 0 else status_code,
                "duration_ms": duration_ms,
            }

            level = logging.DEBUG if route_path == _HEALTH_ROUTE else logging.INFO
            access_logger.log(level, "request", extra=extra)

    @staticmethod
    def _route_path(scope: Scope, request: Request) -> str:
        """The full public path of the matched route, e.g. ``/api/v1/health``.

        The matched route's own ``path`` is *not* prefixed: this FastAPI version
        applies ``include_router(prefix=...)`` at match time via an
        ``_IncludedRouter`` wrapper, leaving ``scope["route"].path`` as the
        router-relative ``/health`` (verified against the installed version).
        Logging that unprefixed form made the route ambiguous and impossible to
        line up against Caddy's access log, and it silently defeated the health
        check's DEBUG downgrade above, which compares against the full path.

        Still the route *template* rather than ``request.url.path``, so a path
        parameter added later cannot put caller-supplied data in the log; the
        prefix is only added when the route did not already carry it, so a
        future FastAPI that prefixes it itself cannot produce
        ``/api/v1/api/v1/search``.
        """
        route_path: str = getattr(scope.get("route"), "path", "")
        if not route_path:
            return request.url.path
        if route_path.startswith(API_PREFIX):
            return route_path
        return f"{API_PREFIX}{route_path}"
