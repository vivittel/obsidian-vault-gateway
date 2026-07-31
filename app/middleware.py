"""Request size limiting and access logging (IMPLEMENTATION_PLAN sections 14 and 17).

Two things must never reach the log, per section 14: the bearer token and note
content. The access log below only ever writes method, route, status and
duration — the search term's *length*, never its value; the note path a read
or a write touched, never its content.

uvicorn's own access log is disabled (see ``scripts`` invocation / Dockerfile
CMD, ``--no-access-log``) because it logs the raw query string, which would leak
the search term this middleware deliberately omits.
"""

from __future__ import annotations

import logging
import time

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from app.config import get_settings
from app.exceptions import FileTooLargeError, error_envelope

access_logger = logging.getLogger("obsidian_gateway.access")


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    """Reject an oversized body before it reaches request parsing.

    Trusts the client-supplied ``Content-Length``; a request that lies about its
    size and streams more than declared is not something Phase 1 defends
    against (there is no streaming endpoint to exploit that way), but Caddy's
    own request size limit is the second line of defence in production
    (docs/PHASE1_PLAN.md section 4.7 / IMPLEMENTATION_PLAN section 11).

    Returns the error response directly rather than raising ``FileTooLargeError``:
    exceptions raised from inside ``BaseHTTPMiddleware.dispatch`` never reach
    the ``@app.exception_handler(GatewayError)`` registered in app/main.py — in
    Starlette's middleware stack, user middleware sits *outside*
    ``ExceptionMiddleware`` (where per-exception-type handlers live), so such
    an exception would only ever be caught by the generic 500 handler,
    silently losing the intended 413 and error code.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        max_bytes = get_settings().max_request_bytes
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                declared_size = int(content_length)
            except ValueError:
                declared_size = None
            if declared_size is not None and declared_size > max_bytes:
                return JSONResponse(
                    status_code=FileTooLargeError.status_code,
                    content=error_envelope(
                        FileTooLargeError.code, FileTooLargeError.default_message
                    ),
                )
        return await call_next(request)


class AccessLogMiddleware(BaseHTTPMiddleware):
    """One log line per request: timestamp (via the logger), method, route, status, duration.

    Note or Inbox paths are logged by the routers themselves via
    ``request.state.accessed_note`` / ``request.state.created_note`` so this
    middleware stays generic and never inspects the body.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        start = time.monotonic()
        request.state.accessed_note = None
        request.state.created_note = None

        response = await call_next(request)

        duration_ms = round((time.monotonic() - start) * 1000, 1)
        route = request.scope.get("route")
        route_path = getattr(route, "path", request.url.path)

        extra = {
            "method": request.method,
            "route": route_path,
            "status_code": response.status_code,
            "duration_ms": duration_ms,
        }
        if request.state.accessed_note:
            extra["note_path"] = request.state.accessed_note
        if request.state.created_note:
            extra["note_path"] = request.state.created_note
        if request.method == "GET" and "q" in request.query_params:
            extra["query_length"] = len(request.query_params["q"])

        access_logger.info("request", extra=extra)
        return response
