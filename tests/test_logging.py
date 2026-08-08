"""IMPLEMENTATION_PLAN section 14: what must and must not appear in logs."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _capture_access_log(caplog: pytest.LogCaptureFixture):
    caplog.set_level(logging.INFO, logger="obsidian_gateway.access")
    caplog.set_level(logging.INFO, logger="obsidian_gateway")


def test_bearer_token_never_logged(
    client: TestClient,
    auth_headers: dict[str, str],
    api_token: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    client.get("/api/v1/search", params={"q": "anything"}, headers=auth_headers)
    for record in caplog.records:
        assert api_token not in record.getMessage()
        assert api_token not in str(record.__dict__)


def test_search_query_value_never_logged_only_length(
    client: TestClient, auth_headers: dict[str, str], caplog: pytest.LogCaptureFixture
) -> None:
    secret_query = "very-specific-search-term-xyz"
    client.get("/api/v1/search", params={"q": secret_query}, headers=auth_headers)

    for record in caplog.records:
        assert secret_query not in record.getMessage()
        assert secret_query not in str(record.__dict__)

    access_records = [r for r in caplog.records if r.name == "obsidian_gateway.access"]
    assert access_records
    assert getattr(access_records[-1], "query_length", None) == len(secret_query)


def test_note_content_never_logged_on_create(
    client: TestClient, auth_headers: dict[str, str], caplog: pytest.LogCaptureFixture
) -> None:
    secret_content = "extremely sensitive body text that must not leak"
    client.post(
        "/api/v1/inbox/notes",
        json={"title": "log test", "content": secret_content},
        headers=auth_headers,
    )
    for record in caplog.records:
        assert secret_content not in record.getMessage()
        assert secret_content not in str(record.__dict__)


def test_created_note_relative_path_is_logged(
    client: TestClient, auth_headers: dict[str, str], caplog: pytest.LogCaptureFixture
) -> None:
    response = client.post(
        "/api/v1/inbox/notes",
        json={"title": "Logged Note", "content": "x\n"},
        headers=auth_headers,
    )
    created_path = response.json()["path"]

    access_records = [r for r in caplog.records if r.name == "obsidian_gateway.access"]
    assert any(getattr(r, "note_path", None) == created_path for r in access_records)


def test_read_note_relative_path_is_logged(
    client: TestClient, auth_headers: dict[str, str], caplog: pytest.LogCaptureFixture
) -> None:
    client.get(
        "/api/v1/notes",
        params={"path": "Knowledge/PC/GPU/RTX 5070.md"},
        headers=auth_headers,
    )
    access_records = [r for r in caplog.records if r.name == "obsidian_gateway.access"]
    assert any(
        getattr(r, "note_path", None) == "Knowledge/PC/GPU/RTX 5070.md" for r in access_records
    )


def test_absolute_vault_path_never_logged(
    client: TestClient,
    auth_headers: dict[str, str],
    caplog: pytest.LogCaptureFixture,
    vault_root,
) -> None:
    client.get(
        "/api/v1/notes",
        params={"path": "Knowledge/PC/GPU/RTX 5070.md"},
        headers=auth_headers,
    )
    for record in caplog.records:
        assert str(vault_root) not in record.getMessage()


def test_append_content_never_logged(
    client: TestClient,
    auth_headers: dict[str, str],
    caplog: pytest.LogCaptureFixture,
    inbox_root: Path,
) -> None:
    secret_content = "extremely sensitive appended text that must not leak"
    (inbox_root / "LogAppendTarget.md").write_text("x\n", encoding="utf-8")
    client.post(
        "/api/v1/inbox/notes/append",
        json={"path": "00_Inbox/ChatGPT/LogAppendTarget.md", "content": secret_content},
        headers=auth_headers,
    )
    for record in caplog.records:
        assert secret_content not in record.getMessage()
        assert secret_content not in str(record.__dict__)


def test_appended_note_relative_path_is_logged(
    client: TestClient,
    auth_headers: dict[str, str],
    caplog: pytest.LogCaptureFixture,
    inbox_root: Path,
) -> None:
    (inbox_root / "LogAppendPath.md").write_text("x\n", encoding="utf-8")
    response = client.post(
        "/api/v1/inbox/notes/append",
        json={"path": "00_Inbox/ChatGPT/LogAppendPath.md", "content": "y\n"},
        headers=auth_headers,
    )
    appended_path = response.json()["path"]

    access_records = [r for r in caplog.records if r.name == "obsidian_gateway.access"]
    assert any(getattr(r, "note_path", None) == appended_path for r in access_records)


def test_oversized_request_is_logged_once(
    client: TestClient, auth_headers: dict[str, str], caplog: pytest.LogCaptureFixture
) -> None:
    """Before this fix, RequestSizeLimitMiddleware was registered outermost
    (Starlette's add_middleware prepends, so the last-registered middleware
    wraps everything else), so its 413 short-circuit never reached
    AccessLogMiddleware at all — a rejected oversized request left no access
    log line whatsoever.
    """
    from app.config import get_settings

    content = "x" * (get_settings().max_request_bytes + 1)
    response = client.post(
        "/api/v1/inbox/notes",
        json={"title": "oversized", "content": content},
        headers=auth_headers,
    )
    assert response.status_code == 413

    access_records = [r for r in caplog.records if r.name == "obsidian_gateway.access"]
    assert len(access_records) == 1
    assert access_records[0].status_code == 413
    assert access_records[0].route == "/api/v1/inbox/notes"


def test_unhandled_exception_is_still_logged_as_status_500(
    client: TestClient,
    auth_headers: dict[str, str],
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Before this fix, an exception app/main.py's bare-Exception handler
    converts to a 500 never reached AccessLogMiddleware at all: Starlette
    installs that handler on the outermost ServerErrorMiddleware, entirely
    outside every piece of user middleware (see app/middleware.py's
    AccessLogMiddleware docstring), so the request left no access log line
    whatsoever — the one case (an unhandled 500) most worth one.
    """
    from app import application as application_module

    def failing_summarise(*_args: object, **_kwargs: object):
        raise RuntimeError("boom")

    monkeypatch.setattr(application_module, "summarise_vault", failing_summarise)

    response = client.get("/api/v1/vault/summary", headers=auth_headers)
    assert response.status_code == 500

    access_records = [r for r in caplog.records if r.name == "obsidian_gateway.access"]
    assert len(access_records) == 1
    assert access_records[0].status_code == 500
    assert access_records[0].route == "/api/v1/vault/summary"
