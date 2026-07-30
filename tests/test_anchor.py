"""Pure-logic tests for heading anchors (`file.md#some-heading`), no `sublime` needed.

Run directly:  python3 tests/test_anchor.py
Or via pytest: pytest tests/test_anchor.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from anchor import split_anchor, slugify, anchor_index


# --- split_anchor: separate the file part from the #fragment -----------------

def test_split_anchor_path_and_fragment():
    assert split_anchor("docs/TRIGGER_RULES.md#over-the-client-lifecycle") == (
        "docs/TRIGGER_RULES.md", "over-the-client-lifecycle")


def test_split_anchor_same_document():
    # A bare "#frag" targets the current buffer: empty path, not None.
    assert split_anchor("#usage") == ("", "usage")


def test_split_anchor_without_fragment():
    assert split_anchor("docs/internals.md") == ("docs/internals.md", None)


def test_split_anchor_normalises_the_fragment():
    # GitHub percent-encodes non-ASCII anchors and matches case-insensitively.
    assert split_anchor("a.md#Configuraci%C3%B3n") == ("a.md", "configuración")


# --- slugify: heading text -> GitHub-style anchor ----------------------------

def test_slugify_lowercases_and_hyphenates():
    assert slugify("Over the client lifecycle") == "over-the-client-lifecycle"


def test_slugify_drops_punctuation():
    assert slugify("What's new? (v2)") == "whats-new-v2"


def test_slugify_strips_inline_markup():
    assert slugify("The `mmdc` binary and **bold**") == "the-mmdc-binary-and-bold"
    assert slugify("See [the docs](docs/internals.md)") == "see-the-docs"


def test_slugify_keeps_numbers_and_unicode():
    assert slugify("3. Usage") == "3-usage"
    assert slugify("Configuración") == "configuración"


# --- anchor_index: first heading whose slug matches --------------------------

DOC = [
    "# Trigger rules",
    "Prose that mentions ## nothing.",
    "## Over the client lifecycle",
    "### Retry policy",
    "## Retry policy",
]


def test_anchor_index_matches_a_heading():
    assert anchor_index("over-the-client-lifecycle", DOC) == 2


def test_anchor_index_ignores_non_headings():
    assert anchor_index("prose-that-mentions-nothing", DOC) is None


def test_anchor_index_disambiguates_repeats():
    # GitHub suffixes each repeat of a slug: the second "Retry policy" is "-1".
    assert anchor_index("retry-policy", DOC) == 3
    assert anchor_index("retry-policy-1", DOC) == 4


def test_anchor_index_miss():
    assert anchor_index("no-such-heading", DOC) is None


# --- tiny runner so this file works without pytest ---------------------------

if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print("ok   %s" % t.__name__)
        except AssertionError as e:
            failed += 1
            print("FAIL %s: %s" % (t.__name__, e or "assertion"))
    print("\n%d passed, %d failed" % (len(tests) - failed, failed))
    sys.exit(1 if failed else 0)
