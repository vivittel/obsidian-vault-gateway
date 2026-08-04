"""Reading and parsing note source.

Deliberately no Markdown parser dependency: Phase 1 needs heading text for search
and the raw body for the read endpoint, both of which a regex covers. Adding a
full parser would ship a dependency nothing consumes.

Frontmatter splitting is implemented directly against PyYAML rather than the
python-frontmatter library that IMPLEMENTATION_PLAN.md section 4 lists: that
library's ``loads()`` unconditionally replaces every ``"\\r\\n"`` with ``"\\n"``
in the *whole* input, before it even looks for delimiters (``frontmatter.util.u``).
That would silently rewrite every CRLF note on a plain read, which is the exact
opposite of section 17's "改行コードは既存ノートを尊重する". Slicing the
original text ourselves keeps whatever line endings the note already has; see
docs/PHASE1_PLAN.md's deviations list.

Every function here has to survive a real vault, which contains notes with broken
YAML, mixed line endings and non-UTF-8 bytes. Parsing never raises on bad input —
it degrades to "no frontmatter".
"""

from __future__ import annotations

import datetime as dt
import math
import re
from pathlib import Path
from typing import Any, NamedTuple

import yaml

# A frontmatter block must open on the note's very first line...
_OPEN_DELIMITER_RE = re.compile(r"\A---[ \t]*\r?\n")
# ...and close on a line of its own, found anywhere after that.
_CLOSE_DELIMITER_RE = re.compile(r"^---[ \t]*\r?\n", re.MULTILINE)

# ATX headings, tolerating up to three leading spaces and optional closing hashes.
_HEADING_RE = re.compile(r"^[ \t]{0,3}(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$", re.MULTILINE)
_TAG_SPLIT_RE = re.compile(r"[,\s]+")

# yaml.CSafeLoader (libyaml) parses roughly 7x faster than the pure-Python
# yaml.SafeLoader — worth having on the two paths that parse every note in
# the vault (search, vault/summary) — but it is a C extension with its own
# call stack, and confirmed directly (not a hypothetical): deeply nested
# YAML that raises a clean, catchable RecursionError under SafeLoader
# instead SEGFAULTS THE WHOLE PROCESS under CSafeLoader, in both flow style
# (`[[[...]]]`) and block style (nested mappings), somewhere between depth
# 20,000 (confirmed safe) and 50,000 (confirmed crash) for either form.
# Below this character cap, that depth is unreachable even in the most
# compact encoding (flow style costs a minimum of 2 characters per nesting
# level, so 8192 characters bounds depth to at most 4096 — better than 4x
# below the confirmed-safe ceiling, let alone the crash zone; block style's
# cumulative indentation makes it quadratically more expensive per level,
# so it is bounded far more tightly still). Legitimate frontmatter — a
# handful of scalar fields — sits far under this cap, so the fast loader is
# what every real note gets; only a document already shaped like an attack
# pays for the pure-Python fallback.
_CSAFE_MAX_YAML_CHARS = 8192

_FAST_YAML_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)

# to_json_safe()'s conversion budget. YAML aliases let a handful of bytes
# describe a structure that shares the same sub-object many times over
# (`a: &a [1,2]` / `b: *a`); to_json_safe rebuilds it without that sharing, so
# an aliased frontmatter block that is fine for yaml.safe_load can still blow
# up here. A 402-byte note with eight-way aliases nested nine deep exhausted a
# 2 GiB budget in 11 seconds before these limits existed. Total characters (not
# a per-string cap) is what closes the "many medium strings" gap a per-node or
# per-string limit alone would leave: the three limits are independent, so a
# per-string cap would still let node-count × string-length multiply past any
# sane bound.
MAX_JSON_SAFE_DEPTH = 64
MAX_JSON_SAFE_NODES = 10_000
MAX_JSON_SAFE_TOTAL_CHARS = 1_000_000


class FrontmatterBudgetExceededError(ValueError):
    """to_json_safe's output would exceed the conversion budget."""


class FrontmatterCycleError(ValueError):
    """to_json_safe found a YAML alias that refers back to its own ancestor.

    ``a: &a [*a]`` is valid YAML that PyYAML happily parses into a list that
    contains itself — not just repeated sharing, an actual cycle. Rebuilding
    that without cycle detection overflows Python's call stack.
    """


class ParsedNote(NamedTuple):
    metadata: dict[str, Any]
    body: str
    title: str
    tags: list[str]
    headings: list[str]


def read_note_text(path: Path, *, size_bytes: int, max_bytes: int) -> tuple[str, bool]:
    """Read a note as text, capping it at ``max_bytes`` of *file* bytes.

    Returns ``(text, truncated)``. Reading bytes and decoding (rather than opening
    in text mode) does two things: it makes the cap a real byte budget for
    multi-byte scripts, and it preserves CRLF exactly, which section 17 requires
    ("改行コードは既存ノートを尊重する").
    """
    truncated = size_bytes > max_bytes
    with path.open("rb") as handle:
        raw = handle.read(max_bytes) if truncated else handle.read()

    text = raw.decode("utf-8", errors="replace")
    if truncated:
        # A byte-boundary cut leaves replacement characters at the tail.
        text = text.rstrip("�")
    return text, truncated


# read_frontmatter_text()'s bounds. Real frontmatter is a handful of scalar
# fields — comfortably under either limit — so this only ever engages for a
# note already unlike anything a real vault produces, which is then treated
# the same as having no frontmatter at all (see the function's docstring).
MAX_FRONTMATTER_READ_CHARS = 64 * 1024
MAX_FRONTMATTER_READ_LINES = 1000


def read_frontmatter_text(path: Path) -> str | None:
    """Read only a note's leading frontmatter block — the body is never read.

    Returns the block verbatim (including both ``---`` delimiter lines), or
    ``None`` if the note has none, or has one too large to be real
    frontmatter (see the module-level bounds above). Raises ``OSError``
    exactly as :func:`read_note_text` does, rather than catching it, so a
    caller that already distinguishes "note unreadable" from "note has no
    frontmatter" (:func:`~app.services.vault_service.summarise_vault`) can
    keep doing so.

    A bounded, line-by-line streaming read: it stops at the first line that
    matches the closing delimiter, so a note's body — everything after that
    line — is never touched. Written for :func:`~app.services.vault_service.
    summarise_vault`, which used to pay for a full :func:`read_note_text` +
    :func:`parse_note` of every note in the vault only to keep the tags and
    discard the rest.

    Shares :data:`_OPEN_DELIMITER_RE`/:data:`_CLOSE_DELIMITER_RE` with
    :func:`_split_frontmatter`, so both agree on exactly where a block
    starts and ends for the same note — see
    ``test_summary_and_full_read_agree_on_frontmatter_boundaries``.
    """
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        first_line = handle.readline(MAX_FRONTMATTER_READ_CHARS)
        if not _OPEN_DELIMITER_RE.match(first_line):
            return None

        lines = [first_line]
        total_chars = len(first_line)
        for _ in range(MAX_FRONTMATTER_READ_LINES):
            line = handle.readline(MAX_FRONTMATTER_READ_CHARS)
            if not line:
                return None  # EOF before any closing delimiter was found.
            total_chars += len(line)
            if total_chars > MAX_FRONTMATTER_READ_CHARS:
                return None
            lines.append(line)
            if _CLOSE_DELIMITER_RE.match(line):
                return "".join(lines)
        return None  # No closing delimiter within MAX_FRONTMATTER_READ_LINES.


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Split a leading YAML frontmatter block from the body.

    Returns ``({}, text)`` unchanged whenever a block cannot be found or fails
    to parse as a YAML mapping — a real vault contains notes with no
    frontmatter and notes with malformed frontmatter, and neither should fail
    the request.
    """
    open_match = _OPEN_DELIMITER_RE.match(text)
    if not open_match:
        return {}, text

    close_match = _CLOSE_DELIMITER_RE.search(text, open_match.end())
    if not close_match:
        return {}, text

    yaml_text = text[open_match.end() : close_match.start()]
    # See _CSAFE_MAX_YAML_CHARS's comment: the fast C loader is only safe
    # below this length, so anything larger — already shaped unlike any real
    # frontmatter block — takes the slower but crash-proof pure-Python path.
    loader = yaml.SafeLoader if len(yaml_text) > _CSAFE_MAX_YAML_CHARS else _FAST_YAML_LOADER
    try:
        # S506 (bandit's "unsafe Loader") is a false positive here: `loader`
        # is always yaml.SafeLoader or yaml.CSafeLoader — its safe,
        # C-accelerated equivalent — never yaml.Loader/FullLoader/
        # UnsafeLoader. The linter can't see that from a variable.
        data = yaml.load(yaml_text, Loader=loader)  # noqa: S506
    except (yaml.YAMLError, RecursionError):
        # RecursionError alongside YAMLError: deeply nested (but alias-free,
        # so to_json_safe's own guards never see it) YAML can exhaust
        # PyYAML's own composer stack before it ever returns a value. Only
        # yaml.SafeLoader (pure Python) can raise this cleanly — see above.
        return {}, text

    if not isinstance(data, dict):
        return {}, text

    return data, text[close_match.end() :]


def parse_note(text: str, *, fallback_title: str) -> ParsedNote:
    """Split frontmatter from body and pull out title, tags and headings."""
    metadata, body = _split_frontmatter(text)

    raw_title = metadata.get("title")
    title = (
        raw_title.strip()
        if isinstance(raw_title, str) and raw_title.strip()
        else fallback_title
    )

    return ParsedNote(
        metadata=metadata,
        body=body,
        title=title,
        tags=normalise_tags(metadata.get("tags")),
        headings=[match.group(2).strip() for match in _HEADING_RE.finditer(body)],
    )


def normalise_tags(value: Any) -> list[str]:
    """Coerce a frontmatter ``tags`` value into an ordered, de-duplicated list.

    Obsidian vaults hold tags as a YAML list, as a whitespace- or comma-separated
    string, and occasionally with a leading ``#``. All three are accepted.
    """
    candidates: list[str] = []

    if isinstance(value, str):
        candidates = _TAG_SPLIT_RE.split(value)
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            if isinstance(item, str):
                candidates.append(item)
            elif isinstance(item, (int, float)) and not isinstance(item, bool):
                candidates.append(str(item))

    tags: list[str] = []
    for candidate in candidates:
        tag = candidate.strip().lstrip("#").strip()
        if tag and tag not in tags:
            tags.append(tag)
    return tags


def parse_frontmatter_tags(frontmatter_block: str) -> list[str]:
    """Parse a frontmatter block (as returned by :func:`read_frontmatter_text`)
    and return its normalised tags, or ``[]`` if it has none.

    A narrow pairing with :func:`read_frontmatter_text` for
    :func:`~app.services.vault_service.summarise_vault`, which wants
    exactly this — not the title, body, or headings :func:`parse_note`
    also computes. Reuses :func:`_split_frontmatter` rather than
    re-implementing delimiter/YAML handling, so both this and the full
    parse path degrade identically on malformed YAML.
    """
    metadata, _ = _split_frontmatter(frontmatter_block)
    return normalise_tags(metadata.get("tags"))


class _ConversionBudget:
    """Running totals for one :func:`to_json_safe` call, shared across recursion."""

    __slots__ = ("nodes", "chars")

    def __init__(self) -> None:
        self.nodes = 0
        self.chars = 0

    def charge_node(self) -> None:
        self.nodes += 1
        if self.nodes > MAX_JSON_SAFE_NODES:
            raise FrontmatterBudgetExceededError(f"more than {MAX_JSON_SAFE_NODES} output nodes")

    def charge_chars(self, count: int) -> None:
        self.chars += count
        if self.chars > MAX_JSON_SAFE_TOTAL_CHARS:
            raise FrontmatterBudgetExceededError(
                f"more than {MAX_JSON_SAFE_TOTAL_CHARS} output characters"
            )


def to_json_safe(value: Any) -> Any:
    """Convert parsed YAML into something JSON can represent.

    YAML gives back ``date``/``datetime`` objects and can produce non-finite
    floats; both would otherwise break serialisation or emit invalid JSON.

    Bounded by :data:`MAX_JSON_SAFE_NODES`/:data:`MAX_JSON_SAFE_TOTAL_CHARS`
    (raises :class:`FrontmatterBudgetExceededError`) and by cycle detection (raises
    :class:`FrontmatterCycleError`) — see this module's docstring above those
    constants. Callers that want the read path's usual "malformed frontmatter
    degrades to an empty dict" behaviour must catch both themselves; this
    function only ever returns a fully-converted value or raises.
    """
    return _to_json_safe(value, budget=_ConversionBudget(), depth=0, active=set())


def _to_json_safe(value: Any, *, budget: _ConversionBudget, depth: int, active: set[int]) -> Any:
    budget.charge_node()
    if depth > MAX_JSON_SAFE_DEPTH:
        raise FrontmatterBudgetExceededError(f"more than {MAX_JSON_SAFE_DEPTH} levels deep")

    if value is None or isinstance(value, (str, bool, int)):
        if isinstance(value, str):
            budget.charge_chars(len(value))
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        text = value.isoformat()
        budget.charge_chars(len(text))
        return text

    if isinstance(value, (dict, list, tuple, set)):
        # Containers only: interning and the small-int cache make id() on a
        # str/int meaningless for cycle detection, and YAML aliases can only
        # ever make a container refer back to itself in the first place.
        object_id = id(value)
        if object_id in active:
            raise FrontmatterCycleError("frontmatter contains a cyclic YAML alias")
        active.add(object_id)
        try:
            if isinstance(value, dict):
                result: dict[str, Any] = {}
                for key, item in value.items():
                    budget.charge_node()
                    key_text = str(key)
                    budget.charge_chars(len(key_text))
                    result[key_text] = _to_json_safe(
                        item, budget=budget, depth=depth + 1, active=active
                    )
                return result
            return [
                _to_json_safe(item, budget=budget, depth=depth + 1, active=active)
                for item in value
            ]
        finally:
            # A shared (non-cyclic) alias may legitimately reappear in a
            # later sibling branch — only remove it once this branch is
            # done, so re-use elsewhere in the tree is not misread as a cycle.
            active.discard(object_id)

    text = str(value)
    budget.charge_chars(len(text))
    return text
