"""Build an .ass subtitle file from word-level TTS timings.

Style is short-form friendly: a few big, high-contrast words at a time, which
is what makes captions "readable very well" on phones.
"""
from __future__ import annotations

import re

from pathlib import Path

from .config import Config
from .tts import Word


def _ass_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "(").replace("}", ")").replace("\n", " ")


def _group(words: list[Word], per: int) -> list[tuple[float, float, str]]:
    captions: list[tuple[float, float, str]] = []
    for i in range(0, len(words), per):
        chunk = words[i:i + per]
        if not chunk:
            continue
        start = chunk[0].start
        end = chunk[-1].end
        text = " ".join(w.text for w in chunk).strip()
        if text:
            captions.append((start, end, text))
    return captions


def _chunks(words: list[Word], per: int, pause: float = 0.32) -> list[list[Word]]:
    """Caption groups that follow the PHRASE, not a fixed word count.

    Two steps. First the words are split into phrases at punctuation
    (. , ! ? : ; …) or where the voice pauses. Then each phrase is divided
    into BALANCED groups no longer than `per`: a 10-word phrase with per=4
    becomes 4/3/3, never 4/4/2 — and never a lone trailing word like
    "GENTE." on its own caption, which is what a fixed cut produced.
    """
    phrases: list[list[Word]] = []
    cur: list[Word] = []
    for i, w in enumerate(words):
        cur.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        ends_phrase = w.text.rstrip().endswith((".", ",", "!", "?", ":", ";", "…"))
        paused = nxt is not None and (nxt.start - w.end) >= pause
        if ends_phrase or paused or nxt is None:
            phrases.append(cur)
            cur = []
    out: list[list[Word]] = []
    for ph in phrases:
        n = len(ph)
        groups = max(1, -(-n // per))          # ceil
        base, extra = divmod(n, groups)
        k = 0
        for g in range(groups):
            size = base + (1 if g < extra else 0)
            out.append(ph[k:k + size])
            k += size
    return [g for g in out if g]


def _color_tag(color: str) -> str:
    """Normalize a config color like '&H0000E5FF' into an ASS override tag."""
    c = color.strip()
    if not c.startswith("&H"):
        c = "&H" + c
    if not c.endswith("&"):
        c += "&"
    return c


_FONT_CACHE: dict = {}


def _fit_size(text: str, font_name: str, font_px: int, max_w: int) -> int:
    """Largest size <= font_px at which `text` fits on one line. A caption
    that wraps gets two boxes that overlap each other; never wrap."""
    from PIL import ImageFont
    fonts_dir = Path(__file__).resolve().parent.parent / "assets" / "fonts"
    cands = {"Archivo Black": str(fonts_dir / "ArchivoBlack-Regular.ttf"),
             "Arial Black": "C:/Windows/Fonts/ariblk.ttf",
             "Arial": "C:/Windows/Fonts/arialbd.ttf"}.get(font_name, str(fonts_dir / "ArchivoBlack-Regular.ttf"))
    size = font_px
    while size > int(font_px * 0.6):
        key = (cands, size)
        if key not in _FONT_CACHE:
            try:
                _FONT_CACHE[key] = ImageFont.truetype(cands, size)
            except OSError:
                return font_px
        if _FONT_CACHE[key].getlength(text) <= max_w:
            return size
        size -= 4
    return size


def _karaoke_events(chunk: list[Word], offset: float, primary: str, highlight: str,
                    uppercase: bool,
                    emphasis: tuple | None = None,
                    fit: tuple | None = None) -> list[tuple[float, float, str]]:
    """One event per spoken word: the whole caption stays on screen, the active
    word is highlighted, and the caption pops in slightly on arrival."""
    g_start, g_end = chunk[0].start, chunk[-1].end
    hi, pri = _color_tag(highlight), _color_tag(primary)
    events: list[tuple[float, float, str]] = []
    for k, w in enumerate(chunk):
        ev_start = g_start if k == 0 else max(w.start, g_start)
        ev_end = chunk[k + 1].start if k + 1 < len(chunk) else g_end
        if ev_end <= ev_start:
            ev_end = ev_start + 0.05
        parts = []
        emph_re, emph_col, emph_scale = emphasis or (None, None, 100)
        for j, wj in enumerate(chunk):
            t = _escape(wj.text.upper() if uppercase else wj.text)
            # Key words (numbers, R$, %, config list) are orange and bigger for
            # their whole time on screen; the word being spoken is amber. When
            # both apply the emphasis wins — it is the information, not the
            # cursor, that the viewer must not miss.
            is_emph = bool(emph_re and emph_re.search(wj.text))
            if is_emph:
                parts.append("{\\c" + emph_col + "\\fscx" + str(emph_scale) + "\\fscy" + str(emph_scale) + "}"
                             + t + "{\\c" + pri + "\\fscx100\\fscy100}")
            elif j == k:
                parts.append("{\\c" + hi + "}" + t + "{\\c" + pri + "}")
            else:
                parts.append(t)
        text = " ".join(parts)
        if fit:
            font_name, font_px, max_w = fit[:3]
            plain = " ".join((wj.text.upper() if uppercase else wj.text) for wj in chunk)
            size = _fit_size(plain, font_name, font_px, max_w)
            if size < font_px:
                text = "{\\fs" + str(size) + "}" + text
        if fit and len(fit) > 3 and fit[3]:
            # glow: blur the outline edge so it reads as a halo, not a border
            text = "{\\blur" + str(fit[3]) + "}" + text
        if k == 0:
            # pop-in: scale 82% -> 100% over the first 110 ms
            text = "{\\fscx82\\fscy82\\t(0,110,\\fscx100\\fscy100)}" + text
        events.append((ev_start + offset, ev_end + offset, text))
    return events


def build_srt(
    segments: list[tuple[list[Word], float]],
    out_path: Path,
    *,
    words_per_caption: int = 6,
    shift: float = 0.0,
) -> Path | None:
    """Write a plain .srt sidecar (for YouTube closed captions + accessibility).

    Slightly longer captions than the burned-in ones — SRT is read as classic
    closed captions, not as on-screen graphics.
    """
    def srt_time(seconds: float) -> str:
        seconds = max(0.0, seconds)
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        ms = int(round((seconds - int(seconds)) * 1000))
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    lines: list[str] = []
    n = 0
    for words, offset in segments:
        for start, end, text in _group(words, words_per_caption):
            n += 1
            lines.append(str(n))
            lines.append(f"{srt_time(start + offset + shift)} --> {srt_time(end + offset + shift)}")
            lines.append(text)
            lines.append("")
    if n == 0:
        return None
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path


def build_ass(
    segments: list[tuple[list[Word], float]],
    cfg: Config,
    *,
    width: int,
    height: int,
    is_short: bool,
    banners: list[tuple[float, float, str]] | None = None,
    hook: tuple[str, float, float] | None = None,
) -> Path | None:
    """segments = list of (words, time_offset_seconds). Returns path to .ass.

    banners: (start, end, text) section chips at the top (amber).
    hook:    (text, start, end) — the headline, big and centred, over the first
             seconds. Shorts are decided in the first 2-3 s; a photo with a
             three-word caption does not say what the video is about, the
             headline does.
    """
    if not cfg.get("subtitles.enabled", True):
        return None

    per = int(cfg.get("subtitles.words_per_caption", 3))
    pct = float(cfg.get("subtitles.font_size_short" if is_short else "subtitles.font_size_long", 6))
    font_px = max(12, int(height * pct / 100.0))
    margin_v = int(cfg.get("subtitles.margin_v", 120))
    if not is_short:
        margin_v = int(margin_v * 0.6)

    font = cfg.get("subtitles.font", "Arial")
    primary = cfg.get("subtitles.primary_color", "&H00FFFFFF")
    outline_col = cfg.get("subtitles.outline_color", "&H00000000")
    outline = int(cfg.get("subtitles.outline", 3))
    shadow = int(cfg.get("subtitles.shadow", 1))

    # Alignment 2 = bottom-center. Bold on. Outline+shadow for contrast.
    # Side margins keep text off the frame edges; WrapStyle 0 (below) wraps
    # anything longer onto a second line instead of cutting it off.
    margin_lr = max(40, int(width * 0.06))
    # Optional dark box behind captions (BorderStyle 3). On app screenshots
    # the caption used to sit on product rows — two texts in the same pixels.
    # Opacity 0 keeps the classic outline-only look.
    # Caption backing. "glow" = a thick, blurred dark-green outline that
    # follows the letter shapes (one continuous halo, whatever the colours
    # inside). "box" = BorderStyle 3, which draws ONE BOX PER STYLE RUN, so a
    # line with an amber spoken word and an orange emphasis word became three
    # overlapping boxes. "none" = the classic thin black outline.
    backing = str(cfg.get("subtitles.caption_backing", "glow")).lower()
    box = float(cfg.get("subtitles.box_opacity", 0.0) or 0) if backing == "box" else 0.0
    glow_px = 0
    if backing == "glow":
        glow_alpha = int(round(255 * (1 - float(cfg.get("subtitles.glow_opacity", 0.85)))))
        glow_col = f"&H{glow_alpha:02X}1F3000"
        glow_px = max(6, int(font_px * float(cfg.get("subtitles.glow_size", 0.16))))
        style = (
            f"Style: Caption,{font},{font_px},{primary},{primary},{glow_col},"
            f"&H00000000,-1,0,0,0,100,100,0,0,1,{glow_px},0,2,{margin_lr},{margin_lr},{margin_v},1"
        )
    elif box > 0:
        alpha = int(round(255 * (1 - max(0.0, min(1.0, box)))))   # ASS alpha: 00 opaque
        box_col = f"&H{alpha:02X}1F3000"                           # logo dark green
        style = (
            f"Style: Caption,{font},{font_px},{primary},{primary},{box_col},"
            f"{box_col},-1,0,0,0,100,100,0,0,3,{max(8, outline * 3)},0,2,{margin_lr},{margin_lr},{margin_v},1"
        )
    else:
        style = (
            f"Style: Caption,{font},{font_px},{primary},{primary},{outline_col},"
            f"&H64000000,-1,0,0,0,100,100,0,0,1,{outline},{shadow},2,{margin_lr},{margin_lr},{margin_v},1"
        )
    # Banner style: big accent text at the top (listicle ranks: TOP 5, #5..#1).
    banner_px = max(16, int(height * 0.055))
    banner_color = _color_tag(cfg.get("subtitles.highlight_color", "&H0000E5FF")).rstrip("&")
    # BorderStyle 3 = opaque box behind the text (a "chip"): on an app
    # screenshot the top of the frame is full of UI text, and an outlined
    # label on top of it was two sentences fighting for the same pixels.
    banner_bg = _color_tag(cfg.get("subtitles.banner_bg_color", "&H001F3000")).rstrip("&")
    banner_style = (
        f"Style: Banner,{font},{banner_px},{banner_color},{banner_color},{banner_bg},"
        f"{banner_bg},-1,0,0,0,100,100,0,0,3,{max(6, outline * 3)},0,8,{margin_lr},{margin_lr},"
        f"{int(height * 0.09)},1"
    )

    # Hook style: centred (Alignment 5), ~8.5% of the height, same outline.
    hook_px = max(20, int(height * float(cfg.get("subtitles.hook_size", 8.5)) / 100.0))
    hook_style = (
        f"Style: Hook,{font},{hook_px},{primary},{primary},{outline_col},"
        f"&H64000000,-1,0,0,0,100,100,0,0,1,{outline + 1},{shadow},5,{margin_lr},{margin_lr},0,1"
    )

    style_mode = cfg.get("subtitles.style", "karaoke")
    uppercase = bool(cfg.get("subtitles.uppercase", True))
    highlight = cfg.get("subtitles.highlight_color", "&H0000E5FF")

    # Emphasis: numbers / money / percentages automatically, plus a config
    # list of words the channel always wants to land ("grátis", "Anvisa"...).
    emphasis = None
    if cfg.get("subtitles.emphasis_enabled", True):
        pats = []
        if cfg.get("subtitles.emphasis_auto", True):
            pats.append(r"[0-9]|R[$]|%|por cento|reais|centavos|vírgula")
        words_cfg = [str(x).strip() for x in (cfg.get("subtitles.emphasis_words", []) or []) if str(x).strip()]
        if words_cfg:
            pats.append("^[^0-9A-Za-zÀ-ÿ]*(" + "|".join(re.escape(x) for x in words_cfg) + ")[^0-9A-Za-zÀ-ÿ]*$")
        if pats:
            # With the caption box on, a bigger word breaks the box into
            # segments of different heights, which reads as a glitch. Keep
            # the colour, drop the size change in that mode.
            scale = 100 if box > 0 else int(cfg.get("subtitles.emphasis_scale", 118))   # box mode only
            emphasis = (re.compile("|".join(f"(?:{x})" for x in pats), re.IGNORECASE),
                        _color_tag(cfg.get("subtitles.emphasis_color", "&H001050D0")),
                        scale)

    events: list[str] = []
    for words, offset in segments:
        if style_mode == "karaoke" and words:
            for chunk in _chunks(words, per, float(cfg.get("subtitles.phrase_pause", 0.32))):
                for start, end, text in _karaoke_events(chunk, offset, primary, highlight, uppercase, emphasis,
                                                        fit=(font, font_px, width - 2 * margin_lr - 2 * max(outline * 3, glow_px), max(0, glow_px // 2))):
                    events.append(
                        f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Caption,,0,0,0,,{text}"
                    )
        else:
            for start, end, text in _group(words, per):
                if uppercase:
                    text = text.upper()
                events.append(
                    f"Dialogue: 0,{_ass_time(start + offset)},{_ass_time(end + offset)},"
                    f"Caption,,0,0,0,,{_escape(text)}"
                )

    for b_start, b_end, b_text in banners or []:
        events.append(
            f"Dialogue: 1,{_ass_time(b_start)},{_ass_time(b_end)},Banner,,0,0,0,,"
            f"{{\\fscx80\\fscy80\\t(0,120,\\fscx100\\fscy100)}}{_escape(b_text)}"
        )

    if hook and hook[0].strip():
        h_text, h_start, h_end = hook
        words = h_text.split()
        per = 3 if len(words) > 6 else 2
        lines = [" ".join(words[i:i + per]) for i in range(0, len(words), per)][:4]
        h_body = "\\N".join(_escape(l.upper() if uppercase else l) for l in lines)
        events.append(
            f"Dialogue: 2,{_ass_time(h_start)},{_ass_time(h_end)},Hook,,0,0,0,,"
            f"{{\\pos({width // 2},{int(height * 0.40)})\\fad(120,260)\\fscx86\\fscy86"
            f"\\t(0,160,\\fscx100\\fscy100)}}{h_body}"
        )

    if not events:
        return None

    content = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
{style}
{banner_style}
{hook_style}

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
""" + "\n".join(events) + "\n"

    out = cfg.output_dir / "_work" / "captions.ass"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(content, encoding="utf-8")
    return out
