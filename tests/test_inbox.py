"""Inbox creation tests. All writes go into the tmp_path vault from conftest —
never a real vault (AGENTS.md).
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi.testclient import TestClient


def test_create_note_minimal(
    client: TestClient, auth_headers: dict[str, str], inbox_root: Path
) -> None:
    response = client.post(
        "/api/v1/inbox/notes",
        json={"title": "Gateway smoke test", "content": "# Gateway smoke test\n"},
        headers=auth_headers,
    )
    assert response.status_code == 201
    body = response.json()
    assert body["path"] == "00_Inbox/ChatGPT/Gateway smoke test.md"
    assert body["id"] == body["path"]
    assert (inbox_root / "Gateway smoke test.md").read_text(encoding="utf-8") == (
        "# Gateway smoke test\n"
    )


def test_create_note_with_frontmatter(
    client: TestClient, auth_headers: dict[str, str], inbox_root: Path
) -> None:
    response = client.post(
        "/api/v1/inbox/notes",
        json={
            "title": "ChatGPTとObsidian Vaultの連携",
            "content": "# ChatGPTとObsidian Vaultの連携\n\n本文。\n",
            "frontmatter": {"tags": ["chatgpt", "obsidian"], "source": "chatgpt"},
        },
        headers=auth_headers,
    )
    assert response.status_code == 201
    written = (inbox_root / "ChatGPTとObsidian Vaultの連携.md").read_text(encoding="utf-8")
    assert written.startswith("---\n")
    assert "tags:" in written
    assert "source: chatgpt" in written
    assert "# ChatGPTとObsidian Vaultの連携" in written


def test_create_note_does_not_overwrite_existing(
    client: TestClient, auth_headers: dict[str, str], inbox_root: Path
) -> None:
    (inbox_root / "Duplicate.md").write_text("original\n", encoding="utf-8")

    response = client.post(
        "/api/v1/inbox/notes",
        json={"title": "Duplicate", "content": "new content\n"},
        headers=auth_headers,
    )
    assert response.status_code == 201
    assert response.json()["path"] == "00_Inbox/ChatGPT/Duplicate-2.md"
    assert (inbox_root / "Duplicate.md").read_text(encoding="utf-8") == "original\n"
    assert (inbox_root / "Duplicate-2.md").read_text(encoding="utf-8") == "new content\n"


def test_create_note_sequence_numbers_increment(
    client: TestClient, auth_headers: dict[str, str], inbox_root: Path
) -> None:
    for _ in range(3):
        response = client.post(
            "/api/v1/inbox/notes",
            json={"title": "Repeat", "content": "x\n"},
            headers=auth_headers,
        )
        assert response.status_code == 201

    names = sorted(p.name for p in inbox_root.glob("Repeat*.md"))
    assert names == ["Repeat-2.md", "Repeat-3.md", "Repeat.md"]


def test_create_note_leaves_no_temp_files_behind(
    client: TestClient, auth_headers: dict[str, str], inbox_root: Path
) -> None:
    response = client.post(
        "/api/v1/inbox/notes",
        json={"title": "No leftovers", "content": "x\n"},
        headers=auth_headers,
    )
    assert response.status_code == 201
    leftovers = [p for p in inbox_root.iterdir() if p.name.startswith(".tmp-")]
    assert leftovers == []


def test_create_note_sanitises_forbidden_filename_characters(
    client: TestClient, auth_headers: dict[str, str], inbox_root: Path
) -> None:
    response = client.post(
        "/api/v1/inbox/notes",
        json={"title": "a/b:c*d", "content": "x\n"},
        headers=auth_headers,
    )
    assert response.status_code == 201
    assert response.json()["path"] == "00_Inbox/ChatGPT/a-b-c-d.md"


def test_create_note_rejects_reserved_windows_name(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.post(
        "/api/v1/inbox/notes", json={"title": "CON", "content": "x\n"}, headers=auth_headers
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_TITLE"


def test_create_note_rejects_empty_title_after_sanitising(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.post(
        "/api/v1/inbox/notes", json={"title": "...", "content": "x\n"}, headers=auth_headers
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_TITLE"


def test_create_note_rejects_unknown_body_fields(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.post(
        "/api/v1/inbox/notes",
        json={"title": "x", "content": "y\n", "path": "../escape.md"},
        headers=auth_headers,
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_create_note_rejects_nested_frontmatter_structures(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.post(
        "/api/v1/inbox/notes",
        json={
            "title": "x",
            "content": "y\n",
            "frontmatter": {"nested": {"a": 1}},
        },
        headers=auth_headers,
    )
    assert response.status_code == 400


def test_created_note_is_readable_via_get_notes(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    create_response = client.post(
        "/api/v1/inbox/notes",
        json={"title": "Round trip", "content": "# Round trip\n"},
        headers=auth_headers,
    )
    path = create_response.json()["path"]

    read_response = client.get("/api/v1/notes", params={"path": path}, headers=auth_headers)
    assert read_response.status_code == 200
    assert "# Round trip" in read_response.json()["content"]


def test_create_note_only_writes_inside_inbox_root(
    client: TestClient, auth_headers: dict[str, str], inbox_root: Path
) -> None:
    before = set(os.listdir(inbox_root.parent))
    client.post(
        "/api/v1/inbox/notes",
        json={"title": "Contained", "content": "x\n"},
        headers=auth_headers,
    )
    after = set(os.listdir(inbox_root.parent))
    assert before == after  # nothing written to the vault root, only inside 00_Inbox/ChatGPT


def test_create_note_over_size_limit_returns_413(
    client: TestClient, auth_headers: dict[str, str], monkeypatch
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
        assert response.json()["error"]["code"] == "FILE_TOO_LARGE"
    finally:
        get_settings.cache_clear()
