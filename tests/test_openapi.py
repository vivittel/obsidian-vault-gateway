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

from app.exceptions import ErrorCode

REPO_ROOT = Path(__file__).resolve().parent.parent

EXPECTED_OPERATIONS = {
    ("GET", "/api/v1/health"): "getHealth",
    ("GET", "/api/v1/search"): "searchNotes",
    ("GET", "/api/v1/notes"): "readNote",
    ("GET", "/api/v1/vault/tree"): "getVaultTree",
    ("GET", "/api/v1/vault/summary"): "getVaultSummary",
    ("GET", "/api/v1/inbox/duplicate-candidates"): "findDuplicateCandidates",
    ("POST", "/api/v1/inbox/notes"): "createInboxNote",
    ("POST", "/api/v1/inbox/notes/append"): "appendInboxNote",
}

# Every authenticated operation — every one of EXPECTED_OPERATIONS except
# health, which takes no bearer token at all (test_health_has_no_security_
# requirement below).
AUTHENTICATED_OPERATIONS = {
    key: value for key, value in EXPECTED_OPERATIONS.items() if key != ("GET", "/api/v1/health")
}

# RATE_LIMITED is part of the published code vocabulary (app/exceptions.py's
# ErrorCode) but no GatewayError subclass ever raises it — see that module's
# docstring. Every other code must appear on at least one operation's
# responses, or app/openapi_responses.py's per-route mapping has silently
# fallen out of sync with app/exceptions.py.
UNREACHABLE_ERROR_CODES = {ErrorCode.RATE_LIMITED}


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


def test_inbox_note_create_request_documents_the_content_export_exclusion(
    client: TestClient,
) -> None:
    # The `content`/`export` exclusion is enforced by a model_validator, not
    # by an `oneOf` in the schema — the schema alone can't express it. This
    # pins that the rule is at least documented in the generated OpenAPI
    # (the FastAPI/pydantic docstring -> schema `description` path), so it
    # doesn't silently vanish the way a LogRecord attribute can be dropped by
    # a formatter without ever reaching the rendered output.
    schema = client.get("/openapi.json").json()
    description = schema["components"]["schemas"]["InboxNoteCreateRequest"]["description"]
    assert "Exactly one of" in description
    assert "`content`" in description
    assert "`export`" in description


# --- error contract: every failing response is `ErrorResponse`, never the
# 422/HTTPValidationError shape this application never actually returns
# (app/main.py's handle_validation_error converts every RequestValidationError
# into a 400 with the same envelope every other failure uses). See
# app/openapi_responses.py. -----------------------------------------------------


def test_no_operation_documents_a_422(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    for path, methods in schema["paths"].items():
        for method, op in methods.items():
            assert "422" not in op["responses"], f"{method.upper()} {path} still documents a 422"


def test_no_operation_documents_4xx_wildcard(client: TestClient) -> None:
    # A "4XX" entry would suppress FastAPI's phantom 422 exactly like
    # "default" does, but this project's error_responses() always uses
    # "default" specifically — asserting "4XX" is absent catches a future
    # edit that accidentally introduces a second, redundant suppression path.
    schema = client.get("/openapi.json").json()
    for methods in schema["paths"].values():
        for op in methods.values():
            assert "4XX" not in op["responses"]


def test_fastapis_own_validation_error_schemas_are_not_generated(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    schema_names = set(schema["components"]["schemas"])
    assert "HTTPValidationError" not in schema_names
    assert "ValidationError" not in schema_names


def test_error_response_schema_is_registered_and_referenced(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    assert "ErrorResponse" in schema["components"]["schemas"]
    assert "ErrorDetail" in schema["components"]["schemas"]

    referenced = False
    for methods in schema["paths"].values():
        for op in methods.values():
            for response in op["responses"].values():
                content = response.get("content", {}).get("application/json", {})
                if "ErrorResponse" in content.get("schema", {}).get("$ref", ""):
                    referenced = True
    assert referenced, "ErrorResponse is defined but no operation's responses reference it"


def test_every_operation_has_a_default_error_response(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    for path, methods in schema["paths"].items():
        for method, op in methods.items():
            assert "default" in op["responses"], f"{method.upper()} {path} has no default response"


def test_every_authenticated_operation_documents_401(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    for (method, path), _operation_id in AUTHENTICATED_OPERATIONS.items():
        op = schema["paths"][path][method.lower()]
        assert "401" in op["responses"], f"{method} {path} does not document 401"


def test_health_documents_no_401(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    health_op = schema["paths"]["/api/v1/health"]["get"]
    assert "401" not in health_op["responses"]


def test_append_inbox_note_documents_503_inbox_lock_timeout(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    op = schema["paths"]["/api/v1/inbox/notes/append"]["post"]
    assert "503" in op["responses"]
    assert ErrorCode.INBOX_LOCK_TIMEOUT.value in op["responses"]["503"]["description"]


def test_every_reachable_error_code_appears_on_some_operation(client: TestClient) -> None:
    """Drift guard: every ``ErrorCode`` a ``GatewayError`` subclass can
    actually raise (i.e. everything except the never-raised RATE_LIMITED —
    see app/exceptions.py's own docstring) must be named in at least one
    operation's response ``description``. Catches app/openapi_responses.py's
    per-route mapping silently falling out of sync with a new or renamed
    ``ErrorCode`` — this test only checks presence, not which specific route
    documents which code, so it survives reshuffling responses between
    operations without needing an update itself.
    """
    schema = client.get("/openapi.json").json()
    all_descriptions = " ".join(
        response.get("description", "")
        for methods in schema["paths"].values()
        for op in methods.values()
        for response in op["responses"].values()
    )
    for code in ErrorCode:
        if code in UNREACHABLE_ERROR_CODES:
            continue
        assert code.value in all_descriptions, f"{code.value} is not documented on any operation"
