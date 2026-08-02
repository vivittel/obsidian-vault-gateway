"""Request and response schemas.

Response models are fixed and fully typed on purpose: section 12 of the plan
requires stable schemas with explicit required fields, and the same models
back both the REST responses here and the MCP tools' structured output
(app/mcp_server.py) — one schema, two transports.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

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


class InboxNoteCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(
        min_length=1,
        max_length=300,
        description=(
            "Human-readable title. The file name is derived from it by the API; "
            "callers cannot choose a path."
        ),
    )
    content: str = Field(
        max_length=_MAX_CONTENT_CHARS,
        description="Markdown body. Written as-is with LF line endings.",
    )
    frontmatter: dict[str, FrontmatterValue] | None = Field(
        default=None,
        description="Optional YAML frontmatter. Scalars and flat lists of scalars only.",
    )


class CreatedNoteResponse(BaseModel):
    id: str = Field(description="Vault-relative path of the created note.")
    path: str = Field(description="Vault-relative path of the created note.")
    title: str = Field(
        description="Title as stored (may differ from the request after sanitising)."
    )
    modified_at: datetime = Field(description="Creation time in the configured timezone.")


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
