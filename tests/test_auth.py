import pytest
from fastapi.testclient import TestClient

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
