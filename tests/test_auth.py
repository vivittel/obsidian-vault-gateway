import pytest
from fastapi.testclient import TestClient

from app.auth import verify_bearer_token

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
