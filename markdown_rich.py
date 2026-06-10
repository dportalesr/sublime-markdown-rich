"""MarkdownRich: inline image phantoms with a size toggle, plus double-click link opening."""

import os
import re
import hashlib
import tempfile
import threading
import urllib.request
import urllib.parse
import webbrowser

import sublime
import sublime_plugin

SETTINGS_FILE = "MarkdownRich.sublime-settings"
MARKDOWN_SELECTOR = "text.html.markdown"
PHANTOM_KEY = "markdown_rich_images"
STATUS_KEY = "markdown_rich_status"

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


def _fetch_bytes(src):
    req = urllib.request.Request(src, headers={"User-Agent": "SublimeMarkdownRich"})
    token = _github_token()
    if token and _is_github_host(src):
        req.add_header("Authorization", "Bearer " + token)
    with _OPENER.open(req, timeout=15) as resp:
        return resp.read()


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
            return ("ok", cached)
        self._start_fetch(src, cached)
        return ("loading", None)

    def _start_fetch(self, src, dest):
        if src in self._fetching:
            return
        self._fetching.add(src)

        def worker():
            err = None
            try:
                data = _fetch_bytes(src)
                with open(dest, "wb") as f:
                    f.write(data)
            except Exception as e:
                err = str(e)
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


# --- listeners / commands ----------------------------------------------------

class MarkdownRichImages(sublime_plugin.ViewEventListener):
    @classmethod
    def is_applicable(cls, settings):
        return "Markdown" in (settings.get("syntax") or "")

    def on_load_async(self):
        if _settings().get("auto_show_on_load", True):
            _manager(self.view).show()

    def on_activated_async(self):
        # Covers buffers already open when the plugin loaded, and tab focus.
        m = _manager(self.view)
        if not m.visible and _settings().get("auto_show_on_load", True):
            m.show()

    def on_post_save_async(self):
        m = _manager(self.view)
        if m.visible:
            m.render()

    def on_close(self):
        _managers.pop(self.view.id(), None)


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

    def run(self, edit):
        view = self.view
        if not len(view.sel()):
            return
        point = view.sel()[0].begin()
        if not view.match_selector(point, MARKDOWN_SELECTOR):
            return
        target = _target_at(view, point)
        if not target:
            return
        url = target[1]
        if re.match(r'^(https?|ftp|mailto):', url, re.I):
            webbrowser.open(url)
        else:
            path = _resolve_local(view, url)
            if path and os.path.exists(path):
                view.window().open_file(path)
            else:
                sublime.status_message("MarkdownRich: cannot open " + url)


def plugin_loaded():
    """Render images in already-open Markdown buffers (plugin reload, or session restore at startup)."""
    sublime.set_timeout_async(_show_open_markdown_views, 100)


def _show_open_markdown_views():
    if not _settings().get("auto_show_on_load", True):
        return
    for window in sublime.windows():
        for view in window.views():
            if view.is_loading():
                continue
            if "Markdown" in (view.settings().get("syntax") or ""):
                _manager(view).show()
