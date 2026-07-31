from app.services.markdown_parser import normalise_tags, parse_note, to_json_safe


def test_parses_frontmatter_title_and_tags() -> None:
    text = "---\ntitle: RTX 5070\ntags: [gpu, nvidia]\n---\n\n# RTX 5070\n\nBody.\n"
    parsed = parse_note(text, fallback_title="fallback")
    assert parsed.title == "RTX 5070"
    assert parsed.tags == ["gpu", "nvidia"]
    assert "Body." in parsed.body
    assert "title:" not in parsed.body


def test_falls_back_to_provided_title_when_missing() -> None:
    parsed = parse_note("# Just a heading\n", fallback_title="fallback-title")
    assert parsed.title == "fallback-title"


def test_tolerates_broken_yaml() -> None:
    text = "---\ntitle: [unterminated\n---\n\n# Broken\n"
    parsed = parse_note(text, fallback_title="fallback")
    assert parsed.title == "fallback"
    assert parsed.metadata == {}


def test_extracts_headings() -> None:
    text = "# Title\n\n## Sub\n\nBody\n\n### Sub sub ###\n"
    parsed = parse_note(text, fallback_title="fallback")
    assert parsed.headings == ["Title", "Sub", "Sub sub"]


def test_preserves_wikilinks_in_body() -> None:
    text = "---\ntitle: A\n---\n\nSee [[Other Note]] for details.\n"
    parsed = parse_note(text, fallback_title="fallback")
    assert "[[Other Note]]" in parsed.body


def test_normalise_tags_handles_string_list_and_hash_prefix() -> None:
    assert normalise_tags(["gpu", "#nvidia"]) == ["gpu", "nvidia"]
    assert normalise_tags("gpu, nvidia") == ["gpu", "nvidia"]
    assert normalise_tags(None) == []
    assert normalise_tags(["dup", "dup"]) == ["dup"]


def test_to_json_safe_converts_dates_and_nested_structures() -> None:
    import datetime

    value = {"when": datetime.date(2026, 7, 31), "list": [1, 2.5, True, None]}
    safe = to_json_safe(value)
    assert safe == {"when": "2026-07-31", "list": [1, 2.5, True, None]}
