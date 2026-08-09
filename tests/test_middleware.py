"""app.middleware.AccessLogMiddleware — pure-ASGI scoping
(MCP_IMPLEMENTATION_PLAN section 15).

Exercises the middleware directly at the ASGI level, without a full FastAPI
app, so the passthrough guard (scope type / path prefix) is proven
independently of whatever routes happen to exist. tests/test_logging.py
covers the same middleware through the real REST app.

``RequestSizeLimitMiddleware`` was removed along with the REST routes that
carried a request body (docs/adr/0010-*.md) — no ``/api/v1`` route accepts
one any more, and ``/mcp``'s own body cap is enforced independently by the
MCP SDK's ``RequestBodyLimitMiddleware`` (app/mcp_server.py's
``build_mcp_transport``).
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from app.middleware import AccessLogMiddleware

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def _dummy_receive() -> dict:
    return {"type": "http.disconnect"}


class _RecordingApp:
    """A minimal downstream ASGI app that records exactly what it was called
    with and, for HTTP scopes, sends a trivial 200 response.
    """

    def __init__(self) -> None:
        self.called_with: tuple | None = None

    async def __call__(self, scope: dict, receive, send) -> None:
        self.called_with = (scope, receive, send)
        if scope["type"] == "http":
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b""})


def _http_scope(path: str, *, method: str = "GET", headers: list | None = None) -> dict:
    return {
        "type": "http",
        "method": method,
        "path": path,
        "query_string": b"",
        "headers": headers or [],
    }


async def test_lifespan_scope_passes_through_untouched() -> None:
    inner = _RecordingApp()
    middleware = AccessLogMiddleware(inner)
    scope = {"type": "lifespan"}
    sent_events = []

    async def send(message: dict) -> None:
        sent_events.append(message)

    await middleware(scope, _dummy_receive, send)

    # Passthrough means the exact same scope/receive/send reach the inner
    # app — not a copy, not a wrapped send — because the guard returns before
    # touching any of them.
    assert inner.called_with == (scope, _dummy_receive, send)


async def test_non_api_v1_http_request_passes_through_untouched() -> None:
    inner = _RecordingApp()
    middleware = AccessLogMiddleware(inner)
    scope = _http_scope("/openapi.json")
    sent_events = []

    async def send(message: dict) -> None:
        sent_events.append(message)

    await middleware(scope, _dummy_receive, send)

    called_scope, called_receive, called_send = inner.called_with
    assert called_scope is scope
    assert called_receive is _dummy_receive
    assert called_send is send  # not wrapped — same object identity


async def test_non_api_v1_request_never_logged(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="obsidian_gateway.access")
    inner = _RecordingApp()
    middleware = AccessLogMiddleware(inner)
    scope = _http_scope("/openapi.json")

    async def send(message: dict) -> None:
        pass

    await middleware(scope, _dummy_receive, send)

    access_records = [r for r in caplog.records if r.name == "obsidian_gateway.access"]
    assert access_records == []


async def test_api_v1_request_is_logged_with_status_and_route(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="obsidian_gateway.access")
    inner = _RecordingApp()
    middleware = AccessLogMiddleware(inner)
    # Deliberately not /api/v1/health: that one route logs at DEBUG (see
    # test_health_request_is_logged_at_debug_not_info below), so it would not
    # exercise the ordinary INFO path this test is about.
    scope = _http_scope("/api/v1/does-not-exist")
    sent_events = []

    async def send(message: dict) -> None:
        sent_events.append(message)

    await middleware(scope, _dummy_receive, send)

    access_records = [r for r in caplog.records if r.name == "obsidian_gateway.access"]
    assert len(access_records) == 1
    record = access_records[0]
    assert record.status_code == 200
    assert record.method == "GET"
    assert record.transport == "rest"
    assert sent_events[0]["type"] == "http.response.start"


async def test_health_request_is_logged_at_debug_not_info(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Dockerfile's HEALTHCHECK hits /api/v1/health every 30s. At INFO that one
    route accounted for almost the whole access log, so it is logged at DEBUG:
    still available when LOG_LEVEL=DEBUG, never the default noise floor.
    """
    inner = _RecordingApp()
    middleware = AccessLogMiddleware(inner)

    async def send(message: dict) -> None:
        pass

    caplog.set_level(logging.INFO, logger="obsidian_gateway.access")
    await middleware(_http_scope("/api/v1/health"), _dummy_receive, send)
    assert [r for r in caplog.records if r.name == "obsidian_gateway.access"] == []

    caplog.clear()
    caplog.set_level(logging.DEBUG, logger="obsidian_gateway.access")
    await middleware(_http_scope("/api/v1/health"), _dummy_receive, send)
    records = [r for r in caplog.records if r.name == "obsidian_gateway.access"]
    assert len(records) == 1
    assert records[0].levelno == logging.DEBUG
    assert records[0].route == "/api/v1/health"


async def test_unhandled_exception_is_still_logged_as_status_500(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A bug this pins: an exception that reaches this middleware un-responded
    to (bare Exception/500 handlers run on Starlette's outermost
    ServerErrorMiddleware, entirely outside this one — see the comment in
    app/middleware.py) used to propagate straight through ``await
    self.app(...)`` and leave the request with zero access-log lines. The
    500 case is the one most worth logging.
    """
    caplog.set_level(logging.INFO, logger="obsidian_gateway.access")

    class _CrashingApp:
        async def __call__(self, scope: dict, receive, send) -> None:  # noqa: ARG002
            raise RuntimeError("boom")

    middleware = AccessLogMiddleware(_CrashingApp())
    scope = _http_scope("/api/v1/does-not-exist")

    async def send(message: dict) -> None:
        pass

    with pytest.raises(RuntimeError, match="boom"):
        await middleware(scope, _dummy_receive, send)

    access_records = [r for r in caplog.records if r.name == "obsidian_gateway.access"]
    assert len(access_records) == 1
    assert access_records[0].status_code == 500
    assert access_records[0].route == "/api/v1/does-not-exist"


async def test_cancelled_request_is_not_misreported_as_a_fabricated_500(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A bug this pins: the crash-detection ``except`` clause was originally
    ``BaseException``, which also catches ``asyncio.CancelledError`` — a
    client disconnect or server shutdown, not a crash, and not something
    ``ServerErrorMiddleware`` itself ever converts to a 500 (it only catches
    ``Exception``). Recording it as status 500 would fabricate an error that
    never actually happened on the wire.
    """
    caplog.set_level(logging.INFO, logger="obsidian_gateway.access")

    class _CancelledApp:
        async def __call__(self, scope: dict, receive, send) -> None:  # noqa: ARG002
            raise asyncio.CancelledError

    middleware = AccessLogMiddleware(_CancelledApp())
    scope = _http_scope("/api/v1/does-not-exist")

    async def send(message: dict) -> None:
        pass

    with pytest.raises(asyncio.CancelledError):
        await middleware(scope, _dummy_receive, send)

    access_records = [r for r in caplog.records if r.name == "obsidian_gateway.access"]
    assert len(access_records) == 1
    assert access_records[0].status_code == 0


async def test_a_response_that_already_started_is_not_overwritten_by_a_later_crash(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """If a handler crashes *after* already sending http.response.start (an
    in-progress response, not the pre-response case above), the log must
    keep the real status it saw rather than fabricating a 500 over it.
    """
    caplog.set_level(logging.INFO, logger="obsidian_gateway.access")

    class _CrashesAfterRespondingApp:
        async def __call__(self, scope: dict, receive, send) -> None:  # noqa: ARG002
            await send({"type": "http.response.start", "status": 200, "headers": []})
            raise RuntimeError("boom after responding")

    middleware = AccessLogMiddleware(_CrashesAfterRespondingApp())
    scope = _http_scope("/api/v1/does-not-exist")

    async def send(message: dict) -> None:
        pass

    with pytest.raises(RuntimeError, match="boom after responding"):
        await middleware(scope, _dummy_receive, send)

    access_records = [r for r in caplog.records if r.name == "obsidian_gateway.access"]
    assert len(access_records) == 1
    assert access_records[0].status_code == 200


async def test_a_quiet_disconnect_with_no_response_and_no_exception_logs_status_zero(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Distinct from the crash case above: a handler that returns normally
    without ever responding (e.g. the connection dropped) is not a crash and
    must not be misreported as a 500.
    """
    caplog.set_level(logging.INFO, logger="obsidian_gateway.access")

    class _NeverRespondsApp:
        async def __call__(self, scope: dict, receive, send) -> None:  # noqa: ARG002
            return

    middleware = AccessLogMiddleware(_NeverRespondsApp())
    scope = _http_scope("/api/v1/does-not-exist")

    async def send(message: dict) -> None:
        pass

    await middleware(scope, _dummy_receive, send)

    access_records = [r for r in caplog.records if r.name == "obsidian_gateway.access"]
    assert len(access_records) == 1
    assert access_records[0].status_code == 0
