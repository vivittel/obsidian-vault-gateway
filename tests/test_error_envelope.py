"""The single failing-response envelope (IMPLEMENTATION_PLAN section 13):
every non-2xx REST response is ``{"error": {"code": ..., "message": ...}}`` —
the exact contract app/openapi_responses.py's ``"default"`` response
documents on every operation, which is what lets that module suppress
FastAPI's own phantom ``422``/``HTTPValidationError`` instead of merely
hiding it.

One scenario per status code this API can actually return (see
app/openapi_responses.py's per-route mapping), asserting the envelope's
*exact* shape rather than only that an ``"error"`` key exists somewhere — a
future handler that adds an extra top-level field, or a status this app
doesn't know about, fails loudly here. Includes both routes through a
registered ``GatewayError`` and the two paths that are not one at all
(an unmatched route, a wrong HTTP method) and, deliberately twice from two
different call paths, an entirely unhandled exception — the case
app/middleware.py's ``AccessLogMiddleware`` fix and this envelope contract
both exist to keep correct.
"""

from __future__ import annotations

import multiprocessing
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def _assert_envelope(response) -> None:
    assert response.headers["content-type"].startswith("application/json")
    body = response.json()
    assert set(body.keys()) == {"error"}
    assert set(body["error"].keys()) == {"code", "message"}
    assert isinstance(body["error"]["code"], str) and body["error"]["code"]
    assert isinstance(body["error"]["message"], str) and body["error"]["message"]


def test_400_invalid_path(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.get("/api/v1/notes", params={"path": "../secret.md"}, headers=auth_headers)
    assert response.status_code == 400
    _assert_envelope(response)
    assert response.json()["error"]["code"] == "INVALID_PATH"


def test_400_validation_error_from_request_validation(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.get("/api/v1/search", params={"limit": 999}, headers=auth_headers)
    assert response.status_code == 400
    _assert_envelope(response)
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_401_missing_token(client: TestClient) -> None:
    response = client.get("/api/v1/search")
    assert response.status_code == 401
    _assert_envelope(response)
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


def test_403_path_outside_vault(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.post(
        "/api/v1/inbox/notes/append",
        json={"path": "Knowledge/no_frontmatter.md", "content": "x"},
        headers=auth_headers,
    )
    assert response.status_code == 403
    _assert_envelope(response)
    assert response.json()["error"]["code"] == "PATH_OUTSIDE_VAULT"


def test_404_note_not_found(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.get(
        "/api/v1/notes", params={"path": "Knowledge/does-not-exist.md"}, headers=auth_headers
    )
    assert response.status_code == 404
    _assert_envelope(response)
    assert response.json()["error"]["code"] == "NOTE_NOT_FOUND"


def test_404_unmatched_route_still_uses_the_envelope(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    # Not a GatewayError at all — a route that matches nothing, converted by
    # app/main.py's handle_http_exception (StarletteHTTPException), not
    # handle_gateway_error. The envelope must hold here too.
    response = client.get("/api/v1/does-not-exist", headers=auth_headers)
    assert response.status_code == 404
    _assert_envelope(response)
    assert response.json()["error"]["code"] == "NOTE_NOT_FOUND"


def test_405_method_not_allowed_still_uses_the_envelope(client: TestClient) -> None:
    response = client.request("DELETE", "/api/v1/search")
    assert response.status_code == 405
    _assert_envelope(response)
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"


def test_409_note_already_exists(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import inbox_service

    def always_exists(*_args: object, **_kwargs: object):
        raise FileExistsError

    monkeypatch.setattr(inbox_service.os, "link", always_exists)
    monkeypatch.setattr(inbox_service, "MAX_SEQUENCE_ATTEMPTS", 1)

    response = client.post(
        "/api/v1/inbox/notes",
        json={"title": "Collision", "content": "x"},
        headers=auth_headers,
    )
    assert response.status_code == 409
    _assert_envelope(response)
    assert response.json()["error"]["code"] == "NOTE_ALREADY_EXISTS"


def test_413_oversized_body(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.config import get_settings

    monkeypatch.setenv("MAX_REQUEST_BYTES", "1024")
    get_settings.cache_clear()
    try:
        response = client.post(
            "/api/v1/inbox/notes",
            json={"title": "big", "content": "x" * 1000},
            headers=auth_headers,
        )
        assert response.status_code == 413
        _assert_envelope(response)
        assert response.json()["error"]["code"] == "FILE_TOO_LARGE"
    finally:
        get_settings.cache_clear()


def test_503_inbox_lock_timeout(
    client: TestClient,
    auth_headers: dict[str, str],
    inbox_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    hold_flock_in_subprocess,
) -> None:
    from app.services import inbox_service

    monkeypatch.setattr(inbox_service, "_LOCK_TIMEOUT_SECONDS", 0.3)
    monkeypatch.setattr(inbox_service, "_LOCK_POLL_INTERVAL_SECONDS", 0.02)

    note = inbox_root / "Locked.md"
    note.write_text("original\n", encoding="utf-8")
    lock_path = str(inbox_root / ".append.lock")

    ctx = multiprocessing.get_context("spawn")
    acquired = ctx.Event()
    holder = ctx.Process(target=hold_flock_in_subprocess, args=(lock_path, 5.0, acquired))
    holder.start()
    try:
        assert acquired.wait(timeout=5), "holder process never acquired the lock"
        response = client.post(
            "/api/v1/inbox/notes/append",
            json={"path": "00_Inbox/ChatGPT/Locked.md", "content": "x"},
            headers=auth_headers,
        )
    finally:
        holder.terminate()
        holder.join(timeout=5)

    assert response.status_code == 503
    _assert_envelope(response)
    assert response.json()["error"]["code"] == "INBOX_LOCK_TIMEOUT"


def test_500_unhandled_exception_in_a_request_handler(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app import application as application_module

    def failing_summarise(*_args: object, **_kwargs: object):
        raise RuntimeError("boom")

    monkeypatch.setattr(application_module, "summarise_vault", failing_summarise)

    response = client.get("/api/v1/vault/summary", headers=auth_headers)
    assert response.status_code == 500
    _assert_envelope(response)
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    assert response.json()["error"]["message"] == "An internal error occurred."


def test_500_unhandled_exception_from_a_different_call_path(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # A different endpoint and a bare OSError from the filesystem layer (not
    # a GatewayError at all, unlike the RuntimeError above), to confirm the
    # envelope — and the no-internal-detail rule below — hold regardless of
    # which layer or route the unexpected exception comes from.
    from app.services import note_service

    def failing_read_note(*_args: object, **_kwargs: object):
        raise OSError(5, "simulated I/O error")

    monkeypatch.setattr(note_service, "read_note", failing_read_note)

    response = client.get(
        "/api/v1/notes", params={"path": "Knowledge/no_frontmatter.md"}, headers=auth_headers
    )
    assert response.status_code == 500
    _assert_envelope(response)
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    # AGENTS.md: never expose internal detail (exception type/message, host
    # paths) in anything client-visible — only the server log gets that.
    message = response.json()["error"]["message"]
    assert "OSError" not in message
    assert "/" not in message
