"""app.mcp_server — MCP_IMPLEMENTATION_PLAN sections 9-14 (unmounted server, 8 tools).

Exercises tools through ``mcp.call_tool(...)`` — the same per-tool argument
validation and tool-body dispatch a real ``tools/call`` request drives —
without any transport; mounting ``/mcp`` itself is a later slice (S6,
tests/test_mcp_protocol.py).

One exception: ``mcp.call_tool(...)`` is the SDK's own convenience method,
and it calls straight into the tool manager, bypassing ``ServerRunner``'s
request dispatch and its ``ServerMiddleware`` chain entirely (verified
against the installed SDK's source). ``_StrictCreateInboxNoteArgumentsMiddleware``
(app/mcp_server.py) therefore never runs for calls made through this module —
its fail-closed behaviour on stray top-level arguments is tested at the wire
level in tests/test_mcp_protocol.py instead, where a real JSON-RPC request
actually goes through that dispatch.
"""

from __future__ import annotations

import logging
import multiprocessing
from datetime import datetime
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from mcp.shared.exceptions import MCPError

from app.application import GatewayApplication
from app.mcp_server import SERVER_INSTRUCTIONS, mcp

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# --- tools/list: presence, annotations, schemas ------------------------------


async def test_tools_list_has_exactly_eight_tools() -> None:
    tools = await mcp.list_tools()
    assert {t.name for t in tools} == {
        "get_health",
        "search_notes",
        "read_note",
        "get_vault_tree",
        "get_vault_summary",
        "find_duplicate_candidates",
        "create_inbox_note",
        "append_inbox_note",
    }


async def test_read_only_tools_have_read_only_annotations() -> None:
    tools = {t.name: t for t in await mcp.list_tools()}
    for name in (
        "get_health",
        "search_notes",
        "read_note",
        "get_vault_tree",
        "get_vault_summary",
        "find_duplicate_candidates",
    ):
        annotations = tools[name].annotations
        assert annotations.read_only_hint is True
        assert annotations.destructive_hint is False
        assert annotations.idempotent_hint is True
        assert annotations.open_world_hint is False


async def test_find_duplicate_candidates_input_schema_has_expected_fields() -> None:
    tools = {t.name: t for t in await mcp.list_tools()}
    schema = tools["find_duplicate_candidates"].input_schema
    assert set(schema["properties"]) == {"title", "project", "keywords", "limit"}
    assert schema["properties"]["limit"]["default"] == 5
    assert schema["properties"]["limit"]["minimum"] == 1
    assert schema["properties"]["limit"]["maximum"] == 10


async def test_create_inbox_note_has_write_annotations() -> None:
    tools = {t.name: t for t in await mcp.list_tools()}
    annotations = tools["create_inbox_note"].annotations
    assert annotations.read_only_hint is False
    assert annotations.destructive_hint is False
    assert annotations.idempotent_hint is False
    assert annotations.open_world_hint is False


async def test_create_inbox_note_input_schema_is_title_plus_structured_export() -> None:
    tools = {t.name: t for t in await mcp.list_tools()}
    schema = tools["create_inbox_note"].input_schema
    assert set(schema["properties"]) == {"title", "export"}
    assert "content" not in schema["properties"]
    assert "frontmatter" not in schema["properties"]
    export_schema = schema["$defs"]["ChatExport"]["properties"]
    assert "path" not in export_schema
    assert "related_notes" in export_schema


async def test_create_inbox_note_export_schema_related_notes_is_bounded() -> None:
    tools = {t.name: t for t in await mcp.list_tools()}
    schema = tools["create_inbox_note"].input_schema
    export_schema = schema["$defs"]["ChatExport"]["properties"]
    related_notes_schema = export_schema["related_notes"]
    # Only the list's item COUNT is schema-enforced; an individual item's
    # shape (e.g. length) is not, so one oversized candidate never blocks
    # the whole export — see the oversized-candidate test further below.
    assert related_notes_schema["maxItems"] == 10
    assert "maxLength" not in related_notes_schema["items"]
    assert "search_notes" in related_notes_schema["description"]


async def test_create_inbox_note_export_schema_orders_related_notes_field() -> None:
    tools = {t.name: t for t in await mcp.list_tools()}
    schema = tools["create_inbox_note"].input_schema
    property_names = list(schema["$defs"]["ChatExport"]["properties"])
    assert property_names.index("next_actions") < property_names.index("related_notes")
    assert property_names.index("related_notes") < property_names.index("sources")


async def test_create_inbox_note_export_mode_defaults_to_summary_in_schema() -> None:
    tools = {t.name: t for t in await mcp.list_tools()}
    schema = tools["create_inbox_note"].input_schema
    mode_schema = schema["$defs"]["ChatExport"]["properties"]["mode"]
    assert mode_schema["default"] == "summary"
    assert set(mode_schema["enum"]) == {
        "summary",
        "technical",
        "history",
        "full",
        "procedure",
        "issue",
        "reference",
    }


async def test_create_inbox_note_export_schema_describes_every_mode_specific_field() -> None:
    from app.services.chat_export import _FIELD_OWNER_MODES

    tools = {t.name: t for t in await mcp.list_tools()}
    schema = tools["create_inbox_note"].input_schema
    export_schema = schema["$defs"]["ChatExport"]["properties"]
    for field_name, owner_modes in _FIELD_OWNER_MODES.items():
        description = export_schema[field_name]["description"]
        for mode in owner_modes:
            assert mode in description, f"{field_name}'s description omits '{mode}'"


async def test_create_inbox_note_steps_description_documents_the_shorthand_rule() -> None:
    # docs/adr/0009-*.md: steps accepts a ProcedureStep object *or* a bare
    # string (a backward-compatible shorthand for a single text block) — the
    # schema-level anyOf can't say which is preferred, so the description
    # must, for a calling model deciding how to send a step with code in it.
    tools = {t.name: t for t in await mcp.list_tools()}
    schema = tools["create_inbox_note"].input_schema
    steps_schema = schema["$defs"]["ChatExport"]["properties"]["steps"]
    description = steps_schema["description"]
    assert "backward-compatible shorthand" in description
    assert "ProcedureStep" in description


async def test_create_inbox_note_code_blocks_description_documents_the_ownership_split() -> None:
    # docs/adr/0009-*.md: code that belongs to a procedure step must not be
    # moved into the top-level code_blocks section, or the step's context is
    # lost — the description is the only place a calling model sees this.
    tools = {t.name: t for t in await mcp.list_tools()}
    schema = tools["create_inbox_note"].input_schema
    code_blocks_schema = schema["$defs"]["ChatExport"]["properties"]["code_blocks"]
    assert "Never move a procedure step's code here" in code_blocks_schema["description"]


async def test_append_inbox_note_has_write_annotations() -> None:
    tools = {t.name: t for t in await mcp.list_tools()}
    annotations = tools["append_inbox_note"].annotations
    assert annotations.read_only_hint is False
    assert annotations.destructive_hint is False
    assert annotations.idempotent_hint is False
    assert annotations.open_world_hint is False


async def test_append_inbox_note_input_schema_has_path_and_content() -> None:
    tools = {t.name: t for t in await mcp.list_tools()}
    schema = tools["append_inbox_note"].input_schema
    assert set(schema["properties"]) == {"path", "content"}


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


async def test_find_duplicate_candidates_matches_application_layer(
    application: GatewayApplication, inbox_root: Path
) -> None:
    (inbox_root / "Existing.md").write_text("---\ntitle: Shared Title\n---\n", encoding="utf-8")
    result = await mcp.call_tool("find_duplicate_candidates", {"title": "Shared Title"})
    expected = application.find_duplicate_candidates(title="Shared Title").model_dump(mode="json")
    assert result.structured_content == expected


# --- issue #14: approval boundary and client-workflow contract ---------------


async def test_find_duplicate_candidates_confirm_does_not_block_create_inbox_note(
    env: None, inbox_root: Path
) -> None:
    # The Gateway never gates create_inbox_note on a duplicate finding —
    # that gating is a client-workflow contract (checked below via the tool
    # descriptions/instructions), not something enforced at this layer.
    (inbox_root / "Existing.md").write_text("---\ntitle: Shared Title\n---\n", encoding="utf-8")
    duplicates = await mcp.call_tool("find_duplicate_candidates", {"title": "Shared Title"})
    assert duplicates.structured_content["recommendation"] in ("confirm", "choose")

    created = await mcp.call_tool(
        "create_inbox_note", {"title": "Shared Title", "export": {"tldr": ["x"]}}
    )
    assert created.is_error is False


async def test_tool_descriptions_document_the_client_workflow_contract() -> None:
    tools = {t.name: t for t in await mcp.list_tools()}
    create_description = tools["create_inbox_note"].description
    append_description = tools["append_inbox_note"].description
    assert "find_duplicate_candidates" in create_description
    assert "find_duplicate_candidates" in append_description
    for phrase in ("confirm", "choose"):
        assert phrase in create_description
        assert phrase in append_description
    assert "find_duplicate_candidates" in SERVER_INSTRUCTIONS
    assert "cancel" in SERVER_INSTRUCTIONS


async def test_create_inbox_note_description_documents_the_scan_failure_fallback() -> None:
    # PR #18 review (P2): create_inbox_note's own description must restate
    # SERVER_INSTRUCTIONS' failure fallback, not just the happy-path
    # confirm/choose/create flow — a client reading only this tool's
    # description must not conclude a recommendation is always required
    # before writing.
    tools = {t.name: t for t in await mcp.list_tools()}
    create_description = tools["create_inbox_note"].description
    assert "find_duplicate_candidates" in create_description
    assert "fails" in create_description
    assert "strict" in create_description


async def test_find_duplicate_candidates_returned_path_is_accepted_by_append_inbox_note(
    env: None, inbox_root: Path
) -> None:
    (inbox_root / "Existing.md").write_text("original\n", encoding="utf-8")
    (inbox_root / "Existing.md").write_text(
        "---\ntitle: Shared Title\n---\n\noriginal\n", encoding="utf-8"
    )
    duplicates = await mcp.call_tool("find_duplicate_candidates", {"title": "Shared Title"})
    candidate_path = duplicates.structured_content["candidates"][0]["path"]

    appended = await mcp.call_tool(
        "append_inbox_note", {"path": candidate_path, "content": "more\n"}
    )
    assert appended.is_error is False


async def test_create_inbox_note_matches_application_layer_shape(env: None) -> None:
    result = await mcp.call_tool(
        "create_inbox_note", {"title": "MCP parity check", "export": {"tldr": ["x"]}}
    )
    assert set(result.structured_content) == {
        "id",
        "path",
        "title",
        "modified_at",
        "related_notes_linked",
        "related_notes_skipped",
    }
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


async def test_search_notes_cursor_matches_application_layer(
    application: GatewayApplication,
) -> None:
    first = await mcp.call_tool("search_notes", {"limit": 1})
    cursor = first.structured_content["next_cursor"]
    assert cursor is not None

    result = await mcp.call_tool("search_notes", {"limit": 1, "cursor": cursor})
    expected = application.search_notes(limit=1, cursor=cursor).model_dump(mode="json")
    assert result.structured_content == expected


async def test_search_notes_invalid_cursor_maps_to_invalid_cursor_code(env: None) -> None:
    with pytest.raises(MCPError) as excinfo:
        await mcp.call_tool("search_notes", {"cursor": "not-a-real-cursor"})
    assert excinfo.value.data == {"code": "INVALID_CURSOR"}


async def test_get_vault_tree_matches_application_layer(application: GatewayApplication) -> None:
    result = await mcp.call_tool("get_vault_tree", {})
    expected = application.get_vault_tree().model_dump(mode="json")
    assert result.structured_content == expected


async def test_get_vault_tree_cursor_matches_application_layer(
    application: GatewayApplication,
) -> None:
    first = await mcp.call_tool("get_vault_tree", {"limit": 1})
    cursor = first.structured_content["next_cursor"]
    assert cursor is not None

    result = await mcp.call_tool("get_vault_tree", {"limit": 1, "cursor": cursor})
    expected = application.get_vault_tree(limit=1, cursor=cursor).model_dump(mode="json")
    assert result.structured_content == expected


async def test_get_vault_tree_folders_before_notes(env: None) -> None:
    result = await mcp.call_tool("get_vault_tree", {"folder": "Knowledge"})
    entries = result.structured_content["entries"]
    kinds = [e["type"] for e in entries]
    first_note_index = next((i for i, k in enumerate(kinds) if k == "note"), len(kinds))
    assert all(k == "folder" for k in kinds[:first_note_index])
    assert all(k == "note" for k in kinds[first_note_index:])


async def test_get_vault_summary_matches_application_layer(
    application: GatewayApplication,
) -> None:
    result = await mcp.call_tool("get_vault_summary", {})
    expected = application.get_vault_summary().model_dump(mode="json")
    assert result.structured_content == expected


async def test_get_vault_summary_never_exposes_note_content(env: None) -> None:
    result = await mcp.call_tool("get_vault_summary", {})
    body = result.structured_content
    assert set(body) == {
        "note_count",
        "total_bytes",
        "folder_count",
        "top_level_folders",
        "tags",
        "last_modified_at",
        "skipped_count",
    }


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


@pytest.mark.parametrize("raw_path", REJECTED_READ_PATHS)
async def test_get_vault_tree_rejects_malicious_folders_without_leaking_internals(
    raw_path: str, env: None, api_token: str, vault_root: Path
) -> None:
    with pytest.raises(MCPError) as excinfo:
        await mcp.call_tool("get_vault_tree", {"folder": raw_path})

    message = excinfo.value.message
    assert "Traceback" not in message
    assert str(vault_root) not in message
    assert "/vault-ro" not in message
    assert api_token not in message
    assert "Errno" not in message
    assert "repr" not in message.lower()


async def test_get_vault_tree_rejects_symlinked_directory(env: None) -> None:
    with pytest.raises(MCPError):
        await mcp.call_tool("get_vault_tree", {"folder": "Knowledge/SymlinkedDir"})


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
    await mcp.call_tool(
        "create_inbox_note", {"title": "MCP contained write", "export": {"tldr": ["x"]}}
    )
    after = set(os.listdir(inbox_root.parent))
    assert before == after


async def test_create_inbox_note_does_not_overwrite_existing(env: None, inbox_root: Path) -> None:
    (inbox_root / "Duplicate.md").write_text("original\n", encoding="utf-8")
    result = await mcp.call_tool(
        "create_inbox_note", {"title": "Duplicate", "export": {"tldr": ["x"]}}
    )
    assert result.structured_content["path"] == "00_Inbox/ChatGPT/Duplicate-2.md"
    assert (inbox_root / "Duplicate.md").read_text(encoding="utf-8") == "original\n"


async def test_create_inbox_note_leaves_no_temp_files_behind(env: None, inbox_root: Path) -> None:
    await mcp.call_tool(
        "create_inbox_note", {"title": "MCP no leftovers", "export": {"tldr": ["x"]}}
    )
    leftovers = [p for p in inbox_root.iterdir() if p.name.startswith(".tmp-")]
    assert leftovers == []


async def test_create_inbox_note_sequence_numbers_increment(env: None, inbox_root: Path) -> None:
    for _ in range(3):
        result = await mcp.call_tool(
            "create_inbox_note", {"title": "MCP repeat", "export": {"tldr": ["x"]}}
        )
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
        await mcp.call_tool("create_inbox_note", {"title": "Full", "export": {"tldr": ["x"]}})
    assert excinfo.value.data == {"code": "NOTE_ALREADY_EXISTS"}


async def test_create_inbox_note_rejects_reserved_windows_name(env: None) -> None:
    with pytest.raises(MCPError) as excinfo:
        await mcp.call_tool("create_inbox_note", {"title": "CON", "export": {"tldr": ["x"]}})
    assert excinfo.value.data == {"code": "INVALID_TITLE"}


async def test_create_inbox_note_rejects_control_characters_leaving_nothing_usable(
    env: None,
) -> None:
    with pytest.raises(MCPError) as excinfo:
        await mcp.call_tool(
            "create_inbox_note", {"title": "\x01\x02\x03", "export": {"tldr": ["x"]}}
        )
    assert excinfo.value.data == {"code": "INVALID_TITLE"}


async def test_create_inbox_note_title_over_max_length_is_rejected(env: None) -> None:
    # Bare-str MCP params carry no length constraint from the SDK unless
    # declared via Annotated[..., Field(...)] — this tool declares
    # max_length=300 on `title` explicitly, so 301 chars is a schema
    # rejection (ToolError), not a silent truncation to the 100-char file stem.
    with pytest.raises(ToolError):
        await mcp.call_tool("create_inbox_note", {"title": "x" * 301, "export": {"tldr": ["y"]}})


async def test_create_inbox_note_title_at_max_length_is_accepted(env: None) -> None:
    result = await mcp.call_tool(
        "create_inbox_note", {"title": "x" * 300, "export": {"tldr": ["y"]}}
    )
    assert len(result.structured_content["title"]) <= 100


# Stray top-level `content`/`frontmatter` alongside `export` used to be
# silently ignored by the SDK's dynamically-generated argument model — see
# _StrictCreateInboxNoteArgumentsMiddleware in app/mcp_server.py (issue #12 /
# PR #16 review). That middleware only runs for requests that go through the
# real JSON-RPC dispatch (the mounted `/mcp` transport), which this module's
# direct `mcp.call_tool(...)` convenience calls bypass entirely — see this
# module's docstring. The fail-closed behaviour itself is therefore tested at
# the wire level in tests/test_mcp_protocol.py, not here.


async def test_create_inbox_note_rejects_unknown_export_field(env: None) -> None:
    # Rejected by the SDK's own argument-schema validation before the tool body
    # (and therefore _McpCall) ever runs — the resulting ToolError embeds a raw
    # pydantic ValidationError message, which only echoes the caller's own
    # submitted value back to them (no absolute paths, no secrets).
    with pytest.raises(ToolError):
        await mcp.call_tool(
            "create_inbox_note",
            {"title": "x", "export": {"tldr": ["y"], "nested": {"a": 1}}},
        )


async def test_create_inbox_note_accepts_related_notes_and_links_verified_targets(
    env: None,
) -> None:
    result = await mcp.call_tool(
        "create_inbox_note",
        {
            "title": "Related notes MCP test",
            "export": {
                "tldr": ["y"],
                "related_notes": ["Knowledge/PC/GPU/RTX 5070.md", "Knowledge/missing.md"],
            },
        },
    )
    assert result.structured_content["related_notes_linked"] == 1
    assert result.structured_content["related_notes_skipped"] == 1


async def test_create_inbox_note_oversized_related_note_candidate_does_not_block_export(
    env: None,
) -> None:
    oversized = "Knowledge/" + "a" * 1020 + ".md"
    result = await mcp.call_tool(
        "create_inbox_note",
        {
            "title": "Oversized related note MCP test",
            "export": {
                "tldr": ["y"],
                "related_notes": [oversized, "Knowledge/PC/GPU/RTX 5070.md"],
            },
        },
    )
    assert result.is_error is False
    assert result.structured_content["related_notes_linked"] == 1
    assert result.structured_content["related_notes_skipped"] == 1


async def test_create_inbox_note_rejects_too_many_related_notes(env: None) -> None:
    with pytest.raises(ToolError):
        await mcp.call_tool(
            "create_inbox_note",
            {
                "title": "x",
                "export": {
                    "tldr": ["y"],
                    "related_notes": [f"Knowledge/{i}.md" for i in range(11)],
                },
            },
        )


async def test_create_inbox_note_is_the_only_write_create_tool() -> None:
    tools = await mcp.list_tools()
    assert not any("export" in t.name for t in tools)


async def test_create_inbox_note_mode_defaults_to_summary(env: None, inbox_root: Path) -> None:
    result = await mcp.call_tool(
        "create_inbox_note", {"title": "Default mode check", "export": {"tldr": ["x"]}}
    )
    written = (inbox_root / "Default mode check.md").read_text(encoding="utf-8")
    assert "export_mode: summary" in written
    assert "## 概要" in written
    assert result.is_error is False


async def test_create_inbox_note_wrong_mode_field_maps_to_validation_error_code(
    env: None, inbox_root: Path
) -> None:
    with pytest.raises(MCPError) as excinfo:
        await mcp.call_tool(
            "create_inbox_note",
            {"title": "Wrong mode field", "export": {"tldr": ["x"], "steps": ["s"]}},
        )
    assert excinfo.value.data == {"code": "VALIDATION_ERROR"}
    assert excinfo.value.message == "Fields not valid for export_mode 'summary': steps."
    assert not (inbox_root / "Wrong mode field.md").exists()


async def test_create_inbox_note_wrong_mode_field_error_leaks_nothing(env: None) -> None:
    with pytest.raises(MCPError) as excinfo:
        await mcp.call_tool(
            "create_inbox_note",
            {"title": "x", "export": {"tldr": ["secret sentence"], "steps": ["classified"]}},
        )
    message = excinfo.value.message
    assert "secret sentence" not in message
    assert "classified" not in message
    assert "Traceback" not in message
    assert "Errno" not in message


@pytest.mark.parametrize(
    "mode,export_extra,heading",
    [
        ("summary", {}, "## 概要"),
        ("technical", {}, "## 背景"),
        ("history", {"timeline": [{"event": "e"}]}, "## 経緯"),
        ("full", {"topics": [{"heading": "h", "points": ["p"]}]}, "## トピック"),
        ("procedure", {"steps": ["do it"]}, "## 手順"),
        ("issue", {"symptom": ["broke"]}, "## 症状"),
        ("reference", {"facts": ["f"]}, "## 用語"),
    ],
)
async def test_each_mode_writes_a_note_with_its_headings(
    env: None, inbox_root: Path, mode: str, export_extra: dict, heading: str
) -> None:
    title = f"Mode {mode} check"
    export = {"mode": mode, "tldr": ["x"], **export_extra}
    result = await mcp.call_tool("create_inbox_note", {"title": title, "export": export})
    assert result.is_error is False
    written = (inbox_root / f"{title}.md").read_text(encoding="utf-8")
    assert f"export_mode: {mode}" in written
    assert heading in written


# --- Verbatim/structure-preserving code content (docs/adr/0009-*.md) -----------


async def test_create_inbox_note_rich_step_writes_a_code_fence(
    env: None, inbox_root: Path
) -> None:
    title = "Rich step check"
    export = {
        "mode": "procedure",
        "tldr": ["x"],
        "steps": [
            {
                "blocks": [
                    {"type": "text", "content": "設定ファイルを開く。"},
                    {"type": "code", "language": "bash", "content": "vi compose.yaml"},
                ]
            }
        ],
    }
    result = await mcp.call_tool("create_inbox_note", {"title": title, "export": export})
    assert result.is_error is False
    written = (inbox_root / f"{title}.md").read_text(encoding="utf-8")
    assert "```bash" in written
    assert "vi compose.yaml" in written


async def test_create_inbox_note_legacy_string_steps_still_write_a_plain_numbered_list(
    env: None, inbox_root: Path
) -> None:
    title = "Legacy string steps check"
    export = {"mode": "procedure", "tldr": ["x"], "steps": ["first", "second"]}
    result = await mcp.call_tool("create_inbox_note", {"title": title, "export": export})
    assert result.is_error is False
    written = (inbox_root / f"{title}.md").read_text(encoding="utf-8")
    assert "1. first\n2. second" in written
    assert "```" not in written


async def test_create_inbox_note_code_first_step_is_rejected(
    env: None, inbox_root: Path
) -> None:
    title = "Code-first step check"
    export = {
        "mode": "procedure",
        "tldr": ["x"],
        "steps": [{"blocks": [{"type": "code", "content": "y"}]}],
    }
    with pytest.raises(MCPError) as excinfo:
        await mcp.call_tool("create_inbox_note", {"title": title, "export": export})
    assert excinfo.value.data == {"code": "VALIDATION_ERROR"}
    assert excinfo.value.message == "steps[0] must start with a text block."
    assert not (inbox_root / f"{title}.md").exists()


async def test_append_inbox_note_matches_application_layer(
    application: GatewayApplication, inbox_root: Path
) -> None:
    (inbox_root / "MCP Append Target.md").write_text("original\n", encoding="utf-8")
    result = await mcp.call_tool(
        "append_inbox_note",
        {"path": "00_Inbox/ChatGPT/MCP Append Target.md", "content": "more\n"},
    )
    assert result.structured_content == {
        "id": "00_Inbox/ChatGPT/MCP Append Target.md",
        "path": "00_Inbox/ChatGPT/MCP Append Target.md",
        "modified_at": result.structured_content["modified_at"],
        "appended_bytes": len(b"more\n"),
    }


async def test_append_inbox_note_appends_without_overwriting(
    env: None, inbox_root: Path
) -> None:
    (inbox_root / "Keep.md").write_text("first\n", encoding="utf-8")
    await mcp.call_tool(
        "append_inbox_note", {"path": "00_Inbox/ChatGPT/Keep.md", "content": "second\n"}
    )
    assert (inbox_root / "Keep.md").read_text(encoding="utf-8") == "first\nsecond\n"


async def test_append_inbox_note_leaves_no_temp_files_behind(
    env: None, inbox_root: Path
) -> None:
    (inbox_root / "NoLeftovers.md").write_text("x\n", encoding="utf-8")
    await mcp.call_tool(
        "append_inbox_note", {"path": "00_Inbox/ChatGPT/NoLeftovers.md", "content": "y\n"}
    )
    leftovers = [p for p in inbox_root.iterdir() if p.name.startswith(".tmp-")]
    assert leftovers == []


async def test_append_inbox_note_writes_only_inside_inbox_root(
    env: None, inbox_root: Path
) -> None:
    import os

    (inbox_root / "Contained.md").write_text("x\n", encoding="utf-8")
    before = set(os.listdir(inbox_root.parent))
    await mcp.call_tool(
        "append_inbox_note", {"path": "00_Inbox/ChatGPT/Contained.md", "content": "y\n"}
    )
    after = set(os.listdir(inbox_root.parent))
    assert before == after


async def test_append_inbox_note_rejects_missing_note(env: None) -> None:
    with pytest.raises(MCPError) as excinfo:
        await mcp.call_tool(
            "append_inbox_note",
            {"path": "00_Inbox/ChatGPT/does-not-exist.md", "content": "x\n"},
        )
    assert excinfo.value.data == {"code": "NOTE_NOT_FOUND"}


async def test_append_inbox_note_rejects_outside_inbox(env: None) -> None:
    with pytest.raises(MCPError) as excinfo:
        await mcp.call_tool(
            "append_inbox_note",
            {"path": "Knowledge/no_frontmatter.md", "content": "x\n"},
        )
    assert excinfo.value.data == {"code": "PATH_OUTSIDE_VAULT"}


async def test_append_inbox_note_rejects_subdirectory(env: None, inbox_root: Path) -> None:
    subdir = inbox_root / "Sub"
    subdir.mkdir()
    (subdir / "Nested.md").write_text("x\n", encoding="utf-8")
    with pytest.raises(MCPError) as excinfo:
        await mcp.call_tool(
            "append_inbox_note",
            {"path": "00_Inbox/ChatGPT/Sub/Nested.md", "content": "y\n"},
        )
    assert excinfo.value.data == {"code": "INVALID_PATH"}


async def test_append_inbox_note_rejects_empty_content(env: None, inbox_root: Path) -> None:
    (inbox_root / "EmptyContent.md").write_text("x\n", encoding="utf-8")
    with pytest.raises(MCPError) as excinfo:
        await mcp.call_tool(
            "append_inbox_note", {"path": "00_Inbox/ChatGPT/EmptyContent.md", "content": "   "}
        )
    assert excinfo.value.data == {"code": "VALIDATION_ERROR"}


async def test_append_inbox_note_rejects_path_without_leaking_internals(
    env: None, api_token: str, vault_root: Path
) -> None:
    with pytest.raises(MCPError) as excinfo:
        await mcp.call_tool(
            "append_inbox_note", {"path": "../secret.md", "content": "x\n"}
        )
    message = excinfo.value.message
    assert "Traceback" not in message
    assert str(vault_root) not in message
    assert "/vault-write" not in message
    assert api_token not in message
    assert "Errno" not in message
    assert "repr" not in message.lower()


async def test_append_inbox_note_maps_lock_timeout_to_mcp_error(
    env: None, inbox_root: Path, monkeypatch, vault_root: Path, hold_flock_in_subprocess
) -> None:
    from app.services import inbox_service

    monkeypatch.setattr(inbox_service, "_LOCK_TIMEOUT_SECONDS", 0.3)
    monkeypatch.setattr(inbox_service, "_LOCK_POLL_INTERVAL_SECONDS", 0.02)

    (inbox_root / "McpLocked.md").write_text("original\n", encoding="utf-8")
    lock_path = str(inbox_root / ".append.lock")

    ctx = multiprocessing.get_context("spawn")
    acquired = ctx.Event()
    holder = ctx.Process(target=hold_flock_in_subprocess, args=(lock_path, 5.0, acquired))
    holder.start()
    try:
        assert acquired.wait(timeout=5), "holder process never acquired the lock"
        with pytest.raises(MCPError) as excinfo:
            await mcp.call_tool(
                "append_inbox_note",
                {"path": "00_Inbox/ChatGPT/McpLocked.md", "content": "x\n"},
            )
    finally:
        holder.terminate()
        holder.join(timeout=5)

    assert excinfo.value.data == {"code": "INBOX_LOCK_TIMEOUT"}
    message = excinfo.value.message
    assert str(vault_root) not in message
    assert "Errno" not in message
    assert ".append.lock" not in message


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
    """U1: the MCP access log never records ``note_path`` at all — for a
    read, a write, or an error. (REST's own access log has no route left
    that sets one either, but for the unrelated reason that REST is
    health-only now — docs/adr/0010-*.md — not because of this rule.)
    """
    caplog.set_level(logging.INFO, logger="obsidian_gateway.mcp")
    await mcp.call_tool("read_note", {"path": "Knowledge/PC/GPU/RTX 5070.md"})
    await mcp.call_tool(
        "create_inbox_note", {"title": "log path check", "export": {"tldr": ["x"]}}
    )
    with pytest.raises(MCPError):
        await mcp.call_tool("read_note", {"path": "../secret.md"})

    for record in caplog.records:
        assert not hasattr(record, "note_path")
        assert "RTX 5070" not in record.getMessage()
        assert "log path check" not in record.getMessage()


async def test_mcp_access_log_never_contains_structured_export_fields(
    env: None, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="obsidian_gateway.mcp")
    secret_content = "extremely sensitive body text that must not leak"
    await mcp.call_tool(
        "create_inbox_note",
        {
            "title": "log test",
            "export": {"tldr": [secret_content], "project": "top-secret-value"},
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


# --- logging: the error status line carries the gateway error code ----------


async def test_mcp_call_error_status_carries_the_gateway_error_code(
    env: None, caplog: pytest.LogCaptureFixture
) -> None:
    """NoteNotFoundError sets no log_detail, so the supplementary
    mcp_tool_error record never fires for it (only logged when status_code
    >= 500 or log_detail is truthy) — before this fix, that left the
    mcp_call record itself with no code at all for the most common
    rejection case.
    """
    caplog.set_level(logging.INFO, logger="obsidian_gateway.mcp")
    with pytest.raises(MCPError):
        await mcp.call_tool("read_note", {"path": "Knowledge/does-not-exist.md"})

    mcp_call_records = [r for r in caplog.records if r.name == "obsidian_gateway.mcp"]
    assert len(mcp_call_records) == 1
    assert mcp_call_records[0].status == "error"
    assert mcp_call_records[0].code == "NOTE_NOT_FOUND"


async def test_mcp_call_error_status_falls_back_to_internal_error_code(
    env: None, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-GatewayError exception (a real bug, not a validated rejection)
    logs a second, separate mcp_tool_unhandled_error record under the plain
    "obsidian_gateway" logger — filtering by logger name here is what keeps
    this test about the mcp_call record specifically, not "the whole log has
    one record".
    """
    caplog.set_level(logging.INFO, logger="obsidian_gateway.mcp")

    def failing_health(self: GatewayApplication) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(GatewayApplication, "health", failing_health)

    with pytest.raises(MCPError):
        await mcp.call_tool("get_health", {})

    mcp_call_records = [r for r in caplog.records if r.name == "obsidian_gateway.mcp"]
    assert len(mcp_call_records) == 1
    assert mcp_call_records[0].status == "error"
    assert mcp_call_records[0].code == "INTERNAL_ERROR"
