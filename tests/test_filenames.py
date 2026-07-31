import pytest

from app.exceptions import InvalidTitleError
from app.services.filenames import note_file_name, sanitise_title


def test_sanitises_forbidden_characters() -> None:
    assert sanitise_title('a/b\\c:d*e?f"g<h>i|j') == "a-b-c-d-e-f-g-h-i-j"


def test_collapses_whitespace_and_trims() -> None:
    assert sanitise_title("  hello   world  ") == "hello world"


def test_strips_leading_and_trailing_dots() -> None:
    assert sanitise_title("...secret...") == "secret"


def test_preserves_japanese_title() -> None:
    assert sanitise_title("ChatGPTとObsidian Vaultの連携") == "ChatGPTとObsidian Vaultの連携"


def test_truncates_to_max_length() -> None:
    long_title = "a" * 200
    result = sanitise_title(long_title)
    assert len(result) <= 100


@pytest.mark.parametrize("reserved", ["CON", "con", "NUL", "COM1", "lpt9"])
def test_rejects_windows_reserved_names(reserved: str) -> None:
    with pytest.raises(InvalidTitleError):
        sanitise_title(reserved)


def test_rejects_empty_after_sanitising() -> None:
    with pytest.raises(InvalidTitleError):
        sanitise_title("...")


def test_forbidden_characters_become_dashes_not_empty() -> None:
    # Each forbidden character is replaced, not stripped, so a title made only
    # of forbidden characters still yields a usable (if ugly) name rather than
    # raising — it collapses to dashes, which is a valid file name component.
    assert sanitise_title("///***") == "------"


def test_note_file_name_sequence() -> None:
    assert note_file_name("foo", 1) == "foo.md"
    assert note_file_name("foo", 2) == "foo-2.md"
    assert note_file_name("foo", 3) == "foo-3.md"
