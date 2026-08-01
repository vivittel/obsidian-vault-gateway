"""app.mcp_server — MCP_IMPLEMENTATION_PLAN sections 9-14 (unmounted server, 4 tools).

Exercises tools through ``mcp.call_tool(...)`` — the same tool-dispatch
machinery a real ``tools/call`` request drives — without any transport;
mounting ``/mcp`` itself is a later slice (S6, tests/test_mcp_protocol.py).
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from mcp.shared.exceptions import MCPError

from app.application import GatewayApplication
from app.config import get_settings
from app.mcp_server import SERVER_INSTRUCTIONS, mcp

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def application(env: None) -> GatewayApplication:
    return GatewayApplication(get_settings())


# --- tools/list: presence, annotations, schemas ------------------------------


async def test_tools_list_has_exactly_four_tools() -> None:
    tools = await mcp.list_tools()
    assert {t.name for t in tools} == {
        "get_health",
        "search_notes",
        "read_note",
        "create_inbox_note",
    }


async def test_read_only_tools_have_read_only_annotations() -> None:
    tools = {t.name: t for t in await mcp.list_tools()}
    for name in ("get_health", "search_notes", "read_note"):
        annotations = tools[name].annotations
        assert annotations.read_only_hint is True
        assert annotations.destructive_hint is False
        assert annotations.idempotent_hint is True
        assert annotations.open_world_hint is False


async def test_create_inbox_note_has_write_annotations() -> None:
    tools = {t.name: t for t in await mcp.list_tools()}
    annotations = tools["create_inbox_note"].annotations
    assert annotations.read_only_hint is False
    assert annotations.destructive_hint is False
    assert annotations.idempotent_hint is False
    assert annotations.open_world_hint is False


async def test_create_inbox_note_input_schema_has_no_path_field() -> None:
    tools = {t.name: t for t in await mcp.list_tools()}
    schema = tools["create_inbox_note"].input_schema
    assert set(schema["properties"]) == {"title", "content", "frontmatter"}


async def test_search_notes_limit_schema_matches_u7_range() -> None:
    tools = {t.name: t for t in await mcp.list_tools()}
    limit_schema = tools["search_notes"].input_schema["properties"]["limit"]
    assert limit_schema["minimum"] == 1
    assert limit_schema["maximum"] == 200
    assert limit_schema["default"] == 20


def test_server_instructions_fit_key_constraints_in_first_512_chars() -> None:
    head = SERVER_INSTRUCTIONS[:512]
    for phrase in (
        "read-mostly",
        "read-only",
        "00_Inbox/ChatGPT",
        "cannot overwrite, delete, move, or rename",
        "Never claim a write succeeded",
    ):
        assert phrase in head


# --- structured output: parity with GatewayApplication, ISO 8601, path passthrough --


async def test_get_health_matches_application_layer(application: GatewayApplication) -> None:
    result = await mcp.call_tool("get_health", {})
    assert result.structured_content == application.health().model_dump(mode="json")


async def test_search_notes_matches_application_layer(application: GatewayApplication) -> None:
    result = await mcp.call_tool("search_notes", {"query": "RTX 5070"})
    expected = application.search_notes(query="RTX 5070").model_dump(mode="json")
    assert result.structured_content == expected


async def test_read_note_matches_application_layer(application: GatewayApplication) -> None:
    result = await mcp.call_tool("read_note", {"path": "Knowledge/PC/GPU/RTX 5070.md"})
    expected = application.read_note(path="Knowledge/PC/GPU/RTX 5070.md").model_dump(mode="json")
    assert result.structured_content == expected


async def test_create_inbox_note_matches_application_layer_shape(env: None) -> None:
    result = await mcp.call_tool(
        "create_inbox_note", {"title": "MCP parity check", "content": "x\n"}
    )
    assert set(result.structured_content) == {"id", "path", "title", "modified_at"}
    assert result.structured_content["path"] == "00_Inbox/ChatGPT/MCP parity check.md"


async def test_datetime_fields_are_iso_8601_strings(env: None) -> None:
    result = await mcp.call_tool("read_note", {"path": "Knowledge/PC/GPU/RTX 5070.md"})
    modified_at = result.structured_content["modified_at"]
    assert isinstance(modified_at, str)
    datetime.fromisoformat(modified_at)  # raises if not a valid ISO 8601 string


async def test_search_result_path_can_be_passed_directly_to_read_note(env: None) -> None:
    search_result = await mcp.call_tool("search_notes", {"query": "RTX 5070"})
    hit = search_result.structured_content["results"][0]

    read_result = await mcp.call_tool("read_note", {"path": hit["path"]})
    assert read_result.structured_content["path"] == hit["path"]
    assert read_result.is_error is False


# --- error conversion: never leak internals -----------------------------------

REJECTED_READ_PATHS = [
    "../secret.md",
    "../../.obsidian/config",
    "%2e%2e%2fsecret.md",
    "%252e%252e%252fsecret.md",
    "..\\secret.md",
    "/vault/secret.md",
    "C:\\secret.md",
    ".hidden.md",
    "folder/.hidden.md",
    "test.txt",
]


@pytest.mark.parametrize("raw_path", REJECTED_READ_PATHS)
async def test_read_note_rejects_malicious_paths_without_leaking_internals(
    raw_path: str, env: None, api_token: str, vault_root: Path
) -> None:
    with pytest.raises(MCPError) as excinfo:
        await mcp.call_tool("read_note", {"path": raw_path})

    message = excinfo.value.message
    assert "Traceback" not in message
    assert str(vault_root) not in message
    assert "/vault-ro" not in message
    assert api_token not in message
    assert "Errno" not in message
    assert "repr" not in message.lower()


async def test_read_note_rejects_symlinked_note(env: None) -> None:
    with pytest.raises(MCPError):
        await mcp.call_tool("read_note", {"path": "Knowledge/symlinked-note.md"})


async def test_read_note_rejects_note_inside_symlinked_directory(env: None) -> None:
    with pytest.raises(MCPError):
        await mcp.call_tool("read_note", {"path": "Knowledge/SymlinkedDir/GPU/RTX 5070.md"})


async def test_error_message_is_the_fixed_gateway_message(env: None) -> None:
    with pytest.raises(MCPError) as excinfo:
        await mcp.call_tool("read_note", {"path": "../secret.md"})
    assert excinfo.value.message == "The requested path is not a valid vault-relative note path."
    assert excinfo.value.data == {"code": "INVALID_PATH"}


async def test_read_note_missing_file_maps_to_note_not_found(env: None) -> None:
    with pytest.raises(MCPError) as excinfo:
        await mcp.call_tool("read_note", {"path": "Knowledge/does-not-exist.md"})
    assert excinfo.value.data == {"code": "NOTE_NOT_FOUND"}


# --- write safety --------------------------------------------------------------


async def test_create_inbox_note_writes_only_inside_inbox_root(env: None, inbox_root: Path) -> None:
    import os

    before = set(os.listdir(inbox_root.parent))
    await mcp.call_tool("create_inbox_note", {"title": "MCP contained write", "content": "x\n"})
    after = set(os.listdir(inbox_root.parent))
    assert before == after


async def test_create_inbox_note_does_not_overwrite_existing(env: None, inbox_root: Path) -> None:
    (inbox_root / "Duplicate.md").write_text("original\n", encoding="utf-8")
    result = await mcp.call_tool("create_inbox_note", {"title": "Duplicate", "content": "new\n"})
    assert result.structured_content["path"] == "00_Inbox/ChatGPT/Duplicate-2.md"
    assert (inbox_root / "Duplicate.md").read_text(encoding="utf-8") == "original\n"


async def test_create_inbox_note_leaves_no_temp_files_behind(env: None, inbox_root: Path) -> None:
    await mcp.call_tool("create_inbox_note", {"title": "MCP no leftovers", "content": "x\n"})
    leftovers = [p for p in inbox_root.iterdir() if p.name.startswith(".tmp-")]
    assert leftovers == []


async def test_create_inbox_note_sequence_numbers_increment(env: None, inbox_root: Path) -> None:
    for _ in range(3):
        result = await mcp.call_tool("create_inbox_note", {"title": "MCP repeat", "content": "x\n"})
        assert result.is_error is False
    names = sorted(p.name for p in inbox_root.glob("MCP repeat*.md"))
    assert names == ["MCP repeat-2.md", "MCP repeat-3.md", "MCP repeat.md"]


async def test_create_inbox_note_hits_sequence_limit_as_note_already_exists(
    env: None, inbox_root: Path
) -> None:
    for sequence in range(1, 101):
        suffix = "" if sequence == 1 else f"-{sequence}"
        (inbox_root / f"Full{suffix}.md").write_text("x\n", encoding="utf-8")

    with pytest.raises(MCPError) as excinfo:
        await mcp.call_tool("create_inbox_note", {"title": "Full", "content": "x\n"})
    assert excinfo.value.data == {"code": "NOTE_ALREADY_EXISTS"}


async def test_create_inbox_note_rejects_reserved_windows_name(env: None) -> None:
    with pytest.raises(MCPError) as excinfo:
        await mcp.call_tool("create_inbox_note", {"title": "CON", "content": "x\n"})
    assert excinfo.value.data == {"code": "INVALID_TITLE"}


async def test_create_inbox_note_rejects_control_characters_leaving_nothing_usable(
    env: None,
) -> None:
    with pytest.raises(MCPError) as excinfo:
        await mcp.call_tool("create_inbox_note", {"title": "\x01\x02\x03", "content": "x\n"})
    assert excinfo.value.data == {"code": "INVALID_TITLE"}


async def test_create_inbox_note_title_over_max_length_is_truncated_not_rejected(
    env: None,
) -> None:
    result = await mcp.call_tool("create_inbox_note", {"title": "x" * 300, "content": "y\n"})
    assert len(result.structured_content["title"]) <= 100


async def test_create_inbox_note_rejects_nested_frontmatter_structures(env: None) -> None:
    # Rejected by the SDK's own argument-schema validation before the tool body
    # (and therefore _McpCall) ever runs — the resulting ToolError embeds a raw
    # pydantic ValidationError message, which only echoes the caller's own
    # submitted value back to them (no absolute paths, no secrets).
    with pytest.raises(ToolError):
        await mcp.call_tool(
            "create_inbox_note",
            {"title": "x", "content": "y\n", "frontmatter": {"nested": {"a": 1}}},
        )


# --- logging: never leak token / query / path / content / frontmatter --------


async def test_mcp_access_log_never_contains_query_value(
    env: None, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="obsidian_gateway.mcp")
    secret_query = "very-specific-search-term-xyz"
    await mcp.call_tool("search_notes", {"query": secret_query})
    for record in caplog.records:
        assert secret_query not in record.getMessage()
        assert secret_query not in str(record.__dict__)


async def test_mcp_access_log_never_contains_a_note_path_field(
    env: None, caplog: pytest.LogCaptureFixture
) -> None:
    """U1: unlike REST's access log, the MCP access log never records
    ``note_path`` at all — for a read, a write, or an error.
    """
    caplog.set_level(logging.INFO, logger="obsidian_gateway.mcp")
    await mcp.call_tool("read_note", {"path": "Knowledge/PC/GPU/RTX 5070.md"})
    await mcp.call_tool("create_inbox_note", {"title": "log path check", "content": "x\n"})
    with pytest.raises(MCPError):
        await mcp.call_tool("read_note", {"path": "../secret.md"})

    for record in caplog.records:
        assert not hasattr(record, "note_path")
        assert "RTX 5070" not in record.getMessage()
        assert "log path check" not in record.getMessage()


async def test_mcp_access_log_never_contains_note_content_or_frontmatter(
    env: None, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="obsidian_gateway.mcp")
    secret_content = "extremely sensitive body text that must not leak"
    await mcp.call_tool(
        "create_inbox_note",
        {
            "title": "log test",
            "content": secret_content,
            "frontmatter": {"source": "top-secret-value"},
        },
    )
    for record in caplog.records:
        assert secret_content not in record.getMessage()
        assert secret_content not in str(record.__dict__)
        assert "top-secret-value" not in record.getMessage()
        assert "top-secret-value" not in str(record.__dict__)


async def test_mcp_access_log_never_contains_bearer_token(
    env: None, api_token: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="obsidian_gateway.mcp")
    await mcp.call_tool("search_notes", {})
    for record in caplog.records:
        assert api_token not in record.getMessage()
        assert api_token not in str(record.__dict__)
