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


def test_tolerates_extremely_deep_nesting_that_forces_the_safe_loader_fallback() -> None:
    # No aliases involved — this is PyYAML's own composer exhausting Python's
    # call stack (confirmed directly: depth 500 already raises RecursionError
    # from yaml.SafeLoader), not the exponential blow-up to_json_safe's own
    # budget guards against below. Deep enough that the YAML text (2 chars
    # per flow-style level in the worst case) exceeds
    # markdown_parser._CSAFE_MAX_YAML_CHARS, forcing the safe pure-Python
    # fallback — CSafeLoader itself tolerates this depth without raising
    # (confirmed directly: it does not segfault until somewhere between
    # depth 20,000 and 50,000), so a shallower case here would silently stop
    # testing the fallback at all and instead exercise CSafeLoader's own
    # (much larger) tolerance. Degrades to no frontmatter, the same as any
    # other unparseable block.
    from app.services.markdown_parser import _CSAFE_MAX_YAML_CHARS

    deeply_nested = "[" * 10_000 + "1" + "]" * 10_000
    text = f"---\na: {deeply_nested}\n---\n\nbody\n"
    yaml_text = text[len("---\n") : text.rindex("---\n")]
    assert len(yaml_text) > _CSAFE_MAX_YAML_CHARS, "no longer exercises the safe fallback"

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


# --- yaml.CSafeLoader routing (_split_frontmatter) --------------------------
#
# CSafeLoader parses ~7x faster than pure-Python SafeLoader — worth having
# on the two paths that parse every note in the vault (search, vault/
# summary) — but confirmed directly (not a hypothetical): deeply nested
# YAML that raises a clean, catchable RecursionError under SafeLoader
# instead segfaults the whole process under CSafeLoader, somewhere between
# depth 20,000 (confirmed safe) and 50,000 (confirmed crash). Reproducing
# that crash inside this test run would be exactly the outcome these tests
# exist to prevent, so what's tested here is the *routing* — that ordinary
# frontmatter takes the fast path and oversized text falls back to the safe
# one — at depths and sizes with a wide, deliberately conservative margin
# below the confirmed-safe boundary, never near it.


def _captured_loader(monkeypatch: pytest.MonkeyPatch) -> dict[str, type]:
    """Patch yaml.load to record which Loader class _split_frontmatter picks,
    without needing size- or depth-based inference to guess after the fact.
    """
    import yaml

    captured: dict[str, type] = {}
    original_load = yaml.load

    def spy(stream, Loader):  # noqa: N803 - matches yaml.load's own parameter name
        captured["loader"] = Loader
        return original_load(stream, Loader=Loader)

    monkeypatch.setattr(yaml, "load", spy)
    return captured


def test_uses_the_fast_loader_for_ordinary_frontmatter(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import markdown_parser

    captured = _captured_loader(monkeypatch)
    parse_note("---\ntitle: RTX 5070\ntags: [gpu, nvidia]\n---\n\nBody.\n", fallback_title="x")
    assert captured["loader"] is markdown_parser._FAST_YAML_LOADER


def test_falls_back_to_the_safe_loader_above_the_size_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import yaml

    from app.services import markdown_parser

    captured = _captured_loader(monkeypatch)
    # A single oversized (but shallow — no nesting at all) string scalar:
    # this tests the size-based routing decision in isolation from depth,
    # confirming the cap is a size cap, not disguised depth-sniffing.
    huge_value = "x" * (markdown_parser._CSAFE_MAX_YAML_CHARS + 1)
    parse_note(f"---\ntitle: {huge_value}\n---\n\nBody.\n", fallback_title="x")
    assert captured["loader"] is yaml.SafeLoader


def test_moderately_deep_nesting_still_uses_the_fast_loader_and_parses_correctly() -> None:
    # Depth with a wide (10x+) margin below the confirmed-safe boundary
    # (20,000) — proving CSafeLoader handles realistic-but-deep nesting
    # correctly, not just that shallow input takes the fast path.
    depth = 2000
    nested = "[" * depth + "1" + "]" * depth
    parsed = parse_note(f"---\na: {nested}\n---\n\nBody.\n", fallback_title="x")
    value = parsed.metadata["a"]
    for _ in range(depth - 1):
        value = value[0]
    assert value == [1]


@pytest.mark.parametrize(
    "yaml_text",
    [
        "a: yes\nb: true\nc: off\n",
        "a: ~\nb: null\nc:\n",
        "a: 2026-01-02\n",
        "a: 007\nb: 1_000\nc: 0x1f\nd: .inf\ne: 1.5e3\n",
        "a: 1\na: 2\n",
        "a: 日本語\nb: ＲＴＸ\n",
        "tags: gpu, pc\n",
        "tags: [gpu, pc]\n",
        "a: &x [1, 2]\nb: *x\n",
    ],
    ids=[
        "bool",
        "null",
        "date",
        "numeric-forms",
        "duplicate-key",
        "unicode-and-fullwidth",
        "tags-as-string",
        "tags-as-list",
        "shared-alias",
    ],
)
def test_fast_and_safe_loaders_agree_on_ordinary_yaml(yaml_text: str) -> None:
    import yaml

    from app.services.markdown_parser import _FAST_YAML_LOADER

    # S506 is a false positive on both sides: yaml.SafeLoader and
    # _FAST_YAML_LOADER (yaml.CSafeLoader or the same yaml.SafeLoader) are
    # both always safe loaders.
    assert yaml.load(yaml_text, Loader=yaml.SafeLoader) == yaml.load(
        yaml_text, Loader=_FAST_YAML_LOADER  # noqa: S506
    )


def test_fast_and_safe_loaders_agree_on_malformed_yaml() -> None:
    import yaml

    from app.services.markdown_parser import _FAST_YAML_LOADER

    malformed = "a: [1, 2\n"
    for loader in (yaml.SafeLoader, _FAST_YAML_LOADER):
        with pytest.raises(yaml.YAMLError):
            yaml.load(malformed, Loader=loader)  # noqa: S506 - always a safe loader


def test_fast_and_safe_loaders_agree_on_cyclic_alias() -> None:
    import yaml

    from app.services.markdown_parser import _FAST_YAML_LOADER

    cyclic = "a: &a [*a]\n"
    for loader in (yaml.SafeLoader, _FAST_YAML_LOADER):
        result = yaml.load(cyclic, Loader=loader)  # noqa: S506 - always a safe loader
        assert result["a"][0] is result["a"]
