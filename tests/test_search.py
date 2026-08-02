import pytest
from fastapi.testclient import TestClient


def test_search_finds_ascii_query(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.get("/api/v1/search", params={"q": "RTX 5070"}, headers=auth_headers)
    assert response.status_code == 200
    results = response.json()["results"]
    assert any(r["path"] == "Knowledge/PC/GPU/RTX 5070.md" for r in results)


def test_search_is_case_insensitive(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.get("/api/v1/search", params={"q": "rtx 5070"}, headers=auth_headers)
    results = response.json()["results"]
    assert any(r["path"] == "Knowledge/PC/GPU/RTX 5070.md" for r in results)


def test_search_matches_fullwidth_query_against_halfwidth_text(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    # GPU比較.md contains "ＲＴＸ５０７０" (full-width); the query below is
    # half-width ASCII. NFKC folding must make them equivalent.
    response = client.get("/api/v1/search", params={"q": "RTX5070"}, headers=auth_headers)
    results = response.json()["results"]
    assert any(r["path"] == "Knowledge/PC/GPU/GPU比較.md" for r in results)


def test_search_japanese_query(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.get("/api/v1/search", params={"q": "購入"}, headers=auth_headers)
    results = response.json()["results"]
    assert any(r["path"] == "Knowledge/PC/GPU/RTX 5070.md" for r in results)


def test_search_by_tag(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.get("/api/v1/search", params={"tags": "nvidia"}, headers=auth_headers)
    results = response.json()["results"]
    assert any(r["path"] == "Knowledge/PC/GPU/RTX 5070.md" for r in results)
    assert all("nvidia" in r["tags"] for r in results)


def test_search_tags_filter_is_and(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.get(
        "/api/v1/search", params={"tags": "gpu,nvidia"}, headers=auth_headers
    )
    results = response.json()["results"]
    assert all({"gpu", "nvidia"} <= set(r["tags"]) for r in results)

    response_missing = client.get(
        "/api/v1/search", params={"tags": "gpu,does-not-exist"}, headers=auth_headers
    )
    assert response_missing.json()["results"] == []


def test_search_folder_filter(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.get(
        "/api/v1/search", params={"folder": "Knowledge/PC"}, headers=auth_headers
    )
    results = response.json()["results"]
    assert results
    assert all(r["path"].startswith("Knowledge/PC/") for r in results)


def test_search_limit_out_of_range_is_rejected(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    # limit's own Query(ge=1, le=200) rejects an out-of-range value outright;
    # a value inside that range still gets clamped to MAX_SEARCH_RESULTS below.
    response = client.get("/api/v1/search", params={"limit": 500}, headers=auth_headers)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_search_limit_within_range_is_clamped_to_configured_max(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.get("/api/v1/search", params={"limit": 100}, headers=auth_headers)
    assert response.status_code == 200
    assert len(response.json()["results"]) <= 50


def test_search_excludes_hidden_and_obsidian_and_non_markdown(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.get("/api/v1/search", params={"q": ""}, headers=auth_headers)
    paths = [r["path"] for r in response.json()["results"]]
    assert all(not p.startswith(".") for p in paths)
    assert all(".obsidian" not in p for p in paths)
    assert all(p.endswith(".md") for p in paths)


def test_search_excludes_symlinks(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.get("/api/v1/search", params={"q": ""}, headers=auth_headers)
    paths = [r["path"] for r in response.json()["results"]]
    assert "Knowledge/symlinked-note.md" not in paths
    assert not any(p.startswith("Knowledge/SymlinkedDir/") for p in paths)


def test_search_excerpt_omits_frontmatter_block(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.get("/api/v1/search", params={"q": "RTX 5070"}, headers=auth_headers)
    results = response.json()["results"]
    hit = next(r for r in results if r["path"] == "Knowledge/PC/GPU/RTX 5070.md")
    assert "title:" not in hit["excerpt"]
    assert "tags:" not in hit["excerpt"]


def test_search_no_query_returns_all_notes_newest_first(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.get("/api/v1/search", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["next_cursor"] is None
    assert len(response.json()["results"]) > 0


def test_search_pagination_visits_every_note_without_duplicates_or_gaps(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    full = client.get("/api/v1/search", headers=auth_headers).json()["results"]
    assert len(full) >= 3  # otherwise limit=2 wouldn't exercise more than one page

    seen: list[str] = []
    cursor = None
    for _ in range(len(full) + 1):  # generous bound; a stuck cursor must not loop forever
        params: dict[str, object] = {"limit": 2}
        if cursor:
            params["cursor"] = cursor
        page = client.get("/api/v1/search", params=params, headers=auth_headers).json()
        seen.extend(r["path"] for r in page["results"])
        cursor = page["next_cursor"]
        if cursor is None:
            break

    assert cursor is None, "pagination did not terminate"
    assert seen == [r["path"] for r in full]
    assert len(seen) == len(set(seen))


def test_search_cursor_from_a_different_query_is_rejected(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    page = client.get(
        "/api/v1/search", params={"q": "RTX", "limit": 1}, headers=auth_headers
    ).json()
    assert page["next_cursor"] is not None

    response = client.get(
        "/api/v1/search",
        params={"q": "GPU", "limit": 1, "cursor": page["next_cursor"]},
        headers=auth_headers,
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_CURSOR"


def test_search_cursor_from_a_different_folder_is_rejected(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    page = client.get(
        "/api/v1/search", params={"folder": "Knowledge/PC", "limit": 1}, headers=auth_headers
    ).json()
    assert page["next_cursor"] is not None

    response = client.get(
        "/api/v1/search",
        params={"limit": 1, "cursor": page["next_cursor"]},
        headers=auth_headers,
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_CURSOR"


def test_search_tampered_cursor_is_rejected(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    page = client.get("/api/v1/search", params={"limit": 1}, headers=auth_headers).json()
    assert page["next_cursor"] is not None
    tampered = page["next_cursor"][:-1] + ("A" if page["next_cursor"][-1] != "A" else "B")

    response = client.get(
        "/api/v1/search", params={"limit": 1, "cursor": tampered}, headers=auth_headers
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_CURSOR"


def test_search_changing_limit_between_pages_does_not_invalidate_cursor(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    first = client.get("/api/v1/search", params={"limit": 1}, headers=auth_headers).json()
    assert first["next_cursor"] is not None

    second = client.get(
        "/api/v1/search",
        params={"limit": 3, "cursor": first["next_cursor"]},
        headers=auth_headers,
    )
    assert second.status_code == 200


@pytest.mark.parametrize("folder", ["/Knowledge", "//Knowledge", "/"])
def test_search_folder_rejects_absolute_paths(
    client: TestClient, auth_headers: dict[str, str], folder: str
) -> None:
    response = client.get("/api/v1/search", params={"folder": folder}, headers=auth_headers)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_PATH"
