"""Render the same context as a screenshot, for the multimodal comparison.

Snap takes images as well as text, so the honest way to test that is to hold the
information constant and change only the modality: the identical code and
failure, once as `state` text and once as pixels. Any accuracy difference is
then attributable to the modality rather than to a different prompt.

This renders a terminal-style PNG with a monospace font, which is what a
developer would actually paste into a tool -- a screenshot of their editor or
test output.
"""

from __future__ import annotations

import base64
import io
import os

from PIL import Image, ImageDraw, ImageFont

BG = (24, 24, 27)
FG = (228, 228, 231)
DIM = (113, 113, 122)
ACCENT = (250, 204, 21)
PAD = 18
LINE_H = 17
FONT_SIZE = 13
MAX_WIDTH_CHARS = 110
MAX_LINES = 160

# Monospace fonts that actually exist on macOS and common Linux images.
FONT_CANDIDATES = (
    "/System/Library/Fonts/Menlo.ttc",
    "/System/Library/Fonts/SFNSMono.ttf",
    "/Library/Fonts/Courier New.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
)


def _font(size: int = FONT_SIZE):
    for path in FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    # Bitmap fallback: ugly but never raises, so a missing font cannot break a run.
    return ImageFont.load_default()


def _wrap(lines: list[str], width: int = MAX_WIDTH_CHARS) -> list[str]:
    out: list[str] = []
    for ln in lines:
        if len(ln) <= width:
            out.append(ln)
        else:
            while ln:
                out.append(ln[:width])
                ln = ln[width:]
    return out


def render_text(content: str, title: str = "") -> bytes:
    """Terminal-style PNG of `content`. Returns raw PNG bytes."""
    lines = _wrap(content.splitlines())
    if title:
        lines = [title, "-" * min(len(title), MAX_WIDTH_CHARS), ""] + lines
    truncated = len(lines) > MAX_LINES
    if truncated:
        lines = lines[:MAX_LINES] + ["", f"... {len(_wrap(content.splitlines())) - MAX_LINES} more lines"]

    font = _font()
    width = PAD * 2 + int(MAX_WIDTH_CHARS * FONT_SIZE * 0.61)
    height = PAD * 2 + LINE_H * max(len(lines), 1)

    img = Image.new("RGB", (width, height), BG)
    draw = ImageDraw.Draw(img)

    for i, ln in enumerate(lines):
        y = PAD + i * LINE_H
        if title and i == 0:
            colour = ACCENT
        elif ln.startswith("[") and ln.rstrip().endswith("]"):
            colour = ACCENT
        elif ln.strip().startswith(("#", "...")):
            colour = DIM
        else:
            colour = FG
        draw.text((PAD, y), ln, font=font, fill=colour)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def render_b64(content: str, title: str = "") -> str:
    """Same, base64-encoded with no data: prefix, as the Decisions API wants."""
    return base64.b64encode(render_text(content, title)).decode()


def context_image(implementation: str, failing_test: str, failure_output: str) -> str:
    """The localisation context, as a developer would screenshot it."""
    body = (
        "[IMPLEMENTATION UNDER TEST]\n"
        f"{implementation}\n"
        "[A TEST THAT FAILS AGAINST IT]\n"
        f"{failing_test}\n"
        "[OBSERVED FAILURE]\n"
        f"{failure_output}\n"
    )
    return render_b64(body, title="failing build")
