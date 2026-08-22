"""One-image LinkedIn post for the day's story — an editorial card, not a photo.

On LinkedIn the Instagram slide (blurred stock photo, shouting caps with a black
outline) reads as an ad and gets scrolled past. What stops a professional feed
is the opposite: a clean, structured card that looks like a clipping from a
report — brand colours, strong typography, the information laid out in order.

Layout (4:5, 1080x1350):
    deep-green header   small date/source line, the REAL headline, a deck
    phone mock-up       a real app screen (brief rule 3: never an invented UI),
                        overlapping the header edge on the right
    light body          the story's four beats, numbered
    footer              logo, domain, and the "buscador de preços" seal the
                        brand brief marks as mandatory on every piece

Colours come from the logo itself (#00301F forest green, #D05010 burnt
orange), not from the Instagram slides.

The card is generated; posting stays manual (a company page needs LinkedIn's
Marketing API approval). See carousel.publish docs in config.yaml.
"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .config import Config

GREEN = (0, 48, 31)          # #00301F — logo lettering
ORANGE = (208, 80, 16)       # #D05010 — logo target
PAPER = (245, 243, 238)      # warm neutral, the brief's "cinza-neutro"
INK = (38, 44, 41)           # body text
MUTED = (110, 118, 113)      # secondary text
MINT = (170, 204, 188)       # light text on green
PHONE = (12, 30, 24)

_MONTHS = ["JAN", "FEV", "MAR", "ABR", "MAI", "JUN",
           "JUL", "AGO", "SET", "OUT", "NOV", "DEZ"]

_FD = str(Path(__file__).resolve().parent.parent / "assets" / "fonts")
_FONTS = {
    "black": [f"{_FD}/Poppins-Black.ttf", "C:/Windows/Fonts/seguibl.ttf", "C:/Windows/Fonts/arialbd.ttf"],
    "bold": [f"{_FD}/Poppins-Bold.ttf", "C:/Windows/Fonts/segoeuib.ttf", "C:/Windows/Fonts/arialbd.ttf"],
    "semibold": [f"{_FD}/Poppins-SemiBold.ttf", "C:/Windows/Fonts/seguisb.ttf", "C:/Windows/Fonts/arialbd.ttf"],
    "regular": [f"{_FD}/Poppins-Regular.ttf", "C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/arial.ttf"],
}


def _font(kind: str, size: int) -> ImageFont.FreeTypeFont:
    for path in _FONTS[kind] + ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]:
        try:
            return ImageFont.truetype(path, size)
        except Exception:  # noqa: BLE001
            continue
    return ImageFont.load_default(size)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_w: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if draw.textlength(trial, font=font) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def _fit(draw, text: str, kind: str, size: int, max_w: int, max_lines: int,
         min_size: int) -> tuple[ImageFont.FreeTypeFont, list[str]]:
    """Largest font at which `text` wraps into at most `max_lines`."""
    while True:
        font = _font(kind, size)
        lines = _wrap(draw, text, font, max_w)
        if len(lines) <= max_lines or size <= min_size:
            return font, lines[:max_lines]
        size -= 2


def _clean_headline(title: str) -> str:
    """The real news headline, minus the ' - Source' suffix, sentence case."""
    title = re.split(r"\s+[-|–—]\s+", title)[0].strip()
    return title[:1].upper() + title[1:] if title else title


def _source_label(source: str, link: str = "") -> str:
    """A short publisher name. Google News feeds report the SEARCH QUERY as the
    source ('ANVISA medicamento OR "farmácia popular" - Google Notícias'),
    which ran across the whole header. Prefer the link's domain in that case."""
    src = (source or "").strip()
    junk = ('"' in src or " OR " in src.upper() or "google" in src.lower()
            or len(src) > 28)
    if junk and link:
        host = re.sub(r"^https?://(www\.)?", "", link).split("/")[0]
        host = re.sub(r"^(noticias|news|g1|www)\.", "", host)
        # Google News links are redirects: the domain says nothing about
        # the publisher. Better no source line than "FONTE: GOOGLE.COM".
        return "" if "google" in host else host.upper()
    return "" if junk else src.upper()


def _rounded_mask(size: tuple[int, int], radius: int) -> Image.Image:
    m = Image.new("L", size, 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, size[0] - 1, size[1] - 1],
                                        radius=radius, fill=255)
    return m


def _phone(screenshot: Path, box_w: int, box_h: int, crop_top: float) -> Image.Image:
    """A phone-shaped frame holding a real screenshot, with a soft shadow."""
    pad = int(box_w * 0.045)
    frame = Image.new("RGBA", (box_w, box_h), (0, 0, 0, 0))
    ImageDraw.Draw(frame).rounded_rectangle(
        [0, 0, box_w - 1, box_h - 1], radius=int(box_w * 0.12), fill=PHONE + (255,))
    with Image.open(screenshot) as im:
        shot = im.convert("RGB")
    trim = int(shot.height * crop_top)
    if 0 < trim < shot.height // 3:
        shot = shot.crop((0, trim, shot.width, shot.height))
    inner_w = box_w - 2 * pad
    scale = inner_w / shot.width
    shot = shot.resize((inner_w, max(1, int(shot.height * scale))), Image.LANCZOS)
    # The frame hugs the screen: a screenshot shorter than the box used to
    # leave a white band under the app's nav bar, which read as a glitch.
    inner_h = min(box_h - 2 * pad, shot.height)
    box_h = inner_h + 2 * pad
    frame = Image.new("RGBA", (box_w, box_h), (0, 0, 0, 0))
    ImageDraw.Draw(frame).rounded_rectangle(
        [0, 0, box_w - 1, box_h - 1], radius=int(box_w * 0.12), fill=PHONE + (255,))
    shot = shot.crop((0, 0, inner_w, inner_h))
    frame.paste(shot, (pad, pad), _rounded_mask((inner_w, inner_h), int(box_w * 0.08)))
    # Shadow: the frame's silhouette, blurred, offset down.
    shadow = Image.new("RGBA", (box_w + 80, box_h + 80), (0, 0, 0, 0))
    sil = Image.new("RGBA", (box_w, box_h), (0, 0, 0, 110))
    shadow.paste(sil, (40, 52), frame.split()[3])
    shadow = shadow.filter(ImageFilter.GaussianBlur(22))
    shadow.alpha_composite(frame, (40, 40))
    return shadow


def pick_screenshot(cfg: Config, when: date | None = None) -> Path | None:
    """Rotate through the medicine screens listed in config, one per day.

    The carousel's own picker chooses by keyword and happily puts a perfume on
    a prescription-drug story. This list is curated by hand to screens that
    show MEDICINES, so the card never contradicts its own headline.
    """
    names = cfg.get("linkedin.screens", []) or []
    root = Path(cfg.get("media.screenshots_dir", "img-channel/vertical"))
    paths = [p for p in (root / n for n in names) if p.exists()]
    if not paths:
        return None
    when = when or date.today()
    return paths[when.toordinal() % len(paths)]


def make_linkedin_card(cfg: Config, *, headline: str, deck: str,
                       points: list[tuple[str, str]], source: str,
                       out_path: Path, when: date | None = None,
                       screenshot: Path | None = None,
                       link: str = "") -> Path | None:
    """Render the card. `points` are (title, body) pairs — the story's beats."""
    W, H = 1080, 1350
    when = when or date.today()
    img = Image.new("RGB", (W, H), PAPER)
    d = ImageDraw.Draw(img)
    M = 64                                   # page margin

    # ---- header ---------------------------------------------------------
    head_h = int(H * 0.47)
    d.rectangle([0, 0, W, head_h], fill=GREEN)

    f_label = _font("bold", 22)
    label = f"NOTÍCIA DO DIA  ·  {when.day:02d} {_MONTHS[when.month - 1]} {when.year}"
    d.text((M, 58), label, font=f_label, fill=ORANGE)
    src = _source_label(source, link)
    if src:
        f_src = _font("semibold", 20)
        src = f"FONTE: {src}"
        sw = d.textlength(src, font=f_src)
        if sw < W * 0.42:                      # never collide with the date
            d.text((W - M - sw, 60), src, font=f_src, fill=MINT)

    text_w = int(W * 0.60) - M               # phone takes the right third
    f_head, head_lines = _fit(d, headline, "bold", 60, text_w, 4, 40)
    y = 118
    lh = int(f_head.size * 1.14)
    for line in head_lines:
        d.text((M, y), line, font=f_head, fill=(255, 255, 255))
        y += lh
    y += 18
    d.rectangle([M, y, M + 120, y + 7], fill=ORANGE)
    y += 34
    if deck:
        f_deck, deck_lines = _fit(d, deck, "regular", 27, text_w, 3, 22)
        for line in deck_lines:
            d.text((M, y), line, font=f_deck, fill=MINT)
            y += int(f_deck.size * 1.35)

    # ---- phone, straddling the header edge --------------------------------
    body_left_w = int(W * 0.58)
    if screenshot is None:
        screenshot = pick_screenshot(cfg, when)
    if screenshot and Path(screenshot).exists():
        pw = int(W * 0.31)
        ph = int(pw * 2.05)
        phone = _phone(Path(screenshot), pw, ph,
                       float(cfg.get("carousel.screenshot_crop_top", 0.05)))
        px = W - M - pw - 40 + 4
        py = int(H * 0.285) - 40
        img.paste(phone, (px, py), phone)
        d = ImageDraw.Draw(img)
    else:
        body_left_w = W - 2 * M               # no phone: points take full width

    # ---- numbered beats ----------------------------------------------------
    foot_top = H - 132
    top = head_h + 46
    avail = foot_top - top - 10
    # Fit ladder: body text first, then title and spacing, and as a last
    # resort two body lines instead of three. The 18/08 story had four
    # three-line beats and the last one ran straight through the footer rule.
    body_size, title_size, gap, max_body_lines = 26, 30, 26, 3
    while True:
        f_num = _font("black", title_size)
        f_title = _font("semibold", title_size)
        f_body = _font("regular", body_size)
        blocks, total = [], 0
        for n, (title, body) in enumerate(points, 1):
            tl = _wrap(d, title, f_title, body_left_w - M - 70)[:2]
            bl = _wrap(d, body, f_body, body_left_w - M - 70)
            if len(bl) > max_body_lines:
                bl = bl[:max_body_lines]
                bl[-1] = bl[-1].rstrip(" ,;:") + "…"
            h = len(tl) * int(f_title.size * 1.2) + len(bl) * int(f_body.size * 1.32)
            blocks.append((f"{n:02d}", tl, bl, h))
            total += h + gap
        if total - gap <= avail:
            break
        # Shrink a long way before cutting: an ellipsis on a professional
        # card reads as sloppy, a 21px body does not.
        if body_size > 21:
            body_size -= 1
        elif title_size > 26:
            title_size -= 1
        elif gap > 12:
            gap -= 2
        elif max_body_lines > 2:
            max_body_lines = 2
        else:
            break
    y = top
    for num, tl, bl, h in blocks:
        d.text((M, y + 2), num, font=f_num, fill=ORANGE)
        x = M + 70
        yy = y
        for line in tl:
            d.text((x, yy), line, font=f_title, fill=GREEN)
            yy += int(f_title.size * 1.2)
        yy += 4
        for line in bl:
            d.text((x, yy), line, font=f_body, fill=INK)
            yy += int(f_body.size * 1.32)
        y += h + gap

    # ---- footer ----------------------------------------------------------
    d.line([M, foot_top, W - M, foot_top], fill=(215, 212, 204), width=2)
    logo_path = Path(cfg.get("media.watermark", "img-channel/logo/bemNamosc4_v2.png"))
    x = M
    if logo_path.exists():
        with Image.open(logo_path) as lg:
            lg = lg.convert("RGBA")
            lh_ = 72
            lg = lg.resize((int(lg.width * lh_ / lg.height), lh_), Image.LANCZOS)
            img.paste(lg, (M, foot_top + 30), lg)
            x = M + lg.width + 26
    d = ImageDraw.Draw(img)
    site = re.sub(r"^https?://|/$", "", str(cfg.get("channel.site", "")))
    f_site = _font("bold", 30)
    d.text((x, foot_top + 44), site, font=f_site, fill=GREEN)
    seal = "Buscador de preços. A compra é feita na farmácia."
    f_seal = _font("regular", 21)
    sw = d.textlength(seal, font=f_seal)
    d.text((W - M - sw, foot_top + 52), seal, font=f_seal, fill=MUTED)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, "JPEG", quality=92, subsampling=0)
    return out_path


def card_from_package(cfg: Config, pkg: Path, when: date | None = None) -> Path | None:
    """Build the card from a carousel package's meta.json (for re-runs)."""
    import json
    meta = json.loads((pkg / "meta.json").read_text(encoding="utf-8"))
    slides = meta.get("slides") or []
    if not slides:
        return None
    created = meta.get("created", "")
    if when is None and created:
        try:
            when = date.fromisoformat(created[:10])
        except ValueError:
            when = None
    return make_linkedin_card(
        cfg,
        headline=_clean_headline(meta.get("headline") or slides[0]["headline"]),
        deck=slides[0].get("body", ""),
        points=[(s["headline"], s.get("body", "")) for s in slides[1:5]],
        source=meta.get("source", ""),
        link=meta.get("link", ""),
        out_path=pkg / "linkedin.jpg",
        when=when,
    )


# --------------------------------------------------------------------------
# LinkedIn text — the post that goes WITH the card
# --------------------------------------------------------------------------
def _opening(headline: str, deck: str) -> str:
    """The two lines shown before 'ver mais' — the only part most people read.
    Fact first (the real headline), then the hook. ~210 chars is the cut."""
    first = headline.rstrip(".?!") + "."
    return f"{first}\n\n{deck}".strip() if deck and deck != headline else first


def build_linkedin_text(cfg: Config, *, headline: str, deck: str,
                        bodies: list[str], story: str,
                        when: date | None = None) -> tuple[str, str]:
    """Returns (post_text, first_comment).

    LinkedIn rewards dwell time and early comments and penalises posts that
    send people off-platform. So: no link in the body (it goes in the first
    comment), one idea per line, a concrete question at the end, and three
    hashtags instead of eight. The Instagram post.txt breaks every one of
    those rules — it was written for a different feed.

    A story about a person in hardship (bridge_allowed) gets no product
    paragraph and a neutral question, the same rule the slides follow.
    """
    from .links import app_url
    from .product_context import bridge_allowed
    when = when or date.today()
    allowed, _ = bridge_allowed(cfg, story)

    parts = [_opening(headline, deck), ""]
    for b in bodies:
        b = (b or "").strip()
        if b:
            parts += [b, ""]
    # The slides' "aplicacao" beat usually pitches the app already; adding
    # the stock pitch after it said the same thing twice in a row.
    mentions_app = any(re.search(r"(?i)(^|[^a-z])app([^a-z]|$)|bem na mosca", b or "")
                       for b in bodies)
    if allowed and not mentions_app:
        pitch = str(cfg.get("linkedin.pitch", "")).strip()
        if pitch:
            parts += [pitch, ""]
    pool_key = "linkedin.questions" if allowed else "linkedin.questions_neutral"
    questions = [q for q in (cfg.get(pool_key, []) or []) if str(q).strip()]
    if questions:
        parts += [str(questions[when.toordinal() % len(questions)]), ""]
    tags = [t for t in (cfg.get("linkedin.hashtags", []) or []) if str(t).strip()][:3]
    if tags:
        parts.append(" ".join(t if t.startswith("#") else f"#{t}" for t in tags))
    text = "\n".join(parts).strip()

    url = app_url(cfg, "linkedin", content="card")
    comment = str(cfg.get("linkedin.comment", "Para comparar: {url}")).replace("{url}", url)
    return text, comment


def text_from_package(cfg: Config, pkg: Path, when: date | None = None) -> Path | None:
    """Write linkedin.txt next to the card, from the package's meta.json."""
    import json
    meta = json.loads((pkg / "meta.json").read_text(encoding="utf-8"))
    slides = meta.get("slides") or []
    if not slides:
        return None
    created = meta.get("created", "")
    if when is None and created:
        try:
            when = date.fromisoformat(created[:10])
        except ValueError:
            when = None
    story = " ".join([meta.get("headline", "")] + [s.get("body", "") for s in slides])
    text, comment = build_linkedin_text(
        cfg,
        headline=_clean_headline(meta.get("headline") or slides[0]["headline"]),
        deck=slides[0].get("body", ""),
        bodies=[s.get("body", "") for s in slides[1:5]],
        story=story, when=when)
    out = pkg / "linkedin.txt"
    out.write_text(
        text + "\n\n" + "-" * 60 + "\n"
        "PRIMEIRO COMENTÁRIO (o link fica aqui, não no post):\n" + comment + "\n",
        encoding="utf-8")
    return out
