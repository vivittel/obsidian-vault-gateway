"""app.auth.verify_bearer_token and Settings.auth_enabled/api_token.

REST's own bearer-token dependency (``require_token``) was removed along
with the rest of the REST surface (docs/adr/0010-*.md) — ``/mcp``'s
authentication middleware (app/mcp_auth.py) is now ``verify_bearer_token``'s
only caller, and is exercised end-to-end in tests/test_mcp_auth.py, including
the ``AUTH_ENABLED=false`` no-op case.
"""

import pytest
from pydantic import ValidationError

from app.auth import verify_bearer_token
from app.config import Settings

TEST_API_TOKEN = "test-token-0123456789abcdef"  # noqa: S105 - test fixture, not a real secret


def test_verify_bearer_token_accepts_matching_value() -> None:
    assert verify_bearer_token(provided="secret-token", expected="secret-token") is True


def test_verify_bearer_token_rejects_mismatch() -> None:
    assert verify_bearer_token(provided="wrong", expected="secret-token") is False


def test_verify_bearer_token_rejects_empty_provided() -> None:
    assert verify_bearer_token(provided="", expected="secret-token") is False


def test_verify_bearer_token_rejects_different_length() -> None:
    assert verify_bearer_token(provided="short", expected="a-much-longer-secret-token") is False


def test_verify_bearer_token_handles_non_ascii_without_raising() -> None:
    # str/str comparison via secrets.compare_digest raises TypeError for
    # non-ASCII input; encoding to bytes first (as verify_bearer_token does)
    # is what keeps this from ever surfacing as a 500 INTERNAL_ERROR.
    assert verify_bearer_token(provided="トークン", expected="secret-token") is False
    assert verify_bearer_token(provided="トークン", expected="トークン") is True


# Settings.auth_enabled: opt-in toggle, and api_token's invariants around it.


def _settings_kwargs(**overrides: object) -> dict[str, object]:
    kwargs: dict[str, object] = {
        "api_token": TEST_API_TOKEN,
        "mcp_allowed_hosts": "testserver",
    }
    kwargs.update(overrides)
    return kwargs


def test_auth_enabled_defaults_to_true() -> None:
    assert Settings(**_settings_kwargs()).auth_enabled is True


@pytest.mark.parametrize("value", ["false", "0", "no", "off"])
def test_auth_enabled_env_var_parses_falsy_values(
    value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("API_TOKEN", TEST_API_TOKEN)
    monkeypatch.setenv("MCP_ALLOWED_HOSTS", "testserver")
    monkeypatch.setenv("AUTH_ENABLED", value)
    assert Settings().auth_enabled is False


def test_auth_enabled_env_var_rejects_invalid_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_TOKEN", TEST_API_TOKEN)
    monkeypatch.setenv("MCP_ALLOWED_HOSTS", "testserver")
    monkeypatch.setenv("AUTH_ENABLED", "maybe")
    with pytest.raises(ValidationError):
        Settings()


def test_api_token_still_required_when_auth_disabled() -> None:
    # api_token also signs pagination cursors (app/services/cursor_service.py),
    # so it stays mandatory regardless of auth_enabled — disabling auth must
    # never leave every deployment with the same, empty signing key.
    with pytest.raises(ValidationError):
        Settings(**_settings_kwargs(auth_enabled=False, api_token=""))


def test_api_token_min_length_applies_after_stripping() -> None:
    # Regression: _strip_token runs mode="before" so min_length is enforced on
    # the *stripped* value — otherwise a whitespace-padded token could pass
    # the length check on its raw form and then collapse to a much shorter
    # real secret.
    with pytest.raises(ValidationError):
        Settings(**_settings_kwargs(api_token="   short   "))


def test_api_token_whitespace_is_stripped_for_a_valid_token() -> None:
    settings = Settings(**_settings_kwargs(api_token=f"  {TEST_API_TOKEN}  "))
    assert settings.api_token == TEST_API_TOKEN
