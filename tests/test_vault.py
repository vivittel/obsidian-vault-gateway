"""GET /api/v1/vault/tree and /vault/summary — PHASE2_PLAN sections 3-4."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REJECTED_FOLDERS = [
    "../secret",
    "../../.obsidian",
    "%2e%2e%2fsecret",
    "%252e%252e%252fsecret",  # double-encoded traversal
    "..\\secret",
    "/vault/secret",
    "C:\\secret",
    ".hidden",
    "Knowledge/.hidden",
    "Knowledge/../../secret",
    "\x00",
]


def test_root_listing_shows_only_top_level_folders(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.get("/api/v1/vault/tree", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["folder"] == ""
    assert [e["path"] for e in body["entries"]] == ["00_Inbox", "Knowledge"]
    assert all(e["type"] == "folder" for e in body["entries"])


def test_listing_returns_only_direct_children(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.get(
        "/api/v1/vault/tree", params={"folder": "Knowledge/PC"}, headers=auth_headers
    )
    body = response.json()
    assert [e["path"] for e in body["entries"]] == ["Knowledge/PC/GPU"]


def test_folders_sort_before_notes(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.get(
        "/api/v1/vault/tree", params={"folder": "Knowledge"}, headers=auth_headers
    )
    entries = response.json()["entries"]
    kinds = [e["type"] for e in entries]
    first_note_index = next((i for i, k in enumerate(kinds) if k == "note"), len(kinds))
    assert all(k == "folder" for k in kinds[:first_note_index])
    assert all(k == "note" for k in kinds[first_note_index:])
    assert kinds.count("folder") == 1


def test_listing_is_stable_across_repeated_calls(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    params = {"folder": "Knowledge"}
    first = client.get("/api/v1/vault/tree", params=params, headers=auth_headers).json()
    second = client.get("/api/v1/vault/tree", params=params, headers=auth_headers).json()
    assert first["entries"] == second["entries"]


def test_empty_folder_returns_no_entries(client: TestClient, auth_headers: dict[str, str]) -> None:
    # 00_Inbox/ChatGPT only contains a hidden .gitkeep in the fixture vault.
    response = client.get(
        "/api/v1/vault/tree", params={"folder": "00_Inbox/ChatGPT"}, headers=auth_headers
    )
    assert response.status_code == 200
    assert response.json()["entries"] == []


def test_japanese_note_name_is_listed(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.get(
        "/api/v1/vault/tree", params={"folder": "Knowledge/PC/GPU"}, headers=auth_headers
    )
    paths = [e["path"] for e in response.json()["entries"]]
    assert "Knowledge/PC/GPU/GPU比較.md" in paths
    assert "Knowledge/PC/GPU/RTX 5070.md" in paths


def test_non_markdown_file_is_excluded(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.get("/api/v1/vault/tree", headers=auth_headers)
    paths = [e["path"] for e in response.json()["entries"]]
    assert "not_markdown.txt" not in paths


def test_hidden_entries_are_excluded(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.get("/api/v1/vault/tree", headers=auth_headers)
    paths = [e["path"] for e in response.json()["entries"]]
    assert ".hidden.md" not in paths
    assert ".obsidian" not in paths


def test_symlinked_note_and_directory_are_excluded(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.get(
        "/api/v1/vault/tree", params={"folder": "Knowledge"}, headers=auth_headers
    )
    paths = [e["path"] for e in response.json()["entries"]]
    assert "Knowledge/symlinked-note.md" not in paths
    assert "Knowledge/SymlinkedDir" not in paths


@pytest.mark.parametrize("raw", REJECTED_FOLDERS)
def test_rejects_malicious_folders(
    client: TestClient, auth_headers: dict[str, str], raw: str
) -> None:
    response = client.get("/api/v1/vault/tree", params={"folder": raw}, headers=auth_headers)
    assert response.status_code in {400, 403, 404}
    assert "error" in response.json()


def test_missing_folder_is_404(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.get(
        "/api/v1/vault/tree", params={"folder": "Knowledge/does-not-exist"}, headers=auth_headers
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOTE_NOT_FOUND"


def test_folder_naming_a_file_is_rejected(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.get(
        "/api/v1/vault/tree",
        params={"folder": "Knowledge/no_frontmatter.md"},
        headers=auth_headers,
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_FILE_TYPE"


def test_response_never_contains_an_absolute_path(
    client: TestClient, auth_headers: dict[str, str], vault_root: Path
) -> None:
    response = client.get(
        "/api/v1/vault/tree", params={"folder": "Knowledge"}, headers=auth_headers
    )
    assert str(vault_root) not in response.text


def test_pagination_visits_every_entry_without_duplicates_or_gaps(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    full = client.get(
        "/api/v1/vault/tree", params={"folder": "Knowledge"}, headers=auth_headers
    ).json()["entries"]
    assert len(full) >= 3

    seen: list[str] = []
    cursor = None
    for _ in range(len(full) + 1):
        params: dict[str, object] = {"folder": "Knowledge", "limit": 2}
        if cursor:
            params["cursor"] = cursor
        page = client.get("/api/v1/vault/tree", params=params, headers=auth_headers).json()
        seen.extend(e["path"] for e in page["entries"])
        cursor = page["next_cursor"]
        if cursor is None:
            break

    assert cursor is None, "pagination did not terminate"
    assert seen == [e["path"] for e in full]
    assert len(seen) == len(set(seen))


def test_cursor_from_a_different_folder_is_rejected(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    page = client.get(
        "/api/v1/vault/tree",
        params={"folder": "Knowledge", "limit": 1},
        headers=auth_headers,
    ).json()
    assert page["next_cursor"] is not None

    response = client.get(
        "/api/v1/vault/tree",
        params={"folder": "00_Inbox", "limit": 1, "cursor": page["next_cursor"]},
        headers=auth_headers,
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_CURSOR"


def test_cursor_shared_between_trailing_slash_and_no_trailing_slash(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    page = client.get(
        "/api/v1/vault/tree",
        params={"folder": "Knowledge/", "limit": 1},
        headers=auth_headers,
    ).json()
    assert page["next_cursor"] is not None

    response = client.get(
        "/api/v1/vault/tree",
        params={"folder": "Knowledge", "limit": 1, "cursor": page["next_cursor"]},
        headers=auth_headers,
    )
    assert response.status_code == 200


# --- GET /api/v1/vault/summary --------------------------------------------

# The fixture vault plus conftest.vault_root's generated notes: everything
# iter_vault_notes would yield (hidden/.obsidian/non-md/symlinks excluded).
_EXPECTED_NOTE_RELATIVE_PATHS = [
    "Knowledge/broken_frontmatter.md",
    "Knowledge/crlf.md",
    "Knowledge/large.md",
    "Knowledge/no_frontmatter.md",
    "Knowledge/PC/GPU/GPU比較.md",
    "Knowledge/PC/GPU/RTX 5070.md",
]


def test_summary_counts_and_sizes(
    client: TestClient, auth_headers: dict[str, str], vault_root: Path
) -> None:
    response = client.get("/api/v1/vault/summary", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()

    assert body["note_count"] == len(_EXPECTED_NOTE_RELATIVE_PATHS)
    expected_total = sum(
        (vault_root / p).stat().st_size for p in _EXPECTED_NOTE_RELATIVE_PATHS
    )
    assert body["total_bytes"] == expected_total
    assert body["folder_count"] == 2  # Knowledge, Knowledge/PC/GPU
    assert body["top_level_folders"] == [{"name": "Knowledge", "note_count": 6}]
    assert body["skipped_count"] == 0


def test_summary_tag_counts_are_normalised_and_sorted(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.get("/api/v1/vault/summary", headers=auth_headers)
    assert response.json()["tags"] == [
        {"name": "gpu", "note_count": 2},
        {"name": "comparison", "note_count": 1},
        {"name": "nvidia", "note_count": 1},
    ]


def test_summary_tag_counts_are_folded_across_case_and_width(
    client: TestClient, auth_headers: dict[str, str], vault_root: Path
) -> None:
    from app.services.search_service import fold

    # A different note tagging the same concept with a full-width, uppercase
    # variant must merge into the existing "gpu" bucket, not create a second
    # one — regardless of which literal spelling happens to become the label
    # (that depends on walk order, which this test does not want to pin down).
    (vault_root / "Knowledge" / "extra_tag_case.md").write_text(
        "---\ntags: [ＧＰＵ]\n---\n\nExtra note.\n", encoding="utf-8"
    )
    response = client.get("/api/v1/vault/summary", headers=auth_headers)
    tags = response.json()["tags"]
    gpu_entries = [t for t in tags if fold(t["name"]) == "gpu"]
    assert len(gpu_entries) == 1
    assert gpu_entries[0]["note_count"] == 3


def test_summary_top_tags_limit_truncates(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.get(
        "/api/v1/vault/summary", params={"top_tags_limit": 1}, headers=auth_headers
    )
    assert response.json()["tags"] == [{"name": "gpu", "note_count": 2}]


def test_summary_top_tags_limit_out_of_range_is_rejected(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.get(
        "/api/v1/vault/summary", params={"top_tags_limit": 500}, headers=auth_headers
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_summary_tolerates_broken_frontmatter(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    # Knowledge/broken_frontmatter.md has an unterminated YAML flow sequence;
    # the request must still succeed rather than propagating a YAML error.
    response = client.get("/api/v1/vault/summary", headers=auth_headers)
    assert response.status_code == 200


def test_summary_skipped_count_includes_walk_level_stat_failures(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    import os as os_module

    original_os_stat = os_module.stat

    def flaky_os_stat(path: object, *, dir_fd: object = None, follow_symlinks: bool = True):
        # iter_vault_notes checks is_symlink() (follow_symlinks=False, via
        # lstat) before its own explicit stat() call (follow_symlinks=True,
        # the default). Only failing the latter targets that specific call
        # without also breaking the symlink check every entry goes through.
        name = getattr(path, "name", None) or os_module.path.basename(os_module.fspath(path))
        if follow_symlinks and name == "crlf.md":
            msg = "simulated stat failure"
            raise OSError(msg)
        return original_os_stat(path, dir_fd=dir_fd, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(os_module, "stat", flaky_os_stat)

    response = client.get("/api/v1/vault/summary", headers=auth_headers)
    body = response.json()
    assert body["skipped_count"] >= 1
    assert body["note_count"] == len(_EXPECTED_NOTE_RELATIVE_PATHS) - 1


def test_summary_skipped_count_includes_read_failures(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import vault_service

    original_read = vault_service.markdown_parser.read_note_text

    def flaky_read(path: Path, *, size_bytes: int, max_bytes: int) -> tuple[str, bool]:
        if path.name == "crlf.md":
            msg = "simulated read failure"
            raise OSError(msg)
        return original_read(path, size_bytes=size_bytes, max_bytes=max_bytes)

    monkeypatch.setattr(vault_service.markdown_parser, "read_note_text", flaky_read)

    response = client.get("/api/v1/vault/summary", headers=auth_headers)
    body = response.json()
    assert body["skipped_count"] >= 1
    assert body["note_count"] == len(_EXPECTED_NOTE_RELATIVE_PATHS) - 1


def test_summary_of_empty_vault_has_zero_counts_and_null_last_modified(
    tmp_path: Path,
) -> None:
    from app.application import GatewayApplication
    from app.config import Settings

    empty_root = tmp_path / "empty-vault"
    empty_root.mkdir()
    inbox_root = empty_root / "00_Inbox" / "ChatGPT"
    inbox_root.mkdir(parents=True)

    settings = Settings(
        api_token="test-token-0123456789abcdef",
        mcp_allowed_hosts="testserver",
        vault_read_root=empty_root,
        vault_inbox_root=inbox_root,
        vault_inbox_relative_path="00_Inbox/ChatGPT",
        max_search_results=50,
        max_note_size_bytes=1_048_576,
        max_request_bytes=2_097_152,
        tz="Asia/Tokyo",
    )
    summary = GatewayApplication(settings).get_vault_summary()

    assert summary.note_count == 0
    assert summary.total_bytes == 0
    assert summary.folder_count == 0
    assert summary.top_level_folders == []
    assert summary.tags == []
    assert summary.last_modified_at is None
    assert summary.skipped_count == 0


def test_summary_response_never_contains_an_absolute_path(
    client: TestClient, auth_headers: dict[str, str], vault_root: Path
) -> None:
    response = client.get("/api/v1/vault/summary", headers=auth_headers)
    assert str(vault_root) not in response.text
