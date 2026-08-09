"""app.application.GatewayApplication.read_note — drives the application
layer directly (REST's own `/api/v1/notes` route was removed; see
docs/adr/0010-*.md). app/services/note_service.py's own unit tests below are
unchanged; MCP's read_note tool is a thin wrapper checked against the same
function in tests/test_mcp_tools.py.
"""

from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.application import GatewayApplication
from app.exceptions import InvalidPathError, NoteNotFoundError
from app.services import note_service


def test_read_note_returns_frontmatter_and_content(application: GatewayApplication) -> None:
    response = application.read_note(path="Knowledge/PC/GPU/RTX 5070.md")
    assert response.id == "Knowledge/PC/GPU/RTX 5070.md"
    assert response.path == "Knowledge/PC/GPU/RTX 5070.md"
    assert response.title == "RTX 5070"
    assert response.frontmatter == {"title": "RTX 5070", "tags": ["gpu", "nvidia"]}
    assert "# RTX 5070" in response.content
    assert response.truncated is False


def test_read_note_preserves_wikilinks(application: GatewayApplication) -> None:
    response = application.read_note(path="Knowledge/PC/GPU/RTX 5070.md")
    assert "[[GPU比較]]" in response.content


def test_read_note_tolerates_broken_yaml(application: GatewayApplication) -> None:
    response = application.read_note(path="Knowledge/broken_frontmatter.md")
    assert response.frontmatter == {}
    assert response.title == "broken_frontmatter"


def test_read_note_falls_back_to_filename_title(application: GatewayApplication) -> None:
    response = application.read_note(path="Knowledge/no_frontmatter.md")
    assert response.title == "no_frontmatter"


def test_read_note_preserves_crlf(application: GatewayApplication) -> None:
    response = application.read_note(path="Knowledge/crlf.md")
    assert "\r\n" in response.content


def test_read_note_truncates_large_note(env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    # A fresh GatewayApplication, not the shared `application` fixture: that
    # fixture already captured Settings by the time this test's env change
    # runs, and GatewayApplication holds its Settings by reference rather
    # than re-reading get_settings() per call.
    from app.config import get_settings

    monkeypatch.setenv("MAX_NOTE_SIZE_BYTES", "1024")
    get_settings.cache_clear()
    try:
        response = GatewayApplication(get_settings()).read_note(path="Knowledge/large.md")
        assert response.truncated is True
        assert len(response.content) <= 1024
    finally:
        get_settings.cache_clear()


def test_read_note_traversal_is_rejected(application: GatewayApplication) -> None:
    with pytest.raises(InvalidPathError):
        application.read_note(path="../secret.md")


def test_read_note_missing_note_is_404(application: GatewayApplication) -> None:
    with pytest.raises(NoteNotFoundError):
        application.read_note(path="Knowledge/does-not-exist.md")


def test_read_note_response_never_contains_absolute_path(
    application: GatewayApplication, vault_root: Path
) -> None:
    response = application.read_note(path="Knowledge/PC/GPU/RTX 5070.md")
    assert str(vault_root) not in response.model_dump_json()


# app.services.note_service unit tests — same behaviour, called directly
# rather than through the application layer, so Phase 1.5's MCP read_note
# tool can be checked against the identical function.

TOKYO = ZoneInfo("Asia/Tokyo")


def test_note_service_parses_frontmatter_and_body(vault_root: Path) -> None:
    response = note_service.read_note(
        "Knowledge/PC/GPU/RTX 5070.md",
        read_root=vault_root,
        max_note_bytes=1_048_576,
        timezone=TOKYO,
    )
    assert response.id == response.path == "Knowledge/PC/GPU/RTX 5070.md"
    assert response.title == "RTX 5070"
    assert response.frontmatter == {"title": "RTX 5070", "tags": ["gpu", "nvidia"]}
    assert "[[GPU比較]]" in response.content


def test_note_service_tolerates_broken_yaml(vault_root: Path) -> None:
    response = note_service.read_note(
        "Knowledge/broken_frontmatter.md",
        read_root=vault_root,
        max_note_bytes=1_048_576,
        timezone=TOKYO,
    )
    assert response.frontmatter == {}
    assert response.title == "broken_frontmatter"


def test_note_service_preserves_crlf(vault_root: Path) -> None:
    response = note_service.read_note(
        "Knowledge/crlf.md",
        read_root=vault_root,
        max_note_bytes=1_048_576,
        timezone=TOKYO,
    )
    assert "\r\n" in response.content


def test_note_service_sets_truncated_flag_over_size_limit(vault_root: Path) -> None:
    response = note_service.read_note(
        "Knowledge/large.md",
        read_root=vault_root,
        max_note_bytes=1024,
        timezone=TOKYO,
    )
    assert response.truncated is True
    assert len(response.content) <= 1024


def test_note_service_response_never_contains_absolute_path(vault_root: Path) -> None:
    response = note_service.read_note(
        "Knowledge/PC/GPU/RTX 5070.md",
        read_root=vault_root,
        max_note_bytes=1_048_576,
        timezone=TOKYO,
    )
    assert str(vault_root) not in response.model_dump_json()


# Frontmatter that is too expensive or too cyclic for to_json_safe to convert
# degrades to no frontmatter, the same as unparseable YAML — the note itself
# is still readable. See app/services/markdown_parser.py's budget/cycle
# guards and their unit tests in tests/test_markdown_parser.py.


def _write_alias_bomb_note(vault_root: Path, name: str, frontmatter: str) -> None:
    (vault_root / name).write_text(f"---\n{frontmatter}\n---\n\nBody text.\n", encoding="utf-8")


def test_note_service_degrades_exponential_alias_frontmatter_to_empty(vault_root: Path) -> None:
    lines = ["a0: &a0 x"]
    for i in range(1, 10):
        refs = ",".join([f"*a{i - 1}"] * 8)
        lines.append(f"a{i}: &a{i} [{refs}]")
    _write_alias_bomb_note(vault_root, "alias-bomb.md", "\n".join(lines))

    response = note_service.read_note(
        "alias-bomb.md", read_root=vault_root, max_note_bytes=1_048_576, timezone=TOKYO
    )
    assert response.frontmatter == {}
    assert "Body text." in response.content


def test_note_service_degrades_cyclic_alias_frontmatter_to_empty(vault_root: Path) -> None:
    _write_alias_bomb_note(vault_root, "alias-cycle.md", "a: &a [*a]")

    response = note_service.read_note(
        "alias-cycle.md", read_root=vault_root, max_note_bytes=1_048_576, timezone=TOKYO
    )
    assert response.frontmatter == {}
    assert "Body text." in response.content


def test_read_note_alias_bomb_degrades_instead_of_raising(
    application: GatewayApplication, vault_root: Path
) -> None:
    lines = ["a0: &a0 x"]
    for i in range(1, 10):
        refs = ",".join([f"*a{i - 1}"] * 8)
        lines.append(f"a{i}: &a{i} [{refs}]")
    _write_alias_bomb_note(vault_root, "alias-bomb-http.md", "\n".join(lines))

    response = application.read_note(path="alias-bomb-http.md")
    assert response.frontmatter == {}
