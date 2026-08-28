"""Carousel posts — the 5-slide structure used for this channel's videos.

  1. HOOK       something striking about the news that stops the scroll
  2. NOTICIA    the story itself, told straight
  3. SOLUCAO    the problem it creates, answered by one feature of the app
  4. APLICACAO  how that feature is actually used, concretely
  5. CONCLUSAO  the close: come and see the tool

The same five beats become three deliverables from one generation:

  slide_1..5.jpg   feed images (4:5) — post by hand on Instagram / LinkedIn
  carousel.txt     the caption to paste with them
  post.txt         a SINGLE post carrying all the information in its text,
                   for places where a carousel is awkward
  video.mp4        the same five beats as a vertical video for YouTube, with
                   narration, music, burned-in subtitles and a different
                   random transition at every cut

The sensitive-story guard from product_context applies here too: slides 3 and
4 are the pitch, so a story that must not carry a pitch collapses to a
straight news carousel instead.
"""
from __future__ import annotations

import json
import random
import re
import time
from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .news import NewsItem, fetch_news
from .product_context import bridge_allowed, bridge_block, product_block
from .script_writer import ScriptSection, VideoScript, _ask_json, _lang_block
from .llm import get_llm

# 21/08: 1 hook / 2-3 the NEWS / 4-5 Bem na Mosca. The old shape gave the
# story one beat and the product three, which read as an ad with a news
# excuse. Two news beats earn the viewer's trust before the app appears, and
# the app gets exactly two: what it is, how to use it. "impacto" is still
# news (what this costs the viewer) with NO product in it.
ROLES = ["hook", "noticia", "impacto", "solucao", "conclusao"]
ROLE_LABELS = {
    "hook": "ATENÇÃO", "noticia": "O QUE ACONTECEU", "impacto": "O QUE ISSO CUSTA",
    "solucao": "BEM NA MOSCA", "conclusao": "COMO USAR",
}


@dataclass
class Slide:
    role: str
    headline: str      # short, goes ON the image
    body: str          # a couple of sentences, goes in the caption
    narration: str     # spoken in the video version
    keyword: str       # visual search term


# --------------------------------------------------------------------------
# generation
# --------------------------------------------------------------------------
def _hook_history_path(cfg: Config) -> Path:
    return cfg.output_dir / "hook_history.json"


def _first_sentence(text: str) -> str:
    return re.split(r"(?<=[.!?…])\s+", (text or "").strip(), maxsplit=1)[0].strip()


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9à-ÿ]+", " ", text.lower()).strip()


def _recent_hooks(cfg: Config, n: int = 30) -> list[str]:
    p = _hook_history_path(cfg)
    if not p.exists():
        return []
    try:
        return [str(x) for x in json.loads(p.read_text(encoding="utf-8"))][-n:]
    except Exception:  # noqa: BLE001
        return []


def _remember_hook(cfg: Config, narration: str, n: int = 30) -> None:
    hooks = _recent_hooks(cfg, n) + [_first_sentence(narration)]
    _hook_history_path(cfg).write_text(json.dumps(hooks[-n:], ensure_ascii=False, indent=2),
                                       encoding="utf-8")


def _weak_hook(cfg: Config, narration: str, strict: bool = True) -> str:
    """Why this opening would be scrolled past — empty string when it is fine.

    Checked in code, not just asked for in the prompt: on 21/08 the model was
    asked for "one striking fact" and opened with "Tratar quem a gente ama
    exige planejamento financeiro" — a moral, not a hook. The first sentence
    must be short and carry a number, a question or a contradiction, and it
    must not start with one of the configured throat-clearing openers.
    """
    text = (narration or "").strip()
    if not text:
        return "empty"
    first = _first_sentence(text)
    max_words = int(cfg.get("carousel.hook_max_words", 12))
    if len(first.split()) > max_words:
        return f"first sentence has {len(first.split())} words (max {max_words})"
    low = first.lower()
    for opener in (cfg.get("carousel.hook_banned_openers", []) or []):
        if low.startswith(str(opener).lower()):
            return f"opens with '{opener}'"
    # Digits, R$, %, and numbers WRITTEN OUT: "Setenta por cento a menos" is a
    # number hook in Portuguese and was rejected three times on 22/08.
    has_number = bool(re.search(
        r"[0-9]|R[$]|%|por cento|(^|[^a-zà-ÿ])(um|uma|dois|duas|três|tres|quatro|cinco|seis|sete|oito|nove|dez|"
        r"onze|doze|quinze|vinte|trinta|quarenta|cinquenta|sessenta|setenta|oitenta|noventa|cem|cento|"
        r"duzentos|trezentos|quinhentos|mil|milhão|milhões|bilhão|bilhões|metade|dobro|triplo)([^a-zà-ÿ]|$)",
        first.lower()))
    has_question = "?" in first
    has_contrast = bool(re.search(r"(^|[^a-zà-ÿ])(mas|mesmo|porém|só que|nem sempre|diferen|três|dois|duas|nunca|ninguém|errad|virou|mudou|agora|primeira vez|mais caro|mais barato|dobr|metade|tão |igual|quanto)", low))
    # A very short fragment is a hook by its shape alone ("Remédio de pet
    # virou produto de farmácia."); the number/question/contrast test is for
    # sentences long enough to ramble.
    # Money words are the channel's whole stake: "de graça no SUS", "mais
    # caro" — a hook in themselves. Rejected three good ones on 22/08.
    has_money = bool(re.search(r"de graça|grátis|gratuit|caro|barat|preço|custa|reais|bolso", low))
    punchy = len(first.split()) <= 9
    # strict=False (last attempt): a clean, short, non-repeated opening is
    # accepted even without a number/question/contrast. Two whole days were
    # lost to three rejections in a row — a decent hook beats no video.
    if strict and not (has_number or has_question or has_contrast or has_money or punchy):
        return f"first sentence has no number, question or contradiction: {first!r}"
    # Same opening as a recent day: the model likes to recycle whatever
    # worked (or whatever the prompt's examples say). Rejected here so the
    # re-ask loop gets a fresh one.
    if _norm(first) in {_norm(h) for h in _recent_hooks(cfg)}:
        return "same opening sentence as a recent video"
    return ""


def write_carousel(cfg: Config, item: NewsItem) -> list[Slide] | None:
    """Ask the LLM for the five beats. None when no LLM is available."""
    client = get_llm(cfg)
    if client is None:
        return None

    story = f"{item.clean_title} {item.clean_summary}"
    allowed, term = bridge_allowed(cfg, story)
    if not allowed:
        print(f"  [carousel] sensitive story (matched '{term}') — "
              "news-only carousel, no product slides")

    hook_rules = """
HOOK RULES (slide 1 narration) — the viewer decides in 2 seconds:
 * The FIRST sentence is the hook: max 10 words, and it must be one of
   - a NUMBER from the story ("R$ 1.300 num remédio que custa R$ 900.")
   - a CONTRADICTION or surprise ("O mesmo remédio. Três preços diferentes.")
   - a direct QUESTION to the viewer ("Você pagou caro no último remédio?")
 * Start mid-action. NEVER open with a greeting, a theme statement or a
   moral ("Tratar quem a gente ama...", "Hoje vamos falar", "Cuidar da
   saúde é importante", "Você sabia que"). Those are scrolled past.
 * Second sentence: the stake for THIS viewer — what it costs them, in
   concrete terms.
 * Last sentence of the hook: an OPEN LOOP — promise something specific the
   video reveals at the end ("No fim eu mostro como saber em 10 segundos se
   você está pagando caro."). Slide 5 MUST pay this promise off explicitly.
 * Slide 3 starts with a mid-video re-hook: ONE short line that re-raises
   the tension. Pick a different shape each time — a warning ("Só que tem
   uma pegadinha."), a counter-intuitive fact ("A farmácia mais barata não
   é a do remédio mais barato."), or a challenge ("Faz a conta comigo.").
 * The quoted sentences above are ILLUSTRATIONS of the shape. Do NOT reuse
   them or paraphrase them closely — write a hook that only fits THIS story.
   Vary the type: if a question would be obvious, use the number or the
   contradiction instead. A viewer who sees the same opening twice stops
   watching.
{recent_hooks}"""
    recent = _recent_hooks(cfg)
    hook_rules = hook_rules.replace("{recent_hooks}", (
        "Openings ALREADY USED on recent days — yours must be different from all of these:\n"
        + "\n".join(f"   - {h}" for h in recent[-12:]) + "\n") if recent else "")
    plan = ("""Slide roles — follow this order exactly. Slides 1-3 are NEWS and must
not mention any app or product; the brand appears only in slides 4-5:
 1 "hook"      — the scroll-stopper. See HOOK RULES below. Concrete, tense, specific.
 2 "noticia"   — what actually happened, told straight and factually. Who, what, when.
 3 "impacto"   — re-hook line, then what this means for the viewer: pocket or
                 routine, in concrete terms. Still the story. No product yet.
 4 "solucao"   — present Bem na Mosca in one breath: what it is (a free price
                 finder for medicines) and the ONE feature that answers slide 3.
                 Plain and confident. No hype words, no urgency, no superlatives.
 5 "conclusao" — how to use that feature (what the person taps, in order), PAY OFF
                 the open loop from slide 1 by simply DELIVERING it ("Como prometi:
                 ..." is fine; never "pagar a promessa" or any talk about the
                 promise itself), then a calm invitation to try it.

TONE: the impact comes from the FACT, never from volume. No fear-mongering,
no "urgente", no "você precisa", no exaggeration, no promise of a cure or of
a guaranteed saving. A well-informed friend, not an ad.""" + hook_rules
            if allowed else
            """Slide roles — follow this order exactly. This story involves a person in
distress, so NO slide may mention any app, product or call to action:
 1 "hook"      — the scroll-stopper. See HOOK RULES below, but keep it respectful:
                 a fact or a question, never sensational about the person.
 2 "noticia"   — what actually happened, told straight and factually.
 3 "impacto"   — the wider context: who else this affects.
 4 "solucao"   — what is being done about it, or what the rules say.
 5 "conclusao" — pay off the open loop, closing on the story itself, with respect.""" + hook_rules)

    prompt = f"""Write a 5-slide CAROUSEL post about {cfg.get('theme.topic')}
for this audience: {cfg.get('script.audience', 'a general audience')}
Voice: {cfg.get('script.style')}
{product_block(cfg) if allowed else ''}
News headline: {item.clean_title}
Summary: {item.clean_summary or '(no summary provided)'}
Source: {item.source}
{bridge_block(cfg, story)}
{plan}

For EACH slide return:
- "headline"  : max 7 words, goes ON the image. Punchy. No hashtags.
- "body"      : 1-2 sentences for the caption text.
- "narration" : 18-26 spoken words for the video version (WHOLE script under
                130 words — it must fit a 58-second Short). Written for the EAR,
                to be read aloud by a voice model, as a Brazilian would actually
                SAY it: spoken Portuguese ("a gente", "tá", "pra" are fine),
                short sentences (max 12 words), commas where a person breathes,
                a full stop after the punchline, one rhetorical question per
                slide where it fits. Punctuation IS the intonation, so use it
                like speech: at most ONE ellipsis in the whole script, only
                for a real dramatic pause, and never inside a sentence to
                split it. No bullet rhythm, no headline voice: flowing
                sentences that connect to each other. In the NARRATION write
                every number OUT IN WORDS as a Brazilian says it ("três vírgula
                oitenta e um por cento", "mil e duzentos reais") — digits stay
                in headline and body only.
- "keyword"   : 2-4 words describing a visual for this slide, IN ENGLISH.

Rules: strictly factual to the story, invent no numbers, no medical advice,
no dose, never promise a cure, name no real pharmacy.
{_lang_block(cfg)}
IMPORTANT EXCEPTION to the language rule above: "keyword" must stay in
ENGLISH. It is a search term for a stock photo library indexed in English —
a Portuguese term there returns photos of the wrong country and language
(searching "aviso importante" returns Spanish street signs). Everything else
stays in the channel's language. Prefer concrete, photographable nouns:
"pharmacy shelf medicine", "person reading price label", "smartphone barcode
scan", not abstractions like "transparency" or "economy".
Return ONLY JSON: {{"slides": [{{"role": "hook", "headline": "...",
 "body": "...", "narration": "...", "keyword": "..."}}, ...]}}"""

    # A thin answer used to be published anyway, behind a warning nobody reads:
    # on 16/08 five slides carrying 38 narrated words became a video that just
    # read titles aloud. Thin output is a MODEL HICCUP, not an outage — the
    # same prompt at temperature 0.7 usually answers properly on the next try —
    # so re-ask immediately here instead of publishing something weak. Only
    # when every attempt is thin do we return None, which hands the problem to
    # the 30-minute retry ladder in `main.py daily` (that one is for the
    # provider being down, a different failure with a different cure).
    min_words = int(cfg.get("carousel.min_narrated_words", 60))
    min_slides = int(cfg.get("carousel.min_slides", 3))
    attempts = max(1, int(cfg.get("carousel.script_attempts", 3)))

    for attempt in range(1, attempts + 1):
        data = _ask_json(client, prompt)
        raw = (data or {}).get("slides") or []
        slides: list[Slide] = []
        missing_narration = 0
        for i, s in enumerate(raw[:5]):
            headline = str(s.get("headline", "")).strip()
            if not headline:
                continue
            body = str(s.get("body", "")).strip()
            # Fall back to the BODY, not the headline. Weaker models in the
            # provider chain sometimes omit "narration" entirely, and falling
            # back to a 2-word headline produced a 16-second video that just
            # read five titles aloud — the failure looked like a finished
            # video, so it went out. The body always carries the explanation.
            narration = str(s.get("narration", "")).strip()
            if not narration:
                missing_narration += 1
                narration = body or headline
            slides.append(Slide(
                role=str(s.get("role") or ROLES[min(i, 4)]).lower(),
                headline=headline,
                body=body,
                narration=narration,
                keyword=str(s.get("keyword", "")).strip() or cfg.get("theme.topic", ""),
            ))

        words = sum(len(sl.narration.split()) for sl in slides)
        hook_issue = _weak_hook(cfg, slides[0].narration, strict=attempt < attempts) if slides else ""
        if len(slides) < min_slides:
            reason = f"only {len(slides)} usable slides (minimum {min_slides})"
        elif hook_issue:
            reason = f"weak hook ({hook_issue})"
        elif words < min_words:
            reason = (f"only {words} narrated words across {len(slides)} slides "
                      f"(minimum {min_words}) — the video would be too short")
        else:
            reason = ""

        if not reason:
            if missing_narration:
                print(f"  [carousel] WARNING: {missing_narration}/{len(slides)} slides "
                      "came back with no narration — using the caption text instead")
            if attempt > 1:
                print(f"  [carousel] accepted on attempt {attempt}: "
                      f"{words} narrated words across {len(slides)} slides")
            _remember_hook(cfg, slides[0].narration)
            return slides

        if attempt < attempts:
            print(f"  [carousel] rejected ({reason}) — asking the model again "
                  f"[{attempt}/{attempts}]")
        else:
            print(f"  [carousel] rejected ({reason}) — giving up after "
                  f"{attempts} attempts")
    return None


# --------------------------------------------------------------------------
# slide images
# --------------------------------------------------------------------------
def pick_sources(cfg: Config, slides: list[Slide],
                 workdir: Path,
                 extras: list | None = None) -> list[tuple[Path | None, bool]]:
    """One ORIGINAL image per slide, chosen by the slide's ROLE.

      hook / noticia            -> stock photo of the story
      solucao / aplicacao /     -> the app itself, from the user's own library
      conclusao                    (img-channel/vertical screenshots)

    Returns (original_path, is_screenshot) — not a resized copy, so the feed
    slides (4:5) and the video slides (9:16) can be built from the SAME picks
    at their own sizes. Running the selection twice used to give the carousel
    and its video different screenshots for the same beat.

    The local library is ordered LEAST-USED-FIRST via media_log, which is the
    project's existing rotation guarantee: nothing is picked a second time
    before everything else has been picked once. Reading the folder directly
    returned it alphabetically, so slides 3-5 kept getting the same three
    screenshots on every single run.

    build_image_pool is still not usable here: it puts stock FIRST and then
    truncates to the number of slides, so with six stock hits per keyword the
    user's own screenshots are never reached at all.
    """
    from .media import gather_local_images, pexels_images
    from .media_log import order_by_usage

    local = order_by_usage(cfg, gather_local_images(cfg, "vertical"))
    # The app beats must show MEDICINE. The library also holds perfume and
    # face-cream screens (taken while testing the scanner), and least-used-
    # first rotation is blind to content: a prescription-drug story shipped
    # with a Prada bottle under its "RESUMO" chip. So the app beats draw only
    # from the hand-curated medicine list (shared with the LinkedIn card),
    # still least-used-first. Empty list = old behaviour.
    if cfg.get("carousel.curated_screens_only", True):
        names = {str(n).lower() for n in (cfg.get("linkedin.screens", []) or [])}
        curated = [p for p in local if p.name.lower() in names]
        if curated:
            local = curated
    stock: list[Path] = []
    # AI photo(s) for the news beats go FIRST, so the hook (and optionally the
    # news beat) use them and Pexels only fills what is left. Generated once
    # per story per day — see ai_image.news_image — so the retry ladder and
    # the feed/video double call never bill twice.
    from . import ai_image
    if ai_image.enabled(cfg):
        n_ai = max(0, min(2, int(cfg.get("media.ai_image.per_story", 1))))
        headline = slides[0].headline if slides else ""
        for s in slides[:n_ai]:
            img = ai_image.news_image(cfg, s.keyword, headline + " " + s.headline)
            if img:
                stock.append(img)
    for s in slides[:2]:                       # only the news beats need stock
        if s.keyword:
            stock.extend(pexels_images(cfg, s.keyword, 4, workdir / "pexels"))

    used: set[Path] = set()
    picks: list[tuple[Path | None, bool]] = []
    for i, s in enumerate(slides):
        wants_app = s.role in ("solucao", "aplicacao", "conclusao")
        primary, secondary = (local, stock) if wants_app else (stock, local)
        chosen: Path | None = None
        is_shot = wants_app
        for source, flag in ((primary, wants_app), (secondary, not wants_app)):
            fresh = [p for p in source if p not in used]
            if fresh:
                chosen, is_shot = fresh[0], flag
                used.add(chosen)
                break
        if chosen is None:
            # Everything already used in this run — repeat rather than go blank.
            pool = primary or secondary
            if pool:
                chosen = pool[i % len(pool)]
                is_shot = wants_app if pool is primary else not wants_app
        picks.append((chosen, is_shot))

    # Second visual per beat (video only). A 10-second beat on one slow-zoom
    # photo is where shorts lose people; a cut halfway through keeps the eye
    # busy. Same pools, same least-used-first order, nothing reused within
    # the run while a fresh file exists. The feed slides ignore this list.
    if extras is not None:
        for i, s in enumerate(slides):
            wants_app = s.role in ("solucao", "aplicacao", "conclusao")
            primary, secondary = (local, stock) if wants_app else (stock, local)
            second: tuple[Path | None, bool] = (None, False)
            for source, flag in ((primary, wants_app), (secondary, not wants_app)):
                fresh = [p for p in source if p not in used]
                if fresh:
                    second = (fresh[0], flag)
                    used.add(fresh[0])
                    break
            if second[0] is None and primary:
                # pools exhausted: reuse something, but never the beat's own first image
                alt = [p for p in primary if p != picks[i][0]]
                if alt:
                    second = (alt[i % len(alt)], wants_app)
            extras.append(second)
    return picks


def _prepared(cfg: Config, src: Path | None, w: int, h: int,
              dest: Path) -> Path | None:
    """Fit one original to w x h. None passes through (flat background)."""
    if src is None:
        return None
    from .media import prepare_image
    return prepare_image(src, w, h, dest)


def _slide_image(cfg: Config, slide: Slide, index: int, total: int,
                 background: Path | None, out_path: Path,
                 w: int, h: int, *, screenshot: bool = False) -> Path | None:
    """One slide rendered at w x h. Accent-safe (this channel writes Portuguese).

    `screenshot` changes the treatment of the background. A photo is scenery:
    blurring and darkening it pushes it back so the headline reads. An app
    screenshot is the CONTENT — the whole reason the slide exists is to let
    someone see the interface — so it stays sharp and bright, and the headline
    is kept legible by a taller, stronger gradient confined to the bottom
    instead of by degrading the whole image.
    """
    from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageOps
    from .thumbnail import _font
    try:
        if background and Path(background).exists():
            with Image.open(background) as im:
                bg = ImageOps.fit(im.convert("RGB"), (w, h), Image.LANCZOS)
        else:
            bg = Image.new("RGB", (w, h), (16, 46, 38))
        if screenshot:
            # An app screenshot is already full of text. Laying the headline on
            # top of it puts two competing sentences in the same pixels, which
            # is what a gradient cannot fix. Instead the screenshot keeps the
            # top of the frame at full width and a SOLID band takes the bottom,
            # so the two never overlap.
            band_top = int(h * 0.62)
            canvas = Image.new("RGB", (w, h), (11, 33, 27))
            with Image.open(background) as im:      # re-open: bg was cropped
                shot = im.convert("RGB")
            # Trim the phone's status bar (clock, wifi, battery). It is the
            # detail that makes a screenshot read as "somebody's phone" rather
            # than as product material.
            trim = int(shot.height * float(cfg.get("carousel.screenshot_crop_top", 0.05)))
            if 0 < trim < shot.height // 3:
                shot = shot.crop((0, trim, shot.width, shot.height))
            scale = w / shot.width
            shot = shot.resize((w, max(1, int(shot.height * scale))), Image.LANCZOS)
            canvas.paste(shot.crop((0, 0, w, min(shot.height, band_top))), (0, 0))
            draw_band = ImageDraw.Draw(canvas)
            draw_band.rectangle([0, band_top, w, h], fill=(11, 33, 27))
            # Short feather so the band does not read as a hard seam.
            feather = int(h * 0.05)
            fade = Image.new("L", (1, feather))
            for y in range(feather):
                fade.putpixel((0, y), int(255 * (y / max(1, feather - 1))))
            canvas.paste(Image.new("RGB", (w, feather), (11, 33, 27)),
                         (0, band_top - feather),
                         fade.resize((w, feather)))
            bg = canvas
        else:
            bg = bg.filter(ImageFilter.GaussianBlur(2.0))
            bg = ImageEnhance.Brightness(bg).enhance(0.72)
            grad = Image.new("L", (1, h))
            for y in range(h):
                grad.putpixel((0, y), int(225 * max(0.0, (y / h) - 0.30) / 0.70))
            bg = Image.composite(Image.new("RGB", (w, h), (0, 0, 0)),
                                 bg, grad.resize((w, h)))

        draw = ImageDraw.Draw(bg)
        emerald, amber, white = (16, 185, 129), (255, 191, 0), (255, 255, 255)

        # Role badge, top-left.
        label = ROLE_LABELS.get(slide.role, slide.role.upper())
        bf = _font(max(30, w // 26))
        tw = draw.textlength(label, font=bf)
        pad, bx, by = 18, int(w * 0.055), int(h * 0.05)
        draw.rectangle([bx - pad, by - pad // 2, bx + tw + pad, by + bf.size + 12],
                       fill=emerald)
        draw.text((bx, by), label, font=bf, fill=white)

        # Headline, bottom-anchored. \w keeps accented letters — the original
        # post renderer used [A-Za-z0-9]+ and split "caríssimo" into two words.
        words = re.findall(r"[\wÀ-ÿ'?!%$.,-]+", slide.headline)[:9]
        per_line = 3 if len(words) > 6 else 2
        lines = [" ".join(words[i:i + per_line]).upper()
                 for i in range(0, len(words), per_line)][:4]
        fsize = int(w * 0.105)
        font = _font(fsize)
        maxw = int(w * 0.86)
        while fsize > int(w * 0.045) and lines and \
                max(draw.textlength(l, font=font) for l in lines) > maxw:
            fsize -= 6
            font = _font(fsize)
        line_h = int(fsize * 1.16)
        if screenshot:
            # Keep the whole headline inside the solid band; a line that spills
            # above it lands back on the screenshot and the overlap returns.
            room = h - int(h * 0.62) - int(h * 0.09)
            while len(lines) * line_h > room and fsize > int(w * 0.045):
                fsize -= 6
                font = _font(fsize)
                line_h = int(fsize * 1.16)
        y = h - int(h * 0.11) - line_h * len(lines)
        for i, line in enumerate(lines):
            draw.text((int(w * 0.055), y + i * line_h), line, font=font,
                      fill=amber if i == len(lines) - 1 and len(lines) > 1 else white,
                      stroke_width=max(4, fsize // 15), stroke_fill=(0, 0, 0))

        # Slide counter, bottom-right — tells the reader to keep swiping.
        cf = _font(max(26, w // 32))
        counter = f"{index}/{total}"
        draw.text((w - int(w * 0.055) - draw.textlength(counter, font=cf),
                   h - int(h * 0.055)), counter, font=cf, fill=white)

        out_path.parent.mkdir(parents=True, exist_ok=True)
        bg.save(out_path, quality=92)
        return out_path
    except Exception as exc:  # noqa: BLE001
        print(f"  [carousel] slide {index} failed: {exc}")
        return None


# --------------------------------------------------------------------------
# text deliverables
# --------------------------------------------------------------------------
def _hashtags(cfg: Config, n: int = 8) -> str:
    tags = list(cfg.get("youtube.default_tags", []) or []) + \
        list(cfg.get("youtube.tag_pool", []) or [])
    out, seen = [], set()
    for t in tags:
        slug = re.sub(r"[^a-z0-9]", "", str(t).lower())
        if slug and slug not in seen:
            seen.add(slug)
            out.append(f"#{slug}")
        if len(out) >= n:
            break
    return " ".join(out)


def _links(cfg: Config, source: str = "instagram") -> str:
    """CTA + tracked app link for the surface this text is going to."""
    from .links import cta_block, social_block
    return "\n\n".join(x for x in (cta_block(cfg, source, content="carousel"),
                                   social_block(cfg)) if x)


def _source_label(item: NewsItem) -> str:
    """A publishable source name.

    Google News items carry the SEARCH QUERY in `source`, so the raw value
    reads `"ANVISA medicamento OR "farmácia popular"" - Google Notícias` — fine
    in a log, embarrassing in a published caption. When the value looks like a
    query, fall back to the domain the story actually came from.
    """
    raw = (item.source or "").strip()
    looks_like_query = ('"' in raw or " OR " in raw or len(raw) > 45)
    if raw and not looks_like_query:
        return raw
    m = re.search(r"https?://(?:www\.)?([^/]+)", item.link or "")
    return m.group(1) if m else (raw.split(" - ")[-1] if " - " in raw else raw)


def build_caption(cfg: Config, slides: list[Slide], item: NewsItem) -> str:
    """Caption for the carousel — the slides carry the story, so this is short."""
    parts = [slides[0].headline, "", slides[1].body if len(slides) > 1 else ""]
    parts += ["", "➡️ Arraste para ver o que fazer.", ""]
    suffix = cfg.get("instagram.caption_suffix", "")
    if suffix:
        parts += [suffix, ""]
    parts += [f"Fonte: {_source_label(item)}", "", _links(cfg), "", _hashtags(cfg)]
    return "\n".join(p for p in parts if p is not None).strip()


def build_single_post(cfg: Config, slides: list[Slide], item: NewsItem) -> str:
    """One post carrying EVERYTHING in its text, for a single image or a
    text-only platform — the whole five beats, in order, spelled out."""
    lines = [slides[0].headline.upper(), ""]
    for s in slides[1:]:
        if s.body:
            lines.append(s.body)
            lines.append("")
    suffix = cfg.get("instagram.caption_suffix", "")
    if suffix:
        lines += [suffix, ""]
    lines += [f"Fonte: {_source_label(item)} — {item.link}", "", _links(cfg), "",
              _hashtags(cfg)]
    return "\n".join(lines).strip()


# --------------------------------------------------------------------------
# video version
# --------------------------------------------------------------------------
def _script_from_slides(cfg: Config, slides: list[Slide], title: str) -> VideoScript:
    """Slide 1 becomes the hook; 2..5 become sections. The outro is left empty
    because slide 5 IS the close — appending the channel outro on top would
    say the call to action twice."""
    return VideoScript(
        title=title[:95],
        hook=slides[0].narration,
        sections=[ScriptSection(s.role, s.narration, s.keyword) for s in slides[1:]],
        outro="",
    )


def render_carousel_video(cfg: Config, slides: list[Slide], item: NewsItem,
                          pkg: Path,
                          picks: list[tuple[Path | None, bool]],
                          extras: list[tuple[Path | None, bool]] | None = None) -> Path | None:
    """The five slides as a vertical video: narration, music, subtitles and a
    different random transition at each cut. Reuses the normal render path."""
    from . import pipeline
    from .media import pick_music
    from .subtitles import build_ass
    from .video import render_video

    w = int(cfg.get("video.short.width", 1080))
    h = int(cfg.get("video.short.height", 1920))
    workdir = cfg.output_dir / "_work_carousel"
    workdir.mkdir(parents=True, exist_ok=True)

    script = _script_from_slides(cfg, slides, item.clean_title)
    voice = pipeline._synthesize_script(script, cfg, workdir)
    if not voice.blocks:
        print("  [carousel] no narration produced — skipping the video")
        return None
    # Keep it a Short. The shorts pipeline always had this guard; the carousel
    # path did not, and with Alice's slower read plus the new 0.8 s tail a
    # 134-word script came out at 58.5 s — one long sentence away from 60 s,
    # where YouTube stops treating it as a Short. Speed-up is capped at 1.2x
    # inside _fit_voice; subtitles and block timings are rescaled with it.
    tail = max(0.0, float(cfg.get("video.tail_seconds", 0.8) or 0))
    max_s = float(cfg.get("video.short.max_seconds", 58))
    voice = pipeline._fit_voice(voice, max_s - tail, workdir)

    # Same picks as the feed slides, prepared at 9:16 instead of 4:5.
    backgrounds = [(_prepared(cfg, src, w, h, workdir / f"bg_{i}.jpg"), shot)
                   for i, (src, shot) in enumerate(picks)]
    per_beat = max(1, min(2, int(cfg.get("carousel.visuals_per_beat", 2))))
    seconds = [(_prepared(cfg, src, w, h, workdir / f"bg2_{i}.jpg") if src else None)
               for i, (src, _shot) in enumerate(extras or [])] if per_beat > 1 else []
    vslides: list[tuple[Path, float]] = []
    durations: list[float] = []
    sfx_at: list[float] = []
    elapsed = 0.0
    for i, (clip, _kw) in enumerate(voice.blocks):
        slide = slides[min(i, len(slides) - 1)]
        bg, _is_shot = backgrounds[i] if i < len(backgrounds) else (None, False)
        # The video uses the RAW photo, never the feed slide. The feed slide
        # has its headline burned in, and burned-in text does not survive the
        # ken-burns pan: it drifts and crops while the synchronized subtitles
        # say the same words underneath. Two moving copies of one sentence is
        # what made the first render unreadable. Here the subtitles carry the
        # words and the watermark carries the brand.
        if bg is not None:
            img = bg
        else:
            # No stock/library image at all — fall back to the text slide so
            # the video is not five seconds of black.
            img = _slide_image(cfg, slide, i + 1, len(slides), None,
                               workdir / f"vslide_{i}.jpg", w, h)
            if img is None:
                continue
        second = seconds[i] if i < len(seconds) else None
        min_cut = float(cfg.get("carousel.min_seconds_per_visual", 3.5))
        if second is not None and clip.duration >= 2 * min_cut:
            half = clip.duration / 2.0
            vslides.append((img, 0.0));    durations.append(half)
            vslides.append((second, 0.0)); durations.append(clip.duration - half)
        else:
            vslides.append((img, 0.0))
            durations.append(clip.duration)
        if i > 0:
            sfx_at.append(elapsed)            # whoosh on BEAT changes only
        elapsed += clip.duration

    if not vslides:
        return None

    # Section chips at the top, one per beat, from the narration block offsets
    # (the hook block gets none — the headline card covers it). And the hook
    # card itself: the headline, big and centred, for the first seconds.
    chips = {"noticia": "O QUE MUDOU", "impacto": "O QUE ISSO CUSTA",
             "solucao": "BEM NA MOSCA", "aplicacao": "NA PRÁTICA", "conclusao": "COMO USAR"}
    chips.update({str(k): str(v) for k, v in (cfg.get("carousel.section_chips", {}) or {}).items()})
    banners: list[tuple[float, float, str]] = []
    t = 0.0
    for i, (clip, _kw) in enumerate(voice.blocks):
        role = slides[min(i, len(slides) - 1)].role
        label = chips.get(role, "")
        if i > 0 and label:
            banners.append((t + 0.15, t + clip.duration - 0.1, label))
        t += clip.duration
    hook_secs = float(cfg.get("carousel.hook_card_seconds", 2.6) or 0)
    hook = None
    if hook_secs > 0 and voice.blocks:
        hook = (slides[0].headline, 0.0, min(hook_secs, voice.blocks[0][0].duration))
    ass = build_ass(voice.sub_segments, cfg, width=w, height=h, is_short=True,
                    banners=banners, hook=hook)
    out = pkg / "video.mp4"
    render_video(vslides, durations, voice.voice_path, ass, cfg,
                 is_short=True, music_path=pick_music(cfg), out_path=out,
                 sfx_at=sfx_at)
    return out if out.exists() else None


# --------------------------------------------------------------------------
# orchestration
# --------------------------------------------------------------------------
def _maybe_upload_youtube(cfg: Config, video: Path, slides: list[Slide],
                          item: NewsItem, pkg: Path) -> None:
    """Upload the carousel video to YouTube as a Short.

    Honours youtube.privacy, so with the default "scheduled" it lands PRIVATE
    and auto-publishes at the next free slot (youtube.schedule.times) instead
    of going live the moment the render finishes.
    """
    if not cfg.get("carousel.publish_youtube", False):
        return
    if not cfg.get("youtube.enabled", False):
        print("  [carousel] youtube skipped (youtube.enabled is false)")
        return
    from .youtube_upload import upload_video
    title = slides[0].headline if slides else item.clean_title
    description = (pkg / "post.txt").read_text(encoding="utf-8")
    print("  [carousel] uploading the video to YouTube…")
    from . import ai_image
    synthetic = ai_image.used_today(cfg, slides[0].headline + " " + slides[0].headline) if slides else False
    vid = upload_video(cfg, video, title, description=description,
                       synthetic_media=synthetic,
                       tags=list(cfg.get("youtube.default_tags", []) or []),
                       links=[item.link] if item.link else None,
                       is_short=True)
    print(f"  [carousel] youtube {'ok: ' + vid if vid else 'FAILED'}")


def _maybe_publish_reel(cfg: Config, video: Path, pkg: Path) -> None:
    """Publish the carousel video as an Instagram Reel, if both switches allow.

    Two switches on purpose: instagram.enabled is the global "this project may
    post to Instagram", carousel.publish_reel is "and the carousel video is one
    of the things it may post". A Reel goes live the instant it is accepted —
    there is no scheduling and no private mode — so this stays opt-in.
    """
    if not cfg.get("carousel.publish_reel", False):
        return
    if not cfg.get("instagram.enabled", False):
        print("  [carousel] reel skipped (instagram.enabled is false)")
        return
    from .instagram_upload import upload_reel
    caption = (pkg / "carousel.txt").read_text(encoding="utf-8")[:2100]
    print("  [carousel] publishing the video as an Instagram Reel…")
    ok = upload_reel(cfg, video, caption, publish=True)
    print(f"  [carousel] reel {'published' if ok else 'FAILED'}")


def make_carousel(cfg: Config, topic: str | None = None,
                  *, with_video: bool | None = None) -> Path | None:
    """Build the whole package. Returns its folder."""
    if with_video is None:
        with_video = bool(cfg.get("carousel.video", True))

    items = fetch_news(cfg, limit=25)
    if topic:
        low = topic.lower()
        items = [i for i in items if low in i.clean_title.lower()] or items
    if not items:
        print("No news found for a carousel.")
        return None

    # Drop what this channel already covered. The carousel never consulted the
    # history — the rest of the pipeline always did — so it re-picked the same
    # Anvisa/Mounjaro story on consecutive days. Demand ranking makes that
    # worse, not better: whatever ranked first yesterday ranks first today.
    from .history import History
    hist = History(cfg)
    fresh = hist.filter_fresh(items)
    if not fresh:
        # Never skip a day: when the price/regulation beat is exhausted, widen
        # to health and medical-discovery stories (theme.fallback_queries).
        # A broader story that still serves the audience beats both a repeat
        # and a silent gap in the channel.
        print("  [carousel] all of today's stories were already covered — "
              "widening to health/discovery topics")
        from .search_news import search_news
        wider = search_news(cfg, cfg.get("theme.fallback_queries", []) or [],
                            per_query=8)
        fresh = hist.filter_fresh(wider)
        if not fresh:
            print("  [carousel] nothing fresh even after widening — "
                  "nothing generated today.")
            return None
    items = fresh

    # Put the stories people are actually searching for at the front, THEN
    # apply the sensitivity filter below. Ranking first and filtering second
    # matters: filtering first would leave the order untouched and the demand
    # signal would only ever break ties.
    from .keywords import rank_items
    items = rank_items(cfg, items)

    # Prefer a story that can carry the product close. Dropping only the pitch
    # is not enough: a story about a named person's illness fundraiser still
    # turns somebody's misfortune into channel content, pitch or no pitch. So
    # such a story is SKIPPED and the next one is used instead.
    item = None
    for candidate in items:
        allowed, term = bridge_allowed(
            cfg, f"{candidate.clean_title} {candidate.clean_summary}")
        if allowed:
            item = candidate
            break
        print(f"  [carousel] skipping (matched '{term}'): "
              f"{candidate.clean_title[:60]}")
    if item is None:
        # Every candidate was sensitive. Better to publish nothing today than
        # to build the channel's daily post out of someone's hardship.
        print("  [carousel] every story today is about someone in hardship — "
              "nothing generated. Run again later or pass a topic.")
        return None
    print(f"Carousel from: {item.clean_title[:80]}")

    slides = write_carousel(cfg, item)
    if not slides:
        return None

    stamp = time.strftime("%Y%m%d_%H%M%S")
    pkg = cfg.output_dir / "carousels" / f"carousel_{stamp}"
    pkg.mkdir(parents=True, exist_ok=True)

    # Feed images (4:5 — the tallest ratio Instagram and LinkedIn both accept,
    # so it takes the most feed height without being cropped).
    fw = int(cfg.get("carousel.width", 1080))
    fh = int(cfg.get("carousel.height", 1350))
    feed_work = cfg.output_dir / "_work_carousel_feed"
    feed_work.mkdir(parents=True, exist_ok=True)
    extras: list[tuple[Path | None, bool]] = []
    picks = pick_sources(cfg, slides, feed_work, extras=extras)
    backgrounds = [(_prepared(cfg, src, fw, fh, feed_work / f"feed_{i}.jpg"), shot)
                   for i, (src, shot) in enumerate(picks)]
    for i, slide in enumerate(slides):
        bg, is_shot = backgrounds[i] if i < len(backgrounds) else (None, False)
        _slide_image(cfg, slide, i + 1, len(slides), bg,
                     pkg / f"slide_{i + 1}.jpg", fw, fh, screenshot=is_shot)

    # Bump the use count of every library file that ended up in the package.
    # Without this the least-used-first ordering never learns anything and the
    # rotation stands still — which is exactly why slides 3-5 kept showing the
    # same three screenshots run after run.
    from .media_log import record_usage
    picked = [(src, None) for src, _ in picks + extras if src is not None]
    if picked:
        record_usage(cfg, pkg / "video.mp4", picked)

    (pkg / "carousel.txt").write_text(build_caption(cfg, slides, item), encoding="utf-8")
    (pkg / "post.txt").write_text(build_single_post(cfg, slides, item), encoding="utf-8")
    # TikTok gets its own caption (no links, no drug-brand hashtags) — using
    # post.txt there got posts locked for "community guidelines violation".
    from .social_captions import build_tiktok_caption
    (pkg / "tiktok.txt").write_text(build_tiktok_caption(
        cfg, slides[0].headline, slides[1].body if len(slides) > 1 else ""),
        encoding="utf-8")
    (pkg / "meta.json").write_text(json.dumps({
        "created": time.strftime("%Y-%m-%d %H:%M"),
        "headline": item.clean_title,
        "source": item.source,
        "link": item.link,
        # narration is stored too: without it, diagnosing a too-short video
        # meant guessing from the duration alone.
        "slides": [{"role": s.role, "headline": s.headline, "body": s.body,
                    "narration": s.narration, "keyword": s.keyword}
                   for s in slides],
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    # LinkedIn card — one image, generated here so it carries the same story.
    # Posting is manual (see config linkedin:). Never let it break the run:
    # the video and its uploads matter more than a JPG you post by hand.
    if cfg.get("linkedin.enabled", True):
        try:
            from .linkedin_card import card_from_package
            from .linkedin_card import text_from_package
            card = card_from_package(cfg, pkg)
            if card:
                print(f"  [linkedin] card: {card}")
            if text_from_package(cfg, pkg):
                print("  [linkedin] text: linkedin.txt (link goes in the first comment)")
        except Exception as exc:  # noqa: BLE001
            print(f"  [linkedin] card failed ({exc}) — continuing without it")

    if with_video:
        print("  building the video version…")
        # A render failure must not crash the routine, and must not be swallowed
        # either: returning None hands the day to the retry ladder in
        # `main.py daily`, which tries again in 30 minutes. Nothing has been
        # published at this point, so a retry cannot duplicate an upload. The
        # slides already written above stay on disk either way, so a bad render
        # never costs you the carousel you post by hand.
        try:
            video = render_carousel_video(cfg, slides, item, pkg, picks, extras)
        except Exception as exc:  # noqa: BLE001
            print(f"  [carousel] video render FAILED: {exc}")
            print(f"  [carousel] slides are still in {pkg}")
            return None
        if video:
            print(f"  video: {video}")
            _maybe_upload_youtube(cfg, video, slides, item, pkg)
            _maybe_publish_reel(cfg, video, pkg)

    # Mark the story covered only now, after the package actually exists — a
    # run that died mid-render must not burn the story for future days.
    hist.record("carousel", slides[0].headline if slides else item.clean_title,
                [item])

    print(f"\nCarousel package: {pkg}")
    print(f"  {len(slides)} slides + carousel.txt (caption) + post.txt (single post)")
    if (pkg / "linkedin.jpg").exists():
        print("  linkedin.jpg + linkedin.txt — post by hand on LinkedIn (link in the first comment).")
    return pkg
