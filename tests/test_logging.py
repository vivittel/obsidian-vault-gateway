"""IMPLEMENTATION_PLAN section 14: what must and must not appear in logs.

REST is health-only now (docs/adr/0010-*.md); the secret-hygiene checks that
used to run through REST's search/create/append/notes routes (bearer token,
query value, note content, note path, absolute vault path, append content)
all have MCP-transport equivalents in tests/test_mcp_tools.py's "logging"
section, which stayed unchanged.
"""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from app.application import GatewayApplication


@pytest.fixture(autouse=True)
def _capture_access_log(caplog: pytest.LogCaptureFixture):
    caplog.set_level(logging.INFO, logger="obsidian_gateway.access")
    caplog.set_level(logging.INFO, logger="obsidian_gateway")


def test_unhandled_exception_is_still_logged_as_status_500(
    client: TestClient, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Before this fix, an exception app/main.py's bare-Exception handler
    converts to a 500 never reached AccessLogMiddleware at all: Starlette
    installs that handler on the outermost ServerErrorMiddleware, entirely
    outside every piece of user middleware (see app/middleware.py's
    AccessLogMiddleware docstring), so the request left no access log line
    whatsoever — the one case (an unhandled 500) most worth one.
    """

    # /api/v1/health logs its access line at DEBUG (see
    # app/middleware.py's _HEALTH_ROUTE downgrade) — the autouse fixture
    # above only captures INFO+, so this overrides it to DEBUG for this one
    # test; the request/response behaviour under test is unaffected either
    # way.
    caplog.set_level(logging.DEBUG, logger="obsidian_gateway.access")

    def failing_health(*_args: object, **_kwargs: object):
        raise RuntimeError("boom")

    monkeypatch.setattr(GatewayApplication, "health", failing_health)

    response = client.get("/api/v1/health")
    assert response.status_code == 500

    access_records = [r for r in caplog.records if r.name == "obsidian_gateway.access"]
    assert len(access_records) == 1
    assert access_records[0].status_code == 500
    assert access_records[0].route == "/api/v1/health"
