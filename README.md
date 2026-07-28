# MarkdownRich

Read Markdown the way it's meant to look, without leaving the editor.

Markdown is written to be read somewhere else: GitHub, a docs site, a preview pane. MarkdownRich brings that reading experience into the buffer itself. Images appear where they're referenced, mermaid blocks show the diagram instead of its source, links open with a keystroke, and cross-references tell you where they lead.

No preview pane, no split window, no build step. The file you're editing is the file you're reading.

## Features

### Images

<!-- screenshot: a Markdown file with inline images at different sizes -->

- **See your images inline**, whether they're Markdown `![](...)` or the raw `<img>` tags you get from pasting a GitHub PR description
- **Click an image to cycle through preset sizes**, or select a size directly by its label
- **Reference images from anywhere in the project**: `docs/design.png` works from any file, no `../../` chains
- **Remote images just work**, including private GitHub attachments once you've pointed the plugin at a token
- **Missing or slow images stay out of the way**, showing a small inline note (with a retry link) instead of a broken block

### Diagrams

<!-- screenshot: a ```mermaid block rendered as a diagram, with the Show source link -->

- **Mermaid blocks render as diagrams** in place of their source, so a document full of charts reads like documentation instead of code
- **One link flips between diagram and source**, always in the same spot
- **Open a diagram in its own tab** when it's too dense to read inline, full size, still on your editor's background, updating as you edit the source
- **Edit in place**: put the cursor in a block to get the source back, move away and the diagram returns
- **Diagrams match your color scheme**, drawn on the editor's own background in a matching theme
- **Rendered locally** when mermaid-cli is installed, so nothing you write leaves the machine

### Links and navigation

<!-- screenshot: hovering a §-reference, showing the target heading popup -->

- **Open what you're reading**: a keystroke or a triple-click follows a link, local files into Sublime and URLs into the browser
- **Land where you meant to**: `[deactivate](app/models/report.rb:167)` opens that file at that line
- **Keep your place**: opened files sit beside the document instead of replacing it, and your window layout is left alone
- **Cross-reference sections** with `§3` or `§3.1`, which jump to the matching numbered heading
- **Hover a reference to see where it goes**, then click through if that's where you wanted to be

### Code blocks

<!-- screenshot: fenced blocks highlighted for a language Sublime doesn't cover by default -->

- **Fenced code is highlighted for every language you have installed**, not just the ones Sublime ships support for
- **Install a language package and it works**: nothing to configure, nothing to list

## Requirements

Sublime Text 4. Nothing else: no Package Control dependencies, no Python packages.

Mermaid diagrams render locally when [mermaid-cli](https://github.com/mermaid-js/mermaid-cli) is installed (`npm install -g @mermaid-js/mermaid-cli`), and fall back to a rendering service otherwise. See [docs/mermaid.md](docs/mermaid.md).

## Installation

Clone into your Packages folder:

```bash
cd "$HOME/Library/Application Support/Sublime Text/Packages/"
git clone git@github.com:dportalesr/sublime-markdown-rich.git MarkdownRich
```

Open any Markdown file and it starts working. For highlighted code fences, pick the `MarkdownRich` syntax (View → Syntax), or make it your default for `.md` files with *Open all with current extension as…*.

## What you can do

| Do this                     | Get this                                                      |
|-----------------------------|---------------------------------------------------------------|
| open a Markdown file        | images render, `§`-references become links                    |
| `ctrl+enter` on a link      | opens it, beside the document or in your browser              |
| triple-click a link         | the same, without touching the keyboard                       |
| `ctrl+enter` on a `§3.1`    | jumps to that section                                         |
| hover a `§3.1`              | shows which heading it points at, click to follow             |
| click an image              | cycles its size                                               |
| click a size label          | switches straight to that size                                |
| click a diagram             | swaps it back to its mermaid source                           |
| click `Open image`          | opens the diagram in its own tab, fit or full size            |
| put the cursor in a diagram | reveals the source to edit; the diagram returns when you save |

## Commands

| Command                                 | What it does                                               |
|-----------------------------------------|------------------------------------------------------------|
| `MarkdownRich: Toggle inline images`    | Show or hide every image in the view                       |
| `MarkdownRich: Show inline images`      | Render images in the view                                  |
| `MarkdownRich: Hide inline images`      | Clear them                                                 |
| `MarkdownRich: Cycle all image sizes`   | Resize every image at once                                 |
| `MarkdownRich: Toggle mermaid diagrams` | Flip every diagram in the view between chart and source    |
| `MarkdownRich: Open diagram image`      | Open the diagram at the cursor as an image, in its own tab |
| `MarkdownRich: Clear Cache`             | Re-fetch and re-render everything, after a bad render      |
| `MarkdownRich: Rebuild Markdown syntax` | Pick up languages installed since the last restart         |
| `MarkdownRich: Toggle debug logging`    | Report fetches and renders to the console while you work   |
| `MarkdownRich: Settings`                | Open your settings beside the defaults                     |

## Settings

Defaults live in `MarkdownRich.sublime-settings`; override them in `Packages/User/MarkdownRich.sublime-settings`, or via `MarkdownRich: Settings`. Mermaid has [its own settings](docs/mermaid.md#settings).

| Setting                    | Default                                              | Effect                                                          |
|----------------------------|------------------------------------------------------|-----------------------------------------------------------------|
| `auto_show_on_load`        | `true`                                               | Images appear as soon as a file opens                           |
| `default_size`             | `"thumbnail"`                                        | Size images start at                                            |
| `render_remote`            | `true`                                               | Fetch images from the web                                       |
| `thumbnail_width`          | `180`                                                | How small the thumbnail size is                                 |
| `max_image_width`          | `800`                                                | How large the medium size can get                               |
| `max_image_height`         | `600`                                                | The same, vertically                                            |
| `medium_merge_threshold`   | `0.1`                                                | Skip the medium size when it's barely smaller than the original |
| `image_extensions`         | `[".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"]` | What counts as an image                                         |
| `remote_cache_dirname`     | `"SublimeMarkdownRich"`                              | Where downloads and renders are kept                            |
| `github_token`             | `""`                                                 | Token for private GitHub images                                 |
| `github_token_file`        | `""`                                                 | Read that token from a file instead, safer with synced settings |
| `status_color`             | `"#c0863a"`                                          | Color of the inline loading and error notes                     |
| `open_link_side_by_side`   | `true`                                               | Opened files appear beside the document                         |
| `section_ref_popup`        | `true`                                               | Hovering a `§`-reference previews its target                    |
| `debug`                    | `false`                                              | Log image downloads and diagram renders to the Sublime console  |
| `generate_markdown_syntax` | `true`                                               | Keep code-fence highlighting in sync with installed languages   |
| `markdown_syntax_parent`   | `"Packages/Markdown/Markdown.sublime-syntax"`        | Base Markdown syntax to build on, e.g. MultiMarkdown            |

## Private GitHub images

Images in PR descriptions, `user-attachments` and private repos need authentication. MarkdownRich sends a token only to GitHub's own hosts, and drops it the moment a redirect leaves them.

Keep the token out of synced settings by putting it in a file:

```bash
mkdir -p ~/.config/markdown_rich && chmod 700 ~/.config/markdown_rich
printf '%s' '<your-fine-grained-token>' > ~/.config/markdown_rich/github_token
chmod 600 ~/.config/markdown_rich/github_token
```

```json
{
    "github_token_file": "~/.config/markdown_rich/github_token"
}
```

A fine-grained token with `Contents: read` is enough.

## When something looks wrong

A failed image or diagram shows an inline note with a **retry** link, which is usually all it takes. If a render succeeded but produced the wrong thing (a diagram made before mermaid-cli was installed, say, or one from a previous color scheme), run `MarkdownRich: Clear Cache`.

When it's less obvious than that (a render that seems stuck, or an edit that doesn't appear to take), turn on `MarkdownRich: Toggle debug logging` and watch the console: every download and render reports which backend ran, how long it took, and the cache key each block resolved to. An edit that produces the same key never reached the block being rendered.

## More

- [Mermaid diagrams](docs/mermaid.md): fence syntax, rendering backends, theming, caching
- [How it works](docs/internals.md): the implementation, and how to run the tests

## License

[MIT](LICENSE).
