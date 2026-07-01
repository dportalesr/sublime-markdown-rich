# MarkdownRich

Inline image phantoms and link opening (click or `ctrl+enter`) for Markdown in Sublime Text.

MarkdownRich auto-renders `![](...)` and raw HTML `<img>` tags as phantoms next to the source line. Each image gets a size toggle (thumbnail → medium → original) in its footer. Putting the caret on any link or URL and pressing `ctrl+enter` (or triple-clicking it) opens it — remote URLs in the browser, local files in Sublime. File links may carry a `:line` (or `:line:col`) suffix to jump straight to a position, and open side-by-side by default so the Markdown stays visible.

## Features

- **Inline image phantoms** — both Markdown `![](...)` and raw HTML `<img>` tags (handy for GitHub PR descriptions pasted into local files)
- **Per-image size toggle** — `thumbnail · medium · original` footer; click image to cycle, click footer label to jump
- **Smart medium-merge** — when "medium" would render within ~10% of the original width, it's dropped from the toggle (configurable)
- **Project-root path resolution** — relative paths in projects resolve against the project root first, falling back to the current file's directory; non-project windows keep file-dir resolution
- **Remote image caching** — http(s) images fetched on a background thread, cached under the OS temp dir
- **Private GitHub images** — optional bearer-token auth for `github.com` and `*.githubusercontent.com`; token dropped on cross-host redirect (signed S3)
- **Open links from the keyboard or mouse** — caret on a link + `ctrl+enter`, or triple-click; local files open in Sublime, URLs in the browser; word-select on plain text is preserved
- **Jump to line/column** — a `[label](path:line)` or `path:line:col` suffix opens the file at that position (`ENCODED_POSITION`)
- **Side-by-side by default** — file links open in an adjacent pane (splitting into two columns when needed) so the Markdown stays in view; toggle with `open_link_side_by_side`
- **Status annotations** — loading / not-found / fetch-failed states render as inline annotations (with retry link), not phantoms

## Requirements

- **Sublime Text 4**

No Package Control dependencies, no Python packages — image dimension probing is built in (PNG/JPEG/GIF headers parsed without PIL).

## Installation

Until published to Package Control, clone into your Packages folder:

```bash
cd "$HOME/Library/Application Support/Sublime Text/Packages/"
git clone git@github.com:dportalesr/sublime-markdown-rich.git MarkdownRich
```

Then restart Sublime Text (or just open any Markdown file — `plugin_loaded` will pick it up).

## Commands

| Command | Effect |
|---------|--------|
| `MarkdownRich: Show inline images` | Render phantoms in the active Markdown view |
| `MarkdownRich: Hide inline images` | Clear phantoms (also drops error annotations) |
| `MarkdownRich: Toggle inline images` | Show ↔ hide |
| `MarkdownRich: Cycle all image sizes` | Advance every phantom one step in its size cycle |
| `MarkdownRich: Settings` | Open user settings side-by-side with defaults |

Opening a link invokes `markdown_rich_open_link`, bound two ways out of the box: `ctrl+enter` when the caret is on a link (`Default (OSX).sublime-keymap`, scoped to `meta.link` / `markup.underline.link` so it passes through elsewhere) and a triple-click (`Default.sublime-mousemap`). The command accepts a `side_by_side` arg (`true`/`false`) that overrides the `open_link_side_by_side` setting per binding, e.g. add `"args": {"side_by_side": false}` to the mousemap to keep triple-click opening in place.

## Path resolution

Relative paths in `[text](path)` and `![alt](path)` are resolved against multiple bases, in priority order:

1. **Project root** — `window.folders()[0]` (covers `.sublime-project` files *and* ad-hoc folder windows)
2. **Current file's directory** — fallback when the project root doesn't carry the target, or when the window has no folders

The first candidate that exists on disk wins. If none exist, the project-root candidate is reported as the missing path.

This means a link like `[design](docs/design.md)` resolves against the project root from any file in the project — without forcing every link to use `./` or `../` segments.

A trailing `:line` or `:line:col` is stripped before resolution and reapplied as the caret position, so `[deactivate](app/models/mower_alert_report.rb:167)` opens that file at line 167. Absolute paths, `~`, and `file:///` URLs are all accepted (the `file://` scheme colon is never mistaken for a line number).

## Settings

| Key | Default | Description |
|-----|---------|-------------|
| `auto_show_on_load` | `true` | Render phantoms automatically when a Markdown view loads or activates |
| `default_size` | `"thumbnail"` | Initial size for new phantoms: `"thumbnail"`, `"medium"`, or `"original"` |
| `render_remote` | `true` | Fetch and render remote http(s) images |
| `thumbnail_width` | `180` | Pixel cap for the thumbnail state (square cap, longest side) |
| `max_image_width` | `800` | Width cap for the medium state |
| `max_image_height` | `600` | Height cap for the medium state |
| `medium_merge_threshold` | `0.1` | Drop medium from the toggle when its width is within this fraction of the original (set to `0` to always keep all three) |
| `image_extensions` | `[".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"]` | File extensions treated as images (SVG excluded — minihtml can't render it) |
| `remote_cache_dirname` | `"SublimeMarkdownRich"` | Sub-folder name under the OS temp dir for cached downloads |
| `github_token` | `""` | Optional bearer token for private-repo images (see below) |
| `github_token_file` | `""` | Path to a file containing the token; preferred over `github_token` |
| `status_color` | `"#c0863a"` | Accent color for inline status annotations (loading / missing / error) |
| `open_link_side_by_side` | `true` | Open file links in an adjacent pane (splitting into two columns when the window has one group); only affects local files, not http(s) |

## Private GitHub images

Some images on GitHub (PR descriptions, `user-attachments`, private-repo content) require auth. MarkdownRich can send a bearer token, but only to `github.com` and `*.githubusercontent.com` — and it strips the `Authorization` header on cross-host redirects (e.g. GitHub forwarding to a signed S3 URL).

**Recommended setup** — keep the token *out* of synced Sublime settings:

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
- Non-image states (loading, missing, fetch-failed) render as `add_regions(annotations=...)` so they don't push layout around like a block phantom would.
- Remote fetches run on a background `threading.Thread` and call `sublime.set_timeout(..., 0)` to re-enter the main thread for re-render.
- Image dimensions are probed by reading the file header (no PIL/Pillow dependency) — PNG `IHDR`, GIF logical screen descriptor, JPEG `SOFn` markers.

## License

[MIT](LICENSE).
