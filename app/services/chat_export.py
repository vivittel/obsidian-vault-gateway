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

Related-note wikilinks (GitHub issue #13;
docs/adr/0006-verified-related-note-wikilinks.md) are the one deliberate
exception to "no filesystem access": this module still never touches the
Vault, but it also never renders ``ChatExport.related_notes`` — that field is
raw, unverified client input. ``render_chat_export`` instead takes an
explicit ``verified_related_notes`` argument, which app/application.py fills
in by calling app.services.related_notes.resolve_related_notes against the
Vault *before* calling here. Two rendering rules follow from that split:
related-note paths are the one client-supplied string this module does not
run through ``one_line`` (rewriting a path can make it name a different
file), and the rendered wikilink bullets are the one list this module does
not run through ``_escape_block_start`` (escaping would corrupt the `[[...]]`
syntax). Both are safe only because the caller has already restricted the
values to paths that passed ``is_renderable_wikilink_target`` and resolved to
a real note.

Code content (``procedure.steps[].blocks`` and the top-level ``code_blocks``;
docs/adr/0009-verbatim-code-blocks-in-structured-exports.md) is rendered
verbatim/structure-preserving, not byte-level lossless: ``_canonicalise_code``
still unifies line endings, strips non-newline/tab control characters, and
collapses a trailing 0-or-1 newline difference before the value is ever
placed inside a Markdown fence. None of ``one_line``'s other transforms
(whitespace-run collapsing, per-line stripping) and none of
``_escape_block_start``'s hazard-escaping ever run on code content — a fence
is already structurally closed, so the hazards that rule exists for
(a bullet/heading/HTML-block forgery) cannot occur inside one. A code block's
optional ``label`` is the one exception: it renders as plain inline text
above the fence, so it is escaped by ``_escape_inline`` (covering both
CommonMark/GFM inline syntax and Obsidian-specific inline semantics —
``#`` tags, ``^`` block IDs, ``==``/``$``/``%%``) before ``_escape_block_start``.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from itertools import chain

from app.exceptions import ValidationError
from app.models import (
    _LANGUAGE_PATTERN,
    ChatExport,
    CodeBlock,
    ExportMode,
    FrontmatterValue,
    ProcedureStep,
    TermDefinition,
    TextBlock,
    TimelineEntry,
    TopicSection,
)

_SOURCE = "chatgpt"

_PLACEHOLDER_NONE = "なし"
_PLACEHOLDER_NOT_RECORDED = "未記録"
_PLACEHOLDER_UNRESOLVED = "未解決"

# Every rendered heading, common and mode-specific. "related_notes" renders
# from the verified_related_notes argument, never from ChatExport.related_notes
# directly — see the module docstring and docs/adr/0006-*.md.
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

# A digit+punctuation prefix (e.g. "1. nested") needs its own branch: escaping
# it like every other hazard (a bare leading backslash) would produce
# "\1. nested" on the wire, and a backslash before a digit is not a CommonMark
# escape sequence at all (only ASCII punctuation is escapable) — the backslash
# would render literally. The correct fix is to escape the punctuation itself
# ("1\. nested"), which both displays cleanly and blocks the nested-list read.
_ORDERED_MARKER_RE = re.compile(r"^(\d+)([.)])(?=\s|$)")

# Leading '<' covers every CommonMark HTML block type (1-7 all start with it)
# without enumerating tag names; leading '[' covers link reference
# definitions ("[foo]: url", which would silently disappear as list content
# and rewire any other "[foo]" reference in the same note) and task-list
# checkboxes ("[ ] "/"[x] ", a GFM/Obsidian extension). The digit+punctuation
# case is handled separately by _ORDERED_MARKER_RE above, not here.
_BLOCK_HAZARD_RE = re.compile(
    r"^(?:#{1,6}(?:\s|$)|>|<|\[|[-*+](?:\s|$)|```|~~~|-{3,}$|={3,}$|_{3,}$)"
)

# app.services.path_security._check_syntax accepts all five of these
# characters in a filename — verified empirically against a real, resolvable
# note. Left in, a genuinely existing "has|pipe.md" or "has#hash.md" renders
# as "[[Knowledge/has|pipe]]" or "[[Knowledge/has#hash]]", and Obsidian reads
# "|"/"#" as an alias/heading-anchor separator, producing a wikilink that
# resolves to the wrong target (or no target) rather than the intended note.
# This is the sole guard against that — path_security has no reason to reject
# these characters for its own (read/write) purposes, so it does not.
_WIKILINK_HAZARD_RE = re.compile(r"[\[\]|#^]")

_MARKDOWN_SUFFIX = ".md"

# Code content (docs/adr/0009-*.md): unlike _CONTROL_RE (used by one_line for
# single-line text), \t and \n must survive — only the other C0/C1 control
# characters are stripped.
_CODE_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
_BACKTICK_RUN_RE = re.compile(r"`+")
_LANGUAGE_RE = re.compile(_LANGUAGE_PATTERN)

# The counterpart of app.models._MAX_CODE_CHARS's per-block budget: no single
# Field(max_length=...) can see the *sum* across every code block in one
# export (steps[].blocks and the top-level code_blocks together), so this is
# enforced here, on normalised data, like every other cross-field check in
# this module. ~400 KiB worst-case UTF-8 (4 bytes/code point), comfortably
# inside MAX_NOTE_SIZE_BYTES's default 1 MiB.
_MAX_TOTAL_CODE_CHARS = 100_000

# Rendered only via _render_supplementary_sections, never through
# _render_section/_HEADINGS: unlike every mode's own heading, "## コード" is
# an *optional* supplementary section — omitted entirely when code_blocks is
# empty, in every mode, rather than always emitted with a placeholder like
# every entry in _HEADINGS is. See _render_supplementary_sections's docstring.
_CODE_BLOCKS_HEADING = "コード"

# Obsidian is the note's reader, not only a CommonMark renderer, so this set
# covers CommonMark/GFM inline syntax (backslash, backtick, emphasis markers,
# link/image brackets, autolink/HTML angle brackets, strikethrough tildes)
# *and* Obsidian-specific inline semantics markdown-it-py cannot detect: "#"
# (a bare hashtag becomes a live Obsidian tag), "^" (can start a block-ID
# reference), "==" (highlight), "$" (math), "%%" (comment). A caption is the
# only place this module emits client text as inline content that must
# render literally — code content never needs it (a fence is verbatim), and
# ordinary text is allowed to contain intentional inline Markdown. Excluded
# on purpose: "|" (significant only inside a GFM table, which a caption never
# is), "!" (only meaningful before "[", already escaped), and
# ": ; , \" ' / ? @" (no CommonMark/GFM/Obsidian inline meaning).
_INLINE_ESCAPE_CHARS = "\\`*_[]<>&~=$%#^"


@dataclass(frozen=True)
class RenderedExport:
    frontmatter: dict[str, FrontmatterValue]
    content: str


@dataclass(frozen=True)
class _NormalisedTextBlock:
    content: str


@dataclass(frozen=True)
class _NormalisedCodeBlock:
    language: str | None
    label: str | None
    content: str


_NormalisedStepBlock = _NormalisedTextBlock | _NormalisedCodeBlock


@dataclass(frozen=True)
class _NormalisedStep:
    blocks: tuple[_NormalisedStepBlock, ...]


@dataclass(frozen=True)
class _Normalised:
    mode: ExportMode
    tldr: list[str]
    decisions: list[str]
    unresolved_issues: list[str]
    next_actions: list[str]
    sources: list[str]
    code_blocks: list[_NormalisedCodeBlock]
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
    steps: list[_NormalisedStep]
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


def is_renderable_wikilink_target(relative_path: str) -> bool:
    """Whether ``relative_path`` can be rendered as ``[[relative_path]]`` without
    ambiguity or corruption.

    This is the single definition of "safe wikilink target" shared by
    app.services.related_notes (which calls it before touching the
    filesystem) and this module's own render path (which calls it again,
    defensively, on whatever it is given — see ``_render_related_notes_section``).
    Keeping one definition means the count a caller reports as "linked" can
    never diverge from what actually gets rendered.

    Does not check the Vault: this module has no filesystem access. A path
    passing this check may still not exist; that is
    app.services.related_notes.resolve_related_notes's job.
    """
    if not relative_path.endswith(_MARKDOWN_SUFFIX):
        return False
    stem = relative_path[: -len(_MARKDOWN_SUFFIX)]
    if not stem or stem.endswith(_MARKDOWN_SUFFIX):
        # An empty stem can't happen via path_security (hidden components are
        # already rejected), but this function has to be correct on its own.
        # A stem still ending in ".md" (i.e. "Foo.md.md") would silently
        # rename the link target to a *different*, possibly real, note once
        # the suffix below is stripped — worse than a broken link.
        return False
    if _WIKILINK_HAZARD_RE.search(relative_path):
        return False
    return not (_LINE_BREAK_RE.search(relative_path) or _CONTROL_RE.search(relative_path))


def format_wikilink(relative_path: str) -> str:
    """Render a verified vault-relative path as a canonical wikilink.

    Callers must have already checked ``is_renderable_wikilink_target``.
    """
    return f"[[{relative_path[: -len(_MARKDOWN_SUFFIX)]}]]"


def render_chat_export(
    export: ChatExport,
    *,
    title: str,
    now: datetime,
    verified_related_notes: Sequence[str] = (),
) -> RenderedExport:
    """Render ``export`` into deterministic Markdown and frontmatter.

    ``verified_related_notes`` — never ``export.related_notes`` — is what
    renders as the '## 関連ノート' section. The caller (app/application.py)
    is responsible for turning the client's raw, unverified
    ``export.related_notes`` candidates into this argument by calling
    app.services.related_notes.resolve_related_notes first; this module
    stays filesystem-free and cannot do that verification itself.
    """
    normalised = _normalise_export(export)
    _check_mode_fields(normalised)
    _check_mode_required(normalised)

    frontmatter = _build_frontmatter(normalised, title=title, now=now)
    content = _build_content(normalised, title=title, verified_related_notes=verified_related_notes)
    return RenderedExport(frontmatter=frontmatter, content=content)


def one_line(value: str) -> str:
    """Normalise ``value`` to a single Markdown-safe line.

    Every line-breaking character becomes a space (step 2), which is what
    makes forging a heading through an embedded newline impossible. Internal
    non-ASCII whitespace (e.g. U+3000) is preserved by step 4 — it only
    collapses ASCII runs — but leading/trailing Unicode whitespace is still
    removed by the final ``strip()``, which is Unicode-aware by default.

    Public (unlike most of this module's helpers): this is also the note's
    own title canonicalisation, so
    :func:`~app.services.duplicate_notes.exact_title_key` (issue #14) reuses
    it directly rather than re-implementing an equivalent normalisation that
    could drift from what actually gets written to a note's frontmatter/H1.
    """
    value = unicodedata.normalize("NFC", value)
    value = _LINE_BREAK_RE.sub(" ", value)
    value = _CONTROL_RE.sub("", value)
    value = _ASCII_SPACE_RUN_RE.sub(" ", value)
    return value.strip()


def _normalise_lines(values: list[str]) -> list[str]:
    """Normalise a plain string list, dropping items that become empty."""
    return [line for value in values if (line := one_line(value))]


def _canonicalise_code(content: str) -> str:
    """Canonicalise ``content`` for verbatim/structure-preserving rendering
    (docs/adr/0009-*.md) — the *only* transform code content goes through.

    Unlike :func:`one_line`, this never touches internal whitespace, blank
    lines, or leading whitespace: three canonicalisations only, each one
    collapsing a difference that carries no information rather than
    reshaping the content itself.

    1. CRLF/CR -> LF, matching every other line-ending canonicalisation in
       this codebase (app/services/inbox_service.py's ``_render_note``).
    2. Control characters other than tab/newline are stripped — same
       characters :data:`_CONTROL_RE` strips for single-line text, minus the
       two this function must preserve.
    3. At most one trailing newline is removed. A closing Markdown fence
       already supplies the line break that ends the code's last line, so
       ``"a"`` and ``"a\\n"`` must render identically; a second (or third)
       trailing newline is a deliberate blank line at the end of the content
       and is preserved, not collapsed.
    """
    content = content.replace("\r\n", "\n").replace("\r", "\n")
    content = _CODE_CONTROL_RE.sub("", content)
    if content.endswith("\n"):
        content = content[:-1]
    return content


def _is_safe_language(value: str) -> bool:
    """Defensive re-check of a fence info string, mirroring
    :func:`is_renderable_wikilink_target`'s own re-check of a value pydantic
    already validated: this module's own render path must stay structurally
    incapable of emitting an unsafe info string no matter what a future
    caller's ``_NormalisedCodeBlock`` carries.
    """
    return bool(_LANGUAGE_RE.fullmatch(value))


def _normalise_text_block(block: TextBlock) -> _NormalisedTextBlock | None:
    content = one_line(block.content)
    return _NormalisedTextBlock(content=content) if content else None


def _normalise_code_block(block: CodeBlock) -> _NormalisedCodeBlock | None:
    content = _canonicalise_code(block.content)
    if not content.strip():
        return None
    label = one_line(block.label) if block.label is not None else ""
    return _NormalisedCodeBlock(language=block.language, label=label or None, content=content)


def _normalise_step_block(block: TextBlock | CodeBlock) -> _NormalisedStepBlock | None:
    if isinstance(block, TextBlock):
        return _normalise_text_block(block)
    return _normalise_code_block(block)


def _normalise_steps(steps: list[ProcedureStep], field_name: str) -> list[_NormalisedStep]:
    """Normalise ``steps``, dropping a step entirely once every one of its
    blocks has normalised away to nothing (matching :func:`_normalise_lines`'s
    "empty after normalisation -> dropped" convention for a plain string
    list). A step surviving with at least one block, but not starting with a
    text block, is rejected outright rather than silently reordered or
    dropped — CommonMark cannot represent a step whose first line is a bare
    fence without a blank line that would split the numbered list and break
    its numbering (verified against markdown-it-py during design).
    """
    result: list[_NormalisedStep] = []
    for index, step in enumerate(steps):
        blocks = tuple(
            normalised
            for block in step.blocks
            if (normalised := _normalise_step_block(block)) is not None
        )
        if not blocks:
            continue
        if not isinstance(blocks[0], _NormalisedTextBlock):
            raise ValidationError(
                f"{field_name}[{index}] must start with a text block.",
                log_detail=f"chat export: {field_name}[{index}] does not start with a text block",
            )
        result.append(_NormalisedStep(blocks=blocks))
    return result


def _normalise_code_blocks(blocks: list[CodeBlock]) -> list[_NormalisedCodeBlock]:
    return [
        normalised
        for block in blocks
        if (normalised := _normalise_code_block(block)) is not None
    ]


def _total_code_chars(steps: list[_NormalisedStep], code_blocks: list[_NormalisedCodeBlock]) -> int:
    total = sum(len(block.content) for block in code_blocks)
    for step in steps:
        total += sum(
            len(block.content) for block in step.blocks if isinstance(block, _NormalisedCodeBlock)
        )
    return total


def _normalise_timeline(
    entries: list[TimelineEntry], field_name: str
) -> list[tuple[str | None, str]]:
    result: list[tuple[str | None, str]] = []
    for index, entry in enumerate(entries):
        event = one_line(entry.event)
        if not event:
            raise ValidationError(
                f"{field_name}[{index}].event must not be empty.",
                log_detail=f"chat export: {field_name}[{index}].event empty after normalisation",
            )
        when = one_line(entry.when) if entry.when is not None else ""
        result.append((when or None, event))
    return result


def _normalise_topics(topics: list[TopicSection], field_name: str) -> list[tuple[str, list[str]]]:
    result: list[tuple[str, list[str]]] = []
    for index, topic in enumerate(topics):
        heading = one_line(topic.heading)
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
        term = one_line(definition.term)
        if not term:
            raise ValidationError(
                f"{field_name}[{index}].term must not be empty.",
                log_detail=f"chat export: {field_name}[{index}].term empty after normalisation",
            )
        description = one_line(definition.description)
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
        candidate = one_line(tag).lstrip("#")
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

    project = one_line(export.project) if export.project is not None else ""
    conversation_type = (
        one_line(export.conversation_type) if export.conversation_type is not None else ""
    )

    steps = _normalise_steps(export.steps, "steps")
    code_blocks = _normalise_code_blocks(export.code_blocks)
    total_code_chars = _total_code_chars(steps, code_blocks)
    if total_code_chars > _MAX_TOTAL_CODE_CHARS:
        raise ValidationError(
            f"Code content exceeds the total limit of {_MAX_TOTAL_CODE_CHARS} characters.",
            log_detail=(
                f"chat export: total code content {total_code_chars} exceeds "
                f"{_MAX_TOTAL_CODE_CHARS}"
            ),
        )

    return _Normalised(
        mode=export.mode,
        tldr=tldr,
        decisions=_normalise_lines(export.decisions),
        unresolved_issues=_normalise_lines(export.unresolved_issues),
        next_actions=_normalise_lines(export.next_actions),
        sources=_normalise_lines(export.sources),
        code_blocks=code_blocks,
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
        steps=steps,
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


def _escape_block_start(value: str) -> str:
    """Escape ``value`` so it cannot be misread as the start of a new
    Markdown block (heading, blockquote, HTML block, link reference
    definition, list marker, fence, thematic break) once it is placed after
    a bullet/ordered marker or rendered as a bare paragraph (``tldr``).

    A bullet or numbered prefix ("- "/"N. ") does not, by itself, stop a
    client value from opening a *nested* block — CommonMark list items may
    contain arbitrary block content, so "- # forged" is a real, rendered
    ``<h1>`` inside the list item, not literal text. Every rendering path in
    this module therefore calls this as the last step before adding its own
    prefix, not only the bare-paragraph ``tldr`` path.
    """
    ordered = _ORDERED_MARKER_RE.match(value)
    if ordered:
        number, punctuation = ordered.groups()
        return f"{number}\\{punctuation}{value[ordered.end():]}"
    if _BLOCK_HAZARD_RE.match(value):
        return f"\\{value}"
    return value


def _placeholder_for(field_name: str) -> str:
    if field_name == "root_cause":
        return _PLACEHOLDER_UNRESOLVED
    if field_name in _COMMON_NONE_FIELDS:
        return _PLACEHOLDER_NONE
    return _PLACEHOLDER_NOT_RECORDED


def _render_timeline_line(when: str | None, event: str) -> str:
    text = f"{when}: {event}" if when else event
    return f"- {_escape_block_start(text)}"


def _render_topic(heading: str, points: list[str]) -> str:
    if points:
        body = "\n".join(f"- {_escape_block_start(point)}" for point in points)
    else:
        body = _PLACEHOLDER_NOT_RECORDED
    return f"### {heading}\n\n{body}"


def _escape_inline(value: str) -> str:
    """Escape ``value`` so it renders as literal inline text — a code
    block's ``label`` caption, the only inline (not block-start) content this
    module ever escapes. See :data:`_INLINE_ESCAPE_CHARS` for what is covered
    and why.
    """
    return "".join(f"\\{char}" if char in _INLINE_ESCAPE_CHARS else char for char in value)


def _render_caption(label: str) -> str:
    """Render a code block's ``label`` as a plain, literal line.

    ``_escape_inline`` must run first: it escapes a bare backslash, so
    running ``_escape_block_start`` afterwards on a label that starts with a
    hazard character (e.g. ``"# note"`` -> ``"\\# note"``) never doubles the
    escape — the leading ``\\`` already there makes
    :data:`_BLOCK_HAZARD_RE`/:data:`_ORDERED_MARKER_RE` no longer match.
    """
    return _escape_block_start(_escape_inline(label))


def _fence_for(content: str) -> str:
    """The shortest backtick fence that cannot be closed by anything already
    inside ``content`` (docs/adr/0009-*.md): three backticks, or one more
    than the longest run of consecutive backticks the content itself
    contains, whichever is longer.
    """
    longest_run = max((len(run) for run in _BACKTICK_RUN_RE.findall(content)), default=0)
    return "`" * max(3, longest_run + 1)


def _render_fenced_code(block: _NormalisedCodeBlock, *, indent: str) -> list[str]:
    """Render one code block's lines (caption, opening fence, content,
    closing fence), each prefixed with ``indent``. The caller decides
    placement (a procedure step's continuation vs. a standalone top-level
    block) and any separating blank line — this only builds the fence's own
    lines, never the content itself, which is placed verbatim per line.
    """
    lines: list[str] = []
    if block.label:
        lines.append(f"{indent}{_render_caption(block.label)}")
    info = block.language if block.language and _is_safe_language(block.language) else ""
    fence = _fence_for(block.content)
    lines.append(f"{indent}{fence}{info}")
    lines.extend(f"{indent}{line}" if line else "" for line in block.content.split("\n"))
    lines.append(f"{indent}{fence}")
    return lines


def _render_step(index: int, step: _NormalisedStep) -> str:
    """Render one ``## 手順`` list item, preserving the order of its text/code
    blocks (docs/adr/0009-*.md): a step with no code renders byte-identical
    to the pre-existing "N. text" line.

    ``indent`` is derived from the marker's own width, not a fixed constant:
    ``_MAX_STEP_ITEMS`` allows up to 50 steps, so step 10 onward has a
    4-character marker ("10. "). A fixed 3-space indent would let that
    step's continuation lines fall short of the marker width, which
    CommonMark treats as *outside* the list item — verified against
    markdown-it-py during design: the code fence then closes the list
    early and every following step renumbers from 1.
    """
    marker = f"{index}. "
    indent = " " * len(marker)
    lines: list[str] = []
    for position, block in enumerate(step.blocks):
        if isinstance(block, _NormalisedTextBlock):
            escaped = _escape_block_start(block.content)
            if position == 0:
                lines.append(f"{marker}{escaped}")
            else:
                lines.append("")
                lines.append(f"{indent}{escaped}")
        else:
            lines.append("")
            lines.extend(_render_fenced_code(block, indent=indent))
    return "\n".join(lines)


def _render_top_level_code_block(block: _NormalisedCodeBlock) -> str:
    return "\n".join(_render_fenced_code(block, indent=""))


def _render_supplementary_sections(normalised: _Normalised) -> list[str]:
    """Render every *optional* section that is not part of any mode's fixed
    heading set (docs/adr/0009-*.md) — currently just ``code_blocks`` ->
    ``## コード``. Unlike :func:`_render_section` (which always emits its
    heading, with a placeholder when empty, for every field the selected
    mode owns), a supplementary section is omitted entirely when it has
    nothing to render: this list is empty whenever ``code_blocks`` is empty,
    in every mode, so an export with no code renders exactly as before this
    feature existed. This does not change what ``_MODE_SECTIONS`` guarantees
    for a mode's own fields — it only adds a section that can appear, at a
    fixed position, in addition to them.
    """
    sections: list[str] = []
    if normalised.code_blocks:
        body = "\n\n".join(
            _render_top_level_code_block(block) for block in normalised.code_blocks
        )
        sections.append(f"## {_CODE_BLOCKS_HEADING}\n\n{body}")
    return sections


def _render_body(field_name: str, normalised: _Normalised) -> str:
    value = getattr(normalised, field_name)
    if not value:
        return _placeholder_for(field_name)

    if field_name == "tldr":
        return _escape_block_start(_join_sentences(value))
    if field_name == "steps":
        return "\n".join(_render_step(index, step) for index, step in enumerate(value, start=1))
    if field_name == "timeline":
        return "\n".join(_render_timeline_line(when, event) for when, event in value)
    if field_name == "topics":
        return "\n\n".join(_render_topic(heading, points) for heading, points in value)
    if field_name == "definitions":
        return "\n".join(
            f"- {_escape_block_start(f'{term}: {description}')}" for term, description in value
        )
    return "\n".join(f"- {_escape_block_start(line)}" for line in value)


def _render_section(field_name: str, normalised: _Normalised) -> str:
    heading = _HEADINGS[field_name]
    body = _render_body(field_name, normalised)
    return f"## {heading}\n\n{body}"


def _render_related_notes_section(verified_related_notes: Sequence[str]) -> str:
    # Re-filters even though every real caller already verified these paths
    # via the same is_renderable_wikilink_target predicate: this function is
    # then structurally incapable of emitting a corrupt "]]" no matter what a
    # future caller passes, without raising — related-note failures must
    # never block export, and that non-blocking rule applies here too.
    links = [path for path in verified_related_notes if is_renderable_wikilink_target(path)]
    if not links:
        body = _PLACEHOLDER_NONE
    else:
        body = "\n".join(f"- {format_wikilink(path)}" for path in links)
    return f"## {_HEADINGS['related_notes']}\n\n{body}"


def _build_content(
    normalised: _Normalised, *, title: str, verified_related_notes: Sequence[str]
) -> str:
    display_title = one_line(title)
    blocks = [f"# {display_title}"]
    blocks.append(_render_section("tldr", normalised))
    blocks.append(_render_section("decisions", normalised))
    for field_name in _MODE_SECTIONS[normalised.mode]:
        blocks.append(_render_section(field_name, normalised))
    blocks.extend(_render_supplementary_sections(normalised))
    blocks.append(_render_section("unresolved_issues", normalised))
    blocks.append(_render_section("next_actions", normalised))
    blocks.append(_render_related_notes_section(verified_related_notes))
    blocks.append(_render_section("sources", normalised))
    return "\n\n".join(blocks) + "\n"


def _build_frontmatter(
    normalised: _Normalised, *, title: str, now: datetime
) -> dict[str, FrontmatterValue]:
    created = now.isoformat(timespec="seconds")
    frontmatter: dict[str, FrontmatterValue] = {
        "title": one_line(title),
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
