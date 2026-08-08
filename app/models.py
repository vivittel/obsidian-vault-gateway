"""Request and response schemas.

Response models are fixed and fully typed on purpose: section 12 of the plan
requires stable schemas with explicit required fields, and the same models
back both the REST responses here and the MCP tools' structured output
(app/mcp_server.py) — one schema, two transports.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.exceptions import ErrorCode

# Frontmatter accepted on write is restricted to scalars and flat lists of
# scalars. This is the injection boundary: a typed dict means an API caller
# cannot smuggle arbitrary YAML structures (anchors, nested maps, tags) into a
# vault note through the frontmatter field.
FrontmatterScalar = str | int | float | bool | None
FrontmatterValue = FrontmatterScalar | list[FrontmatterScalar]

# Backstop only — MAX_REQUEST_BYTES (default 2 MiB) is enforced by middleware
# before the body is parsed. A body that fits in 2 MiB cannot exceed 2M chars.
_MAX_CONTENT_CHARS = 2_000_000


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
# MAX_REQUEST_BYTES (enforced pre-parse by both transports) stays the outer
# backstop; note creation itself has no byte cap (docs/adr/0005-*.md records
# this as an accepted gap, not an oversight).
_MAX_LINE_CHARS = 1_000
_MAX_LABEL_CHARS = 200
_MAX_LIST_ITEMS = 30
_MAX_TLDR_ITEMS = 8
_MAX_TIMELINE_ITEMS = 50
_MAX_STEP_ITEMS = 50
_MAX_TOPIC_ITEMS = 20
_MAX_DEFINITION_ITEMS = 50
_MAX_TAG_ITEMS = 20

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
    points: list[Line] = Field(
        default_factory=list,
        max_length=_MAX_LIST_ITEMS,
        description="What was covered under this topic, one point per item.",
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
    decisions: list[Line] = Field(
        default_factory=list,
        max_length=_MAX_LIST_ITEMS,
        description=(
            "Only conclusions that were actually adopted or choices that were "
            "explicitly agreed. Not options still under discussion, and not "
            "things left to do — those belong in unresolved_issues and "
            "next_actions, never here. Leave empty when nothing was decided."
        ),
    )
    unresolved_issues: list[Line] = Field(
        default_factory=list,
        max_length=_MAX_LIST_ITEMS,
        description=(
            "Questions still open: things not yet decided or not yet known. Keep "
            "this separate from next_actions — an open question is not a task, "
            "and the two must never be merged. Leave empty when nothing is open."
        ),
    )
    next_actions: list[Line] = Field(
        default_factory=list,
        max_length=_MAX_LIST_ITEMS,
        description=(
            "Concrete things someone will do next, one per item. Keep this "
            "separate from unresolved_issues — a task is not an open question, "
            "and the two must never be merged. Leave empty when there is nothing "
            "to do next."
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
    sources: list[Line] = Field(
        default_factory=list,
        max_length=_MAX_LIST_ITEMS,
        description=(
            "Where the information came from: a URL, a document name, a command "
            "that was run. One per item."
        ),
    )

    # --- summary mode only -----------------------------------------------------
    overview: list[Line] = Field(
        default_factory=list,
        max_length=_MAX_LIST_ITEMS,
        description="summary mode only. What the conversation was about, in a few points.",
    )
    key_points: list[Line] = Field(
        default_factory=list,
        max_length=_MAX_LIST_ITEMS,
        description="summary mode only. The points worth remembering, one per item.",
    )

    # --- technical mode only ---------------------------------------------------
    context: list[Line] = Field(
        default_factory=list,
        max_length=_MAX_LIST_ITEMS,
        description="technical mode only. The problem or constraints the design starts from.",
    )
    design: list[Line] = Field(
        default_factory=list,
        max_length=_MAX_LIST_ITEMS,
        description="technical mode only. The design or approach itself, one point per item.",
    )
    implementation_notes: list[Line] = Field(
        default_factory=list,
        max_length=_MAX_LIST_ITEMS,
        description=(
            "technical mode only. Details that matter when implementing: file "
            "names, function names, gotchas."
        ),
    )
    verification: list[Line] = Field(
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
    turning_points: list[Line] = Field(
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
    prerequisites: list[Line] = Field(
        default_factory=list,
        max_length=_MAX_LIST_ITEMS,
        description="procedure mode only. What must already be true before starting.",
    )
    steps: list[Line] = Field(
        default_factory=list,
        max_length=_MAX_STEP_ITEMS,
        description=(
            "procedure mode only, and required for it. One action per item, in "
            "the order they must be performed. Rendered as a numbered list; do "
            "not put your own numbering in the text."
        ),
    )
    rollback: list[Line] = Field(
        default_factory=list,
        max_length=_MAX_LIST_ITEMS,
        description="procedure mode only. How to undo it if it goes wrong.",
    )

    # --- issue mode only ------------------------------------------------------------
    symptom: list[Line] = Field(
        default_factory=list,
        max_length=_MAX_LIST_ITEMS,
        description=(
            "issue mode only, and required for it. What was observed going wrong "
            "— the behaviour, not the cause."
        ),
    )
    environment: list[Line] = Field(
        default_factory=list,
        max_length=_MAX_LIST_ITEMS,
        description="issue mode only. Versions, hosts, configuration relevant to the problem.",
    )
    investigation: list[Line] = Field(
        default_factory=list,
        max_length=_MAX_LIST_ITEMS,
        description="issue mode only. What was checked and what it showed, in order.",
    )
    root_cause: list[Line] = Field(
        default_factory=list,
        max_length=_MAX_LIST_ITEMS,
        description=(
            "issue mode only. The established cause. Leave empty when the cause "
            "was not established — do not put a guess here."
        ),
    )
    workaround: list[Line] = Field(
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
    facts: list[Line] = Field(
        default_factory=list,
        max_length=_MAX_LIST_ITEMS,
        description="reference mode only. Standalone facts worth looking up later.",
    )
    examples: list[Line] = Field(
        default_factory=list,
        max_length=_MAX_LIST_ITEMS,
        description="reference mode only. Concrete examples, one per item.",
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


class InboxNoteCreateRequest(BaseModel):
    """Exactly one of `content` or `export` is required; sending both, or
    sending neither, is rejected. `export` and `frontmatter` are also
    mutually exclusive — `export`'s frontmatter (title/created/updated/
    source/export_mode/tags) is formatter-owned and cannot be supplied as
    arbitrary free-form frontmatter alongside it. `content`/`frontmatter`
    remain the raw-Markdown path kept for existing callers; `export` is the
    structured chat-export path (issue #12 / docs/adr/0005-*.md).
    """

    model_config = ConfigDict(extra="forbid")

    title: str = Field(
        min_length=1,
        max_length=300,
        description=(
            "Human-readable title. The file name is derived from it by the API; "
            "callers cannot choose a path."
        ),
    )
    content: str | None = Field(
        default=None,
        max_length=_MAX_CONTENT_CHARS,
        description=(
            "Markdown body. Written as-is with LF line endings. Exactly one of "
            "`content` or `export` is required."
        ),
    )
    frontmatter: dict[str, FrontmatterValue] | None = Field(
        default=None,
        description=(
            "Optional YAML frontmatter for the `content` path. Scalars and flat "
            "lists of scalars only. Rejected together with `export`, whose "
            "frontmatter is formatter-owned."
        ),
    )
    export: ChatExport | None = Field(
        default=None,
        description=(
            "Structured chat-export input (issue #12). The Gateway renders this "
            "into deterministic Markdown and frontmatter; the client never "
            "supplies raw Markdown or frontmatter alongside it. Exactly one of "
            "`content` or `export` is required."
        ),
    )

    @model_validator(mode="after")
    def _check_content_and_export_are_mutually_exclusive(self) -> InboxNoteCreateRequest:
        if (self.content is None) == (self.export is None):
            raise ValueError("Exactly one of `content` or `export` is required.")
        if self.export is not None and self.frontmatter is not None:
            raise ValueError("`export` and `frontmatter` cannot be supplied together.")
        return self


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


class InboxNoteAppendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(
        min_length=1,
        max_length=1024,
        description="Vault-relative path of an existing .md note directly inside the inbox.",
    )
    content: str = Field(
        min_length=1,
        max_length=_MAX_CONTENT_CHARS,
        description="Markdown to append. The existing note's line ending is preserved.",
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
