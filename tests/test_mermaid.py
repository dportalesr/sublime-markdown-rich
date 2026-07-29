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
                     luminance, auto_theme, with_init_directive, block_at, width_budget,
                     merged_config, node_padding_config, line_height_css)


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


# --- block_at: which block is the cursor in --------------------------------

def test_block_at_finds_the_enclosing_block():
    text = _doc("intro", "```mermaid", "graph TD; A-->B;", "```", "outro")
    b = find_blocks(text)[0]
    assert block_at([b], b.start) is b
    assert block_at([b], b.body_start + 2) is b
    assert block_at([b], b.end) is b


def test_block_at_outside_any_block():
    text = _doc("intro", "```mermaid", "graph TD; A-->B;", "```", "outro")
    blocks = find_blocks(text)
    assert block_at(blocks, 0) is None
    assert block_at(blocks, len(text) - 1) is None


def test_block_at_picks_the_right_one_of_several():
    text = _doc("```mermaid", "A", "```", "x", "```mermaid", "B", "```")
    first, second = find_blocks(text)
    assert block_at([first, second], second.body_start + 1) is second


def test_block_at_with_no_blocks():
    assert block_at([], 5) is None


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
    assert cache_key("graph TD; A-->B;", theme="default", background="transparent", scale=2,
                     config={"flowchart": {"padding": 20}}) != base


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


def test_mmdc_args_config_and_css_optional():
    bare = mmdc_args("mmdc", "i", "o", theme="default", background="white", scale=1)
    assert "-c" not in bare and "-C" not in bare
    full = mmdc_args("mmdc", "i", "o", theme="default", background="white", scale=1,
                     config_path="/tmp/cfg.json", css_path="/tmp/style.css")
    assert full[full.index("-c") + 1] == "/tmp/cfg.json"
    assert full[full.index("-C") + 1] == "/tmp/style.css"


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


# --- width_budget: how much of the view a diagram may take -------------------

def test_width_budget_fraction_of_the_view():
    # the inline diagram is a preview; "Open image" is there for the full thing
    assert width_budget(0.66, 1000) == 660


def test_width_budget_zero_means_the_whole_view():
    assert width_budget(0, 1000) == 1000


def test_width_budget_absolute_pixels():
    assert width_budget(800, 1000) == 800


def test_width_budget_never_exceeds_the_view():
    # a phantom wider than the view is clipped, not scrollable
    assert width_budget(2000, 1000) == 1000


def test_width_budget_without_a_usable_view():
    assert width_budget(0.66, 0) == 528   # falls back to an 800px view


# --- node_padding_config: one padding setting, every diagram type ------------

def test_node_padding_config_covers_the_types_that_support_it():
    out = node_padding_config(16)
    assert out["flowchart"]["padding"] == 16
    assert out["sequence"]["wrapPadding"] == 16      # participant boxes
    assert out["class"]["padding"] == 16
    assert out["mindmap"]["padding"] == 16
    assert out["block"]["padding"] == 16
    assert out["timeline"]["padding"] == 16
    assert out["c4"]["c4ShapePadding"] == 16


def test_node_padding_config_omits_types_that_ignore_it():
    # verified by rendering: these ignore every padding key they declare
    out = node_padding_config(16)
    for absent in ("state", "requirement", "kanban", "architecture", "packet", "journey"):
        assert absent not in out


def test_node_padding_config_disabled():
    assert node_padding_config(0) == {}
    assert node_padding_config(None) == {}


def test_node_padding_config_merges_as_a_base():
    # a user setting for one type must not lose the padding for the others
    out = merged_config({"flowchart": {"nodeSpacing": 60}}, node_padding_config(16))
    assert out["flowchart"] == {"padding": 16, "nodeSpacing": 60}
    assert out["sequence"]["wrapPadding"] == 16


def test_node_padding_config_user_can_override_one_type():
    out = merged_config({"flowchart": {"padding": 2}}, node_padding_config(16))
    assert out["flowchart"]["padding"] == 2
    assert out["class"]["padding"] == 16


# --- line_height_css: spacing between wrapped label lines --------------------

def test_line_height_css_targets_html_labels():
    css = line_height_css(1.5)
    assert "line-height: 1.5" in css
    assert "foreignObject" in css      # where mermaid puts HTML labels


def test_line_height_css_disabled():
    assert line_height_css(0) == ""
    assert line_height_css(1) == ""     # 1 is mermaid's own spacing


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

def test_with_init_directive_prepends_the_theme():
    out = with_init_directive("graph TD; A-->B;", "dark", None)
    assert out.startswith('%%{init: {"theme": "dark"}}%%\n')
    assert out.endswith("graph TD; A-->B;")


def test_with_init_directive_carries_the_config():
    out = with_init_directive("graph TD; A-->B;", "dark", {"flowchart": {"padding": 20}})
    head = out.split("\n")[0]
    assert head.startswith("%%{init: ") and head.endswith("}%%")
    assert '"padding": 20' in head and '"theme": "dark"' in head


def test_with_init_directive_config_without_a_theme():
    out = with_init_directive("graph TD; A-->B;", "default", {"fontSize": 18})
    assert '"fontSize": 18' in out
    assert "theme" not in out.split("\n")[0]


def test_with_init_directive_respects_an_existing_init():
    # a diagram that configures itself wins: the author was specific on purpose
    src = '%%{init: {"theme": "forest"}}%%\ngraph TD; A-->B;'
    assert with_init_directive(src, "dark", {"fontSize": 18}) == src


def test_with_init_directive_nothing_to_add():
    src = "graph TD; A-->B;"
    assert with_init_directive(src, "default", None) == src
    assert with_init_directive(src, "default", {}) == src


def test_with_init_directive_is_deterministic():
    # the directive rides in the cache key, so key order must not wobble
    config = {"z": 1, "a": {"n": 2, "m": 3}}
    first = with_init_directive("graph TD;", "dark", config)
    assert first == with_init_directive("graph TD;", "dark", dict(reversed(list(config.items()))))


# --- merged_config: per-diagram-type defaults, user settings on top ----------

def test_merged_config_is_empty_without_user_settings():
    assert merged_config(None) == {}
    assert merged_config({}) == {}


def test_merged_config_passes_user_settings_through():
    assert merged_config({"flowchart": {"padding": 20}}) == {"flowchart": {"padding": 20}}


def test_merged_config_merges_one_level_deep():
    out = merged_config({"flowchart": {"padding": 20}}, {"flowchart": {"nodeSpacing": 60}})
    assert out["flowchart"] == {"nodeSpacing": 60, "padding": 20}


def test_merged_config_user_wins_over_defaults():
    out = merged_config({"flowchart": {"padding": 20}}, {"flowchart": {"padding": 4}})
    assert out["flowchart"]["padding"] == 20


def test_merged_config_leaves_defaults_untouched():
    defaults = {"flowchart": {"padding": 4}}
    merged_config({"flowchart": {"padding": 20}}, defaults)
    assert defaults == {"flowchart": {"padding": 4}}


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
