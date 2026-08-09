"""app.application.GatewayApplication — MCP_IMPLEMENTATION_PLAN section 7.

Exercises the transport-neutral layer directly, independent of both REST
routers and (from Phase 1.5) MCP tools, since both are meant to be thin
wrappers around it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.application import GatewayApplication
from app.exceptions import InvalidCursorError, ValidationError
from app.models import ChatExport, CreatedNoteResponse, HealthResponse, NoteResponse, SearchResponse


def test_health_returns_health_response(application: GatewayApplication) -> None:
    response = application.health()
    assert isinstance(response, HealthResponse)
    assert response.status == "ok"
    assert response.vault_readable is True
    assert response.inbox_writable is True


def test_search_notes_returns_search_response(application: GatewayApplication) -> None:
    response = application.search_notes(query="RTX 5070")
    assert isinstance(response, SearchResponse)
    assert any(r.path == "Knowledge/PC/GPU/RTX 5070.md" for r in response.results)


def test_search_notes_rejects_limit_below_one(application: GatewayApplication) -> None:
    with pytest.raises(ValidationError):
        application.search_notes(limit=0)


def test_search_notes_rejects_limit_above_two_hundred(application: GatewayApplication) -> None:
    with pytest.raises(ValidationError):
        application.search_notes(limit=201)


def test_search_notes_clamps_valid_limit_to_max_search_results(
    application: GatewayApplication,
) -> None:
    # 100 passes the [1, 200] check but exceeds the configured max (50, from
    # the application fixture above) — application-layer clamping, not just
    # a transport-level Query(le=200) constraint, has to catch this.
    response = application.search_notes(limit=100)
    assert len(response.results) <= 50


def test_search_notes_next_cursor_resumes_at_the_right_offset(
    application: GatewayApplication,
) -> None:
    first = application.search_notes(limit=1)
    assert first.next_cursor is not None
    second = application.search_notes(limit=1, cursor=first.next_cursor)
    assert second.results != first.results


def test_search_notes_rejects_a_garbage_cursor(application: GatewayApplication) -> None:
    with pytest.raises(InvalidCursorError):
        application.search_notes(cursor="not-a-real-cursor")


def test_search_notes_rejects_a_cursor_from_different_conditions(
    application: GatewayApplication,
) -> None:
    page = application.search_notes(folder="Knowledge/PC", limit=1)
    assert page.next_cursor is not None
    with pytest.raises(InvalidCursorError):
        application.search_notes(limit=1, cursor=page.next_cursor)


def test_read_note_returns_note_response(application: GatewayApplication) -> None:
    response = application.read_note(path="Knowledge/PC/GPU/RTX 5070.md")
    assert isinstance(response, NoteResponse)
    assert response.title == "RTX 5070"


def test_find_duplicate_candidates_rejects_limit_below_one(
    application: GatewayApplication,
) -> None:
    with pytest.raises(ValidationError):
        application.find_duplicate_candidates(title="x", limit=0)


def test_find_duplicate_candidates_rejects_limit_above_max(
    application: GatewayApplication,
) -> None:
    with pytest.raises(ValidationError):
        application.find_duplicate_candidates(title="x", limit=11)


def test_find_duplicate_candidates_rejects_too_many_keywords(
    application: GatewayApplication,
) -> None:
    with pytest.raises(ValidationError):
        application.find_duplicate_candidates(title="x", keywords=[str(i) for i in range(11)])


def test_find_duplicate_candidates_reports_truncated_before_slicing(
    application: GatewayApplication, inbox_root: Path
) -> None:
    (inbox_root / "First.md").write_text("---\ntitle: Shared Title\n---\n", encoding="utf-8")
    (inbox_root / "Second.md").write_text("---\ntitle: Shared Title\n---\n", encoding="utf-8")
    response = application.find_duplicate_candidates(title="Shared Title", limit=1)
    assert len(response.candidates) == 1
    assert response.candidate_count == 2
    assert response.truncated is True
    assert response.recommendation == "choose"


def test_find_duplicate_candidates_no_match_is_not_truncated(
    application: GatewayApplication,
) -> None:
    response = application.find_duplicate_candidates(title="Nothing in the empty inbox matches")
    assert response.candidates == []
    assert response.candidate_count == 0
    assert response.truncated is False
    assert response.recommendation == "create"


def test_find_duplicate_candidates_response_has_no_score_field(
    application: GatewayApplication, inbox_root: Path
) -> None:
    (inbox_root / "Existing.md").write_text("---\ntitle: Shared Title\n---\n", encoding="utf-8")
    response = application.find_duplicate_candidates(title="Shared Title")
    assert "score" not in type(response.candidates[0]).model_fields


def test_find_duplicate_candidates_does_not_gate_create_inbox_note(
    application: GatewayApplication, inbox_root: Path
) -> None:
    # The Gateway never infers write approval from similarity (issue #14):
    # a "confirm"/"choose" recommendation must not stop create_inbox_note
    # from succeeding — that gating is a client-workflow contract
    # (app/mcp_server.py), not something this layer enforces.
    (inbox_root / "Existing.md").write_text("---\ntitle: Shared Title\n---\n", encoding="utf-8")
    duplicates = application.find_duplicate_candidates(title="Shared Title")
    assert duplicates.recommendation in ("confirm", "choose")

    created = application.create_inbox_note(title="Shared Title", content="x\n")
    assert isinstance(created, CreatedNoteResponse)


def test_create_inbox_note_returns_created_note_response(
    application: GatewayApplication,
) -> None:
    response = application.create_inbox_note(title="App layer test", content="x\n")
    assert isinstance(response, CreatedNoteResponse)
    assert response.path == "00_Inbox/ChatGPT/App layer test.md"


def test_create_chat_export_note_returns_created_note_response(
    application: GatewayApplication,
) -> None:
    response = application.create_chat_export_note(
        title="App layer export test", export=ChatExport(tldr=["ok"])
    )
    assert isinstance(response, CreatedNoteResponse)
    assert response.path == "00_Inbox/ChatGPT/App layer export test.md"


def test_create_chat_export_note_verifies_and_links_related_notes(
    application: GatewayApplication, vault_root: Path
) -> None:
    response = application.create_chat_export_note(
        title="Related notes wiring test",
        export=ChatExport(
            tldr=["ok"],
            related_notes=["Knowledge/PC/GPU/RTX 5070.md", "Knowledge/missing.md"],
        ),
    )
    assert response.related_notes_linked == 1
    assert response.related_notes_skipped == 1

    written = (vault_root / response.path).read_text(encoding="utf-8")
    assert "## 関連ノート\n\n- [[Knowledge/PC/GPU/RTX 5070]]" in written


def test_create_chat_export_note_oversized_related_note_candidate_does_not_block_export(
    application: GatewayApplication, vault_root: Path
) -> None:
    # A path over path_security.MAX_PATH_LENGTH (1024) must be dropped like
    # any other unresolvable candidate, not reject the whole export — the
    # schema (ChatExport.related_notes's NotePath) deliberately carries no
    # per-item length bound for exactly this reason.
    oversized = "Knowledge/" + "a" * 1020 + ".md"
    response = application.create_chat_export_note(
        title="Oversized related note candidate test",
        export=ChatExport(
            tldr=["ok"],
            related_notes=[oversized, "Knowledge/PC/GPU/RTX 5070.md"],
        ),
    )
    assert response.related_notes_linked == 1
    assert response.related_notes_skipped == 1

    written = (vault_root / response.path).read_text(encoding="utf-8")
    assert "## 関連ノート\n\n- [[Knowledge/PC/GPU/RTX 5070]]" in written


def test_create_inbox_note_reports_zero_related_notes(
    application: GatewayApplication,
) -> None:
    response = application.create_inbox_note(title="Raw content test", content="x\n")
    assert response.related_notes_linked == 0
    assert response.related_notes_skipped == 0


def test_create_chat_export_note_writes_frontmatter_in_the_documented_order(
    application: GatewayApplication, inbox_root: Path
) -> None:
    application.create_chat_export_note(
        title="Frontmatter order test",
        export=ChatExport(tldr=["ok"], project="p", conversation_type="c", tags=["x"]),
    )
    note = application.read_note(path="00_Inbox/ChatGPT/Frontmatter order test.md")
    assert list(note.frontmatter.keys()) == [
        "title",
        "created",
        "updated",
        "source",
        "export_mode",
        "project",
        "conversation_type",
        "tags",
    ]


def test_create_chat_export_note_timestamps_use_the_configured_timezone(
    application: GatewayApplication,
) -> None:
    response = application.create_chat_export_note(
        title="Timezone test", export=ChatExport(tldr=["ok"])
    )
    assert response.modified_at.utcoffset().total_seconds() == 9 * 3600


def test_created_chat_export_note_is_readable_via_read_note(
    application: GatewayApplication,
) -> None:
    application.create_chat_export_note(
        title="Round trip test", export=ChatExport(tldr=["ok"], decisions=["d"])
    )
    note = application.read_note(path="00_Inbox/ChatGPT/Round trip test.md")
    assert note.frontmatter["source"] == "chatgpt"
    assert note.frontmatter["export_mode"] == "summary"
    assert "## 決定事項" in note.content


def test_create_chat_export_note_title_differs_from_frontmatter_and_h1_after_sanitising(
    application: GatewayApplication,
) -> None:
    response = application.create_chat_export_note(
        title="a/b:c*d", export=ChatExport(tldr=["ok"])
    )
    assert response.title == "a-b-c-d"
    note = application.read_note(path=response.path)
    assert note.frontmatter["title"] == "a/b:c*d"
    # NoteResponse.content is the body markdown_parser split off after the
    # closing frontmatter delimiter; the blank line _render_note always
    # inserts between the delimiter and the body (pre-existing, untouched
    # behaviour) is part of that body, hence the leading "\n" here.
    assert note.content.startswith("\n# a/b:c*d\n")


def test_create_chat_export_note_title_injection_stays_confined_to_one_line(
    application: GatewayApplication,
) -> None:
    response = application.create_chat_export_note(
        title="正常タイトル\n## 偽見出し\n---", export=ChatExport(tldr=["ok"])
    )
    note = application.read_note(path=response.path)
    heading_lines = [line for line in note.content.splitlines() if line.startswith("## ")]
    assert not any("偽見出し" in line for line in heading_lines)


def test_no_response_field_contains_an_absolute_path(
    application: GatewayApplication, vault_root: Path
) -> None:
    health = application.health()
    search = application.search_notes()
    note = application.read_note(path="Knowledge/PC/GPU/RTX 5070.md")
    created = application.create_inbox_note(title="Absolute path check", content="x\n")

    haystacks = [
        health.model_dump_json(),
        search.model_dump_json(),
        note.model_dump_json(),
        created.model_dump_json(),
    ]
    for haystack in haystacks:
        assert str(vault_root) not in haystack
