"""Opaque, tamper-evident pagination cursors (PHASE2_PLAN section 5).

A cursor encodes an ``operation`` discriminator, an integer ``offset``, and a
keyed ``fingerprint`` of the request's other conditions (query, folder, tags,
...). It never stores the conditions themselves — a keyed HMAC means the
fingerprint cannot be reversed or forged without the signing key, so search
terms never leave the server even indirectly (section 5, "カーソル内に検索語
そのものを保存しない").

The signing key is derived from ``Settings.api_token`` rather than a
dedicated secret, so no new environment variable is required. Two
consequences follow, both deliberate:

* Rotating ``API_TOKEN`` invalidates every outstanding cursor. Clients see
  ``INVALID_CURSOR`` and restart pagination from the first page — an
  acceptable cost for not having to provision and rotate a second secret.
* The fingerprint key and the signature key are independent subkeys derived
  from a common root key with distinct purpose labels, so a value produced
  for one purpose is useless as a forgery for the other.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Final

from app.exceptions import InvalidCursorError

_ROOT_LABEL: Final = b"obsidian-vault-gateway/cursor/v1"
_FINGERPRINT_PURPOSE: Final = b"fingerprint"
_SIGNATURE_PURPOSE: Final = b"signature"
_PAYLOAD_KEYS: Final = frozenset({"o", "n", "f"})

MAX_CURSOR_LENGTH: Final = 512
MAX_CURSOR_OFFSET: Final = 100_000
_MAC_BYTES: Final = 16


def _root_key(api_token: str) -> bytes:
    return hmac.new(api_token.encode("utf-8"), _ROOT_LABEL, hashlib.sha256).digest()


def _subkey(api_token: str, purpose: bytes) -> bytes:
    return hmac.new(_root_key(api_token), purpose, hashlib.sha256).digest()


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def fingerprint(*, operation: str, conditions: dict[str, object], api_token: str) -> str:
    """A keyed digest of the conditions a cursor must match to be reused.

    Two calls with the same ``operation`` and ``conditions`` always produce the
    same fingerprint; a different ``api_token`` or different ``conditions``
    never does. The digest is truncated to 32 hex characters (128 bits) —
    plenty to prevent cursor reuse across different requests without bloating
    the cursor.
    """
    key = _subkey(api_token, _FINGERPRINT_PURPOSE)
    message = _canonical_json({"operation": operation, "conditions": conditions})
    return hmac.new(key, message, hashlib.sha256).hexdigest()[:32]


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64decode_strict(value: str) -> bytes:
    """Decode a stripped-padding urlsafe base64 string, rejecting anything non-canonical."""
    if not value or not value.isascii():
        raise InvalidCursorError(log_detail="cursor segment is not ascii")

    padding_needed = (-len(value)) % 4
    if padding_needed == 3:
        raise InvalidCursorError(log_detail="cursor segment has invalid length")

    padded = value.encode("ascii") + b"=" * padding_needed
    try:
        raw = base64.b64decode(padded, altchars=b"-_", validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise InvalidCursorError(log_detail="cursor segment is not valid base64") from exc

    if _b64encode(raw) != value:
        raise InvalidCursorError(log_detail="cursor segment is not canonical base64")

    return raw


def encode_cursor(*, operation: str, offset: int, fingerprint: str, api_token: str) -> str:
    """Build an opaque cursor for the next page of ``operation``."""
    if type(offset) is not int or not 0 <= offset <= MAX_CURSOR_OFFSET:
        raise InvalidCursorError(log_detail=f"offset out of range [0, {MAX_CURSOR_OFFSET}]")

    payload = {"o": operation, "n": offset, "f": fingerprint}
    body = _b64encode(_canonical_json(payload))

    key = _subkey(api_token, _SIGNATURE_PURPOSE)
    mac = hmac.new(key, body.encode("ascii"), hashlib.sha256).digest()[:_MAC_BYTES]
    signature = _b64encode(mac)

    cursor = f"{body}.{signature}"
    if len(cursor) > MAX_CURSOR_LENGTH:
        raise InvalidCursorError(log_detail="encoded cursor exceeds maximum length")
    return cursor


def decode_cursor(cursor: str, *, operation: str, fingerprint: str, api_token: str) -> int:
    """Validate ``cursor`` and return the offset it encodes, or raise ``InvalidCursorError``."""
    if not cursor or len(cursor) > MAX_CURSOR_LENGTH:
        raise InvalidCursorError(log_detail="cursor exceeds maximum length")

    parts = cursor.split(".")
    if len(parts) != 2:
        raise InvalidCursorError(log_detail="cursor is not in the expected two-part form")
    body, signature = parts

    raw_mac = _b64decode_strict(signature)
    key = _subkey(api_token, _SIGNATURE_PURPOSE)
    expected_mac = hmac.new(key, body.encode("ascii"), hashlib.sha256).digest()[:_MAC_BYTES]
    if not hmac.compare_digest(raw_mac, expected_mac):
        raise InvalidCursorError(log_detail="cursor signature mismatch")

    raw_body = _b64decode_strict(body)
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InvalidCursorError(log_detail="cursor payload is not valid JSON") from exc

    if not isinstance(payload, dict) or payload.keys() != _PAYLOAD_KEYS:
        raise InvalidCursorError(log_detail="cursor payload has unexpected shape")

    offset = payload["n"]
    # bool is an int subclass; isinstance would silently accept True/False as 0/1.
    if type(offset) is not int or not 0 <= offset <= MAX_CURSOR_OFFSET:
        raise InvalidCursorError(log_detail="cursor offset is invalid")

    if not (
        isinstance(payload["o"], str)
        and isinstance(payload["f"], str)
        and hmac.compare_digest(payload["o"], operation)
        and hmac.compare_digest(payload["f"], fingerprint)
    ):
        raise InvalidCursorError(log_detail="cursor does not match this request")

    return offset
