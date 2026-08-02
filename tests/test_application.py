"""app.application.GatewayApplication — MCP_IMPLEMENTATION_PLAN section 7.

Exercises the transport-neutral layer directly, independent of both REST
routers and (from Phase 1.5) MCP tools, since both are meant to be thin
wrappers around it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.application import GatewayApplication
from app.config import Settings, get_settings
from app.exceptions import InvalidCursorError, ValidationError
from app.models import CreatedNoteResponse, HealthResponse, NoteResponse, SearchResponse

TEST_API_TOKEN = "test-token-0123456789abcdef"  # noqa: S105 - test fixture, not a real secret


@pytest.fixture
def application(vault_root: Path, inbox_root: Path) -> GatewayApplication:
    settings = Settings(
        api_token=TEST_API_TOKEN,
        mcp_allowed_hosts="testserver,127.0.0.1:*,localhost:*",
        vault_read_root=vault_root,
        vault_inbox_root=inbox_root,
        vault_inbox_relative_path="00_Inbox/ChatGPT",
        max_search_results=50,
        max_note_size_bytes=1_048_576,
        max_request_bytes=2_097_152,
        tz="Asia/Tokyo",
    )
    return GatewayApplication(settings)


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


def test_create_inbox_note_returns_created_note_response(
    application: GatewayApplication,
) -> None:
    response = application.create_inbox_note(title="App layer test", content="x\n")
    assert isinstance(response, CreatedNoteResponse)
    assert response.path == "00_Inbox/ChatGPT/App layer test.md"


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


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    # This test module builds Settings directly rather than through the env
    # fixture; still clear the process-wide cache so other test modules that
    # rely on get_settings() never observe values left over from here.
    yield
    get_settings.cache_clear()
