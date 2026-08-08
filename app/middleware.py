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
from collections import deque
from typing import TYPE_CHECKING

from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.config import API_PREFIX, get_settings
from app.exceptions import FileTooLargeError, error_envelope

if TYPE_CHECKING:
    from starlette.types import Message, Receive, Scope, Send

access_logger = logging.getLogger("obsidian_gateway.access")

# Logged at DEBUG rather than INFO: Dockerfile's HEALTHCHECK hits this every 30
# seconds, which at INFO drowns out every real request — the log this
# middleware exists to produce was ~90% health checks before this. DEBUG keeps
# the line available under LOG_LEVEL=DEBUG without making it the default noise
# floor.
_HEALTH_ROUTE = f"{API_PREFIX}/health"

# Every REST write goes through POST; PUT/PATCH are included for whatever a
# future endpoint adds. GET/HEAD/DELETE never carry a body on this API, so
# skipping the buffering-and-replay path below for them is not just an
# optimisation — it keeps every read-only route's `receive` untouched, exactly
# as before this class started reading bodies at all.
_BODY_BEARING_METHODS = frozenset({"POST", "PUT", "PATCH"})


def _is_rest_http_request(scope: Scope) -> bool:
    return scope["type"] == "http" and scope["path"].startswith(API_PREFIX)


class RequestSizeLimitMiddleware:
    """Reject an oversized body before it reaches request parsing.

    Two checks, in order:

    1. The client-supplied ``Content-Length``, when present and already over
       the limit — rejected without reading a single body chunk.
    2. The body's actual cumulative size as it streams in, buffered here (see
       :meth:`_read_and_replay`) and checked message by message. (1) alone
       trusts a header a client can simply omit — a chunked-transfer body
       (no ``Content-Length`` at all) sailed through unchecked until this
       check existed. Caddy's own request size limit is still the second
       line of defence in production (docs/PHASE1_PLAN.md section 4.7 /
       IMPLEMENTATION_PLAN section 11), but the application must not depend
       on it alone.

    Mirrors the MCP SDK's own ``RequestBodyLimitMiddleware``
    (``mcp.server.streamable_http_manager``), which the ``/mcp`` transport
    already relies on for exactly this — the two transports now enforce
    their request-body cap the same way.

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
                await self._reject(scope, receive, send)
                return

        if scope["method"] not in _BODY_BEARING_METHODS:
            await self.app(scope, receive, send)
            return

        replay_receive = await self._read_and_replay(receive, max_bytes)
        if replay_receive is None:
            await self._reject(scope, receive, send)
            return

        await self.app(scope, replay_receive, send)

    @staticmethod
    async def _reject(scope: Scope, receive: Receive, send: Send) -> None:
        response = JSONResponse(
            status_code=FileTooLargeError.status_code,
            content=error_envelope(FileTooLargeError.code, FileTooLargeError.default_message),
        )
        await response(scope, receive, send)

    @staticmethod
    async def _read_and_replay(receive: Receive, max_bytes: int) -> Receive | None:
        """Read the whole body, bounded by ``max_bytes``, and hand back a
        ``receive`` that replays it unchanged — or ``None`` if it exceeded
        the bound partway through.

        The body is fully buffered before the downstream app ever sees it:
        the point is to know the *complete* size is within budget before
        request parsing starts, not to stream-validate chunk by chunk. The
        buffer itself can never exceed ``max_bytes`` (checked after every
        chunk, before extending it), so this adds no unbounded memory use.
        """
        received = bytearray()
        received_request = False
        body_complete = False
        trailing_message: Message | None = None

        while True:
            message = await receive()
            if message["type"] != "http.request":
                trailing_message = message
                break

            received_request = True
            body = message.get("body", b"")
            if len(received) + len(body) > max_bytes:
                return None
            received.extend(body)
            if not message.get("more_body", False):
                body_complete = True
                break

        cached: deque[Message] = deque()
        if received_request:
            cached.append(
                {"type": "http.request", "body": bytes(received), "more_body": not body_complete}
            )
        if trailing_message is not None:
            cached.append(trailing_message)

        async def replay() -> Message:
            if cached:
                return cached.popleft()
            return await receive()

        return replay


class AccessLogMiddleware:
    """One log line per REST request: transport, method, route, status, duration.

    Note or Inbox paths and result counts are logged by the routers themselves
    via ``request.state.accessed_note`` / ``request.state.created_note`` /
    ``request.state.appended_note`` / ``request.state.result_count`` (set on the
    shared ``scope["state"]`` dict) so this middleware stays generic and never
    inspects the body.
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
        request.state.appended_note = None
        request.state.result_count = None

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
        except BaseException:
            # An exception that is not a GatewayError/StarletteHTTPException/
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
            # first should render as a fabricated 500.
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
            if request.state.accessed_note:
                extra["note_path"] = request.state.accessed_note
            if request.state.created_note:
                extra["note_path"] = request.state.created_note
            if request.state.appended_note:
                extra["note_path"] = request.state.appended_note
            if request.state.result_count is not None:
                extra["result_count"] = request.state.result_count
            if request.method == "GET" and "q" in request.query_params:
                extra["query_length"] = len(request.query_params["q"])

            level = logging.DEBUG if route_path == _HEALTH_ROUTE else logging.INFO
            access_logger.log(level, "request", extra=extra)

    @staticmethod
    def _route_path(scope: Scope, request: Request) -> str:
        """The full public path of the matched route, e.g. ``/api/v1/search``.

        The matched route's own ``path`` is *not* prefixed: this FastAPI version
        applies ``include_router(prefix=...)`` at match time via an
        ``_IncludedRouter`` wrapper, leaving ``scope["route"].path`` as the
        router-relative ``/search`` (verified against the installed version).
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
