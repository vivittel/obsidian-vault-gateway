"""IMPLEMENTATION_PLAN section 15's rejection list, plus the rules from section 7."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.exceptions import GatewayError, InvalidFileTypeError, InvalidPathError, NoteNotFoundError
from app.services.path_security import resolve_read_path

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
