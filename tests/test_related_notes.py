"""app/services/related_notes.py — verified related-note wikilink resolution
(GitHub issue #13; docs/adr/0006-verified-related-note-wikilinks.md).

Most cases build a minimal disposable vault directly under ``tmp_path``
(never the shared fixture vault) so ambiguous-basename and hazardous-filename
coverage does not have to touch ``tests/fixtures/vault``, which other tests'
count assertions depend on. A few end-to-end cases use the ``vault_root``
fixture from conftest.py (a throwaway per-test copy of that fixture vault)
where the point is specifically to prove behaviour against the real,
shared vault content.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from app.exceptions import NoteNotFoundError
from app.services.related_notes import RelatedNotes, resolve_related_notes


@pytest.fixture
def small_vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    (root / "Knowledge").mkdir(parents=True)
    (root / "00_Inbox" / "ChatGPT").mkdir(parents=True)
    (root / "Knowledge" / "A.md").write_text("a\n", encoding="utf-8")
    (root / "Knowledge" / "B.md").write_text("b\n", encoding="utf-8")
    return root.resolve()


# --- Valid candidates ---------------------------------------------------------


def test_existing_notes_are_accepted_with_md_suffix(small_vault: Path) -> None:
    result = resolve_related_notes(["Knowledge/A.md"], read_root=small_vault, max_links=10)
    assert result.links == ("Knowledge/A.md",)
    assert result.skipped == 0


def test_supplied_order_is_preserved(small_vault: Path) -> None:
    result = resolve_related_notes(
        ["Knowledge/B.md", "Knowledge/A.md"], read_root=small_vault, max_links=10
    )
    assert result.links == ("Knowledge/B.md", "Knowledge/A.md")


# --- Missing candidates -------------------------------------------------------


def test_missing_target_is_omitted_and_counted(small_vault: Path) -> None:
    result = resolve_related_notes(["Knowledge/missing.md"], read_root=small_vault, max_links=10)
    assert result.links == ()
    assert result.skipped == 1


# --- Duplicate candidates ------------------------------------------------------


def test_duplicate_path_dedupes_preserving_first_occurrence(small_vault: Path) -> None:
    result = resolve_related_notes(
        ["Knowledge/A.md", "Knowledge/B.md", "Knowledge/A.md"],
        read_root=small_vault,
        max_links=10,
    )
    assert result.links == ("Knowledge/A.md", "Knowledge/B.md")
    assert result.skipped == 1


def test_hardlinked_note_under_a_different_path_is_not_deduplicated(small_vault: Path) -> None:
    # Identity is the vault-relative path, not the inode: a hardlink at a
    # second path is a second, distinct note in Obsidian's namespace (a
    # second search_notes result, a second graph node), so both link.
    os.link(small_vault / "Knowledge" / "A.md", small_vault / "Knowledge" / "A-copy.md")
    result = resolve_related_notes(
        ["Knowledge/A.md", "Knowledge/A-copy.md"], read_root=small_vault, max_links=10
    )
    assert result.links == ("Knowledge/A.md", "Knowledge/A-copy.md")
    assert result.skipped == 0


# --- Ambiguous basenames: no guessing -----------------------------------------


def test_same_basename_in_two_folders_renders_two_distinct_links(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    (root / "A").mkdir(parents=True)
    (root / "B").mkdir(parents=True)
    (root / "A" / "Note.md").write_text("a\n", encoding="utf-8")
    (root / "B" / "Note.md").write_text("b\n", encoding="utf-8")
    root = root.resolve()

    result = resolve_related_notes(["A/Note.md", "B/Note.md"], read_root=root, max_links=10)
    assert result.links == ("A/Note.md", "B/Note.md")


def test_bare_basename_is_omitted_not_guessed(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    (root / "A").mkdir(parents=True)
    (root / "A" / "Note.md").write_text("a\n", encoding="utf-8")
    root = root.resolve()

    # "Note.md" does not exist at the vault root; the Gateway must not guess
    # that it means "A/Note.md".
    result = resolve_related_notes(["Note.md"], read_root=root, max_links=10)
    assert result.links == ()
    assert result.skipped == 1


def test_ambiguous_basename_end_to_end_does_not_collapse(vault_root: Path) -> None:
    (vault_root / "Knowledge" / "Other").mkdir(parents=True, exist_ok=True)
    (vault_root / "Knowledge" / "Other" / "RTX 5070.md").write_text("dup\n", encoding="utf-8")

    result = resolve_related_notes(
        ["Knowledge/PC/GPU/RTX 5070.md", "Knowledge/Other/RTX 5070.md"],
        read_root=vault_root,
        max_links=10,
    )
    assert result.links == ("Knowledge/PC/GPU/RTX 5070.md", "Knowledge/Other/RTX 5070.md")


# --- Invalid candidates --------------------------------------------------------


@pytest.mark.parametrize(
    "candidate",
    [
        "",
        " ",
        "../secret.md",
        "/abs.md",
        "C:/x.md",
        "a\\b.md",
        "note.txt",
        ".hidden.md",
        "Knowledge/.hidden.md",
        "x\x00.md",
        "%2e%2e/secret.md",
        "a" * 1100 + ".md",
    ],
)
def test_invalid_candidate_is_omitted(candidate: str, small_vault: Path) -> None:
    result = resolve_related_notes([candidate], read_root=small_vault, max_links=10)
    assert result.links == ()
    assert result.skipped == 1


def test_directory_named_like_a_note_is_omitted(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    (root / "Foo.md").mkdir(parents=True)
    root = root.resolve()

    result = resolve_related_notes(["Foo.md"], read_root=root, max_links=10)
    assert result.links == ()
    assert result.skipped == 1


def test_symlinked_note_is_omitted(vault_root: Path) -> None:
    result = resolve_related_notes(
        ["Knowledge/symlinked-note.md"], read_root=vault_root, max_links=10
    )
    assert result.links == ()
    assert result.skipped == 1


@pytest.mark.parametrize(
    "name", ["Bad]].md", "Bad|x.md", "Bad#x.md", "Bad^x.md", "Bad[x.md"]
)
def test_hazardous_characters_in_an_existing_filename_are_omitted(
    name: str, tmp_path: Path
) -> None:
    root = tmp_path / "vault"
    root.mkdir(parents=True)
    (root / name).write_text("x\n", encoding="utf-8")
    root = root.resolve()

    result = resolve_related_notes([name], read_root=root, max_links=10)
    assert result.links == (), f"{name!r} should have been omitted"
    assert result.skipped == 1


def test_newline_in_an_existing_filename_is_omitted(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    root.mkdir(parents=True)
    name = "line\nbreak.md"
    (root / name).write_text("x\n", encoding="utf-8")
    root = root.resolve()

    result = resolve_related_notes([name], read_root=root, max_links=10)
    assert result.links == ()
    assert result.skipped == 1


def test_double_md_suffix_is_omitted(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    root.mkdir(parents=True)
    (root / "Foo.md.md").write_text("x\n", encoding="utf-8")
    root = root.resolve()

    result = resolve_related_notes(["Foo.md.md"], read_root=root, max_links=10)
    assert result.links == ()
    assert result.skipped == 1


def test_note_with_broken_frontmatter_is_accepted(vault_root: Path) -> None:
    # Content is never parsed here, only existence/type is checked.
    result = resolve_related_notes(
        ["Knowledge/broken_frontmatter.md"], read_root=vault_root, max_links=10
    )
    assert result.links == ("Knowledge/broken_frontmatter.md",)


# --- Max count / boundary contract --------------------------------------------


def test_survivors_are_capped_at_max_links(small_vault: Path) -> None:
    (small_vault / "Knowledge" / "C.md").write_text("c\n", encoding="utf-8")
    result = resolve_related_notes(
        ["Knowledge/A.md", "Knowledge/B.md", "Knowledge/C.md"],
        read_root=small_vault,
        max_links=2,
    )
    assert result.links == ("Knowledge/A.md", "Knowledge/B.md")
    assert result.skipped == 1
    assert len(result.links) + result.skipped == 3


def test_max_links_zero_yields_no_links(small_vault: Path) -> None:
    result = resolve_related_notes(
        ["Knowledge/A.md", "Knowledge/B.md"], read_root=small_vault, max_links=0
    )
    assert result.links == ()
    assert result.skipped == 2


def test_negative_max_links_is_a_programming_error(small_vault: Path) -> None:
    with pytest.raises(ValueError, match="non-negative"):
        resolve_related_notes(["Knowledge/A.md"], read_root=small_vault, max_links=-1)


def test_linked_plus_skipped_always_equals_candidate_count(small_vault: Path) -> None:
    candidates = [
        "Knowledge/A.md",
        "Knowledge/A.md",  # duplicate
        "Knowledge/missing.md",  # missing
        "Knowledge/B.md",
        "not_markdown.txt",  # invalid
    ]
    result = resolve_related_notes(candidates, read_root=small_vault, max_links=10)
    assert len(result.links) + result.skipped == len(candidates)


# --- Failure detection: candidate failures are dropped, I/O failures are not --


def test_permission_error_propagates_rather_than_being_dropped(small_vault: Path) -> None:
    with (
        patch(
            "app.services.related_notes.resolve_read_path",
            side_effect=PermissionError("denied"),
        ),
        pytest.raises(PermissionError),
    ):
        resolve_related_notes(["Knowledge/A.md"], read_root=small_vault, max_links=10)


def test_file_not_found_error_is_dropped_like_a_missing_candidate(small_vault: Path) -> None:
    # Simulates a TOCTOU race: resolve_read_path's own lookup succeeds but its
    # trailing stat() finds the file gone (e.g. LiveSync deleted it).
    with patch(
        "app.services.related_notes.resolve_read_path",
        side_effect=FileNotFoundError("gone"),
    ):
        result = resolve_related_notes(["Knowledge/A.md"], read_root=small_vault, max_links=10)
    assert result.links == ()
    assert result.skipped == 1


def test_gateway_error_is_dropped(small_vault: Path) -> None:
    with patch(
        "app.services.related_notes.resolve_read_path",
        side_effect=NoteNotFoundError(),
    ):
        result = resolve_related_notes(["Knowledge/A.md"], read_root=small_vault, max_links=10)
    assert result.links == ()
    assert result.skipped == 1


# --- Empty input ---------------------------------------------------------------


def test_empty_candidate_list_yields_empty_result(small_vault: Path) -> None:
    result = resolve_related_notes([], read_root=small_vault, max_links=10)
    assert result == RelatedNotes(links=(), skipped=0)


# --- Policy: inbox notes are eligible targets ---------------------------------


def test_existing_inbox_note_can_be_linked(vault_root: Path) -> None:
    inbox_note = vault_root / "00_Inbox" / "ChatGPT" / "Existing.md"
    inbox_note.write_text("x\n", encoding="utf-8")

    result = resolve_related_notes(
        ["00_Inbox/ChatGPT/Existing.md"], read_root=vault_root, max_links=10
    )
    assert result.links == ("00_Inbox/ChatGPT/Existing.md",)
