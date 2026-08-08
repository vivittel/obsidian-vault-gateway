"""IMPLEMENTATION_PLAN section 15's rejection list, plus the rules from section 7."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.exceptions import GatewayError, InvalidFileTypeError, InvalidPathError, NoteNotFoundError
from app.services.path_security import (
    WalkStats,
    iter_directory,
    iter_vault_notes,
    normalise_relative_dir,
    resolve_read_path,
)

REJECTED_PATHS = [
    "../secret.md",
    "../../.obsidian/config",
    "%2e%2e%2fsecret.md",
    "..\\secret.md",
    "/vault/secret.md",
    "test.txt",
    ".hidden.md",
    "Knowledge/../../secret.md",
    "Knowledge//no_frontmatter.md",
    "Knowledge/.hidden/note.md",
    "C:\\secret.md",
    "%252e%252e%252fsecret.md",  # double-encoded traversal
    "\x00.md",
]


@pytest.mark.parametrize("raw", REJECTED_PATHS)
def test_rejects_malicious_paths(vault_root: Path, raw: str) -> None:
    with pytest.raises(GatewayError) as excinfo:
        resolve_read_path(raw, vault_root)
    assert excinfo.value.status_code in {400, 403, 404}


def test_rejects_symlinked_file(vault_root: Path) -> None:
    with pytest.raises(InvalidPathError):
        resolve_read_path("Knowledge/symlinked-note.md", vault_root)


def test_rejects_note_inside_symlinked_directory(vault_root: Path) -> None:
    with pytest.raises(InvalidPathError):
        resolve_read_path("Knowledge/SymlinkedDir/GPU/RTX 5070.md", vault_root)


def test_rejects_non_markdown_extension(vault_root: Path) -> None:
    (vault_root / "note.MD").write_text("upper case extension", encoding="utf-8")
    with pytest.raises(InvalidFileTypeError):
        resolve_read_path("note.MD", vault_root)


def test_missing_note_is_404(vault_root: Path) -> None:
    with pytest.raises(NoteNotFoundError):
        resolve_read_path("Knowledge/does-not-exist.md", vault_root)


def test_accepts_valid_relative_path(vault_root: Path) -> None:
    resolved = resolve_read_path("Knowledge/PC/GPU/RTX 5070.md", vault_root)
    assert resolved.relative == "Knowledge/PC/GPU/RTX 5070.md"
    assert resolved.path == vault_root / "Knowledge" / "PC" / "GPU" / "RTX 5070.md"


def test_percent_sequences_are_validated_but_not_decoded_for_lookup(vault_root: Path) -> None:
    """resolve_read_path never decodes for the actual filesystem lookup.

    Query-string decoding happens exactly once, at the HTTP layer (Starlette),
    before this function ever sees the value — see test_notes.py for the
    end-to-end case of a real space in a note name. A literal ``%20`` reaching
    this function is therefore looked up as a literal three-character sequence,
    which does not exist on disk, and 404s rather than silently matching the
    space-named file.
    """
    with pytest.raises(NoteNotFoundError):
        resolve_read_path("Knowledge/PC/GPU/RTX%205070.md", vault_root)


@pytest.mark.parametrize("raw", ["/Knowledge", "//Knowledge", "/"])
def test_normalise_relative_dir_rejects_absolute_paths(raw: str) -> None:
    """A leading slash must be rejected before it is stripped away — stripping

    first (an earlier version of this function did) would silently accept
    ``/Knowledge`` as the relative path ``Knowledge``.
    """
    with pytest.raises(InvalidPathError):
        normalise_relative_dir(raw)


def test_normalise_relative_dir_strips_only_a_trailing_slash() -> None:
    assert normalise_relative_dir("Knowledge/PC/") == normalise_relative_dir("Knowledge/PC")


# --- iter_directory's optional `stats` (issue #14: scan failure vs. no-match) --


def test_iter_directory_without_stats_matches_original_behaviour(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Existing callers (get_vault_tree) omit `stats` entirely — a directory
    # scan failure must still just end the iterator, exactly as before this
    # parameter existed.
    root = tmp_path / "vault"
    root.mkdir()

    def _raise(*_args: object, **_kwargs: object):
        raise PermissionError("denied")

    monkeypatch.setattr("app.services.path_security.os.scandir", _raise)
    assert list(iter_directory(root, root)) == []


def test_iter_directory_stats_records_directory_scan_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "vault"
    root.mkdir()

    def _raise(*_args: object, **_kwargs: object):
        raise PermissionError("denied")

    monkeypatch.setattr("app.services.path_security.os.scandir", _raise)
    stats = WalkStats()
    assert list(iter_directory(root, root, stats=stats)) == []
    assert stats.scan_failed is True
    assert stats.skipped == 0


def test_iter_directory_stats_stays_false_on_a_normal_scan(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "Note.md").write_text("x\n", encoding="utf-8")

    stats = WalkStats()
    entries = list(iter_directory(root, root, stats=stats))
    assert {entry.name for entry in entries} == {"Note.md"}
    assert stats.scan_failed is False
    assert stats.skipped == 0


def test_iter_directory_stats_counts_per_entry_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "Good.md").write_text("x\n", encoding="utf-8")
    (root / "Bad.md").write_text("x\n", encoding="utf-8")

    original_is_dir = Path.is_dir

    def flaky_is_dir(self: Path, *args: object, **kwargs: object) -> bool:
        if self.name == "Bad.md":
            raise OSError("simulated stat failure")
        return original_is_dir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "is_dir", flaky_is_dir)

    stats = WalkStats()
    entries = list(iter_directory(root, root, stats=stats))
    assert {entry.name for entry in entries} == {"Good.md"}
    assert stats.skipped == 1


def test_iter_vault_notes_prefix_excludes_stat_failures_outside_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bug this pins: a note that fails to stat() *outside* the requested
    prefix must not be counted at all — not yielded (already true before
    this test) and not added to stats.skipped either (the actual bug:
    app/services/search_service.py's folder-scoped skipped_count used to
    include failures anywhere in the vault, since the folder filter ran
    only after iter_vault_notes had already stat'd — and counted — every
    file, in or out of the requested subtree).
    """
    root = tmp_path / "vault"
    root.mkdir()
    (root / "Knowledge").mkdir()
    (root / "Knowledge" / "Good.md").write_text("x\n", encoding="utf-8")
    (root / "Private").mkdir()
    (root / "Private" / "Bad.md").write_text("x\n", encoding="utf-8")

    import os as os_module

    original_os_stat = os_module.stat

    def flaky_os_stat(path: object, *, dir_fd: object = None, follow_symlinks: bool = True):
        # iter_vault_notes checks is_symlink() (follow_symlinks=False, via
        # lstat) before its own explicit stat() call (follow_symlinks=True,
        # the default). Only failing the latter targets that specific call
        # without also breaking the symlink check every entry goes through
        # (see tests/test_vault.py's identical pattern).
        name = getattr(path, "name", None) or os_module.path.basename(os_module.fspath(path))
        if follow_symlinks and name == "Bad.md":
            raise OSError("simulated permission or unlink race")
        return original_os_stat(path, dir_fd=dir_fd, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(os_module, "stat", flaky_os_stat)

    stats = WalkStats()
    notes = list(iter_vault_notes(root, prefix="Knowledge/", stats=stats))
    assert [note.relative for note in notes] == ["Knowledge/Good.md"]
    assert stats.skipped == 0  # Private/Bad.md is outside the prefix

    stats_in_scope = WalkStats()
    notes_in_scope = list(iter_vault_notes(root, prefix="Private/", stats=stats_in_scope))
    assert notes_in_scope == []
    assert stats_in_scope.skipped == 1  # Private/Bad.md is inside this prefix


def test_iter_vault_notes_without_prefix_matches_original_behaviour(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "A.md").write_text("x\n", encoding="utf-8")
    (root / "B.md").write_text("x\n", encoding="utf-8")

    notes = list(iter_vault_notes(root))
    assert sorted(note.relative for note in notes) == ["A.md", "B.md"]
