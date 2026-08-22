"""YouTube Community post packages: trending topic -> viral text + branded image.

The official YouTube API cannot create Community posts (Google never exposed
that endpoint), so this generates a ready-to-paste package instead:

    output/posts/<timestamp>/
        image.jpg    <- branded 1080x1080 image
        post.txt     <- post text with hashtags (paste as-is)
        meta.json    <- topic, sources, date

Topics are picked from what users are REALLY searching right now: Google's
"related searches" + "People also ask" for the theme (via the Serper pool),
merged with the evergreen hot topics in config (price, release date, online...).
History prevents repeating a topic within the fuzzy window.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFilter, ImageEnhance, ImageOps

from .config import Config
from .history import History
from .llm import get_llm
from .script_writer import _ask_json, _avoid_block, _lang_block
from .search_news import _State, _env_keys, search_news
from .thumbnail import _font, _wrap


# --------------------------------------------------------------------------
# trending topics — what users are actually typing into Google
# --------------------------------------------------------------------------
def news_topics(cfg: Config, hist: History, limit: int = 8) -> list[str]:
    """Genuinely NEW subjects: fresh headlines the channel hasn't covered.

    This is the primary source — evergreen searches like "GTA 6 price" are
    always trending, so relying on them alone made every post the same topic.
    """
    from .history import _key
    from .news import fetch_news
    fresh = hist.filter_fresh(fetch_news(cfg, limit=40))

    core, generic, seen = [], [], set()
    for item in fresh:
        # Drop the outlet suffix ("... - GAMINGbible", "... | IGN") — it isn't
        # part of the subject and pollutes the search query.
        title = re.split(r"\s+[-|–—]\s+(?=\S{2,20}\s*$)|\s+\|\s+.*$",
                         item.clean_title)[0].strip()
        if len(title) < 16:
            continue
        k = _key(title)
        if k in seen:          # same story from two feeds
            continue
        seen.add(k)
        # Stories explicitly about GTA 6 beat generic GTA listicles.
        target = core if re.search(r"gta\s*(6|vi)\b|grand theft auto\s*(6|vi)\b",
                                   title, re.I) else generic
        target.append(title)
    return (core + generic)[:limit]


def trending_queries(cfg: Config) -> list[str]:
    """Related searches + People-also-ask questions for the theme (real user
    demand). Falls back to config posts.topics when Serper is unavailable."""
    topic = cfg.get("theme.topic", "")
    seeds: list[str] = list(cfg.get("posts.topics", []) or [])
    state = _State(cfg.output_dir / "search_keys_state.json")
    keys = [k for k in _env_keys("SERPER_API_KEYS") if state.usable(k)]
    found: list[str] = []
    if keys:
        try:
            from .search_news import locale_of
            r = requests.post(
                "https://google.serper.dev/search",
                headers={"X-API-KEY": keys[0], "Content-Type": "application/json"},
                json={"q": topic, "num": 10, **locale_of(cfg)},
                timeout=30,
            )
            if r.status_code in (403, 429):
                state.mark(keys[0])
            else:
                r.raise_for_status()
                data = r.json()
                for rs in data.get("relatedSearches", []) or []:
                    q = (rs.get("query") or "").strip()
                    if q:
                        found.append(q)
                for paa in data.get("peopleAlsoAsk", []) or []:
                    q = (paa.get("question") or "").strip()
                    if q:
                        found.append(q)
        except Exception as exc:  # noqa: BLE001
            print(f"  [posts] trending lookup failed: {exc}")
    # Real user searches first, evergreen seeds after.
    out, seen = [], set()
    for q in found + seeds:
        k = q.lower()
        if k not in seen:
            seen.add(k)
            out.append(q)
    return out


# --------------------------------------------------------------------------
# branded 1:1 image
# --------------------------------------------------------------------------
def make_post_image(cfg: Config, headline: str, background: Path | None,
                    out_path: Path, size: int = 1080,
                    badge: str | None = None) -> Path | None:
    try:
        if background and Path(background).exists():
            with Image.open(background) as im:
                bg = ImageOps.fit(im.convert("RGB"), (size, size), Image.LANCZOS)
        else:
            bg = Image.new("RGB", (size, size), (24, 12, 48))
        bg = bg.filter(ImageFilter.GaussianBlur(1.5))
        bg = ImageEnhance.Brightness(bg).enhance(0.9)

        # Bottom gradient for text contrast.
        grad = Image.new("L", (1, size))
        for y in range(size):
            grad.putpixel((0, y), int(235 * max(0.0, (y / size) - 0.35) / 0.65))
        grad = grad.resize((size, size))
        bg = Image.composite(Image.new("RGB", (size, size), (0, 0, 0)), bg, grad)

        draw = ImageDraw.Draw(bg)
        accent = (255, 210, 0)
        white = (255, 255, 255)

        # Badge top-left: the story's nature (BREAKING / LEAK / RUMOR...) next
        # to the theme — a red label is the single biggest "stop scrolling"
        # cue on a feed image.
        label = " ".join(x for x in [(badge or "").upper(),
                                     cfg.get("theme.topic", "").upper()] if x)
        if label:
            bf = _font(52)
            pad = 16
            tw = draw.textlength(label, font=bf)
            draw.rectangle([40 - pad, 48 - pad // 2, 40 + tw + pad, 48 + 62], fill=(200, 16, 46))
            draw.text((40, 48), label, font=bf, fill=white)

        # Headline: big, bottom-anchored.
        words = re.findall(r"[A-Za-z0-9'?!$%.]+", headline)[:8]
        lines = _wrap(words, per_line=2)
        fsize = 120 if len(lines) <= 2 else 96
        font = _font(fsize)
        maxw = int(size * 0.88)
        while fsize > 48 and max(draw.textlength(l, font=font) for l in lines) > maxw:
            fsize -= 8
            font = _font(fsize)
        line_h = int(fsize * 1.14)
        y = size - 70 - line_h * len(lines)
        for i, line in enumerate(lines):
            color = accent if i == len(lines) - 1 and len(lines) > 1 else white
            draw.text((54, y + i * line_h), line, font=font, fill=color,
                      stroke_width=max(4, fsize // 16), stroke_fill=(0, 0, 0))

        # Logo bottom-right.
        logo = cfg.get("video.watermark")
        if logo and cfg.path_of(logo).exists():
            with Image.open(cfg.path_of(logo)) as lg:
                lg = lg.convert("RGBA")
                lw = int(size * 0.14)
                lh = int(lg.height * lw / lg.width)
                lg = lg.resize((lw, lh), Image.LANCZOS)
                bg.paste(lg, (size - lw - 30, 30), lg)

        out_path.parent.mkdir(parents=True, exist_ok=True)
        bg.save(out_path, "JPEG", quality=92)
        return out_path
    except Exception as exc:  # noqa: BLE001
        print(f"  [posts] image failed: {exc}")
        return None


# --------------------------------------------------------------------------
# the post package
# --------------------------------------------------------------------------
def make_post(cfg: Config, topic: str | None = None) -> Path | None:
    """`topic` (optional): a user-requested subject, e.g. "GTA 6 trailer 3
    release date". When given, it overrides the trending-topic picker (and the
    recently-covered check — an explicit request wins). Facts still come only
    from real news found for that topic."""
    sandbox = bool(cfg.get("posts.sandbox", False))
    mode = "SANDBOX" if sandbox else "LIVE"
    print(f"== Building a COMMUNITY POST package ({mode}) ==")
    llm = get_llm(cfg)
    if llm is None:
        print("  Posts need a working LLM — skipping.")
        return None

    hist = History(cfg)

    explicit = bool(topic and topic.strip())
    if explicit:
        # 1) User-requested topic — explicit request overrides rotation/history.
        candidates = [topic.strip()]
        print(f"  Requested topic: {candidates[0]}")
    else:
        # 1) Candidate topics, best first:
        #      a) fresh REAL headlines (actual news — most interesting)
        #      b) what users are searching right now (Serper), rotated
        #      c) evergreen config seeds, rotated
        #    Rotation = least-recently-used first, and anything posted in the
        #    last 10 days goes to the back. Without this the picker always
        #    returned the same first entry ("GTA 6 price") forever.
        headlines = news_topics(cfg, hist)
        evergreen = hist.rank_topics(trending_queries(cfg))
        cool = [q for q in evergreen if not hist.topic_cooling(q)]
        hot = [q for q in evergreen if hist.topic_cooling(q)]
        candidates = headlines + cool + hot
        if not candidates:
            print("  No topics available (no news and no trending queries).")
            return None
        print(f"  Candidates: {len(headlines)} fresh headlines + "
              f"{len(cool)} rotated topics ({len(hot)} on cooldown)")

    # 2) Real news for the topic (facts to post about). Walk the candidates
    #    until one actually has fresh news — a topic with nothing new is
    #    skipped instead of producing another rehash of old facts.
    topic_q, news = None, []
    for cand in candidates[:6]:
        found = search_news(cfg, [cand], per_query=6)
        # For an explicit request we keep already-covered stories too.
        fresh = found if explicit else hist.filter_fresh(found)
        if fresh:
            topic_q, news = cand, fresh
            break
        print(f"  - no fresh news for '{cand[:60]}' — trying next")
    if not news:
        print("  No real news found for any candidate — skipping (no invented content).")
        return None
    print(f"  TOPIC: {topic_q}")
    src_block = "\n".join(
        f"- {n.clean_title} — {n.clean_summary[:120]} ({n.source})" for n in news[:5]
    )

    # 3) Viral post text (strictly factual).
    topic = cfg.get("theme.topic", "")
    avoid = _avoid_block(hist.recent_content())
    prompt = f"""Write a VIRAL YouTube Community post for a {topic} fan channel about:
"{topic_q}"

REAL facts to use (use ONLY these — invent NOTHING; label leaks/rumors as such):
{src_block}
{avoid}
GOAL: make a fan STOP scrolling, read to the end, and comment.

HOOK RULES (line 1 — the most important line):
- Lead with the single most surprising CONCRETE fact: a number, a date, a
  price, a name, a change. Specific beats vague, always.
- Use a curiosity gap or tension: something confirmed vs expected, something
  that changed, something nobody noticed.
- 3-9 words. It must be readable at a glance.
- GOOD: "Rockstar just confirmed the $100 edition 🚨" / "Trailer 3 has a
  date — and it's sooner than you think 👀" / "One line in the FAQ changed
  everything about GTA Online"
- BAD (never write these): "Big news about GTA 6!" / "You won't believe
  this!" / "Here's what we know so far" / anything vague or clickbait with
  no real fact behind it.

BODY RULES:
- 3-5 short lines, one idea per line, emoji where natural (🔥🚨👀🎮📅💰).
- Concrete facts only: numbers, dates, names, quotes. NO filler, NO markdown.
- If something is a leak/rumor, say "leak"/"rumor" explicitly — credibility
  is what makes fans come back.
- Last line = a question fans will actually answer (day-one buy? which
  character? worth the price? agree?) ending with 👇

ALSO RETURN:
- "image_text": the HEADLINE for the image — 3-5 punchy words, the single
  most clickable fact, no channel name (e.g. "$100 PRICE CONFIRMED",
  "TRAILER 3 DATE LEAKED", "MAP IS 2X BIGGER").
- "badge": ONE word label matching the truth of the story — exactly one of:
  BREAKING (confirmed news today), CONFIRMED (official), LEAK (leaked info),
  RUMOR (unconfirmed), UPDATE (developing story).

{_lang_block(cfg)}
Return ONLY JSON:
{{"post": "...", "hashtags": ["#..."], "image_text": "...", "badge": "..."}}"""

    data = _ask_json(llm, prompt)
    if not data or not data.get("post"):
        print("  LLM did not produce a post — skipping.")
        return None

    post_text = str(data["post"]).strip()
    hashtags = [h if h.startswith("#") else f"#{h}"
                for h in (data.get("hashtags") or [])][:4]
    if hashtags:
        post_text += "\n\n" + " ".join(hashtags)
    image_text = str(data.get("image_text") or topic_q).strip()
    badge = str(data.get("badge") or "").strip().upper()
    if badge not in {"BREAKING", "CONFIRMED", "LEAK", "RUMOR", "UPDATE"}:
        badge = "BREAKING"

    # 4) Branded image: AI-generated background tailored to the topic
    #    (Cloudflare FLUX, free tier), falling back to the channel's art.
    #    When the user pinned a media folder (CLI folder argument), their
    #    images win over AI generation.
    background = None
    if not cfg.get("media.override_dir"):
        from .imagen import generate_image
        stamp_bg = time.strftime("%Y%m%d_%H%M%S")
        # Avoid brand names in the scene prompt — they make FLUX paint fake logos.
        background = generate_image(
            cfg,
            f"Dramatic 1980s Miami night street scene evoking {topic_q}, neon lights, "
            f"palm trees, sports cars in the distance",
            cfg.output_dir / "posts" / f"_bg_{stamp_bg}.png",
        )
    if background is None:
        from .media import gather_local_images
        from .media_log import order_by_usage
        pool = gather_local_images(cfg, "horizontal") or gather_local_images(cfg, "vertical")
        # Least-used first — posts rotate the whole library before repeating.
        background = order_by_usage(cfg, pool)[0] if pool else None

    stamp = time.strftime("%Y%m%d_%H%M%S")
    pkg = cfg.output_dir / "posts" / f"post_{stamp}"
    pkg.mkdir(parents=True, exist_ok=True)
    make_post_image(cfg, image_text, background, pkg / "image.jpg", badge=badge)
    (pkg / "post.txt").write_text(post_text, encoding="utf-8")
    (pkg / "meta.json").write_text(json.dumps({
        "topic": topic_q,
        "badge": badge,
        "created": time.strftime("%Y-%m-%d %H:%M"),
        "sources": [n.link for n in news[:5]],
        "image_text": image_text,
    }, indent=2), encoding="utf-8")

    # 5) Remember the topic + stories so tomorrow picks something new;
    #    log which background image this post consumed (rotation).
    hist.record("post", f"POST: {topic_q}", news[:3])
    hist.record_topic(topic_q)   # <- rotation: this subject goes to the back
    if background is not None and cfg.output_dir.resolve() not in Path(background).resolve().parents:
        from .media_log import record_usage
        record_usage(cfg, pkg, [(Path(background), None)])

    handle = cfg.get("channel.handle", "").lstrip("@")
    community_url = (f"https://www.youtube.com/@{handle}/community"
                     if handle else "https://studio.youtube.com (Content -> Posts)")
    print("\n" + "=" * 60)
    print("POST READY — paste it in 20 seconds:")
    print(f"  1. Open  {community_url}")
    print("     (or YouTube Studio -> Content -> Posts -> Create)")
    print(f"  2. Attach image: {pkg / 'image.jpg'}")
    print("  3. Paste this text:")
    print("-" * 60)
    print(post_text)
    print("=" * 60)

    # Cross-post the same image+text to Instagram (feed photo, LIVE at once).
    if sandbox:
        print("  [sandbox] Publishing disabled. Package generated only.")
    elif cfg.get("instagram.post_enabled", False):
        try:
            from .instagram_upload import upload_post_package
            upload_post_package(cfg, pkg)  # best-effort; never fails the run
        except Exception as exc:  # noqa: BLE001
            print(f"  [instagram] post cross-post failed: {exc}")
    else:
        print("  (Instagram auto-post off — instagram.post_enabled: false;"
              " manual: python main.py igpost)")

    # Open the folder so the image is one drag away.
    try:
        import os
        os.startfile(pkg)  # noqa: S606
    except Exception:  # noqa: BLE001
        pass
    return pkg
