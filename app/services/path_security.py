"""Path validation — the security core of the gateway (IMPLEMENTATION_PLAN section 7).

Every filesystem access in this service goes through :func:`resolve_read_path`,
:func:`resolve_read_dir` or :func:`resolve_inbox_append_path`. Nothing else is
allowed to build a path from caller-supplied data.

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

import dataclasses
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
    """Validate a caller-supplied vault-relative directory path (the search ``folder``).

    The leading slash is checked on the string as given — stripping it first,
    as an earlier version of this function did, would silently turn an
    absolute path like ``/Knowledge`` into the accepted relative path
    ``Knowledge``. Only a trailing slash (a normal way to write a directory
    path) is normalised away.
    """
    stripped = raw.strip()
    if stripped.startswith("/"):
        raise InvalidPathError(log_detail="absolute path")
    cleaned = stripped.rstrip("/")
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


def resolve_read_dir(raw: str | None, read_root: Path) -> ResolvedNote:
    """Turn a caller-supplied folder path into a directory inside ``read_root``.

    Mirrors :func:`resolve_read_path`'s steps but without the ``.md`` suffix
    requirement, and accepts ``None``/blank for the vault root itself.
    """
    if raw is None or not raw.strip():
        return ResolvedNote(path=read_root, relative="")

    relative = normalise_relative_dir(raw)
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

    if not stat.S_ISDIR(resolved.stat().st_mode):
        raise InvalidFileTypeError(log_detail="path does not name a directory")

    return ResolvedNote(path=resolved, relative=relative.as_posix())


class VaultEntry(NamedTuple):
    name: str
    relative: str
    is_dir: bool
    stat_result: os.stat_result | None


def iter_directory(directory: Path, read_root: Path) -> Iterator[VaultEntry]:
    """List the direct, non-hidden, non-symlink children of ``directory``.

    One level only — unlike :func:`iter_vault_notes`, this does not recurse.
    Non-Markdown files are excluded entirely; sub-folders are listed even when
    they contain no notes. Per-entry ``OSError`` (e.g. a race with a delete on
    the host) is skipped rather than raised, matching :func:`iter_vault_notes`.
    """
    try:
        with os.scandir(directory) as entries:
            names = sorted(entry.name for entry in entries)
    except OSError:
        return

    for name in names:
        if name.startswith("."):
            continue
        path = directory / name
        try:
            if path.is_symlink():
                continue
            # path is confirmed not a symlink above, so a plain is_dir() (no
            # follow_symlinks kwarg — that requires Python 3.13+, and this
            # project targets 3.12 too) cannot be fooled by one here.
            is_dir = path.is_dir()
            if not is_dir and not name.endswith(MARKDOWN_SUFFIX):
                continue
            stat_result = None if is_dir else path.stat()
        except OSError:
            continue
        if stat_result is not None and not stat.S_ISREG(stat_result.st_mode):
            continue
        yield VaultEntry(
            name=name,
            relative=path.relative_to(read_root).as_posix(),
            is_dir=is_dir,
            stat_result=stat_result,
        )


def resolve_inbox_append_path(
    raw: str, *, inbox_root: Path, inbox_relative_path: str
) -> ResolvedNote:
    """Turn a caller-supplied path into an *existing* note directly inside
    the inbox, or raise (PHASE2_PLAN section 6).

    The caller passes a full vault-relative path (e.g.
    ``00_Inbox/ChatGPT/Example.md``), matching how they addressed the note
    with ``read_note``/``search_notes`` — but the writable mount is
    ``inbox_root``, a different bind mount than the read-only vault root, so
    this both verifies the ``inbox_relative_path`` prefix and re-roots onto
    ``inbox_root`` for the actual filesystem access. Rejects any path with a
    subdirectory under the inbox — appends only ever touch a file directly
    inside it — and, unlike :func:`resolve_read_path`, requires the target to
    already exist rather than creating it.
    """
    relative = normalise_relative_path(raw)
    prefix = PurePosixPath(inbox_relative_path).parts
    parts = relative.parts

    if parts[: len(prefix)] != prefix:
        raise PathOutsideVaultError(log_detail="append target is outside the inbox")
    if len(parts) != len(prefix) + 1:
        raise InvalidPathError(log_detail="append target must be directly inside the inbox")

    candidate = inbox_root / parts[-1]
    if candidate.is_symlink():
        raise InvalidPathError(log_detail="append target is a symlink")

    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError as exc:
        raise NoteNotFoundError() from exc
    except OSError as exc:  # ELOOP, ENAMETOOLONG, ENOTDIR, ...
        raise InvalidPathError(log_detail=f"unresolvable path: {type(exc).__name__}") from exc

    if not resolved.is_relative_to(inbox_root):
        raise PathOutsideVaultError(log_detail="append target escapes the inbox root")
    if not stat.S_ISREG(resolved.stat().st_mode):
        raise InvalidFileTypeError(log_detail="append target is not a regular file")

    return ResolvedNote(path=resolved, relative=relative.as_posix())


@dataclasses.dataclass
class WalkStats:
    """Counts entries :func:`iter_vault_notes` skipped rather than yielded.

    Optional and additive: existing callers that omit ``stats`` see no change
    in behaviour, only this counter is new.
    """

    skipped: int = 0


def iter_vault_notes(read_root: Path, *, stats: WalkStats | None = None) -> Iterator[VaultNote]:
    """Walk every readable Markdown note in the vault.

    Applies the same exclusions as the read path: hidden directories (which
    covers ``.obsidian``, ``.git`` and ``.trash``), hidden files, symlinks,
    non-regular files and anything that is not ``.md``. Results are sorted so a
    given vault always produces the same ordering. When ``stats`` is given, a
    file skipped because it could not be ``stat()``-ed or was not a regular
    file increments ``stats.skipped``.
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
                if stats is not None:
                    stats.skipped += 1
                continue
            if not stat.S_ISREG(stat_result.st_mode):
                if stats is not None:
                    stats.skipped += 1
                continue
            yield VaultNote(
                path=path,
                relative=path.relative_to(read_root).as_posix(),
                stat_result=stat_result,
            )
