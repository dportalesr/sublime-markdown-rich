"""Pure logic for heading anchors like ``file.md#some-heading`` — no ``sublime`` import.

Sits beside ``section_ref.py`` for the same reason: ``markdown_rich.py`` imports
``sublime`` at module top, so the parsing and slug rules live here where plain Python
can unit-test them. Slugs follow GitHub's anchor generation, which is what the links in
a README were written against.
"""

import re
import urllib.parse

# Inline link/image: keep the label, drop the target. GitHub slugs the rendered text,
# so "[the docs](docs/internals.md)" contributes "the docs".
_INLINE_LINK_RE = re.compile(r'!?\[([^\]]*)\]\([^)]*\)')
_HTML_TAG_RE = re.compile(r'<[^>]+>')
# Emphasis/code delimiters vanish when the heading renders. `_` is deliberately absent:
# GitHub keeps it in slugs, so `snake_case` headings stay reachable (the cost is that a
# heading emphasised with underscores keeps them).
_MARKUP_RE = re.compile(r'[`*~]')
# Everything that isn't a word character, whitespace or a hyphen is dropped.
_PUNCTUATION_RE = re.compile(r'[^\w\s-]')
_WHITESPACE_RE = re.compile(r'\s+')

# ATX heading: up to 3 leading spaces, 1-6 hashes, a required space, then the text.
# A closing run of hashes ("## Foo ##") is markup, not part of the text.
HEADING_TEXT_RE = re.compile(r'^ {0,3}#{1,6}[ \t]+(.*?)[ \t]*#*[ \t]*$')


def split_anchor(target):
    """Split a link target into ``(path, anchor)``.

    :param str target: a link target, e.g. ``"docs/a.md#usage"`` or ``"#usage"``
    :returns: the path (``""`` for a same-document reference) and the anchor,
        percent-decoded and lowercased, or ``None`` when the target carries no
        usable fragment
    :rtype: tuple(str, str or None)
    """
    path, sep, fragment = target.partition("#")
    if not (sep and fragment):
        return path, None
    return path, urllib.parse.unquote(fragment).lower()


def slugify(text):
    """Return the GitHub anchor slug for a heading's text.

    Inline markup is stripped, punctuation dropped, the rest lowercased with runs of
    whitespace collapsed to single hyphens. Non-ASCII letters survive, matching
    GitHub (a link to such a heading is usually percent-encoded; see `split_anchor`).

    :param str text: heading text without its ``#`` markers
    :returns: the slug, e.g. ``"over-the-client-lifecycle"``
    :rtype: str
    """
    text = _INLINE_LINK_RE.sub(r'\1', text)
    text = _HTML_TAG_RE.sub('', text)
    text = _MARKUP_RE.sub('', text)
    text = _PUNCTUATION_RE.sub('', text)
    return _WHITESPACE_RE.sub('-', text.strip().lower())


def heading_slug(line):
    """Return the anchor slug of an ATX heading line, or ``None``.

    :param str line: a full line of text
    :returns: the slug for a heading line, else ``None``
    :rtype: str or None
    """
    m = HEADING_TEXT_RE.match(line)
    return slugify(m.group(1)) if m else None


def anchor_index(anchor, lines):
    """Index of the first line in ``lines`` whose heading slug is ``anchor``.

    Repeated slugs are disambiguated the way GitHub does it: the second heading that
    slugs to ``"retry-policy"`` answers to ``"retry-policy-1"``, the third to ``-2``.

    :param str anchor: an anchor without its ``#``, e.g. ``"retry-policy-1"``
    :param lines: iterable of line-text strings, in document order
    :returns: the 0-based index of the matching heading, or ``None``
    :rtype: int or None
    """
    anchor = anchor.lower()
    seen = {}
    for i, line in enumerate(lines):
        slug = heading_slug(line)
        if not slug:
            continue
        repeats = seen.get(slug, 0)
        seen[slug] = repeats + 1
        candidate = slug if not repeats else "%s-%d" % (slug, repeats)
        if candidate == anchor:
            return i
    return None
