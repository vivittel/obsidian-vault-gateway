"""Request and response schemas.

Response models are fixed and fully typed on purpose: section 12 of the plan
requires stable schemas with explicit required fields. Most also back MCP's
structured tool output (app/mcp_server.py) — REST is health-only
(docs/adr/0010-*.md), so ``HealthResponse``/``ErrorResponse``/``ErrorDetail``
are the only ones a REST response still uses directly.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

from app.exceptions import ErrorCode

# Frontmatter accepted on write is restricted to scalars and flat lists of
# scalars. This is the injection boundary: a typed dict means an API caller
# cannot smuggle arbitrary YAML structures (anchors, nested maps, tags) into a
# vault note through the frontmatter field.
FrontmatterScalar = str | int | float | bool | None
FrontmatterValue = FrontmatterScalar | list[FrontmatterScalar]


class ErrorDetail(BaseModel):
    code: ErrorCode
    message: str


class ErrorResponse(BaseModel):
    """The single error shape used by every failing response (section 13)."""

    error: ErrorDetail


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"] = Field(
        description="'degraded' when a mount is missing or has the wrong permissions."
    )
    vault_readable: bool = Field(description="The read-only vault mount is readable.")
    inbox_writable: bool = Field(description="The inbox mount is writable.")


class SearchResultItem(BaseModel):
    id: str = Field(description="Vault-relative path; pass it to readNote as `path`.")
    path: str = Field(description="Vault-relative path of the note.")
    title: str = Field(description="Frontmatter `title`, else the file name without .md.")
    excerpt: str = Field(description="Short plain-text snippet around the match.")
    tags: list[str] = Field(description="Frontmatter tags, in file order.")
    modified_at: datetime = Field(description="File mtime in the configured timezone.")


class SearchResponse(BaseModel):
    results: list[SearchResultItem]
    next_cursor: str | None = Field(
        default=None,
        description=(
            "Opaque pagination token. Pass it back as `cursor` with the same "
            "query/folder/tags to fetch the next page. Null when there is no "
            "further page."
        ),
    )
    skipped_count: int = Field(
        default=0,
        ge=0,
        description=(
            "Notes that matched the requested scope but could not be read while "
            "scanning the Vault for this search, and were excluded from `results` "
            "as a result — never the difference between `results` and the total "
            "number of matches, which is what `next_cursor` accounts for. 0 on an "
            "ordinary search."
        ),
    )


class VaultTreeEntry(BaseModel):
    type: Literal["folder", "note"]
    name: str = Field(description="File or folder name, without any path component.")
    path: str = Field(description="Vault-relative path.")
    modified_at: datetime | None = Field(
        default=None, description="File mtime in the configured timezone. Null for folders."
    )


class VaultTreeResponse(BaseModel):
    folder: str = Field(description="Vault-relative folder that was listed. Empty for the root.")
    entries: list[VaultTreeEntry]
    next_cursor: str | None = Field(
        default=None,
        description=(
            "Opaque pagination token. Pass it back as `cursor` with the same "
            "`folder` to fetch the next page. Null when there is no further page."
        ),
    )


class VaultNameCount(BaseModel):
    name: str
    note_count: int


class VaultSummaryResponse(BaseModel):
    note_count: int = Field(description="Total number of Markdown notes in the vault.")
    total_bytes: int = Field(description="Combined file size of every Markdown note, in bytes.")
    folder_count: int = Field(
        description="Number of distinct folders that directly contain a note."
    )
    top_level_folders: list[VaultNameCount] = Field(
        description="Note counts per top-level folder. A note directly at the vault root "
        "is not counted here."
    )
    tags: list[VaultNameCount] = Field(
        description="Most common frontmatter tags, folded for case/width, up to `top_tags_limit`."
    )
    last_modified_at: datetime | None = Field(
        default=None, description="Newest note mtime in the configured timezone. Null if empty."
    )
    skipped_count: int = Field(
        description="Notes that could not be read while aggregating and were excluded."
    )


class NoteResponse(BaseModel):
    id: str = Field(description="Vault-relative path of the note.")
    path: str = Field(description="Vault-relative path of the note.")
    title: str = Field(description="Frontmatter `title`, else the file name without .md.")
    frontmatter: dict[str, object] = Field(
        description="Parsed YAML frontmatter. Empty when absent or unparseable."
    )
    content: str = Field(description="Markdown body with the frontmatter block removed.")
    modified_at: datetime = Field(description="File mtime in the configured timezone.")
    truncated: bool = Field(
        description="True when the note exceeded MAX_NOTE_SIZE_BYTES and content was cut."
    )


ExportMode = Literal[
    "summary", "technical", "history", "full", "procedure", "issue", "reference"
]

# Every string field renders as exactly one Markdown line, so a per-string cap
# plus a per-list cap is what bounds the rendered note deterministically.
# MAX_REQUEST_BYTES (enforced pre-parse by /mcp's own SDK middleware — REST
# is health-only and takes no body, docs/adr/0010-*.md) stays the outer
# backstop; note creation itself has no byte cap (docs/adr/0005-*.md records
# this as an accepted gap, not an oversight).
_MAX_LINE_CHARS = 1_000
_MAX_LABEL_CHARS = 200
_MAX_LIST_ITEMS = 30
_MAX_TLDR_ITEMS = 8
_MAX_TIMELINE_ITEMS = 50
_MAX_STEP_ITEMS = 50

# `full` mode's own pair (docs/adr/0013-*.md), deliberately not shared with
# _MAX_LIST_ITEMS: `topics[].points` is a list[BodyItem] — paragraph/bullet/
# code/table/quote — so it grows faster per topic than the plain list[Line]
# fields that _MAX_LIST_ITEMS still bounds (decisions, next_actions, etc.).
# Both were raised from production reports of a single `full` export hitting
# the old 20/30 caps (29 topics, 37 points in one topic).
_MAX_TOPIC_ITEMS = 50
_MAX_TOPIC_POINT_ITEMS = 100

_MAX_DEFINITION_ITEMS = 50
_MAX_TAG_ITEMS = 20

# Bounds for verbatim/structure-preserving code content (docs/adr/0009-*.md).
# _MAX_CODE_CHARS is 8x Line's own cap — enough for a compose.yaml/Dockerfile/
# mid-sized script/CLI log excerpt; worst-case UTF-8 is 4 bytes per Unicode
# code point (len() counts code points, not bytes), so 8_000 chars is at most
# ~32 KiB.
_MAX_CODE_CHARS = 8_000
_MAX_BLOCKS_PER_STEP = 12
_MAX_CODE_BLOCK_ITEMS = 10

# Bounds for structured tables (docs/adr/0011-*.md). _MAX_TABLE_COLUMNS caps
# both a header row and every data row at the schema layer (defense in
# depth — the exact "every row has the same length as headers" check still
# has to live in app/services/chat_export.py, since no single Field can see
# two sibling fields at once). _MAX_TABLE_ROWS is a realistic ceiling for a
# table pasted into a note, not a hard Markdown constraint.
_MAX_TABLE_COLUMNS = 12
_MAX_TABLE_ROWS = 100

# Bound for a blockquote/callout's body (docs/adr/0011-*.md) — a realistic
# ceiling for a note or warning pasted into a note, matching the other
# rich-block container caps (_MAX_BLOCKS_PER_STEP, _MAX_CODE_BLOCK_ITEMS).
_MAX_QUOTE_LINES = 30

# Maximum nesting depth for a BulletBlock (docs/adr/0011-*.md): a realistic
# ceiling for a note's own nested lists, not a hard Markdown constraint —
# app/services/chat_export.py separately rejects any *jump* deeper than one
# level from the previous bullet, regardless of this cap.
_MAX_BULLET_DEPTH = 3

# _MAX_TOTAL_BLOCK_CHARS bounds the sum of every client-supplied string inside
# every rich block in one export — code content/label, table label/headers/
# rows, quote title/lines, and a paragraph's lines plus its line-break
# separators (docs/adr/0012-*.md; app/services/chat_export.py enforces this
# on normalised data, since no single-field Field(max_length=...) can see a
# cross-field/cross-block total). This is a conservative budget on *input*
# payload, not a guarantee about the rendered Markdown's byte size — escaping
# (table cells, code fences) can only grow the text further, and it is not a
# guarantee about the created note's final byte size either:
# Settings.max_note_size_bytes is enforced only by search_notes, read_note,
# and append_inbox_note (app/application.py) — the create_inbox_note/
# create_chat_export_note write path has no byte cap of its own (an accepted
# gap, not something this budget closes). Creation's actual outer limits
# remain MAX_REQUEST_BYTES (the MCP transport's pre-parse backstop) and the
# per-field/per-item schema caps.
_MAX_TOTAL_BLOCK_CHARS = 100_000

# Markdown fence info-string safety: no line breaks, no backtick, no control
# characters, and non-empty when present. Deliberately permissive within that
# (does not enumerate a language vocabulary) — Field(pattern=...) rejects
# anything outside this set instead of silently mangling it.
_LANGUAGE_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9+#._-]{0,31}$"

# Public (unlike the _MAX_* constants above): app/application.py reads this
# too, as the max_links argument to app.services.related_notes.resolve_related_notes.
MAX_RELATED_NOTES = 10

Line = Annotated[str, Field(max_length=_MAX_LINE_CHARS)]
Label = Annotated[str, Field(min_length=1, max_length=_MAX_LABEL_CHARS)]
# No min_length, unlike Label: an empty/whitespace-only tag is normalised
# away by chat_export._normalise_tags, the same "pydantic allows it, the
# formatter drops it" convention every other simple list (Line) already
# follows. Sharing Label here (min_length=1) would reject "" before the
# formatter ever saw it, contradicting that convention for tags specifically.
Tag = Annotated[str, Field(max_length=_MAX_LABEL_CHARS)]
# No length bound either, unlike every other string field in this module:
# an empty, over-length, or otherwise unresolvable candidate must be silently
# omitted by app.services.related_notes.resolve_related_notes (via
# path_security's own MAX_PATH_LENGTH check, which raises a GatewayError that
# service already catches) rather than rejected by the schema. A
# Field(max_length=...) here would reject the *entire* export over one
# oversized candidate — exactly the "must not block export" contract issue
# #13 forbids for an individual invalid path. Only the list's own item COUNT
# is schema-enforced (ChatExport.related_notes's max_length=MAX_RELATED_NOTES
# below); an individual item's shape never is.
NotePath = str

# Verbatim/structure-preserving code content (docs/adr/0009-*.md). Unlike
# Line, no attempt is made to bound this to "one Markdown line" — a fenced
# code block's whole point is to carry indentation and internal blank lines
# untouched. min_length=1 rejects an empty code block at the schema layer
# (issue reported in review: a rich-object CodeBlock carries no value at all
# if empty, unlike a legacy plain-string step, which must stay permissive —
# see TextBlock.content's own comment for why the two are deliberately not
# symmetric).
CodeContent = Annotated[str, Field(min_length=1, max_length=_MAX_CODE_CHARS)]

# Bound for a body field's paragraph prose (docs/adr/0012-*.md). Deliberately
# NOT Line's 1_000 — Line's cap exists because "every string field renders
# as exactly one Markdown line" (see the comment above _MAX_LINE_CHARS), and
# a paragraph is deliberately *not* one line, so that premise does not apply.
# The right sibling is CodeContent, the other multiline-capable type, hence
# the same 8_000. This is not the binding constraint in practice: 8_000 x
# _MAX_LIST_ITEMS (30) = 240_000 in a single field already exceeds
# _MAX_TOTAL_BLOCK_CHARS (100_000) — and 8_000 x _MAX_TOPIC_POINT_ITEMS (100)
# = 800_000 for topics[].points does too — so the shared budget
# (app/services/chat_export.py's _paragraph_chars) binds first — see that
# module for why paragraph content, unlike a bullet's, must be counted
# there.
_MAX_PARAGRAPH_CHARS = 8_000

# No min_length, unlike CodeContent: ParagraphBlock is the bare-string
# shorthand's target (see _coerce_body_item below), so it must stay at least
# as permissive as the plain list[Line] shape it replaces, including an
# empty ""  — dropped by app/services/chat_export.py's normalisation, the
# same "pydantic allows it, the formatter drops it" convention every other
# plain-string field already follows (TextBlock.content, BulletBlock.content).
ParagraphContent = Annotated[str, Field(max_length=_MAX_PARAGRAPH_CHARS)]


class TextBlock(BaseModel):
    """One paragraph of ordinary text inside a `ProcedureStep`.

    `content` reuses `Line` (no `min_length`) rather than a stricter type: a
    legacy plain-string `steps` item is coerced into exactly one `TextBlock`
    (see `_coerce_step` below), and that coercion must not reject anything
    the old `list[Line]` shape already accepted. An empty/whitespace-only
    `content` is dropped by app/services/chat_export.py's normalisation, the
    same "pydantic allows it, the formatter drops it" convention every other
    plain-string field already follows.
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["text"]
    content: Line = Field(description="Plain text for this part of the step.")


class CodeBlock(BaseModel):
    """One fenced code block: a command, a config file, a log, CLI output —
    anything whose line breaks and indentation must survive verbatim.

    Rendered as-is inside a dynamically-sized Markdown fence (docs/adr/
    0009-*.md): no line-breaking, no whitespace collapsing, no Markdown
    escaping is applied to `content`. Unlike `TextBlock.content`, `content`
    here has `min_length=1` — a rich-object code block carries no meaning
    when empty, and (unlike a legacy plain-string step) there is no
    backward-compatibility reason to accept one.
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["code"]
    language: str | None = Field(
        default=None,
        pattern=_LANGUAGE_PATTERN,
        description="Optional Markdown fence info string, e.g. 'bash', 'yaml', 'json'.",
    )
    label: str | None = Field(
        default=None,
        max_length=_MAX_LABEL_CHARS,
        description=(
            "Optional caption shown directly above the code, e.g. a file name "
            "('compose.yaml') or a description ('起動コマンド'). Rendered as "
            "plain text, never as a Markdown heading or emphasis."
        ),
    )
    content: CodeContent = Field(
        description=(
            "The code/config/log/output itself. Rendered verbatim/structure-"
            "preserving: indentation and blank lines are kept exactly, but "
            "this is not byte-level lossless — line endings are normalised "
            "to LF, non-tab/newline control characters are stripped, and at "
            "most one trailing newline is collapsed (a second one is kept "
            "as a deliberate blank line). See docs/adr/0009-*.md."
        )
    )


TableRow = Annotated[list[Line], Field(max_length=_MAX_TABLE_COLUMNS)]


class TableBlock(BaseModel):
    """One Markdown table: structured input, not a raw Markdown string
    (docs/adr/0011-*.md). Unlike `CodeBlock.content`, a table is not a
    self-closing construct — a missing delimiter row or a mismatched column
    count degrades silently to a plain paragraph or drops cells rather than
    raising, so the Gateway generates the table's Markdown itself from
    `headers`/`rows` instead of accepting client-written GFM syntax.

    `headers`/`rows` length-matching and `alignments` length-matching are
    combination checks the schema cannot express (no `Field` sees two
    sibling fields at once), so they are enforced in
    app/services/chat_export.py on normalised data, like every other
    cross-field check in that module.
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["table"]
    label: str | None = Field(
        default=None,
        max_length=_MAX_LABEL_CHARS,
        description=(
            "Optional caption shown directly above the table. Rendered as "
            "plain text, never as a Markdown heading or emphasis."
        ),
    )
    headers: list[Line] = Field(
        min_length=1,
        max_length=_MAX_TABLE_COLUMNS,
        description=(
            "Column headers, left to right. Each must be non-empty after "
            "trimming — an unnamed column is not accepted."
        ),
    )
    alignments: list[Literal["left", "center", "right"]] | None = Field(
        default=None,
        description=(
            "Optional per-column alignment, same length and order as "
            "headers. Omit for the Markdown default (no explicit alignment)."
        ),
    )
    rows: list[TableRow] = Field(
        default_factory=list,
        max_length=_MAX_TABLE_ROWS,
        description=(
            "Data rows, each a list of cells in column order. Every row "
            "must have exactly as many cells as headers — pad with an empty "
            "string rather than omitting a cell. A cell may be empty."
        ),
    )


class QuoteBlock(BaseModel):
    """One blockquote, or an Obsidian callout (docs/adr/0011-*.md):
    ``> line`` for each of `lines`, optionally preceded by a
    ``> [!callout] title`` header line.

    `callout` is a pattern, not an enumerated vocabulary — the same choice
    `CodeBlock.language` already makes (docs/adr/0009-*.md decision 5) —
    since Obsidian accepts both its own built-in callout types and
    arbitrary custom ones; the Gateway has no reason to maintain a list.
    `title` is only meaningful alongside `callout`: a plain blockquote has
    no header line to put a title on.
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["quote"]
    callout: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z][A-Za-z0-9-]{0,31}$",
        description=(
            "Optional Obsidian callout type, e.g. 'note', 'warning', 'tip', or "
            "a custom type — any of these render '> [!callout] ...' instead of "
            "a plain blockquote. Omit for an ordinary blockquote."
        ),
    )
    title: str | None = Field(
        default=None,
        max_length=_MAX_LABEL_CHARS,
        description="Optional callout header text. Only valid together with callout.",
    )
    lines: list[Line] = Field(
        min_length=1,
        max_length=_MAX_QUOTE_LINES,
        description="The quoted text, one line per item.",
    )


StepBlock = Annotated[
    TextBlock | CodeBlock | TableBlock | QuoteBlock, Field(discriminator="type")
]


class ProcedureStep(BaseModel):
    """One step of a `procedure` export, as an ordered mix of text and code.

    `blocks` must start with a `TextBlock` (app/services/chat_export.py
    enforces this after normalisation — CommonMark cannot represent a step
    whose first line is a bare fence without inserting a blank line that
    would split the list and break its numbering). `min_length=1`: a step
    with no blocks carries no meaning, unlike a legacy plain-string step
    (which the schema never lets become empty in this shape at all — see
    `_coerce_step`).
    """

    model_config = ConfigDict(extra="forbid")

    blocks: list[StepBlock] = Field(
        min_length=1,
        max_length=_MAX_BLOCKS_PER_STEP,
        description=(
            "Ordered text/code/table/quote parts of this step, in the order they "
            "should appear."
        ),
    )


def _coerce_step(value: object) -> object:
    """Backward-compatible shorthand: a bare string step becomes a
    `ProcedureStep` with exactly one `TextBlock`. Existing callers sending
    `steps: ["do it", ...]` keep working unchanged — this is the only place
    that equivalence is expressed.
    """
    if isinstance(value, str):
        return {"blocks": [{"type": "text", "content": value}]}
    return value


StepInput = Annotated[
    ProcedureStep,
    BeforeValidator(_coerce_step, json_schema_input_type=Line | ProcedureStep),
]


class ParagraphBlock(BaseModel):
    """One or more paragraphs of ordinary prose inside a body field's rich
    block sequence (docs/adr/0012-*.md) — what a bare string in a body field
    means (see `_coerce_body_item` below).

    Line breaks and blank lines in `content` are preserved: a single "\\n"
    stays a line break inside one Markdown paragraph, "\\n\\n" starts a
    second paragraph. Unlike every other plain-string field in this module,
    `content` is deliberately not run through the single-line canonicaliser
    (app/services/chat_export.one_line) — a paragraph's whole point is that
    its structure survives. Inline Markdown (`**bold**`, `` `code` ``,
    links) renders live; a line that would otherwise open a new Markdown
    block (a leading '#', '>', '-', a fence, a setext underline, a table
    delimiter row) is escaped so it renders as literal text instead.

    Not part of `StepBlock`: a `ProcedureStep`'s continuation lines are
    indented to its numbered marker's width (docs/adr/0009-*.md), a
    different problem with its own indent rules that this block does not
    solve.
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["paragraph"]
    content: ParagraphContent = Field(
        description=(
            "Prose. Line breaks and blank lines are preserved — a single "
            "'\\n' stays a line break, '\\n\\n' starts a second paragraph. "
            "Inline Markdown (**bold**, `code`, links) renders live; text "
            "that would open a new Markdown block (a leading '#', '>', "
            "'-', a fence, a table delimiter row) is escaped so it renders "
            "literally."
        )
    )


class BulletBlock(BaseModel):
    """One bullet-list item inside a body field's rich block sequence
    (docs/adr/0011-*.md) — the direct analogue of `TextBlock` for a body
    field rather than a `ProcedureStep`.

    `type` is `"bullet"`, not `"text"`: this model represents a list item,
    not prose, and `ProcedureStep`'s own `TextBlock` (also `content: Line`,
    but a continuation paragraph, never a bullet) already uses `"text"` for
    a different rendering — reusing the same discriminator value for two
    models with different meaning would be confusing, not merely redundant.
    `ParagraphBlock` (docs/adr/0012-*.md) is the analogue for prose instead;
    use this only for an actual list item.

    `content` reuses `Line` (no `min_length`) for the same reason
    `TextBlock.content` does: an empty/whitespace-only `content` is dropped
    by app/services/chat_export.py's normalisation like every other plain
    string list already is.

    `depth` is validated only for its own range here; the *sequence* rule —
    the first bullet in a run must start at depth 0, and every following
    bullet may nest at most one level deeper than the one before it — is a
    cross-item check app/services/chat_export.py enforces on normalised
    data, the same split ADR-0005 decision 6 already draws between
    per-field pydantic bounds and combination checks. A requested depth
    that cannot be honoured is rejected outright, never silently clamped to
    the nearest valid depth — the same fail-closed rule already applied to
    a mismatched table row (docs/adr/0011-*.md).
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["bullet"]
    content: Line = Field(description="Text for this bullet.")
    depth: int = Field(
        default=0,
        ge=0,
        le=_MAX_BULLET_DEPTH,
        description=(
            "Nesting depth: 0 for a top-level bullet, 1 for a bullet nested "
            "one level under the previous one, and so on. The first bullet "
            "after the start of a list (or after a table/quote/code block) "
            "must be 0; every later bullet may be at most one deeper than "
            "the bullet immediately before it — never deeper, and never "
            "negative."
        ),
    )
    checked: bool | None = Field(
        default=None,
        description=(
            "Set to render this bullet as a GFM task-list item: false for "
            "an open checkbox ('- [ ]'), true for a checked one ('- [x]'). "
            "Omit for an ordinary bullet."
        ),
    )


BodyBlock = Annotated[
    ParagraphBlock | BulletBlock | CodeBlock | TableBlock | QuoteBlock,
    Field(discriminator="type"),
]


def _coerce_body_item(value: object) -> object:
    """Shorthand: a bare string becomes a `ParagraphBlock`
    (docs/adr/0012-*.md, superseding ADR-0011's bare-string-is-a-bullet
    shorthand) — the direct analogue of `_coerce_step` for a body field's
    rich block sequence. A client wanting an actual list item must send an
    explicit `{"type": "bullet", ...}` instead.

    This is a deliberate breaking change to what a bare string renders as
    (bullet -> paragraph); no migration of already-written Vault notes is
    performed, since only new exports are affected.
    """
    if isinstance(value, str):
        return {"type": "paragraph", "content": value}
    return value


BodyItem = Annotated[
    BodyBlock,
    BeforeValidator(_coerce_body_item, json_schema_input_type=ParagraphContent | BodyBlock),
]


class TimelineEntry(BaseModel):
    """One dated or ordered event, for `history` mode."""

    model_config = ConfigDict(extra="forbid")

    when: str | None = Field(
        default=None,
        max_length=_MAX_LABEL_CHARS,
        description=(
            "When it happened, exactly as the conversation stated it — a date, a "
            "phase name, or a relative marker such as 'after the review'. Omit it "
            "when the conversation did not say; never guess a date."
        ),
    )
    event: Line = Field(description="What happened, in one sentence.")


class TopicSection(BaseModel):
    """One '###' subsection of a `full` export."""

    model_config = ConfigDict(extra="forbid")

    heading: Label = Field(
        description="Short label for this topic, rendered as a '###' subheading."
    )
    points: list[BodyItem] = Field(
        default_factory=list,
        max_length=_MAX_TOPIC_POINT_ITEMS,
        description="What was covered under this topic.",
    )


class TermDefinition(BaseModel):
    """One term/description pair, for `reference` mode."""

    model_config = ConfigDict(extra="forbid")

    term: Label = Field(description="The term being defined.")
    description: Line = Field(description="What it means, in one sentence.")


class ChatExport(BaseModel):
    """Structured summary of a conversation, ready to be formatted into a note.

    One flat model rather than a discriminated union, because `mode` must
    default to "summary" when the client omits it and a pydantic discriminated
    union requires the tag to be present. Fields that do not belong to the
    selected `mode` are rejected by app/services/chat_export.py — inside the
    tool body, so the rejection travels through _McpCall's GatewayError
    conversion instead of the SDK's raw ToolError path.

    `related_notes` carries only the client's candidate paths — it is not what
    gets rendered. app/services/related_notes.py re-verifies every path
    against the Vault (this model cannot: it has no filesystem access), and
    app/services/chat_export.render_chat_export takes the verified survivors
    through a separate `verified_related_notes` argument, never this field
    directly. See docs/adr/0006-verified-related-note-wikilinks.md.
    """

    model_config = ConfigDict(extra="forbid")

    mode: ExportMode = Field(
        default="summary",
        description=(
            "Which export shape to render. Use 'summary' for a plain 'summarise "
            "this and save it' request and whenever no other mode clearly fits. "
            "'technical': a design or implementation discussion. 'history': how "
            "something changed over time. 'full': a long conversation covering "
            "several distinct topics. 'procedure': repeatable steps someone will "
            "follow later. 'issue': a problem, its investigation, and its cause. "
            "'reference': terms, facts, and examples to look up later."
        ),
    )

    # --- Common sections, present in every mode, in this rendered order ------
    tldr: list[Line] = Field(
        min_length=1,
        max_length=_MAX_TLDR_ITEMS,
        description=(
            "Required. Normally 2-5 short sentences covering the subject, the "
            "conclusion, and the current state. One sentence per item; they are "
            "joined into a single paragraph directly under the title."
        ),
    )
    decisions: list[BodyItem] = Field(
        default_factory=list,
        max_length=_MAX_LIST_ITEMS,
        description=(
            "Only conclusions that were actually adopted or choices that were "
            "explicitly agreed. Not options still under discussion, and not "
            "things left to do — those belong in unresolved_issues and "
            "next_actions, never here. Leave empty when nothing was decided."
        ),
    )
    unresolved_issues: list[BodyItem] = Field(
        default_factory=list,
        max_length=_MAX_LIST_ITEMS,
        description=(
            "Questions still open: things not yet decided or not yet known. Keep "
            "this separate from next_actions — an open question is not a task, "
            "and the two must never be merged. Leave empty when nothing is open."
        ),
    )
    next_actions: list[BodyItem] = Field(
        default_factory=list,
        max_length=_MAX_LIST_ITEMS,
        description=(
            "Concrete things someone will do next. Keep this separate from "
            "unresolved_issues — a task is not an open question, and the two "
            "must never be merged. Leave empty when there is nothing to do next."
        ),
    )
    related_notes: list[NotePath] = Field(
        default_factory=list,
        max_length=MAX_RELATED_NOTES,
        description=(
            "Vault-relative .md paths of existing notes this conversation "
            "relates to — call search_notes first and pass paths exactly as it "
            "returned them; never invent or guess a path. Usually 3-5 items, "
            "at most 10. The Gateway re-verifies every path when writing the "
            "note: a path it cannot verify is left out rather than blocking "
            "the export, so the rendered '## 関連ノート' section — not this "
            "input — is the record of what actually got linked."
        ),
    )
    sources: list[BodyItem] = Field(
        default_factory=list,
        max_length=_MAX_LIST_ITEMS,
        description=(
            "Where the information came from: a URL, a document name, a "
            "command that was run."
        ),
    )
    code_blocks: list[CodeBlock] = Field(
        default_factory=list,
        max_length=_MAX_CODE_BLOCK_ITEMS,
        description=(
            "Standalone code that does not belong to any single procedure step: "
            "a finished config file, a complete script, reference code, an "
            "appendix log. Rendered as an optional '## コード' section, omitted "
            "entirely when empty — available in every mode, not only "
            "'procedure'. Never move a procedure step's code here: code that "
            "belongs to a step goes in that step's own export.steps[].blocks, "
            "so the procedure keeps its order (text -> code -> text -> ...)."
        ),
    )

    # --- summary mode only -----------------------------------------------------
    # Note (docs/adr/0012-*.md): keep the "<mode> mode only." prefix on any
    # future edit to these descriptions — tests/test_mcp_tools.py's
    # _FIELD_OWNER_MODES-driven check asserts every mode-specific field's
    # description names its owning mode(s).
    overview: list[BodyItem] = Field(
        default_factory=list,
        max_length=_MAX_LIST_ITEMS,
        description="summary mode only. What the conversation was about.",
    )
    key_points: list[BodyItem] = Field(
        default_factory=list,
        max_length=_MAX_LIST_ITEMS,
        description="summary mode only. What is worth remembering.",
    )

    # --- technical mode only ---------------------------------------------------
    context: list[BodyItem] = Field(
        default_factory=list,
        max_length=_MAX_LIST_ITEMS,
        description="technical mode only. The problem or constraints the design starts from.",
    )
    design: list[BodyItem] = Field(
        default_factory=list,
        max_length=_MAX_LIST_ITEMS,
        description="technical mode only. The design or approach itself.",
    )
    implementation_notes: list[BodyItem] = Field(
        default_factory=list,
        max_length=_MAX_LIST_ITEMS,
        description=(
            "technical mode only. Details that matter when implementing: file "
            "names, function names, gotchas."
        ),
    )
    verification: list[BodyItem] = Field(
        default_factory=list,
        max_length=_MAX_LIST_ITEMS,
        description=(
            "technical and procedure modes only. How to confirm it works: tests, "
            "commands, expected output."
        ),
    )

    # --- history mode only ------------------------------------------------------
    timeline: list[TimelineEntry] = Field(
        default_factory=list,
        max_length=_MAX_TIMELINE_ITEMS,
        description=(
            "history mode only, and required for it. Events in the order they "
            "happened, oldest first."
        ),
    )
    turning_points: list[BodyItem] = Field(
        default_factory=list,
        max_length=_MAX_LIST_ITEMS,
        description="history mode only. The moments where the direction actually changed.",
    )

    # --- full mode only ----------------------------------------------------------
    topics: list[TopicSection] = Field(
        default_factory=list,
        max_length=_MAX_TOPIC_ITEMS,
        description=(
            "full mode only, and required for it. One entry per distinct topic "
            "the conversation covered, in the order they came up. Each becomes a "
            "'###' subsection."
        ),
    )

    # --- procedure mode only ------------------------------------------------------
    prerequisites: list[BodyItem] = Field(
        default_factory=list,
        max_length=_MAX_LIST_ITEMS,
        description="procedure mode only. What must already be true before starting.",
    )
    steps: list[StepInput] = Field(
        default_factory=list,
        max_length=_MAX_STEP_ITEMS,
        description=(
            "procedure mode only, and required for it. One step per item, in "
            "the order they must be performed. Rendered as a numbered list; do "
            "not put your own numbering in the text. For new exports, send a "
            "ProcedureStep object: {\"blocks\": [...]}, where blocks is an "
            "ordered mix of {\"type\": \"text\", \"content\": ...} and "
            "{\"type\": \"code\", \"language\": ..., \"label\": ..., "
            "\"content\": ...} — that ordering is what preserves the context "
            "of a procedure (e.g. text, then the command, then more text, "
            "then the next command). A bare string is a backward-compatible "
            "shorthand for a step with a single text block; do not use it "
            "when the step involves code. Each step must start with a text "
            "block."
        ),
    )
    rollback: list[BodyItem] = Field(
        default_factory=list,
        max_length=_MAX_LIST_ITEMS,
        description="procedure mode only. How to undo it if it goes wrong.",
    )

    # --- issue mode only ------------------------------------------------------------
    symptom: list[BodyItem] = Field(
        default_factory=list,
        max_length=_MAX_LIST_ITEMS,
        description=(
            "issue mode only, and required for it. What was observed going wrong "
            "— the behaviour, not the cause."
        ),
    )
    environment: list[BodyItem] = Field(
        default_factory=list,
        max_length=_MAX_LIST_ITEMS,
        description="issue mode only. Versions, hosts, configuration relevant to the problem.",
    )
    investigation: list[BodyItem] = Field(
        default_factory=list,
        max_length=_MAX_LIST_ITEMS,
        description="issue mode only. What was checked and what it showed, in order.",
    )
    root_cause: list[BodyItem] = Field(
        default_factory=list,
        max_length=_MAX_LIST_ITEMS,
        description=(
            "issue mode only. The established cause. Leave empty when the cause "
            "was not established — do not put a guess here."
        ),
    )
    workaround: list[BodyItem] = Field(
        default_factory=list,
        max_length=_MAX_LIST_ITEMS,
        description="issue mode only. What makes it usable without a full fix.",
    )

    # --- reference mode only ------------------------------------------------------
    definitions: list[TermDefinition] = Field(
        default_factory=list,
        max_length=_MAX_DEFINITION_ITEMS,
        description="reference mode only. Term and meaning pairs.",
    )
    facts: list[BodyItem] = Field(
        default_factory=list,
        max_length=_MAX_LIST_ITEMS,
        description="reference mode only. Standalone facts worth looking up later.",
    )
    examples: list[BodyItem] = Field(
        default_factory=list,
        max_length=_MAX_LIST_ITEMS,
        description="reference mode only. Concrete examples.",
    )

    # --- metadata (frontmatter) --------------------------------------------------
    project: str | None = Field(
        default=None,
        max_length=_MAX_LABEL_CHARS,
        description=(
            "Optional. The project or repository this belongs to, when the "
            "conversation names one. Omit it rather than guessing."
        ),
    )
    conversation_type: str | None = Field(
        default=None,
        max_length=_MAX_LABEL_CHARS,
        description=(
            "Optional. A short free-form label for the kind of conversation, e.g. "
            "'design', 'debugging', 'review'. Omit it rather than guessing."
        ),
    )
    tags: list[Tag] = Field(
        default_factory=list,
        max_length=_MAX_TAG_ITEMS,
        description=(
            "Open-ended Obsidian tags you choose for this note. No fixed "
            "vocabulary. Write them without a leading '#'; spaces are replaced "
            "with '-'. title, created, updated, source and export_mode are "
            "generated by the Gateway and cannot be set here."
        ),
    )


class CreatedNoteResponse(BaseModel):
    id: str = Field(description="Vault-relative path of the created note.")
    path: str = Field(description="Vault-relative path of the created note.")
    title: str = Field(
        description=(
            "Sanitised file-name stem of the created note, without .md. May differ "
            "from the H1 and frontmatter title of a structured export."
        )
    )
    modified_at: datetime = Field(description="Creation time in the configured timezone.")
    related_notes_linked: int = Field(
        ge=0,
        description=(
            "Number of export.related_notes candidates that were verified and "
            "rendered as wikilinks. Always 0 for the raw content/frontmatter path."
        ),
    )
    related_notes_skipped: int = Field(
        ge=0,
        description=(
            "Number of export.related_notes candidates omitted because they "
            "could not be verified — invalid, missing, or duplicate. Always 0 "
            "for the raw content/frontmatter path. Submitting more than the "
            "documented maximum is a separate, harder failure: the whole "
            "request is rejected before this count is ever produced."
        ),
    )


class AppendedNoteResponse(BaseModel):
    id: str = Field(description="Vault-relative path of the note.")
    path: str = Field(description="Vault-relative path of the note.")
    modified_at: datetime = Field(description="Modification time in the configured timezone.")
    appended_bytes: int = Field(
        description="Bytes added to the file, including any inserted separator/terminator."
    )


# --- Duplicate-note detection (issue #14, docs/adr/0007-*.md) ------------------

# Mirrors MAX_RELATED_NOTES's own limit; both bound a client-supplied list that
# app/services/duplicate_notes.py re-derives/re-verifies rather than trusting.
MAX_DUPLICATE_CANDIDATES = 10
MAX_DUPLICATE_KEYWORDS = 10

DuplicateConfidence = Literal["high", "medium", "low"]

# Deliberately excludes "fingerprint" — exact-content fingerprinting is out of
# scope for this first implementation (ADR-0007's "Alternatives considered").
DuplicateMatchSignal = Literal["exact_title", "normalized_title", "project", "keywords"]

# The decision-flow outcome, not a permission: the Gateway never infers write
# approval from similarity (AGENTS.md, issue #14's safety constraints). A
# client still decides for itself whether and how to prompt the user.
DuplicateRecommendation = Literal["create", "confirm", "choose"]


class DuplicateCandidate(BaseModel):
    """One existing inbox note that may already cover the same conversation.

    No absolute path, note body, or excerpt — only what is needed to decide
    between creating a new note and appending to this one. ``score`` is
    intentionally not exposed: it is an internal sort key
    (docs/adr/0007-*.md), not a stable contract a client should depend on.
    """

    path: str = Field(
        description=(
            "Full vault-relative path, directly inside 00_Inbox/ChatGPT. Already "
            "validated as a syntactically acceptable append_inbox_note target."
        )
    )
    title: str = Field(description="Frontmatter `title`, else the file name without .md.")
    project: str | None = Field(
        default=None, description="This candidate's frontmatter `project`, if it has one."
    )
    tags: list[str] = Field(description="Frontmatter tags, in file order.")
    confidence: DuplicateConfidence = Field(
        description="How strong a duplicate signal this candidate is, most to least: "
        "high, medium, low."
    )
    matched_signals: list[DuplicateMatchSignal] = Field(
        description=(
            "Which signals matched. Title signals are mutually exclusive: a candidate "
            "with `exact_title` never also carries `normalized_title` for the same match."
        )
    )
    matched_keywords: list[str] = Field(
        description="Which of the input keywords matched this candidate, in input order."
    )
    modified_at: datetime = Field(description="File mtime in the configured timezone.")


class DuplicateCandidatesResponse(BaseModel):
    candidates: list[DuplicateCandidate] = Field(
        description="Up to `limit` candidates, most confident first."
    )
    candidate_count: int = Field(
        ge=0,
        description=(
            "Total number of candidates found before `limit` was applied. "
            "`recommendation` is decided from this full set, not from `candidates`."
        ),
    )
    truncated: bool = Field(
        description="True when `candidate_count` exceeds the number of `candidates` returned."
    )
    recommendation: DuplicateRecommendation = Field(
        description=(
            "'create': no high/medium candidate — proceed without asking. 'confirm': "
            "exactly one high candidate and nothing else at high/medium — ask the user "
            "to choose new/append/cancel. 'choose': more than one high/medium candidate, "
            "or only medium ones — show the candidate list and require an explicit pick. "
            "This is advisory, not write authorization: the Gateway never blocks or "
            "gates create_inbox_note/append_inbox_note on this value."
        )
    )
    scanned_count: int = Field(ge=0, description="Notes directly inside the inbox that were read.")
    skipped_count: int = Field(
        ge=0,
        description=(
            "Notes directly inside the inbox that were excluded: unreadable "
            "frontmatter, or a file name that would not be accepted as an "
            "append_inbox_note target."
        ),
    )
