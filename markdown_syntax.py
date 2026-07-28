"""Generates a Markdown syntax that highlights fenced code for *every* installed syntax.

Sublime's own Markdown syntax embeds a fixed list of languages, one hand-written
context each, so a newly installed syntax (mermaid, d2, ...) leaves its fenced blocks
as plain `markup.raw`. There is no injection and no lookup of the info string against
installed syntaxes, so the only way in is another syntax that `extends` Markdown.

Rather than hardcode the missing languages there too (the same trap one level down),
this module renders that wrapper from whatever is installed: the caller supplies the
syntaxes, this decides which the parent already covers, which info strings should open
each one, and what the file looks like.

No ``sublime`` import, so all of it is unit-testable.
"""

import collections
import re

#: One language in the generated syntax.
#:
#: ``name``   display name of the syntax (for the generated comment)
#: ``scope``  its top-level scope, e.g. ``source.mermaid``
#: ``tokens`` info strings that should open it, e.g. ``["mermaid", "mmd"]``
Entry = collections.namedtuple("Entry", "name scope tokens")

_EMBED_RE = re.compile(r'^\s*embed:\s*scope:([\w.+#-]+)\s*$', re.M)
_EXT_BLOCK_RE = re.compile(r'^file_extensions:\s*$\n((?:^\s+-\s*\S+\s*$\n?)+)', re.M)
_EXT_FLOW_RE = re.compile(r'^file_extensions:\s*\[([^\]]*)\]', re.M)
_TOKEN_RE = re.compile(r'^[a-z0-9][a-z0-9+#._-]*$')


def embedded_scopes(syntax_text):
    """Scopes a syntax already embeds, i.e. the languages it needs no help with.

    :param str syntax_text: contents of a ``.sublime-syntax`` file
    :returns: every scope named by an ``embed: scope:...`` line
    :rtype: set
    """
    return set(_EMBED_RE.findall(syntax_text or ""))


def extensions_of(syntax_text):
    """File extensions declared by a syntax, in declaration order.

    These double as fence aliases: the extension is what people type in an info
    string (```` ```rb ````, ```` ```mmd ````) far more often than the syntax's name.

    :param str syntax_text: contents of a ``.sublime-syntax`` file
    :returns: extensions without dots, e.g. ``["mmd", "mermaid"]``
    :rtype: list
    """
    m = _EXT_BLOCK_RE.search(syntax_text or "")
    if m:
        return [line.strip().lstrip("-").strip().strip('"\'')
                for line in m.group(1).splitlines() if line.strip()]
    m = _EXT_FLOW_RE.search(syntax_text or "")
    if m:
        return [p.strip().strip('"\'') for p in m.group(1).split(",") if p.strip()]
    return []


def extends_of(syntax_text):
    """The syntax a file inherits from, or None.

    :param str syntax_text: contents of a ``.sublime-syntax`` file
    :returns: the raw ``extends`` value, which may be a bare filename
    :rtype: str or None
    """
    m = re.search(r'^extends:\s*(\S.*?)\s*$', syntax_text or "", re.M)
    return m.group(1).strip('"\'') if m else None


def resolve_resource(base, value):
    """Resolve an ``extends`` value against the file that declared it.

    Sublime allows a bare filename there, resolved inside the same package (that is
    how MultiMarkdown refers to ``Markdown.sublime-syntax``).

    :param str base: resource path of the declaring file
    :param str value: the ``extends`` value
    :returns: a full ``Packages/...`` resource path
    :rtype: str
    """
    if value.startswith("Packages/"):
        return value
    return base.rsplit("/", 1)[0] + "/" + value


def covered_scopes(load, path, limit=8):
    """Every scope embedded by ``path`` or anything it inherits from.

    The chain matters: a syntax that extends Markdown (MultiMarkdown, or this
    generator's own output) embeds nothing itself, so stopping at the first file
    would report every language as uncovered and duplicate the parent's rules.

    :param load: callable taking a resource path and returning its text
    :param str path: resource path of the parent syntax
    :param int limit: chain depth guard, in case of a cycle
    :returns: union of the embedded scopes along the chain
    :rtype: set
    """
    scopes, seen = set(), set()
    while path and path not in seen and len(seen) < limit:
        seen.add(path)
        try:
            text = load(path)
        except Exception:
            break
        scopes |= embedded_scopes(text)
        nxt = extends_of(text)
        path = resolve_resource(path, nxt) if nxt else None
    return scopes


def is_covered(scope, covered):
    """Does the parent syntax already handle ``scope``, directly or by relation?

    Relation matters in both directions, and each caught a real bug:

    * ``source.css.mermaid`` (a helper the Mermaid package embeds into its own
      diagrams) is a specialization of the covered ``source.css``. Emitting it would
      hand the ``mermaid`` info string to a CSS fragment.
    * ``source.shell.bash`` looks uncovered, because the parent embeds the tailored
      ``source.shell.bash.embedded.markdown`` instead. Emitting it would prepend a
      cruder rule in front of the parent's.

    :param str scope: candidate scope
    :param covered: scopes the parent already embeds
    :returns: True when the candidate should be left to the parent
    :rtype: bool
    """
    for c in covered:
        if scope == c or scope.startswith(c + ".") or c.startswith(scope + "."):
            return True
    return False


def drop_specializations(entries):
    """Remove entries whose scope refines another entry's scope.

    ``source.mermaid.flowchart`` and ``source.mermaid.sequence`` are pieces of the
    Mermaid package's own machinery, not languages anyone opens a fence with; keeping
    them would also claim the ``flowchart`` and ``sequence`` info strings.

    :param entries: :class:`Entry` values
    :returns: entries with no scope that extends another entry's scope
    :rtype: list
    """
    scopes = [e.scope for e in entries]
    return [e for e in entries
            if not any(e.scope.startswith(s + ".") for s in scopes if s != e.scope)]


def fence_tokens(name, scope, extensions):
    """Info strings that should open a language, most specific first.

    Drawn from the scope's last component (``source.mermaid`` -> ``mermaid``), the
    syntax name when it reads like an info string (single word, so "Bourne Again
    Shell (bash)" is skipped), and the declared extensions.

    :param str name: syntax display name
    :param str scope: syntax scope
    :param extensions: declared file extensions
    :returns: lowercase tokens, deduped, scope-derived first
    :rtype: list
    """
    candidates = []
    tail = (scope or "").split(".")[-1]
    if tail:
        candidates.append(tail)
    lowered = (name or "").strip().lower()
    if " " not in lowered:
        candidates.append(lowered)
    candidates.extend(e.lower().lstrip(".") for e in extensions or [])

    tokens = []
    for c in candidates:
        if _TOKEN_RE.match(c) and c not in tokens:
            tokens.append(c)
    return tokens


def assign_tokens(entries):
    """Give every info string a single owner, earlier entries winning.

    Two syntaxes claiming ```` ```json ```` would make the fence ambiguous, so a token
    is handed to the first entry that asks for it. An entry left with nothing is
    dropped: it has no info string anyone would type.

    :param entries: :class:`Entry` values, in priority order
    :returns: entries with disjoint token lists, empty ones removed
    :rtype: list
    """
    taken, out = set(), []
    for e in entries:
        tokens = [t for t in e.tokens if t not in taken]
        if not tokens:
            continue
        taken.update(tokens)
        out.append(e._replace(tokens=tokens))
    return out


_HEADER = """\
%YAML 1.2
---
# GENERATED by MarkdownRich. Edits are overwritten; run
# "MarkdownRich: Rebuild Markdown syntax" after installing a new language.
#
# Sublime's Markdown syntax embeds a fixed list of languages, so fenced blocks for
# anything else stay unhighlighted. This inherits all of it and prepends one fence
# rule per installed syntax the parent doesn't already cover.
name: {name}
# `scope` is required even when extending: it is not inherited. Keeping the parent's
# scope as the prefix means every `text.html.markdown` selector (this plugin's, and
# the color scheme's markdown rules) still matches.
scope: {scope}
# `version: 2` is what makes `extends` and `meta_prepend` legal; without it Sublime
# rejects the file and the syntax never shows up in the menu.
version: 2
extends: {parent}
"""

_CONTEXT = """\

  fenced-{slug}:
    # {name}
    - match: |-
        (?x)
        {{{{fenced_code_block_start}}}}
        (?i:\\s*({alternation}))
        {{{{fenced_code_block_trailing_infostring_characters}}}}
      captures:
        0: meta.code-fence.definition.begin.markdown-gfm
        2: punctuation.definition.raw.code-fence.begin.markdown
        5: constant.other.language-name.markdown
        6: comment.line.infostring.markdown
        7: meta.fold.code-fence.begin.markdown
      embed: scope:{scope}
      embed_scope:
        meta.code-fence.body.markdown-gfm
        markup.raw.code-fence.{slug}.markdown-gfm
        {scope}
      escape: '{{{{fenced_code_block_escape}}}}'
      escape_captures:
        0: meta.code-fence.definition.end.markdown-gfm
        1: punctuation.definition.raw.code-fence.end.markdown
        2: meta.fold.code-fence.end.markdown
"""


def _slug(entry):
    """Context-name-safe id for a language, from its first token."""
    return re.sub(r'[^a-z0-9]+', '-', entry.tokens[0]).strip("-") or "lang"


def render_syntax(entries, parent, name, scope):
    """Render the whole ``.sublime-syntax`` file.

    The generated rules mirror the parent's own fence rules (same variables, same
    capture numbering, same ``embed_scope`` shape), so code folding, the infostring
    scope and the language-name scope keep behaving as they do for built-in languages.

    :param entries: :class:`Entry` values to support
    :param str parent: resource path of the syntax to extend
    :param str name: display name for the generated syntax
    :param str scope: scope for the generated syntax, extending the parent's own
    :returns: file contents
    :rtype: str
    """
    out = [_HEADER.format(name=name, parent=parent, scope=scope)]
    if entries:
        slugs = [_slug(e) for e in entries]
        out.append("\ncontexts:\n  fenced-syntaxes:\n    - meta_prepend: true\n")
        out.extend("    - include: fenced-%s\n" % s for s in slugs)
        for entry, slug in zip(entries, slugs):
            out.append(_CONTEXT.format(
                slug=slug, name=entry.name, scope=entry.scope,
                alternation="|".join(re.escape(t) for t in entry.tokens),
            ))
    return "".join(out)
