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
    scope = _http_scope("/api/v1/health")
    sent_events = []

    async def send(message: dict) -> None:
        sent_events.append(message)

    await middleware(scope, _dummy_receive, send)

    access_records = [r for r in caplog.records if r.name == "obsidian_gateway.access"]
    assert len(access_records) == 1
    record = access_records[0]
    assert record.status_code == 200
    assert record.method == "GET"
    assert sent_events[0]["type"] == "http.response.start"


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
