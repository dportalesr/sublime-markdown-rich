# How it works

Implementation notes for MarkdownRich. The [README](../README.md) covers what the plugin does; this covers how, and why the awkward parts are the way they are.

## Layout

Logic that doesn't need a running editor lives in modules that import no `sublime`, so it runs under plain Python and is unit-tested:

| Module               | Responsibility                                                               |
|----------------------|------------------------------------------------------------------------------|
| `section_ref.py`     | Parsing `§N` references, matching them against numbered headings             |
| `mermaid.py`         | Finding mermaid fences, cache keys, render invocations, theme choice, sizing |
| `markdown_syntax.py` | Which languages need a fence rule, and what the generated syntax looks like  |
| `markdown_rich.py`   | Everything that touches a view: phantoms, folds, regions, threads, commands  |

## Images

A per-view `PhantomManager` finds image regions with `view.find_all(...)`, resolves each source path or URL, and renders one `sublime.Phantom` per image at the end of its source line.

Non-image states (loading, missing, fetch-failed) render as `add_regions(annotations=...)` rather than phantoms, so they don't reflow the buffer the way a block phantom does.

Remote fetches run on a background `threading.Thread` and re-enter the main thread through `sublime.set_timeout(..., 0)` to re-render. Image dimensions are probed by reading the file header, with no PIL dependency: PNG `IHDR`, GIF logical screen descriptor, JPEG `SOFn` markers.

## Path resolution

Relative paths in `[text](path)` and `![alt](path)` are resolved against, in order:

1. **Project root**, `window.folders()[0]`, which covers `.sublime-project` files and ad-hoc folder windows alike
2. **The current file's directory**, when the project root doesn't carry the target or the window has no folders

The first candidate that exists on disk wins; if none do, the project-root candidate is reported as the missing path. That's what lets `[design](docs/design.md)` resolve from anywhere in the project.

A trailing `:line` or `:line:col` is stripped before resolution and reapplied as the caret position (`ENCODED_POSITION`). Only a trailing digit group counts, so the colon in `file://` is never mistaken for a line number. Absolute paths, `~` and `file:///` URLs are all accepted.

## Side-by-side reveal

Opening a link used to split the window into two columns, which permanently changed the user's layout. Instead the opened view is moved into the origin's group and `window.select_sheets([origin, opened])` lets Sublime tile the two sheets. The layout is untouched. `select_sheets` needs build 4050+; older builds get a plain tab.

## Section references

Sublime has no syntax injection, so a `§3.1` in prose carries no scope a keymap could match. Two consequences:

- **Styling** is done by the plugin: `add_regions(..., "markup.underline.link", ...)` with underline draw flags, refreshed on load, activation, save, and (debounced) while typing. References inside code spans and fences are skipped.
- **The keymap** is gated on an `on_query_context` key (`markdown_rich_section_ref`) instead of a selector, so `ctrl+enter` passes through everywhere else.

Resolution is an exact match on a heading's leading number, read from `markup.heading` regions rather than a text scan, so `##`-looking lines inside fenced code aren't matched. Duplicates resolve to the first occurrence.

Hovering shows the target heading in a popup whose link and the `ctrl+enter` path share one helper, so mouse and keyboard land identically.

## Mermaid diagrams

See [mermaid.md](mermaid.md) for the full picture: backends, theming and caching. The editor-side mechanics:

- Blocks are found by walking fences in sequence, so a mermaid fence nested inside a longer outer fence is content, not a diagram.
- The source is hidden with `view.fold(body_start..end)` and the phantom is anchored on the still-visible fence line, so the fold doesn't swallow it.
- Render state is keyed by the block's content hash rather than its position, so toggling one block to source survives edits elsewhere, and an edited diagram re-renders on its own.
- A block whose body holds the caret shows its source, since a folded region can't be typed into. Asking for the diagram while editing parks the caret on the fence line first, or the next render would immediately unfold the block again.

## Fenced code highlighting

Sublime's Markdown syntax embeds a fixed list of languages, one hand-written rule each, and never consults a fence's info string against installed syntaxes. A fence tagged with anything outside that list stays plain `markup.raw`, no matter what you install.

The only way in is another syntax that `extends` Markdown, which would just move the hardcoded list one level down. So the plugin generates it instead:

- `extends` the parent Markdown syntax and `meta_prepend`s into its `fenced-syntaxes` context, which is a plain list of includes
- takes its entries from `sublime.list_syntaxes()` at load time, so installing a language package is all it takes
- writes to `Packages/User/MarkdownRich.sublime-syntax`, and only when the contents change, since rewriting makes Sublime reload the syntax

Generated rules copy the parent's variables, capture numbering and `embed_scope`, so folding, the infostring scope and the language-name scope behave as they do for built-in languages.

**Which languages get a rule** is the fiddly part, and each exclusion below came from a real misfire:

- Coverage is read along the whole `extends` chain. MultiMarkdown embeds nothing itself, so stopping at the first file would report all 44 of Markdown's languages as uncovered and duplicate them.
- A scope that refines a covered one is skipped: `source.css.mermaid` is a CSS fragment inside the Mermaid package, and its scope tail would have claimed the `mermaid` fence.
- A scope that a covered one refines is skipped too: the parent embeds the tailored `source.shell.bash.embedded.markdown`, so a plain `source.shell.bash` rule would be prepended in front of it.
- Sub-syntaxes are dropped: `source.mermaid.flowchart` would otherwise own the `flowchart` info string.

**Info strings** come from the scope's last component, the syntax name when it reads like one (single word), and the declared file extensions, which is where aliases like `mmd` and `rb` come from. Only the scope tail is used, never intermediate components, or `text.html.markdown.vue` would claim `html` and shadow the parent. Each token has exactly one owner: the first entry to claim it keeps it.

Three keys are required in the generated file and none are inherited through `extends`: `name`, `scope` and `version: 2`. Sublime rejects the file outright without them, and the syntax simply never appears in the menu. The scope keeps `text.html.markdown` as its prefix (`text.html.markdown.rich`, the way MultiMarkdown does it) so every selector written against Markdown, including this plugin's and the color scheme's, keeps matching.

## Python host

The package ships a `.python-version` pinning it to Sublime's 3.8 plugin host. Without it, plugins load on the legacy 3.3 host, where `subprocess.run` doesn't exist: every local render fails instantly with `'module' object has no attribute 'run'`, the remote fallback silently does all the work, and the only symptom is diagrams that take seconds and fail whenever the service hiccups.

The subprocess helper uses `Popen` rather than `run` anyway, so a missing or ignored version file costs a slow render instead of a broken one.

## Caching

Downloads and diagram renders share one directory under the OS temp dir. Renders are written to a temporary file and moved into place, so a partially-written PNG is never displayed. Diagram cache names record the scale they were rendered at, because mermaid-cli honours the configured scale and the remote renderer doesn't; without it, remote diagrams would display at half size.

Cache filenames avoid `@`: `pathname2url` percent-escapes it, and minihtml takes `src` literally, so `name@2x.png` resolves to a file that doesn't exist and renders as a broken image.

## Tests

```bash
python3 tests/test_section_ref.py     # built-in runner, no dependencies
python3 tests/test_mermaid.py
python3 tests/test_markdown_syntax.py
pytest tests/                         # or the whole suite at once
```

The rest of the plugin is view, region and fold plumbing around those helpers, and is exercised in Sublime.
