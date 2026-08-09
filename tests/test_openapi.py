"""IMPLEMENTATION_PLAN section 12: a stable OpenAPI contract for the REST API.

REST is health-only now (docs/adr/0010-*.md) — kept for `docker healthcheck`,
Caddy, and curl-based diagnostics against `GET /api/v1/health`, not as a
general-purpose API surface. The drift guard this module cares about most is
that the surface stays exactly that one operation (test_rest_surface_is_
exactly_health below); everything else pins the still-applicable parts of
the error-envelope contract.
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


def test_rest_surface_is_exactly_health(client: TestClient) -> None:
    """The REST surface-reduction contract (docs/adr/0010-*.md): every other
    REST route, and the bearer-auth security scheme they used to require,
    is gone — not just individually undocumented. A future PR that quietly
    re-adds a REST route fails here first, before anyone notices the wider
    ``ErrorCode`` coverage this module used to guard also silently
    regressed (that guard's replacement is this test, not a like-for-like
    successor — see this module's docstring).
    """
    schema = client.get("/openapi.json").json()
    assert set(schema["paths"]) == {"/api/v1/health"}
    assert "securitySchemes" not in schema.get("components", {})


def test_committed_openapi_json_matches_the_app() -> None:
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "export_openapi.py"), "--check"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_health_has_no_security_requirement(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    health_op = schema["paths"]["/api/v1/health"]["get"]
    assert not health_op.get("security")


def test_committed_openapi_json_file_exists_and_is_valid_json() -> None:
    path = REPO_ROOT / "openapi.json"
    assert path.exists()
    json.loads(path.read_text(encoding="utf-8"))


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


def test_health_documents_no_401(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    health_op = schema["paths"]["/api/v1/health"]["get"]
    assert "401" not in health_op["responses"]
