"""The single failing-response envelope (IMPLEMENTATION_PLAN section 13):
every non-2xx REST response is ``{"error": {"code": ..., "message": ...}}`` —
the exact contract app/openapi_responses.py's ``"default"`` response
documents on every operation, which is what lets that module suppress
FastAPI's own phantom ``422``/``HTTPValidationError`` instead of merely
hiding it.

REST is health-only now (docs/adr/0010-*.md), so most of the status codes
this contract once covered are reachable only via ``handle_gateway_error``
directly (there is no route left that can raise, say, ``PATH_OUTSIDE_VAULT``)
— MCP's equivalent error conversion is exercised end-to-end in
tests/test_mcp_tools.py's "error conversion" section instead.
``handle_gateway_error``/``handle_validation_error`` are still called
directly here because app/main.py deliberately keeps them registered as a
safety net for any REST route added later (docs/adr/0010-*.md) — this pins
that the envelope contract still holds for a raw ``GatewayError``/
``RequestValidationError``, independent of any specific route existing to
raise one today.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient


def _assert_envelope(response) -> None:
    assert response.headers["content-type"].startswith("application/json")
    body = response.json()
    assert set(body.keys()) == {"error"}
    assert set(body["error"].keys()) == {"code", "message"}
    assert isinstance(body["error"]["code"], str) and body["error"]["code"]
    assert isinstance(body["error"]["message"], str) and body["error"]["message"]


def _decode(response) -> dict:
    return json.loads(response.body)


def test_401_missing_token_still_uses_the_envelope(env: None) -> None:
    # No REST route raises UnauthorizedError any more (health takes no
    # token) — pin the envelope directly against the handler instead. `env`
    # is required directly because importing app.main for the first time in
    # a process runs its module-level get_settings() call.
    from app.exceptions import UnauthorizedError
    from app.main import handle_gateway_error

    response = asyncio.run(handle_gateway_error(None, UnauthorizedError()))
    assert response.status_code == 401
    body = _decode(response)
    assert set(body.keys()) == {"error"}
    assert body["error"]["code"] == "UNAUTHORIZED"


def test_400_validation_error_still_uses_the_envelope(env: None) -> None:
    from app.main import handle_validation_error

    response = asyncio.run(handle_validation_error(None, RequestValidationError([])))
    assert response.status_code == 400
    body = _decode(response)
    assert body["error"]["code"] == "VALIDATION_ERROR"


def test_404_unmatched_route_still_uses_the_envelope(client: TestClient) -> None:
    # Not a GatewayError at all — a route that matches nothing, converted by
    # app/main.py's handle_http_exception (StarletteHTTPException), not
    # handle_gateway_error. The envelope must hold here too.
    response = client.get("/api/v1/does-not-exist")
    assert response.status_code == 404
    _assert_envelope(response)
    assert response.json()["error"]["code"] == "NOTE_NOT_FOUND"


def test_405_method_not_allowed_still_uses_the_envelope(client: TestClient) -> None:
    response = client.request("DELETE", "/api/v1/health")
    assert response.status_code == 405
    _assert_envelope(response)
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"


def test_500_unhandled_exception_in_a_request_handler(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app import application as application_module

    def failing_access(*_args: object, **_kwargs: object):
        raise RuntimeError("boom")

    monkeypatch.setattr(application_module.os, "access", failing_access)

    response = client.get("/api/v1/health")
    assert response.status_code == 500
    _assert_envelope(response)
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    assert response.json()["error"]["message"] == "An internal error occurred."


def test_500_unhandled_exception_from_a_different_call_path(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A different call path and a bare OSError (not a GatewayError at all,
    # unlike the RuntimeError above) — patching GatewayApplication.health
    # itself, rather than a helper it calls, to confirm the envelope — and
    # the no-internal-detail rule below — hold regardless of which layer or
    # depth the unexpected exception comes from.
    from app.application import GatewayApplication

    def failing_health(*_args: object, **_kwargs: object):
        raise OSError(5, "simulated I/O error")

    monkeypatch.setattr(GatewayApplication, "health", failing_health)

    response = client.get("/api/v1/health")
    assert response.status_code == 500
    _assert_envelope(response)
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    # AGENTS.md: never expose internal detail (exception type/message, host
    # paths) in anything client-visible — only the server log gets that.
    message = response.json()["error"]["message"]
    assert "OSError" not in message
    assert "/" not in message
