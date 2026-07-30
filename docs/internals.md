# How it works

Implementation notes for MarkdownRich. The [README](../README.md) covers what the plugin does; this covers how, and why the awkward parts are the way they are.

## Layout

Logic that doesn't need a running editor lives in modules that import no `sublime`, so it runs under plain Python and is unit-tested:

| Module               | Responsibility                                                               |
|----------------------|------------------------------------------------------------------------------|
| `section_ref.py`     | Parsing `§N` references, matching them against numbered headings             |
| `anchor.py`          | Splitting `path#fragment` targets, GitHub heading slugs, anchor matching     |
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

## Heading anchors

A `#fragment` is split off before path resolution, otherwise it travels into the filename and every anchored link reports "cannot open". Slugs follow GitHub's rules, since that's what the links in a README were written against: inline markup stripped, punctuation dropped, lowercased, whitespace to hyphens, and repeats suffixed `-1`, `-2`. Headings come from the same `markup.heading` scan as §-refs, so a `##` inside a fence can't answer a link.

An empty path (`[label](#usage)`) jumps within the current buffer. For another file, `open_file` returns before the buffer has content, and being loaded still doesn't mean being scoped: `find_by_selector` can come back empty for a beat afterwards, which looks exactly like a missing anchor. So the jump polls twice over, with a long budget for loading and a short one for scoping, and only then reports the miss. An explicit `:line` wins over an anchor, being the more precise of the two.

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

## The diagram tab

`Open image` doesn't hand the PNG to Sublime's image viewer, which draws a checkerboard behind transparent pixels: a diagram left to blend with the editor is mostly transparent, so it would arrive on a chessboard. Instead the plugin opens a scratch view holding a single phantom, so the image composites over the color scheme's background exactly as the inline one does, and the file keeps its alpha.

Diagram images are embedded as `data:` URLs rather than referenced by `file://`. minihtml loads a file-backed image asynchronously and draws `broken_image.png` until the bytes arrive, stretched to whatever width and height the tag asked for, which on a full-size diagram is an enormous red-and-white icon. Embedding costs about a third more bytes in the phantom's HTML and removes the load entirely. Encodings are cached by path and mtime, since inline phantoms re-render on save, focus and typing, and anything over 4MB falls back to `file://`.

Owning the markup means the tab can carry a `fit / actual size` toggle. Redrawing to switch between them is only safe because the image is embedded; while it was loaded from `file://`, every redraw flashed the broken-image glyph, and the toggle had to be removed until the data URL fixed the cause.

Renders start on load, on focus, on save and on an explicit `Show diagram`, never on modification. A block with a tab following it renders on save too, even though it is showing its source, since that render is what the tab is waiting for. Moving the caret out of an edited block redraws with renders disabled, so it folds and unfolds without spending a subprocess or a network request on half-written text.

A block with a tab open stays on source, so `Show diagram` has no state left to change; it reveals the tab instead, which is the only place that diagram now exists. Both it and `Open image` go through the same reveal, so the pairing of document and diagram is consistent however you got there.

An open tab is registered against the origin view and the block's ordinal position, and every render pass repoints it at the newest cache entry for that block. Tracking the position rather than the file is what makes edits show up: a changed diagram hashes to a different entry, so the file the tab opened with is never updated. The swap waits for the new render to exist, so an edit in progress leaves the last good picture on screen.

Scrolling to a diagram wider than the window takes a line of spaces as wide as the image, since phantoms add vertical layout but no horizontal extent. It is written once when the tab opens, sized to the render, so switching size never edits the buffer the phantom is anchored to.

The tab shows one fixed size and never re-renders. minihtml decodes a phantom's image after laying it out, so any redraw flashes the broken-image glyph before the picture arrives; a size toggle was tried and abandoned for exactly that. The view scrolls instead, which needs a line of spaces as wide as the image, since phantoms add vertical layout but no horizontal extent.

## Python host

The package ships a `.python-version` pinning it to Sublime's 3.8 plugin host. Without it, plugins load on the legacy 3.3 host, where `subprocess.run` doesn't exist: every local render fails instantly with `'module' object has no attribute 'run'`, the remote fallback silently does all the work, and the only symptom is diagrams that take seconds and fail whenever the service hiccups.

The subprocess helper uses `Popen` rather than `run` anyway, so a missing or ignored version file costs a slow render instead of a broken one.

## Caching

Downloads and diagram renders share one directory under the OS temp dir. Renders are written to a temporary file and moved into place, so a partially-written PNG is never displayed. Diagram cache names record the scale they were rendered at, because mermaid-cli honours the configured scale and the remote renderer doesn't; without it, remote diagrams would display at half size.

Cache filenames avoid `@`: `pathname2url` percent-escapes it, and minihtml takes `src` literally, so `name@2x.png` resolves to a file that doesn't exist and renders as a broken image.

## Tests

```bash
python3 tests/test_section_ref.py     # built-in runner, no dependencies
python3 tests/test_anchor.py
python3 tests/test_mermaid.py
python3 tests/test_markdown_syntax.py
pytest tests/                         # or the whole suite at once
```

The rest of the plugin is view, region and fold plumbing around those helpers, and is exercised in Sublime.
