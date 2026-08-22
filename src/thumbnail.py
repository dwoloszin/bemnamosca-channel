"""Auto-generate a clickable YouTube thumbnail (1280x720).

Design: story image as background, dark gradient for contrast, 2-3 lines of
huge uppercase text (the title's key words), an accent bar, and the channel
logo. High contrast + big type is what wins clicks at small sizes.
"""
from __future__ import annotations

import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

from .config import Config

W, H = 1280, 720

_FONT_CANDIDATES = [
    # Bundled first (assets/fonts, OFL): the same face on Windows and on the
    # GitHub runner. The Windows names below are only a fallback.
    str(Path(__file__).resolve().parent.parent / "assets" / "fonts" / "ArchivoBlack-Regular.ttf"),
    "C:/Windows/Fonts/impact.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/seguibl.ttf",       # Segoe UI Black
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]

_STOP = {"the", "a", "an", "of", "to", "in", "on", "for", "and", "is", "are",
         "with", "this", "that", "its", "it's", "as", "at", "by", "-"}


def _font(size: int) -> ImageFont.FreeTypeFont:
    for path in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except Exception:  # noqa: BLE001
            continue
    return ImageFont.load_default(size)


def _headline_words(title: str, max_words: int = 6) -> list[str]:
    # Drop the source suffix ("... - IGN") and boilerplate words, keep punch.
    title = re.split(r"\s+[-|–]\s+", title)[0]
    words = re.findall(r"[A-Za-z0-9'?!$%.]+", title)
    strong = [w for w in words if w.lower() not in _STOP]
    return (strong or words)[:max_words]


def _wrap(words: list[str], per_line: int = 2) -> list[str]:
    lines = []
    for i in range(0, len(words), per_line):
        lines.append(" ".join(words[i:i + per_line]).upper())
    return lines[:3]


def make_thumbnail(cfg: Config, title: str, background: Path | None,
                   out_path: Path) -> Path | None:
    try:
        # --- background ------------------------------------------------------
        if background and Path(background).exists():
            with Image.open(background) as im:
                bg = ImageOps.fit(im.convert("RGB"), (W, H), Image.LANCZOS)
        else:
            bg = Image.new("RGB", (W, H), (24, 12, 48))
        bg = bg.filter(ImageFilter.GaussianBlur(2))
        bg = ImageEnhance.Brightness(bg).enhance(0.85)

        # Dark gradient from the left so text pops.
        grad = Image.new("L", (W, 1))
        for x in range(W):
            grad.putpixel((x, 0), int(210 * max(0.0, 1.0 - x / (W * 0.72))))
        grad = grad.resize((W, H))
        black = Image.new("RGB", (W, H), (0, 0, 0))
        bg = Image.composite(black, bg, grad)

        draw = ImageDraw.Draw(bg)
        accent = (255, 210, 0)          # yellow
        white = (255, 255, 255)

        # --- headline text ---------------------------------------------------
        lines = _wrap(_headline_words(title))
        if not lines:
            lines = [cfg.get("theme.topic", "NEWS").upper()]
        size = 150 if len(lines) <= 2 else 120
        font = _font(size)
        # Shrink until the longest line fits.
        max_w = int(W * 0.62)
        while size > 60:
            longest = max(draw.textlength(l, font=font) for l in lines)
            if longest <= max_w:
                break
            size -= 8
            font = _font(size)

        line_h = int(size * 1.12)
        total_h = line_h * len(lines)
        y = (H - total_h) // 2
        x = int(W * 0.055)

        # Accent bar above the text block.
        draw.rectangle([x, y - 26, x + 220, y - 10], fill=accent)

        for i, line in enumerate(lines):
            ly = y + i * line_h
            color = accent if i == len(lines) - 1 and len(lines) > 1 else white
            # Heavy black stroke = readable at any size.
            draw.text((x, ly), line, font=font, fill=color,
                      stroke_width=max(4, size // 16), stroke_fill=(0, 0, 0))

        # Topic badge (small, top-left).
        badge_font = _font(44)
        topic = cfg.get("theme.topic", "").upper()
        if topic:
            pad = 14
            tw = draw.textlength(topic, font=badge_font)
            bx, by = x, int(H * 0.075)
            draw.rectangle([bx - pad, by - pad // 2, bx + tw + pad, by + 52], fill=(200, 16, 46))
            draw.text((bx, by), topic, font=badge_font, fill=white)

        # Logo bottom-right.
        logo_path = cfg.get("video.watermark")
        if logo_path and cfg.path_of(logo_path).exists():
            with Image.open(cfg.path_of(logo_path)) as lg:
                lg = lg.convert("RGBA")
                lw = int(W * 0.13)
                lh = int(lg.height * lw / lg.width)
                lg = lg.resize((lw, lh), Image.LANCZOS)
                bg.paste(lg, (W - lw - 28, H - lh - 24), lg)

        out_path.parent.mkdir(parents=True, exist_ok=True)
        bg.save(out_path, "JPEG", quality=92)
        return out_path
    except Exception as exc:  # noqa: BLE001
        print(f"  [thumbnail] failed: {exc}")
        return None
