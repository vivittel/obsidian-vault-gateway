"""Cursor codec: round trip, tamper detection, canonical encoding (PHASE2_PLAN section 5)."""

from __future__ import annotations

import base64
import hashlib
import hmac

import pytest

from app.exceptions import InvalidCursorError
from app.services import cursor_service

API_TOKEN = "test-token-0123456789abcdef"
OTHER_TOKEN = "other-token-0123456789abcdef"
OPERATION = "search"


def _fp(api_token: str = API_TOKEN, **conditions: object) -> str:
    return cursor_service.fingerprint(
        operation=OPERATION, conditions=conditions, api_token=api_token
    )


def _encode(offset: int, fp: str, *, api_token: str = API_TOKEN) -> str:
    return cursor_service.encode_cursor(
        operation=OPERATION, offset=offset, fingerprint=fp, api_token=api_token
    )


def _decode(cursor: str, fp: str, *, operation: str = OPERATION, api_token: str = API_TOKEN) -> int:
    return cursor_service.decode_cursor(
        cursor, operation=operation, fingerprint=fp, api_token=api_token
    )


def _sign(body: str, key: bytes) -> str:
    mac = hmac.new(key, body.encode("ascii"), hashlib.sha256).digest()[: cursor_service._MAC_BYTES]
    return base64.urlsafe_b64encode(mac).rstrip(b"=").decode("ascii")


def _forge(payload: dict[str, object], *, api_token: str = API_TOKEN) -> str:
    """Build a cursor from an arbitrary payload, signed with the real signature subkey."""
    body = cursor_service._b64encode(cursor_service._canonical_json(payload))
    key = cursor_service._subkey(api_token, cursor_service._SIGNATURE_PURPOSE)
    return f"{body}.{_sign(body, key)}"


def test_round_trip_recovers_the_offset() -> None:
    fp = _fp(query="rtx")
    cursor = _encode(42, fp)
    assert _decode(cursor, fp) == 42


def test_fingerprint_is_stable_for_the_same_conditions() -> None:
    assert _fp(query="rtx", folder=None) == _fp(query="rtx", folder=None)


def test_fingerprint_differs_for_different_conditions() -> None:
    assert _fp(query="rtx") != _fp(query="gpu")


def test_fingerprint_differs_for_different_api_token() -> None:
    assert _fp(query="rtx") != _fp(OTHER_TOKEN, query="rtx")


def test_tampered_body_is_rejected() -> None:
    fp = _fp(query="rtx")
    body, sig = _encode(1, fp).split(".")
    with pytest.raises(InvalidCursorError):
        _decode(f"{body}x.{sig}", fp)


def test_tampered_signature_is_rejected() -> None:
    fp = _fp(query="rtx")
    body, sig = _encode(1, fp).split(".")
    flipped = "A" if sig[-1] != "A" else "B"
    with pytest.raises(InvalidCursorError):
        _decode(f"{body}.{sig[:-1]}{flipped}", fp)


def test_truncated_cursor_is_rejected() -> None:
    fp = _fp(query="rtx")
    cursor = _encode(1, fp)
    with pytest.raises(InvalidCursorError):
        _decode(cursor[:-4], fp)


@pytest.mark.parametrize("raw", ["", "no-dot-here", "a.b.c", "."])
def test_malformed_shape_is_rejected(raw: str) -> None:
    with pytest.raises(InvalidCursorError):
        _decode(raw, _fp(query="rtx"))


def test_non_canonical_base64_with_explicit_padding_is_rejected() -> None:
    """A well-formed cursor whose body is re-padded with '=' must be rejected,
    even though base64.b64decode(..., validate=True) alone would accept it."""
    fp = _fp(query="rtx")
    body, _sig = _encode(1, fp).split(".")
    padded_body = body + "=="
    key = cursor_service._subkey(API_TOKEN, cursor_service._SIGNATURE_PURPOSE)
    forged = f"{padded_body}.{_sign(padded_body, key)}"
    with pytest.raises(InvalidCursorError):
        _decode(forged, fp)


def test_payload_missing_a_key_is_rejected() -> None:
    fp = _fp(query="rtx")
    with pytest.raises(InvalidCursorError):
        _decode(_forge({"o": OPERATION, "n": 1}), fp)


def test_payload_with_extra_key_is_rejected() -> None:
    fp = _fp(query="rtx")
    with pytest.raises(InvalidCursorError):
        _decode(_forge({"o": OPERATION, "n": 1, "f": fp, "extra": "x"}), fp)


def test_bool_offset_is_rejected_not_coerced() -> None:
    """``bool`` is an ``int`` subclass; ``isinstance`` would silently accept it."""
    fp = _fp(query="rtx")
    with pytest.raises(InvalidCursorError):
        _decode(_forge({"o": OPERATION, "n": True, "f": fp}), fp)


def test_string_offset_is_rejected() -> None:
    fp = _fp(query="rtx")
    with pytest.raises(InvalidCursorError):
        _decode(_forge({"o": OPERATION, "n": "1", "f": fp}), fp)


def test_negative_offset_is_rejected() -> None:
    fp = _fp(query="rtx")
    with pytest.raises(InvalidCursorError):
        _decode(_forge({"o": OPERATION, "n": -1, "f": fp}), fp)


def test_offset_beyond_max_is_rejected_on_decode() -> None:
    fp = _fp(query="rtx")
    huge = cursor_service.MAX_CURSOR_OFFSET + 1
    with pytest.raises(InvalidCursorError):
        _decode(_forge({"o": OPERATION, "n": huge, "f": fp}), fp)


def test_offset_beyond_max_is_rejected_on_encode() -> None:
    fp = _fp(query="rtx")
    with pytest.raises(InvalidCursorError):
        _encode(cursor_service.MAX_CURSOR_OFFSET + 1, fp)


def test_negative_offset_is_rejected_on_encode() -> None:
    with pytest.raises(InvalidCursorError):
        _encode(-1, _fp(query="rtx"))


def test_operation_mismatch_is_rejected() -> None:
    fp = _fp(query="rtx")
    cursor = _encode(1, fp)
    with pytest.raises(InvalidCursorError):
        _decode(cursor, fp, operation="tree")


def test_fingerprint_mismatch_is_rejected() -> None:
    fp = _fp(query="rtx")
    cursor = _encode(1, fp)
    with pytest.raises(InvalidCursorError):
        _decode(cursor, _fp(query="gpu"))


def test_different_api_token_cannot_decode() -> None:
    fp = _fp(query="rtx")
    cursor = _encode(1, fp)
    other_fp = _fp(OTHER_TOKEN, query="rtx")
    with pytest.raises(InvalidCursorError):
        _decode(cursor, other_fp, api_token=OTHER_TOKEN)


def test_overly_long_cursor_is_rejected() -> None:
    with pytest.raises(InvalidCursorError):
        _decode("a" * 600, _fp(query="rtx"))


def test_signature_subkey_cannot_be_forged_from_fingerprint_subkey() -> None:
    """Signing with the fingerprint subkey (instead of the signature subkey)
    must not validate — the two purposes must not share key material."""
    fp = _fp(query="rtx")
    payload = {"o": OPERATION, "n": 1, "f": fp}
    body = cursor_service._b64encode(cursor_service._canonical_json(payload))
    wrong_key = cursor_service._subkey(API_TOKEN, cursor_service._FINGERPRINT_PURPOSE)
    forged = f"{body}.{_sign(body, wrong_key)}"
    with pytest.raises(InvalidCursorError):
        _decode(forged, fp)
