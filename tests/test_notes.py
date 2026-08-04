from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from app.services import note_service


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


# app.services.note_service unit tests — same behaviour, called directly
# rather than through the REST router, so Phase 1.5's MCP read_note tool can
# be checked against the identical function.

TOKYO = ZoneInfo("Asia/Tokyo")


def test_note_service_parses_frontmatter_and_body(vault_root: Path) -> None:
    response = note_service.read_note(
        "Knowledge/PC/GPU/RTX 5070.md",
        read_root=vault_root,
        max_note_bytes=1_048_576,
        timezone=TOKYO,
    )
    assert response.id == response.path == "Knowledge/PC/GPU/RTX 5070.md"
    assert response.title == "RTX 5070"
    assert response.frontmatter == {"title": "RTX 5070", "tags": ["gpu", "nvidia"]}
    assert "[[GPU比較]]" in response.content


def test_note_service_tolerates_broken_yaml(vault_root: Path) -> None:
    response = note_service.read_note(
        "Knowledge/broken_frontmatter.md",
        read_root=vault_root,
        max_note_bytes=1_048_576,
        timezone=TOKYO,
    )
    assert response.frontmatter == {}
    assert response.title == "broken_frontmatter"


def test_note_service_preserves_crlf(vault_root: Path) -> None:
    response = note_service.read_note(
        "Knowledge/crlf.md",
        read_root=vault_root,
        max_note_bytes=1_048_576,
        timezone=TOKYO,
    )
    assert "\r\n" in response.content


def test_note_service_sets_truncated_flag_over_size_limit(vault_root: Path) -> None:
    response = note_service.read_note(
        "Knowledge/large.md",
        read_root=vault_root,
        max_note_bytes=1024,
        timezone=TOKYO,
    )
    assert response.truncated is True
    assert len(response.content) <= 1024


def test_note_service_response_never_contains_absolute_path(vault_root: Path) -> None:
    response = note_service.read_note(
        "Knowledge/PC/GPU/RTX 5070.md",
        read_root=vault_root,
        max_note_bytes=1_048_576,
        timezone=TOKYO,
    )
    assert str(vault_root) not in response.model_dump_json()


# Frontmatter that is too expensive or too cyclic for to_json_safe to convert
# degrades to no frontmatter, the same as unparseable YAML — the note itself
# is still readable. See app/services/markdown_parser.py's budget/cycle
# guards and their unit tests in tests/test_markdown_parser.py.


def _write_alias_bomb_note(vault_root: Path, name: str, frontmatter: str) -> None:
    (vault_root / name).write_text(f"---\n{frontmatter}\n---\n\nBody text.\n", encoding="utf-8")


def test_note_service_degrades_exponential_alias_frontmatter_to_empty(vault_root: Path) -> None:
    lines = ["a0: &a0 x"]
    for i in range(1, 10):
        refs = ",".join([f"*a{i - 1}"] * 8)
        lines.append(f"a{i}: &a{i} [{refs}]")
    _write_alias_bomb_note(vault_root, "alias-bomb.md", "\n".join(lines))

    response = note_service.read_note(
        "alias-bomb.md", read_root=vault_root, max_note_bytes=1_048_576, timezone=TOKYO
    )
    assert response.frontmatter == {}
    assert "Body text." in response.content


def test_note_service_degrades_cyclic_alias_frontmatter_to_empty(vault_root: Path) -> None:
    _write_alias_bomb_note(vault_root, "alias-cycle.md", "a: &a [*a]")

    response = note_service.read_note(
        "alias-cycle.md", read_root=vault_root, max_note_bytes=1_048_576, timezone=TOKYO
    )
    assert response.frontmatter == {}
    assert "Body text." in response.content


def test_read_note_alias_bomb_via_http_degrades_instead_of_500(
    client: TestClient, auth_headers: dict[str, str], vault_root: Path
) -> None:
    lines = ["a0: &a0 x"]
    for i in range(1, 10):
        refs = ",".join([f"*a{i - 1}"] * 8)
        lines.append(f"a{i}: &a{i} [{refs}]")
    _write_alias_bomb_note(vault_root, "alias-bomb-http.md", "\n".join(lines))

    response = client.get(
        "/api/v1/notes", params={"path": "alias-bomb-http.md"}, headers=auth_headers
    )
    assert response.status_code == 200
    assert response.json()["frontmatter"] == {}
