"""Request size limiting and access logging (IMPLEMENTATION_PLAN sections 14 and 17).

Two things must never reach the log, per section 14: the bearer token and note
content. The access log below only ever writes method, route, status and
duration — the search term's *length*, never its value; the note path a read
or a write touched, never its content.

uvicorn's own access log is disabled (see ``scripts`` invocation / Dockerfile
CMD, ``--no-access-log``) because it logs the raw query string, which would leak
the search term this middleware deliberately omits.

Both middlewares below are plain ASGI callables, not ``BaseHTTPMiddleware``
subclasses, and both pass a request straight through — without touching
``receive``/``send`` at all — for anything that isn't an HTTP request under
``/api/v1``. Two independent reasons forced this once ``/mcp`` existed
alongside ``/api/v1`` (MCP_IMPLEMENTATION_PLAN section 15):

1. ``BaseHTTPMiddleware`` buffers the whole response before handing it back,
   which is incompatible with the Streamable HTTP transport's SSE responses.
2. Scoping by ``scope["path"]`` keeps this REST-only logging/limiting logic
   from ever touching MCP traffic, whose own access log (transport=mcp) is
   written by the MCP tool wrapper instead — see app/mcp_server.py.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.config import API_PREFIX, get_settings
from app.exceptions import FileTooLargeError, error_envelope

if TYPE_CHECKING:
    from starlette.types import Receive, Scope, Send

access_logger = logging.getLogger("obsidian_gateway.access")


def _is_rest_http_request(scope: Scope) -> bool:
    return scope["type"] == "http" and scope["path"].startswith(API_PREFIX)


class RequestSizeLimitMiddleware:
    """Reject an oversized body before it reaches request parsing.

    Trusts the client-supplied ``Content-Length``; a request that lies about its
    size and streams more than declared is not something Phase 1 defends
    against (there is no streaming endpoint to exploit that way), but Caddy's
    own request size limit is the second line of defence in production
    (docs/PHASE1_PLAN.md section 4.7 / IMPLEMENTATION_PLAN section 11).

    Returns the error response directly rather than raising ``FileTooLargeError``:
    an exception raised from ASGI middleware never reaches the
    ``@app.exception_handler(GatewayError)`` registered in app/main.py — those
    handlers live inside ``ExceptionMiddleware``, further down the stack — so
    it would only ever surface as the generic 500 handler, silently losing the
    intended 413 and error code.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not _is_rest_http_request(scope):
            await self.app(scope, receive, send)
            return

        max_bytes = get_settings().max_request_bytes
        content_length = Headers(scope=scope).get("content-length")
        if content_length is not None:
            try:
                declared_size = int(content_length)
            except ValueError:
                declared_size = None
            if declared_size is not None and declared_size > max_bytes:
                response = JSONResponse(
                    status_code=FileTooLargeError.status_code,
                    content=error_envelope(
                        FileTooLargeError.code, FileTooLargeError.default_message
                    ),
                )
                await response(scope, receive, send)
                return

        await self.app(scope, receive, send)


class AccessLogMiddleware:
    """One log line per REST request: method, route, status, duration.

    Note or Inbox paths are logged by the routers themselves via
    ``request.state.accessed_note`` / ``request.state.created_note`` (set on
    the shared ``scope["state"]`` dict) so this middleware stays generic and
    never inspects the body.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not _is_rest_http_request(scope):
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        request.state.accessed_note = None
        request.state.created_note = None

        start = time.monotonic()
        status_code = 0

        async def send_wrapper(message: dict) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        await self.app(scope, receive, send_wrapper)

        duration_ms = round((time.monotonic() - start) * 1000, 1)
        route = scope.get("route")
        route_path = getattr(route, "path", request.url.path)

        extra = {
            "method": request.method,
            "route": route_path,
            "status_code": status_code,
            "duration_ms": duration_ms,
        }
        if request.state.accessed_note:
            extra["note_path"] = request.state.accessed_note
        if request.state.created_note:
            extra["note_path"] = request.state.created_note
        if request.method == "GET" and "q" in request.query_params:
            extra["query_length"] = len(request.query_params["q"])

        access_logger.info("request", extra=extra)
