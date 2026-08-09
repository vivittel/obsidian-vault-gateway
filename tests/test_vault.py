"""app.application.GatewayApplication.get_vault_tree / get_vault_summary —
PHASE2_PLAN sections 3-4. Drives the application layer directly (REST's own
`/api/v1/vault/*` routes were removed; see docs/adr/0010-*.md) — this is the
only test coverage for app/services/vault_service.py, so nothing here may be
dropped during migration.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.application import GatewayApplication
from app.exceptions import (
    InvalidCursorError,
    InvalidFileTypeError,
    InvalidPathError,
    NoteNotFoundError,
    PathOutsideVaultError,
    ValidationError,
)

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


def test_root_listing_shows_only_top_level_folders(application: GatewayApplication) -> None:
    response = application.get_vault_tree()
    assert response.folder == ""
    assert [e.path for e in response.entries] == ["00_Inbox", "Knowledge"]
    assert all(e.type == "folder" for e in response.entries)


def test_listing_returns_only_direct_children(application: GatewayApplication) -> None:
    response = application.get_vault_tree(folder="Knowledge/PC")
    assert [e.path for e in response.entries] == ["Knowledge/PC/GPU"]


def test_folders_sort_before_notes(application: GatewayApplication) -> None:
    response = application.get_vault_tree(folder="Knowledge")
    kinds = [e.type for e in response.entries]
    first_note_index = next((i for i, k in enumerate(kinds) if k == "note"), len(kinds))
    assert all(k == "folder" for k in kinds[:first_note_index])
    assert all(k == "note" for k in kinds[first_note_index:])
    assert kinds.count("folder") == 1


def test_listing_is_stable_across_repeated_calls(application: GatewayApplication) -> None:
    first = application.get_vault_tree(folder="Knowledge")
    second = application.get_vault_tree(folder="Knowledge")
    assert first.entries == second.entries


def test_empty_folder_returns_no_entries(application: GatewayApplication) -> None:
    # 00_Inbox/ChatGPT only contains a hidden .gitkeep in the fixture vault.
    response = application.get_vault_tree(folder="00_Inbox/ChatGPT")
    assert response.entries == []


def test_japanese_note_name_is_listed(application: GatewayApplication) -> None:
    response = application.get_vault_tree(folder="Knowledge/PC/GPU")
    paths = [e.path for e in response.entries]
    assert "Knowledge/PC/GPU/GPU比較.md" in paths
    assert "Knowledge/PC/GPU/RTX 5070.md" in paths


def test_non_markdown_file_is_excluded(application: GatewayApplication) -> None:
    response = application.get_vault_tree()
    paths = [e.path for e in response.entries]
    assert "not_markdown.txt" not in paths


def test_hidden_entries_are_excluded(application: GatewayApplication) -> None:
    response = application.get_vault_tree()
    paths = [e.path for e in response.entries]
    assert ".hidden.md" not in paths
    assert ".obsidian" not in paths


def test_symlinked_note_and_directory_are_excluded(application: GatewayApplication) -> None:
    response = application.get_vault_tree(folder="Knowledge")
    paths = [e.path for e in response.entries]
    assert "Knowledge/symlinked-note.md" not in paths
    assert "Knowledge/SymlinkedDir" not in paths


@pytest.mark.parametrize("raw", REJECTED_FOLDERS)
def test_rejects_malicious_folders(application: GatewayApplication, raw: str) -> None:
    with pytest.raises((InvalidPathError, PathOutsideVaultError, NoteNotFoundError)):
        application.get_vault_tree(folder=raw)


def test_missing_folder_is_404(application: GatewayApplication) -> None:
    with pytest.raises(NoteNotFoundError):
        application.get_vault_tree(folder="Knowledge/does-not-exist")


def test_folder_naming_a_file_is_rejected(application: GatewayApplication) -> None:
    with pytest.raises(InvalidFileTypeError):
        application.get_vault_tree(folder="Knowledge/no_frontmatter.md")


def test_response_never_contains_an_absolute_path(
    application: GatewayApplication, vault_root: Path
) -> None:
    response = application.get_vault_tree(folder="Knowledge")
    assert str(vault_root) not in response.model_dump_json()


def test_pagination_visits_every_entry_without_duplicates_or_gaps(
    application: GatewayApplication,
) -> None:
    full = application.get_vault_tree(folder="Knowledge").entries
    assert len(full) >= 3

    seen: list[str] = []
    cursor = None
    for _ in range(len(full) + 1):
        page = application.get_vault_tree(folder="Knowledge", limit=2, cursor=cursor)
        seen.extend(e.path for e in page.entries)
        cursor = page.next_cursor
        if cursor is None:
            break

    assert cursor is None, "pagination did not terminate"
    assert seen == [e.path for e in full]
    assert len(seen) == len(set(seen))


def test_cursor_from_a_different_folder_is_rejected(application: GatewayApplication) -> None:
    page = application.get_vault_tree(folder="Knowledge", limit=1)
    assert page.next_cursor is not None

    with pytest.raises(InvalidCursorError):
        application.get_vault_tree(folder="00_Inbox", limit=1, cursor=page.next_cursor)


def test_cursor_shared_between_trailing_slash_and_no_trailing_slash(
    application: GatewayApplication,
) -> None:
    page = application.get_vault_tree(folder="Knowledge/", limit=1)
    assert page.next_cursor is not None

    second = application.get_vault_tree(folder="Knowledge", limit=1, cursor=page.next_cursor)
    assert second is not None


# --- get_vault_summary -----------------------------------------------------

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
    application: GatewayApplication, vault_root: Path
) -> None:
    summary = application.get_vault_summary()

    assert summary.note_count == len(_EXPECTED_NOTE_RELATIVE_PATHS)
    expected_total = sum(
        (vault_root / p).stat().st_size for p in _EXPECTED_NOTE_RELATIVE_PATHS
    )
    assert summary.total_bytes == expected_total
    assert summary.folder_count == 2  # Knowledge, Knowledge/PC/GPU
    assert [item.model_dump() for item in summary.top_level_folders] == [
        {"name": "Knowledge", "note_count": 6}
    ]
    assert summary.skipped_count == 0


def test_summary_tag_counts_are_normalised_and_sorted(application: GatewayApplication) -> None:
    summary = application.get_vault_summary()
    assert [item.model_dump() for item in summary.tags] == [
        {"name": "gpu", "note_count": 2},
        {"name": "comparison", "note_count": 1},
        {"name": "nvidia", "note_count": 1},
    ]


def test_summary_tag_counts_are_folded_across_case_and_width(
    application: GatewayApplication, vault_root: Path
) -> None:
    from app.services.search_service import fold

    # A different note tagging the same concept with a full-width, uppercase
    # variant must merge into the existing "gpu" bucket, not create a second
    # one — regardless of which literal spelling happens to become the label
    # (that depends on walk order, which this test does not want to pin down).
    (vault_root / "Knowledge" / "extra_tag_case.md").write_text(
        "---\ntags: [ＧＰＵ]\n---\n\nExtra note.\n", encoding="utf-8"
    )
    summary = application.get_vault_summary()
    gpu_entries = [t for t in summary.tags if fold(t.name) == "gpu"]
    assert len(gpu_entries) == 1
    assert gpu_entries[0].note_count == 3


def test_summary_top_tags_limit_truncates(application: GatewayApplication) -> None:
    summary = application.get_vault_summary(top_tags_limit=1)
    assert [item.model_dump() for item in summary.tags] == [{"name": "gpu", "note_count": 2}]


def test_summary_top_tags_limit_out_of_range_is_rejected(
    application: GatewayApplication,
) -> None:
    with pytest.raises(ValidationError):
        application.get_vault_summary(top_tags_limit=500)


def test_summary_tolerates_broken_frontmatter(application: GatewayApplication) -> None:
    # Knowledge/broken_frontmatter.md has an unterminated YAML flow sequence;
    # the request must still succeed rather than propagating a YAML error.
    application.get_vault_summary()


def test_summary_skipped_count_includes_walk_level_stat_failures(
    application: GatewayApplication, monkeypatch: pytest.MonkeyPatch
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

    summary = application.get_vault_summary()
    assert summary.skipped_count >= 1
    assert summary.note_count == len(_EXPECTED_NOTE_RELATIVE_PATHS) - 1


def test_summary_skipped_count_includes_read_failures(
    application: GatewayApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import vault_service

    original_read = vault_service.markdown_parser.read_frontmatter_text

    def flaky_read(path: Path) -> str | None:
        if path.name == "crlf.md":
            msg = "simulated read failure"
            raise OSError(msg)
        return original_read(path)

    monkeypatch.setattr(vault_service.markdown_parser, "read_frontmatter_text", flaky_read)

    summary = application.get_vault_summary()
    assert summary.skipped_count >= 1
    assert summary.note_count == len(_EXPECTED_NOTE_RELATIVE_PATHS) - 1


def test_summary_of_empty_vault_has_zero_counts_and_null_last_modified(
    tmp_path: Path,
) -> None:
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
    application: GatewayApplication, vault_root: Path
) -> None:
    summary = application.get_vault_summary()
    assert str(vault_root) not in summary.model_dump_json()
