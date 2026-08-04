"""``/mcp`` bearer authentication — MCP_IMPLEMENTATION_PLAN sections 8, 17.

app/mcp_auth.py's ``McpBearerAuthMiddleware`` wraps the MCP transport before
it is mounted, so every request is checked before any JSON-RPC method is
even parsed — this file proves that holds for both protocol eras
(``server/discover`` and ``initialize``) and that the failure responses
never leak the configured token or an internal reason string.
"""

from __future__ import annotations

import json
import logging

import pytest
from fastapi.testclient import TestClient
from mcp.server.mcpserver import MCPServer

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _tools_list_body() -> dict:
    return {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}


def _discover_body() -> dict:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "server/discover",
        "params": {
            "_meta": {
                "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                "io.modelcontextprotocol/clientCapabilities": {},
                "io.modelcontextprotocol/clientInfo": {"name": "pytest", "version": "0"},
            }
        },
    }


def _discover_headers(mcp_headers: dict) -> dict:
    return {
        **mcp_headers,
        "MCP-Protocol-Version": "2026-07-28",
        "Mcp-Method": "server/discover",
    }


def _initialize_body() -> dict:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "pytest", "version": "0"},
        },
    }


# --- rejected: missing / wrong scheme / empty / mismatched / malformed -------


def test_missing_authorization_header_is_rejected(
    mcp_client: TestClient, mcp_headers: dict
) -> None:
    headers = {k: v for k, v in mcp_headers.items() if k != "Authorization"}
    response = mcp_client.post("/mcp/", json=_tools_list_body(), headers=headers)
    assert response.status_code == 401


def test_basic_scheme_is_rejected(mcp_client: TestClient, mcp_headers: dict) -> None:
    headers = {**mcp_headers, "Authorization": "Basic dXNlcjpwYXNz"}
    response = mcp_client.post("/mcp/", json=_tools_list_body(), headers=headers)
    assert response.status_code == 401


def test_empty_bearer_credential_is_rejected(mcp_client: TestClient, mcp_headers: dict) -> None:
    headers = {**mcp_headers, "Authorization": "Bearer "}
    response = mcp_client.post("/mcp/", json=_tools_list_body(), headers=headers)
    assert response.status_code == 401


def test_short_token_is_rejected(mcp_client: TestClient, mcp_headers: dict) -> None:
    headers = {**mcp_headers, "Authorization": "Bearer short"}
    response = mcp_client.post("/mcp/", json=_tools_list_body(), headers=headers)
    assert response.status_code == 401


def test_mismatched_token_is_rejected(mcp_client: TestClient, mcp_headers: dict) -> None:
    headers = {**mcp_headers, "Authorization": "Bearer " + "x" * 32}
    response = mcp_client.post("/mcp/", json=_tools_list_body(), headers=headers)
    assert response.status_code == 401


def test_bearer_with_no_space_before_token_is_rejected(
    mcp_client: TestClient, mcp_headers: dict
) -> None:
    # Malformed header: no separator between scheme and credential, so the
    # partition("" ") split leaves the whole thing as the scheme.
    headers = {**mcp_headers, "Authorization": "BearerTokenGlued"}
    response = mcp_client.post("/mcp/", json=_tools_list_body(), headers=headers)
    assert response.status_code == 401


def test_completely_empty_authorization_header_is_rejected(
    mcp_client: TestClient, mcp_headers: dict
) -> None:
    headers = {**mcp_headers, "Authorization": ""}
    response = mcp_client.post("/mcp/", json=_tools_list_body(), headers=headers)
    assert response.status_code == 401


async def test_non_ascii_token_is_rejected_without_crashing(mcp_client: TestClient) -> None:
    # verify_bearer_token encodes both sides to UTF-8 before comparing (see
    # tests/test_auth.py) specifically so a non-ASCII credential is a clean
    # mismatch, never a 500 from an unhandled TypeError/UnicodeError. httpx2
    # (the client tests/conftest.py's TestClient is built on) refuses outright
    # to encode a str Authorization value as ASCII, so this drives the ASGI
    # scope directly with the raw UTF-8 bytes a real non-ASCII token would
    # arrive as on the wire. Safe to call the shared app without entering its
    # lifespan again: an unauthorized request is rejected by
    # McpBearerAuthMiddleware before it ever reaches the MCP transport/session
    # manager. `mcp_client` is only depended on to guarantee app.main has
    # already been imported and configured by the time this runs.
    from app.main import app

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/mcp/",
        "raw_path": b"/mcp/",
        "query_string": b"",
        "headers": [
            (b"host", b"testserver"),
            (b"content-type", b"application/json"),
            (b"accept", b"application/json, text/event-stream"),
            (b"authorization", "Bearer トークン".encode()),
        ],
        "client": ("127.0.0.1", 1234),
        "server": ("testserver", 80),
        "scheme": "http",
        "root_path": "",
    }
    body = json.dumps(_tools_list_body()).encode("utf-8")
    sent_once = False

    async def receive() -> dict:
        nonlocal sent_once
        if sent_once:
            return {"type": "http.disconnect"}
        sent_once = True
        return {"type": "http.request", "body": body, "more_body": False}

    events: list[dict] = []

    async def send(message: dict) -> None:
        events.append(message)

    await app(scope, receive, send)

    status = next(m["status"] for m in events if m["type"] == "http.response.start")
    assert status == 401


# --- accepted: correct token, and a differently-cased scheme name -----------


def test_correct_bearer_token_is_accepted(mcp_client: TestClient, mcp_headers: dict) -> None:
    response = mcp_client.post("/mcp/", json=_tools_list_body(), headers=mcp_headers)
    assert response.status_code == 200


@pytest.mark.parametrize("scheme", ["bearer", "BEARER", "BeArEr"])
def test_scheme_name_is_case_insensitive_like_rest(
    mcp_client: TestClient, mcp_headers: dict, api_token: str, scheme: str
) -> None:
    # Matches app/auth.py's require_token, which also does
    # credentials.scheme.lower() != "bearer" — this is an existing, deliberate
    # REST behaviour (RFC 7235 auth-scheme tokens are case-insensitive), not a
    # new relaxation introduced for MCP.
    headers = {**mcp_headers, "Authorization": f"{scheme} {api_token}"}
    response = mcp_client.post("/mcp/", json=_tools_list_body(), headers=headers)
    assert response.status_code == 200


# --- both server/discover (modern) and initialize (legacy) require auth -----


def test_discover_without_auth_is_rejected(mcp_client: TestClient, mcp_headers: dict) -> None:
    headers = {k: v for k, v in _discover_headers(mcp_headers).items() if k != "Authorization"}
    response = mcp_client.post("/mcp/", json=_discover_body(), headers=headers)
    assert response.status_code == 401


def test_discover_with_correct_auth_succeeds(mcp_client: TestClient, mcp_headers: dict) -> None:
    response = mcp_client.post(
        "/mcp/", json=_discover_body(), headers=_discover_headers(mcp_headers)
    )
    assert response.status_code == 200


def test_initialize_without_auth_is_rejected(mcp_client: TestClient, mcp_headers: dict) -> None:
    headers = {k: v for k, v in mcp_headers.items() if k != "Authorization"}
    response = mcp_client.post("/mcp/", json=_initialize_body(), headers=headers)
    assert response.status_code == 401


def test_initialize_with_correct_auth_succeeds(mcp_client: TestClient, mcp_headers: dict) -> None:
    response = mcp_client.post("/mcp/", json=_initialize_body(), headers=mcp_headers)
    assert response.status_code == 200


# --- the 401 body never leaks the token or an internal reason string --------


def test_unauthorized_response_never_contains_the_configured_token(
    mcp_client: TestClient, mcp_headers: dict, api_token: str
) -> None:
    headers = {k: v for k, v in mcp_headers.items() if k != "Authorization"}
    response = mcp_client.post("/mcp/", json=_tools_list_body(), headers=headers)
    assert api_token not in response.text


def test_unauthorized_response_body_is_the_fixed_generic_message(
    mcp_client: TestClient, mcp_headers: dict
) -> None:
    headers = {k: v for k, v in mcp_headers.items() if k != "Authorization"}
    response = mcp_client.post("/mcp/", json=_tools_list_body(), headers=headers)
    body = response.json()
    assert body == {
        "error": "invalid_token",
        "error_description": "A valid bearer token is required.",
    }
    # No internal reason label ("bearer_token_mismatch" etc.) anywhere in the
    # response — that goes to the server log only (app/mcp_auth.py's own
    # mcp_auth_failed log record), never to the client.
    assert "mismatch" not in response.text
    assert "missing_or_non_bearer" not in response.text


def test_unauthorized_response_has_www_authenticate_header(
    mcp_client: TestClient, mcp_headers: dict
) -> None:
    headers = {k: v for k, v in mcp_headers.items() if k != "Authorization"}
    response = mcp_client.post("/mcp/", json=_tools_list_body(), headers=headers)
    assert response.headers["www-authenticate"] == 'Bearer error="invalid_token"'


# --- /mcp bypasses the REST-only middleware entirely (D2) --------------------


def test_mcp_requests_never_produce_a_rest_access_log_entry(
    mcp_client: TestClient, mcp_headers: dict, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="obsidian_gateway.access")

    mcp_client.post("/mcp/", json=_tools_list_body(), headers=mcp_headers)
    mcp_client.get("/mcp/", headers={**mcp_headers, "Accept": "application/json"})
    mcp_client.delete("/mcp/", headers=mcp_headers)

    access_records = [r for r in caplog.records if r.name == "obsidian_gateway.access"]
    assert access_records == []


# --- AUTH_ENABLED=false: the middleware passes every request straight
# through, without inspecting Authorization at all --------------------------
#
# The shared mcp_client fixture's session manager can only ever run once, with
# the Settings it already started with (see conftest.py's mcp_client
# docstring), so these use an independent MCPServer/Settings pair instead —
# the same pattern tests/test_mcp_lifespan.py uses for lifecycle mechanics.


async def _send_tools_list(app, headers: dict[bytes, bytes]) -> tuple[int, dict]:
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "headers": [
            (b"host", b"testserver"),
            (b"content-type", b"application/json"),
            (b"accept", b"application/json, text/event-stream"),
            *headers.items(),
        ],
        "client": ("127.0.0.1", 1234),
        "server": ("testserver", 80),
        "scheme": "http",
        "root_path": "",
    }
    body = json.dumps(_tools_list_body()).encode("utf-8")
    sent_once = False

    async def receive() -> dict:
        nonlocal sent_once
        if sent_once:
            return {"type": "http.disconnect"}
        sent_once = True
        return {"type": "http.request", "body": body, "more_body": False}

    events: list[dict] = []

    async def send(message: dict) -> None:
        events.append(message)

    await app(scope, receive, send)

    status = next(m["status"] for m in events if m["type"] == "http.response.start")
    payload_bytes = b"".join(m["body"] for m in events if m["type"] == "http.response.body")
    return status, json.loads(payload_bytes)


@pytest.fixture
def disabled_auth_app(monkeypatch: pytest.MonkeyPatch):
    """McpBearerAuthMiddleware checks the process-wide ``get_settings()``
    directly (see app/mcp_auth.py) rather than whatever ``Settings`` instance
    ``build_mcp_transport`` itself was called with — so disabling auth for
    this fresh, independent ``MCPServer`` means driving that same cached
    singleton via the environment, exactly as it would be in production.
    """
    from app.config import get_settings
    from app.mcp_server import build_mcp_transport

    monkeypatch.setenv("API_TOKEN", "x" * 16)
    monkeypatch.setenv("MCP_ALLOWED_HOSTS", "testserver")
    monkeypatch.setenv("AUTH_ENABLED", "false")
    get_settings.cache_clear()

    fresh_mcp = MCPServer(name="mcp-auth-disabled-test")
    app = build_mcp_transport(fresh_mcp, get_settings())

    yield fresh_mcp, app

    get_settings.cache_clear()


async def test_auth_disabled_allows_missing_authorization_header(disabled_auth_app) -> None:
    fresh_mcp, app = disabled_auth_app
    async with fresh_mcp.session_manager.run():
        status, payload = await _send_tools_list(app, headers={})
    assert status == 200
    assert payload["result"]["tools"] == []


async def test_auth_disabled_allows_wrong_bearer_token(disabled_auth_app) -> None:
    fresh_mcp, app = disabled_auth_app
    async with fresh_mcp.session_manager.run():
        status, payload = await _send_tools_list(
            app, headers={b"authorization": b"Bearer wrong-token"}
        )
    assert status == 200
    assert payload["result"]["tools"] == []


async def test_auth_disabled_allows_malformed_authorization_header(disabled_auth_app) -> None:
    fresh_mcp, app = disabled_auth_app
    async with fresh_mcp.session_manager.run():
        status, payload = await _send_tools_list(
            app, headers={b"authorization": b"Basic dXNlcjpwYXNz"}
        )
    assert status == 200
    assert payload["result"]["tools"] == []
