"""Pure logic for mermaid diagram blocks: no ``sublime`` import, so it stays unit-testable.

Scanning fenced blocks, naming cache entries, and building the two render invocations
(local ``mmdc``, remote kroki GET) are all plain string work. ``markdown_rich.py``
supplies the view/phantom/fold plumbing and the threads around them.
"""

import base64
import collections
import hashlib
import re
import zlib

# Opening fence: up to 3 spaces, a run of >=3 backticks or tildes, then the info string.
_OPEN_RE = re.compile(r'^ {0,3}(`{3,}|~{3,})[ \t]*(\S*)')
LANGUAGE = "mermaid"

#: A fenced mermaid block.
#:
#: ``start``      offset of the opening fence line's first character
#: ``body_start`` offset just past the opening fence line (the newline before the body),
#:                so folding ``body_start..end`` keeps the fence line visible as a handle
#: ``end``        offset of the closing fence line's last character
#: ``source``     the diagram text between the fences, verbatim
Block = collections.namedtuple("Block", "start body_start end source")


def find_blocks(text):
    """Return every fenced mermaid block in ``text``, in document order.

    Fences are walked in sequence, so a mermaid fence nested inside a longer outer
    fence (a ````` ````markdown ````` sample, say) is content and not a diagram. An
    unterminated fence yields nothing: a half-typed block has no closing fence yet.

    :param str text: full buffer text
    :returns: list of :class:`Block`, possibly empty
    :rtype: list
    """
    lines = text.split("\n")
    offsets, pos = [], 0
    for line in lines:
        offsets.append(pos)
        pos += len(line) + 1

    blocks, i = [], 0
    while i < len(lines):
        m = _OPEN_RE.match(lines[i])
        if not m:
            i += 1
            continue
        fence, info = m.group(1), m.group(2)
        close_re = re.compile(r'^ {0,3}%s{%d,}[ \t]*$' % (re.escape(fence[0]), len(fence)))
        j = next((k for k in range(i + 1, len(lines)) if close_re.match(lines[k])), None)
        if j is None:
            break   # unterminated fence swallows the rest of the document
        if info.lower() == LANGUAGE:
            blocks.append(Block(
                start=offsets[i],
                body_start=offsets[i] + len(lines[i]),
                end=offsets[j] + len(lines[j]),
                source="\n".join(lines[i + 1:j]),
            ))
        i = j + 1
    return blocks


def cache_key(source, theme, background, scale):
    """Content address for a rendered diagram: same inputs, same PNG.

    :param str source: diagram text
    :param str theme: mermaid theme name
    :param str background: background color passed to the renderer
    :param scale: device pixel ratio the diagram is rendered at
    :returns: hex digest usable as a filename stem
    :rtype: str
    """
    payload = "\x00".join([source, theme, background, str(scale)])
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def remote_url(endpoint, source):
    """Kroki GET URL for a PNG render of ``source`` (deflate + base64url payload).

    :param str endpoint: kroki base url, with or without a trailing slash
    :param str source: diagram text
    :returns: fully-formed url
    :rtype: str
    """
    payload = base64.urlsafe_b64encode(zlib.compress(source.encode("utf-8"), 9)).decode("ascii")
    return "%s/mermaid/png/%s" % (endpoint.rstrip("/"), payload)


def mmdc_args(binary, in_path, out_path, theme, background, scale, width=None):
    """Argument vector for a local mermaid-cli render.

    :param str binary: path to (or name of) the ``mmdc`` executable
    :param str in_path: temp file holding the diagram source
    :param str out_path: PNG destination
    :param str theme: mermaid theme name
    :param str background: background color (``transparent``, ``white``, ...)
    :param scale: device pixel ratio to render at
    :param width: optional pixel width passed to mermaid-cli
    :returns: argv list for ``subprocess``
    :rtype: list
    """
    args = [binary, "-i", in_path, "-o", out_path,
            "-t", str(theme), "-b", str(background), "-s", str(scale)]
    if width:
        args += ["-w", str(width)]
    return args


_HEX_RE = re.compile(r'^#?([0-9a-fA-F]{3,8})$')
#: Perceived brightness weights (ITU-R BT.601), the usual light/dark test.
_LUMA = (0.299, 0.587, 0.114)


def luminance(color):
    """Perceived brightness of a hex color, 0.0 (black) to 1.0 (white).

    :param color: ``#rgb``, ``#rrggbb`` or ``#rrggbbaa`` (alpha ignored), or None
    :returns: brightness, or None when the color isn't hex (a named color, say)
    :rtype: float or None
    """
    m = _HEX_RE.match(color or "")
    if not m:
        return None
    digits = m.group(1)
    if len(digits) in (3, 4):
        digits = "".join(c * 2 for c in digits)
    if len(digits) not in (6, 8):
        return None
    rgb = [int(digits[i:i + 2], 16) / 255.0 for i in (0, 2, 4)]
    return sum(w * c for w, c in zip(_LUMA, rgb))


def auto_theme(background):
    """Mermaid theme that stays legible against ``background``.

    A diagram drawn on the editor's own background needs its ink to contrast with it,
    so a dark color scheme gets mermaid's ``dark`` theme and everything else (light
    schemes, unparseable colors) gets ``default``.

    :param background: editor background color
    :returns: ``"dark"`` or ``"default"``
    :rtype: str
    """
    lum = luminance(background)
    return "dark" if lum is not None and lum < 0.5 else "default"


def with_theme_directive(source, theme):
    """Bake ``theme`` into the diagram source with a ``%%{init}%%`` directive.

    Needed for renderers driven purely by the source (kroki has no theme flag). A
    source that already carries its own init directive is left untouched, as is the
    ``default`` theme, which is what mermaid picks anyway.

    :param str source: diagram text
    :param str theme: mermaid theme name
    :returns: source, prefixed with an init directive when one is warranted
    :rtype: str
    """
    if theme == "default" or source.lstrip().startswith("%%{"):
        return source
    return '%%{init: {"theme": "' + theme + '"}}%%\n' + source


def display_size(natural, scale, max_width):
    """Logical display size for a diagram rendered at ``scale`` device pixels.

    The render is deliberately oversampled (2x by default) so the phantom looks crisp;
    dividing by the same factor gives the size it should actually occupy. The result is
    then capped to ``max_width``, keeping the aspect ratio, and is never upscaled.

    :param natural: ``(width, height)`` of the rendered PNG, or None when unknown
    :param scale: device pixel ratio used for the render
    :param max_width: pixel cap for the displayed width
    :returns: ``(width, height)``, or ``(None, None)`` when ``natural`` is unknown
    :rtype: tuple
    """
    if not natural:
        return (None, None)
    w, h = natural
    factor = float(scale) if scale else 1.0
    w, h = w / factor, h / factor
    ratio = min(max_width / w, 1.0) if w else 1.0
    return (int(w * ratio), int(h * ratio))
