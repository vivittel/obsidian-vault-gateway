"""REST/MCP coexistence — MCP_IMPLEMENTATION_PLAN section 15 (U6).

Confirms mounting ``/mcp`` alongside the REST app changes nothing about
REST's own behaviour, and that the two transports' logs never
cross-contaminate. Deliberately narrow, per U6: REST's own filesystem and
security edge cases are already exhaustively covered by test_health.py /
test_search.py / test_notes.py / test_inbox.py / test_logging.py, all of
which already run against this same composed app.
"""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient


def test_health_still_ok_without_auth_after_mcp_mount(client: TestClient) -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "vault_readable": True, "inbox_writable": True}


def test_search_still_requires_auth_and_returns_same_shape(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    unauthenticated = client.get("/api/v1/search")
    assert unauthenticated.status_code == 401

    response = client.get("/api/v1/search", params={"q": "RTX 5070"}, headers=auth_headers)
    assert response.status_code == 200
    assert set(response.json().keys()) == {"results", "next_cursor", "skipped_count"}


def test_read_note_still_works_the_same(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.get(
        "/api/v1/notes",
        params={"path": "Knowledge/PC/GPU/RTX 5070.md"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert set(response.json().keys()) == {
        "id",
        "path",
        "title",
        "frontmatter",
        "content",
        "modified_at",
        "truncated",
    }


def test_create_inbox_note_still_works_the_same(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.post(
        "/api/v1/inbox/notes",
        json={"title": "Coexistence check", "content": "x\n"},
        headers=auth_headers,
    )
    assert response.status_code == 201
    body = response.json()
    # related_notes_linked/related_notes_skipped (issue #13) are a deliberate,
    # additive CreatedNoteResponse extension — always 0/0 on this raw-content
    # path, since there is no export.related_notes to verify.
    assert set(body.keys()) == {
        "id",
        "path",
        "title",
        "modified_at",
        "related_notes_linked",
        "related_notes_skipped",
    }
    assert body["related_notes_linked"] == 0
    assert body["related_notes_skipped"] == 0
    assert body["path"] == "00_Inbox/ChatGPT/Coexistence check.md"


def test_rest_call_never_logged_under_the_mcp_logger(
    client: TestClient, auth_headers: dict[str, str], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="obsidian_gateway.mcp")
    client.get("/api/v1/search", params={"q": "RTX 5070"}, headers=auth_headers)
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
