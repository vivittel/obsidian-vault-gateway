from pathlib import Path

from fastapi.testclient import TestClient


def test_read_note_returns_frontmatter_and_content(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.get(
        "/api/v1/notes",
        params={"path": "Knowledge/PC/GPU/RTX 5070.md"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "Knowledge/PC/GPU/RTX 5070.md"
    assert body["path"] == "Knowledge/PC/GPU/RTX 5070.md"
    assert body["title"] == "RTX 5070"
    assert body["frontmatter"] == {"title": "RTX 5070", "tags": ["gpu", "nvidia"]}
    assert "# RTX 5070" in body["content"]
    assert body["truncated"] is False


def test_read_note_with_space_in_path_via_http(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    # httpx encodes the space when building the request; Starlette decodes the
    # query string exactly once, so path_security receives a literal space —
    # the same contract test_path_security.py documents at the unit level.
    response = client.get(
        "/api/v1/notes",
        params={"path": "Knowledge/PC/GPU/RTX 5070.md"},
        headers=auth_headers,
    )
    assert response.status_code == 200


def test_read_note_preserves_wikilinks(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.get(
        "/api/v1/notes",
        params={"path": "Knowledge/PC/GPU/RTX 5070.md"},
        headers=auth_headers,
    )
    assert "[[GPU比較]]" in response.json()["content"]


def test_read_note_tolerates_broken_yaml(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.get(
        "/api/v1/notes",
        params={"path": "Knowledge/broken_frontmatter.md"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["frontmatter"] == {}
    assert body["title"] == "broken_frontmatter"


def test_read_note_falls_back_to_filename_title(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.get(
        "/api/v1/notes",
        params={"path": "Knowledge/no_frontmatter.md"},
        headers=auth_headers,
    )
    assert response.json()["title"] == "no_frontmatter"


def test_read_note_preserves_crlf(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.get(
        "/api/v1/notes", params={"path": "Knowledge/crlf.md"}, headers=auth_headers
    )
    assert response.status_code == 200
    assert "\r\n" in response.json()["content"]


def test_read_note_truncates_large_note(
    client: TestClient, auth_headers: dict[str, str], monkeypatch, vault_root: Path
) -> None:
    from app.config import get_settings

    monkeypatch.setenv("MAX_NOTE_SIZE_BYTES", "1024")
    get_settings.cache_clear()
    try:
        response = client.get(
            "/api/v1/notes", params={"path": "Knowledge/large.md"}, headers=auth_headers
        )
        assert response.status_code == 200
        body = response.json()
        assert body["truncated"] is True
        assert len(body["content"]) <= 1024
    finally:
        get_settings.cache_clear()


def test_read_note_missing_path_param_is_400(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.get("/api/v1/notes", headers=auth_headers)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_read_note_traversal_is_rejected(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.get(
        "/api/v1/notes", params={"path": "../secret.md"}, headers=auth_headers
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_PATH"


def test_read_note_missing_note_is_404(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.get(
        "/api/v1/notes", params={"path": "Knowledge/does-not-exist.md"}, headers=auth_headers
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOTE_NOT_FOUND"


def test_read_note_response_never_contains_absolute_path(
    client: TestClient, auth_headers: dict[str, str], vault_root: Path
) -> None:
    response = client.get(
        "/api/v1/notes",
        params={"path": "Knowledge/PC/GPU/RTX 5070.md"},
        headers=auth_headers,
    )
    assert str(vault_root) not in response.text
