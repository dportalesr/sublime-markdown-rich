"""Pure-logic tests for section-reference parsing/matching (no `sublime` needed).

Run directly:  python3 tests/test_section_ref.py
Or via pytest: pytest tests/test_section_ref.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from section_ref import ref_at, heading_number, first_matching_index


# --- ref_at: is the caret on a "§N" reference, and what number? --------------

def test_ref_at_basic():
    # "see §3 now" -> § at index 4, digit at 5
    assert ref_at("see §3 now", 5) == "3"
    assert ref_at("see §3 now", 4) == "3"   # on the sigil
    assert ref_at("see §3 now", 6) == "3"   # just past the digit (inclusive end)


def test_ref_at_dotted():
    assert ref_at("§3.1", 0) == "3.1"
    assert ref_at("prefix §12.4.5 tail", 10) == "12.4.5"


def test_ref_at_optional_space():
    assert ref_at("§ 7 here", 2) == "7"


def test_ref_at_off_target():
    assert ref_at("see §3 now", 0) is None
    assert ref_at("no reference here", 4) is None


def test_ref_at_strips_trailing_punctuation():
    # "(§3)." — the ref is just "3"; the digit sits at index 2
    assert ref_at("(§3).", 2) == "3"


# --- heading_number: leading numeric label of an ATX heading -----------------

def test_heading_number_simple():
    assert heading_number("## 3. Usage") == "3"
    assert heading_number("# 1 Intro") == "1"


def test_heading_number_dotted():
    assert heading_number("### 3.1 Foo") == "3.1"
    assert heading_number("###### 3.2.1 Bar") == "3.2.1"


def test_heading_number_indented():
    assert heading_number("   ## 4. Indented up to 3 spaces") == "4"


def test_heading_number_none():
    assert heading_number("## Usage") is None          # no number
    assert heading_number("not a heading 3") is None    # no marker
    assert heading_number("#3 nospace") is None          # ATX needs space after #
    assert heading_number("####### 8 too deep") is None  # 7 hashes is not a heading


# --- first_matching_index: first heading whose number equals the label -------

DOC = [
    "# Doc Title",
    "## 1. Intro",
    "## 2. Setup",
    "## 3. Usage",
    "### 3.1 Basics",
    "### 3.1 Duplicate",
]


def test_first_matching_index_top_level():
    assert first_matching_index("3", DOC) == 3


def test_first_matching_index_subsection():
    assert first_matching_index("3.1", DOC) == 4


def test_first_matching_index_miss():
    assert first_matching_index("9", DOC) is None
    assert first_matching_index("3.2", DOC) is None


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
