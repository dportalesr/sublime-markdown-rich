"""Pure logic for section references like ``§3`` / ``§3.1`` — no ``sublime`` import.

Kept separate from ``markdown_rich.py`` (which imports ``sublime`` at module top and
so can't be imported outside Sublime) so this navigation logic is unit-testable with
plain Python. ``markdown_rich.py`` imports these helpers and supplies the view/region
plumbing around them.
"""

import re

# A section reference in prose: the § sign, optional space, then a dotted number.
# Trailing sentence punctuation (".", ")", ...) falls outside the digit run, so it is
# naturally excluded from the captured label.
SECTION_REF_RE = re.compile(r'§\s*(\d+(?:\.\d+)*)')

# ATX heading with a leading numeric label: up to 3 leading spaces, 1-6 hashes, a
# required space, then the number. The `(?:\.\d+)*` stops before a "." that isn't
# followed by a digit, so "## 3. Usage" yields "3" and "### 3.1 Foo" yields "3.1".
HEADING_NUMBER_RE = re.compile(r'^ {0,3}#{1,6}[ \t]+(\d+(?:\.\d+)*)')


def ref_at(text, col):
    """Return the section number under ``col`` in ``text``, or ``None``.

    :param str text: a single line of buffer text
    :param int col: 0-based column of the caret within ``text``
    :returns: the dotted number (e.g. ``"3.1"``) when the caret sits on a ``§`` ref,
        else ``None``
    :rtype: str or None
    """
    for m in SECTION_REF_RE.finditer(text):
        if m.start() <= col <= m.end():
            return m.group(1)
    return None


def heading_number(line):
    """Return the leading numeric label of an ATX heading line, or ``None``.

    :param str line: a full line of text
    :returns: the dotted number (e.g. ``"3"`` or ``"3.1"``) for a numbered ATX
        heading, else ``None`` (unnumbered headings and non-headings)
    :rtype: str or None
    """
    m = HEADING_NUMBER_RE.match(line)
    return m.group(1) if m else None


def first_matching_index(label, lines):
    """Index of the first line in ``lines`` that is a heading numbered ``label``.

    :param str label: a dotted section number, e.g. ``"3"`` or ``"3.1"``
    :param lines: iterable of line-text strings, in document order
    :returns: the 0-based index of the first exact-numbered-heading match, or ``None``
    :rtype: int or None
    """
    for i, line in enumerate(lines):
        if heading_number(line) == label:
            return i
    return None
