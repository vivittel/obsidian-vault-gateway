import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.auth import verify_bearer_token
from app.config import Settings, get_settings

TEST_API_TOKEN = "test-token-0123456789abcdef"  # noqa: S105 - test fixture, not a real secret

PROTECTED_ENDPOINTS = [
    ("GET", "/api/v1/search"),
    ("GET", "/api/v1/notes?path=Knowledge/no_frontmatter.md"),
]


@pytest.mark.parametrize(("method", "url"), PROTECTED_ENDPOINTS)
def test_missing_token_rejected(client: TestClient, method: str, url: str) -> None:
    response = client.request(method, url)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


@pytest.mark.parametrize(("method", "url"), PROTECTED_ENDPOINTS)
def test_wrong_token_rejected(client: TestClient, method: str, url: str) -> None:
    response = client.request(method, url, headers={"Authorization": "Bearer wrong-token"})
    assert response.status_code == 401


@pytest.mark.parametrize(("method", "url"), PROTECTED_ENDPOINTS)
def test_malformed_auth_header_rejected(client: TestClient, method: str, url: str) -> None:
    response = client.request(method, url, headers={"Authorization": "Basic dXNlcjpwYXNz"})
    assert response.status_code == 401


def test_correct_token_accepted(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.get("/api/v1/search", headers=auth_headers)
    assert response.status_code == 200


def test_error_body_never_leaks_detail(client: TestClient) -> None:
    response = client.get("/api/v1/search")
    body = response.json()
    assert set(body.keys()) == {"error"}
    assert set(body["error"].keys()) == {"code", "message"}


# verify_bearer_token: the pure comparison shared by the REST dependency above
# and, from Phase 1.5 on, the MCP ASGI auth middleware (app/mcp_auth.py).


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


# AUTH_ENABLED=false: require_token becomes a no-op for REST.


def _disable_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "false")
    get_settings.cache_clear()


@pytest.mark.parametrize(("method", "url"), PROTECTED_ENDPOINTS)
def test_auth_disabled_allows_missing_token(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, method: str, url: str
) -> None:
    _disable_auth(monkeypatch)
    response = client.request(method, url)
    assert response.status_code == 200


@pytest.mark.parametrize(("method", "url"), PROTECTED_ENDPOINTS)
def test_auth_disabled_allows_wrong_token(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, method: str, url: str
) -> None:
    _disable_auth(monkeypatch)
    response = client.request(method, url, headers={"Authorization": "Bearer wrong-token"})
    assert response.status_code == 200


@pytest.mark.parametrize(("method", "url"), PROTECTED_ENDPOINTS)
def test_auth_disabled_allows_malformed_auth_header(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, method: str, url: str
) -> None:
    _disable_auth(monkeypatch)
    response = client.request(method, url, headers={"Authorization": "Basic dXNlcjpwYXNz"})
    assert response.status_code == 200
