"""app.middleware — pure-ASGI scoping (MCP_IMPLEMENTATION_PLAN section 15).

These exercise the two middlewares directly at the ASGI level, without a full
FastAPI app, so the passthrough guard (scope type / path prefix) and the
``scope["state"]`` sharing it depends on are proven independently of whatever
routes happen to exist. tests/test_logging.py covers the same middlewares
through the real REST app.
"""

from __future__ import annotations

import logging

import pytest

from app.middleware import AccessLogMiddleware, RequestSizeLimitMiddleware

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


@pytest.mark.parametrize("middleware_cls", [AccessLogMiddleware, RequestSizeLimitMiddleware])
async def test_lifespan_scope_passes_through_untouched(middleware_cls) -> None:
    inner = _RecordingApp()
    middleware = middleware_cls(inner)
    scope = {"type": "lifespan"}
    sent_events = []

    async def send(message: dict) -> None:
        sent_events.append(message)

    await middleware(scope, _dummy_receive, send)

    # Passthrough means the exact same scope/receive/send reach the inner
    # app — not a copy, not a wrapped send — because the guard returns before
    # touching any of them.
    assert inner.called_with == (scope, _dummy_receive, send)


@pytest.mark.parametrize("middleware_cls", [AccessLogMiddleware, RequestSizeLimitMiddleware])
async def test_non_api_v1_http_request_passes_through_untouched(middleware_cls) -> None:
    inner = _RecordingApp()
    middleware = middleware_cls(inner)
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
    scope = _http_scope("/api/v1/search")
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
    scope = _http_scope("/api/v1/vault/summary")

    async def send(message: dict) -> None:
        pass

    with pytest.raises(RuntimeError, match="boom"):
        await middleware(scope, _dummy_receive, send)

    access_records = [r for r in caplog.records if r.name == "obsidian_gateway.access"]
    assert len(access_records) == 1
    assert access_records[0].status_code == 500
    assert access_records[0].route == "/api/v1/vault/summary"


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
    scope = _http_scope("/api/v1/search")

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
    scope = _http_scope("/api/v1/search")

    async def send(message: dict) -> None:
        pass

    await middleware(scope, _dummy_receive, send)

    access_records = [r for r in caplog.records if r.name == "obsidian_gateway.access"]
    assert len(access_records) == 1
    assert access_records[0].status_code == 0


async def test_scope_state_set_downstream_is_visible_to_access_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The exact mechanism app/routers/notes.py and app/routers/inbox.py rely
    on: a downstream handler sets ``request.state.accessed_note`` (i.e.
    mutates ``scope["state"]``) and this middleware, which runs *around* that
    handler, reads the same dict back afterwards.
    """
    caplog.set_level(logging.INFO, logger="obsidian_gateway.access")

    class _NoteReadingApp:
        async def __call__(self, scope: dict, receive, send) -> None:  # noqa: ARG002
            # Mirrors what a router does via `request.state.accessed_note = ...`.
            scope["state"]["accessed_note"] = "Knowledge/PC/GPU/RTX 5070.md"
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b""})

    middleware = AccessLogMiddleware(_NoteReadingApp())
    scope = _http_scope("/api/v1/notes")

    async def send(message: dict) -> None:
        pass

    await middleware(scope, _dummy_receive, send)

    access_records = [r for r in caplog.records if r.name == "obsidian_gateway.access"]
    assert len(access_records) == 1
    assert access_records[0].note_path == "Knowledge/PC/GPU/RTX 5070.md"


async def test_request_size_limit_still_rejects_oversized_body_under_api_v1(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.config import get_settings

    monkeypatch.setenv("API_TOKEN", "x" * 16)
    monkeypatch.setenv("MCP_ALLOWED_HOSTS", "localhost")
    monkeypatch.setenv("MAX_REQUEST_BYTES", "1024")
    get_settings.cache_clear()
    try:
        inner = _RecordingApp()
        middleware = RequestSizeLimitMiddleware(inner)
        scope = _http_scope(
            "/api/v1/inbox/notes",
            method="POST",
            headers=[(b"content-length", b"999999")],
        )
        sent_events = []

        async def send(message: dict) -> None:
            sent_events.append(message)

        await middleware(scope, _dummy_receive, send)

        assert inner.called_with is None  # never reached the inner app
        assert sent_events[0]["status"] == 413
    finally:
        get_settings.cache_clear()


def _chunked_receive(chunks: list[bytes]):
    """A ``receive`` that streams ``chunks`` as successive
    ``http.request`` messages (no ``Content-Length``, mirroring a real
    ``Transfer-Encoding: chunked`` request), then a disconnect.
    """
    remaining = list(chunks)

    async def receive() -> dict:
        if remaining:
            body = remaining.pop(0)
            return {"type": "http.request", "body": body, "more_body": bool(remaining)}
        return {"type": "http.disconnect"}

    return receive


async def test_request_size_limit_rejects_a_chunked_body_with_no_content_length(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A body with no Content-Length header (chunked transfer) must still be
    capped — the declared-size fast path in the parent test only catches a
    client that reports its own size honestly.
    """
    from app.config import get_settings

    monkeypatch.setenv("API_TOKEN", "x" * 16)
    monkeypatch.setenv("MCP_ALLOWED_HOSTS", "localhost")
    monkeypatch.setenv("MAX_REQUEST_BYTES", "1024")
    get_settings.cache_clear()
    try:
        inner = _RecordingApp()
        middleware = RequestSizeLimitMiddleware(inner)
        scope = _http_scope("/api/v1/inbox/notes", method="POST")
        receive = _chunked_receive([b"x" * 600, b"x" * 600])  # 1200 > 1024
        sent_events = []

        async def send(message: dict) -> None:
            sent_events.append(message)

        await middleware(scope, receive, send)

        assert inner.called_with is None  # never reached the inner app
        assert sent_events[0]["status"] == 413
    finally:
        get_settings.cache_clear()


async def test_request_size_limit_replays_a_chunked_body_within_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A chunked body under the limit must reach the inner app byte-for-byte,
    reassembled from however many chunks it originally arrived in.
    """
    from app.config import get_settings

    monkeypatch.setenv("API_TOKEN", "x" * 16)
    monkeypatch.setenv("MCP_ALLOWED_HOSTS", "localhost")
    monkeypatch.setenv("MAX_REQUEST_BYTES", "1024")
    get_settings.cache_clear()
    try:
        inner = _RecordingApp()

        class _BodyReadingApp:
            def __init__(self) -> None:
                self.body: bytes = b""

            async def __call__(self, scope: dict, receive, send) -> None:  # noqa: ARG002
                chunks = []
                while True:
                    message = await receive()
                    chunks.append(message.get("body", b""))
                    if not message.get("more_body", False):
                        break
                self.body = b"".join(chunks)
                await send({"type": "http.response.start", "status": 200, "headers": []})
                await send({"type": "http.response.body", "body": b""})

        inner = _BodyReadingApp()
        middleware = RequestSizeLimitMiddleware(inner)
        scope = _http_scope("/api/v1/inbox/notes", method="POST")
        receive = _chunked_receive([b"abc", b"def", b"ghi"])
        sent_events = []

        async def send(message: dict) -> None:
            sent_events.append(message)

        await middleware(scope, receive, send)

        assert inner.body == b"abcdefghi"
        assert sent_events[0]["status"] == 200
    finally:
        get_settings.cache_clear()


async def test_request_size_limit_leaves_get_requests_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GET never carries a body on this API — the buffering/replay path must
    not engage for it, so ``receive`` reaches the inner app unwrapped.
    """
    from app.config import get_settings

    monkeypatch.setenv("API_TOKEN", "x" * 16)
    monkeypatch.setenv("MCP_ALLOWED_HOSTS", "localhost")
    monkeypatch.setenv("MAX_REQUEST_BYTES", "1024")
    get_settings.cache_clear()
    try:
        inner = _RecordingApp()
        middleware = RequestSizeLimitMiddleware(inner)
        scope = _http_scope("/api/v1/search", method="GET")

        async def send(message: dict) -> None:
            pass

        await middleware(scope, _dummy_receive, send)

        called_scope, called_receive, _called_send = inner.called_with
        assert called_scope is scope
        assert called_receive is _dummy_receive  # not wrapped
    finally:
        get_settings.cache_clear()
