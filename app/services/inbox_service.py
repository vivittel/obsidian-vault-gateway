"""Inbox note creation and append (IMPLEMENTATION_PLAN sections 6.6 and 17;
append is PHASE2_PLAN section 6, and docs/adr/0003-*.md records why
``os.replace()`` is permitted here but not for creation).

Creation's core operation is "create this file without ever overwriting an
existing one, and without leaving a half-written file behind if something
goes wrong". ``os.replace()`` cannot do that — it silently overwrites
whatever is already at the destination, which is exactly what section 6.6
forbids for a *new* note. Instead:

1. Write the full content to a hidden temp file inside the inbox directory
   (same filesystem, required for step 2) and fsync it.
2. Try ``os.link()` from the temp file to each candidate name in turn
   (``title.md``, ``title-2.md``, ...). ``os.link`` is atomic and fails with
   ``FileExistsError`` if the target exists — it can never clobber a note that
   is already there.
3. Unlink the temp file and fsync the directory so the create is durable.

The temp file is always cleaned up, including when linking never finds a free
name or the write itself fails.

Append updates a note that is already known to exist, so the "never
overwrite" concern above does not apply to it — what append must instead
prevent is a lost update if something else (this process concurrently, or a
host-side tool such as LiveSync) touches the same file mid-operation. See
:func:`append_inbox_note`'s docstring for that algorithm.
"""

from __future__ import annotations

import errno
import fcntl
import os
import secrets
import stat
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from app.exceptions import (
    FileTooLargeError,
    InboxLockTimeoutError,
    InternalError,
    InvalidFileTypeError,
    InvalidPathError,
    NoteAlreadyExistsError,
    NoteModifiedError,
    ValidationError,
)
from app.models import FrontmatterValue
from app.services.filenames import note_file_name, sanitise_title
from app.services.path_security import resolve_inbox_append_path

MAX_SEQUENCE_ATTEMPTS = 100

_LOCK_FILE_NAME = ".append.lock"
_READ_CHUNK_SIZE = 1024 * 1024

# How long append() waits for the inbox-wide lock before giving up. A plain
# LOCK_EX blocks forever — fine when every holder releases promptly, but a
# stuck request (or, before REST handlers ran in a worker thread, one simply
# doing its own slow disk I/O) would otherwise hang every later append with
# no client-visible error. Module constants rather than a Settings field:
# nothing here needs tuning per deployment, only per test (tests monkeypatch
# these directly).
_LOCK_TIMEOUT_SECONDS = 5.0
_LOCK_POLL_INTERVAL_SECONDS = 0.05

# EAGAIN and EWOULDBLOCK are the same value on Linux; POSIX also permits
# flock() to signal contention with EACCES, which BlockingIOError alone does
# not catch (BlockingIOError maps only EAGAIN/EWOULDBLOCK/EALREADY/
# EINPROGRESS — verified against the installed CPython's OSError subclass
# table), so contention is matched on errno directly instead.
_LOCK_RETRY_ERRNOS = frozenset({errno.EAGAIN, errno.EWOULDBLOCK, errno.EACCES})


@dataclass(frozen=True)
class CreatedNote:
    relative: str
    title: str
    modified_at: datetime


@dataclass(frozen=True)
class AppendedNote:
    relative: str
    modified_at: datetime
    appended_bytes: int


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


def _acquire_exclusive_lock(fd: int) -> None:
    """Block on an exclusive flock, but never past ``_LOCK_TIMEOUT_SECONDS``.

    The deadline is set before the first attempt, not after the first
    failure, so the actual wait never exceeds the configured timeout by more
    than one poll interval. Raises :class:`InboxLockTimeoutError` — with no
    path, fd, or inode in its message (AGENTS.md: never expose absolute host
    paths in anything client-visible) — rather than blocking forever.
    """
    deadline = time.monotonic() + _LOCK_TIMEOUT_SECONDS
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except OSError as exc:
            if exc.errno not in _LOCK_RETRY_ERRNOS:
                raise
            if time.monotonic() >= deadline:
                raise InboxLockTimeoutError(
                    log_detail="timed out waiting for the inbox append lock"
                ) from exc
            time.sleep(_LOCK_POLL_INTERVAL_SECONDS)


def _open_append_lock(inbox_root: Path) -> int:
    """Open (or create) the inbox-wide append lock and hold an exclusive flock.

    A single lock file for the whole inbox, not one per target note: locking
    on the target's own inode would stop working the moment ``os.replace()``
    swaps that inode out from under the lock, and per-note lock files would
    otherwise accumulate inside the vault. The lock only serialises append
    requests reaching *this* process — it says nothing about a host-side tool
    such as LiveSync writing the same file concurrently; that is caught by
    the identity re-check below instead.

    The lock file's fixed name makes it a target a host-side process could
    replace with a symlink, so it gets exactly the same treatment as any
    other caller-adjacent path here: opened with ``O_NOFOLLOW`` and confirmed
    to be a regular file before use.
    """
    lock_path = inbox_root / _LOCK_FILE_NAME
    try:
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    except OSError as exc:  # ELOOP (a symlink at that name) among others
        raise InvalidPathError(log_detail=f"cannot open append lock: {type(exc).__name__}") from exc

    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise InvalidFileTypeError(log_detail="append lock is not a regular file")
        _acquire_exclusive_lock(fd)
    except BaseException:
        os.close(fd)
        raise
    return fd


def _append_bytes(content: str, *, existing: bytes, crlf: bool) -> bytes:
    """Render ``content`` for appending, matching ``existing``'s line ending.

    Unlike a new note (:func:`_render_note`, always LF), an appended note
    must preserve whatever line ending the file already has (section 17:
    "改行コードは既存ノートを尊重する"). A separating newline is inserted
    only when ``existing`` is non-empty and does not already end in one.
    """
    body = content.replace("\r\n", "\n").replace("\r", "\n")
    if not body.endswith("\n"):
        body += "\n"
    terminator = b"\r\n" if crlf else b"\n"
    if crlf:
        body = body.replace("\n", "\r\n")
    appended = body.encode("utf-8")

    if existing and not existing.endswith(terminator):
        appended = terminator + appended
    return appended


def _write_temp_bytes(inbox_root: Path, data: bytes, *, mode: int) -> Path:
    temp_path = inbox_root / f".tmp-{secrets.token_hex(8)}"
    fd = os.open(temp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError:
        temp_path.unlink(missing_ok=True)
        raise
    return temp_path


def append_inbox_note(
    raw_path: str,
    *,
    inbox_root: Path,
    inbox_relative_path: str,
    content: str,
    max_note_bytes: int,
    timezone: ZoneInfo,
) -> AppendedNote:
    """Append ``content`` to an existing ``.md`` note directly inside the inbox.

    1. Resolve and validate the target (:func:`resolve_inbox_append_path`).
    2. Take the inbox-wide append lock (serialises this process's own
       concurrent requests; see :func:`_open_append_lock`).
    3. Open the target with ``O_NOFOLLOW`` and read its full content from
       that one file descriptor — resolving a path and later reopening it by
       name would let a host-side symlink swap go unnoticed in between.
    4. Reject outright if the note is already over ``max_note_bytes``, or if
       appending would push it over — before anything is written.
    5. Render the append, matching the existing line ending.
    6. Write ``existing + appended`` to a hidden temp file in the inbox and
       fsync it, preserving the target's file mode.
    7. Re-check the target's identity (device, inode, mtime, size) against
       what was read in step 3, and that it has not become a symlink. A
       mismatch means something else modified the file since it was read —
       raise rather than silently discard that change.
    8. ``os.replace()`` the temp file onto the target (atomic; permitted here
       specifically because the target already exists — see docs/adr/0003-*)
       and fsync the directory.

    The temp file and the lock are always released, on every exit path.
    """
    if not content or not content.strip():
        raise ValidationError(log_detail="empty append content")

    resolved = resolve_inbox_append_path(
        raw_path, inbox_root=inbox_root, inbox_relative_path=inbox_relative_path
    )
    target = resolved.path

    lock_fd = _open_append_lock(inbox_root)
    try:
        fd = os.open(target, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode):
                raise InvalidFileTypeError(log_detail="append target is not a regular file")
            if before.st_size > max_note_bytes:
                raise FileTooLargeError(log_detail="existing note already exceeds the size limit")

            chunks = []
            while True:
                chunk = os.read(fd, _READ_CHUNK_SIZE)
                if not chunk:
                    break
                chunks.append(chunk)
            existing = b"".join(chunks)
        finally:
            os.close(fd)

        crlf = b"\r\n" in existing
        appended = _append_bytes(content, existing=existing, crlf=crlf)
        if before.st_size + len(appended) > max_note_bytes:
            raise FileTooLargeError(log_detail="append would exceed the note size limit")

        temp_path = _write_temp_bytes(
            inbox_root, existing + appended, mode=stat.S_IMODE(before.st_mode)
        )
        try:
            after = os.lstat(target)
            if stat.S_ISLNK(after.st_mode):
                raise InvalidPathError(log_detail="append target became a symlink")
            before_identity = (before.st_dev, before.st_ino, before.st_mtime_ns, before.st_size)
            after_identity = (after.st_dev, after.st_ino, after.st_mtime_ns, after.st_size)
            if before_identity != after_identity:
                raise NoteModifiedError()

            os.replace(temp_path, target)
            _fsync_directory(inbox_root)
        finally:
            temp_path.unlink(missing_ok=True)
    finally:
        os.close(lock_fd)

    modified_at = datetime.fromtimestamp(target.stat().st_mtime, tz=timezone)
    return AppendedNote(
        relative=resolved.relative, modified_at=modified_at, appended_bytes=len(appended)
    )
