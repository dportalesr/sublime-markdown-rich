"""MarkdownRich: inline image phantoms with a size toggle, plus double-click link opening."""

import os
import re
import base64
import hashlib
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.request
import urllib.parse
import webbrowser

import sublime
import sublime_plugin

# Pure section-reference and mermaid logic lives in sibling modules with no `sublime`
# import so they stay unit-testable. Relative import inside the package, plain import
# as fallback.
try:
    from .section_ref import SECTION_REF_RE, ref_at as _section_ref_number, first_matching_index
    from .mermaid import (find_blocks, cache_key, remote_url, mmdc_args, display_size,
                          auto_theme, with_theme_directive, block_at, width_budget)
    from .markdown_syntax import (Entry, embedded_scopes, extensions_of, fence_tokens,
                                  assign_tokens, render_syntax, is_covered,
                                  drop_specializations, covered_scopes)
except (ImportError, ValueError, SystemError):
    from section_ref import SECTION_REF_RE, ref_at as _section_ref_number, first_matching_index
    from mermaid import (find_blocks, cache_key, remote_url, mmdc_args, display_size,
                         auto_theme, with_theme_directive, block_at, width_budget)
    from markdown_syntax import (Entry, embedded_scopes, extensions_of, fence_tokens,
                                 assign_tokens, render_syntax, is_covered,
                                 drop_specializations, covered_scopes)

SETTINGS_FILE = "MarkdownRich.sublime-settings"
MARKDOWN_SELECTOR = "text.html.markdown"
PHANTOM_KEY = "markdown_rich_images"
STATUS_KEY = "markdown_rich_status"
SECTION_KEY = "markdown_rich_section_refs"
MERMAID_PHANTOM_KEY = "markdown_rich_mermaid"
MERMAID_STATUS_KEY = "markdown_rich_mermaid_status"
# Sublime has no syntax injection, so §-refs are link-styled by the plugin via
# add_regions rather than a scope. find_all wants a pattern string; reuse the pure
# module's regex (its capture group doesn't narrow the match extent) so the finder
# and the parser can't drift apart.
SECTION_REF_FIND_RE = SECTION_REF_RE.pattern
# Context key the ctrl+enter keymap queries (on_query_context) to fire on a §-ref.
SECTION_CONTEXT_KEY = "markdown_rich_section_ref"

# Inline image: ![alt](src ["title"]) — used both for find_all (ST regex) and parsing.
IMAGE_FIND_RE = r'!\[[^\]]*\]\([^)]+\)'
# Raw HTML <img ...> tags (GitHub PR descriptions, embedded HTML). ST regex, case-insensitive.
HTML_IMG_FIND_RE = r'(?i)<img\b[^>]*/?>'
HTML_SRC_RE = re.compile(r'src\s*=\s*["\']([^"\']+)["\']', re.I)
HTML_W_RE = re.compile(r'\bwidth\s*=\s*["\']?(\d+)', re.I)
HTML_H_RE = re.compile(r'\bheight\s*=\s*["\']?(\d+)', re.I)
LINK_RE = re.compile(r'(!?)\[[^\]\n]*\]\(\s*<?([^)>\s]+)>?(?:\s+"[^"]*")?\s*\)')
ANGLE_URL_RE = re.compile(r'<((?:https?|ftp|mailto):[^>\s]+)>')
BARE_URL_RE = re.compile(r'(?:https?|ftp)://[^\s)>\]"\']+')
# Markup/punctuation that clings to the end of a bare URL in prose but isn't part of
# it: inline-code backtick, emphasis/strike delimiters, sentence punctuation.
_URL_TRAILING = '`*_~.,;:!?'

# Size states (cycle order). Thumbnail is the initial state.
THUMB, FIXED, ORIGINAL = 0, 1, 2
STATE_LABEL = {THUMB: "thumbnail", FIXED: "medium", ORIGINAL: "original"}
LABEL_STATE = {label: state for state, label in STATE_LABEL.items()}


def _settings():
    return sublime.load_settings(SETTINGS_FILE)


def _log(message, *args):
    """Print to the Sublime console when `debug` is on.

    Renders happen on background threads behind a cache, so "nothing happened" and
    "it worked, from cache" and "it failed and will retry" all look identical from
    the outside. This makes them distinguishable without attaching a debugger.
    """
    if _settings().get("debug", False):
        print("MarkdownRich: " + (message % args if args else message))


def _elapsed(started):
    return "%.1fs" % (time.monotonic() - started)


def _default_state():
    """Initial size state for newly-rendered images, from the `default_size` setting."""
    return LABEL_STATE.get(_settings().get("default_size", "thumbnail"), THUMB)


def _is_remote(url):
    return bool(re.match(r'^(https?|ftp)://', url, re.I))


def _has_image_ext(url):
    exts = _settings().get("image_extensions", [])
    path = urllib.parse.urlparse(url).path if _is_remote(url) else url
    return os.path.splitext(path)[1].lower() in exts


def _relative_bases(view):
    """Base dirs for resolving relative paths, in priority order.

    Project root (first entry of `window.folders()`) takes precedence when the
    window has folders attached — covers both `.sublime-project` files and
    ad-hoc folder windows. The current file's directory is appended as a
    fallback so non-project windows behave as before, and so files referenced
    relative to the current document still resolve when the project root
    doesn't carry them.
    """
    bases = []
    window = view.window()
    if window:
        for folder in window.folders():
            if folder not in bases:
                bases.append(folder)
    file_path = view.file_name()
    if file_path:
        file_dir = os.path.dirname(file_path)
        if file_dir not in bases:
            bases.append(file_dir)
    return bases


def _resolve_local(view, url):
    if url.startswith("file://"):
        url = urllib.request.url2pathname(url[len("file://"):])
    url = os.path.expanduser(url)
    if os.path.isabs(url):
        return url
    bases = _relative_bases(view)
    if not bases:
        return None
    for base in bases:
        candidate = os.path.normpath(os.path.join(base, url))
        if os.path.exists(candidate):
            return candidate
    return os.path.normpath(os.path.join(bases[0], url))


_POSITION_RE = re.compile(r':(\d+)(?::(\d+))?$')


def _split_position(target):
    """Split a `path:line[:col]` file reference into (path, line, col).

    Line/col are ints when a trailing `:line` (optionally `:line:col`) is present,
    else None. Only a trailing digit group is treated as a position, so a colon
    elsewhere (e.g. the `file://` scheme separator) leaves the path intact.
    """
    m = _POSITION_RE.search(target)
    if not m:
        return target, None, None
    col = int(m.group(2)) if m.group(2) else None
    return target[:m.start()], int(m.group(1)), col


def _reveal_beside(window, origin, new_view):
    """Place `new_view` in `origin`'s group and select both sheets, so Sublime tiles
    them side-by-side within the current group without changing the window layout.

    Mirrors Parley's edit/original buffer reveal (see edit_view.py). No-op when the
    two views coincide, when the origin isn't in a group, or on Sublime builds
    without `select_sheets` (< 4050) — those just get a plain tab.
    """
    if origin is None or origin == new_view:
        return
    cur_group, _ = window.get_view_index(origin)
    if cur_group < 0:
        return
    new_group, _ = window.get_view_index(new_view)
    if new_group != cur_group:
        window.set_view_index(new_view, cur_group, len(window.views_in_group(cur_group)))
    select_sheets = getattr(window, "select_sheets", None)
    if select_sheets is not None:
        select_sheets([origin.sheet(), new_view.sheet()])


def _target_at(view, point):
    """Return (is_image, url) for the link/url under `point`, or None."""
    line = view.line(point)
    text = view.substr(line)
    col = point - line.begin()
    for m in LINK_RE.finditer(text):
        if m.start() <= col <= m.end():
            url = m.group(2)
            return (bool(m.group(1)) or _has_image_ext(url), url)
    for m in ANGLE_URL_RE.finditer(text):
        if m.start() <= col <= m.end():
            return (_has_image_ext(m.group(1)), m.group(1))
    for m in BARE_URL_RE.finditer(text):
        if m.start() <= col <= m.end():
            url = m.group(0).rstrip(_URL_TRAILING)
            return (_has_image_ext(url), url)
    return None


# --- section references (§3 -> numbered heading) -----------------------------

def _section_ref_at(view, point):
    """Return the section number of the `§N` reference under `point`, or None."""
    line = view.line(point)
    return _section_ref_number(view.substr(line), point - line.begin())


def _heading_line_regions(view):
    """Full-line regions of every heading, in document order (one per line).

    Driven by the `markup.heading` scope rather than a text scan, so `##`-looking
    lines inside fenced code blocks aren't mistaken for headings. Each scoped region
    is expanded to its full line so `heading_number` sees the `#` markers.
    """
    lines, seen = [], set()
    for region in view.find_by_selector("markup.heading"):
        line = view.line(region.begin())
        if line.begin() not in seen:
            seen.add(line.begin())
            lines.append(line)
    lines.sort(key=lambda r: r.begin())
    return lines


def _section_target(view, label):
    """Region of the first heading numbered `label` (e.g. "3.1"), or None."""
    lines = _heading_line_regions(view)
    idx = first_matching_index(label, [view.substr(r) for r in lines])
    return lines[idx] if idx is not None else None


def _heading_text(view, region):
    """Heading line without its `#` markers, e.g. "3.1 Basics"."""
    return re.sub(r'^\s*#+\s*', '', view.substr(region)).strip()


def _goto_region(view, region):
    """Put the caret at `region` and centre it. Shared by the keymap and the popup."""
    view.hide_popup()
    view.sel().clear()
    view.sel().add(sublime.Region(region.begin()))
    view.show_at_center(region)


_POPUP_STYLE = (
    '<style>'
    'body { margin: 0; padding: 6px 10px; font-size: 0.95rem; }'
    'a { color: var(--bluish); text-decoration: none; }'
    '.mr-loc { color: color(var(--foreground) alpha(0.55)); }'
    '.mr-miss { color: color(var(--foreground) alpha(0.6)); }'
    '</style>'
)


def _section_popup(view, point, label):
    """Show what a `§N` reference points at, as a clickable link.

    Saves the caret-then-`ctrl+enter` dance for the common case of "where does this
    go?", and names the destination heading so the answer needs no jump at all.
    """
    target = _section_target(view, label)
    if target is None:
        body = '<span class="mr-miss">&#167;%s &middot; no such section</span>' % _esc(label)
    else:
        # No "§N →" prefix: the reference is right under the cursor already.
        body = ('<a href="jump">%s</a> <span class="mr-loc">line %d</span>'
                % (_esc(_heading_text(view, target)), view.rowcol(target.begin())[0] + 1))
    view.show_popup(
        '<body>' + _POPUP_STYLE + body + '</body>',
        flags=sublime.HIDE_ON_MOUSE_MOVE_AWAY,
        location=point, max_width=720,
        on_navigate=lambda href: _goto_region(view, target),
    )


# Link-color foreground + underline, no fill/outline (the underline flags require both
# NO_FILL and NO_OUTLINE). Makes §-refs read as links without owning the syntax.
_SECTION_DRAW = sublime.DRAW_NO_FILL | sublime.DRAW_NO_OUTLINE | sublime.DRAW_SOLID_UNDERLINE


def _style_section_refs(view):
    """Paint every `§N` ref (outside code) with the color scheme's link style."""
    regions = [
        r for r in view.find_all(SECTION_REF_FIND_RE)
        if not view.match_selector(r.begin(), "markup.raw")
    ]
    view.add_regions(SECTION_KEY, regions, "markup.underline.link", flags=_SECTION_DRAW)


# --- image dimension probing (no PIL dependency) ----------------------------

def _png_size(d):
    if d[:8] == b'\x89PNG\r\n\x1a\n' and d[12:16] == b'IHDR':
        return int.from_bytes(d[16:20], 'big'), int.from_bytes(d[20:24], 'big')


def _gif_size(d):
    if d[:6] in (b'GIF87a', b'GIF89a'):
        return int.from_bytes(d[6:8], 'little'), int.from_bytes(d[8:10], 'little')


def _jpeg_size(d):
    if d[:2] != b'\xff\xd8':
        return None
    i, n = 2, len(d)
    sof = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
    while i < n - 1:
        if d[i] != 0xFF:
            i += 1
            continue
        marker = d[i + 1]
        i += 2
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            continue
        if i + 2 > n:
            break
        seglen = int.from_bytes(d[i:i + 2], 'big')
        if marker in sof and i + 7 <= n:
            return int.from_bytes(d[i + 5:i + 7], 'big'), int.from_bytes(d[i + 3:i + 5], 'big')
        i += seglen
    return None


def _image_size(path):
    try:
        with open(path, 'rb') as f:
            head = f.read(65536)
    except OSError:
        return None
    return _png_size(head) or _gif_size(head) or _jpeg_size(head)


def _file_src(path):
    return "file://" + urllib.request.pathname2url(path)


# Encoded diagrams, keyed by path and mtime. Inline phantoms re-render on save, focus
# and (debounced) typing, so the encoding has to survive between renders.
_data_src_cache = {}
#: Past this, embedding costs more than the flash it avoids.
DATA_SRC_LIMIT = 4 * 1024 * 1024


def _data_src(path):
    """A `data:` URL for an image, or a `file://` one when it is too large.

    minihtml loads a `file://` image asynchronously, drawing its broken-image glyph
    until the bytes arrive, stretched to whatever width and height the tag asks for.
    Embedding the image removes the load, and with it the flash.
    """
    try:
        stat = os.stat(path)
    except OSError:
        return _file_src(path)
    if stat.st_size > DATA_SRC_LIMIT:
        return _file_src(path)
    key = (path, stat.st_mtime, stat.st_size)
    cached = _data_src_cache.get(key)
    if cached is None:
        try:
            with open(path, "rb") as f:
                cached = "data:image/png;base64," + base64.b64encode(f.read()).decode("ascii")
        except OSError:
            return _file_src(path)
        _data_src_cache.clear()      # only the current renders are worth holding on to
        _data_src_cache[key] = cached
    return cached


def _esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


_PHANTOM_STYLE = (
    '<style>'
    '.mr-c { padding: 10px 14px; padding-bottom: 6px; line-height: 1.5; }'
    '.mr-f { color: color(var(--foreground) alpha(0.55)); padding-top: 10px; margin-top: 6px;'
    ' font-size: 0.85rem; text-align: left; }'
    '.mr-f a { color: color(var(--foreground) alpha(0.7)); text-decoration: none; }'
    '.mr-cur { color: var(--foreground); font-weight: bold; }'
    '</style>'
)


# --- remote caching ----------------------------------------------------------

def _cache_dir():
    d = os.path.join(tempfile.gettempdir(), _settings().get("remote_cache_dirname", "SublimeMarkdownRich"))
    os.makedirs(d, exist_ok=True)
    return d


def _clear_cache():
    """Delete every cached download/render. Returns how many files went."""
    removed = 0
    for name in os.listdir(_cache_dir()):
        try:
            os.remove(os.path.join(_cache_dir(), name))
            removed += 1
        except OSError:
            pass
    return removed


def _cached_path(url):
    name = hashlib.sha1(url.encode("utf-8")).hexdigest()
    ext = os.path.splitext(urllib.parse.urlparse(url).path)[1] or ".img"
    return os.path.join(_cache_dir(), name + ext)


def _is_github_host(url):
    host = urllib.parse.urlparse(url).netloc.lower()
    return host == "github.com" or host.endswith(".github.com") or host.endswith("githubusercontent.com")


class _AuthDropRedirect(urllib.request.HTTPRedirectHandler):
    """Follow redirects, but strip Authorization when the host changes (github.com -> signed S3)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None:
            src_host = urllib.parse.urlparse(req.full_url).netloc
            dst_host = urllib.parse.urlparse(newurl).netloc
            if src_host != dst_host:
                new.headers = {k: v for k, v in new.headers.items() if k.lower() != "authorization"}
        return new


_OPENER = urllib.request.build_opener(_AuthDropRedirect())


def _github_token():
    """Token from settings, or from github_token_file (kept outside iCloud-synced settings)."""
    s = _settings()
    tok = s.get("github_token", "")
    if tok:
        return tok
    path = s.get("github_token_file", "")
    if path:
        try:
            with open(os.path.expanduser(path)) as f:
                return f.read().strip()
        except OSError:
            return ""
    return ""


def _fetch_bytes(src, timeout=15):
    req = urllib.request.Request(src, headers={"User-Agent": "SublimeMarkdownRich"})
    token = _github_token()
    if token and _is_github_host(src):
        req.add_header("Authorization", "Bearer " + token)
    with _OPENER.open(req, timeout=timeout) as resp:
        return resp.read()


# --- mermaid rendering (local mermaid-cli, remote kroki fallback) -------------

# Sublime inherits a GUI process environment, so a login shell's PATH additions
# (homebrew, asdf/rbenv shims, npm prefixes) are missing. mermaid-cli's shebang also
# needs `node` on PATH, not just `mmdc`, so both get these directories.
_EXTRA_PATH = [
    "/opt/homebrew/bin", "/usr/local/bin",
    os.path.expanduser("~/.asdf/shims"), os.path.expanduser("~/.local/bin"),
    os.path.expanduser("~/.volta/bin"), os.path.expanduser("~/.nvm/current/bin"),
]


def _mermaid_env():
    env = os.environ.copy()
    parts = [p for p in _EXTRA_PATH if os.path.isdir(p)]
    env["PATH"] = os.pathsep.join(parts + [env.get("PATH", "")])
    return env


def _display_width(view):
    """Width budget for a diagram, from `mermaid_max_width` and the view's own width.

    The setting is a fraction of the view by default, so the inline diagram reads as a
    preview and the full-size render stays one click away in its own tab.
    """
    usable = 0
    if view is not None:
        usable = max(240, int(view.viewport_extent()[0]) - 80)
    return width_budget(_settings().get("mermaid_max_width", 0.66), usable)


def _mermaid_opts(view=None):
    """Render options that also key the cache: theme, background, scale, display cap.

    Both `"auto"` values come from the color scheme: the diagram is drawn on the
    editor's own background, and the theme is picked to contrast with it. A dark
    scheme with mermaid's light `default` theme would otherwise put near-black text
    on a near-black backdrop.
    """
    s = _settings()
    theme = s.get("mermaid_theme", "auto")
    background = s.get("mermaid_background", "auto")
    if theme == "auto" or background == "auto":
        editor_bg = (view.style() or {}).get("background", "#ffffff") if view else "#ffffff"
        if theme == "auto":
            theme = auto_theme(editor_bg)
        if background == "auto":
            background = editor_bg
    return {
        "theme": theme,
        "background": background,
        "scale": s.get("mermaid_scale", 2),
        "max_width": _display_width(view),
        "min_height": s.get("mermaid_min_height", 200),
    }


def _mermaid_key(source, opts):
    return cache_key(source, opts["theme"], opts["background"], opts["scale"])


def _mermaid_path(key, scale):
    """Cache path for a render, tagged with the scale it was actually rendered at.

    mermaid-cli honours `-s`, kroki always renders at 1x, so the factor the phantom
    must divide by is a property of the *file*, not of the current settings. Tagging it
    into the name keeps that straight without a sidecar.

    The separator is a plain `-`: `pathname2url` percent-escapes `@`, and minihtml
    takes the `src` literally, so an `@2x` name resolves to a file that doesn't exist.
    """
    return os.path.join(_cache_dir(), "mermaid-%s-%sx.png" % (key, scale))


def _cached_render(key, opts):
    """First cached render for `key`: (path, rendered_scale), or (None, None)."""
    for scale in (opts["scale"], 1):
        path = _mermaid_path(key, scale)
        if os.path.exists(path) and os.path.getsize(path) > 0:
            return path, scale
    return None, None


def _mmdc_binary():
    """Configured mermaid-cli path, else `mmdc` on the augmented PATH, else None."""
    configured = os.path.expanduser(_settings().get("mermaid_cli_path", "") or "")
    if configured:
        return configured if os.path.exists(configured) else None
    return shutil.which("mmdc", path=_mermaid_env()["PATH"])


def _run(args, cwd, timeout):
    """Run a command, returning (exit code, combined output).

    `subprocess.run` only exists from Python 3.5, and Sublime still loads plugins on
    the 3.3 host unless the package ships a `.python-version`. Popen works on both, so
    a missing or ignored version file degrades to a slow render rather than a crash.

    :returns: the process exit code and its stdout+stderr, decoded
    :rtype: tuple
    """
    proc = subprocess.Popen(args, cwd=cwd, env=_mermaid_env(),
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, _ = proc.communicate()
        raise RuntimeError("timed out after %ds" % timeout)
    return proc.returncode, (out or b"").decode("utf-8", "replace")


def _mmdc_error(output, returncode):
    """Pick the one useful line out of mermaid-cli's output.

    The interesting line is neither the first (a progress banner) nor the last (a
    puppeteer stack frame). Dropping the noise leaves the actual complaint at the top:
    "Error: Parse error on line 2:" for a bad diagram, "No version is set for command
    mmdc" when a version-manager shim intercepted the call.
    """
    noise = ("generating single mermaid chart",)
    for line in (output or "").splitlines():
        line = line.strip()
        if not line or line.lower().startswith(noise) or line.startswith("at "):
            continue
        if "node_modules" in line:   # stack frames, with or without the "at " prefix
            continue
        return line[:160]
    return "exit %d" % returncode


def _mmdc_render(binary, source, dest, opts):
    """Render `source` to `dest` with mermaid-cli. Raises RuntimeError on failure."""
    tmp_dir = tempfile.mkdtemp(prefix="markdown_rich_")
    src_path = os.path.join(tmp_dir, "diagram.mmd")
    out_path = os.path.join(tmp_dir, "diagram.png")
    try:
        with open(src_path, "w", encoding="utf-8") as f:
            f.write(source)
        args = mmdc_args(binary, src_path, out_path, opts["theme"], opts["background"], opts["scale"])
        _log("mermaid-cli: %s", " ".join(args))
        started = time.monotonic()
        code, output = _run(args, cwd=tmp_dir, timeout=120)
        _log("mermaid-cli exited %d in %s", code, _elapsed(started))
        if code != 0 or not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
            raise RuntimeError(_mmdc_error(output, code))
        # Move into the cache only once complete: a render() on another thread must
        # never find a half-written PNG at the path it is about to display.
        _log("mermaid-cli produced %d bytes -> %s", os.path.getsize(out_path), dest)
        shutil.move(out_path, dest)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _kroki_render(source, dest, theme):
    """Render `source` via the kroki endpoint. Retries a few times: 500s are transient.

    Kroki takes no theme flag, so the theme rides along inside the diagram source as an
    init directive. It has no background knob either, and doesn't need one: renders keep
    their alpha and are shown against the editor's own background.
    """
    endpoint = _settings().get("mermaid_remote_endpoint", "https://kroki.io")
    url = remote_url(endpoint, with_theme_directive(source, theme))
    last = None
    for attempt in (1, 2, 3):
        if attempt > 1:
            time.sleep(attempt - 1)   # a moment's pause: kroki 500s are transient
        _log("kroki GET %s/mermaid/png (%d byte payload, attempt %d)",
             endpoint.rstrip("/"), len(url.rsplit("/", 1)[-1]), attempt)
        started = time.monotonic()
        try:
            data = _fetch_bytes(url, timeout=60)
        except Exception as e:
            last = e
            _log("kroki failed after %s: %s", _elapsed(started), e)
            continue
        if not _png_size(data):
            last = RuntimeError("response was not a PNG")
            _log("kroki returned %d bytes that are not a PNG", len(data))
            continue
        _log("kroki ok: %d bytes in %s -> %s", len(data), _elapsed(started), dest)
        partial = dest + ".part"
        with open(partial, "wb") as f:
            f.write(data)
        os.replace(partial, dest)   # atomic: no half-written PNG is ever displayed
        return
    raise RuntimeError(str(last))


def _render_mermaid(source, key, opts):
    """Render `source` into the cache: local mermaid-cli first, kroki as fallback.

    Each backend writes to the path tagged with the scale it renders at (mermaid-cli
    honours `mermaid_scale`, kroki is always 1x).

    Raises RuntimeError describing every attempt that failed, so the annotation can
    say whether mermaid-cli was missing, errored, or the network render was refused.
    """
    problems = []
    binary = _mmdc_binary()
    _log("render %s: mermaid-cli %s", key[:12], binary or "not found")
    if binary:
        try:
            _mmdc_render(binary, source, _mermaid_path(key, opts["scale"]), opts)
            return
        except Exception as e:
            problems.append("mermaid-cli: %s" % e)
            _log("mermaid-cli failed: %s", e)
    else:
        problems.append("mermaid-cli not found")
    if _settings().get("mermaid_remote_fallback", True):
        try:
            _kroki_render(source, _mermaid_path(key, 1), opts["theme"])
            return
        except Exception as e:
            problems.append("remote render: %s" % e)
            _log("remote render failed: %s", e)
    else:
        problems.append("remote fallback disabled")
        _log("remote fallback disabled, giving up on %s", key[:12])
    raise RuntimeError("; ".join(problems))


# --- generated Markdown syntax (fenced code for every installed language) -----

DEFAULT_PARENT_SYNTAX = "Packages/Markdown/Markdown.sublime-syntax"
GENERATED_SYNTAX_NAME = "MarkdownRich"
# Extends the parent scope rather than reusing it, the way MultiMarkdown does, so
# `text.html.markdown` selectors keep matching while the two stay distinguishable.
GENERATED_SYNTAX_SCOPE = "text.html.markdown.rich"
# Written to User/ rather than the package: it is generated output, and keeping it
# out of the package leaves this repo clean.
GENERATED_SYNTAX_FILE = "MarkdownRich.sublime-syntax"
# Markdown embedded in Markdown, and plain-text-ish syntaxes, are not worth a fence.
_SYNTAX_SCOPE_SKIP = ("text.html.markdown", "text.plain")


def _parent_syntax():
    """Syntax to inherit from. Point it at MultiMarkdown to keep its metadata block."""
    return _settings().get("markdown_syntax_parent", DEFAULT_PARENT_SYNTAX)


def _generated_syntax_path():
    return os.path.join(sublime.packages_path(), "User", GENERATED_SYNTAX_FILE)


def _syntax_entries():
    """Languages the parent Markdown syntax doesn't already highlight in fences.

    Only the uncovered syntaxes have their resource read (for file extensions, which
    are the aliases people actually type), so the common case costs one resource load
    for the parent plus a handful for the stragglers.
    """
    parent = _parent_syntax()
    covered = covered_scopes(sublime.load_resource, parent)
    entries, seen = [], set()
    for syntax in sublime.list_syntaxes():
        scope = (syntax.scope or "").split()[0] if syntax.scope else ""
        if not scope or syntax.hidden or scope in seen:
            continue
        if scope.startswith(_SYNTAX_SCOPE_SKIP) or is_covered(scope, covered):
            continue
        seen.add(scope)
        try:
            extensions = extensions_of(sublime.load_resource(syntax.path))
        except Exception:
            extensions = []
        tokens = fence_tokens(syntax.name, scope, extensions)
        if tokens:
            entries.append(Entry(name=syntax.name, scope=scope, tokens=tokens))
    entries = drop_specializations(entries)
    entries.sort(key=lambda e: e.tokens[0])
    return assign_tokens(entries)


def _write_markdown_syntax():
    """Regenerate the MarkdownRich syntax. Returns (path, language_count, changed)."""
    entries = _syntax_entries()
    text = render_syntax(entries, _parent_syntax(), GENERATED_SYNTAX_NAME, GENERATED_SYNTAX_SCOPE)
    path = _generated_syntax_path()
    try:
        with open(path, encoding="utf-8") as f:
            unchanged = f.read() == text
    except OSError:
        unchanged = False
    if not unchanged:
        # Rewriting makes Sublime reload the syntax, so only touch it on a real change.
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    return path, len(entries), not unchanged


# --- per-view phantom manager ------------------------------------------------

_managers = {}


def _manager(view):
    m = _managers.get(view.id())
    if m is None:
        m = PhantomManager(view)
        _managers[view.id()] = m
    return m


class PhantomManager:
    def __init__(self, view):
        self.view = view
        self.pset = sublime.PhantomSet(view, PHANTOM_KEY)
        self.states = {}     # ordinal index -> size state
        self.visible = False
        self.count = 0
        self._fetching = set()
        self._failed = {}    # remote src -> error message (prevents refetch loop)
        self._logged_cache = set()   # sources already reported to the console (debug only)
        self._avail = {}     # ordinal index -> available size states (medium may be merged out)

    def show(self):
        self.visible = True
        self._failed.clear()
        self.render()

    def clear(self):
        self.visible = False
        self.pset.update([])
        self.view.erase_regions(STATUS_KEY)

    def toggle_visible(self):
        self.clear() if self.visible else self.show()

    def cycle(self, idx):
        self._advance(idx)
        self.render()

    def cycle_all(self):
        for i in range(self.count):
            self._advance(i)
        self.render()

    def _advance(self, idx):
        """Step to the next size within this image's available set (skips merged-out medium)."""
        avail = self._avail.get(idx, (THUMB, FIXED, ORIGINAL))
        cur = self._effective_state(idx, avail)
        self.states[idx] = avail[(avail.index(cur) + 1) % len(avail)]

    def render(self):
        if not self.visible:
            return
        items = self._scan()
        self.count = len(items)
        phantoms, regions, annotations = [], [], []
        for i, (region, src, natural) in enumerate(items):
            status, value = self._resolve(src)
            anchor = sublime.Region(region.end())
            if status == "ok":
                phantoms.append(sublime.Phantom(
                    anchor, self._image_html(i, value, natural),
                    sublime.LAYOUT_BLOCK, on_navigate=self._on_nav,
                ))
            else:
                regions.append(anchor)
                annotations.append(self._status_text(i, status, value))
        self.pset.update(phantoms)
        if regions:
            self.view.add_regions(
                STATUS_KEY, regions, "comment",
                flags=sublime.DRAW_NO_FILL | sublime.DRAW_NO_OUTLINE,
                annotations=annotations,
                annotation_color=_settings().get("status_color", "#c0863a"),
                on_navigate=self._on_nav,
            )
        else:
            self.view.erase_regions(STATUS_KEY)

    def _scan(self):
        """Find Markdown and HTML images in document order: list of (region, src, natural_or_None)."""
        found = []
        for region in self.view.find_all(IMAGE_FIND_RE):
            m = LINK_RE.search(self.view.substr(region))
            if m:
                found.append((region, m.group(2), None))
        for region in self.view.find_all(HTML_IMG_FIND_RE):
            text = self.view.substr(region)
            sm = HTML_SRC_RE.search(text)
            if not sm:
                continue
            wm, hm = HTML_W_RE.search(text), HTML_H_RE.search(text)
            natural = (int(wm.group(1)), int(hm.group(1))) if wm and hm else None
            found.append((region, sm.group(1), natural))
        found.sort(key=lambda t: t[0].begin())
        return found

    def _resolve(self, src):
        """Return (status, value): 'ok'(path) | 'loading' | 'error'(msg) | 'missing'(path) | 'disabled'."""
        if not _is_remote(src):
            path = _resolve_local(self.view, src)
            if path and os.path.exists(path):
                return ("ok", path)
            return ("missing", path or src)
        if not _settings().get("render_remote", True):
            return ("disabled", None)
        if src in self._failed:
            return ("error", self._failed[src])
        cached = _cached_path(src)
        if os.path.exists(cached) and os.path.getsize(cached) > 0:
            if src not in self._logged_cache:
                self._logged_cache.add(src)
                _log("image cache hit: %s -> %s", src, os.path.basename(cached))
            return ("ok", cached)
        self._start_fetch(src, cached)
        return ("loading", None)

    def _start_fetch(self, src, dest):
        if src in self._fetching:
            _log("image fetch already in flight: %s", src)
            return
        self._fetching.add(src)
        _log("image fetch start: %s", src)

        def worker():
            err, started = None, time.monotonic()
            try:
                data = _fetch_bytes(src)
                with open(dest, "wb") as f:
                    f.write(data)
                _log("image fetch ok: %s (%d bytes in %s) -> %s",
                     src, len(data), _elapsed(started), dest)
            except Exception as e:
                err = str(e)
                _log("image fetch failed: %s (after %s) %s", src, _elapsed(started), err)
            finally:
                self._fetching.discard(src)

            def done():
                if err:
                    self._failed[src] = err   # stop the refetch loop; show an error phantom
                self.render()

            sublime.set_timeout(done, 0)

        threading.Thread(target=worker, daemon=True).start()

    def _dims(self, state, natural):
        s = _settings()
        if state == ORIGINAL:
            return natural  # natural size, or None -> minihtml uses intrinsic
        if state == THUMB:
            cap = s.get("thumbnail_width", 180)
            cap_w = cap_h = cap
        else:
            cap_w = s.get("max_image_width", 600)
            cap_h = s.get("max_image_height", 500)
        if not natural:
            return (cap_w, None)
        w, h = natural
        ratio = min(cap_w / w, cap_h / h, 1.0)
        return (int(w * ratio), int(h * ratio))

    def _status_text(self, idx, status, value):
        """Inline annotation markup for non-image states (loading/missing/error/disabled)."""
        if status == "loading":
            return "&#8987; loading image&#8230;"
        if status == "disabled":
            return "remote images disabled"
        if status == "missing":
            return "&#9888; not found: %s" % _esc(value)
        return ('&#9888; %s &middot; <a href="mrich:retry:%d">retry</a>'
                % (_esc(value), idx))

    def _available_states(self, natural):
        """Size options to offer. Medium is dropped when it would render within
        `medium_merge_threshold` of the original width (same aspect ratio, so width
        alone decides), collapsing the toggle to thumbnail <-> original."""
        if not natural or natural[0] <= 0:
            return (THUMB, FIXED, ORIGINAL)
        orig_w = natural[0]
        med_w = self._dims(FIXED, natural)[0]
        threshold = _settings().get("medium_merge_threshold", 0.1)
        if (orig_w - med_w) / orig_w <= threshold:
            return (THUMB, ORIGINAL)
        return (THUMB, FIXED, ORIGINAL)

    def _effective_state(self, idx, available):
        """Stored/default state, snapped into the available set (merged medium -> original)."""
        st = self.states.get(idx, _default_state())
        return st if st in available else ORIGINAL

    def _image_html(self, idx, path, natural_hint=None):
        natural = natural_hint or _image_size(path)
        available = self._avail[idx] = self._available_states(natural)
        state = self._effective_state(idx, available)
        w, h = self._dims(state, natural)
        dims = (' width="%d"' % w if w else "") + (' height="%d"' % h if h else "")
        body = (
            '<div class="mr-c">'
            '<a href="mrich:%d"><img src="%s"%s></a>'
            '<div class="mr-f">%s</div>'
            '</div>'
        ) % (idx, _file_src(path), dims, self._size_footer(idx, state, available))
        return '<body>' + _PHANTOM_STYLE + body + '</body>'

    def _size_footer(self, idx, state, available):
        """Right-aligned size selector: the active size is bold, the others are links."""
        parts = []
        for st in available:
            if st == state:
                parts.append('<span class="mr-cur">%s</span>' % STATE_LABEL[st])
            else:
                parts.append('<a href="mrich:set:%d:%d">%s</a>' % (idx, st, STATE_LABEL[st]))
        return ' &middot; '.join(parts)

    def _on_nav(self, href):
        if href.startswith("mrich:set:"):
            idx_s, st_s = href[len("mrich:set:"):].split(":")
            self.states[int(idx_s)] = int(st_s)
            self.render()
        elif href.startswith("mrich:retry:"):
            self._failed.clear()
            self.render()
        elif href.startswith("mrich:"):
            self.cycle(int(href[len("mrich:"):]))


# --- full-size diagram view ---------------------------------------------------

DIAGRAM_PHANTOM_KEY = "markdown_rich_diagram"
DIAGRAM_VIEW_STYLE = (
    '<style>'
    'body { margin: 0; padding: 12px; }'
    '.mr-f { color: color(var(--foreground) alpha(0.55)); padding-top: 10px;'
    ' font-size: 0.85rem; }'
    '.mr-f a { color: color(var(--foreground) alpha(0.75)); text-decoration: none; }'
    '.mr-cur { color: var(--foreground); font-weight: bold; }'
    '</style>'
)


def _view_width(view):
    """Usable pixel width of a view, or a sane guess when it can't be measured.

    A freshly created view reports a zero-size viewport until Sublime lays it out,
    which would otherwise fit a diagram to nothing.
    """
    width = int(view.viewport_extent()[0]) if view is not None else 0
    return max(240, width - 40) if width > 100 else 800


def _diagram_target(view, path, fit):
    """Pixel size for the diagram tab: fitted to the view, or the render's own size.

    "Actual size" means the pixels the renderer produced, not those divided by
    `mermaid_scale`: a 2x render shown at 2x is the sharp, readable one, and dividing
    would only reproduce what the inline phantom already shows.
    """
    natural = _image_size(path)
    if not natural:
        return (None, None)
    if not fit:
        return natural
    w, h = natural
    ratio = min(_view_width(view) / w, 1.0)
    return (int(w * ratio), int(h * ratio))


def _full_diagram_html(view, path, fit):
    """The diagram, with a size toggle. The dimensions belong to "actual size", so they
    hang off that label rather than reading as a third option."""
    w, h = _diagram_target(view, path, fit)
    natural = _image_size(path) or (0, 0)
    dims = (' width="%d"' % w if w else "") + (' height="%d"' % h if h else "")
    labels = []
    for mode, label in ((True, "fit"), (False, "actual size (%d&#215;%d)" % natural)):
        if mode == fit:
            labels.append('<span class="mr-cur">%s</span>' % label)
        else:
            labels.append('<a href="%s">%s</a>' % ("fit" if mode else "actual", label))
    return ('<body>' + DIAGRAM_VIEW_STYLE +
            '<div><img src="%s"%s></div>'
            '<div class="mr-f">%s</div></body>'
            % (_data_src(path), dims, ' &middot; '.join(labels)))


def _render_diagram_view(view, path, fit, force=False):
    """(Re)draw the diagram phantom at the requested size.

    Safe to redraw now that the image is embedded rather than loaded: a `file://`
    image arrives asynchronously, and minihtml fills the gap with its broken-image
    glyph stretched to the full size of the tag.
    """
    settings = view.settings()
    if not force and settings.get("markdown_rich_diagram_fit") == fit \
            and settings.get("markdown_rich_diagram_id"):
        return
    previous = settings.get("markdown_rich_diagram_id")
    phantom_id = view.add_phantom(
        DIAGRAM_PHANTOM_KEY, sublime.Region(0), _full_diagram_html(view, path, fit),
        sublime.LAYOUT_BLOCK,
        on_navigate=lambda href: _render_diagram_view(view, path, href == "fit"),
    )
    if previous:
        view.erase_phantom_by_id(previous)
    settings.set("markdown_rich_diagram_id", phantom_id)
    settings.set("markdown_rich_diagram_fit", fit)


#: Open diagram tabs, by view id: which document and which block each one follows.
_diagram_views = {}


def _sync_diagram_views(origin, blocks, opts):
    """Repoint open diagram tabs at the newest render of the block they follow.

    A tab tracks a block's position in the document rather than a file, because
    editing a diagram gives it a new content hash and so an entirely different cache
    entry. Without this the tab would keep showing the render you opened it with.
    """
    for view_id, info in list(_diagram_views.items()):
        view = sublime.View(view_id)
        if not view.is_valid():
            _diagram_views.pop(view_id, None)
            continue
        if info["origin"] != origin.id() or info["ordinal"] >= len(blocks):
            continue
        key = _mermaid_key(blocks[info["ordinal"]].source, opts)
        path, _ = _cached_render(key, opts)
        if path and path != info["path"]:
            # Only once the new render exists: until then the old one is better than
            # an empty tab.
            info["path"] = path
            _log("diagram tab follows an edit -> %s", os.path.basename(path))
            _render_diagram_view(view, path,
                                 view.settings().get("markdown_rich_diagram_fit", True),
                                 force=True)


def _find_diagram_view(origin, ordinal):
    """An open tab already following this block, or None."""
    for view_id, info in list(_diagram_views.items()):
        if info["origin"] != origin.id() or info["ordinal"] != ordinal:
            continue
        view = sublime.View(view_id)
        if view.is_valid():
            return view
        _diagram_views.pop(view_id, None)
    return None


def _reveal_diagram_view(view, origin):
    """Bring a diagram tab to the front, beside the document it belongs to."""
    window = view.window() or origin.window()
    if window is None:
        return
    window.focus_view(view)
    if _settings().get("open_link_side_by_side", True):
        _reveal_beside(window, origin, view)


def _open_diagram_view(window, path, origin, ordinal):
    """A scratch tab showing one diagram, on the editor's own background."""
    view = window.new_file()
    view.set_scratch(True)
    view.set_read_only(True)     # a diagram tab is for looking at, not typing into
    view.set_name("Diagram")
    settings = view.settings()
    for key, value in (("gutter", False), ("line_numbers", False), ("word_wrap", False),
                       ("draw_indent_guides", False), ("highlight_line", False),
                       ("scroll_past_end", False), ("draw_white_space", "none")):
        settings.set(key, value)
    # Pad once, to the widest the diagram will ever be drawn: phantoms add vertical
    # layout but no horizontal extent, so a wide image would otherwise be cut off, and
    # editing the buffer later would disturb the phantom anchored to it.
    natural = _image_size(path) or (0, 0)
    em = getattr(view, "em_width", lambda: 8.0)() or 8.0
    view.run_command("markdown_rich_pad_view", {"columns": int(natural[0] / em) + 2})
    _diagram_views[view.id()] = {"origin": origin.id(), "ordinal": ordinal, "path": path}
    # The view has no size until Sublime lays it out, so "fit" has to wait for it.
    sublime.set_timeout(lambda: _render_diagram_view(view, path, fit=True), 50)
    return view


# --- per-view mermaid manager ------------------------------------------------

_mermaid_managers = {}


def _mermaid(view):
    m = _mermaid_managers.get(view.id())
    if m is None:
        m = MermaidManager(view)
        _mermaid_managers[view.id()] = m
    return m


class MermaidManager:
    """Folds ```mermaid blocks and renders the diagram in their place.

    State is keyed by the block's cache key (content + render options) rather than its
    ordinal, so toggling one block to source survives edits elsewhere in the document,
    and editing a diagram naturally starts it back at the rendered state.
    """

    def __init__(self, view):
        self.view = view
        self.pset = sublime.PhantomSet(view, MERMAID_PHANTOM_KEY)
        self.as_source = set()   # cache keys the user switched to source view
        self._rendering = set()
        self._failed = {}        # cache key -> error message
        self._caret_keys = frozenset()
        self._logged = set()     # keys already reported to the console (debug only)
        self._summary = None

    def clear(self):
        """Drop every diagram phantom and reveal all folded blocks."""
        self.pset.update([])
        self.view.erase_regions(MERMAID_STATUS_KEY)
        for b in self._blocks():
            self.view.unfold(sublime.Region(b.body_start, b.end))

    def toggle_all(self):
        """Flip the whole document: any diagram showing -> all source, else all diagrams."""
        opts = _mermaid_opts(self.view)
        keys = [_mermaid_key(b.source, opts) for b in self._blocks()]
        if not keys:
            sublime.status_message("MarkdownRich: no mermaid blocks")
            return
        if any(k not in self.as_source for k in keys):
            self.as_source.update(keys)
        else:
            self.as_source.difference_update(keys)
        self.render()

    def render(self, allow_render=True):
        """Draw the diagrams. `allow_render` gates *starting* new renders.

        Rendering is tied to saving: a diagram costs a subprocess or a network round
        trip, and rendering every pause in typing spends both on text that is still
        being written. Moving the caret out of an edited block therefore leaves it
        showing its source until you save, or until you ask for the diagram directly.
        """
        view = self.view
        if not _settings().get("render_mermaid", True):
            self.clear()
            return
        opts = _mermaid_opts(self.view)
        blocks = self._blocks()
        self._caret_keys = self._keys_at_caret(blocks, opts)
        phantoms, regions, annotations = [], [], []
        for ordinal, b in enumerate(blocks):
            key = _mermaid_key(b.source, opts)
            fold = sublime.Region(b.body_start, b.end)
            anchor = sublime.Region(b.start)
            # Every state annotates the same spot, the end of the fence line, so the
            # control never moves: only its label changes with the state.
            regions.append(anchor)
            # A block with its own tab open stays on source: the diagram is already
            # on screen beside it. Tracked by position, not by cache key, so it
            # survives edits, which give the block an entirely new key.
            followed = _find_diagram_view(view, ordinal) is not None
            if followed or key in self.as_source or key in self._caret_keys:
                view.unfold(fold)
                annotations.append('<a href="mmd:img:%s">Show diagram</a>' % key)
                # Keep the render coming even though the block shows source: the tab
                # beside it is the whole point of editing this way.
                if allow_render and followed and _cached_render(key, opts)[0] is None:
                    self._start_render(b.source, key, opts)
                continue
            if key in self._failed:
                view.unfold(fold)
                annotations.append('&#9888; %s &middot; <a href="mmd:retry:%s">retry</a>'
                                   % (_esc(self._failed[key]), key))
                continue
            path, rendered_scale = _cached_render(key, opts)
            self._log_block(b, key, path)
            if path is None:
                view.unfold(fold)
                if allow_render:
                    annotations.append("&#8987; rendering diagram&#8230;")
                    self._start_render(b.source, key, opts)
                else:
                    annotations.append('<a href="mmd:img:%s">Show diagram</a>' % key)
                continue
            view.fold(fold)
            annotations.append('<a href="mmd:src:%s">Show source</a> &middot; '
                               '<a href="mmd:open:%s">Open image</a>' % (key, key))
            phantoms.append(sublime.Phantom(
                anchor, self._diagram_html(key, path, rendered_scale, opts),
                sublime.LAYOUT_BLOCK, on_navigate=self._on_nav,
            ))
        self.pset.update(phantoms)
        summary = (len(blocks), len(phantoms), len(self._failed), len(self._rendering))
        if summary != self._summary:
            self._summary = summary
            _log("view has %d diagram(s): %d shown, %d failed, %d rendering", *summary)
        if regions:
            view.add_regions(
                MERMAID_STATUS_KEY, regions, "comment",
                flags=sublime.DRAW_NO_FILL | sublime.DRAW_NO_OUTLINE,
                annotations=annotations,
                annotation_color=_settings().get("status_color", "#c0863a"),
                on_navigate=self._on_nav,
            )
        else:
            view.erase_regions(MERMAID_STATUS_KEY)
        _sync_diagram_views(view, blocks, opts)

    def _log_block(self, block, key, path):
        """Report a block's cache identity once, so an edit that didn't take is visible.

        An edited diagram gets a new key; if the console shows the same key after a
        change, the edit never reached the block being rendered.
        """
        if key in self._logged:
            return
        self._logged.add(key)
        _log("block at line %d: key=%s (%d chars) cache=%s",
             self.view.rowcol(block.start)[0] + 1, key[:12], len(block.source),
             os.path.basename(path) if path else "miss")

    def sync_caret(self):
        """Re-render only when the set of blocks holding the caret changed.

        A block under the caret shows its source so it stays editable (a folded region
        can't be typed into); moving away folds it back to the diagram.
        """
        opts = _mermaid_opts(self.view)
        blocks = self._blocks()
        keys = self._keys_at_caret(blocks, opts)
        if keys != self._caret_keys:
            self.render(allow_render=False)

    def _blocks(self):
        """Fenced mermaid blocks, re-scanned only when the buffer actually changed.

        Caret moves re-run this on every settled selection change, and the scan reads
        the whole buffer, so it's memoized on `change_count`.
        """
        count = self.view.change_count()
        if count != getattr(self, "_blocks_at", None):
            self._blocks_cache = find_blocks(self.view.substr(sublime.Region(0, self.view.size())))
            self._blocks_at = count
        return self._blocks_cache

    def _keys_at_caret(self, blocks, opts):
        """Keys of blocks whose *body* holds a caret. The fence line is excluded: it
        stays visible when folded, and parking the caret there is how a block folds
        back after editing it."""
        points = [r.begin() for r in self.view.sel()] + [r.end() for r in self.view.sel()]
        return frozenset(
            _mermaid_key(b.source, opts) for b in blocks
            if any(b.body_start <= p <= b.end for p in points)
        )

    def _start_render(self, source, key, opts):
        if key in self._rendering:
            _log("diagram %s already rendering, skipping duplicate request", key[:12])
            return
        self._rendering.add(key)
        _log("diagram %s queued (%d chars, theme=%s bg=%s scale=%s)",
             key[:12], len(source), opts["theme"], opts["background"], opts["scale"])

        def worker():
            err, started = None, time.monotonic()
            try:
                _render_mermaid(source, key, opts)
                _log("diagram %s rendered in %s", key[:12], _elapsed(started))
            except Exception as e:
                err = str(e)
                _log("diagram %s failed after %s: %s", key[:12], _elapsed(started), err)
            finally:
                self._rendering.discard(key)

            def done():
                if err:
                    self._failed[key] = err   # stop the re-render loop; offer a retry
                self.render()

            sublime.set_timeout(done, 0)

        threading.Thread(target=worker, daemon=True).start()

    def _diagram_html(self, key, path, rendered_scale, opts):
        """The diagram alone. The state control lives in the fence-line annotation, so
        the phantom carries no footer; clicking the image is just a shortcut for it."""
        w, h = display_size(_image_size(path), rendered_scale,
                            opts["max_width"], opts["min_height"])
        dims = (' width="%d"' % w if w else "") + (' height="%d"' % h if h else "")
        body = (
            '<div class="mr-c">'
            '<a href="mmd:src:%s"><img src="%s"%s></a>'
            '</div>'
        ) % (key, _data_src(path), dims)
        return '<body>' + _PHANTOM_STYLE + body + '</body>'

    def _on_nav(self, href):
        action, _, key = href[len("mmd:"):].partition(":")
        if action == "open":
            self.open_image(key)
            return
        if action == "src":
            self.as_source.add(key)
        elif action == "img":
            # With a tab already open the block stays on source, so re-rendering would
            # change nothing on screen. The diagram being asked for is in that tab.
            ordinal = self._ordinal_of(key, _mermaid_opts(self.view))
            existing = _find_diagram_view(self.view, ordinal) if ordinal is not None else None
            if existing is not None:
                _log("diagram %s is open, revealing its tab", key[:12])
                _reveal_diagram_view(existing, self.view)
                return
            self.as_source.discard(key)
            self._park_caret_outside(key)
        elif action == "retry":
            self._failed.pop(key, None)
        self.render()

    def _ordinal_of(self, key, opts):
        """Position of the block with this cache key, or None."""
        for i, block in enumerate(self._blocks()):
            if _mermaid_key(block.source, opts) == key:
                return i
        return None

    def open_image(self, key=None):
        """Show a diagram full size in its own tab.

        Not `open_file`: Sublime's image viewer draws a checkerboard behind transparent
        pixels, which is exactly what a diagram is made of once the background is left
        to the editor. A scratch view holding one phantom renders it the same way the
        inline one does, against the color scheme's background, alpha intact.
        """
        opts = _mermaid_opts(self.view)
        if key is None:
            block = block_at(self._blocks(), self.view.sel()[0].begin()) if self.view.sel() else None
            if block is None:
                sublime.status_message("MarkdownRich: no mermaid block at the cursor")
                return
            key = _mermaid_key(block.source, opts)
        path, _ = _cached_render(key, opts)
        if path is None:
            sublime.status_message("MarkdownRich: that diagram hasn't rendered yet")
            return
        window = self.view.window()
        if window is None:
            return
        _log("opening diagram %s in its own tab: %s", key[:12], path)
        # The diagram is now in a tab of its own, so the block is more useful showing
        # its source: the two end up side by side rather than duplicating each other.
        self.as_source.add(key)
        self.render()
        ordinal = self._ordinal_of(key, opts)
        if ordinal is None:
            return
        existing = _find_diagram_view(self.view, ordinal)
        if existing is not None:
            # Asking twice for the same diagram means "show me that", not "give me
            # another copy of it".
            _log("diagram %s already open, focusing its tab", key[:12])
            info = _diagram_views[existing.id()]
            if info["path"] != path:
                info["path"] = path
                _render_diagram_view(existing, path,
                                     existing.settings().get("markdown_rich_diagram_fit", True),
                                     force=True)
            _reveal_diagram_view(existing, self.view)
            return
        opened = _open_diagram_view(window, path, self.view, ordinal)
        _reveal_diagram_view(opened, self.view)

    def _park_caret_outside(self, key):
        """Move a caret sitting in this block's body up to its fence line.

        Without this, asking for the diagram while editing the block would do nothing
        visible: the caret would keep the block unfolded on the very next render.
        """
        opts = _mermaid_opts(self.view)
        for b in self._blocks():
            if _mermaid_key(b.source, opts) != key:
                continue
            if any(b.body_start <= r.begin() <= b.end for r in self.view.sel()):
                self.view.sel().clear()
                self.view.sel().add(sublime.Region(b.start))
            return


# --- listeners / commands ----------------------------------------------------

class MarkdownRichImages(sublime_plugin.ViewEventListener):
    @classmethod
    def is_applicable(cls, settings):
        return "Markdown" in (settings.get("syntax") or "")

    def _debounce(self, name, delay, fn):
        """Run `fn` once a burst of events settles (last call within `delay` wins)."""
        attr = "_token_" + name
        token = getattr(self, attr, 0) + 1
        setattr(self, attr, token)

        def fire():
            if token == getattr(self, attr, 0):
                fn()

        sublime.set_timeout(fire, delay)

    def on_load_async(self):
        _style_section_refs(self.view)
        _mermaid(self.view).render()
        if _settings().get("auto_show_on_load", True):
            _manager(self.view).show()

    def on_activated_async(self):
        # Covers buffers already open when the plugin loaded, and tab focus.
        _style_section_refs(self.view)
        _mermaid(self.view).render()
        m = _manager(self.view)
        if not m.visible and _settings().get("auto_show_on_load", True):
            m.show()

    def on_modified_async(self):
        # Section-ref styling tracks edits live (like real link highlighting would).
        # Debounced so a burst of keystrokes only restyles once things settle.
        self._debounce("section", 120, lambda: _style_section_refs(self.view))

    def on_selection_modified_async(self):
        self._debounce("mermaid_caret", 200, lambda: _mermaid(self.view).sync_caret())

    def on_post_save_async(self):
        _style_section_refs(self.view)
        _mermaid(self.view).render()
        m = _manager(self.view)
        if m.visible:
            m.render()

    def on_hover(self, point, hover_zone):
        """Preview a §-ref's destination, and offer it as a link (no caret needed)."""
        if hover_zone != sublime.HOVER_TEXT:
            return
        if not _settings().get("section_ref_popup", True):
            return
        view = self.view
        if not view.match_selector(point, MARKDOWN_SELECTOR):
            return
        label = _section_ref_at(view, point)
        if label is not None:
            _section_popup(view, point, label)

    def on_query_context(self, key, operator, operand, match_all):
        """Answer the ctrl+enter keymap: is the caret on a §-ref? (markdown only)."""
        if key != SECTION_CONTEXT_KEY:
            return None
        view = self.view
        on_ref = (
            len(view.sel()) > 0
            and view.match_selector(view.sel()[0].begin(), MARKDOWN_SELECTOR)
            and _section_ref_at(view, view.sel()[0].begin()) is not None
        )
        if operator == sublime.OP_EQUAL:
            return on_ref == bool(operand)
        if operator == sublime.OP_NOT_EQUAL:
            return on_ref != bool(operand)
        return None

    def on_close(self):
        _managers.pop(self.view.id(), None)
        _mermaid_managers.pop(self.view.id(), None)


class MarkdownRichToggleMermaidCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        _mermaid(self.view).toggle_all()


class MarkdownRichRebuildSyntaxCommand(sublime_plugin.ApplicationCommand):
    """Regenerate the MarkdownRich syntax from the currently installed syntaxes."""

    def run(self):
        try:
            _, count, changed = _write_markdown_syntax()
        except Exception as e:
            sublime.error_message("MarkdownRich: could not write the syntax\n\n%s" % e)
            return
        sublime.status_message(
            "MarkdownRich: %s syntax with %d fenced language(s)"
            % ("rebuilt" if changed else "unchanged", count))


class MarkdownRichOpenDiagramCommand(sublime_plugin.TextCommand):
    """Open the rendered image of the diagram under the cursor in its own tab."""

    def run(self, edit):
        _mermaid(self.view).open_image()

    def is_enabled(self):
        return "Markdown" in (self.view.settings().get("syntax") or "")


class MarkdownRichDiagramTabs(sublime_plugin.EventListener):
    """Forgets diagram tabs as they close, so they stop being followed."""

    def on_close(self, view):
        _diagram_views.pop(view.id(), None)


class MarkdownRichPadViewCommand(sublime_plugin.TextCommand):
    """Widen a diagram view with blank columns, so a full-size image can be scrolled to.

    Only ever appends. Rewriting the whole buffer would edit the text the phantom is
    anchored to, dropping it mid-redraw, which is what produced a flash of broken
    image. Leftover width when switching back to a smaller size is just scroll room.
    """

    def run(self, edit, columns):
        missing = max(columns, 1) - self.view.size()
        if missing <= 0:
            return
        self.view.set_read_only(False)
        self.view.insert(edit, self.view.size(), " " * missing)
        self.view.set_read_only(True)


class MarkdownRichToggleDebugCommand(sublime_plugin.ApplicationCommand):
    """Flip console logging on or off without opening the settings file."""

    def run(self):
        settings = _settings()
        enabled = not settings.get("debug", False)
        settings.set("debug", enabled)
        sublime.save_settings(SETTINGS_FILE)
        sublime.status_message("MarkdownRich: debug logging %s"
                               % ("on, see the console" if enabled else "off"))

    def is_checked(self):
        return bool(_settings().get("debug", False))


class MarkdownRichClearCacheCommand(sublime_plugin.TextCommand):
    """Drop cached downloads/renders and rebuild every open Markdown view.

    The escape hatch for a failure that isn't the document's fault: a transient fetch
    error, a diagram rendered before mermaid-cli was reachable, a stale cache entry.
    """

    def run(self, edit):
        removed = _clear_cache()
        for window in sublime.windows():
            for view in window.views():
                if "Markdown" not in (view.settings().get("syntax") or ""):
                    continue
                m = _manager(view)
                m._failed.clear()
                if m.visible:
                    m.render()
                mm = _mermaid(view)
                mm._failed.clear()
                mm.render()
        sublime.status_message("MarkdownRich: cleared %d cached file(s), re-rendering" % removed)


class MarkdownRichShowImagesCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        _manager(self.view).show()


class MarkdownRichClearImagesCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        _manager(self.view).clear()


class MarkdownRichToggleImagesCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        _manager(self.view).toggle_visible()


class MarkdownRichCycleAllImagesCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        _manager(self.view).cycle_all()


class MarkdownRichOpenLinkCommand(sublime_plugin.TextCommand):
    """Open the link/url under the cursor; no-op otherwise (preserves double-click word-select)."""

    def run(self, edit, side_by_side=None):
        view = self.view
        if not len(view.sel()):
            return
        point = view.sel()[0].begin()
        if not view.match_selector(point, MARKDOWN_SELECTOR):
            return
        target = _target_at(view, point)
        if target:
            self._open_target(view, target[1], side_by_side)
            return
        label = _section_ref_at(view, point)
        if label is not None:
            self._jump_to_section(view, label)

    def _open_target(self, view, url, side_by_side):
        if re.match(r'^(https?|ftp|mailto):', url, re.I):
            webbrowser.open(url)
            return
        file_ref, line, col = _split_position(url)
        path = _resolve_local(view, file_ref)
        if not (path and os.path.exists(path)):
            sublime.status_message("MarkdownRich: cannot open " + url)
            return
        if side_by_side is None:
            side_by_side = _settings().get("open_link_side_by_side", True)
        self._open(path, line, col, side_by_side)

    def _jump_to_section(self, view, label):
        """Move the caret to the numbered heading a `§N` reference points at."""
        region = _section_target(view, label)
        if region is None:
            sublime.status_message("MarkdownRich: no section " + label)
            return
        _goto_region(view, region)

    def _open(self, path, line, col, side_by_side):
        window = self.view.window()
        loc, flags = path, 0
        if line is not None:
            loc = "%s:%d" % (path, line)
            if col is not None:
                loc += ":%d" % col
            flags = sublime.ENCODED_POSITION
        new_view = window.open_file(loc, flags)
        if side_by_side:
            _reveal_beside(window, self.view, new_view)


def plugin_loaded():
    """Style refs + render images in already-open Markdown buffers (plugin reload / session restore)."""
    sublime.set_timeout_async(_show_open_markdown_views, 100)
    sublime.set_timeout_async(_refresh_markdown_syntax, 500)


def _refresh_markdown_syntax():
    """Keep the generated syntax in step with the installed syntaxes.

    Runs on every load because packages come and go between sessions; the file is only
    written when its contents actually differ, so a steady setup stays untouched.
    """
    if not _settings().get("generate_markdown_syntax", True):
        return
    try:
        _, count, changed = _write_markdown_syntax()
    except Exception as e:
        print("MarkdownRich: syntax generation failed: %s" % e)
        return
    if changed:
        print("MarkdownRich: rebuilt %s with %d fenced language(s)"
              % (GENERATED_SYNTAX_FILE, count))


def _show_open_markdown_views():
    auto_show = _settings().get("auto_show_on_load", True)
    for window in sublime.windows():
        for view in window.views():
            if view.is_loading():
                continue
            if "Markdown" not in (view.settings().get("syntax") or ""):
                continue
            _style_section_refs(view)   # always-on, independent of image auto-show
            _mermaid(view).render()
            if auto_show:
                _manager(view).show()
