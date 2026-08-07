"""Deterministic Markdown/frontmatter rendering for structured chat exports
(GitHub issue #12; docs/adr/0005-single-structured-entry-point-for-chat-exports.md).

This module is a pure function of its inputs: no filesystem access, no
``Settings``, no ``datetime.now()``. ``render_chat_export`` never summarises,
rewrites, or infers anything — it only arranges what the caller already
decided into the fixed section order and frontmatter schema the Gateway owns.
Determinism means "given the same ``now``": the caller (app/application.py)
is the only place that reads the clock, so this module can be re-run with a
fixed ``now`` and always produce byte-identical output.

Validation is deliberately split across two layers. Type shape and per-field
bounds live on the pydantic models in app/models.py, because a schema-level
rejection there becomes an unsanitised ``ToolError`` at the MCP boundary — an
accepted, existing trade-off (see tests/test_mcp_tools.py's nested-frontmatter
test). Everything that depends on *combinations* of fields — which fields a
mode allows, which fields a mode requires, whether required text survived
normalisation — has to raise from inside the tool body instead, so it is
sanitised by ``_McpCall`` into a coded ``MCPError``. That is why the checks
below run only after normalisation, not as pydantic validators.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from itertools import chain

from app.exceptions import ValidationError
from app.models import (
    ChatExport,
    ExportMode,
    FrontmatterValue,
    TermDefinition,
    TimelineEntry,
    TopicSection,
)

_SOURCE = "chatgpt"

_PLACEHOLDER_NONE = "なし"
_PLACEHOLDER_NOT_RECORDED = "未記録"
_PLACEHOLDER_UNRESOLVED = "未解決"

# Every rendered heading, common and mode-specific. "related_notes" has no
# backing field yet (issue #13 adds one; this module only fixes the heading,
# its position, and its always-empty placeholder — see ChatExport's docstring).
_HEADINGS: dict[str, str] = {
    "tldr": "要約",
    "decisions": "決定事項",
    "unresolved_issues": "未解決の論点",
    "next_actions": "次のアクション",
    "related_notes": "関連ノート",
    "sources": "出典",
    "overview": "概要",
    "key_points": "要点",
    "context": "背景",
    "design": "設計",
    "implementation_notes": "実装メモ",
    "verification": "検証",
    "timeline": "経緯",
    "turning_points": "転換点",
    "topics": "トピック",
    "prerequisites": "前提条件",
    "steps": "手順",
    "rollback": "ロールバック",
    "symptom": "症状",
    "environment": "環境",
    "investigation": "調査",
    "root_cause": "原因",
    "workaround": "回避策",
    "definitions": "用語",
    "facts": "事実",
    "examples": "例",
}

# Mode -> its fields, in render order. Every heading for the selected mode is
# always emitted, whether or not its field was supplied, so "same mode -> same
# heading set and order" holds independently of the input.
_MODE_SECTIONS: dict[ExportMode, tuple[str, ...]] = {
    "summary": ("overview", "key_points"),
    "technical": ("context", "design", "implementation_notes", "verification"),
    "history": ("timeline", "turning_points"),
    "full": ("topics",),
    "procedure": ("prerequisites", "steps", "verification", "rollback"),
    "issue": ("symptom", "environment", "investigation", "root_cause", "workaround"),
    "reference": ("definitions", "facts", "examples"),
}

# dict.fromkeys, not frozenset iteration: a frozenset's iteration order depends
# on string-hash randomisation and differs across processes, which would make
# the "fields not valid for this mode" error message non-deterministic.
_ALL_MODE_FIELDS_IN_ORDER: tuple[str, ...] = tuple(
    dict.fromkeys(chain.from_iterable(_MODE_SECTIONS.values()))
)
_ALL_MODE_FIELDS: frozenset[str] = frozenset(_ALL_MODE_FIELDS_IN_ORDER)

# Derived from _MODE_SECTIONS rather than hand-maintained, so it can never
# drift from it: field name -> every mode that owns it, in _MODE_SECTIONS's
# own definition order. Used by tests to check that a shared field's
# description (e.g. "verification") names every mode that owns it.
_FIELD_OWNER_MODES: dict[str, tuple[ExportMode, ...]] = {}
for _mode, _fields in _MODE_SECTIONS.items():
    for _field_name in _fields:
        _FIELD_OWNER_MODES[_field_name] = (*_FIELD_OWNER_MODES.get(_field_name, ()), _mode)

# The field (or one-of group of fields) without which the mode's note would be
# meaningless. Checked against normalised data, never the raw pydantic input —
# a field can be non-empty before normalisation and empty after it.
_MODE_REQUIRED: dict[ExportMode, tuple[tuple[str, ...], ...]] = {
    "history": (("timeline",),),
    "full": (("topics",),),
    "procedure": (("steps",),),
    "issue": (("symptom",),),
    "reference": (("definitions", "facts", "examples"),),
}

_COMMON_NONE_FIELDS = frozenset({"decisions", "unresolved_issues", "next_actions", "sources"})

_LINE_BREAK_RE = re.compile(r"[\r\n\v\f\x1c-\x1e\x85\u2028\u2029]+")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_ASCII_SPACE_RUN_RE = re.compile(r"[ \t]+")
_WHITESPACE_RUN_RE = re.compile(r"\s+")
_SENTENCE_END_CHARS = "。！？」）"
_PARAGRAPH_HAZARD_RE = re.compile(
    r"^(#{1,6}(\s|$)|>|[-*+](\s|$)|\d+[.)](\s|$)|```|~~~|-{3,}$|={3,}$|_{3,}$)"
)


@dataclass(frozen=True)
class RenderedExport:
    frontmatter: dict[str, FrontmatterValue]
    content: str


@dataclass(frozen=True)
class _Normalised:
    mode: ExportMode
    tldr: list[str]
    decisions: list[str]
    unresolved_issues: list[str]
    next_actions: list[str]
    sources: list[str]
    overview: list[str]
    key_points: list[str]
    context: list[str]
    design: list[str]
    implementation_notes: list[str]
    verification: list[str]
    timeline: list[tuple[str | None, str]]
    turning_points: list[str]
    topics: list[tuple[str, list[str]]]
    prerequisites: list[str]
    steps: list[str]
    rollback: list[str]
    symptom: list[str]
    environment: list[str]
    investigation: list[str]
    root_cause: list[str]
    workaround: list[str]
    definitions: list[tuple[str, str]]
    facts: list[str]
    examples: list[str]
    project: str | None
    conversation_type: str | None
    tags: list[str]


def render_chat_export(export: ChatExport, *, title: str, now: datetime) -> RenderedExport:
    normalised = _normalise_export(export)
    _check_mode_fields(normalised)
    _check_mode_required(normalised)

    frontmatter = _build_frontmatter(normalised, title=title, now=now)
    content = _build_content(normalised, title=title)
    return RenderedExport(frontmatter=frontmatter, content=content)


def _one_line(value: str) -> str:
    """Normalise ``value`` to a single Markdown-safe line.

    Every line-breaking character becomes a space (step 2), which is what
    makes forging a heading through an embedded newline impossible. Internal
    non-ASCII whitespace (e.g. U+3000) is preserved by step 4 — it only
    collapses ASCII runs — but leading/trailing Unicode whitespace is still
    removed by the final ``strip()``, which is Unicode-aware by default.
    """
    value = unicodedata.normalize("NFC", value)
    value = _LINE_BREAK_RE.sub(" ", value)
    value = _CONTROL_RE.sub("", value)
    value = _ASCII_SPACE_RUN_RE.sub(" ", value)
    return value.strip()


def _normalise_lines(values: list[str]) -> list[str]:
    """Normalise a plain string list, dropping items that become empty."""
    return [line for value in values if (line := _one_line(value))]


def _normalise_timeline(
    entries: list[TimelineEntry], field_name: str
) -> list[tuple[str | None, str]]:
    result: list[tuple[str | None, str]] = []
    for index, entry in enumerate(entries):
        event = _one_line(entry.event)
        if not event:
            raise ValidationError(
                f"{field_name}[{index}].event must not be empty.",
                log_detail=f"chat export: {field_name}[{index}].event empty after normalisation",
            )
        when = _one_line(entry.when) if entry.when is not None else ""
        result.append((when or None, event))
    return result


def _normalise_topics(topics: list[TopicSection], field_name: str) -> list[tuple[str, list[str]]]:
    result: list[tuple[str, list[str]]] = []
    for index, topic in enumerate(topics):
        heading = _one_line(topic.heading)
        if not heading:
            raise ValidationError(
                f"{field_name}[{index}].heading must not be empty.",
                log_detail=f"chat export: {field_name}[{index}].heading empty after normalisation",
            )
        result.append((heading, _normalise_lines(topic.points)))
    return result


def _normalise_definitions(
    definitions: list[TermDefinition], field_name: str
) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    for index, definition in enumerate(definitions):
        term = _one_line(definition.term)
        if not term:
            raise ValidationError(
                f"{field_name}[{index}].term must not be empty.",
                log_detail=f"chat export: {field_name}[{index}].term empty after normalisation",
            )
        description = _one_line(definition.description)
        if not description:
            raise ValidationError(
                f"{field_name}[{index}].description must not be empty.",
                log_detail=(
                    f"chat export: {field_name}[{index}].description empty "
                    "after normalisation"
                ),
            )
        result.append((term, description))
    return result


def _normalise_tags(tags: list[str]) -> list[str]:
    """Mechanical tag repair: no vocabulary, no defaults, just syntax.

    A tag containing whitespace is not usable in Obsidian, so whitespace runs
    become ``-`` rather than being rejected. Leading ``#`` characters are
    stripped in full (``##tag`` -> ``tag``, not ``#tag``) because a single
    leading ``#`` is itself not a valid tag body.
    """
    normalised: list[str] = []
    seen: set[str] = set()
    for tag in tags:
        candidate = _one_line(tag).lstrip("#")
        candidate = _WHITESPACE_RUN_RE.sub("-", candidate).strip("-")
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        normalised.append(candidate)
    return normalised


def _normalise_export(export: ChatExport) -> _Normalised:
    tldr = _normalise_lines(export.tldr)
    if not tldr:
        raise ValidationError(
            "tldr must contain at least one non-empty sentence.",
            log_detail="chat export: tldr empty after normalisation",
        )

    project = _one_line(export.project) if export.project is not None else ""
    conversation_type = (
        _one_line(export.conversation_type) if export.conversation_type is not None else ""
    )

    return _Normalised(
        mode=export.mode,
        tldr=tldr,
        decisions=_normalise_lines(export.decisions),
        unresolved_issues=_normalise_lines(export.unresolved_issues),
        next_actions=_normalise_lines(export.next_actions),
        sources=_normalise_lines(export.sources),
        overview=_normalise_lines(export.overview),
        key_points=_normalise_lines(export.key_points),
        context=_normalise_lines(export.context),
        design=_normalise_lines(export.design),
        implementation_notes=_normalise_lines(export.implementation_notes),
        verification=_normalise_lines(export.verification),
        timeline=_normalise_timeline(export.timeline, "timeline"),
        turning_points=_normalise_lines(export.turning_points),
        topics=_normalise_topics(export.topics, "topics"),
        prerequisites=_normalise_lines(export.prerequisites),
        steps=_normalise_lines(export.steps),
        rollback=_normalise_lines(export.rollback),
        symptom=_normalise_lines(export.symptom),
        environment=_normalise_lines(export.environment),
        investigation=_normalise_lines(export.investigation),
        root_cause=_normalise_lines(export.root_cause),
        workaround=_normalise_lines(export.workaround),
        definitions=_normalise_definitions(export.definitions, "definitions"),
        facts=_normalise_lines(export.facts),
        examples=_normalise_lines(export.examples),
        project=project or None,
        conversation_type=conversation_type or None,
        tags=_normalise_tags(export.tags),
    )


def _field_length(normalised: _Normalised, field_name: str) -> int:
    return len(getattr(normalised, field_name))


def _check_mode_fields(normalised: _Normalised) -> None:
    allowed = set(_MODE_SECTIONS[normalised.mode])
    offending = [
        field_name
        for field_name in _ALL_MODE_FIELDS_IN_ORDER
        if field_name not in allowed and _field_length(normalised, field_name) > 0
    ]
    if offending:
        names = ", ".join(offending)
        raise ValidationError(
            f"Fields not valid for export_mode '{normalised.mode}': {names}.",
            log_detail=f"chat export: mode-mismatched fields for '{normalised.mode}': {names}",
        )


def _check_mode_required(normalised: _Normalised) -> None:
    for group in _MODE_REQUIRED.get(normalised.mode, ()):
        if any(_field_length(normalised, field_name) > 0 for field_name in group):
            continue
        if len(group) == 1:
            message = f"Export mode '{normalised.mode}' requires {group[0]}."
        else:
            message = (
                f"Export mode '{normalised.mode}' requires at least one of "
                f"{', '.join(group)}."
            )
        raise ValidationError(
            message,
            log_detail=f"chat export: '{normalised.mode}' missing required field(s) {group}",
        )


def _join_sentences(items: list[str]) -> str:
    """Join TL;DR sentences without inserting a redundant space after a
    Japanese sentence-ending character."""
    result = ""
    for item in items:
        if result and result[-1] not in _SENTENCE_END_CHARS:
            result += " "
        result += item
    return result


def _escape_paragraph(line: str) -> str:
    """Escape a bare paragraph line that would otherwise read as Markdown
    structure (heading, blockquote, list marker, fence, thematic break).

    Only ``tldr`` renders as a bare paragraph; every other field is rendered
    as a bullet or numbered item, which already carries a prefix that a
    client value cannot forge past.
    """
    if _PARAGRAPH_HAZARD_RE.match(line):
        return f"\\{line}"
    return line


def _placeholder_for(field_name: str) -> str:
    if field_name == "root_cause":
        return _PLACEHOLDER_UNRESOLVED
    if field_name in _COMMON_NONE_FIELDS:
        return _PLACEHOLDER_NONE
    return _PLACEHOLDER_NOT_RECORDED


def _render_topic(heading: str, points: list[str]) -> str:
    body = "\n".join(f"- {point}" for point in points) if points else _PLACEHOLDER_NOT_RECORDED
    return f"### {heading}\n\n{body}"


def _render_body(field_name: str, normalised: _Normalised) -> str:
    value = getattr(normalised, field_name)
    if not value:
        return _placeholder_for(field_name)

    if field_name == "tldr":
        return _escape_paragraph(_join_sentences(value))
    if field_name == "steps":
        return "\n".join(f"{index}. {line}" for index, line in enumerate(value, start=1))
    if field_name == "timeline":
        return "\n".join(f"- {when}: {event}" if when else f"- {event}" for when, event in value)
    if field_name == "topics":
        return "\n\n".join(_render_topic(heading, points) for heading, points in value)
    if field_name == "definitions":
        return "\n".join(f"- {term}: {description}" for term, description in value)
    return "\n".join(f"- {line}" for line in value)


def _render_section(field_name: str, normalised: _Normalised) -> str:
    heading = _HEADINGS[field_name]
    body = _render_body(field_name, normalised)
    return f"## {heading}\n\n{body}"


def _render_related_notes_section() -> str:
    return f"## {_HEADINGS['related_notes']}\n\n{_PLACEHOLDER_NONE}"


def _build_content(normalised: _Normalised, *, title: str) -> str:
    display_title = _one_line(title)
    blocks = [f"# {display_title}"]
    blocks.append(_render_section("tldr", normalised))
    blocks.append(_render_section("decisions", normalised))
    for field_name in _MODE_SECTIONS[normalised.mode]:
        blocks.append(_render_section(field_name, normalised))
    blocks.append(_render_section("unresolved_issues", normalised))
    blocks.append(_render_section("next_actions", normalised))
    blocks.append(_render_related_notes_section())
    blocks.append(_render_section("sources", normalised))
    return "\n\n".join(blocks) + "\n"


def _build_frontmatter(
    normalised: _Normalised, *, title: str, now: datetime
) -> dict[str, FrontmatterValue]:
    created = now.isoformat(timespec="seconds")
    frontmatter: dict[str, FrontmatterValue] = {
        "title": _one_line(title),
        "created": created,
        "updated": created,
        "source": _SOURCE,
        "export_mode": normalised.mode,
    }
    if normalised.project:
        frontmatter["project"] = normalised.project
    if normalised.conversation_type:
        frontmatter["conversation_type"] = normalised.conversation_type
    frontmatter["tags"] = normalised.tags
    return frontmatter
