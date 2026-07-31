"""Inbox note creation (IMPLEMENTATION_PLAN sections 6.6 and 17).

The core operation is "create this file without ever overwriting an existing
one, and without leaving a half-written file behind if something goes wrong".
``os.replace()`` cannot do this — it silently overwrites whatever is already at
the destination, which is exactly what section 6.6 forbids. Instead:

1. Write the full content to a hidden temp file inside the inbox directory
   (same filesystem, required for step 2) and fsync it.
2. Try ``os.link()` from the temp file to each candidate name in turn
   (``title.md``, ``title-2.md``, ...). ``os.link`` is atomic and fails with
   ``FileExistsError`` if the target exists — it can never clobber a note that
   is already there.
3. Unlink the temp file and fsync the directory so the create is durable.

The temp file is always cleaned up, including when linking never finds a free
name or the write itself fails.
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from app.exceptions import InternalError, NoteAlreadyExistsError
from app.models import FrontmatterValue
from app.services.filenames import note_file_name, sanitise_title

MAX_SEQUENCE_ATTEMPTS = 100


@dataclass(frozen=True)
class CreatedNote:
    relative: str
    title: str
    modified_at: datetime


def _render_note(*, content: str, frontmatter: dict[str, FrontmatterValue] | None) -> str:
    body = content.replace("\r\n", "\n").replace("\r", "\n")
    if body and not body.endswith("\n"):
        body += "\n"

    if not frontmatter:
        return body

    yaml_block = yaml.safe_dump(
        frontmatter,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    )
    return f"---\n{yaml_block}---\n\n{body}"


def _write_temp_file(inbox_root: Path, text: str) -> Path:
    temp_path = inbox_root / f".tmp-{secrets.token_hex(8)}"
    fd = os.open(temp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError:
        temp_path.unlink(missing_ok=True)
        raise
    return temp_path


def create_inbox_note(
    *,
    inbox_root: Path,
    title: str,
    content: str,
    frontmatter: dict[str, FrontmatterValue] | None,
    timezone: ZoneInfo,
) -> CreatedNote:
    stem = sanitise_title(title)
    text = _render_note(content=content, frontmatter=frontmatter)

    temp_path = _write_temp_file(inbox_root, text)
    try:
        for sequence in range(1, MAX_SEQUENCE_ATTEMPTS + 1):
            file_name = note_file_name(stem, sequence)
            destination = inbox_root / file_name
            try:
                os.link(temp_path, destination)
            except FileExistsError:
                continue
            except OSError as exc:
                raise InternalError(log_detail=f"os.link failed: {exc!r}") from exc
            else:
                _fsync_directory(inbox_root)
                modified_at = datetime.fromtimestamp(
                    destination.stat().st_mtime, tz=timezone
                )
                return CreatedNote(relative=file_name, title=stem, modified_at=modified_at)
        raise NoteAlreadyExistsError(
            log_detail=f"no free sequence number after {MAX_SEQUENCE_ATTEMPTS} attempts"
        )
    finally:
        temp_path.unlink(missing_ok=True)


def _fsync_directory(directory: Path) -> None:
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
