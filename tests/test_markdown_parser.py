import time
from typing import Any

import pytest

from app.services.markdown_parser import (
    MAX_JSON_SAFE_DEPTH,
    MAX_JSON_SAFE_TOTAL_CHARS,
    FrontmatterBudgetExceededError,
    FrontmatterCycleError,
    normalise_tags,
    parse_note,
    to_json_safe,
)


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


def test_tolerates_extremely_deep_nesting_without_aliases() -> None:
    # No aliases involved — this is PyYAML's own composer exhausting Python's
    # call stack (confirmed directly: depth 500 already raises RecursionError
    # from yaml.safe_load before to_json_safe ever runs), not the exponential
    # blow-up to_json_safe's own budget guards against below. Degrades to no
    # frontmatter, the same as any other unparseable block.
    deeply_nested = "[" * 800 + "1" + "]" * 800
    text = f"---\na: {deeply_nested}\n---\n\nbody\n"
    parsed = parse_note(text, fallback_title="fallback")
    # Same degradation contract as malformed YAML: the whole original text
    # is returned as the body, unsplit, when the frontmatter block can't be
    # parsed at all (see _split_frontmatter's docstring).
    assert parsed.metadata == {}
    assert parsed.body == text


def _alias_bomb(depth: int, width: int) -> str:
    """A frontmatter block where each anchor references ``width`` copies of
    the previous one, ``depth`` levels deep — the same shape that exhausted a
    2 GiB budget in 11 seconds before ``to_json_safe`` had one. YAML's alias
    sharing keeps parsing this cheap; rebuilding it without that sharing is
    what explodes.
    """
    lines = ["a0: &a0 x"]
    for i in range(1, depth + 1):
        refs = ",".join([f"*a{i - 1}"] * width)
        lines.append(f"a{i}: &a{i} [{refs}]")
    return "---\n" + "\n".join(lines) + "\n---\n\nbody\n"


def test_to_json_safe_raises_on_exponential_alias_explosion() -> None:
    text = _alias_bomb(depth=9, width=8)
    assert len(text) < 500, "must reproduce the exploit with a tiny note, not a large one"
    parsed = parse_note(text, fallback_title="fallback")

    start = time.perf_counter()
    with pytest.raises(FrontmatterBudgetExceededError):
        to_json_safe(parsed.metadata)
    # The budget must abort during the walk, not after building the full
    # (8**9-element) structure and then measuring it.
    assert time.perf_counter() - start < 2.0

    # Only frontmatter conversion is bounded; the body is unaffected.
    assert parsed.body.strip() == "body"


def test_to_json_safe_raises_on_cyclic_alias() -> None:
    # `&a [*a]` is valid YAML that PyYAML parses into a list containing
    # itself — an actual cycle, not just repeated sharing.
    parsed = parse_note("---\na: &a [*a]\n---\n\nbody\n", fallback_title="fallback")
    assert parsed.metadata["a"][0] is parsed.metadata["a"]

    with pytest.raises(FrontmatterCycleError):
        to_json_safe(parsed.metadata)


def test_to_json_safe_allows_a_shared_non_cyclic_alias_in_two_branches() -> None:
    # `b: *x` reuses the exact same list object `a` points at. That is
    # sharing, not a cycle: the ancestor set must be cleared after `a`
    # finishes so `b` does not spuriously look like a self-reference.
    parsed = parse_note("---\na: &x [1, 2]\nb: *x\n---\n\nbody\n", fallback_title="fallback")
    assert parsed.metadata["a"] is parsed.metadata["b"]

    assert to_json_safe(parsed.metadata) == {"a": [1, 2], "b": [1, 2]}


def test_to_json_safe_charges_dict_keys_toward_the_character_budget() -> None:
    # A single oversized key, not a value — the budget must count keys too.
    huge_key = "k" * (MAX_JSON_SAFE_TOTAL_CHARS + 1)
    with pytest.raises(FrontmatterBudgetExceededError):
        to_json_safe({huge_key: "x"})


def test_to_json_safe_rejects_excessive_depth() -> None:
    nested: Any = "leaf"
    for _ in range(MAX_JSON_SAFE_DEPTH + 2):
        nested = [nested]
    with pytest.raises(FrontmatterBudgetExceededError):
        to_json_safe(nested)
