"""app/services/chat_export.py — the structured chat-export formatter (issue
#12). Pure-function tests: no filesystem, no Settings, a fixed ``now``.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
import yaml
from markdown_it import MarkdownIt
from pydantic import ValidationError as PydanticValidationError

from app.exceptions import ValidationError
from app.models import ChatExport
from app.services.chat_export import (
    _ALL_MODE_FIELDS_IN_ORDER,
    _FIELD_OWNER_MODES,
    _INLINE_ESCAPE_CHARS,
    _MAX_TOTAL_BLOCK_CHARS,
    _MODE_SECTIONS,
    _canonicalise_code,
    _escape_inline,
    _fence_for,
    _NormalisedCodeBlock,
    _render_fenced_code,
    format_wikilink,
    is_renderable_wikilink_target,
    one_line,
    render_chat_export,
)

_MD = MarkdownIt("commonmark")

# GFM tables are off in the commonmark preset — _MD above deliberately keeps
# them off, since every pre-existing test relies on a pipe line parsing as an
# ordinary paragraph. A second instance with the table rule enabled is used
# only by the docs/adr/0011-*.md tests below. No new dependency: `.enable`
# only turns on a rule already bundled with markdown-it-py's core preset.
_MD_TABLE = MarkdownIt("commonmark").enable("table")

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


def test_summary_mode_worked_example_with_related_notes_renders_exactly() -> None:
    export = ChatExport(tldr=["ok"])
    rendered = render_chat_export(
        export,
        title="t",
        now=_NOW,
        verified_related_notes=["Knowledge/PC/GPU/RTX 5070.md", "Knowledge/no_frontmatter.md"],
    )
    yaml_block = yaml.safe_dump(
        rendered.frontmatter, allow_unicode=True, sort_keys=False, default_flow_style=False
    )
    full = f"---\n{yaml_block}---\n\n{rendered.content}"

    assert full == (
        "---\n"
        "title: t\n"
        "created: '2026-08-06T14:30:00+09:00'\n"
        "updated: '2026-08-06T14:30:00+09:00'\n"
        "source: chatgpt\n"
        "export_mode: summary\n"
        "tags: []\n"
        "---\n"
        "\n"
        "# t\n"
        "\n"
        "## 要約\n"
        "\n"
        "ok\n"
        "\n"
        "## 決定事項\n"
        "\n"
        "なし\n"
        "\n"
        "## 概要\n"
        "\n"
        "未記録\n"
        "\n"
        "## 要点\n"
        "\n"
        "未記録\n"
        "\n"
        "## 未解決の論点\n"
        "\n"
        "なし\n"
        "\n"
        "## 次のアクション\n"
        "\n"
        "なし\n"
        "\n"
        "## 関連ノート\n"
        "\n"
        "- [[Knowledge/PC/GPU/RTX 5070]]\n"
        "- [[Knowledge/no_frontmatter]]\n"
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
def test_related_notes_is_present_and_empty_when_no_links(mode: str) -> None:
    export = _build(mode)
    rendered = render_chat_export(export, title="t", now=_NOW)
    assert "## 関連ノート\n\nなし" in rendered.content


def test_related_notes_renders_verified_links_in_supplied_order() -> None:
    export = _build("summary")
    rendered = render_chat_export(
        export,
        title="t",
        now=_NOW,
        verified_related_notes=["Knowledge/B.md", "Knowledge/A.md"],
    )
    assert "## 関連ノート\n\n- [[Knowledge/B]]\n- [[Knowledge/A]]" in rendered.content


def test_related_notes_ignores_export_related_notes_field_directly() -> None:
    # render_chat_export only renders verified_related_notes; the raw client
    # field on ChatExport is never read by the formatter (docs/adr/0006-*.md).
    export = _build("summary", related_notes=["Knowledge/Unverified.md"])
    rendered = render_chat_export(export, title="t", now=_NOW)
    assert "## 関連ノート\n\nなし" in rendered.content
    assert "Unverified" not in rendered.content


def test_rendering_with_related_notes_is_byte_identical_across_calls() -> None:
    export = _build("summary")
    links = ["Knowledge/B.md", "Knowledge/A.md"]
    a = render_chat_export(export, title="t", now=_NOW, verified_related_notes=links)
    b = render_chat_export(export, title="t", now=_NOW, verified_related_notes=links)
    assert a == b


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
    ("raw", "expected"),
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
    # Through the public contract, not the private _normalise_tags helper:
    # `Tag` (unlike `Label`) has no min_length, so raw="" must be pydantic-valid
    # on ChatExport itself — that gap between the model and the formatter is
    # exactly what this test would otherwise fail to catch.
    export = ChatExport(tldr=["ok"], tags=[raw])
    rendered = render_chat_export(export, title="t", now=_NOW)
    assert rendered.frontmatter["tags"] == expected


def test_tag_normalisation_deduplicates_preserving_first_occurrence() -> None:
    export = ChatExport(tldr=["ok"], tags=["x", "x", "y"])
    rendered = render_chat_export(export, title="t", now=_NOW)
    assert rendered.frontmatter["tags"] == ["x", "y"]


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


# --- Block-start hazards in list/ordered/timeline/definitions rendering ------
#
# A bullet or numbered prefix does not, by itself, stop a client value from
# opening a *nested* block inside that list item (CommonMark list items may
# contain arbitrary block content). Verified against markdown-it-py during
# design (not a project dependency): "- # forged" renders as a real nested
# <h1>, "<script>" opens an unclosed HTML block that swallows every
# following heading, "- [ref]: url" disappears as a link reference
# definition, and "- [ ] task" becomes a task-list checkbox. Each test below
# both asserts the escaped literal text and that the fixed headings that
# follow survive as real "## " lines.


def test_decisions_item_starting_with_hash_cannot_forge_a_nested_heading() -> None:
    export = _build("summary", decisions=["# forged"])
    rendered = render_chat_export(export, title="t", now=_NOW)
    assert "- \\# forged" in rendered.content
    assert _heading_lines(rendered.content)[:2] == ["## 要約", "## 決定事項"]


def test_steps_item_that_is_a_bare_code_fence_is_escaped() -> None:
    export = _build("procedure", steps=["```", "do it"])
    rendered = render_chat_export(export, title="t", now=_NOW)
    assert "1. \\```" in rendered.content
    assert "2. do it" in rendered.content
    assert "## 検証" in _heading_lines(rendered.content)


def test_facts_item_starting_with_blockquote_marker_is_escaped() -> None:
    export = _build("reference", facts=["> quote"])
    rendered = render_chat_export(export, title="t", now=_NOW)
    assert "- \\> quote" in rendered.content
    assert "## 例" in _heading_lines(rendered.content)


def test_tldr_item_that_opens_an_html_block_is_escaped() -> None:
    export = ChatExport(mode="summary", tldr=["<script>"])
    rendered = render_chat_export(export, title="t", now=_NOW)
    assert "\\<script>" in rendered.content
    # The whole point: an unclosed <script> HTML block would otherwise
    # swallow every following heading as raw HTML block content.
    assert "## 決定事項" in _heading_lines(rendered.content)
    assert "## 出典" in _heading_lines(rendered.content)


def test_decisions_item_starting_with_task_checkbox_is_escaped() -> None:
    export = _build("summary", decisions=["[ ] task"])
    rendered = render_chat_export(export, title="t", now=_NOW)
    assert "- \\[ ] task" in rendered.content


def test_facts_item_that_looks_like_a_link_reference_definition_is_escaped() -> None:
    export = _build("reference", facts=["[ref]: https://example.com"])
    rendered = render_chat_export(export, title="t", now=_NOW)
    assert "- \\[ref]: https://example.com" in rendered.content


def test_steps_item_starting_with_ordered_marker_escapes_the_punctuation_not_the_digit() -> None:
    # A bare backslash before a digit is not a CommonMark escape and would
    # render literally ("\1. nested") — the punctuation itself must be
    # escaped instead ("1\. nested").
    export = _build("procedure", steps=["1. nested", "second"])
    rendered = render_chat_export(export, title="t", now=_NOW)
    assert "1. 1\\. nested" in rendered.content
    assert "2. second" in rendered.content


def test_control_characters_are_stripped() -> None:
    assert one_line("a\x01b\x02c") == "abc"


def test_internal_ideographic_space_is_preserved_but_edges_are_stripped() -> None:
    assert one_line("A　B") == "A　B"
    assert one_line("　A　") == "A"
    assert one_line("　") == ""


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


# --- Related-note wikilink target predicate (issue #13) -------------------------


@pytest.mark.parametrize(
    "relative_path",
    [
        "Knowledge/has[bracket].md",
        "Knowledge/has]bracket.md",
        "Knowledge/has|pipe.md",
        "Knowledge/has#hash.md",
        "Knowledge/has^caret.md",
        "Knowledge/Foo.md.md",
        "Knowledge/note.txt",
        ".md",
        "Knowledge/line\nbreak.md",
        "Knowledge/control\x00char.md",
    ],
)
def test_is_renderable_wikilink_target_rejects_hazards(relative_path: str) -> None:
    assert is_renderable_wikilink_target(relative_path) is False


@pytest.mark.parametrize(
    "relative_path",
    ["Knowledge/PC/GPU/RTX 5070.md", "Knowledge/GPU比較.md", "note.md"],
)
def test_is_renderable_wikilink_target_accepts_ordinary_paths(relative_path: str) -> None:
    assert is_renderable_wikilink_target(relative_path) is True


def test_format_wikilink_strips_the_md_suffix() -> None:
    assert format_wikilink("Knowledge/PC/GPU/RTX 5070.md") == "[[Knowledge/PC/GPU/RTX 5070]]"


def test_related_notes_section_drops_a_hazardous_link_defensively() -> None:
    # render_chat_export re-filters verified_related_notes with the same
    # predicate, so it cannot emit a corrupt "]]" even if a future caller
    # skips verification — this is a defensive re-check, not the primary
    # guard (that lives in app.services.related_notes).
    export = _build("summary")
    rendered = render_chat_export(
        export, title="t", now=_NOW, verified_related_notes=["Knowledge/has|pipe.md"]
    )
    assert "## 関連ノート\n\nなし" in rendered.content


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


def test_related_notes_accepts_up_to_the_maximum() -> None:
    export = ChatExport(tldr=["ok"], related_notes=[f"Knowledge/{i}.md" for i in range(10)])
    assert len(export.related_notes) == 10


def test_related_notes_over_the_maximum_is_rejected() -> None:
    with pytest.raises(PydanticValidationError):
        ChatExport(tldr=["ok"], related_notes=[f"Knowledge/{i}.md" for i in range(11)])


# --- Field-owner-mode consistency (drives the MCP schema description test) ----


def test_field_owner_modes_is_derived_correctly_from_mode_sections() -> None:
    assert _FIELD_OWNER_MODES["verification"] == ("technical", "procedure")
    assert _FIELD_OWNER_MODES["steps"] == ("procedure",)
    assert _FIELD_OWNER_MODES["topics"] == ("full",)
    assert set(_FIELD_OWNER_MODES) == set(_ALL_MODE_FIELDS_IN_ORDER)


# =================================================================================
# Verbatim/structure-preserving code content (docs/adr/0009-*.md)
#
# The contract is verbatim/structure-preserving, not byte-level lossless: see
# _canonicalise_code's own docstring for the three canonicalisations this
# module still applies (CRLF/CR -> LF, non-tab/newline control-character
# stripping, at-most-one trailing newline). Every "preservation" test below
# therefore compares against _canonicalise_code(input) + "\n" — the fence
# token's content, not the raw input — never the raw Markdown string
# (procedure steps add list-item indentation the fence content itself never
# carries).
# =================================================================================


def _fence_tokens(markdown: str) -> list:
    return [token for token in _MD.parse(markdown) if token.type == "fence"]


def _single_fence_content_for(step_blocks: list[dict]) -> tuple[str, str]:
    """Render one procedure step from ``step_blocks``, parse the result, and
    return the sole fence token's ``(content, info)``. Fails loudly if the
    step did not render to exactly one fence."""
    export = ChatExport(mode="procedure", tldr=["ok"], steps=[{"blocks": step_blocks}])
    rendered = render_chat_export(export, title="t", now=_NOW)
    fences = _fence_tokens(rendered.content)
    assert len(fences) == 1, rendered.content
    return fences[0].content, fences[0].info


# --- Schema: TextBlock / CodeBlock / ProcedureStep -----------------------------


def test_text_and_code_blocks_are_accepted_in_a_procedure_step() -> None:
    export = ChatExport(
        mode="procedure",
        tldr=["ok"],
        steps=[
            {
                "blocks": [
                    {"type": "text", "content": "open it"},
                    {"type": "code", "language": "bash", "content": "ls"},
                ]
            }
        ],
    )
    assert export.steps[0].blocks[0].content == "open it"
    assert export.steps[0].blocks[1].content == "ls"


def test_code_block_language_and_label_default_to_none() -> None:
    export = ChatExport(
        mode="procedure",
        tldr=["ok"],
        steps=[{"blocks": [{"type": "text", "content": "a"}, {"type": "code", "content": "x"}]}],
    )
    code = export.steps[0].blocks[1]
    assert code.language is None
    assert code.label is None


def test_text_block_rejects_unknown_field() -> None:
    with pytest.raises(PydanticValidationError):
        ChatExport(
            mode="procedure",
            tldr=["ok"],
            steps=[{"blocks": [{"type": "text", "content": "a", "bogus": 1}]}],
        )


def test_code_block_rejects_unknown_field() -> None:
    with pytest.raises(PydanticValidationError):
        ChatExport(
            mode="procedure",
            tldr=["ok"],
            steps=[
                {
                    "blocks": [
                        {"type": "text", "content": "a"},
                        {"type": "code", "content": "x", "bogus": 1},
                    ]
                }
            ],
        )


def test_procedure_step_rejects_unknown_field() -> None:
    with pytest.raises(PydanticValidationError):
        ChatExport(
            mode="procedure",
            tldr=["ok"],
            steps=[{"blocks": [{"type": "text", "content": "a"}], "bogus": 1}],
        )


@pytest.mark.parametrize(
    "language",
    ["ba sh", "bash\n", "bash\r", "bash`", "", "a" * 33, "b@sh", "\x00bash", " bash"],
)
def test_invalid_language_is_rejected_at_schema_level(language: str) -> None:
    with pytest.raises(PydanticValidationError):
        ChatExport(
            mode="procedure",
            tldr=["ok"],
            steps=[
                {
                    "blocks": [
                        {"type": "text", "content": "a"},
                        {"type": "code", "language": language, "content": "x"},
                    ]
                }
            ],
        )


@pytest.mark.parametrize(
    "language", ["bash", "yaml", "json", "c++", "c#", "shell-session", "text", "Dockerfile"]
)
def test_valid_language_examples_are_accepted(language: str) -> None:
    export = ChatExport(
        mode="procedure",
        tldr=["ok"],
        steps=[
            {
                "blocks": [
                    {"type": "text", "content": "a"},
                    {"type": "code", "language": language, "content": "x"},
                ]
            }
        ],
    )
    assert export.steps[0].blocks[1].language == language


def test_empty_blocks_list_is_rejected_at_schema_level() -> None:
    with pytest.raises(PydanticValidationError):
        ChatExport(mode="procedure", tldr=["ok"], steps=[{"blocks": []}])


def test_missing_blocks_is_rejected_at_schema_level() -> None:
    with pytest.raises(PydanticValidationError):
        ChatExport(mode="procedure", tldr=["ok"], steps=[{}])


def test_empty_code_content_is_rejected_at_schema_level() -> None:
    with pytest.raises(PydanticValidationError):
        ChatExport(
            mode="procedure",
            tldr=["ok"],
            steps=[
                {
                    "blocks": [
                        {"type": "text", "content": "a"},
                        {"type": "code", "content": ""},
                    ]
                }
            ],
        )


def test_whitespace_only_code_content_passes_schema_but_is_dropped_by_formatter() -> None:
    export = ChatExport(
        mode="procedure",
        tldr=["ok"],
        steps=[
            {
                "blocks": [
                    {"type": "text", "content": "a"},
                    {"type": "code", "content": "   \n\t \n  "},
                ]
            }
        ],
    )
    rendered = render_chat_export(export, title="t", now=_NOW)
    assert "```" not in rendered.content


def test_empty_text_content_passes_schema_and_is_dropped_like_a_legacy_string_step() -> None:
    # The one deliberate asymmetry (docs/adr/0009-*.md): TextBlock.content has
    # no min_length, unlike CodeBlock.content, because a legacy plain-string
    # step ("" or whitespace-only) must keep being silently dropped exactly as
    # it was before this feature existed — see steps=["", "second"] below.
    export = ChatExport(mode="procedure", tldr=["ok"], steps=["", "second"])
    rendered = render_chat_export(export, title="t", now=_NOW)
    assert "1. second" in rendered.content


def test_code_content_over_max_length_is_rejected_at_schema_level() -> None:
    with pytest.raises(PydanticValidationError):
        ChatExport(
            mode="procedure",
            tldr=["ok"],
            steps=[
                {
                    "blocks": [
                        {"type": "text", "content": "a"},
                        {"type": "code", "content": "x" * 8001},
                    ]
                }
            ],
        )


def test_code_content_at_max_length_is_accepted_at_schema_level() -> None:
    export = ChatExport(
        mode="procedure",
        tldr=["ok"],
        steps=[
            {
                "blocks": [
                    {"type": "text", "content": "a"},
                    {"type": "code", "content": "x" * 8000},
                ]
            }
        ],
    )
    assert len(export.steps[0].blocks[1].content) == 8000


def test_blocks_over_max_per_step_is_rejected_at_schema_level() -> None:
    blocks = [{"type": "text", "content": "a"}] + [
        {"type": "code", "content": f"x{i}"} for i in range(12)
    ]
    with pytest.raises(PydanticValidationError):
        ChatExport(mode="procedure", tldr=["ok"], steps=[{"blocks": blocks}])


def test_code_blocks_over_max_items_is_rejected_at_schema_level() -> None:
    with pytest.raises(PydanticValidationError):
        ChatExport(
            tldr=["ok"],
            code_blocks=[{"type": "code", "content": f"x{i}"} for i in range(11)],
        )


def test_code_blocks_at_max_items_is_accepted_at_schema_level() -> None:
    export = ChatExport(
        tldr=["ok"], code_blocks=[{"type": "code", "content": f"x{i}"} for i in range(10)]
    )
    assert len(export.code_blocks) == 10


# --- Backward compatibility: legacy plain-string steps -------------------------


def test_legacy_string_step_is_equivalent_to_a_single_text_block() -> None:
    export_string = ChatExport(mode="procedure", tldr=["ok"], steps=["do it"])
    export_object = ChatExport(
        mode="procedure",
        tldr=["ok"],
        steps=[{"blocks": [{"type": "text", "content": "do it"}]}],
    )
    a = render_chat_export(export_string, title="t", now=_NOW)
    b = render_chat_export(export_object, title="t", now=_NOW)
    assert a == b


def test_multiple_steps_mix_legacy_strings_and_rich_objects() -> None:
    export = ChatExport(
        mode="procedure",
        tldr=["ok"],
        steps=[
            "plain first",
            {"blocks": [{"type": "text", "content": "second"}, {"type": "code", "content": "x"}]},
            "plain third",
        ],
    )
    rendered = render_chat_export(export, title="t", now=_NOW)
    assert "1. plain first" in rendered.content
    assert "2. second" in rendered.content
    assert "3. plain third" in rendered.content
    tokens = _MD.parse(rendered.content)
    assert sum(1 for t in tokens if t.type == "ordered_list_open") == 1
    assert sum(1 for t in tokens if t.type == "list_item_open") == 3


# --- Ordering: a single step with several interleaved text/code blocks -------


def test_one_step_with_text_and_code_alternating_several_times_preserves_order() -> None:
    export = ChatExport(
        mode="procedure",
        tldr=["ok"],
        steps=[
            {
                "blocks": [
                    {"type": "text", "content": "a"},
                    {"type": "code", "content": "1"},
                    {"type": "text", "content": "b"},
                    {"type": "code", "content": "2"},
                    {"type": "text", "content": "c"},
                    {"type": "code", "content": "3"},
                ]
            }
        ],
    )
    rendered = render_chat_export(export, title="t", now=_NOW)
    tokens = _MD.parse(rendered.content)

    assert sum(1 for t in tokens if t.type == "ordered_list_open") == 1
    assert sum(1 for t in tokens if t.type == "list_item_open") == 1

    fences = [t for t in tokens if t.type == "fence"]
    assert [f.content for f in fences] == ["1\n", "2\n", "3\n"]

    inline_lines = {
        "".join(child.content for child in (t.children or [])): t.map[0]
        for t in tokens
        if t.type == "inline"
    }
    fence_lines = [f.map[0] for f in fences]
    assert inline_lines["a"] < fence_lines[0]
    assert fence_lines[0] < inline_lines["b"] < fence_lines[1]
    assert fence_lines[1] < inline_lines["c"] < fence_lines[2]


# --- Structural rejection: a step must start with a text block ----------------


def test_code_first_step_is_rejected() -> None:
    export = ChatExport(
        mode="procedure", tldr=["ok"], steps=[{"blocks": [{"type": "code", "content": "x"}]}]
    )
    with pytest.raises(ValidationError) as excinfo:
        render_chat_export(export, title="t", now=_NOW)
    assert excinfo.value.message == "steps[0] must start with a text block."


def test_code_first_step_error_identifies_the_right_index() -> None:
    export = ChatExport(
        mode="procedure",
        tldr=["ok"],
        steps=["fine", {"blocks": [{"type": "code", "content": "x"}]}],
    )
    with pytest.raises(ValidationError) as excinfo:
        render_chat_export(export, title="t", now=_NOW)
    assert excinfo.value.message == "steps[1] must start with a text block."


def test_step_that_normalises_to_only_a_code_block_is_rejected() -> None:
    # A text block that drops out during normalisation (whitespace-only)
    # leaves the step starting with code — this must be caught after
    # normalisation, not only against the raw schema shape.
    export = ChatExport(
        mode="procedure",
        tldr=["ok"],
        steps=[
            {
                "blocks": [
                    {"type": "text", "content": "\n"},
                    {"type": "code", "content": "x"},
                ]
            }
        ],
    )
    with pytest.raises(ValidationError) as excinfo:
        render_chat_export(export, title="t", now=_NOW)
    assert excinfo.value.message == "steps[0] must start with a text block."


# --- Total block-size budget (docs/adr/0011-*.md, superseding docs/adr/
# 0009-*.md's code-only budget; app/models._MAX_TOTAL_BLOCK_CHARS) ------------


def test_total_code_content_within_the_budget_is_accepted() -> None:
    export = ChatExport(
        mode="procedure",
        tldr=["ok"],
        code_blocks=[{"type": "code", "content": "x" * 8000} for _ in range(10)],
        steps=["s"],
    )
    render_chat_export(export, title="t", now=_NOW)  # must not raise


def test_total_code_content_over_the_budget_is_rejected() -> None:
    blocks = [{"type": "text", "content": "a"}] + [
        {"type": "code", "content": "z" * 8000} for _ in range(3)
    ]
    export = ChatExport(
        mode="procedure",
        tldr=["ok"],
        code_blocks=[{"type": "code", "content": "x" * 8000} for _ in range(10)],
        steps=[{"blocks": blocks}],
    )
    with pytest.raises(ValidationError) as excinfo:
        render_chat_export(export, title="t", now=_NOW)
    assert excinfo.value.message == (
        f"Block content exceeds the total limit of {_MAX_TOTAL_BLOCK_CHARS} characters."
    )


def test_total_code_content_error_never_echoes_client_code() -> None:
    secret = ("SECRET_TOKEN=abc123" * 421)[:8000]  # exactly at the per-block max
    blocks = [{"type": "text", "content": "a"}] + [
        {"type": "code", "content": secret} for _ in range(3)
    ]
    export = ChatExport(
        mode="procedure",
        tldr=["ok"],
        code_blocks=[{"type": "code", "content": secret} for _ in range(10)],
        steps=[{"blocks": blocks}],
    )
    with pytest.raises(ValidationError) as excinfo:
        render_chat_export(export, title="t", now=_NOW)
    assert "SECRET_TOKEN" not in excinfo.value.message
    assert excinfo.value.log_detail is not None
    assert "SECRET_TOKEN" not in excinfo.value.log_detail


def _build_export_with_total_code_chars(total: int) -> ChatExport:
    """Build an export whose normalised code content sums to exactly
    ``total`` characters: 10 top-level code_blocks at the maximum
    _MAX_CODE_CHARS each (80,000 — app.models._MAX_CODE_BLOCK_ITEMS x
    _MAX_CODE_CHARS), plus as many 8,000-char code blocks in a single step
    as needed for the remainder — never exceeding _MAX_BLOCKS_PER_STEP or
    _MAX_CODE_CHARS per block.
    """
    remainder = total - 80_000
    blocks = [{"type": "text", "content": "a"}]
    while remainder > 0:
        chunk = min(remainder, 8_000)
        blocks.append({"type": "code", "content": "y" * chunk})
        remainder -= chunk
    return ChatExport(
        mode="procedure",
        tldr=["ok"],
        code_blocks=[{"type": "code", "content": "x" * 8_000} for _ in range(10)],
        steps=[{"blocks": blocks}],
    )


def test_total_code_content_at_exactly_the_budget_is_accepted() -> None:
    # Pins the implementation's `>` (not `>=`) comparison: the limit itself
    # is an accepted amount, not a rejected one.
    export = _build_export_with_total_code_chars(_MAX_TOTAL_BLOCK_CHARS)
    render_chat_export(export, title="t", now=_NOW)  # must not raise


def test_total_code_content_one_char_over_the_budget_is_rejected() -> None:
    export = _build_export_with_total_code_chars(_MAX_TOTAL_BLOCK_CHARS + 1)
    with pytest.raises(ValidationError) as excinfo:
        render_chat_export(export, title="t", now=_NOW)
    assert excinfo.value.message == (
        f"Block content exceeds the total limit of {_MAX_TOTAL_BLOCK_CHARS} characters."
    )


# --- Canonicalisation boundary: what _canonicalise_code changes, and only that -


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("a\r\nb\r\n", "a\nb"),
        ("a\rb\r", "a\nb"),
        ("a\n", "a"),
        ("a\n\n", "a\n"),  # a deliberate trailing blank line is preserved
        ("a\n\n\n", "a\n\n"),
        ("a\x01b\x1fc", "abc"),
        ("a\tb\nc", "a\tb\nc"),  # tab and newline both survive
        ("\nleading blank\nafter", "\nleading blank\nafter"),
    ],
)
def test_canonicalise_code_boundary_cases(raw: str, expected: str) -> None:
    assert _canonicalise_code(raw) == expected


@pytest.mark.parametrize(
    "raw_code",
    [
        "  indented\n\tmixed tab\nplain",
        "line1\n\nline3",
        "trailing space   \nnext",
        "日本語 コメント # not a heading\n- not a bullet\n> not a quote",
        "environment:\n  FOO: bar\n  BAZ:\n    - one\n    - two",
        '{\n  "a": 1,\n  "b": [1, 2, 3]\n}',
        "--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-old\n+new",
        "2026-08-09 12:00:00 INFO starting\n2026-08-09 12:00:01 ERROR boom",
        "$ ls -la\ntotal 0\ndrwxr-xr-x",
        "絵文字😀テスト、日本語",
        "< > [ ] # - * _ special",
        "back`tick`s",
    ],
)
def test_code_content_is_preserved_up_to_canonicalisation(raw_code: str) -> None:
    content, _info = _single_fence_content_for(
        [{"type": "text", "content": "a"}, {"type": "code", "content": raw_code}]
    )
    assert content == _canonicalise_code(raw_code) + "\n"


# --- Dynamic fence length -------------------------------------------------------


@pytest.mark.parametrize(
    ("content", "expected_fence"),
    [
        ("plain", "```"),
        ("`x`", "```"),
        ("``y``", "```"),
        ("```z```", "````"),
        ("````w````", "`````"),
        ("`````v`````", "``````"),
    ],
)
def test_fence_for_is_one_longer_than_the_longest_backtick_run(
    content: str, expected_fence: str
) -> None:
    assert _fence_for(content) == expected_fence


@pytest.mark.parametrize(
    "content",
    ["contains ` one backtick", "contains `` two", "contains ``` three", "contains ```` four"],
)
def test_fence_never_closes_early_for_embedded_backtick_runs(content: str) -> None:
    fenced_content, _info = _single_fence_content_for(
        [{"type": "text", "content": "a"}, {"type": "code", "content": content}]
    )
    assert fenced_content == _canonicalise_code(content) + "\n"


# --- Defensive re-check: an unsafe language bypassing pydantic ----------------
#
# _is_safe_language's rejection path is unreachable through the public
# ChatExport API — pydantic's Field(pattern=_LANGUAGE_PATTERN) already
# guarantees a safe value before a _NormalisedCodeBlock can exist. This
# constructs one directly, bypassing pydantic entirely, the same approach
# test_related_notes_section_drops_a_hazardous_link_defensively uses for
# is_renderable_wikilink_target's own defensive re-check.


def test_render_fenced_code_omits_info_string_for_a_bypassed_unsafe_language() -> None:
    unsafe = _NormalisedCodeBlock(language="not safe\n", label=None, content="x")
    safe = _NormalisedCodeBlock(language="bash", label=None, content="x")
    assert _render_fenced_code(unsafe, indent="")[0] == "```"
    assert _render_fenced_code(safe, indent="")[0] == "```bash"


# --- Label / caption: plain, literal, never a Markdown heading or emphasis ----


@pytest.mark.parametrize(
    "label",
    [
        "compose.yaml",
        "docker-compose.yml",
        "起動コマンド",
        "**bold**",
        "`cmd`",
        "[l](u)",
        "# head",
        "1. item",
        "a_b_c",
        "<tag>",
        "~~s~~",
        "a\\b",
        "- dash",
        "> q",
        "+ p",
    ],
)
def test_caption_renders_as_literal_inline_text(label: str) -> None:
    export = ChatExport(
        mode="procedure",
        tldr=["ok"],
        steps=[
            {
                "blocks": [
                    {"type": "text", "content": "a"},
                    {"type": "code", "label": label, "content": "x"},
                ]
            }
        ],
    )
    rendered = render_chat_export(export, title="t", now=_NOW)
    tokens = _MD.parse(rendered.content)
    caption_inlines = [
        t
        for t in tokens
        if t.type == "inline"
        and "".join(c.content for c in (t.children or []) if c.type == "text") == label
    ]
    assert caption_inlines, f"label {label!r} did not round-trip as literal inline text"
    for inline in caption_inlines:
        kinds = {child.type for child in inline.children or []}
        assert kinds == {"text"}, f"label {label!r} rendered as non-literal token kinds {kinds}"


@pytest.mark.parametrize(
    ("label", "obsidian_marker"),
    [
        ("C#", "#"),
        ("^blockid", "^"),
        ("==highlight==", "="),
        ("$math$", "$"),
        ("%%comment%%", "%"),
        ("[[wikilink]]", "["),
    ],
)
def test_caption_escapes_obsidian_specific_inline_markers(
    label: str, obsidian_marker: str
) -> None:
    # markdown-it-py cannot detect Obsidian-specific inline semantics (tags,
    # block IDs, highlight, math, comments, wikilinks/embeds) — this asserts
    # the fixed character set directly rather than through a CommonMark
    # parse, and docs/adr/0009-*.md records that this is a constructional
    # guarantee, not something the test suite can verify by parsing.
    escaped = _escape_inline(label)
    assert f"\\{obsidian_marker}" in escaped
    assert obsidian_marker in _INLINE_ESCAPE_CHARS


def test_caption_for_ordinary_filenames_is_unescaped() -> None:
    for label in ["compose.yaml", "docker-compose.yml", "起動コマンド", "実行結果"]:
        assert _escape_inline(label) == label


def test_blank_label_produces_no_caption_line() -> None:
    export = ChatExport(
        mode="procedure",
        tldr=["ok"],
        steps=[
            {
                "blocks": [
                    {"type": "text", "content": "a"},
                    {"type": "code", "label": "   ", "content": "x"},
                ]
            }
        ],
    )
    rendered = render_chat_export(export, title="t", now=_NOW)
    assert "```" in rendered.content
    lines = rendered.content.splitlines()
    fence_line = next(line for line in lines if line.strip().startswith("```"))
    caption_candidate = lines[lines.index(fence_line) - 1]
    assert caption_candidate == ""  # a blank line, not a caption, precedes the fence


# --- Renderer structure: step numbering never breaks (markdown-it-py) --------


def test_step_ten_and_beyond_keeps_a_single_ordered_list_with_correct_numbering() -> None:
    # _MAX_STEP_ITEMS allows up to 50 steps; step 10 onward has a 4-character
    # marker ("10. "). A fixed 3-space continuation indent would put a code
    # fence outside the list item (verified against markdown-it-py during
    # design), splitting the list and renumbering everything after it.
    steps = [{"blocks": [{"type": "text", "content": f"s{i}"}]} for i in range(1, 10)]
    steps.append(
        {
            "blocks": [
                {"type": "text", "content": "s10"},
                {"type": "code", "content": "x"},
            ]
        }
    )
    steps.append({"blocks": [{"type": "text", "content": "s11"}]})
    export = ChatExport(mode="procedure", tldr=["ok"], steps=steps)
    rendered = render_chat_export(export, title="t", now=_NOW)

    tokens = _MD.parse(rendered.content)
    assert sum(1 for t in tokens if t.type == "ordered_list_open") == 1
    assert sum(1 for t in tokens if t.type == "list_item_open") == 11

    item_index = 0
    fence_item_index = None
    for token in tokens:
        if token.type == "list_item_open":
            item_index += 1
        elif token.type == "fence":
            fence_item_index = item_index
    assert fence_item_index == 10


def test_code_in_a_step_produces_a_fence_not_an_indented_code_block() -> None:
    export = ChatExport(
        mode="procedure",
        tldr=["ok"],
        steps=[{"blocks": [{"type": "text", "content": "a"}, {"type": "code", "content": "x"}]}],
    )
    rendered = render_chat_export(export, title="t", now=_NOW)
    tokens = _MD.parse(rendered.content)
    assert sum(1 for t in tokens if t.type == "code_block") == 0
    assert sum(1 for t in tokens if t.type == "fence") == 1


def test_text_after_code_within_a_step_stays_in_the_same_list_item() -> None:
    export = ChatExport(
        mode="procedure",
        tldr=["ok"],
        steps=[
            {
                "blocks": [
                    {"type": "text", "content": "before"},
                    {"type": "code", "content": "x"},
                    {"type": "text", "content": "after"},
                ]
            },
            "next step",
        ],
    )
    rendered = render_chat_export(export, title="t", now=_NOW)
    tokens = _MD.parse(rendered.content)
    assert sum(1 for t in tokens if t.type == "ordered_list_open") == 1
    assert sum(1 for t in tokens if t.type == "list_item_open") == 2

    item_index = 0
    after_item_index = None
    for token in tokens:
        if token.type == "list_item_open":
            item_index += 1
        elif token.type == "inline":
            text = "".join(child.content for child in (token.children or []))
            if text == "after":
                after_item_index = item_index
    assert after_item_index == 1


# --- Regression: no code_blocks input renders exactly as before this feature --


@pytest.mark.parametrize("mode", list(_MODE_SECTIONS))
def test_no_code_blocks_input_never_renders_a_code_heading(mode: str) -> None:
    export = _build(mode)
    rendered = render_chat_export(export, title="t", now=_NOW)
    assert "## コード" not in rendered.content


def test_top_level_code_blocks_that_all_normalise_to_empty_omit_the_heading() -> None:
    # Distinct from test_no_code_blocks_input_never_renders_a_code_heading
    # above: here code_blocks is non-empty in the *raw* input, but every
    # entry drops to nothing after normalisation (whitespace-only content)
    # — the same "normalised to empty -> section omitted" outcome must still
    # hold, not just "never emit a placeholder-only section".
    export = ChatExport(tldr=["ok"], code_blocks=[{"type": "code", "content": "   \n\t  "}])
    rendered = render_chat_export(export, title="t", now=_NOW)
    assert "## コード" not in rendered.content


def test_code_blocks_section_appears_between_mode_fields_and_unresolved_issues() -> None:
    export = _build("summary", code_blocks=[{"type": "code", "content": "x"}])
    rendered = render_chat_export(export, title="t", now=_NOW)
    headings = _heading_lines(rendered.content)
    assert headings[0] == "## 要約"
    assert headings[1] == "## 決定事項"
    assert headings[-4:] == ["## 未解決の論点", "## 次のアクション", "## 関連ノート", "## 出典"]
    assert "## コード" in headings
    assert headings.index("## コード") > headings.index("## 要点")
    assert headings.index("## コード") < headings.index("## 未解決の論点")


def test_top_level_code_blocks_render_outside_any_list() -> None:
    export = ChatExport(
        tldr=["ok"],
        code_blocks=[
            {"type": "code", "language": "yaml", "label": "compose.yaml", "content": "a: b"},
            {"type": "code", "content": "second"},
        ],
    )
    rendered = render_chat_export(export, title="t", now=_NOW)
    tokens = _MD.parse(rendered.content)
    assert sum(1 for t in tokens if t.type == "list_item_open") == 0
    fences = [t for t in tokens if t.type == "fence"]
    assert [(f.content, f.info) for f in fences] == [("a: b\n", "yaml"), ("second\n", "")]


def test_top_level_code_blocks_available_outside_procedure_mode() -> None:
    export = _build("summary", code_blocks=[{"type": "code", "content": "x"}])
    render_chat_export(export, title="t", now=_NOW)  # must not raise


def test_procedure_step_code_is_never_moved_into_the_top_level_section() -> None:
    export = ChatExport(
        mode="procedure",
        tldr=["ok"],
        steps=[
            {"blocks": [{"type": "text", "content": "a"}, {"type": "code", "content": "step-code"}]}
        ],
    )
    rendered = render_chat_export(export, title="t", now=_NOW)
    assert "## コード" not in rendered.content
    assert "step-code" in rendered.content


# --- Existing golden-output pins must still hold byte-for-byte -----------------


def test_procedure_with_plain_steps_still_renders_the_pre_existing_numbered_list() -> None:
    export = _build("procedure", steps=["first", "second", "third"])
    rendered = render_chat_export(export, title="t", now=_NOW)
    assert "1. first\n2. second\n3. third" in rendered.content


# === Rich body blocks (docs/adr/0011-*.md) ====================================

# --- Backward compatibility: every body field still accepts plain strings -----


@pytest.mark.parametrize("mode", list(_MODE_SECTIONS))
def test_plain_string_body_fields_render_byte_identically_across_every_mode(
    mode: str,
) -> None:
    # A dedicated, explicit pin alongside the pre-existing golden-output
    # tests above (which already cover this implicitly): every mode-specific
    # plain-list field still renders as ordinary "- " bullets with no
    # rich-block artifact, whether or not this feature exists.
    extra = {
        field_name: ["a", "b"]
        for field_name in _MODE_SECTIONS[mode]
        if field_name not in ("steps", "timeline", "topics", "definitions")
    }
    export = _build(mode, **extra) if extra else _build(mode)
    rendered = render_chat_export(export, title="t", now=_NOW)
    for field_name in extra:
        heading = "## " + {
            "overview": "概要", "key_points": "要点", "context": "背景", "design": "設計",
            "implementation_notes": "実装メモ", "verification": "検証",
            "turning_points": "転換点", "prerequisites": "前提条件", "rollback": "ロールバック",
            "symptom": "症状", "environment": "環境", "investigation": "調査",
            "root_cause": "原因", "workaround": "回避策", "facts": "事実", "examples": "例",
        }[field_name]
        assert f"{heading}\n\n- a\n- b" in rendered.content


# --- Schema: TableBlock ---------------------------------------------------------


def test_table_block_rejects_unknown_field() -> None:
    with pytest.raises(PydanticValidationError):
        ChatExport(
            tldr=["ok"],
            design=[{"type": "table", "headers": ["a"], "rows": [], "bogus": 1}],
        )


def test_table_headers_empty_list_is_rejected_at_schema_level() -> None:
    with pytest.raises(PydanticValidationError):
        ChatExport(tldr=["ok"], design=[{"type": "table", "headers": [], "rows": []}])


def test_table_headers_over_max_columns_is_rejected_at_schema_level() -> None:
    with pytest.raises(PydanticValidationError):
        ChatExport(
            tldr=["ok"],
            design=[{"type": "table", "headers": [f"h{i}" for i in range(13)], "rows": []}],
        )


def test_table_headers_at_max_columns_is_accepted() -> None:
    export = ChatExport(
        mode="technical",
        tldr=["ok"],
        design=[{"type": "table", "headers": [f"h{i}" for i in range(12)], "rows": []}],
    )
    render_chat_export(export, title="t", now=_NOW)  # must not raise


def test_table_row_over_max_columns_is_rejected_at_schema_level() -> None:
    with pytest.raises(PydanticValidationError):
        ChatExport(
            tldr=["ok"],
            design=[
                {
                    "type": "table",
                    "headers": ["a"],
                    "rows": [[str(i) for i in range(13)]],
                }
            ],
        )


def test_table_invalid_alignment_value_is_rejected_at_schema_level() -> None:
    with pytest.raises(PydanticValidationError):
        ChatExport(
            tldr=["ok"],
            design=[
                {"type": "table", "headers": ["a"], "alignments": ["diagonal"], "rows": []}
            ],
        )


# --- Formatter: a table never silently degrades — it renders exactly or raises -


def test_table_header_empty_after_normalisation_is_rejected() -> None:
    export = ChatExport(
        mode="technical",
        tldr=["ok"],
        design=[{"type": "table", "headers": ["a", "   "], "rows": []}],
    )
    with pytest.raises(ValidationError) as excinfo:
        render_chat_export(export, title="t", now=_NOW)
    assert excinfo.value.message == "design[0]: table header 1 must not be empty."


def test_table_alignments_length_mismatch_is_rejected() -> None:
    export = ChatExport(
        tldr=["ok"],
        design=[{"type": "table", "headers": ["a", "b"], "alignments": ["left"], "rows": []}],
    )
    with pytest.raises(ValidationError) as excinfo:
        render_chat_export(export, title="t", now=_NOW)
    assert excinfo.value.message == (
        "design[0]: table alignments must match the number of headers."
    )


@pytest.mark.parametrize("row", [["1"], ["1", "2", "3"]])
def test_table_row_length_mismatch_is_rejected(row: list[str]) -> None:
    export = ChatExport(
        mode="technical",
        tldr=["ok"],
        design=[{"type": "table", "headers": ["a", "b"], "rows": [row]}],
    )
    with pytest.raises(ValidationError) as excinfo:
        render_chat_export(export, title="t", now=_NOW)
    assert excinfo.value.message == (
        f"design[0]: table row 0 has {len(row)} cells, expected 2."
    )


def test_table_row_length_mismatch_error_never_echoes_client_cell_values() -> None:
    export = ChatExport(
        tldr=["ok"],
        design=[{"type": "table", "headers": ["a"], "rows": [["SECRET_TOKEN", "extra"]]}],
    )
    with pytest.raises(ValidationError) as excinfo:
        render_chat_export(export, title="t", now=_NOW)
    assert "SECRET_TOKEN" not in excinfo.value.message
    assert excinfo.value.log_detail is not None
    assert "SECRET_TOKEN" not in excinfo.value.log_detail


def test_table_with_zero_rows_is_accepted() -> None:
    export = ChatExport(
        mode="technical", tldr=["ok"], design=[{"type": "table", "headers": ["a", "b"], "rows": []}]
    )
    render_chat_export(export, title="t", now=_NOW)  # must not raise


def test_table_row_with_empty_cell_is_accepted() -> None:
    export = ChatExport(
        mode="technical",
        tldr=["ok"],
        design=[{"type": "table", "headers": ["a", "b"], "rows": [["1", ""]]}],
    )
    render_chat_export(export, title="t", now=_NOW)  # must not raise


# --- Structure: bullets and tables as siblings, never nested -------------------


def test_bullet_table_bullet_render_as_sibling_blocks_in_one_field() -> None:
    export = ChatExport(
        mode="technical",
        tldr=["ok"],
        design=[
            "a",
            "b",
            {"type": "table", "headers": ["x", "y"], "rows": [["1", "2"]]},
            "c",
        ],
    )
    rendered = render_chat_export(export, title="t", now=_NOW)
    tokens = _MD_TABLE.parse(rendered.content)
    types = [t.type for t in tokens]
    assert types.count("bullet_list_open") == 2
    assert types.count("table_open") == 1


def test_consecutive_tables_in_one_field_are_separated_by_a_blank_line() -> None:
    export = ChatExport(
        mode="technical",
        tldr=["ok"],
        design=[
            {"type": "table", "headers": ["a"], "rows": [["1"]]},
            {"type": "table", "headers": ["b"], "rows": [["2"]]},
        ],
    )
    rendered = render_chat_export(export, title="t", now=_NOW)
    design_section = rendered.content.split("## 設計\n\n")[1].split("\n\n## ")[0]
    assert "\n\n" in design_section  # the two tables are not concatenated directly
    tokens = _MD_TABLE.parse(rendered.content)
    assert sum(1 for t in tokens if t.type == "table_open") == 2


def test_table_only_field_renders_directly_under_the_heading_with_no_stray_bullet() -> None:
    export = ChatExport(
        mode="technical",
        tldr=["ok"],
        design=[{"type": "table", "headers": ["a"], "rows": [["1"]]}],
    )
    rendered = render_chat_export(export, title="t", now=_NOW)
    design_section = rendered.content.split("## 設計\n\n")[1].split("\n\n## ")[0]
    assert design_section.startswith("| a |")
    assert not any(line.startswith("- ") for line in design_section.splitlines())


def test_topics_points_support_tables() -> None:
    export = ChatExport(
        mode="full",
        tldr=["ok"],
        topics=[
            {
                "heading": "h",
                "points": ["a", {"type": "table", "headers": ["x"], "rows": [["1"]]}],
            }
        ],
    )
    rendered = render_chat_export(export, title="t", now=_NOW)
    tokens = _MD_TABLE.parse(rendered.content)
    assert sum(1 for t in tokens if t.type == "table_open") == 1


# --- Cell escaping and inline formatting ---------------------------------------


def test_table_cell_pipe_is_escaped_and_stays_within_one_column() -> None:
    export = ChatExport(
        mode="technical",
        tldr=["ok"],
        design=[{"type": "table", "headers": ["a"], "rows": [["x|y"]]}],
    )
    rendered = render_chat_export(export, title="t", now=_NOW)
    tokens = _MD_TABLE.parse(rendered.content)
    th_opens = [t for t in tokens if t.type == "th_open"]
    assert len(th_opens) == 1  # one header cell, not split by the unescaped pipe


def test_table_cell_backslash_pipe_round_trips_without_escaping_the_table() -> None:
    raw_cell = "x" + "\\" + "|" + "y"
    export = ChatExport(
        mode="technical",
        tldr=["ok"],
        design=[{"type": "table", "headers": ["a"], "rows": [[raw_cell]]}],
    )
    rendered = render_chat_export(export, title="t", now=_NOW)
    tokens = _MD_TABLE.parse(rendered.content)
    table_opens = [t for t in tokens if t.type == "table_open"]
    assert len(table_opens) == 1
    inline = [t for t in tokens if t.type == "inline"]
    decoded = [
        "".join(child.content for child in (t.children or []))
        for t in inline
        if "x" in "".join(c.content for c in (t.children or []))
        and "y" in "".join(c.content for c in (t.children or []))
    ]
    assert raw_cell in decoded


def test_table_cell_keeps_inline_markdown_live() -> None:
    export = ChatExport(
        mode="technical",
        tldr=["ok"],
        design=[{"type": "table", "headers": ["h"], "rows": [["**bold** `code`"]]}],
    )
    rendered = render_chat_export(export, title="t", now=_NOW)
    tokens = _MD_TABLE.parse(rendered.content)
    cell_inline = [
        t
        for t in tokens
        if t.type == "inline" and any(c.type == "strong_open" for c in (t.children or []))
    ]
    assert len(cell_inline) == 1
    child_types = [c.type for c in cell_inline[0].children]
    assert "strong_open" in child_types
    assert "code_inline" in child_types


# --- Alignment delimiter syntax --------------------------------------------------


@pytest.mark.parametrize(
    ("alignment", "delimiter"),
    [("left", ":---"), ("center", ":---:"), ("right", "---:")],
)
def test_table_alignment_renders_the_correct_delimiter(alignment: str, delimiter: str) -> None:
    export = ChatExport(
        mode="technical",
        tldr=["ok"],
        design=[{"type": "table", "headers": ["a"], "alignments": [alignment], "rows": []}],
    )
    rendered = render_chat_export(export, title="t", now=_NOW)
    assert f"| {delimiter} |" in rendered.content


def test_table_without_alignments_renders_the_unmarked_delimiter() -> None:
    export = ChatExport(
        mode="technical", tldr=["ok"], design=[{"type": "table", "headers": ["a", "b"], "rows": []}]
    )
    rendered = render_chat_export(export, title="t", now=_NOW)
    assert "| --- | --- |" in rendered.content


# --- Table inside a procedure step ----------------------------------------------


def test_table_in_a_step_stays_within_that_step_and_preserves_numbering() -> None:
    steps = [{"blocks": [{"type": "text", "content": f"step {i}"}]} for i in range(1, 11)]
    steps[9] = {
        "blocks": [
            {"type": "text", "content": "step 10"},
            {"type": "table", "headers": ["a"], "rows": [["1"]]},
        ]
    }
    export = ChatExport(mode="procedure", tldr=["ok"], steps=steps)
    rendered = render_chat_export(export, title="t", now=_NOW)
    tokens = _MD_TABLE.parse(rendered.content)
    assert sum(1 for t in tokens if t.type == "ordered_list_open") == 1
    assert sum(1 for t in tokens if t.type == "list_item_open") == 10

    item_index = 0
    table_item_index = None
    for token in tokens:
        if token.type == "list_item_open":
            item_index += 1
        elif token.type == "table_open":
            table_item_index = item_index
    assert table_item_index == 10


def test_table_first_step_is_rejected() -> None:
    export = ChatExport(
        mode="procedure",
        tldr=["ok"],
        steps=[{"blocks": [{"type": "table", "headers": ["a"], "rows": []}]}],
    )
    with pytest.raises(ValidationError) as excinfo:
        render_chat_export(export, title="t", now=_NOW)
    assert excinfo.value.message == "steps[0] must start with a text block."


def test_procedure_step_table_is_never_moved_into_the_top_level_code_section() -> None:
    export = ChatExport(
        mode="procedure",
        tldr=["ok"],
        steps=[
            {
                "blocks": [
                    {"type": "text", "content": "a"},
                    {"type": "table", "headers": ["x"], "rows": [["1"]]},
                ]
            }
        ],
    )
    rendered = render_chat_export(export, title="t", now=_NOW)
    assert "## コード" not in rendered.content


# --- Total block-size budget also counts table and code-label content ---------


def test_table_content_counts_toward_the_total_block_budget() -> None:
    # 100 rows (the schema's own _MAX_TABLE_ROWS ceiling) x one 1000-char
    # cell (Line's own per-cell cap) is within every per-field schema bound
    # yet sums to one more character than the total block budget.
    rows = [["x" * 1000] for _ in range(100)]
    export = ChatExport(
        mode="technical",
        tldr=["ok"],
        design=[{"type": "table", "headers": ["h"], "rows": rows}],
    )
    with pytest.raises(ValidationError) as excinfo:
        render_chat_export(export, title="t", now=_NOW)
    assert excinfo.value.message == (
        f"Block content exceeds the total limit of {_MAX_TOTAL_BLOCK_CHARS} characters."
    )


def test_table_label_counts_toward_the_total_block_budget() -> None:
    # Same table (99 rows x 1000-char cell + a 999-char header = 99,999
    # chars) is accepted on its own, but adding a 2-char label alone pushes
    # the identical table over budget — isolating the label's contribution.
    rows = [["x" * 1000] for _ in range(99)]
    header = "h" * 999
    at_budget = ChatExport(
        mode="technical",
        tldr=["ok"],
        design=[{"type": "table", "headers": [header], "rows": rows}],
    )
    render_chat_export(at_budget, title="t", now=_NOW)  # 99_999 chars, must not raise

    with_label = ChatExport(
        mode="technical",
        tldr=["ok"],
        design=[{"type": "table", "label": "xx", "headers": [header], "rows": rows}],
    )
    with pytest.raises(ValidationError) as excinfo:
        render_chat_export(with_label, title="t", now=_NOW)
    assert excinfo.value.message == (
        f"Block content exceeds the total limit of {_MAX_TOTAL_BLOCK_CHARS} characters."
    )


def test_code_block_label_counts_toward_the_total_block_budget() -> None:
    # docs/adr/0009-*.md's own budget test never counted a code block's
    # label — only its content. docs/adr/0011-*.md widens the budget to
    # every client-supplied string in a rich block, label included: the
    # export built exactly at the budget by content alone (below) is
    # accepted; adding a single-character label to it must push it over.
    export = _build_export_with_total_code_chars(_MAX_TOTAL_BLOCK_CHARS)
    payload = export.model_dump(mode="json")
    payload["code_blocks"][0]["label"] = "x"
    with_label = ChatExport(**payload)
    with pytest.raises(ValidationError) as excinfo:
        render_chat_export(with_label, title="t", now=_NOW)
    assert excinfo.value.message == (
        f"Block content exceeds the total limit of {_MAX_TOTAL_BLOCK_CHARS} characters."
    )


# --- Schema: QuoteBlock ----------------------------------------------------------


def test_quote_block_rejects_unknown_field() -> None:
    with pytest.raises(PydanticValidationError):
        ChatExport(tldr=["ok"], design=[{"type": "quote", "lines": ["a"], "bogus": 1}])


def test_quote_lines_empty_list_is_rejected_at_schema_level() -> None:
    with pytest.raises(PydanticValidationError):
        ChatExport(tldr=["ok"], design=[{"type": "quote", "lines": []}])


@pytest.mark.parametrize("callout", ["1bad", "has space", "-leading-dash", ""])
def test_quote_invalid_callout_is_rejected_at_schema_level(callout: str) -> None:
    with pytest.raises(PydanticValidationError):
        ChatExport(tldr=["ok"], design=[{"type": "quote", "callout": callout, "lines": ["a"]}])


@pytest.mark.parametrize("callout", ["note", "warning", "my-custom-type", "A"])
def test_quote_valid_callout_examples_are_accepted(callout: str) -> None:
    export = ChatExport(
        mode="technical",
        tldr=["ok"],
        design=[{"type": "quote", "callout": callout, "lines": ["a"]}],
    )
    render_chat_export(export, title="t", now=_NOW)  # must not raise


# --- Formatter: title requires callout; an empty quote is dropped --------------


def test_quote_title_without_callout_is_rejected() -> None:
    export = ChatExport(
        mode="technical", tldr=["ok"], design=[{"type": "quote", "title": "t", "lines": ["a"]}]
    )
    with pytest.raises(ValidationError) as excinfo:
        render_chat_export(export, title="t", now=_NOW)
    assert excinfo.value.message == "design[0]: quote title requires callout."


def test_quote_with_only_whitespace_lines_is_dropped() -> None:
    # The same "min_length=1 at the schema layer, still droppable once
    # whitespace-only" precedent _normalise_code_block already sets.
    export = ChatExport(
        mode="technical", tldr=["ok"], design=["a", {"type": "quote", "lines": ["   ", "\t"]}, "b"]
    )
    rendered = render_chat_export(export, title="t", now=_NOW)
    assert ">" not in rendered.content
    assert "- a\n- b" in rendered.content


# --- Structure: plain blockquote, callout header, sibling blocks ---------------


def test_quote_without_callout_renders_as_a_plain_blockquote() -> None:
    export = ChatExport(
        mode="technical", tldr=["ok"], design=[{"type": "quote", "lines": ["a", "b"]}]
    )
    rendered = render_chat_export(export, title="t", now=_NOW)
    design_section = rendered.content.split("## 設計\n\n")[1].split("\n\n## ")[0]
    assert design_section == "> a\n> b"
    tokens = _MD.parse(rendered.content)
    assert sum(1 for t in tokens if t.type == "blockquote_open") == 1


def test_quote_with_callout_renders_the_header_line() -> None:
    export = ChatExport(
        mode="technical",
        tldr=["ok"],
        design=[{"type": "quote", "callout": "warning", "title": "注意", "lines": ["a"]}],
    )
    rendered = render_chat_export(export, title="t", now=_NOW)
    design_section = rendered.content.split("## 設計\n\n")[1].split("\n\n## ")[0]
    assert design_section == "> [!warning] 注意\n> a"


def test_quote_without_title_renders_the_header_line_with_no_trailing_space() -> None:
    export = ChatExport(
        mode="technical", tldr=["ok"], design=[{"type": "quote", "callout": "note", "lines": ["a"]}]
    )
    rendered = render_chat_export(export, title="t", now=_NOW)
    design_section = rendered.content.split("## 設計\n\n")[1].split("\n\n## ")[0]
    assert design_section == "> [!note]\n> a"


def test_quote_title_keeps_inline_markdown_live() -> None:
    export = ChatExport(
        mode="technical",
        tldr=["ok"],
        design=[{"type": "quote", "callout": "note", "title": "**bold**", "lines": ["a"]}],
    )
    rendered = render_chat_export(export, title="t", now=_NOW)
    tokens = _MD.parse(rendered.content)
    header_inline = next(
        t
        for t in tokens
        if t.type == "inline" and any("bold" in c.content for c in (t.children or []))
    )
    assert any(c.type == "strong_open" for c in header_inline.children)


def test_bullet_quote_bullet_render_as_sibling_blocks_in_one_field() -> None:
    export = ChatExport(
        mode="technical",
        tldr=["ok"],
        design=["a", {"type": "quote", "lines": ["quoted"]}, "b"],
    )
    rendered = render_chat_export(export, title="t", now=_NOW)
    tokens = _MD.parse(rendered.content)
    assert sum(1 for t in tokens if t.type == "bullet_list_open") == 2
    assert sum(1 for t in tokens if t.type == "blockquote_open") == 1


# --- Block-forgery hazards inside a quote line are all escaped -----------------

_QUOTE_HAZARD_LINES = [
    "# heading",
    "> nested quote",
    "<tag>",
    "[ref]: url",
    "- bullet",
    "* bullet",
    "+ bullet",
    "```fence",
    "~~~fence",
    "---",
    "===",
    "___",
    "1. item",
    "1) item",
]


@pytest.mark.parametrize("hazard_line", _QUOTE_HAZARD_LINES)
def test_quote_line_hazard_classes_are_all_escaped(hazard_line: str) -> None:
    # _escape_block_start's full hazard set (the same set the pre-existing
    # bullet/tldr tests pin) must also hold for a quote line — testing only
    # "#" would not catch a future narrowing of _BLOCK_HAZARD_RE that a
    # quote-specific code path happened not to exercise.
    baseline = render_chat_export(
        ChatExport(mode="technical", tldr=["ok"], design=[{"type": "quote", "lines": ["safe"]}]),
        title="t",
        now=_NOW,
    )
    baseline_headings = sum(1 for t in _MD.parse(baseline.content) if t.type == "heading_open")

    export = ChatExport(
        mode="technical", tldr=["ok"], design=[{"type": "quote", "lines": [hazard_line]}]
    )
    rendered = render_chat_export(export, title="t", now=_NOW)
    tokens = _MD.parse(rendered.content)
    assert sum(1 for t in tokens if t.type == "blockquote_open") == 1
    assert sum(1 for t in tokens if t.type == "hr") == 0
    assert sum(1 for t in tokens if t.type == "fence") == 0
    assert sum(1 for t in tokens if t.type == "html_block") == 0
    assert sum(1 for t in tokens if t.type == "bullet_list_open") == 0
    assert sum(1 for t in tokens if t.type == "ordered_list_open") == 0
    assert sum(1 for t in tokens if t.type == "heading_open") == baseline_headings


# --- Quote inside a procedure step ----------------------------------------------


def test_quote_in_a_step_stays_within_that_step_and_preserves_numbering() -> None:
    steps = [{"blocks": [{"type": "text", "content": f"step {i}"}]} for i in range(1, 11)]
    steps[9] = {
        "blocks": [
            {"type": "text", "content": "step 10"},
            {"type": "quote", "callout": "warning", "lines": ["careful"]},
        ]
    }
    export = ChatExport(mode="procedure", tldr=["ok"], steps=steps)
    rendered = render_chat_export(export, title="t", now=_NOW)
    tokens = _MD.parse(rendered.content)
    assert sum(1 for t in tokens if t.type == "ordered_list_open") == 1
    assert sum(1 for t in tokens if t.type == "list_item_open") == 10

    item_index = 0
    quote_item_index = None
    for token in tokens:
        if token.type == "list_item_open":
            item_index += 1
        elif token.type == "blockquote_open":
            quote_item_index = item_index
    assert quote_item_index == 10


def test_quote_first_step_is_rejected() -> None:
    export = ChatExport(
        mode="procedure",
        tldr=["ok"],
        steps=[{"blocks": [{"type": "quote", "lines": ["a"]}]}],
    )
    with pytest.raises(ValidationError) as excinfo:
        render_chat_export(export, title="t", now=_NOW)
    assert excinfo.value.message == "steps[0] must start with a text block."


# --- Quote lines/title count toward the total block budget ---------------------


def test_quote_lines_count_toward_the_total_block_budget() -> None:
    # A single QuoteBlock cannot reach the budget on its own (30 lines x
    # 1000 chars, its own schema max, is only 30,000 chars), so four
    # quotes of 25 lines x 1000 chars each — 100,000 chars total, still
    # within every per-item schema bound — sit exactly at the budget;
    # a fifth line anywhere pushes the total one character over.
    quote = {"type": "quote", "lines": ["x" * 1000 for _ in range(25)]}  # 25,000 chars
    at_budget = ChatExport(
        mode="technical", tldr=["ok"], design=[dict(quote) for _ in range(4)]
    )
    render_chat_export(at_budget, title="t", now=_NOW)  # 100_000 chars, must not raise

    over_budget_quotes = [dict(quote) for _ in range(4)]
    over_budget_quotes[0] = {"type": "quote", "lines": ["x" * 1000 for _ in range(26)]}
    over_budget = ChatExport(mode="technical", tldr=["ok"], design=over_budget_quotes)
    with pytest.raises(ValidationError) as excinfo:
        render_chat_export(over_budget, title="t", now=_NOW)
    assert excinfo.value.message == (
        f"Block content exceeds the total limit of {_MAX_TOTAL_BLOCK_CHARS} characters."
    )


def test_quote_title_counts_toward_the_total_block_budget() -> None:
    # Same "exactly at budget, then +1 via one field alone" shape as the
    # lines test above, isolating the title's own contribution this time:
    # four 25,000-char quotes sit exactly at budget; adding a 1-char title
    # to one of them alone must push the identical export over.
    quote = {"type": "quote", "lines": ["x" * 1000 for _ in range(25)]}
    at_budget = ChatExport(
        mode="technical", tldr=["ok"], design=[dict(quote) for _ in range(4)]
    )
    render_chat_export(at_budget, title="t", now=_NOW)  # 100_000 chars, must not raise

    with_title = [dict(quote) for _ in range(4)]
    with_title[0] = {**quote, "callout": "note", "title": "x"}
    over_budget = ChatExport(mode="technical", tldr=["ok"], design=with_title)
    with pytest.raises(ValidationError) as excinfo:
        render_chat_export(over_budget, title="t", now=_NOW)
    assert excinfo.value.message == (
        f"Block content exceeds the total limit of {_MAX_TOTAL_BLOCK_CHARS} characters."
    )


def test_procedure_step_quote_is_never_moved_into_the_top_level_code_section() -> None:
    export = ChatExport(
        mode="procedure",
        tldr=["ok"],
        steps=[
            {
                "blocks": [
                    {"type": "text", "content": "a"},
                    {"type": "quote", "lines": ["careful"]},
                ]
            }
        ],
    )
    rendered = render_chat_export(export, title="t", now=_NOW)
    assert "## コード" not in rendered.content


# === Bullet nesting depth and task-list checkboxes (docs/adr/0011-*.md) =======

# --- Schema: BulletBlock.depth / .checked ---------------------------------------


def test_bullet_depth_out_of_range_is_rejected_at_schema_level() -> None:
    with pytest.raises(PydanticValidationError):
        ChatExport(tldr=["ok"], design=[{"type": "bullet", "content": "a", "depth": 4}])


def test_bullet_depth_negative_is_rejected_at_schema_level() -> None:
    with pytest.raises(PydanticValidationError):
        ChatExport(tldr=["ok"], design=[{"type": "bullet", "content": "a", "depth": -1}])


def test_text_block_rejects_bullet_only_fields() -> None:
    # TextBlock has no depth/checked at all — extra="forbid" is what keeps
    # a client from sending a step block with bullet-only fields, no
    # runtime check required (docs/adr/0011-*.md).
    with pytest.raises(PydanticValidationError):
        ChatExport(
            mode="procedure",
            tldr=["ok"],
            steps=[{"blocks": [{"type": "text", "content": "a", "depth": 1}]}],
        )


# --- Structure: nesting depth renders as CommonMark-nested bullet lists --------


def test_bullet_nesting_depth_zero_one_two_renders_as_nested_lists() -> None:
    export = ChatExport(
        mode="technical",
        tldr=["ok"],
        design=[
            {"type": "bullet", "content": "d0", "depth": 0},
            {"type": "bullet", "content": "d1", "depth": 1},
            {"type": "bullet", "content": "d2", "depth": 2},
        ],
    )
    rendered = render_chat_export(export, title="t", now=_NOW)
    section = rendered.content.split("## 設計\n\n")[1].split("\n\n## ")[0]
    assert section == "- d0\n  - d1\n    - d2"
    tokens = _MD.parse(rendered.content)
    depths = []
    depth = -1
    for token in tokens:
        if token.type == "bullet_list_open":
            depth += 1
        elif token.type == "bullet_list_close":
            depth -= 1
        elif token.type == "list_item_open":
            depths.append(depth)
    assert depths == [0, 1, 2]


def test_bullet_depth_jump_of_more_than_one_is_rejected() -> None:
    export = ChatExport(
        mode="technical",
        tldr=["ok"],
        design=[
            {"type": "bullet", "content": "a", "depth": 0},
            {"type": "bullet", "content": "b", "depth": 2},
        ],
    )
    with pytest.raises(ValidationError) as excinfo:
        render_chat_export(export, title="t", now=_NOW)
    assert excinfo.value.message == "design[1]: bullet depth jumps from 0 to 2."


def test_first_bullet_at_nonzero_depth_is_rejected() -> None:
    export = ChatExport(
        mode="technical", tldr=["ok"], design=[{"type": "bullet", "content": "a", "depth": 1}]
    )
    with pytest.raises(ValidationError) as excinfo:
        render_chat_export(export, title="t", now=_NOW)
    assert excinfo.value.message == "design[0]: bullet depth must start at 0."


def test_bullet_after_a_table_must_restart_at_depth_zero() -> None:
    # A section-level block ends the current bullet list — the next bullet
    # starts a new one and must begin at depth 0 (docs/adr/0011-*.md).
    export = ChatExport(
        mode="technical",
        tldr=["ok"],
        design=[
            {"type": "bullet", "content": "a", "depth": 0},
            {"type": "table", "headers": ["h"], "rows": []},
            {"type": "bullet", "content": "b", "depth": 1},
        ],
    )
    with pytest.raises(ValidationError) as excinfo:
        render_chat_export(export, title="t", now=_NOW)
    assert excinfo.value.message == "design[2]: bullet depth must start at 0."


def test_bullet_after_a_table_at_depth_zero_is_accepted() -> None:
    export = ChatExport(
        mode="technical",
        tldr=["ok"],
        design=[
            {"type": "bullet", "content": "a", "depth": 0},
            {"type": "table", "headers": ["h"], "rows": []},
            {"type": "bullet", "content": "b", "depth": 0},
        ],
    )
    render_chat_export(export, title="t", now=_NOW)  # must not raise


def test_bullet_depth_jump_error_reports_the_source_index_not_the_normalised_one() -> None:
    # docs/adr/0011-*.md: a bullet that normalises to empty content is
    # dropped, which must not shift a later depth-jump error's reported
    # index — it must still name the client's own input position.
    export = ChatExport(
        mode="technical",
        tldr=["ok"],
        design=[
            {"type": "bullet", "content": "A", "depth": 0},
            {"type": "bullet", "content": "   ", "depth": 1},  # drops to empty
            {"type": "bullet", "content": "C", "depth": 2},  # jump 0 -> 2 after the drop
        ],
    )
    with pytest.raises(ValidationError) as excinfo:
        render_chat_export(export, title="t", now=_NOW)
    assert excinfo.value.message == "design[2]: bullet depth jumps from 0 to 2."


# --- Structure: GFM task-list checkboxes ----------------------------------------


def test_bullet_checked_false_renders_an_open_checkbox() -> None:
    export = ChatExport(
        mode="technical",
        tldr=["ok"],
        design=[{"type": "bullet", "content": "todo", "checked": False}],
    )
    rendered = render_chat_export(export, title="t", now=_NOW)
    section = rendered.content.split("## 設計\n\n")[1].split("\n\n## ")[0]
    assert section == "- [ ] todo"


def test_bullet_checked_true_renders_a_checked_checkbox() -> None:
    export = ChatExport(
        mode="technical",
        tldr=["ok"],
        design=[{"type": "bullet", "content": "done", "checked": True}],
    )
    rendered = render_chat_export(export, title="t", now=_NOW)
    section = rendered.content.split("## 設計\n\n")[1].split("\n\n## ")[0]
    assert section == "- [x] done"


def test_bullet_without_checked_renders_an_ordinary_bullet() -> None:
    export = ChatExport(mode="technical", tldr=["ok"], design=[{"type": "bullet", "content": "a"}])
    rendered = render_chat_export(export, title="t", now=_NOW)
    section = rendered.content.split("## 設計\n\n")[1].split("\n\n## ")[0]
    assert section == "- a"


def test_plain_string_bullets_default_to_depth_zero_and_no_checkbox() -> None:
    # Backward compatibility: a bare string is still equivalent to
    # {"type": "bullet", "content": ..., "depth": 0, "checked": None}.
    export = ChatExport(mode="technical", tldr=["ok"], design=["a"])
    rendered = render_chat_export(export, title="t", now=_NOW)
    section = rendered.content.split("## 設計\n\n")[1].split("\n\n## ")[0]
    assert section == "- a"


# === Code blocks reused in body fields (docs/adr/0011-*.md) ====================
#
# CodeBlock already existed (ADR-0009, for ProcedureStep.blocks and the
# top-level code_blocks) — this section covers its addition to BodyBlock,
# not a new block type.


def test_bullet_code_bullet_render_as_sibling_blocks_in_one_field() -> None:
    export = ChatExport(
        mode="technical",
        tldr=["ok"],
        design=["a", {"type": "code", "language": "yaml", "content": "x: y"}, "b"],
    )
    rendered = render_chat_export(export, title="t", now=_NOW)
    tokens = _MD.parse(rendered.content)
    assert sum(1 for t in tokens if t.type == "bullet_list_open") == 2
    fences = [t for t in tokens if t.type == "fence"]
    assert [(f.content, f.info) for f in fences] == [("x: y\n", "yaml")]


def test_code_only_field_renders_directly_under_the_heading_with_no_stray_bullet() -> None:
    export = ChatExport(
        mode="technical",
        tldr=["ok"],
        design=[{"type": "code", "label": "compose.yaml", "content": "a: b"}],
    )
    rendered = render_chat_export(export, title="t", now=_NOW)
    section = rendered.content.split("## 設計\n\n")[1].split("\n\n## ")[0]
    assert section == "compose.yaml\n```\na: b\n```"
    assert not any(line.startswith("- ") for line in section.splitlines())


def test_body_field_code_is_never_moved_into_the_top_level_code_section() -> None:
    export = ChatExport(
        mode="technical",
        tldr=["ok"],
        design=[{"type": "code", "content": "body-field-code"}],
    )
    rendered = render_chat_export(export, title="t", now=_NOW)
    assert "## コード" not in rendered.content
    assert "body-field-code" in rendered.content


def test_body_field_code_that_normalises_to_empty_is_dropped() -> None:
    # The same "min_length=1 at the schema layer, still droppable once
    # whitespace-only" precedent _normalise_code_block already sets.
    export = ChatExport(
        mode="technical", tldr=["ok"], design=["a", {"type": "code", "content": "   \n\t "}, "b"]
    )
    rendered = render_chat_export(export, title="t", now=_NOW)
    assert "```" not in rendered.content
    section = rendered.content.split("## 設計\n\n")[1].split("\n\n## ")[0]
    assert section == "- a\n- b"


def test_bullet_after_body_field_code_must_restart_at_depth_zero() -> None:
    export = ChatExport(
        mode="technical",
        tldr=["ok"],
        design=[
            {"type": "bullet", "content": "a", "depth": 0},
            {"type": "code", "content": "x"},
            {"type": "bullet", "content": "b", "depth": 1},
        ],
    )
    with pytest.raises(ValidationError) as excinfo:
        render_chat_export(export, title="t", now=_NOW)
    assert excinfo.value.message == "design[2]: bullet depth must start at 0."


def test_body_field_code_content_counts_toward_the_total_block_budget() -> None:
    # 12 code blocks at the per-block max (_MAX_CODE_CHARS = 8_000) plus a
    # 13th at 4,000 chars sit exactly at the total budget — still within
    # design's own item-count cap (_MAX_LIST_ITEMS = 30); one more
    # character in the 13th block alone pushes the identical export over.
    full_blocks = [{"type": "code", "content": "x" * 8_000} for _ in range(12)]  # 96,000 chars
    at_budget = ChatExport(
        mode="technical",
        tldr=["ok"],
        design=[*full_blocks, {"type": "code", "content": "y" * 4_000}],
    )
    render_chat_export(at_budget, title="t", now=_NOW)  # 100_000 chars, must not raise

    over_budget = ChatExport(
        mode="technical",
        tldr=["ok"],
        design=[*full_blocks, {"type": "code", "content": "y" * 4_001}],
    )
    with pytest.raises(ValidationError) as excinfo:
        render_chat_export(over_budget, title="t", now=_NOW)
    assert excinfo.value.message == (
        f"Block content exceeds the total limit of {_MAX_TOTAL_BLOCK_CHARS} characters."
    )


def test_procedure_mode_design_field_is_still_rejected_as_mode_mismatched() -> None:
    # A body field's own mode ownership (ADR-0005 decision 4/_MODE_SECTIONS)
    # is unaffected by CodeBlock now being a valid item inside it.
    export = ChatExport(
        mode="procedure",
        tldr=["ok"],
        steps=["do it"],
        design=[{"type": "code", "content": "x"}],
    )
    with pytest.raises(ValidationError) as excinfo:
        render_chat_export(export, title="t", now=_NOW)
    assert excinfo.value.message == "Fields not valid for export_mode 'procedure': design."
