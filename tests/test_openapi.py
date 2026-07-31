"""IMPLEMENTATION_PLAN section 12: a stable OpenAPI contract for the REST API,
kept for curl-based diagnostics, regression tests, and non-MCP clients now
that MCP (app/mcp_server.py) is the primary interface.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent

EXPECTED_OPERATIONS = {
    ("GET", "/api/v1/health"): "getHealth",
    ("GET", "/api/v1/search"): "searchNotes",
    ("GET", "/api/v1/notes"): "readNote",
    ("POST", "/api/v1/inbox/notes"): "createInboxNote",
}


def test_operation_ids_match_the_plan(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    found = {
        (method.upper(), path): op.get("operationId")
        for path, methods in schema["paths"].items()
        for method, op in methods.items()
    }
    for key, operation_id in EXPECTED_OPERATIONS.items():
        assert found.get(key) == operation_id


def test_committed_openapi_json_matches_the_app() -> None:
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "export_openapi.py"), "--check"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_search_and_notes_require_bearer_auth_security_scheme(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    assert "bearerAuth" in schema["components"]["securitySchemes"]
    search_op = schema["paths"]["/api/v1/search"]["get"]
    assert any("bearerAuth" in sec for sec in search_op.get("security", []))


def test_health_has_no_security_requirement(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    health_op = schema["paths"]["/api/v1/health"]["get"]
    assert not health_op.get("security")


def test_committed_openapi_json_file_exists_and_is_valid_json() -> None:
    path = REPO_ROOT / "openapi.json"
    assert path.exists()
    json.loads(path.read_text(encoding="utf-8"))
