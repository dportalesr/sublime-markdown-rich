# MarkdownRich

Rich Markdown reading in Sublime Text 4: inline images, mermaid diagrams, project-aware link opening, and section navigation.

Markdown files are written to be *read* somewhere else (GitHub, a docs site, a preview pane). MarkdownRich pulls that reading experience into the editor without a preview window: images render as phantoms beside the source line, ` ```mermaid ` blocks fold away and show the diagram instead, links open with the keyboard or mouse (local files in Sublime, URLs in the browser), and `§3.1`-style cross-references jump to the heading they name.

Everything works on the buffer as-is. No preview pane, no build step, no Package Control dependencies.

## Features

**Images**

- **Inline phantoms** for both Markdown `![](...)` and raw HTML `<img>` tags (handy for GitHub PR descriptions pasted into local files)
- **Per-image size toggle**: a `thumbnail · medium · original` footer; click the image to cycle, click a label to jump straight to that size
- **Smart medium-merge**: when "medium" would render within ~10% of the original width, it drops out of the toggle (configurable)
- **Remote images** fetched on a background thread and cached under the OS temp dir
- **Private GitHub images** via optional bearer-token auth for `github.com` and `*.githubusercontent.com`, with the token dropped on cross-host redirect (signed S3)
- **Status annotations**: loading / not-found / fetch-failed states render as inline annotations (with a retry link), not phantoms, so they don't push the layout around

**Diagrams**

- **Mermaid blocks render in place**: the fenced source folds behind its ` ```mermaid ` line and the diagram takes its spot; one inline `Show source` / `Show diagram` link on that line switches between the two
- **Local first**: rendered with `mmdc` (mermaid-cli) when it's installed, so nothing leaves the machine; a Kroki server is the fallback (switchable off, or pointable at a self-hosted instance)
- **Content-addressed cache**: a diagram renders once per source + theme + scale, then loads instantly
- **Edit in place**: putting the caret in a block reveals its source; moving away folds the diagram back

**Links**

- **Open from keyboard or mouse**: caret on a link + `ctrl+enter`, or triple-click; word-select on plain text is preserved
- **Project-root path resolution**: relative paths resolve against the project root first, falling back to the current file's directory
- **Jump to line/column**: a `[label](path:line)` or `path:line:col` suffix opens the file at that position
- **Side-by-side by default**: the opened file lands in the Markdown's own group with both sheets selected, so Sublime tiles them without touching your window layout

**Navigation**

- **Section references**: `§3` / `§3.1` render in the link style and `ctrl+enter` jumps to the matching numbered heading in the same document
- **Hover to preview**: hovering a reference shows the heading it points at (`3.1 Basics`, line 42) as a clickable link
- **Live styling**: references restyle as you type (debounced), on load, on tab focus, and on save

**Syntax**

- **Fenced code for every installed language**: Sublime's Markdown syntax embeds a fixed list of languages, so a fence tagged with anything else stays unhighlighted. MarkdownRich generates a `MarkdownRich` syntax that inherits Markdown and adds the missing ones, taken from whatever syntaxes you have installed

## Requirements

- **Sublime Text 4** (side-by-side reveal uses `select_sheets`, build 4050+; older builds open a plain tab instead)

No Package Control dependencies, no Python packages. Image dimension probing is built in (PNG/JPEG/GIF headers parsed without PIL).

## Installation

Until published to Package Control, clone into your Packages folder:

```bash
cd "$HOME/Library/Application Support/Sublime Text/Packages/"
git clone git@github.com:dportalesr/sublime-markdown-rich.git MarkdownRich
```

Then restart Sublime Text, or just open any Markdown file (`plugin_loaded` picks up already-open buffers).

## Usage

| Interaction                  | Effect                                                                       |
|------------------------------|------------------------------------------------------------------------------|
| open a Markdown file         | images render automatically (`auto_show_on_load`); `§`-refs get link styling |
| `ctrl+enter` on a link       | opens it: local file in Sublime (side-by-side), url in the browser           |
| `ctrl+enter` on a `§3.1` ref | jumps the caret to the `### 3.1 ...` heading                                 |
| hover a `§3.1` ref           | popup names the target heading; click it to jump                             |
| triple-click a link          | same as `ctrl+enter`                                                         |
| click an image phantom       | cycles its size: thumbnail → medium → original                               |
| click a footer label         | jumps directly to that size                                                  |
| click a mermaid diagram      | switches that block back to its source (same as `Show source`)               |
| caret inside a mermaid block | unfolds it for editing; leaving refolds the diagram                          |

`ctrl+enter` is bound in `Default (OSX).sublime-keymap` twice: once scoped to link scopes (`meta.link` / `markup.underline.link`) and once gated on the `markdown_rich_section_ref` context key, so it passes through untouched everywhere else. Triple-click lives in `Default.sublime-mousemap`.

The `markdown_rich_open_link` command takes a `side_by_side` arg (`true`/`false`) that overrides the `open_link_side_by_side` setting per binding, e.g. add `"args": {"side_by_side": false}` to the mousemap to keep triple-click opening in place.

## Commands

| Command                                 | Effect                                                               |
|-----------------------------------------|----------------------------------------------------------------------|
| `MarkdownRich: Show inline images`      | Render phantoms in the active Markdown view                          |
| `MarkdownRich: Hide inline images`      | Clear phantoms (also drops error annotations)                        |
| `MarkdownRich: Toggle inline images`    | Show ↔ hide                                                          |
| `MarkdownRich: Cycle all image sizes`   | Advance every phantom one step in its size cycle                     |
| `MarkdownRich: Toggle mermaid diagrams` | Flip every mermaid block in the view between diagram and source      |
| `MarkdownRich: Clear Cache`             | Delete cached downloads/renders and rebuild every open Markdown view |
| `MarkdownRich: Rebuild Markdown syntax` | Regenerate the MarkdownRich syntax from the installed syntaxes       |
| `MarkdownRich: Settings`                | Open user settings side-by-side with defaults                        |

## Fenced code highlighting

Sublime's built-in Markdown syntax embeds a fixed list of languages, one hand-written rule each, and there is no lookup of a fence's info string against installed syntaxes. A fence tagged `mermaid`, `d2`, `nix` or anything else outside that list stays plain `markup.raw`, and installing a syntax for the language changes nothing.

MarkdownRich generates a syntax that fixes this without hardcoding a second list:

- it `extends` the built-in Markdown, so every rule the parent has keeps working
- it enumerates the syntaxes you actually have installed, skips the ones the parent already embeds, and emits one fence rule per language that's left
- the generated rules mirror the parent's own (same variables, capture numbering and `embed_scope` shape), so code folding, the infostring scope and the language-name scope behave as they do for built-in languages

Install a new language package and the next reload picks it up; `MarkdownRich: Rebuild Markdown syntax` does it on demand. The file is written to `Packages/User/MarkdownRich.sublime-syntax` and only rewritten when its contents change, so a steady setup never sees a syntax reload.

Info strings come from each syntax's scope tail, its name when that reads like an info string, and its declared file extensions, so ```` ```mermaid ```` and ```` ```mmd ```` both work. A token is owned by one language: whoever claims it first keeps it, and sub-syntaxes (`source.mermaid.flowchart`) and helper fragments (`source.css.mermaid`) are skipped, since they'd otherwise claim `flowchart` or `mermaid` for something that isn't the language.

**To use it**, pick `MarkdownRich` from the syntax menu, or make it the default for Markdown files with *View → Syntax → Open all with current extension as…*. Set `generate_markdown_syntax` to `false` to turn generation off.

It inherits plain Markdown by default. `markdown_syntax_parent` points it somewhere else, e.g. `Packages/Markdown/MultiMarkdown.sublime-syntax` to keep MultiMarkdown's metadata block on top of the fenced languages. Coverage is read along the whole `extends` chain, so inheriting a syntax that itself inherits Markdown doesn't duplicate the 44 languages Markdown already embeds.

## Path resolution

Relative paths in `[text](path)` and `![alt](path)` are resolved against multiple bases, in priority order:

1. **Project root**: `window.folders()[0]`, which covers `.sublime-project` files *and* ad-hoc folder windows
2. **Current file's directory**: fallback when the project root doesn't carry the target, or when the window has no folders

The first candidate that exists on disk wins. If none exist, the project-root candidate is reported as the missing path.

This means a link like `[design](docs/design.md)` resolves against the project root from any file in the project, without forcing every link to use `./` or `../` segments.

A trailing `:line` or `:line:col` is stripped before resolution and reapplied as the caret position (`ENCODED_POSITION`), so `[deactivate](app/models/mower_alert_report.rb:167)` opens that file at line 167. Absolute paths, `~`, and `file:///` URLs are all accepted (the `file://` scheme colon is never mistaken for a line number).

## Mermaid diagrams

A ` ```mermaid ` block folds away and renders as a diagram in its place, themed to match your color scheme. Rendering is local through [mermaid-cli](https://github.com/mermaid-js/mermaid-cli) when it's installed, with [Kroki](https://kroki.io) as an optional fallback.

See **[docs/mermaid.md](docs/mermaid.md)** for fence syntax, backends, theming, caching, and the mermaid settings.

## Recovering from a bad render

Three levels, cheapest first:

1. **Retry link** in the failure annotation: re-runs that one diagram (or re-fetches that one image). Enough for a transient network blip; remote renders already retry once on their own, since Kroki 500s tend to be momentary.
2. **`MarkdownRich: Clear Cache`**: empties the cache directory and rebuilds every open Markdown view. Use it when a render succeeded but produced the wrong thing: a diagram rendered before mermaid-cli was reachable (so it came from Kroki), a stale entry after switching color schemes, a truncated download.
3. **Delete the cache directory** (`$TMPDIR/SublimeMarkdownRich`, see `remote_cache_dirname`) if Sublime isn't running.

Renders are written to a temporary file and moved into place, so a partially-written PNG is never displayed.

## Section references

Write `§3` (or `§ 3`, `§3.1`, `§3.2.1`) anywhere in prose to point at a numbered heading. Each reference is painted in the color scheme's link style, and `ctrl+enter` on it moves the caret to the heading and centers it.

Resolution is an exact match on the heading's **leading number**: `§3` lands on `## 3. Usage`, `§3.1` on `### 3.1 Basics`. Only ATX headings (`#`-prefixed) count, headings are read from the `markup.heading` scope so `##`-looking lines inside fenced code aren't matched, and duplicates resolve to the first occurrence. Trailing sentence punctuation stays out of the reference, so `(§3).` points at section 3. A miss just flashes a status message.

Hovering a reference pops up the heading it resolves to, with its line number, as a link: clicking navigates without placing the caret first (`section_ref_popup` turns this off). A reference that resolves to nothing says so instead.

Styling skips references inside code spans and fences (`markup.raw`), which stay plain text.

## Settings

Defaults live in `MarkdownRich.sublime-settings`; override them in `Packages/User/MarkdownRich.sublime-settings` (or via `MarkdownRich: Settings`). Mermaid's own settings are documented in [docs/mermaid.md](docs/mermaid.md#settings).

| Key                        | Default                                              | Description                                                                                                                                                        |
|----------------------------|------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `auto_show_on_load`        | `true`                                               | Render phantoms automatically when a Markdown view loads or activates                                                                                              |
| `default_size`             | `"thumbnail"`                                        | Initial size for new phantoms: `"thumbnail"`, `"medium"`, or `"original"`                                                                                          |
| `render_remote`            | `true`                                               | Fetch and render remote http(s) images                                                                                                                             |
| `thumbnail_width`          | `180`                                                | Pixel cap for the thumbnail state (square cap, longest side)                                                                                                       |
| `max_image_width`          | `800`                                                | Width cap for the medium state                                                                                                                                     |
| `max_image_height`         | `600`                                                | Height cap for the medium state                                                                                                                                    |
| `medium_merge_threshold`   | `0.1`                                                | Drop medium from the toggle when its width is within this fraction of the original (set to `0` to always keep all three)                                           |
| `image_extensions`         | `[".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"]` | File extensions treated as images (SVG excluded: minihtml can't render it)                                                                                         |
| `remote_cache_dirname`     | `"SublimeMarkdownRich"`                              | Sub-folder name under the OS temp dir for cached downloads                                                                                                         |
| `github_token`             | `""`                                                 | Optional bearer token for private-repo images (see below)                                                                                                          |
| `github_token_file`        | `""`                                                 | Path to a file containing the token; preferred over `github_token`                                                                                                 |
| `status_color`             | `"#c0863a"`                                          | Accent color for inline status annotations (loading / missing / error)                                                                                             |
| `section_ref_popup`        | `true`                                               | Hovering a §-reference previews its target heading as a clickable link                                                                                             |
| `generate_markdown_syntax` | `true`                                               | Generate a `MarkdownRich` Markdown syntax that highlights fenced code for every installed language                                                                 |
| `markdown_syntax_parent`   | `"Packages/Markdown/Markdown.sublime-syntax"`        | Syntax the generated one inherits from; point it at MultiMarkdown to keep its metadata block                                                                       |
| `open_link_side_by_side`   | `true`                                               | Open file links beside the origin Markdown by selecting both sheets in the same group (Sublime tiles them; window layout unchanged); local files only, not http(s) |

## Private GitHub images

Some images on GitHub (PR descriptions, `user-attachments`, private-repo content) require auth. MarkdownRich can send a bearer token, but only to `github.com` and `*.githubusercontent.com`, and it strips the `Authorization` header on cross-host redirects (e.g. GitHub forwarding to a signed S3 URL).

**Recommended setup**, keeping the token *out* of synced Sublime settings:

```bash
mkdir -p ~/.config/markdown_rich
chmod 700 ~/.config/markdown_rich
printf '%s' '<your-fine-grained-token>' > ~/.config/markdown_rich/github_token
chmod 600 ~/.config/markdown_rich/github_token
```

Then in `Packages/User/MarkdownRich.sublime-settings`:

```json
{
    "github_token_file": "~/.config/markdown_rich/github_token"
}
```

A fine-grained token with `Contents: read` on the relevant repos is enough.

## How it works

- A per-view `PhantomManager` finds image regions with `view.find_all(...)`, resolves each source path/URL, and renders one `sublime.Phantom` per resolved image at the end of the source line.
- Non-image states (loading, missing, fetch-failed) render as `add_regions(annotations=...)` so they don't reflow the buffer like a block phantom would.
- Remote fetches run on a background `threading.Thread` and call `sublime.set_timeout(..., 0)` to re-enter the main thread for re-render.
- Image dimensions are probed by reading the file header (no PIL/Pillow dependency): PNG `IHDR`, GIF logical screen descriptor, JPEG `SOFn` markers.
- Section references can't be scoped by syntax (Sublime has no injection into a grammar it doesn't own), so the plugin styles them with `add_regions(..., "markup.underline.link", ...)` and answers an `on_query_context` key (`markdown_rich_section_ref`) that gates the `ctrl+enter` keymap.
- Side-by-side reveal keeps the window layout intact: the opened view is moved into the origin's group and `window.select_sheets([origin, opened])` lets Sublime tile the two sheets.
- Mermaid blocks fold their source away and render a phantom in its place; the details are in [docs/mermaid.md](docs/mermaid.md#implementation-notes).

## Development

Logic that doesn't need a running editor lives in sibling modules that import no `sublime`, so it runs under plain Python: `section_ref.py` (reference parsing, heading matching) and `mermaid.py` (fence scanning, cache keys, render invocations, display sizing). Tests:

```bash
python3 tests/test_section_ref.py   # built-in runner, no dependencies
python3 tests/test_mermaid.py
python3 tests/test_markdown_syntax.py
pytest tests/                       # or the whole suite via pytest
```

The rest of the plugin is view/region/fold plumbing around those helpers plus the two phantom managers, and is exercised in Sublime.

## License

[MIT](LICENSE).
