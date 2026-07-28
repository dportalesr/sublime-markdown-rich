"""Specs for generating the MarkdownRich syntax (pure, no `sublime` needed).

Run directly:  python3 tests/test_markdown_syntax.py
Or via pytest: pytest tests/test_markdown_syntax.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from markdown_syntax import (embedded_scopes, extensions_of, fence_tokens,
                             assign_tokens, render_syntax, Entry,
                             is_covered, drop_specializations,
                             extends_of, resolve_resource, covered_scopes)


PARENT = """\
  fenced-ruby:
    - match: ...
      embed: scope:source.ruby
      escape: '{{fenced_code_block_escape}}'
  fenced-yaml:
    - match: ...
      embed: scope:source.yaml
  frontmatter:
      embed: scope:source.json
"""


# --- embedded_scopes: what the parent syntax already covers ------------------

def test_embedded_scopes_collects_every_embed():
    assert embedded_scopes(PARENT) == {"source.ruby", "source.yaml", "source.json"}


def test_embedded_scopes_of_empty_text():
    assert embedded_scopes("") == set()


# --- extensions_of: fence aliases taken from a syntax's own extensions -------

def test_extensions_of_block_form():
    text = "name: Mermaid\nfile_extensions:\n  - mmd\n  - mermaid\nscope: source.mermaid\n"
    assert extensions_of(text) == ["mmd", "mermaid"]


def test_extensions_of_flow_form():
    assert extensions_of("file_extensions: [mmd, mermaid]\n") == ["mmd", "mermaid"]


def test_extensions_of_missing():
    assert extensions_of("name: Whatever\n") == []


# --- extends chain: what a derived parent really covers ----------------------

def test_extends_of_reads_the_declaration():
    assert extends_of("name: X\nextends: Markdown.sublime-syntax\n") == "Markdown.sublime-syntax"


def test_extends_of_absent():
    assert extends_of("name: X\n") is None


def test_resolve_resource_relative_to_the_declaring_package():
    assert resolve_resource("Packages/Markdown/MultiMarkdown.sublime-syntax",
                            "Markdown.sublime-syntax") == "Packages/Markdown/Markdown.sublime-syntax"


def test_resolve_resource_leaves_absolute_paths_alone():
    assert resolve_resource("Packages/A/B.sublime-syntax",
                            "Packages/C/D.sublime-syntax") == "Packages/C/D.sublime-syntax"


CHAIN = {
    "Packages/Markdown/MultiMarkdown.sublime-syntax":
        "name: MultiMarkdown\nextends: Markdown.sublime-syntax\n",
    "Packages/Markdown/Markdown.sublime-syntax":
        "name: Markdown\n      embed: scope:source.ruby\n      embed: scope:source.css\n",
}


def test_covered_scopes_follows_the_extends_chain():
    # MultiMarkdown embeds nothing itself; its coverage is Markdown's
    assert covered_scopes(CHAIN.__getitem__,
                          "Packages/Markdown/MultiMarkdown.sublime-syntax") == {"source.ruby", "source.css"}


def test_covered_scopes_survives_a_cycle():
    loop = {"a": "extends: b\n      embed: scope:source.x\n", "b": "extends: a\n"}
    assert covered_scopes(lambda p: loop[p.rsplit("/", 1)[-1]], "a") == {"source.x"}


def test_covered_scopes_survives_a_missing_resource():
    assert covered_scopes(CHAIN.__getitem__, "Packages/Nope/Gone.sublime-syntax") == set()


# --- is_covered: leave the parent's own languages alone ----------------------

COVERED = {"source.css", "source.shell.bash.embedded.markdown", "source.ruby"}


def test_is_covered_exact_match():
    assert is_covered("source.ruby", COVERED)


def test_is_covered_specialization_of_a_covered_scope():
    # source.css.mermaid is a CSS fragment, not the mermaid language
    assert is_covered("source.css.mermaid", COVERED)


def test_is_covered_when_the_parent_embeds_a_tailored_variant():
    # the parent embeds source.shell.bash.embedded.markdown, so bash is handled
    assert is_covered("source.shell.bash", COVERED)


def test_is_covered_leaves_unrelated_scopes_alone():
    assert not is_covered("source.mermaid", COVERED)
    assert not is_covered("source.rubyish", COVERED)


# --- drop_specializations: sub-syntaxes aren't fence languages ---------------

def test_drop_specializations_removes_refinements():
    entries = [
        Entry(name="Mermaid", scope="source.mermaid", tokens=["mermaid"]),
        Entry(name="Mermaid Flowchart", scope="source.mermaid.flowchart", tokens=["flowchart"]),
        Entry(name="D2", scope="source.d2", tokens=["d2"]),
    ]
    assert [e.scope for e in drop_specializations(entries)] == ["source.mermaid", "source.d2"]


def test_drop_specializations_keeps_siblings():
    entries = [
        Entry(name="A", scope="source.a", tokens=["a"]),
        Entry(name="B", scope="source.b", tokens=["b"]),
    ]
    assert len(drop_specializations(entries)) == 2


# --- fence_tokens: what info strings should open this language --------------

def test_fence_tokens_from_scope_name_and_extensions():
    assert fence_tokens("Mermaid", "source.mermaid", ["mmd", "mermaid"]) == ["mermaid", "mmd"]


def test_fence_tokens_lowercases_and_dedupes():
    assert fence_tokens("D2", "source.d2", ["d2"]) == ["d2"]


def test_fence_tokens_skips_multiword_names():
    # "Bourne Again Shell (bash)" is a title, not an info string
    assert fence_tokens("Bourne Again Shell (bash)", "source.shell.bash", ["bash"]) == ["bash"]


def test_fence_tokens_ignores_intermediate_scope_components():
    # only the tail counts: `text.html.vue` must not claim ```html and shadow the
    # parent's own html fence, since generated rules are prepended
    assert fence_tokens("Vue", "text.html.vue", ["vue"]) == ["vue"]


def test_fence_tokens_keeps_symbol_names():
    assert "c++" in fence_tokens("C++", "source.c++", [])


# --- assign_tokens: one owner per info string -------------------------------

def test_assign_tokens_resolves_collisions_first_wins():
    a = Entry(name="Mermaid", scope="source.mermaid", tokens=["mermaid", "mmd"])
    b = Entry(name="Mermaid Alt", scope="source.mermaid-alt", tokens=["mermaid", "malt"])
    out = assign_tokens([a, b])
    assert out[0].tokens == ["mermaid", "mmd"]
    assert out[1].tokens == ["malt"]


def test_assign_tokens_drops_entries_left_without_tokens():
    a = Entry(name="One", scope="source.one", tokens=["x"])
    b = Entry(name="Two", scope="source.two", tokens=["x"])
    out = assign_tokens([a, b])
    assert [e.scope for e in out] == ["source.one"]


# --- render_syntax: the generated .sublime-syntax ---------------------------

ENTRIES = [Entry(name="Mermaid", scope="source.mermaid", tokens=["mermaid", "mmd"])]
RENDERED = render_syntax(ENTRIES, "Packages/Markdown/Markdown.sublime-syntax", "MarkdownRich",
                         "text.html.markdown.rich")


def test_render_syntax_header():
    assert RENDERED.startswith("%YAML 1.2\n---\n")
    assert "name: MarkdownRich" in RENDERED
    assert "extends: Packages/Markdown/Markdown.sublime-syntax" in RENDERED


def test_render_syntax_declares_its_own_scope():
    # `scope` is required even with `extends`, and the parent's scope must stay the
    # prefix so text.html.markdown selectors keep matching
    assert "\nscope: text.html.markdown.rich\n" in RENDERED


def test_render_syntax_declares_version_2():
    # `extends` and `meta_prepend` are version-2 features; without the declaration
    # Sublime refuses the file and the syntax never appears in the menu
    assert "\nversion: 2\n" in RENDERED


def test_render_syntax_prepends_into_the_parent_list():
    # the parent's own languages must stay reachable, so this appends to its context
    assert "  fenced-syntaxes:\n    - meta_prepend: true\n" in RENDERED
    assert "    - include: fenced-mermaid\n" in RENDERED


def test_render_syntax_emits_a_context_per_language():
    assert "  fenced-mermaid:\n" in RENDERED
    assert "(?i:\\s*(mermaid|mmd))" in RENDERED
    assert "embed: scope:source.mermaid" in RENDERED
    assert "escape: '{{fenced_code_block_escape}}'" in RENDERED


def test_render_syntax_reuses_parent_variables_and_captures():
    # matching the parent's shape is what keeps folding and infostring scopes working
    assert "{{fenced_code_block_start}}" in RENDERED
    assert "{{fenced_code_block_trailing_infostring_characters}}" in RENDERED
    assert "5: constant.other.language-name.markdown" in RENDERED
    assert "markup.raw.code-fence.mermaid.markdown-gfm" in RENDERED


def test_render_syntax_escapes_regex_specials_in_tokens():
    out = render_syntax([Entry(name="C++", scope="source.c++", tokens=["c++"])], "P", "N", "S")
    assert r"(?i:\s*(c\+\+))" in out


def test_render_syntax_is_stable():
    assert render_syntax(ENTRIES, "Packages/Markdown/Markdown.sublime-syntax", "MarkdownRich",
                         "text.html.markdown.rich") == RENDERED


def test_render_syntax_with_no_entries_still_valid():
    out = render_syntax([], "P", "N", "S")
    assert "fenced-syntaxes" not in out   # nothing to prepend, so no empty context
    assert "extends: P" in out


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
