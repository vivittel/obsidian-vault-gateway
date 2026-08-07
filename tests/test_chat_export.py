"""app/services/chat_export.py — the structured chat-export formatter (issue
#12). Pure-function tests: no filesystem, no Settings, a fixed ``now``.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
import yaml
from pydantic import ValidationError as PydanticValidationError

from app.exceptions import ValidationError
from app.models import ChatExport
from app.services.chat_export import (
    _ALL_MODE_FIELDS_IN_ORDER,
    _FIELD_OWNER_MODES,
    _MODE_SECTIONS,
    _normalise_tags,
    _one_line,
    render_chat_export,
)

_NOW = datetime(2026, 8, 6, 14, 30, tzinfo=ZoneInfo("Asia/Tokyo"))

_MODE_MINIMAL_FIELDS: dict[str, dict[str, object]] = {
    "summary": {},
    "technical": {},
    "history": {"timeline": [{"event": "x"}]},
    "full": {"topics": [{"heading": "h", "points": ["p"]}]},
    "procedure": {"steps": ["do it"]},
    "issue": {"symptom": ["broke"]},
    "reference": {"facts": ["f"]},
}


def _build(mode: str, **extra: object) -> ChatExport:
    payload: dict[str, object] = {"tldr": ["ok"], "mode": mode}
    payload.update(_MODE_MINIMAL_FIELDS[mode])
    payload.update(extra)
    return ChatExport(**payload)


def _heading_lines(content: str) -> list[str]:
    return [line for line in content.splitlines() if line.startswith("## ")]


# --- Determinism -------------------------------------------------------------


def test_rendering_is_byte_identical_across_calls() -> None:
    export = _build("summary", overview=["a"])
    first = render_chat_export(export, title="t", now=_NOW)
    second = render_chat_export(export, title="t", now=_NOW)
    assert first == second


def test_two_equal_inputs_render_identically() -> None:
    payload = {"tldr": ["ok"], "overview": ["a"]}
    a = ChatExport(**payload)
    b = ChatExport(**payload)
    assert render_chat_export(a, title="t", now=_NOW) == render_chat_export(b, title="t", now=_NOW)


def test_summary_mode_worked_example_renders_exactly() -> None:
    export = ChatExport(
        tldr=[
            "create_inbox_noteを構造化入力の単一窓口に拡張する方針を確認した。",
            "整形はGateway側の決定的フォーマッタが担い、要約はクライアントが行う。",
            "現在はADR-0005の起草待ちで、実装は未着手。",
        ],
        decisions=["エクスポート専用ツールは追加しない。"],
        next_actions=["ADR-0005を起草する。"],
        overview=[
            "既存のcreate_inbox_noteを置き換えず拡張する。",
            "REST側は後方互換のためcontentを残す。",
        ],
        key_points=["MCPは構造化入力のみを受け付ける。", "見出しの順序はGatewayが固定する。"],
        project="obsidian-vault-gateway",
        conversation_type="design",
        tags=["chatgpt", "mcp", "obsidian"],
    )
    rendered = render_chat_export(export, title="MCPゲートウェイの構造化エクスポート設計", now=_NOW)
    yaml_block = yaml.safe_dump(
        rendered.frontmatter, allow_unicode=True, sort_keys=False, default_flow_style=False
    )
    full = f"---\n{yaml_block}---\n\n{rendered.content}"

    assert full == (
        "---\n"
        "title: MCPゲートウェイの構造化エクスポート設計\n"
        "created: '2026-08-06T14:30:00+09:00'\n"
        "updated: '2026-08-06T14:30:00+09:00'\n"
        "source: chatgpt\n"
        "export_mode: summary\n"
        "project: obsidian-vault-gateway\n"
        "conversation_type: design\n"
        "tags:\n"
        "- chatgpt\n"
        "- mcp\n"
        "- obsidian\n"
        "---\n"
        "\n"
        "# MCPゲートウェイの構造化エクスポート設計\n"
        "\n"
        "## 要約\n"
        "\n"
        "create_inbox_noteを構造化入力の単一窓口に拡張する方針を確認した。"
        "整形はGateway側の決定的フォーマッタが担い、要約はクライアントが行う。"
        "現在はADR-0005の起草待ちで、実装は未着手。\n"
        "\n"
        "## 決定事項\n"
        "\n"
        "- エクスポート専用ツールは追加しない。\n"
        "\n"
        "## 概要\n"
        "\n"
        "- 既存のcreate_inbox_noteを置き換えず拡張する。\n"
        "- REST側は後方互換のためcontentを残す。\n"
        "\n"
        "## 要点\n"
        "\n"
        "- MCPは構造化入力のみを受け付ける。\n"
        "- 見出しの順序はGatewayが固定する。\n"
        "\n"
        "## 未解決の論点\n"
        "\n"
        "なし\n"
        "\n"
        "## 次のアクション\n"
        "\n"
        "- ADR-0005を起草する。\n"
        "\n"
        "## 関連ノート\n"
        "\n"
        "なし\n"
        "\n"
        "## 出典\n"
        "\n"
        "なし\n"
    )


# --- Common sections always present, in order --------------------------------


@pytest.mark.parametrize("mode", list(_MODE_SECTIONS))
def test_common_headings_appear_in_required_order(mode: str) -> None:
    export = _build(mode)
    rendered = render_chat_export(export, title="t", now=_NOW)
    headings = _heading_lines(rendered.content)
    assert headings[0] == "## 要約"
    assert headings[1] == "## 決定事項"
    assert headings[-4:] == ["## 未解決の論点", "## 次のアクション", "## 関連ノート", "## 出典"]


@pytest.mark.parametrize("mode", list(_MODE_SECTIONS))
def test_related_notes_is_always_present_and_empty(mode: str) -> None:
    export = _build(mode)
    rendered = render_chat_export(export, title="t", now=_NOW)
    assert "## 関連ノート\n\nなし" in rendered.content


# --- Every mode renders its own headings, in position -------------------------

_EXPECTED_MODE_HEADINGS: dict[str, list[str]] = {
    "summary": ["## 概要", "## 要点"],
    "technical": ["## 背景", "## 設計", "## 実装メモ", "## 検証"],
    "history": ["## 経緯", "## 転換点"],
    "full": ["## トピック"],
    "procedure": ["## 前提条件", "## 手順", "## 検証", "## ロールバック"],
    "issue": ["## 症状", "## 環境", "## 調査", "## 原因", "## 回避策"],
    "reference": ["## 用語", "## 事実", "## 例"],
}


@pytest.mark.parametrize("mode", list(_MODE_SECTIONS))
def test_mode_renders_its_own_headings_in_position(mode: str) -> None:
    export = _build(mode)
    rendered = render_chat_export(export, title="t", now=_NOW)
    headings = _heading_lines(rendered.content)
    assert headings[2 : 2 + len(_EXPECTED_MODE_HEADINGS[mode])] == _EXPECTED_MODE_HEADINGS[mode]


# --- Empty states --------------------------------------------------------------


@pytest.mark.parametrize(
    "field_name", ["decisions", "unresolved_issues", "next_actions", "sources"]
)
def test_empty_common_sections_render_none_placeholder(field_name: str) -> None:
    export = _build("summary")
    rendered = render_chat_export(export, title="t", now=_NOW)
    heading = {
        "decisions": "決定事項",
        "unresolved_issues": "未解決の論点",
        "next_actions": "次のアクション",
        "sources": "出典",
    }[field_name]
    assert f"## {heading}\n\nなし" in rendered.content


@pytest.mark.parametrize("mode,field_name,heading", [
    ("summary", "overview", "概要"),
    ("summary", "key_points", "要点"),
    ("technical", "context", "背景"),
    ("technical", "design", "設計"),
    ("technical", "implementation_notes", "実装メモ"),
    ("technical", "verification", "検証"),
    ("procedure", "verification", "検証"),
    ("history", "turning_points", "転換点"),
    ("procedure", "prerequisites", "前提条件"),
    ("procedure", "rollback", "ロールバック"),
    ("issue", "environment", "環境"),
    ("issue", "investigation", "調査"),
    ("issue", "workaround", "回避策"),
    ("reference", "facts", "事実"),
    ("reference", "examples", "例"),
])
def test_empty_mode_sections_render_not_recorded_placeholder(
    mode: str, field_name: str, heading: str
) -> None:
    # "reference"'s minimal fixture satisfies its spine via `facts`; testing
    # `facts` (or `examples`) empty means satisfying the spine some other way.
    if mode == "reference" and field_name in {"facts", "examples"}:
        export = _build(mode, facts=[], definitions=[{"term": "t", "description": "d"}])
    else:
        export = _build(mode)
    rendered = render_chat_export(export, title="t", now=_NOW)
    assert f"## {heading}\n\n未記録" in rendered.content


def test_empty_root_cause_renders_unresolved_placeholder() -> None:
    export = _build("issue")
    rendered = render_chat_export(export, title="t", now=_NOW)
    assert "## 原因\n\n未解決" in rendered.content


def test_topic_with_no_points_renders_not_recorded() -> None:
    export = _build("full", topics=[{"heading": "h", "points": []}])
    rendered = render_chat_export(export, title="t", now=_NOW)
    assert "### h\n\n未記録" in rendered.content


# --- Metadata generation --------------------------------------------------------


def test_frontmatter_key_order_is_stable_with_optional_fields() -> None:
    export = _build("summary", project="p", conversation_type="c", tags=["x"])
    rendered = render_chat_export(export, title="t", now=_NOW)
    assert list(rendered.frontmatter.keys()) == [
        "title",
        "created",
        "updated",
        "source",
        "export_mode",
        "project",
        "conversation_type",
        "tags",
    ]


def test_frontmatter_key_order_is_stable_without_optional_fields() -> None:
    export = _build("summary")
    rendered = render_chat_export(export, title="t", now=_NOW)
    assert list(rendered.frontmatter.keys()) == [
        "title",
        "created",
        "updated",
        "source",
        "export_mode",
        "tags",
    ]


def test_project_and_conversation_type_omitted_when_absent() -> None:
    export = _build("summary")
    rendered = render_chat_export(export, title="t", now=_NOW)
    assert "project" not in rendered.frontmatter
    assert "conversation_type" not in rendered.frontmatter


@pytest.mark.parametrize("blank", ["", " ", "\n", "\t \n"])
def test_blank_project_is_treated_as_absent(blank: str) -> None:
    export = _build("summary", project=blank)
    rendered = render_chat_export(export, title="t", now=_NOW)
    assert "project" not in rendered.frontmatter


def test_source_is_always_chatgpt() -> None:
    export = _build("summary")
    rendered = render_chat_export(export, title="t", now=_NOW)
    assert rendered.frontmatter["source"] == "chatgpt"


def test_export_mode_matches_input() -> None:
    export = _build("technical")
    rendered = render_chat_export(export, title="t", now=_NOW)
    assert rendered.frontmatter["export_mode"] == "technical"


def test_created_equals_updated_and_is_iso_seconds() -> None:
    export = _build("summary")
    rendered = render_chat_export(export, title="t", now=_NOW)
    assert rendered.frontmatter["created"] == rendered.frontmatter["updated"]
    assert rendered.frontmatter["created"] == "2026-08-06T14:30:00+09:00"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("", []),
        (" ", []),
        ("-", []),
        ("---", []),
        ("#", []),
        ("##tag", ["tag"]),
        ("a b", ["a-b"]),
    ],
)
def test_tag_normalisation_edge_cases(raw: str, expected: list[str]) -> None:
    assert _normalise_tags([raw]) == expected


def test_tag_normalisation_deduplicates_preserving_first_occurrence() -> None:
    assert _normalise_tags(["x", "x", "y"]) == ["x", "y"]


def test_empty_tags_render_as_flow_style_empty_list() -> None:
    export = _build("summary")
    rendered = render_chat_export(export, title="t", now=_NOW)
    assert rendered.frontmatter["tags"] == []
    dumped = yaml.safe_dump(rendered.frontmatter, allow_unicode=True, sort_keys=False)
    assert "tags: []" in dumped


# --- Invalid input (GatewayError path) -----------------------------------------


@pytest.mark.parametrize("mode,field_name", [("summary", "steps"), ("summary", "rollback")])
def test_field_from_another_mode_is_rejected(mode: str, field_name: str) -> None:
    export = _build(mode, **{field_name: ["x"]})
    with pytest.raises(ValidationError) as excinfo:
        render_chat_export(export, title="t", now=_NOW)
    assert field_name in excinfo.value.message


def test_wrong_mode_fields_are_listed_in_definition_order_regardless_of_input_order() -> None:
    export = _build("summary", steps=["s"], rollback=["r"])
    with pytest.raises(ValidationError) as excinfo:
        render_chat_export(export, title="t", now=_NOW)
    assert excinfo.value.message == "Fields not valid for export_mode 'summary': steps, rollback."

    export_reversed = _build("summary", rollback=["r"], steps=["s"])
    with pytest.raises(ValidationError) as excinfo_reversed:
        render_chat_export(export_reversed, title="t", now=_NOW)
    assert excinfo_reversed.value.message == excinfo.value.message


@pytest.mark.parametrize(
    "mode,message",
    [
        ("history", "Export mode 'history' requires timeline."),
        ("full", "Export mode 'full' requires topics."),
        ("procedure", "Export mode 'procedure' requires steps."),
        ("issue", "Export mode 'issue' requires symptom."),
        (
            "reference",
            "Export mode 'reference' requires at least one of definitions, facts, examples.",
        ),
    ],
)
def test_mode_without_its_spine_field_is_rejected(mode: str, message: str) -> None:
    export = ChatExport(mode=mode, tldr=["ok"])
    with pytest.raises(ValidationError) as excinfo:
        render_chat_export(export, title="t", now=_NOW)
    assert excinfo.value.message == message


@pytest.mark.parametrize("field_name", ["definitions", "facts", "examples"])
def test_reference_accepts_any_one_of_the_three(field_name: str) -> None:
    value = [{"term": "t", "description": "d"}] if field_name == "definitions" else ["x"]
    export = ChatExport(mode="reference", tldr=["ok"], **{field_name: value})
    render_chat_export(export, title="t", now=_NOW)  # must not raise


def test_tldr_of_only_whitespace_is_rejected() -> None:
    export = ChatExport(mode="summary", tldr=["\n", "\t"])
    with pytest.raises(ValidationError) as excinfo:
        render_chat_export(export, title="t", now=_NOW)
    assert excinfo.value.message == "tldr must contain at least one non-empty sentence."


# --- Validation must happen on normalised data, not raw pydantic input --------


def test_steps_of_only_whitespace_is_rejected_as_missing_spine() -> None:
    export = _build("procedure", steps=["\n", "\t"])
    with pytest.raises(ValidationError) as excinfo:
        render_chat_export(export, title="t", now=_NOW)
    assert excinfo.value.message == "Export mode 'procedure' requires steps."


def test_symptom_of_only_whitespace_is_rejected_as_missing_spine() -> None:
    export = _build("issue", symptom=[""])
    with pytest.raises(ValidationError) as excinfo:
        render_chat_export(export, title="t", now=_NOW)
    assert excinfo.value.message == "Export mode 'issue' requires symptom."


def test_topic_with_whitespace_only_heading_is_rejected() -> None:
    export = _build("full", topics=[{"heading": "\n", "points": ["x"]}])
    with pytest.raises(ValidationError) as excinfo:
        render_chat_export(export, title="t", now=_NOW)
    assert excinfo.value.message == "topics[0].heading must not be empty."


def test_timeline_entry_with_empty_event_is_rejected() -> None:
    export = _build("history", timeline=[{"event": ""}])
    with pytest.raises(ValidationError) as excinfo:
        render_chat_export(export, title="t", now=_NOW)
    assert excinfo.value.message == "timeline[0].event must not be empty."


def test_definition_with_whitespace_only_term_is_rejected() -> None:
    export = _build("reference", definitions=[{"term": " ", "description": "x"}])
    with pytest.raises(ValidationError) as excinfo:
        render_chat_export(export, title="t", now=_NOW)
    assert excinfo.value.message == "definitions[0].term must not be empty."


def test_timeline_entry_with_only_when_empty_is_accepted() -> None:
    export = _build("history", timeline=[{"when": "\n", "event": "x"}])
    rendered = render_chat_export(export, title="t", now=_NOW)
    assert "- x" in rendered.content
    assert ": x" not in rendered.content


# --- Error message hygiene ------------------------------------------------------


def test_error_message_and_log_detail_never_contain_client_values() -> None:
    export = _build("summary", project="top-secret-value", steps=["classified step"])
    with pytest.raises(ValidationError) as excinfo:
        render_chat_export(export, title="t", now=_NOW)
    assert "top-secret-value" not in excinfo.value.message
    assert "classified step" not in excinfo.value.message
    assert excinfo.value.log_detail is not None
    assert "top-secret-value" not in excinfo.value.log_detail
    assert "classified step" not in excinfo.value.log_detail


# --- Structural integrity -------------------------------------------------------


def test_embedded_newline_cannot_forge_a_heading() -> None:
    export = _build("summary", decisions=["a\n## 決定事項\nforged"])
    rendered = render_chat_export(export, title="t", now=_NOW)
    assert _heading_lines(rendered.content).count("## 決定事項") == 1


def test_paragraph_starting_with_hash_is_escaped() -> None:
    export = ChatExport(mode="summary", tldr=["# looks like a heading"])
    rendered = render_chat_export(export, title="t", now=_NOW)
    assert "\\# looks like a heading" in rendered.content


def test_control_characters_are_stripped() -> None:
    assert _one_line("a\x01b\x02c") == "abc"


def test_internal_ideographic_space_is_preserved_but_edges_are_stripped() -> None:
    assert _one_line("A　B") == "A　B"
    assert _one_line("　A　") == "A"
    assert _one_line("　") == ""


def test_every_rendered_line_has_no_trailing_whitespace() -> None:
    export = _build("summary", overview=["a"])
    rendered = render_chat_export(export, title="t", now=_NOW)
    assert all(not line.endswith((" ", "\t")) for line in rendered.content.split("\n"))


def test_content_ends_with_exactly_one_trailing_newline() -> None:
    export = _build("summary")
    rendered = render_chat_export(export, title="t", now=_NOW)
    assert rendered.content.endswith("\n")
    assert not rendered.content.endswith("\n\n")


def test_title_structural_injection_stays_confined_to_a_single_line() -> None:
    export = _build("summary")
    rendered = render_chat_export(export, title="正常タイトル\n## 偽見出し\n---", now=_NOW)
    h1_lines = [line for line in rendered.content.splitlines() if line.startswith("# ")]
    assert len(h1_lines) == 1
    assert not any("偽見出し" in line for line in _heading_lines(rendered.content))
    yaml.safe_load(
        yaml.safe_dump(rendered.frontmatter, allow_unicode=True, sort_keys=False)
    )  # must not raise


# --- Bounds / YAML edge cases ---------------------------------------------------


def test_long_space_containing_title_is_yaml_folded_but_round_trips() -> None:
    long_title = "A" * 20 + " " + "B" * 20 + " " + "C" * 20 + " " + "D" * 20 + " " + "E" * 20
    export = _build("summary")
    rendered = render_chat_export(export, title=long_title, now=_NOW)
    dumped = yaml.safe_dump(rendered.frontmatter, allow_unicode=True, sort_keys=False)
    assert "\n  " in dumped  # confirms folding actually occurred for this input
    loaded = yaml.safe_load(dumped)
    assert loaded["title"] == rendered.frontmatter["title"] == long_title


def test_steps_render_as_a_one_indexed_ordered_list() -> None:
    export = _build("procedure", steps=["first", "second", "third"])
    rendered = render_chat_export(export, title="t", now=_NOW)
    assert "1. first\n2. second\n3. third" in rendered.content


# --- Model-level bounds (pydantic) ----------------------------------------------


def test_tldr_requires_at_least_one_item() -> None:
    with pytest.raises(PydanticValidationError):
        ChatExport(tldr=[])


def test_line_over_max_length_is_rejected() -> None:
    with pytest.raises(PydanticValidationError):
        ChatExport(tldr=["x" * 1001])


def test_unknown_field_is_rejected() -> None:
    with pytest.raises(PydanticValidationError):
        ChatExport(tldr=["ok"], made_up_field="x")


def test_related_notes_is_not_a_field_yet() -> None:
    # issue #13 owns this field; #12 only fixes the heading/position/placeholder.
    with pytest.raises(PydanticValidationError):
        ChatExport(tldr=["ok"], related_notes=["Knowledge/Foo.md"])


# --- Field-owner-mode consistency (drives the MCP schema description test) ----


def test_field_owner_modes_is_derived_correctly_from_mode_sections() -> None:
    assert _FIELD_OWNER_MODES["verification"] == ("technical", "procedure")
    assert _FIELD_OWNER_MODES["steps"] == ("procedure",)
    assert _FIELD_OWNER_MODES["topics"] == ("full",)
    assert set(_FIELD_OWNER_MODES) == set(_ALL_MODE_FIELDS_IN_ORDER)
