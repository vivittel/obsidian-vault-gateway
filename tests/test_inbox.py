"""Inbox creation and append tests. All writes go into the tmp_path vault
from conftest — never a real vault (AGENTS.md).
"""

from __future__ import annotations

import errno
import multiprocessing
import os
import stat
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
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


def test_create_note_mode_is_0o644(
    client: TestClient, auth_headers: dict[str, str], inbox_root: Path
) -> None:
    # Access-control policy (app/services/inbox_service.py's
    # _CREATED_NOTE_MODE), not an incidental default: the vault is a bind
    # mount shared with the host's own Obsidian, and every other note in it
    # is ordinarily 0o644.
    response = client.post(
        "/api/v1/inbox/notes",
        json={"title": "Mode Check", "content": "x\n"},
        headers=auth_headers,
    )
    assert response.status_code == 201
    assert stat.S_IMODE((inbox_root / "Mode Check.md").stat().st_mode) == 0o644


def test_create_note_mode_is_0o644_even_under_a_restrictive_umask(
    client: TestClient, auth_headers: dict[str, str], inbox_root: Path
) -> None:
    # os.fchmod (app/services/inbox_service.py's _write_temp_bytes) sets the
    # mode explicitly, so the process umask must never affect it — unlike a
    # plain os.open(..., mode) create, which the umask does modify.
    old_umask = os.umask(0o077)
    try:
        response = client.post(
            "/api/v1/inbox/notes",
            json={"title": "Umask Check", "content": "x\n"},
            headers=auth_headers,
        )
    finally:
        os.umask(old_umask)
    assert response.status_code == 201
    assert stat.S_IMODE((inbox_root / "Umask Check.md").stat().st_mode) == 0o644


def test_create_note_conflict_does_not_change_the_existing_files_mode(
    client: TestClient, auth_headers: dict[str, str], inbox_root: Path
) -> None:
    existing = inbox_root / "Duplicate.md"
    existing.write_text("original\n", encoding="utf-8")
    existing.chmod(0o600)

    response = client.post(
        "/api/v1/inbox/notes",
        json={"title": "Duplicate", "content": "new content\n"},
        headers=auth_headers,
    )
    assert response.status_code == 201
    assert stat.S_IMODE(existing.stat().st_mode) == 0o600
    assert stat.S_IMODE((inbox_root / "Duplicate-2.md").stat().st_mode) == 0o644


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


def test_create_note_with_empty_content_is_still_accepted(
    client: TestClient, auth_headers: dict[str, str], inbox_root: Path
) -> None:
    # `content` became `str | None` so `export` could be introduced; the
    # sentinel for "not provided" is `is None`, not falsiness, so an explicit
    # empty string must remain valid exactly as before.
    response = client.post(
        "/api/v1/inbox/notes",
        json={"title": "Empty content still valid", "content": ""},
        headers=auth_headers,
    )
    assert response.status_code == 201
    assert (inbox_root / "Empty content still valid.md").read_text(encoding="utf-8") == ""


def test_create_note_with_structured_export(
    client: TestClient, auth_headers: dict[str, str], inbox_root: Path
) -> None:
    response = client.post(
        "/api/v1/inbox/notes",
        json={
            "title": "Structured export test",
            "export": {"mode": "summary", "tldr": ["ok"], "decisions": ["d1"]},
        },
        headers=auth_headers,
    )
    assert response.status_code == 201
    written = (inbox_root / "Structured export test.md").read_text(encoding="utf-8")
    assert "export_mode: summary" in written
    assert "## 決定事項" in written
    assert "- d1" in written


def test_create_note_with_related_notes(
    client: TestClient, auth_headers: dict[str, str], inbox_root: Path
) -> None:
    response = client.post(
        "/api/v1/inbox/notes",
        json={
            "title": "Related notes REST test",
            "export": {
                "tldr": ["ok"],
                "related_notes": ["Knowledge/PC/GPU/RTX 5070.md", "Knowledge/missing.md"],
            },
        },
        headers=auth_headers,
    )
    assert response.status_code == 201
    body = response.json()
    assert body["related_notes_linked"] == 1
    assert body["related_notes_skipped"] == 1
    written = (inbox_root / "Related notes REST test.md").read_text(encoding="utf-8")
    assert "## 関連ノート\n\n- [[Knowledge/PC/GPU/RTX 5070]]" in written


def test_create_note_with_oversized_related_note_candidate_does_not_block_export(
    client: TestClient, auth_headers: dict[str, str], inbox_root: Path
) -> None:
    oversized = "Knowledge/" + "a" * 1020 + ".md"
    response = client.post(
        "/api/v1/inbox/notes",
        json={
            "title": "Oversized related note REST test",
            "export": {
                "tldr": ["ok"],
                "related_notes": [oversized, "Knowledge/PC/GPU/RTX 5070.md"],
            },
        },
        headers=auth_headers,
    )
    assert response.status_code == 201
    body = response.json()
    assert body["related_notes_linked"] == 1
    assert body["related_notes_skipped"] == 1
    written = (inbox_root / "Oversized related note REST test.md").read_text(encoding="utf-8")
    assert "## 関連ノート\n\n- [[Knowledge/PC/GPU/RTX 5070]]" in written


def test_create_note_structured_export_defaults_to_summary(
    client: TestClient, auth_headers: dict[str, str], inbox_root: Path
) -> None:
    response = client.post(
        "/api/v1/inbox/notes",
        json={"title": "Default mode via REST", "export": {"tldr": ["ok"]}},
        headers=auth_headers,
    )
    assert response.status_code == 201
    written = (inbox_root / "Default mode via REST.md").read_text(encoding="utf-8")
    assert "export_mode: summary" in written


def test_create_note_rejects_neither_content_nor_export(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.post(
        "/api/v1/inbox/notes",
        json={"title": "x"},
        headers=auth_headers,
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_create_note_rejects_both_content_and_export(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.post(
        "/api/v1/inbox/notes",
        json={"title": "x", "content": "y\n", "export": {"tldr": ["z"]}},
        headers=auth_headers,
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_create_note_rejects_export_combined_with_frontmatter(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.post(
        "/api/v1/inbox/notes",
        json={
            "title": "x",
            "export": {"tldr": ["z"]},
            "frontmatter": {"source": "not-chatgpt"},
        },
        headers=auth_headers,
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


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


def test_create_note_over_size_limit_returns_413_even_with_no_content_length(
    client: TestClient, auth_headers: dict[str, str], monkeypatch, inbox_root
) -> None:
    """The same oversized body as the test above, but sent chunked (no
    ``Content-Length`` header at all) — RequestSizeLimitMiddleware's
    declared-size fast path cannot see this one at all, so this pins the
    cumulative-body check that closes it. Regression for the bug where a
    chunked request bypassed MAX_REQUEST_BYTES entirely and wrote an
    over-cap note to the inbox.
    """
    from app.config import get_settings

    monkeypatch.setenv("MAX_REQUEST_BYTES", "1024")
    get_settings.cache_clear()
    try:

        def body_chunks():
            yield b'{"title":"big","content":"'
            yield b"x" * 1000
            yield b'"}'

        response = client.post(
            "/api/v1/inbox/notes",
            headers={**auth_headers, "Content-Type": "application/json"},
            content=body_chunks(),
        )
        assert response.status_code == 413
        assert response.json()["error"]["code"] == "FILE_TOO_LARGE"
        assert [p.name for p in inbox_root.iterdir() if p.name != ".gitkeep"] == []
    finally:
        get_settings.cache_clear()


# --- POST /api/v1/inbox/notes/append ---------------------------------------

REJECTED_APPEND_PATHS = [
    "../secret.md",
    "../../.obsidian/config",
    "%2e%2e%2fsecret.md",
    "%252e%252e%252fsecret.md",
    "..\\secret.md",
    "/vault/secret.md",
    "C:\\secret.md",
    "00_Inbox/ChatGPT/.hidden.md",
    "not_markdown.txt",
]


def test_append_appends_without_overwriting_lf(
    client: TestClient, auth_headers: dict[str, str], inbox_root: Path
) -> None:
    (inbox_root / "Note.md").write_text("first\n", encoding="utf-8")
    response = client.post(
        "/api/v1/inbox/notes/append",
        json={"path": "00_Inbox/ChatGPT/Note.md", "content": "second\n"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["path"] == "00_Inbox/ChatGPT/Note.md"
    assert body["id"] == body["path"]
    assert (inbox_root / "Note.md").read_text(encoding="utf-8") == "first\nsecond\n"
    assert body["appended_bytes"] == len(b"second\n")


def test_append_preserves_crlf_line_endings(
    client: TestClient, auth_headers: dict[str, str], inbox_root: Path
) -> None:
    (inbox_root / "Crlf.md").write_bytes(b"first\r\n")
    response = client.post(
        "/api/v1/inbox/notes/append",
        json={"path": "00_Inbox/ChatGPT/Crlf.md", "content": "second\n"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert (inbox_root / "Crlf.md").read_bytes() == b"first\r\nsecond\r\n"


def test_append_inserts_separating_newline_when_missing(
    client: TestClient, auth_headers: dict[str, str], inbox_root: Path
) -> None:
    (inbox_root / "NoTrailingNewline.md").write_bytes(b"first")
    response = client.post(
        "/api/v1/inbox/notes/append",
        json={"path": "00_Inbox/ChatGPT/NoTrailingNewline.md", "content": "second\n"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert (inbox_root / "NoTrailingNewline.md").read_bytes() == b"first\nsecond\n"


def test_append_to_empty_note_needs_no_separator(
    client: TestClient, auth_headers: dict[str, str], inbox_root: Path
) -> None:
    (inbox_root / "Empty.md").write_bytes(b"")
    response = client.post(
        "/api/v1/inbox/notes/append",
        json={"path": "00_Inbox/ChatGPT/Empty.md", "content": "first\n"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert (inbox_root / "Empty.md").read_bytes() == b"first\n"
    assert response.json()["appended_bytes"] == len(b"first\n")


def test_appended_bytes_equals_the_actual_file_size_increase(
    client: TestClient, auth_headers: dict[str, str], inbox_root: Path
) -> None:
    note = inbox_root / "SizeDelta.md"
    note.write_text("first", encoding="utf-8")  # no trailing newline
    before_size = note.stat().st_size

    response = client.post(
        "/api/v1/inbox/notes/append",
        json={"path": "00_Inbox/ChatGPT/SizeDelta.md", "content": "second"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    after_size = note.stat().st_size
    assert response.json()["appended_bytes"] == after_size - before_size


def test_append_preserves_file_mode(
    client: TestClient, auth_headers: dict[str, str], inbox_root: Path
) -> None:
    note = inbox_root / "ModeCheck.md"
    note.write_text("first\n", encoding="utf-8")
    note.chmod(0o640)

    response = client.post(
        "/api/v1/inbox/notes/append",
        json={"path": "00_Inbox/ChatGPT/ModeCheck.md", "content": "second\n"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert stat.S_IMODE(note.stat().st_mode) == 0o640


def test_append_preserves_a_0o600_file_mode(
    client: TestClient, auth_headers: dict[str, str], inbox_root: Path
) -> None:
    # A distinct mode from test_append_preserves_file_mode's 0o640: append
    # must preserve whatever the target already has, not normalise toward
    # either create's 0o644 policy or any other fixed value.
    note = inbox_root / "StrictModeCheck.md"
    note.write_text("first\n", encoding="utf-8")
    note.chmod(0o600)

    response = client.post(
        "/api/v1/inbox/notes/append",
        json={"path": "00_Inbox/ChatGPT/StrictModeCheck.md", "content": "second\n"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert stat.S_IMODE(note.stat().st_mode) == 0o600


def test_append_leaves_no_temp_files_behind(
    client: TestClient, auth_headers: dict[str, str], inbox_root: Path
) -> None:
    (inbox_root / "Clean.md").write_text("x\n", encoding="utf-8")
    response = client.post(
        "/api/v1/inbox/notes/append",
        json={"path": "00_Inbox/ChatGPT/Clean.md", "content": "y\n"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    leftovers = [p for p in inbox_root.iterdir() if p.name.startswith(".tmp-")]
    assert leftovers == []


def test_append_only_writes_inside_inbox_root(
    client: TestClient, auth_headers: dict[str, str], inbox_root: Path
) -> None:
    (inbox_root / "Contained.md").write_text("x\n", encoding="utf-8")
    before = set(os.listdir(inbox_root.parent))
    client.post(
        "/api/v1/inbox/notes/append",
        json={"path": "00_Inbox/ChatGPT/Contained.md", "content": "y\n"},
        headers=auth_headers,
    )
    after = set(os.listdir(inbox_root.parent))
    assert before == after


def test_append_rejects_empty_content(
    client: TestClient, auth_headers: dict[str, str], inbox_root: Path
) -> None:
    (inbox_root / "Target.md").write_text("x\n", encoding="utf-8")
    response = client.post(
        "/api/v1/inbox/notes/append",
        json={"path": "00_Inbox/ChatGPT/Target.md", "content": ""},
        headers=auth_headers,
    )
    assert response.status_code == 400


def test_append_rejects_whitespace_only_content(
    client: TestClient, auth_headers: dict[str, str], inbox_root: Path
) -> None:
    (inbox_root / "Target.md").write_text("x\n", encoding="utf-8")
    response = client.post(
        "/api/v1/inbox/notes/append",
        json={"path": "00_Inbox/ChatGPT/Target.md", "content": "   \n  "},
        headers=auth_headers,
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_append_rejects_missing_note(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.post(
        "/api/v1/inbox/notes/append",
        json={"path": "00_Inbox/ChatGPT/does-not-exist.md", "content": "x\n"},
        headers=auth_headers,
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOTE_NOT_FOUND"


@pytest.mark.parametrize("raw", REJECTED_APPEND_PATHS)
def test_append_rejects_malicious_or_invalid_paths(
    client: TestClient, auth_headers: dict[str, str], raw: str
) -> None:
    response = client.post(
        "/api/v1/inbox/notes/append",
        json={"path": raw, "content": "x\n"},
        headers=auth_headers,
    )
    assert response.status_code in {400, 403, 404}
    assert "error" in response.json()


def test_append_rejects_path_outside_inbox(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.post(
        "/api/v1/inbox/notes/append",
        json={"path": "Knowledge/no_frontmatter.md", "content": "x\n"},
        headers=auth_headers,
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "PATH_OUTSIDE_VAULT"


def test_append_rejects_subdirectory_of_inbox(
    client: TestClient, auth_headers: dict[str, str], inbox_root: Path
) -> None:
    subdir = inbox_root / "Sub"
    subdir.mkdir()
    (subdir / "Nested.md").write_text("x\n", encoding="utf-8")

    response = client.post(
        "/api/v1/inbox/notes/append",
        json={"path": "00_Inbox/ChatGPT/Sub/Nested.md", "content": "y\n"},
        headers=auth_headers,
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_PATH"


def test_append_rejects_symlinked_note(
    client: TestClient, auth_headers: dict[str, str], inbox_root: Path, vault_root: Path
) -> None:
    outside_target = vault_root.parent / "outside-secret.md"
    outside_target.write_text("secret\n", encoding="utf-8")
    (inbox_root / "Link.md").symlink_to(outside_target)

    response = client.post(
        "/api/v1/inbox/notes/append",
        json={"path": "00_Inbox/ChatGPT/Link.md", "content": "x\n"},
        headers=auth_headers,
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_PATH"
    assert outside_target.read_text(encoding="utf-8") == "secret\n"


def test_append_rejects_already_oversized_note(
    client: TestClient, auth_headers: dict[str, str], inbox_root: Path, monkeypatch
) -> None:
    from app.config import get_settings

    note = inbox_root / "AlreadyBig.md"
    note.write_text("x" * 2000, encoding="utf-8")
    note.chmod(0o640)
    before = note.stat()

    monkeypatch.setenv("MAX_NOTE_SIZE_BYTES", "1024")
    get_settings.cache_clear()
    try:
        response = client.post(
            "/api/v1/inbox/notes/append",
            json={"path": "00_Inbox/ChatGPT/AlreadyBig.md", "content": "y\n"},
            headers=auth_headers,
        )
        assert response.status_code == 413
        assert response.json()["error"]["code"] == "FILE_TOO_LARGE"
        after = note.stat()
        assert note.read_text(encoding="utf-8") == "x" * 2000
        # A rejected append must be a complete no-op on the target: same
        # mode, same inode (never replaced), same mtime (never rewritten).
        assert stat.S_IMODE(after.st_mode) == 0o640
        assert after.st_ino == before.st_ino
        assert after.st_mtime_ns == before.st_mtime_ns
    finally:
        get_settings.cache_clear()


def test_append_rejects_when_result_would_exceed_size_limit(
    client: TestClient, auth_headers: dict[str, str], inbox_root: Path, monkeypatch
) -> None:
    from app.config import get_settings

    (inbox_root / "NearLimit.md").write_text("x" * 900, encoding="utf-8")
    monkeypatch.setenv("MAX_NOTE_SIZE_BYTES", "1024")
    get_settings.cache_clear()
    try:
        response = client.post(
            "/api/v1/inbox/notes/append",
            json={"path": "00_Inbox/ChatGPT/NearLimit.md", "content": "y" * 900},
            headers=auth_headers,
        )
        assert response.status_code == 413
        assert response.json()["error"]["code"] == "FILE_TOO_LARGE"
        assert (inbox_root / "NearLimit.md").read_text(encoding="utf-8") == "x" * 900
    finally:
        get_settings.cache_clear()


def test_append_lock_file_being_a_symlink_is_rejected_and_target_unchanged(
    client: TestClient, auth_headers: dict[str, str], inbox_root: Path, vault_root: Path
) -> None:
    outside_target = vault_root.parent / "lock-escape-secret.md"
    outside_target.write_text("do not touch\n", encoding="utf-8")
    (inbox_root / ".append.lock").symlink_to(outside_target)
    (inbox_root / "Note.md").write_text("original\n", encoding="utf-8")

    response = client.post(
        "/api/v1/inbox/notes/append",
        json={"path": "00_Inbox/ChatGPT/Note.md", "content": "x\n"},
        headers=auth_headers,
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_PATH"
    assert outside_target.read_text(encoding="utf-8") == "do not touch\n"
    assert (inbox_root / "Note.md").read_text(encoding="utf-8") == "original\n"
    assert str(vault_root) not in response.text


def test_append_lock_file_not_a_regular_file_is_rejected(
    client: TestClient, auth_headers: dict[str, str], inbox_root: Path
) -> None:
    os.mkfifo(inbox_root / ".append.lock")
    (inbox_root / "Note.md").write_text("original\n", encoding="utf-8")

    response = client.post(
        "/api/v1/inbox/notes/append",
        json={"path": "00_Inbox/ChatGPT/Note.md", "content": "x\n"},
        headers=auth_headers,
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_FILE_TYPE"
    assert (inbox_root / "Note.md").read_text(encoding="utf-8") == "original\n"


def test_append_detects_concurrent_modification_and_leaves_target_unchanged(
    client: TestClient, auth_headers: dict[str, str], inbox_root: Path, monkeypatch
) -> None:
    """A modification landing on the target between this request's read and

    its pre-replace identity check (e.g. LiveSync writing the same file
    concurrently) must be detected — the append must not silently discard
    that other write.
    """
    from app.services import inbox_service

    note = inbox_root / "RaceTarget.md"
    note.write_text("original\n", encoding="utf-8")

    original_write_temp_bytes = inbox_service._write_temp_bytes

    def write_then_mutate(*args, **kwargs):
        note.write_text("original\nsomeone else wrote this\n", encoding="utf-8")
        return original_write_temp_bytes(*args, **kwargs)

    monkeypatch.setattr(inbox_service, "_write_temp_bytes", write_then_mutate)

    response = client.post(
        "/api/v1/inbox/notes/append",
        json={"path": "00_Inbox/ChatGPT/RaceTarget.md", "content": "appended\n"},
        headers=auth_headers,
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "NOTE_MODIFIED"
    assert note.read_text(encoding="utf-8") == "original\nsomeone else wrote this\n"


def test_concurrent_appends_do_not_lose_content(
    client: TestClient, auth_headers: dict[str, str], inbox_root: Path
) -> None:
    (inbox_root / "Concurrent.md").write_text("start\n", encoding="utf-8")
    markers = [f"line-{i}\n" for i in range(8)]

    def append_one(marker: str) -> int:
        response = client.post(
            "/api/v1/inbox/notes/append",
            json={"path": "00_Inbox/ChatGPT/Concurrent.md", "content": marker},
            headers=auth_headers,
        )
        return response.status_code

    with ThreadPoolExecutor(max_workers=8) as pool:
        statuses = list(pool.map(append_one, markers))

    assert all(status == 200 for status in statuses)
    final = (inbox_root / "Concurrent.md").read_text(encoding="utf-8")
    assert final.startswith("start\n")
    for marker in markers:
        assert final.count(marker) == 1


def test_append_response_never_contains_an_absolute_path(
    client: TestClient, auth_headers: dict[str, str], inbox_root: Path, vault_root: Path
) -> None:
    (inbox_root / "PathCheck.md").write_text("x\n", encoding="utf-8")
    response = client.post(
        "/api/v1/inbox/notes/append",
        json={"path": "00_Inbox/ChatGPT/PathCheck.md", "content": "y\n"},
        headers=auth_headers,
    )
    assert str(vault_root) not in response.text


def test_append_note_content_is_readable_via_get_notes(
    client: TestClient, auth_headers: dict[str, str], inbox_root: Path
) -> None:
    (inbox_root / "RoundTrip.md").write_text("# Round trip\n\n", encoding="utf-8")
    client.post(
        "/api/v1/inbox/notes/append",
        json={"path": "00_Inbox/ChatGPT/RoundTrip.md", "content": "more content\n"},
        headers=auth_headers,
    )
    read_response = client.get(
        "/api/v1/notes",
        params={"path": "00_Inbox/ChatGPT/RoundTrip.md"},
        headers=auth_headers,
    )
    assert read_response.status_code == 200
    assert "more content" in read_response.json()["content"]


# --- append lock timeout: app/services/inbox_service.py's _acquire_exclusive_lock ---


def test_acquire_exclusive_lock_retries_eagain_and_eacces_then_succeeds(monkeypatch) -> None:
    # EACCES is the one flock() can raise on contention that BlockingIOError
    # alone would not catch (BlockingIOError only maps EAGAIN/EWOULDBLOCK/
    # EALREADY/EINPROGRESS) — both must be retried, not just EAGAIN.
    from app.services import inbox_service

    pending_errnos = [errno.EAGAIN, errno.EACCES]

    def fake_flock(_fd: int, _flags: int) -> None:
        if pending_errnos:
            raise OSError(pending_errnos.pop(0), "locked")

    monkeypatch.setattr(inbox_service.fcntl, "flock", fake_flock)
    monkeypatch.setattr(inbox_service, "_LOCK_POLL_INTERVAL_SECONDS", 0.001)

    inbox_service._acquire_exclusive_lock(-1)  # fd is unused by the fake
    assert pending_errnos == []


def test_acquire_exclusive_lock_reraises_unrelated_oserror_immediately(monkeypatch) -> None:
    from app.exceptions import InboxLockTimeoutError
    from app.services import inbox_service

    def fake_flock(_fd: int, _flags: int) -> None:
        raise OSError(errno.EIO, "disk error")

    monkeypatch.setattr(inbox_service.fcntl, "flock", fake_flock)
    # A large timeout would make this test slow if the retry loop swallowed
    # EIO instead of re-raising it immediately.
    monkeypatch.setattr(inbox_service, "_LOCK_TIMEOUT_SECONDS", 30.0)

    with pytest.raises(OSError) as excinfo:
        inbox_service._acquire_exclusive_lock(-1)
    assert excinfo.value.errno == errno.EIO
    assert not isinstance(excinfo.value, InboxLockTimeoutError)


def test_acquire_exclusive_lock_times_out_on_sustained_contention(monkeypatch) -> None:
    from app.exceptions import InboxLockTimeoutError
    from app.services import inbox_service

    monkeypatch.setattr(inbox_service, "_LOCK_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(inbox_service, "_LOCK_POLL_INTERVAL_SECONDS", 0.01)

    def always_blocked(_fd: int, _flags: int) -> None:
        raise OSError(errno.EAGAIN, "locked")

    monkeypatch.setattr(inbox_service.fcntl, "flock", always_blocked)

    start = time.monotonic()
    with pytest.raises(InboxLockTimeoutError):
        inbox_service._acquire_exclusive_lock(-1)
    # Bounded: the deadline is set before the first attempt, so the actual
    # wait should not run away past the configured timeout.
    assert time.monotonic() - start < 1.0


# --- append lock timeout: end-to-end, against a real cross-process flock ---


def test_append_times_out_when_lock_held_by_another_process_then_recovers(
    client: TestClient,
    auth_headers: dict[str, str],
    inbox_root: Path,
    monkeypatch,
    vault_root: Path,
    hold_flock_in_subprocess,
) -> None:
    from app.services import inbox_service

    monkeypatch.setattr(inbox_service, "_LOCK_TIMEOUT_SECONDS", 0.3)
    monkeypatch.setattr(inbox_service, "_LOCK_POLL_INTERVAL_SECONDS", 0.02)

    note = inbox_root / "LockedOut.md"
    note.write_text("original\n", encoding="utf-8")
    lock_path = str(inbox_root / ".append.lock")

    ctx = multiprocessing.get_context("spawn")
    acquired = ctx.Event()
    holder = ctx.Process(target=hold_flock_in_subprocess, args=(lock_path, 5.0, acquired))
    holder.start()
    try:
        assert acquired.wait(timeout=5), "holder process never acquired the lock"
        start = time.monotonic()
        response = client.post(
            "/api/v1/inbox/notes/append",
            json={"path": "00_Inbox/ChatGPT/LockedOut.md", "content": "x\n"},
            headers=auth_headers,
        )
        elapsed = time.monotonic() - start
    finally:
        holder.terminate()
        holder.join(timeout=5)

    assert response.status_code == 503
    body = response.json()
    assert body["error"]["code"] == "INBOX_LOCK_TIMEOUT"
    # Bounded by the timeout, not by the holder's 5-second hold.
    assert elapsed < 3.0

    # No absolute path, fd, or inode leaked into the response.
    assert str(vault_root) not in response.text
    assert "Errno" not in response.text
    assert ".append.lock" not in response.text

    # The target must be untouched, and no temp file left behind, while the
    # lock was contended.
    assert note.read_text(encoding="utf-8") == "original\n"
    leftovers = [p for p in inbox_root.iterdir() if p.name.startswith(".tmp-")]
    assert leftovers == []

    # Once the holder process has exited (which releases its flock), the
    # lock must not be stuck: the next append succeeds normally.
    retry = client.post(
        "/api/v1/inbox/notes/append",
        json={"path": "00_Inbox/ChatGPT/LockedOut.md", "content": "second\n"},
        headers=auth_headers,
    )
    assert retry.status_code == 200
    assert note.read_text(encoding="utf-8") == "original\nsecond\n"


def test_append_lock_timeout_is_logged_with_a_reason(
    client: TestClient,
    auth_headers: dict[str, str],
    inbox_root: Path,
    monkeypatch,
    caplog: pytest.LogCaptureFixture,
    hold_flock_in_subprocess,
) -> None:
    from app.services import inbox_service

    monkeypatch.setattr(inbox_service, "_LOCK_TIMEOUT_SECONDS", 0.3)
    monkeypatch.setattr(inbox_service, "_LOCK_POLL_INTERVAL_SECONDS", 0.02)

    (inbox_root / "LoggedTimeout.md").write_text("original\n", encoding="utf-8")
    lock_path = str(inbox_root / ".append.lock")

    ctx = multiprocessing.get_context("spawn")
    acquired = ctx.Event()
    holder = ctx.Process(target=hold_flock_in_subprocess, args=(lock_path, 5.0, acquired))
    holder.start()
    try:
        assert acquired.wait(timeout=5), "holder process never acquired the lock"
        with caplog.at_level("INFO", logger="obsidian_gateway"):
            response = client.post(
                "/api/v1/inbox/notes/append",
                json={"path": "00_Inbox/ChatGPT/LoggedTimeout.md", "content": "x\n"},
                headers=auth_headers,
            )
    finally:
        holder.terminate()
        holder.join(timeout=5)

    assert response.status_code == 503
    records = [r for r in caplog.records if r.name == "obsidian_gateway"]
    assert any(getattr(r, "code", None) == "INBOX_LOCK_TIMEOUT" for r in records)


# --- GET /api/v1/inbox/duplicate-candidates (issue #14) -----------------------


def test_find_duplicate_candidates_requires_auth(client: TestClient) -> None:
    response = client.get("/api/v1/inbox/duplicate-candidates", params={"title": "x"})
    assert response.status_code == 401


def test_find_duplicate_candidates_returns_expected_shape(
    client: TestClient, auth_headers: dict[str, str], inbox_root: Path
) -> None:
    (inbox_root / "Existing.md").write_text("---\ntitle: Shared Title\n---\n", encoding="utf-8")
    response = client.get(
        "/api/v1/inbox/duplicate-candidates",
        params={"title": "Shared Title"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {
        "candidates",
        "candidate_count",
        "truncated",
        "recommendation",
        "scanned_count",
        "skipped_count",
    }
    assert body["recommendation"] == "confirm"
    assert body["candidates"][0]["path"] == "00_Inbox/ChatGPT/Existing.md"
    assert "score" not in body["candidates"][0]


def test_find_duplicate_candidates_no_match_recommends_create(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.get(
        "/api/v1/inbox/duplicate-candidates",
        params={"title": "Nothing in the empty inbox matches"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["candidates"] == []
    assert body["recommendation"] == "create"


def test_find_duplicate_candidates_splits_comma_separated_keywords(
    client: TestClient, auth_headers: dict[str, str], inbox_root: Path
) -> None:
    (inbox_root / "Existing.md").write_text(
        "---\ntitle: Unrelated\ntags: [alpha, beta]\n---\n", encoding="utf-8"
    )
    response = client.get(
        "/api/v1/inbox/duplicate-candidates",
        params={"title": "Different", "keywords": "alpha,beta,gamma"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert set(body["candidates"][0]["matched_keywords"]) == {"alpha", "beta"}


def test_find_duplicate_candidates_rejects_limit_out_of_range(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    # FastAPI's own Query(ge=1, le=10) validation, converted to this app's
    # uniform error envelope by app/main.py's RequestValidationError handler.
    response = client.get(
        "/api/v1/inbox/duplicate-candidates",
        params={"title": "x", "limit": 0},
        headers=auth_headers,
    )
    assert response.status_code == 400

    response = client.get(
        "/api/v1/inbox/duplicate-candidates",
        params={"title": "x", "limit": 11},
        headers=auth_headers,
    )
    assert response.status_code == 400


def test_find_duplicate_candidates_matches_application_layer(
    client: TestClient, auth_headers: dict[str, str], inbox_root: Path
) -> None:
    from app.application import GatewayApplication
    from app.config import get_settings

    (inbox_root / "Existing.md").write_text("---\ntitle: Shared Title\n---\n", encoding="utf-8")
    response = client.get(
        "/api/v1/inbox/duplicate-candidates",
        params={"title": "Shared Title"},
        headers=auth_headers,
    )
    expected = GatewayApplication(get_settings()).find_duplicate_candidates(
        title="Shared Title"
    )
    assert response.json() == expected.model_dump(mode="json")
