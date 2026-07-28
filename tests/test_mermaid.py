"""Specs for mermaid block scanning / render plumbing (pure, no `sublime` needed).

Run directly:  python3 tests/test_mermaid.py
Or via pytest: pytest tests/test_mermaid.py
"""

import base64
import os
import sys
import zlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mermaid import (find_blocks, cache_key, remote_url, mmdc_args, display_size,
                     luminance, auto_theme, with_theme_directive)


def _doc(*lines):
    return "\n".join(lines)


# --- find_blocks: locate ```mermaid fences ----------------------------------

def test_find_blocks_basic():
    text = _doc("intro", "```mermaid", "graph TD;", "  A-->B;", "```", "outro")
    blocks = find_blocks(text)
    assert len(blocks) == 1
    b = blocks[0]
    assert b.source == "graph TD;\n  A-->B;"
    # whole block spans the opening fence through the closing fence
    assert text[b.start:b.end] == "```mermaid\ngraph TD;\n  A-->B;\n```"
    # the fold region starts after the opening fence line, so it stays visible
    assert text[b.body_start:b.end] == "\ngraph TD;\n  A-->B;\n```"


def test_find_blocks_tilde_fence():
    text = _doc("~~~mermaid", "graph LR; A-->B;", "~~~")
    assert [b.source for b in find_blocks(text)] == ["graph LR; A-->B;"]


def test_find_blocks_indented_and_case_insensitive():
    text = _doc("  ```Mermaid", "  graph TD; A-->B;", "  ```")
    blocks = find_blocks(text)
    assert len(blocks) == 1
    assert blocks[0].source == "  graph TD; A-->B;"


def test_find_blocks_info_string_with_attributes():
    text = _doc('```mermaid title="Flow"', "graph TD; A-->B;", "```")
    assert len(find_blocks(text)) == 1


def test_find_blocks_ignores_other_languages():
    text = _doc("```ruby", "puts 'mermaid'", "```")
    assert find_blocks(text) == []


def test_find_blocks_ignores_nested_fence_inside_outer_block():
    # a ````markdown sample containing a mermaid fence is sample text, not a diagram
    text = _doc("````markdown", "```mermaid", "graph TD; A-->B;", "```", "````")
    assert find_blocks(text) == []


def test_find_blocks_ignores_unterminated_fence():
    # half-typed block: nothing to render yet
    text = _doc("```mermaid", "graph TD; A-->B;")
    assert find_blocks(text) == []


def test_find_blocks_requires_matching_fence_length():
    # a 3-backtick run does not close a 4-backtick mermaid fence
    text = _doc("````mermaid", "graph TD; A-->B;", "```", "````")
    blocks = find_blocks(text)
    assert len(blocks) == 1
    assert blocks[0].source == "graph TD; A-->B;\n```"


def test_find_blocks_multiple_in_document_order():
    text = _doc("```mermaid", "A", "```", "prose", "```mermaid", "B", "```")
    blocks = find_blocks(text)
    assert [b.source for b in blocks] == ["A", "B"]
    assert blocks[0].start < blocks[1].start


def test_find_blocks_empty_body():
    text = _doc("```mermaid", "```")
    blocks = find_blocks(text)
    assert len(blocks) == 1
    assert blocks[0].source == ""


# --- cache_key: content-addressed render cache ------------------------------

def test_cache_key_is_stable():
    a = cache_key("graph TD; A-->B;", theme="default", background="transparent", scale=2)
    b = cache_key("graph TD; A-->B;", theme="default", background="transparent", scale=2)
    assert a == b
    assert a.isalnum()


def test_cache_key_varies_with_source_and_options():
    base = cache_key("graph TD; A-->B;", theme="default", background="transparent", scale=2)
    assert cache_key("graph TD; A-->C;", theme="default", background="transparent", scale=2) != base
    assert cache_key("graph TD; A-->B;", theme="dark", background="transparent", scale=2) != base
    assert cache_key("graph TD; A-->B;", theme="default", background="white", scale=2) != base
    assert cache_key("graph TD; A-->B;", theme="default", background="transparent", scale=3) != base


# --- remote_url: kroki-style deflate + base64url GET ------------------------

def test_remote_url_round_trips_the_source():
    src = "graph TD;\n  A-->B;"
    url = remote_url("https://kroki.io", src)
    assert url.startswith("https://kroki.io/mermaid/png/")
    payload = url.rsplit("/", 1)[-1]
    assert zlib.decompress(base64.urlsafe_b64decode(payload)).decode("utf-8") == src


def test_remote_url_strips_trailing_slash_from_endpoint():
    assert remote_url("https://kroki.io/", "graph TD; A-->B;").startswith("https://kroki.io/mermaid/png/")


def test_remote_url_payload_is_url_safe():
    url = remote_url("https://kroki.io", "graph TD;\n" + "  A-->B;\n" * 40)
    payload = url.rsplit("/", 1)[-1]
    assert "+" not in payload and "/" not in payload


# --- mmdc_args: local mermaid-cli invocation --------------------------------

def test_mmdc_args_shape():
    args = mmdc_args("/usr/local/bin/mmdc", "/tmp/in.mmd", "/tmp/out.png",
                     theme="dark", background="transparent", scale=2)
    assert args[0] == "/usr/local/bin/mmdc"
    assert args[args.index("-i") + 1] == "/tmp/in.mmd"
    assert args[args.index("-o") + 1] == "/tmp/out.png"
    assert args[args.index("-t") + 1] == "dark"
    assert args[args.index("-b") + 1] == "transparent"
    assert args[args.index("-s") + 1] == "2"


def test_mmdc_args_width_optional():
    without = mmdc_args("mmdc", "i", "o", theme="default", background="white", scale=1)
    assert "-w" not in without
    with_w = mmdc_args("mmdc", "i", "o", theme="default", background="white", scale=1, width=900)
    assert with_w[with_w.index("-w") + 1] == "900"


# --- display_size: undo the render scale, then cap --------------------------

def test_display_size_divides_by_render_scale():
    # rendered at 2x for crispness -> shown at logical size
    assert display_size((400, 300), scale=2, max_width=800) == (200, 150)


def test_display_size_caps_width_keeping_ratio():
    assert display_size((2000, 1000), scale=2, max_width=400) == (400, 200)


def test_display_size_never_upscales():
    assert display_size((100, 50), scale=1, max_width=800) == (100, 50)


def test_display_size_unknown_natural():
    assert display_size(None, scale=2, max_width=800) == (None, None)


# --- display_size: a floor, so wide-and-short diagrams stay legible ---------

def test_display_size_grows_a_short_diagram_to_the_minimum_height():
    # an LR flowchart is wide and shallow: shrinking it to the width budget leaves
    # text too small to read, so scale it back up until it reaches the floor
    assert display_size((1200, 300), scale=2, max_width=1000, min_height=200) == (800, 200)


def test_display_size_minimum_height_yields_to_the_width_budget():
    # growing to the floor must never push the diagram past the width it has
    assert display_size((1200, 300), scale=2, max_width=700, min_height=400) == (700, 175)


def test_display_size_never_upscales_past_the_rendered_pixels():
    # beyond the pixels actually rendered it is just blur
    assert display_size((400, 100), scale=2, max_width=2000, min_height=400) == (400, 100)


def test_display_size_leaves_tall_diagrams_alone():
    assert display_size((400, 1200), scale=2, max_width=800, min_height=200) == (200, 600)


def test_display_size_minimum_height_off_by_default():
    assert display_size((1200, 300), scale=2, max_width=1000) == (600, 150)


# --- theme selection: match the diagram to the color scheme -----------------

def test_luminance_extremes():
    assert luminance("#000000") == 0.0
    assert luminance("#ffffff") == 1.0


def test_luminance_short_and_alpha_forms():
    assert luminance("#fff") == 1.0
    assert luminance("#000000ff") == 0.0


def test_luminance_weights_green_highest():
    assert luminance("#00ff00") > luminance("#ff0000") > luminance("#0000ff")


def test_luminance_unparseable():
    assert luminance("rebeccapurple") is None
    assert luminance("") is None
    assert luminance(None) is None


def test_auto_theme_follows_background():
    assert auto_theme("#1e2a35") == "dark"      # dark editor -> light-on-dark diagram
    assert auto_theme("#fdf6e3") == "default"   # light editor -> dark-on-light diagram
    assert auto_theme("nonsense") == "default"  # unknown -> mermaid's own default


# --- with_theme_directive: theme a kroki render, which has no -t flag --------

def test_with_theme_directive_prepends_init():
    out = with_theme_directive("graph TD; A-->B;", "dark")
    assert out.startswith('%%{init: {"theme": "dark"}}%%\n')
    assert out.endswith("graph TD; A-->B;")


def test_with_theme_directive_respects_an_existing_init():
    src = '%%{init: {"theme": "forest"}}%%\ngraph TD; A-->B;'
    assert with_theme_directive(src, "dark") == src


def test_with_theme_directive_default_theme_is_left_alone():
    src = "graph TD; A-->B;"
    assert with_theme_directive(src, "default") == src


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
