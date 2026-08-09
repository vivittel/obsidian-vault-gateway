"""REST/MCP coexistence — MCP_IMPLEMENTATION_PLAN section 15 (U6).

Confirms mounting ``/mcp`` alongside the REST app changes nothing about
REST's own behaviour, and that the two transports' logs never
cross-contaminate. REST is health-only now (docs/adr/0010-*.md), so the only
REST behaviour left to check here is health itself; MCP's own filesystem and
security edge cases are already exhaustively covered by tests/test_mcp_tools.py
and tests/test_mcp_auth.py.
"""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient


def test_health_still_ok_without_auth_after_mcp_mount(client: TestClient) -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "vault_readable": True, "inbox_writable": True}


def test_rest_call_never_logged_under_the_mcp_logger(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="obsidian_gateway.mcp")
    client.get("/api/v1/health")
    mcp_records = [r for r in caplog.records if r.name == "obsidian_gateway.mcp"]
    assert mcp_records == []


def test_mcp_call_never_logged_under_the_rest_access_logger(
    mcp_client: TestClient, mcp_headers: dict, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="obsidian_gateway.access")
    mcp_client.post(
        "/mcp/",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "get_health", "arguments": {}},
        },
        headers=mcp_headers,
    )
    access_records = [r for r in caplog.records if r.name == "obsidian_gateway.access"]
    assert access_records == []
