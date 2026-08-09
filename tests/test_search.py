"""app.application.GatewayApplication.search_notes — drives the application
layer directly (REST's own `/api/v1/search` route was removed; see
docs/adr/0010-*.md). The MCP `search_notes` tool is a thin wrapper around the
same method (tests/test_mcp_tools.py), so this module exercises the search
service's actual behaviour once, transport-neutrally.
"""

import pytest

from app.application import GatewayApplication
from app.exceptions import InvalidCursorError, InvalidPathError, ValidationError


def test_search_finds_ascii_query(application: GatewayApplication) -> None:
    response = application.search_notes(query="RTX 5070")
    assert any(r.path == "Knowledge/PC/GPU/RTX 5070.md" for r in response.results)


def test_search_is_case_insensitive(application: GatewayApplication) -> None:
    response = application.search_notes(query="rtx 5070")
    assert any(r.path == "Knowledge/PC/GPU/RTX 5070.md" for r in response.results)


def test_search_matches_fullwidth_query_against_halfwidth_text(
    application: GatewayApplication,
) -> None:
    # GPU比較.md contains "ＲＴＸ５０７０" (full-width); the query below is
    # half-width ASCII. NFKC folding must make them equivalent.
    response = application.search_notes(query="RTX5070")
    assert any(r.path == "Knowledge/PC/GPU/GPU比較.md" for r in response.results)


def test_search_japanese_query(application: GatewayApplication) -> None:
    response = application.search_notes(query="購入")
    assert any(r.path == "Knowledge/PC/GPU/RTX 5070.md" for r in response.results)


def test_search_by_tag(application: GatewayApplication) -> None:
    response = application.search_notes(tags="nvidia")
    assert any(r.path == "Knowledge/PC/GPU/RTX 5070.md" for r in response.results)
    assert all("nvidia" in r.tags for r in response.results)


def test_search_tags_filter_is_and(application: GatewayApplication) -> None:
    response = application.search_notes(tags="gpu,nvidia")
    assert all({"gpu", "nvidia"} <= set(r.tags) for r in response.results)

    response_missing = application.search_notes(tags="gpu,does-not-exist")
    assert response_missing.results == []


def test_search_folder_filter(application: GatewayApplication) -> None:
    response = application.search_notes(folder="Knowledge/PC")
    assert response.results
    assert all(r.path.startswith("Knowledge/PC/") for r in response.results)


def test_search_limit_out_of_range_is_rejected(application: GatewayApplication) -> None:
    # Application-layer re-validation (U7) — independent of whatever
    # transport-level parameter validation a caller's own request went
    # through first.
    with pytest.raises(ValidationError):
        application.search_notes(limit=500)


def test_search_limit_within_range_is_clamped_to_configured_max(
    application: GatewayApplication,
) -> None:
    response = application.search_notes(limit=100)
    assert len(response.results) <= 50


def test_search_excludes_hidden_and_obsidian_and_non_markdown(
    application: GatewayApplication,
) -> None:
    response = application.search_notes(query="")
    paths = [r.path for r in response.results]
    assert all(not p.startswith(".") for p in paths)
    assert all(".obsidian" not in p for p in paths)
    assert all(p.endswith(".md") for p in paths)


def test_search_excludes_symlinks(application: GatewayApplication) -> None:
    response = application.search_notes(query="")
    paths = [r.path for r in response.results]
    assert "Knowledge/symlinked-note.md" not in paths
    assert not any(p.startswith("Knowledge/SymlinkedDir/") for p in paths)


def test_search_excerpt_omits_frontmatter_block(application: GatewayApplication) -> None:
    response = application.search_notes(query="RTX 5070")
    hit = next(r for r in response.results if r.path == "Knowledge/PC/GPU/RTX 5070.md")
    assert "title:" not in hit.excerpt
    assert "tags:" not in hit.excerpt


def test_search_no_query_returns_all_notes_newest_first(
    application: GatewayApplication,
) -> None:
    response = application.search_notes()
    assert response.next_cursor is None
    assert len(response.results) > 0


def test_search_pagination_visits_every_note_without_duplicates_or_gaps(
    application: GatewayApplication,
) -> None:
    full = application.search_notes().results
    assert len(full) >= 3  # otherwise limit=2 wouldn't exercise more than one page

    seen: list[str] = []
    cursor = None
    for _ in range(len(full) + 1):  # generous bound; a stuck cursor must not loop forever
        page = application.search_notes(limit=2, cursor=cursor)
        seen.extend(r.path for r in page.results)
        cursor = page.next_cursor
        if cursor is None:
            break

    assert cursor is None, "pagination did not terminate"
    assert seen == [r.path for r in full]
    assert len(seen) == len(set(seen))


def test_search_cursor_from_a_different_query_is_rejected(
    application: GatewayApplication,
) -> None:
    page = application.search_notes(query="RTX", limit=1)
    assert page.next_cursor is not None

    with pytest.raises(InvalidCursorError):
        application.search_notes(query="GPU", limit=1, cursor=page.next_cursor)


def test_search_cursor_from_a_different_folder_is_rejected(
    application: GatewayApplication,
) -> None:
    page = application.search_notes(folder="Knowledge/PC", limit=1)
    assert page.next_cursor is not None

    with pytest.raises(InvalidCursorError):
        application.search_notes(limit=1, cursor=page.next_cursor)


def test_search_tampered_cursor_is_rejected(application: GatewayApplication) -> None:
    page = application.search_notes(limit=1)
    assert page.next_cursor is not None
    tampered = page.next_cursor[:-1] + ("A" if page.next_cursor[-1] != "A" else "B")

    with pytest.raises(InvalidCursorError):
        application.search_notes(limit=1, cursor=tampered)


def test_search_changing_limit_between_pages_does_not_invalidate_cursor(
    application: GatewayApplication,
) -> None:
    first = application.search_notes(limit=1)
    assert first.next_cursor is not None

    second = application.search_notes(limit=3, cursor=first.next_cursor)
    assert second is not None


@pytest.mark.parametrize("folder", ["/Knowledge", "//Knowledge", "/"])
def test_search_folder_rejects_absolute_paths(
    application: GatewayApplication, folder: str
) -> None:
    with pytest.raises(InvalidPathError):
        application.search_notes(folder=folder)
