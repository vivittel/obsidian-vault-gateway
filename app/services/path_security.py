"""Path validation — the security core of the gateway (IMPLEMENTATION_PLAN section 7).

Every filesystem access in this service goes through :func:`resolve_read_path` or
:func:`resolve_inbox_write_path`. Nothing else is allowed to build a path from
caller-supplied data.

Two ordering details matter and are easy to get wrong:

1. Symlinks are checked **component by component before** ``resolve()``.
   ``Path.resolve()`` silently follows symlinks, so checking the resolved result
   would accept ``notes/link.md -> /etc/passwd`` whenever the target happened to
   sit inside the vault, and would give no way to reject in-vault symlinks at all.
2. Percent-decoding is applied repeatedly and every round is validated, so a
   doubly-encoded traversal (``%252e%252e%252f`` → ``%2e%2e%2f`` → ``../``) is
   caught at whichever round it becomes visible.
"""

from __future__ import annotations

import os
import re
import stat
from collections.abc import Iterator
from pathlib import Path, PurePosixPath
from typing import NamedTuple
from urllib.parse import unquote

from app.exceptions import (
    InvalidFileTypeError,
    InvalidPathError,
    NoteNotFoundError,
    PathOutsideVaultError,
)

MARKDOWN_SUFFIX = ".md"
MAX_PATH_LENGTH = 1024
MAX_COMPONENT_LENGTH = 255

_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")
_MAX_DECODE_ROUNDS = 3


class ResolvedNote(NamedTuple):
    """An absolute path proven to be inside the vault, plus its vault-relative form.

    ``relative`` is what may appear in responses and logs; ``path`` is for local
    filesystem access only and must never be serialised.
    """

    path: Path
    relative: str


class VaultNote(NamedTuple):
    path: Path
    relative: str
    stat_result: os.stat_result


def _check_syntax(value: str, *, require_markdown: bool) -> None:
    """Reject a path on syntax alone. No filesystem access."""
    if "\x00" in value:
        raise InvalidPathError(log_detail="null byte in path")
    if "\\" in value:
        raise InvalidPathError(log_detail="backslash in path")
    if len(value) > MAX_PATH_LENGTH:
        raise InvalidPathError(log_detail="path exceeds maximum length")
    if value.startswith("/") or _WINDOWS_DRIVE.match(value):
        raise InvalidPathError(log_detail="absolute path")

    parts = value.split("/")
    for part in parts:
        if not part:
            raise InvalidPathError(log_detail="empty path component")
        if part in {".", ".."}:
            raise InvalidPathError(log_detail="relative path component")
        if part.startswith("."):
            raise InvalidPathError(log_detail="hidden path component")
        if len(part) > MAX_COMPONENT_LENGTH:
            raise InvalidPathError(log_detail="path component exceeds maximum length")

    # Hidden files are rejected above, so this only sees ordinary names.
    if require_markdown and not parts[-1].endswith(MARKDOWN_SUFFIX):
        raise InvalidFileTypeError(log_detail="path does not name a .md file")


def _check_every_encoding(value: str, *, require_markdown: bool) -> None:
    """Validate the value as given and after each percent-decoding round.

    Decoding is used for validation only and never rewrites the request: the
    literal input is what gets looked up on disk.
    """
    current = value
    _check_syntax(current, require_markdown=require_markdown)
    for _ in range(_MAX_DECODE_ROUNDS):
        decoded = unquote(current)
        if decoded == current:
            break
        _check_syntax(decoded, require_markdown=require_markdown)
        current = decoded


def normalise_relative_path(raw: str) -> PurePosixPath:
    """Validate a caller-supplied vault-relative note path."""
    if not raw or not raw.strip():
        raise InvalidPathError(log_detail="empty path")
    _check_every_encoding(raw, require_markdown=True)
    return PurePosixPath(raw)


def normalise_relative_dir(raw: str) -> PurePosixPath:
    """Validate a caller-supplied vault-relative directory path (the search ``folder``)."""
    cleaned = raw.strip().strip("/")
    if not cleaned:
        raise InvalidPathError(log_detail="empty folder")
    _check_every_encoding(cleaned, require_markdown=False)
    return PurePosixPath(cleaned)


def _reject_symlinked_components(root: Path, relative: PurePosixPath) -> None:
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise InvalidPathError(log_detail="symlink in path")


def resolve_read_path(raw: str, read_root: Path) -> ResolvedNote:
    """Turn a caller-supplied path into a note inside ``read_root``, or raise.

    ``read_root`` must already be resolved (see :attr:`app.config.Settings.read_root`).
    """
    relative = normalise_relative_path(raw)
    _reject_symlinked_components(read_root, relative)

    candidate = read_root / relative
    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError as exc:
        raise NoteNotFoundError() from exc
    except OSError as exc:  # ELOOP, ENAMETOOLONG, ENOTDIR, ...
        raise InvalidPathError(log_detail=f"unresolvable path: {type(exc).__name__}") from exc

    if not resolved.is_relative_to(read_root):
        raise PathOutsideVaultError(log_detail="resolved path escapes the read root")

    if not stat.S_ISREG(resolved.stat().st_mode):
        raise InvalidFileTypeError(log_detail="path does not name a regular file")

    return ResolvedNote(path=resolved, relative=relative.as_posix())


def resolve_inbox_write_path(file_name: str, inbox_root: Path) -> Path:
    """Validate an already-sanitised file name for writing into the inbox.

    Takes a bare file name, never a path: the inbox is flat and callers never get
    to influence the directory (section 6.6, "クライアントから保存パスは受け取らない").
    """
    if not file_name or "/" in file_name or "\\" in file_name or "\x00" in file_name:
        raise InvalidPathError(log_detail="inbox file name must be a single component")
    if file_name.startswith("."):
        raise InvalidPathError(log_detail="hidden inbox file name")
    if len(file_name) > MAX_COMPONENT_LENGTH:
        raise InvalidPathError(log_detail="inbox file name exceeds maximum length")
    if not file_name.endswith(MARKDOWN_SUFFIX):
        raise InvalidFileTypeError(log_detail="inbox file name must end in .md")

    candidate = inbox_root / file_name
    if candidate.is_symlink():
        raise InvalidPathError(log_detail="inbox target is a symlink")
    if not candidate.resolve().parent.is_relative_to(inbox_root):
        raise PathOutsideVaultError(log_detail="inbox target escapes the inbox root")

    return candidate


def iter_vault_notes(read_root: Path) -> Iterator[VaultNote]:
    """Walk every readable Markdown note in the vault.

    Applies the same exclusions as the read path: hidden directories (which
    covers ``.obsidian``, ``.git`` and ``.trash``), hidden files, symlinks,
    non-regular files and anything that is not ``.md``. Results are sorted so a
    given vault always produces the same ordering.
    """
    for dirpath, dirnames, filenames in os.walk(read_root, followlinks=False):
        directory = Path(dirpath)
        dirnames[:] = sorted(
            name
            for name in dirnames
            if not name.startswith(".") and not (directory / name).is_symlink()
        )

        for name in sorted(filenames):
            if name.startswith(".") or not name.endswith(MARKDOWN_SUFFIX):
                continue
            path = directory / name
            if path.is_symlink():
                continue
            try:
                stat_result = path.stat()
            except OSError:
                continue
            if not stat.S_ISREG(stat_result.st_mode):
                continue
            yield VaultNote(
                path=path,
                relative=path.relative_to(read_root).as_posix(),
                stat_result=stat_result,
            )
