"""What a log line actually looks like once rendered.

tests/test_logging.py already asserts on ``LogRecord`` *attributes* via
``caplog``, and every one of those tests passed while the real output was::

    request
    Terminating session: None
    mcp_call

— because nothing configured a formatter that read those attributes, so they
were all discarded on the way out. Record-level coverage cannot catch that.
Everything here therefore asserts on the rendered string: the columns, the
fields that must be present, and above all the fields that must never appear.
"""

from __future__ import annotations

import io
import logging
from collections.abc import Callable, Iterator
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from app.logging_config import PlainLogFormatter, configure_logging

_APP_LOGGERS = ("obsidian_gateway", "obsidian_gateway.access", "obsidian_gateway.mcp")
_HANDLER_NAME = "obsidian_gateway_stdout"

Rendered = Callable[[], list[str]]


@pytest.fixture(autouse=True)
def _restore_logging() -> Iterator[None]:
    """Undo every global logging mutation these tests make.

    Both :func:`configure_logging` and the ``rendered`` fixture touch the root
    logger, and pytest's own ``caplog`` handler lives there too.
    """
    root = logging.getLogger()
    handlers = list(root.handlers)
    levels = {name: logging.getLogger(name).level for name in _APP_LOGGERS}
    root_level = root.level
    yield
    root.handlers = handlers
    root.setLevel(root_level)
    for name, level in levels.items():
        logging.getLogger(name).setLevel(level)


@pytest.fixture
def rendered() -> Iterator[Rendered]:
    """Capture what the real formatter writes for anything logged in the test.

    Attaches its own handler rather than reading the process-wide one: that one
    was built at import time around the real ``sys.stdout``, which pytest has
    since replaced.
    """
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(PlainLogFormatter(ZoneInfo("Asia/Tokyo")))

    root = logging.getLogger()
    root.addHandler(handler)
    for name in _APP_LOGGERS:
        logging.getLogger(name).setLevel(logging.DEBUG)

    yield lambda: [line for line in stream.getvalue().splitlines() if line.strip()]

    root.removeHandler(handler)


def _line_with(lines: list[str], needle: str) -> str:
    matches = [line for line in lines if needle in line]
    assert matches, f"no rendered line containing {needle!r} in {lines!r}"
    return matches[-1]


# --- the column layout -------------------------------------------------------


def test_every_column_is_exactly_one_whitespace_separated_field(rendered: Rendered) -> None:
    """The property that makes aligned plain text worth choosing over JSON.

    If the timestamp used a space separator instead of ``T`` this would fail:
    every column after it would shift by one and ``awk '$3=="mcp"'`` would
    silently select the level instead of the transport.
    """
    logging.getLogger("obsidian_gateway.mcp").info(
        "mcp_call",
        extra={
            "transport": "mcp",
            "method": "tools/call",
            "tool": "search_notes",
            "status": "success",
            "duration_ms": 31.7,
            "result_count": 5,
        },
    )

    fields = _line_with(rendered(), "search_notes").split()
    assert fields[1] == "INFO"
    assert fields[2] == "mcp"
    assert fields[3] == "tools/call"
    assert fields[4] == "search_notes"
    assert fields[5] == "success"
    assert fields[6] == "31.7ms"
    assert fields[7] == "results=5"


def test_timestamp_is_iso8601_with_offset(rendered: Rendered) -> None:
    from datetime import datetime

    logging.getLogger("obsidian_gateway").info("anything")

    timestamp = rendered()[-1].split()[0]
    parsed = datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%S.%f%z")
    assert parsed.utcoffset() is not None


def test_source_column_separates_transports_from_everything_else(
    rendered: Rendered,
) -> None:
    logging.getLogger("obsidian_gateway.access").info(
        "request", extra={"transport": "rest", "route": "/api/v1/notes"}
    )
    logging.getLogger("obsidian_gateway").info("gateway_error")
    logging.getLogger("uvicorn.error").setLevel(logging.INFO)
    logging.getLogger("uvicorn.error").info("Started server process [1]")
    logging.getLogger("mcp.server.streamable_http_manager").setLevel(logging.INFO)
    logging.getLogger("mcp.server.streamable_http_manager").info("session manager started")

    lines = rendered()
    assert _line_with(lines, "/api/v1/notes").split()[2] == "rest"
    assert _line_with(lines, "gateway_error").split()[2] == "app"
    assert _line_with(lines, "Started server process").split()[2] == "uvicorn"
    assert _line_with(lines, "session manager started").split()[2] == "mcp-sdk"


def test_unstructured_record_keeps_its_message_as_free_text(rendered: Rendered) -> None:
    logging.getLogger("uvicorn.error").setLevel(logging.INFO)
    logging.getLogger("uvicorn.error").info("Uvicorn running on http://0.0.0.0:8000")

    line = _line_with(rendered(), "Uvicorn running")
    assert line.endswith("Uvicorn running on http://0.0.0.0:8000")


def test_auth_failure_falls_back_to_the_event_name_as_target(rendered: Rendered) -> None:
    """``mcp_auth_failed`` is rejected before any tool is named, so it has
    neither a route nor a tool to show — the event name is more useful in that
    column than a bare dash.
    """
    logging.getLogger("obsidian_gateway.mcp").info(
        "mcp_auth_failed",
        extra={"transport": "mcp", "status": "unauthorized", "reason": "bearer_token_mismatch"},
    )

    fields = _line_with(rendered(), "mcp_auth_failed").split()
    assert fields[2] == "mcp"
    assert fields[4] == "mcp_auth_failed"
    assert fields[5] == "unauthorized"
    assert "reason=bearer_token_mismatch" in " ".join(fields)


# --- one record is one line --------------------------------------------------


def test_newline_in_a_value_does_not_split_the_line(rendered: Rendered) -> None:
    """A note whose filename contains a newline is legal on Linux, and
    app/services/path_security.py rejects null bytes and backslashes but not
    newlines. Without escaping, one event would render as two lines and read as
    two unrelated events.
    """
    logging.getLogger("obsidian_gateway.access").info(
        "request",
        extra={
            "transport": "rest",
            "method": "GET",
            "route": "/api/v1/notes",
            "status_code": 200,
            "duration_ms": 1.0,
            "note_path": "Knowledge/two\nlines.md",
        },
    )

    lines = rendered()
    assert len(lines) == 1
    assert "note=Knowledge/two\\nlines.md" in lines[0]


def test_traceback_is_kept_on_one_line(rendered: Rendered) -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        logging.getLogger("obsidian_gateway").exception("unhandled_error")

    lines = rendered()
    assert len(lines) == 1
    assert "exc=" in lines[0]
    assert "ValueError" in lines[0]


def test_note_path_with_spaces_stays_at_the_end_of_the_line(rendered: Rendered) -> None:
    """The one caller-derived value that can contain spaces is ordered last in
    the tail, so it cannot shift any fixed column's field position.
    """
    logging.getLogger("obsidian_gateway.access").info(
        "request",
        extra={
            "transport": "rest",
            "method": "GET",
            "route": "/api/v1/notes",
            "status_code": 200,
            "duration_ms": 12.4,
            "note_path": "Knowledge/PC/GPU/RTX 5070.md",
            "result_count": 1,
        },
    )

    line = rendered()[-1]
    assert line.endswith("note=Knowledge/PC/GPU/RTX 5070.md")
    fields = line.split()
    assert fields[2] == "rest"
    assert fields[5] == "200"
    assert fields[6] == "12.4ms"


# --- the allow-list ----------------------------------------------------------


def test_extra_field_outside_the_allow_list_is_never_rendered(rendered: Rendered) -> None:
    """The regression guard for AGENTS.md's "never expose note content".

    A deny-list would render this the moment someone adds a field nobody
    thought to exclude; the allow-list drops anything it does not name.
    """
    logging.getLogger("obsidian_gateway.access").info(
        "request",
        extra={
            "transport": "rest",
            "method": "POST",
            "route": "/api/v1/inbox/notes",
            "status_code": 201,
            "duration_ms": 5.0,
            "content": "extremely sensitive body text that must not leak",
            "frontmatter": {"secret": "value"},
            "authorization": "Bearer some-token",
        },
    )

    line = rendered()[-1]
    assert "extremely sensitive body text" not in line
    assert "secret" not in line
    assert "some-token" not in line


# --- driven through the real application -------------------------------------


def test_rest_read_renders_transport_route_status_and_note_path(
    client: TestClient, auth_headers: dict[str, str], rendered: Rendered
) -> None:
    client.get(
        "/api/v1/notes",
        params={"path": "Knowledge/PC/GPU/RTX 5070.md"},
        headers=auth_headers,
    )

    line = _line_with(rendered(), "/api/v1/notes")
    fields = line.split()
    assert fields[2] == "rest"
    assert fields[3] == "GET"
    assert fields[4] == "/api/v1/notes"
    assert fields[5] == "200"
    assert line.endswith("note=Knowledge/PC/GPU/RTX 5070.md")


def test_rest_search_renders_query_length_and_result_count_but_not_the_query(
    client: TestClient, auth_headers: dict[str, str], rendered: Rendered
) -> None:
    secret_query = "very-specific-search-term-xyz"
    client.get("/api/v1/search", params={"q": secret_query}, headers=auth_headers)

    line = _line_with(rendered(), "/api/v1/search")
    assert secret_query not in line
    assert f"q_len={len(secret_query)}" in line
    assert "results=" in line


def test_vault_tree_renders_result_count(
    client: TestClient, auth_headers: dict[str, str], rendered: Rendered
) -> None:
    client.get("/api/v1/vault/tree", params={"limit": 100}, headers=auth_headers)

    assert "results=" in _line_with(rendered(), "/api/v1/vault/tree")


def test_bearer_token_never_appears_in_a_rendered_line(
    client: TestClient, auth_headers: dict[str, str], api_token: str, rendered: Rendered
) -> None:
    client.get("/api/v1/search", params={"q": "anything"}, headers=auth_headers)
    client.get("/api/v1/notes", params={"path": "does-not-exist.md"}, headers=auth_headers)

    for line in rendered():
        assert api_token not in line


def test_absolute_vault_path_never_appears_in_a_rendered_line(
    client: TestClient, auth_headers: dict[str, str], vault_root: Path, rendered: Rendered
) -> None:
    client.get(
        "/api/v1/notes",
        params={"path": "Knowledge/PC/GPU/RTX 5070.md"},
        headers=auth_headers,
    )
    client.get("/api/v1/notes", params={"path": "../secret.md"}, headers=auth_headers)

    for line in rendered():
        assert str(vault_root) not in line


def test_mcp_call_renders_tool_status_duration_and_result_count(
    mcp_client: TestClient, mcp_headers: dict[str, str], rendered: Rendered
) -> None:
    mcp_client.post(
        "/mcp/",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "search_notes", "arguments": {"query": "RTX"}},
        },
        headers=mcp_headers,
    )

    fields = _line_with(rendered(), "search_notes").split()
    assert fields[2] == "mcp"
    assert fields[3] == "tools/call"
    assert fields[4] == "search_notes"
    assert fields[5] == "success"
    assert fields[6].endswith("ms")
    assert any(field.startswith("results=") for field in fields)


def test_mcp_auth_failure_renders_the_reason(
    mcp_client: TestClient, rendered: Rendered
) -> None:
    mcp_client.post(
        "/mcp/",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )

    line = _line_with(rendered(), "mcp_auth_failed")
    assert line.split()[2] == "mcp"
    assert "reason=missing_or_non_bearer_authorization_header" in line


def test_mcp_call_note_not_found_renders_the_error_code(
    mcp_client: TestClient, mcp_headers: dict[str, str], rendered: Rendered
) -> None:
    """NoteNotFoundError sets no log_detail, so the supplementary
    mcp_tool_error record never fires for it — before this fix, the
    mcp_call record itself rendered with no code at all for the most
    common rejection case.
    """
    mcp_client.post(
        "/mcp/",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "read_note",
                "arguments": {"path": "Knowledge/does-not-exist.md"},
            },
        },
        headers=mcp_headers,
    )

    fields = _line_with(rendered(), "read_note").split()
    assert fields[2] == "mcp"
    assert fields[3] == "tools/call"
    assert fields[4] == "read_note"
    assert fields[5] == "error"
    assert "code=NOTE_NOT_FOUND" in fields


def test_oversized_request_renders_413_access_log(
    client: TestClient, auth_headers: dict[str, str], rendered: Rendered
) -> None:
    from app.config import get_settings

    content = "x" * (get_settings().max_request_bytes + 1)
    response = client.post(
        "/api/v1/inbox/notes",
        json={"title": "oversized", "content": content},
        headers=auth_headers,
    )
    assert response.status_code == 413

    line = _line_with(rendered(), "/api/v1/inbox/notes")
    fields = line.split()
    assert fields[2] == "rest"
    assert fields[3] == "POST"
    assert fields[4] == "/api/v1/inbox/notes"
    assert fields[5] == "413"
    assert content[:100] not in line


# --- configure_logging's own contract ----------------------------------------


def test_sdk_basic_config_cannot_add_a_second_handler_once_configured() -> None:
    """The assumption the call-site ordering in app/mcp_server.py rests on.

    Constructing an ``MCPServer`` calls ``logging.basicConfig``; that is
    documented to do nothing once the root logger has a handler. If a future
    SDK version passed ``force=True``, this test fails and the ordering comment
    in app/mcp_server.py stops being true — which is exactly when someone needs
    to know.
    """
    configure_logging()
    before = list(logging.getLogger().handlers)

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    assert logging.getLogger().handlers == before


def test_configure_logging_is_idempotent() -> None:
    configure_logging()
    configure_logging()
    configure_logging()

    ours = [h for h in logging.getLogger().handlers if h.get_name() == _HANDLER_NAME]
    assert len(ours) == 1


def test_configure_logging_leaves_other_root_handlers_alone() -> None:
    """Why this uses the plain logging API rather than ``dictConfig``: with an
    explicit ``root``, ``dictConfig`` replaces the handler list and would
    delete pytest's ``caplog`` handler for whichever test first imports the
    application.
    """
    someone_elses = logging.NullHandler()
    logging.getLogger().addHandler(someone_elses)

    configure_logging()

    assert someone_elses in logging.getLogger().handlers


def test_log_level_env_var_is_applied(monkeypatch: pytest.MonkeyPatch) -> None:
    """LOG_LEVEL reached Settings and compose.yaml before this change but was
    never applied to any logger.
    """
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    configure_logging()
    assert logging.getLogger("obsidian_gateway.access").level == logging.DEBUG

    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    configure_logging()
    assert logging.getLogger("obsidian_gateway.access").level == logging.WARNING


def test_unusable_log_level_falls_back_to_info(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "not-a-level")
    configure_logging()
    assert logging.getLogger("obsidian_gateway.access").level == logging.INFO


def test_unusable_timezone_does_not_stop_logging(
    monkeypatch: pytest.MonkeyPatch, rendered: Rendered
) -> None:
    """Misconfiguring TZ must not stop the process from logging: the line
    reporting the misconfiguration is the one that matters most.
    """
    monkeypatch.setenv("TZ", "Not/AZone")
    configure_logging()

    logging.getLogger("obsidian_gateway").warning("still_logging")
    assert "still_logging" in _line_with(rendered(), "still_logging")


def test_uvicorn_access_log_cannot_reach_the_handler(rendered: Rendered) -> None:
    """uvicorn's access formatter prints the raw request line, query string and
    all, so a propagating ``uvicorn.access`` would put the search term in the
    log — the thing IMPLEMENTATION_PLAN section 14 forbids and the reason the
    Dockerfile passes ``--no-access-log``. Unifying uvicorn's *other* loggers
    with ours must not drag this one along with them.
    """
    configure_logging()

    access = logging.getLogger("uvicorn.access")
    assert access.propagate is False
    assert access.handlers == []

    access.info('127.0.0.1:1234 - "GET /api/v1/search?q=secret-term HTTP/1.1" 200')
    assert not [line for line in rendered() if "secret-term" in line]


def test_sdk_per_request_session_noise_is_below_info(rendered: Rendered) -> None:
    """The SDK logs "Terminating session: None" at INFO once per request. This
    server is stateless, so there is never a session id and the line carries no
    information — while its sibling logger's startup lines still do.
    """
    configure_logging()

    logging.getLogger("mcp.server.streamable_http").info("Terminating session: None")
    logging.getLogger("mcp.server.streamable_http_manager").info(
        "StreamableHTTP session manager started"
    )

    lines = rendered()
    assert not [line for line in lines if "Terminating session" in line]
    assert _line_with(lines, "session manager started")


def test_auth_disabled_warning_renders_with_detail_and_no_secret(rendered: Rendered) -> None:
    """app/main.py's startup WARNING for AUTH_ENABLED=false — visible in
    ``docker logs`` and carrying no token or host information, only the fixed
    event name and a static detail string.
    """
    logging.getLogger("obsidian_gateway").warning(
        "authentication_disabled",
        extra={"detail": "AUTH_ENABLED=false: no bearer token is required for REST or MCP"},
    )

    line = _line_with(rendered(), "authentication_disabled")
    assert line.split()[1] == "WARN"
    assert "detail=AUTH_ENABLED=false:" in line
