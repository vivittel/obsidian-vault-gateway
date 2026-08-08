"""``/mcp`` Streamable HTTP protocol — MCP_IMPLEMENTATION_PLAN sections 15-18.

Covers both protocol eras (2026-07-28's ``server/discover`` and the legacy
``initialize`` handshake), the high-level SDK ``Client``, and the transport
edge cases the plan calls out: malformed input, an unknown tool, oversized
requests, concurrent calls, and GET/DELETE.

Wire-level requirements below (the ``Mcp-Method``/``Mcp-Name`` headers and
the ``params._meta`` envelope for modern requests) were reverse-engineered
against the installed SDK by hand, not copied from documentation — none of
the project's planning docs mention them. Getting any of the three wrong
produces a ``400`` with a message naming exactly which header/key is missing
or mismatched, which is how each was found.

Most tests below share the session-scoped ``mcp_client`` fixture (see its
docstring in conftest.py for why session-scoped: ``mcp.session_manager.run()``
is one-shot per ``MCPServer`` instance, and the shared production singleton
is built once at import time). The two exceptions — the high-level ``Client``
test and the GET/SSE test — build their own independent, throwaway
``MCPServer`` via ``app.mcp_server.build_mcp_transport`` instead, because
each needs to drive raw async/ASGI calls in *this test's own* event loop,
which cannot safely share a session manager whose task group lives in
``mcp_client``'s separate portal thread.
"""

from __future__ import annotations

import concurrent.futures
import contextlib
import logging

import anyio
import httpx2
import pytest
from fastapi.testclient import TestClient
from mcp.client.client import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from starlette.applications import Starlette
from starlette.routing import Mount

from app.config import PACKAGE_VERSION, Settings, get_settings
from app.mcp_server import build_mcp_transport, get_health

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


MODERN_PROTOCOL_VERSION = "2026-07-28"
LEGACY_PROTOCOL_VERSION = "2025-06-18"  # any HANDSHAKE_PROTOCOL_VERSIONS member


def _modern_meta() -> dict:
    return {
        "io.modelcontextprotocol/protocolVersion": MODERN_PROTOCOL_VERSION,
        "io.modelcontextprotocol/clientCapabilities": {},
        "io.modelcontextprotocol/clientInfo": {"name": "pytest", "version": "0"},
    }


def _modern_headers(
    mcp_headers: dict[str, str], *, method: str, name: str | None = None
) -> dict[str, str]:
    headers = {
        **mcp_headers,
        "MCP-Protocol-Version": MODERN_PROTOCOL_VERSION,
        "Mcp-Method": method,
    }
    if name is not None:
        headers["Mcp-Name"] = name
    return headers


def _modern_call_tool(
    mcp_client: TestClient, mcp_headers: dict, *, request_id: int, name: str, arguments: dict
):
    return mcp_client.post(
        "/mcp/",
        json={
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments, "_meta": _modern_meta()},
        },
        headers=_modern_headers(mcp_headers, method="tools/call", name=name),
    )


def _legacy_call_tool(
    mcp_client: TestClient, mcp_headers: dict, *, request_id: int, name: str, arguments: dict
):
    return mcp_client.post(
        "/mcp/",
        json={
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
        headers=mcp_headers,
    )


@contextlib.asynccontextmanager
async def _standalone_mcp_app(settings: Settings):
    """An independent, single-tool MCP ASGI app for tests that must drive
    their own event loop directly (see module docstring). Reuses the real
    ``get_health`` tool function and the real ``build_mcp_transport`` wiring,
    just on a throwaway ``MCPServer`` instead of the shared singleton.
    """
    fresh_mcp = MCPServer(name="standalone-test-server")
    fresh_mcp.add_tool(
        get_health,
        annotations=ToolAnnotations(
            read_only_hint=True,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        ),
    )
    mcp_app_with_auth = build_mcp_transport(fresh_mcp, settings)

    @contextlib.asynccontextmanager
    async def _lifespan(_app: Starlette):
        async with fresh_mcp.session_manager.run():
            yield

    test_app = Starlette(routes=[Mount("/mcp", app=mcp_app_with_auth)], lifespan=_lifespan)
    async with test_app.router.lifespan_context(test_app):
        yield test_app


# --- modern: server/discover -> tools/list -> tools/call (8 tools) -----------


def test_modern_discover_returns_instructions_and_capabilities(
    mcp_client: TestClient, mcp_headers: dict
) -> None:
    response = mcp_client.post(
        "/mcp/",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "server/discover",
            "params": {"_meta": _modern_meta()},
        },
        headers=_modern_headers(mcp_headers, method="server/discover"),
    )
    assert response.status_code == 200
    result = response.json()["result"]
    assert result["supportedVersions"] == [MODERN_PROTOCOL_VERSION]
    assert "read-only" in result["instructions"]
    assert "tools" in result["capabilities"]


def test_modern_tools_list_has_eight_tools(mcp_client: TestClient, mcp_headers: dict) -> None:
    response = mcp_client.post(
        "/mcp/",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {"_meta": _modern_meta()},
        },
        headers=_modern_headers(mcp_headers, method="tools/list"),
    )
    assert response.status_code == 200
    names = {tool["name"] for tool in response.json()["result"]["tools"]}
    assert names == {
        "get_health",
        "search_notes",
        "read_note",
        "get_vault_tree",
        "get_vault_summary",
        "find_duplicate_candidates",
        "create_inbox_note",
        "append_inbox_note",
    }


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("get_health", {}),
        ("search_notes", {"query": "RTX 5070"}),
        ("read_note", {"path": "Knowledge/PC/GPU/RTX 5070.md"}),
        ("create_inbox_note", {"title": "Modern flow note", "export": {"tldr": ["x"]}}),
    ],
)
def test_modern_tools_call_succeeds(
    mcp_client: TestClient, mcp_headers: dict, name: str, arguments: dict
) -> None:
    response = _modern_call_tool(
        mcp_client, mcp_headers, request_id=3, name=name, arguments=arguments
    )
    assert response.status_code == 200
    result = response.json()["result"]
    assert result["isError"] is False
    assert "structuredContent" in result


# --- legacy: initialize -> initialized -> tools/list -> tools/call (8 tools) --


def test_legacy_initialize_succeeds(mcp_client: TestClient, mcp_headers: dict) -> None:
    response = mcp_client.post(
        "/mcp/",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": LEGACY_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "0"},
            },
        },
        headers=mcp_headers,
    )
    assert response.status_code == 200
    result = response.json()["result"]
    assert result["protocolVersion"] == LEGACY_PROTOCOL_VERSION
    assert result["serverInfo"]["name"] == "Obsidian Vault Gateway"
    assert result["serverInfo"]["version"]  # non-empty: catches both sides being blank
    assert result["serverInfo"]["version"] == PACKAGE_VERSION
    assert "read-only" in result["instructions"]

    from app.main import rest_app

    assert rest_app.version == PACKAGE_VERSION


def test_legacy_initialized_notification_is_accepted(
    mcp_client: TestClient, mcp_headers: dict
) -> None:
    response = mcp_client.post(
        "/mcp/",
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        headers=mcp_headers,
    )
    assert response.status_code == 202


def test_legacy_tools_list_has_eight_tools(mcp_client: TestClient, mcp_headers: dict) -> None:
    response = mcp_client.post(
        "/mcp/",
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        headers=mcp_headers,
    )
    assert response.status_code == 200
    names = {tool["name"] for tool in response.json()["result"]["tools"]}
    assert names == {
        "get_health",
        "search_notes",
        "read_note",
        "get_vault_tree",
        "get_vault_summary",
        "find_duplicate_candidates",
        "create_inbox_note",
        "append_inbox_note",
    }


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("get_health", {}),
        ("search_notes", {"query": "RTX 5070"}),
        ("read_note", {"path": "Knowledge/PC/GPU/RTX 5070.md"}),
        ("create_inbox_note", {"title": "Legacy flow note", "export": {"tldr": ["x"]}}),
    ],
)
def test_legacy_tools_call_succeeds(
    mcp_client: TestClient, mcp_headers: dict, name: str, arguments: dict
) -> None:
    response = _legacy_call_tool(
        mcp_client, mcp_headers, request_id=3, name=name, arguments=arguments
    )
    assert response.status_code == 200
    result = response.json()["result"]
    assert result["isError"] is False


# --- high-level Client(mode="auto") over a real HTTP transport ---------------


async def test_high_level_client_auto_mode_over_http(env: None, api_token: str) -> None:
    """``mode="auto"`` (the default) probes ``server/discover`` and falls
    back to the initialize handshake — this exercises that probe against a
    real Streamable HTTP transport (JSON-RPC framing over HTTP), not the
    in-process no-framing shortcut ``Client`` also supports for a bare
    ``Server``/``MCPServer`` instance.
    """
    async with _standalone_mcp_app(get_settings()) as app:
        http_client = httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app),
            headers={"Authorization": f"Bearer {api_token}"},
        )
        transport = streamable_http_client("http://testserver/mcp/", http_client=http_client)
        async with Client(transport, mode="auto") as client:
            result = await client.call_tool("get_health", {})
            assert result.structured_content["status"] == "ok"


# --- both /mcp and /mcp/ must work --------------------------------------------


@pytest.mark.parametrize("path", ["/mcp", "/mcp/"])
def test_both_mcp_and_trailing_slash_variant_connect(
    mcp_client: TestClient, mcp_headers: dict, path: str
) -> None:
    response = mcp_client.post(
        path,
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        headers=mcp_headers,
    )
    assert response.status_code == 200
    assert {t["name"] for t in response.json()["result"]["tools"]} == {
        "get_health",
        "search_notes",
        "read_note",
        "get_vault_tree",
        "get_vault_summary",
        "find_duplicate_candidates",
        "create_inbox_note",
        "append_inbox_note",
    }


# --- bare "/mcp" is normalized onto "/mcp/" before routing, not redirected ----
#
# A 307-redirect previously handled the bare path, but sat outside
# McpBearerAuthMiddleware (which only wraps the *mounted* app at "/mcp/") and
# was registered for GET/POST/DELETE only. Any other verb fell through to the
# catch-all "/" Mount(app=rest_app) and came back as a REST error envelope.
# The tests below drive httpx with follow_redirects=False specifically so a
# stray 307 (rather than a direct MCP-shaped response) makes the assertion
# fail loudly instead of being silently swallowed by redirect-following.


def test_unauthenticated_post_bare_mcp_is_401_not_a_redirect(
    mcp_client: TestClient, mcp_headers: dict
) -> None:
    headers = {k: v for k, v in mcp_headers.items() if k != "Authorization"}
    response = mcp_client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        headers=headers,
        follow_redirects=False,
    )
    assert response.status_code == 401
    assert response.json() == {
        "error": "invalid_token",
        "error_description": "A valid bearer token is required.",
    }


@pytest.mark.parametrize("method", ["post", "get", "delete", "options", "patch", "put"])
def test_unauthenticated_bare_and_slash_mcp_get_the_same_status(
    mcp_client: TestClient, mcp_headers: dict, method: str
) -> None:
    headers = {k: v for k, v in mcp_headers.items() if k != "Authorization"}
    bare = getattr(mcp_client, method)("/mcp", headers=headers, follow_redirects=False)
    slashed = getattr(mcp_client, method)("/mcp/", headers=headers, follow_redirects=False)
    assert bare.status_code == slashed.status_code
    assert bare.status_code != 307


@pytest.mark.parametrize("method", ["options", "patch", "put"])
def test_authenticated_unsupported_methods_never_return_a_rest_error_envelope(
    mcp_client: TestClient, mcp_headers: dict, method: str
) -> None:
    # These verbs are not handled by the MCP transport's own routes either,
    # but the response must still come from the MCP side (or the auth
    # middleware) — never app/main.py's REST exception handlers, which would
    # shape the body as {"error": {"code": ..., "message": ...}} with a
    # NOTE_NOT_FOUND/INTERNAL_ERROR code rather than MCP's own error shape.
    response = getattr(mcp_client, method)(
        "/mcp", headers=mcp_headers, follow_redirects=False
    )
    body = response.json()
    # REST's envelope (app/exceptions.py's error_envelope) is exactly
    # {"error": {"code": <ErrorCode string>, "message": ...}} with no
    # "jsonrpc" key at all. MCP's own JSON-RPC error shape always carries
    # "jsonrpc" and an integer error.code (e.g. -32600) — checking for that
    # key is what actually distinguishes "the MCP side answered" from "the
    # request fell through to rest_app and got REST's envelope instead".
    assert body.get("jsonrpc") == "2.0"
    assert isinstance(body["error"]["code"], int)


def test_authenticated_post_bare_and_slash_mcp_return_equivalent_mcp_responses(
    mcp_client: TestClient, mcp_headers: dict
) -> None:
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    bare = mcp_client.post("/mcp", json=body, headers=mcp_headers, follow_redirects=False)
    slashed = mcp_client.post("/mcp/", json=body, headers=mcp_headers, follow_redirects=False)
    assert bare.status_code == slashed.status_code == 200
    assert {t["name"] for t in bare.json()["result"]["tools"]} == {
        t["name"] for t in slashed.json()["result"]["tools"]
    }


def test_bare_mcp_rejects_a_host_outside_the_allowlist(
    mcp_client: TestClient, mcp_headers: dict
) -> None:
    # env's MCP_ALLOWED_HOSTS is "testserver,127.0.0.1:*,localhost:*" (see
    # conftest.py); overriding the Host header directly proves normalization
    # runs before, not instead of, the SDK's own DNS-rebinding check.
    headers = {**mcp_headers, "Host": "evil.example.net"}
    response = mcp_client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        headers=headers,
        follow_redirects=False,
    )
    assert response.status_code == 421


def test_bare_mcp_query_string_survives_normalization(
    mcp_client: TestClient, mcp_headers: dict
) -> None:
    # The rewrite must only touch scope["path"]/scope["raw_path"] — a query
    # string on the bare path must reach the MCP transport unchanged.
    response = mcp_client.post(
        "/mcp?foo=bar",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        headers=mcp_headers,
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert {t["name"] for t in response.json()["result"]["tools"]} == {
        "get_health",
        "search_notes",
        "read_note",
        "get_vault_tree",
        "get_vault_summary",
        "find_duplicate_candidates",
        "create_inbox_note",
        "append_inbox_note",
    }


def test_rest_health_unaffected_by_bare_mcp_path_normalization(client: TestClient) -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "vault_readable": True, "inbox_writable": True}


# --- malformed input / unknown tool / bad arguments / oversized / concurrent --


def test_malformed_jsonrpc_missing_field_is_rejected(
    mcp_client: TestClient, mcp_headers: dict
) -> None:
    response = mcp_client.post(
        "/mcp/",
        json={"id": 1, "method": "tools/list", "params": {}},  # no "jsonrpc"
        headers=mcp_headers,
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == -32602


def test_non_json_body_is_rejected(mcp_client: TestClient, mcp_headers: dict) -> None:
    response = mcp_client.post("/mcp/", content=b"not json{{{", headers=mcp_headers)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == -32700


def test_unknown_method_returns_method_not_found(mcp_client: TestClient, mcp_headers: dict) -> None:
    response = mcp_client.post(
        "/mcp/",
        json={"jsonrpc": "2.0", "id": 1, "method": "totally/bogus", "params": {}},
        headers=mcp_headers,
    )
    assert response.status_code == 200
    assert response.json()["error"]["code"] == -32601


def test_unknown_tool_is_a_clean_tool_error_not_a_protocol_error(
    mcp_client: TestClient, mcp_headers: dict
) -> None:
    response = _legacy_call_tool(
        mcp_client, mcp_headers, request_id=1, name="not_a_real_tool", arguments={}
    )
    assert response.status_code == 200
    result = response.json()["result"]
    assert result["isError"] is True
    assert "not_a_real_tool" in result["content"][0]["text"]


def test_missing_required_argument_is_a_tool_error(
    mcp_client: TestClient, mcp_headers: dict
) -> None:
    response = _legacy_call_tool(
        mcp_client, mcp_headers, request_id=1, name="read_note", arguments={}
    )
    assert response.status_code == 200
    assert response.json()["result"]["isError"] is True


def test_extra_unexpected_argument_is_ignored_not_rejected(
    mcp_client: TestClient, mcp_headers: dict
) -> None:
    # Confirmed against the installed SDK: the auto-generated argument model
    # silently ignores unrecognised keys rather than rejecting them.
    #
    # This is no longer true for `create_inbox_note`: it alone has
    # `_StrictCreateInboxNoteArgumentsMiddleware` (app/mcp_server.py) fail
    # closed on stray top-level arguments instead, since silently dropping
    # `content`/`frontmatter` on a write tool is user-visible data loss, not
    # a compatibility nicety — see
    # test_create_inbox_note_rejects_unexpected_top_level_arguments below.
    response = _legacy_call_tool(
        mcp_client, mcp_headers, request_id=1, name="get_health", arguments={"bogus": "x"}
    )
    assert response.status_code == 200
    assert response.json()["result"]["isError"] is False


# --- create_inbox_note: fail-closed on unexpected top-level arguments -------
#
# _StrictCreateInboxNoteArgumentsMiddleware (app/mcp_server.py) only engages
# for requests that actually go through the JSON-RPC dispatch, which is
# exactly this module's mcp_client — unlike tests/test_mcp_tools.py's direct
# mcp.call_tool(...) calls, which bypass it (see that module's docstring).


@pytest.mark.parametrize(
    "extra_arguments",
    [
        {"content": "legacy body"},
        {"content": "legacy body", "export": {"tldr": ["y"]}},
        {"frontmatter": {"source": "x"}, "export": {"tldr": ["y"]}},
        {"path": "00_Inbox/ChatGPT/x.md", "export": {"tldr": ["y"]}},
        {"totally_unknown_key": "z", "export": {"tldr": ["y"]}},
        # related_notes belongs inside export, not at the top level (issue #13).
        {"related_notes": ["Knowledge/Foo.md"], "export": {"tldr": ["y"]}},
    ],
)
def test_create_inbox_note_rejects_unexpected_top_level_arguments(
    mcp_client: TestClient, mcp_headers: dict, extra_arguments: dict
) -> None:
    inbox_root = get_settings().inbox_root
    response = _legacy_call_tool(
        mcp_client,
        mcp_headers,
        request_id=1,
        name="create_inbox_note",
        arguments={"title": "x", **extra_arguments},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["error"]["data"] == {"code": "VALIDATION_ERROR"}
    assert not list(inbox_root.glob("x*.md"))


def test_create_inbox_note_rejects_unexpected_top_level_arguments_modern(
    mcp_client: TestClient, mcp_headers: dict
) -> None:
    inbox_root = get_settings().inbox_root
    response = _modern_call_tool(
        mcp_client,
        mcp_headers,
        request_id=1,
        name="create_inbox_note",
        arguments={"title": "modern reject check", "content": "legacy body"},
    )
    # Confirmed by direct inspection: the 2026-07-28 era maps a JSON-RPC-level
    # error to HTTP 400 (unlike the legacy era's 200-with-an-error-body
    # convention used elsewhere in this file) — a pre-existing transport
    # difference between eras, not something this middleware changes.
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["data"] == {"code": "VALIDATION_ERROR"}
    assert not list(inbox_root.glob("modern reject check*.md"))


def test_create_inbox_note_rejection_sorts_keys_and_does_not_log_values(
    mcp_client: TestClient,
    mcp_headers: dict,
    caplog: pytest.LogCaptureFixture,
) -> None:
    inbox_root = get_settings().inbox_root
    caplog.set_level(logging.INFO, logger="obsidian_gateway.mcp")

    response = _legacy_call_tool(
        mcp_client,
        mcp_headers,
        request_id=1,
        name="create_inbox_note",
        arguments={
            "title": "sorted reject check",
            "frontmatter": {"secret": "frontmatter-value"},
            "content": "content-value",
            "export": {"tldr": ["y"]},
        },
    )

    body = response.json()
    assert body["error"]["data"] == {"code": "VALIDATION_ERROR"}
    assert body["error"]["message"] == (
        "Unexpected fields for create_inbox_note: content, frontmatter."
    )
    assert "content-value" not in caplog.text
    assert "frontmatter-value" not in caplog.text
    assert not list(inbox_root.glob("sorted reject check*.md"))


def test_create_inbox_note_rejection_is_recorded_in_the_mcp_access_log(
    mcp_client: TestClient,
    mcp_headers: dict,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # A middleware veto never runs the tool body, so _McpCall never runs
    # either — this pins that the write attempt still leaves exactly one
    # mcp_call audit-log line, via the shared _log_mcp_call helper.
    caplog.set_level(logging.INFO, logger="obsidian_gateway.mcp")

    _legacy_call_tool(
        mcp_client,
        mcp_headers,
        request_id=1,
        name="create_inbox_note",
        arguments={"title": "audit log check", "content": "x"},
    )

    mcp_call_records = [
        r
        for r in caplog.records
        if r.name == "obsidian_gateway.mcp" and getattr(r, "tool", None) == "create_inbox_note"
    ]
    assert len(mcp_call_records) == 1
    assert mcp_call_records[0].status == "error"
    assert mcp_call_records[0].code == "VALIDATION_ERROR"


def test_argument_type_mismatch_is_a_tool_error(
    mcp_client: TestClient, mcp_headers: dict
) -> None:
    response = _legacy_call_tool(
        mcp_client,
        mcp_headers,
        request_id=1,
        name="search_notes",
        arguments={"limit": "not-a-number"},
    )
    assert response.status_code == 200
    assert response.json()["result"]["isError"] is True


def test_oversized_request_is_rejected(mcp_client: TestClient, mcp_headers: dict) -> None:
    # Shared MAX_REQUEST_BYTES (U3) — mcp_client's own environment sets 2097152.
    oversized_content = "x" * (3 * 1024 * 1024)
    response = _legacy_call_tool(
        mcp_client,
        mcp_headers,
        request_id=1,
        name="create_inbox_note",
        arguments={"title": "too big", "export": {"tldr": [oversized_content]}},
    )
    assert response.status_code == 413


def test_concurrent_tool_calls_do_not_interfere(mcp_client: TestClient, mcp_headers: dict) -> None:
    # stateless_http=True means a fresh transport per request — this proves
    # that holds under real overlap, not just sequential calls.
    def call(i: int):
        return _legacy_call_tool(
            mcp_client, mcp_headers, request_id=i, name="get_health", arguments={}
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        responses = list(executor.map(call, range(5)))

    assert all(r.status_code == 200 for r in responses)
    assert all(r.json()["result"]["structuredContent"]["status"] == "ok" for r in responses)


# --- GET / DELETE: no hang, no 5xx, SDK-documented status --------------------


def test_delete_returns_405_not_a_hang_or_crash(mcp_client: TestClient, mcp_headers: dict) -> None:
    # D5: stateless_http=True means there is no session to terminate, and
    # that is not treated as a failure — DELETE succeeding is not required,
    # only that it answers cleanly instead of hanging or 500ing.
    response = mcp_client.delete("/mcp/", headers=mcp_headers)
    assert response.status_code == 405
    assert (
        response.json()["error"]["message"]
        == "Method Not Allowed: Session termination not supported"
    )


def test_get_without_sse_accept_returns_406_not_a_hang_or_crash(
    mcp_client: TestClient, mcp_headers: dict
) -> None:
    headers = {**mcp_headers, "Accept": "application/json"}
    response = mcp_client.get("/mcp/", headers=headers)
    assert response.status_code == 406
    assert "text/event-stream" in response.json()["error"]["message"]


async def test_get_with_sse_accept_opens_stream_promptly_not_a_hang(
    env: None, api_token: str
) -> None:
    """A GET with a correct SSE Accept header is a deliberately long-lived
    server-push channel by design (MCP_IMPLEMENTATION_PLAN section 15's
    "ハングせず" requirement, D5) — the failure mode this actually guards
    against is the server never answering at all. httpx's ASGI transport
    awaits the whole app call before returning anything, which would make
    *any* open stream look like a hang through it regardless of server
    behaviour, so this drives the ASGI scope directly and asserts only that
    ``http.response.start`` (status + headers) arrives within a few seconds.
    """
    async with _standalone_mcp_app(get_settings()) as app:

        async def receive() -> dict:
            return {"type": "http.disconnect"}

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/mcp/",
            "raw_path": b"/mcp/",
            "query_string": b"",
            "headers": [
                (b"host", b"testserver"),
                (b"authorization", f"Bearer {api_token}".encode()),
                (b"accept", b"text/event-stream"),
            ],
            "client": ("127.0.0.1", 1234),
            "server": ("testserver", 80),
            "scheme": "http",
            "root_path": "",
        }
        got_start = anyio.Event()
        start_message: dict = {}

        async def send(message: dict) -> None:
            if message["type"] == "http.response.start":
                start_message.update(message)
                got_start.set()

        async with anyio.create_task_group() as tg:
            tg.start_soon(app, scope, receive, send)
            with anyio.fail_after(5):
                await got_start.wait()
            tg.cancel_scope.cancel()

    assert start_message["status"] == 200
    headers = dict(start_message["headers"])
    assert headers[b"content-type"] == b"text/event-stream"
