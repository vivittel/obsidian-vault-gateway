"""Title → file name sanitisation (IMPLEMENTATION_PLAN section 8).

The caller supplies a human-readable title, never a path. This module is the only
place that turns one into a file name.
"""

from __future__ import annotations

import re
import unicodedata

from app.exceptions import InvalidTitleError

MARKDOWN_SUFFIX = ".md"
MAX_STEM_LENGTH = 100

_FORBIDDEN_RE = re.compile(r'[/\\:*?"<>|]')
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_WHITESPACE_RE = re.compile(r"\s+")

# Reserved on Windows with or without an extension. Rejected rather than mangled
# so a vault synced to a Windows machine never grows an unopenable file.
_WINDOWS_RESERVED = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{n}" for n in range(1, 10)}
    | {f"LPT{n}" for n in range(1, 10)}
)


def sanitise_title(title: str) -> str:
    """Return a safe file stem (no extension) for ``title``.

    Raises :class:`~app.exceptions.InvalidTitleError` when nothing usable remains.
    """
    stem = unicodedata.normalize("NFC", title)
    stem = _CONTROL_RE.sub("", stem)
    stem = _FORBIDDEN_RE.sub("-", stem)
    stem = _WHITESPACE_RE.sub(" ", stem).strip()
    # Leading dots would make a hidden file; trailing dots and spaces are invalid
    # on Windows and get silently dropped by some sync clients.
    stem = stem.strip(". ")
    stem = stem[:MAX_STEM_LENGTH].strip(". ")

    if not stem:
        raise InvalidTitleError(log_detail="title is empty after sanitising")
    if stem.split(".", 1)[0].upper() in _WINDOWS_RESERVED:
        raise InvalidTitleError(log_detail="title is a reserved device name")

    return stem


def note_file_name(stem: str, sequence: int = 1) -> str:
    """``foo`` → ``foo.md`` for sequence 1, ``foo-2.md``, ``foo-3.md``, ...

    Section 6.6 requires de-duplication by suffix rather than overwriting.
    """
    suffix = "" if sequence <= 1 else f"-{sequence}"
    return f"{stem}{suffix}{MARKDOWN_SUFFIX}"
