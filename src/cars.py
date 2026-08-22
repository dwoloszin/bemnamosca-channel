"""GTA 6 vehicles vs real-life cars — manually-triggered comparison videos.

Flow (user runs `python main.py cars [short|long]`):
  1. You paste an initial prompt (a research text like Gemini's vehicle mapping,
     or just a theme like "sports cars"). End input with a line: END
  2. The LLM structures it into car pairs (GTA name <-> real car + specs).
  3. Visuals: official GTA art from your assets (matched by name) on one side,
     REAL car photos from Wikimedia Commons (CC-licensed, credited in the
     description) on the other.
  4. Renders a Short (3 cars) or a long video (up to 10 cars, with chapters),
     then uploads like any other video (private until you flip it).

Gemini image generation was tested and requires a paid tier on the current
keys, so this uses real photos instead — which arguably fit "real counterpart"
content better anyway.
"""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path

import requests

from .config import Config
from .history import History
from .llm import get_llm
from .media import gather_local_images, music_credit, pick_music, prepare_image
from .news import NewsItem
from .pipeline import (INTRO_SECONDS, OUTRO_SECONDS, _fit_voice, _pad_voice,
                       _reset_dir, _shift_ass, _slug, _stamp,
                       _synthesize_script, _thumb_background)
from .script_writer import ScriptSection, VideoScript, _ask_json, write_seo
from .subtitles import build_ass, build_srt
from .thumbnail import make_thumbnail
from .video import render_video

_UA = {"User-Agent": "gta6-channel/1.0 (car comparison content)"}


# --------------------------------------------------------------------------
# Wikimedia Commons — real car photos (CC-licensed, credited)
# --------------------------------------------------------------------------
def commons_photo(term: str, workdir: Path) -> tuple[Path, str] | None:
    """Download the best Commons photo for `term`. Returns (path, credit)."""
    try:
        r = requests.get("https://commons.wikimedia.org/w/api.php", params={
            "action": "query", "format": "json", "generator": "search",
            "gsrsearch": f"filetype:bitmap {term}", "gsrnamespace": 6, "gsrlimit": 8,
            "prop": "imageinfo", "iiprop": "url|extmetadata|size", "iiurlwidth": 1920,
        }, headers=_UA, timeout=30)
        r.raise_for_status()
        pages = (r.json().get("query") or {}).get("pages", {})
        candidates = []
        for p in pages.values():
            ii = (p.get("imageinfo") or [{}])[0]
            if ii.get("width", 0) < 900:
                continue
            url = ii.get("thumburl") or ii.get("url")
            if not url or url.lower().endswith((".svg", ".gif")):
                continue
            meta = ii.get("extmetadata", {})
            lic = meta.get("LicenseShortName", {}).get("value", "")
            author = re.sub(r"<[^>]+>", "", meta.get("Artist", {}).get("value", "")).strip()
            candidates.append((ii.get("width", 0), url, lic, author))
        if not candidates:
            return None
        candidates.sort(reverse=True)
        _w, url, lic, author = candidates[0]
        img = None
        for attempt in range(3):
            img = requests.get(url, headers=_UA, timeout=60)
            if img.status_code == 429:   # Wikimedia rate limit — wait and retry
                time.sleep(6 * (attempt + 1))
                continue
            break
        img.raise_for_status()
        dest = workdir / ("commons_" + re.sub(r"[^a-z0-9]+", "_", term.lower())[:40] + ".jpg")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(img.content)
        credit = f'"{term}" photo: {author or "Wikimedia Commons"} ({lic or "CC"}), via Wikimedia Commons'
        return dest, credit
    except Exception as exc:  # noqa: BLE001
        print(f"  [cars] Commons photo for '{term}' failed: {exc}")
        return None


# --------------------------------------------------------------------------
# official GTA art matched by name
# --------------------------------------------------------------------------
def find_gta_art(cfg: Config, keywords: str, max_n: int = 2) -> list[Path]:
    tokens = [t for t in re.findall(r"[a-z0-9]+", (keywords or "").lower()) if len(t) > 2]
    if not tokens:
        return []
    scored: list[tuple[int, Path]] = []
    pool = gather_local_images(cfg, "horizontal") + gather_local_images(cfg, "vertical")
    seen = set()
    for p in pool:
        if p in seen:
            continue
        seen.add(p)
        name = p.name.lower()
        score = sum(1 for t in tokens if t in name)
        if score:
            scored.append((score, p))
    scored.sort(key=lambda x: -x[0])
    return [p for _s, p in scored[:max_n]]


# --------------------------------------------------------------------------
# reference wikis (e.g. gta.fandom.com Vehicles_in_GTA_VI)
# --------------------------------------------------------------------------
def _fetch_page_text(url: str, cap: int = 6000) -> str:
    """Generic HTML page -> readable text (for non-wiki reference sites)."""
    try:
        r = requests.get(url, headers=_UA, timeout=30)
        r.raise_for_status()
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(r.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        text = re.sub(r"\s+", " ", soup.get_text(" ")).strip()
        # JS-only pages yield almost nothing — not useful as reference.
        return text[:cap] if len(text) > 300 else ""
    except Exception as exc:  # noqa: BLE001
        print(f"  [cars] page fetch failed for {url}: {exc}")
        return ""


def fetch_references(cfg: Config, max_chars: int = 12000) -> str:
    """Fetch configured wiki pages via the MediaWiki API (clean wikitext with
    the vehicle tables) and return a trimmed reference block for the LLM."""
    chunks: list[str] = []
    for url in cfg.get("cars.reference_urls", []) or []:
        try:
            m = re.match(r"(https?://[^/]+)/wiki/(.+)", url)
            if not m:
                # Not a MediaWiki site (e.g. rockstargames.com, gta-6-wiki.com):
                # fetch the page and strip it to readable text.
                text = _fetch_page_text(url)
                if text:
                    name = url.split("//", 1)[-1][:60]
                    chunks.append(f"[{name}]\n{text}")
                    print(f"  [cars] reference loaded: {name} ({len(text)} chars)")
                continue
            base, page = m.group(1), m.group(2)
            wikitext = ""
            for api_path in ("/api.php", "/w/api.php"):   # Fandom vs Wikipedia
                r = requests.get(f"{base}{api_path}", params={
                    "action": "parse", "page": page, "format": "json",
                    "prop": "wikitext",
                }, headers=_UA, timeout=30)
                if r.ok and "parse" in r.text[:200]:
                    wikitext = (r.json().get("parse") or {}).get("wikitext", {}).get("*", "")
                    if wikitext:
                        break
            if not wikitext:
                continue
            # Strip templates/links noise, keep the informative text and tables.
            text = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]+)\]\]", r"\1", wikitext)
            text = re.sub(r"\{\{[^{}]*\}\}", " ", text)
            text = re.sub(r"<[^>]+>", " ", text)
            text = re.sub(r"[ \t]+", " ", text)
            chunks.append(f"[{page} — community wiki]\n{text}")
            print(f"  [cars] reference loaded: {page} ({len(text)} chars)")
        except Exception as exc:  # noqa: BLE001
            print(f"  [cars] reference fetch failed for {url}: {exc}")
    joined = "\n\n".join(chunks)
    return joined[:max_chars]


# --------------------------------------------------------------------------
# interactive input
# --------------------------------------------------------------------------
def read_initial_prompt() -> str:
    print("=" * 60)
    print("CARS COMPARISON — paste your initial prompt below.")
    print("It can be a full research text (like Gemini's vehicle mapping)")
    print("or just a theme, e.g. 'GTA 6 supercars vs real life'.")
    print("Finish with a line containing only: END")
    print("=" * 60)
    lines: list[str] = []
    for line in sys.stdin:
        if line.strip().upper() == "END":
            break
        lines.append(line.rstrip("\n"))
    return "\n".join(lines).strip()


# --------------------------------------------------------------------------
# vehicles showcase — montage of assets/images/vehicles/** in file order
# --------------------------------------------------------------------------
def _vehicle_groups(cfg: Config) -> list[tuple[str, str, list[Path]]]:
    """(category, pretty_vehicle_name, images) in folder + filename order."""
    base = cfg.path_of(cfg.get("cars.vehicles_dir", "assets/images/vehicles"))
    if not base.exists():
        return []
    cat_order = ["cars", "bikes", "boats", "planes"]
    cats = [c for c in cat_order if (base / c).is_dir()]
    cats += [d.name for d in sorted(base.iterdir()) if d.is_dir() and d.name not in cats]

    def group_key(p: Path) -> str:
        stem = re.sub(r"\.(jpg|jpeg|png|webp)$", "", p.name, flags=re.I)
        stem = re.sub(r"^(ULTIMATE_EDITION|VINTAGE_VICE_CITY_PACK)_", "", stem, flags=re.I)
        stem = re.sub(r"_\d+$", "", stem)
        return stem.replace("_", " ").strip().title()

    groups: list[tuple[str, str, list[Path]]] = []
    for cat in cats:
        by_name: dict[str, list[Path]] = {}
        order: list[str] = []
        for p in sorted((base / cat).iterdir()):
            if p.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
                continue
            k = group_key(p)
            if k not in by_name:
                by_name[k] = []
                order.append(k)
            by_name[k].append(p)
        for k in order:
            groups.append((cat, k, by_name[k]))
    return groups


def make_vehicles_showcase(cfg: Config) -> Path | None:
    """One Short showing ALL vehicle art (grouped/ordered by filename), with
    narration highlighting the most iconic ones and a name banner per vehicle."""
    print("== Building a VEHICLES SHOWCASE Short ==")
    llm = get_llm(cfg)
    if llm is None:
        print("  Needs a working LLM.")
        return None

    groups = _vehicle_groups(cfg)
    if not groups:
        print("  No images found in assets/images/vehicles/.")
        return None
    total_imgs = sum(len(g[2]) for g in groups)
    print(f"  {len(groups)} vehicles, {total_imgs} images "
          f"({', '.join(sorted(set(c for c, _n, _i in groups)))})")

    listing = "\n".join(f"- [{cat}] {name} ({len(imgs)} images)"
                        for cat, name, imgs in groups)
    prompt = f"""You are narrating a fast-cut YouTube Short showcasing ALL officially
revealed GTA 6 vehicles from Rockstar's art, in this exact on-screen order:
{listing}

Write (in ENGLISH, spoken style, energetic):
- "hook": 1 sentence ("every single GTA 6 vehicle revealed so far" energy).
- "narration": ~110 words flowing tour that follows the order above: sweep
  through categories (cars -> bikes -> boats -> planes) and CALL OUT the 4-5
  most iconic by name (e.g. Grotti Cheetah, Stock 305, Vapid Buggy, Squalo)
  with one juicy detail each — real-life inspiration ("fan-identified as...")
  or what makes it special. STRICTLY things known from official art/trailers;
  invent nothing.
- "title": catchy YouTube title, max 70 chars.

Return ONLY JSON: {{"title": "...", "hook": "...", "narration": "..."}}"""

    data = _ask_json(llm, prompt)
    if not data or not data.get("narration"):
        print("  LLM produced no narration.")
        return None

    outro = cfg.get("script.outro", "Subscribe for more.")
    script = VideoScript(
        title=(data.get("title") or "Every GTA 6 Vehicle Revealed So Far").strip()[:95],
        hook=(data.get("hook") or "Every GTA 6 vehicle revealed so far — in one video.").strip(),
        sections=[ScriptSection("Vehicles", str(data["narration"]).strip(), "gta 6 cars")],
        outro=outro,
    )

    workdir = cfg.output_dir / "_work"
    _reset_dir(workdir)

    voice = _synthesize_script(script, cfg, workdir)
    max_s = float(cfg.get("video.short.max_seconds", 58))
    voice = _fit_voice(voice, max_s - INTRO_SECONDS - OUTRO_SECONDS, workdir)

    w = int(cfg.get("video.short.width"))
    h = int(cfg.get("video.short.height"))

    # Montage: every image gets an equal slice of the whole narration.
    per_img = max(0.9, voice.duration / total_imgs)
    prep_dir = workdir / "prepared"
    slides: list[tuple[Path, float]] = []
    durations: list[float] = []
    banners: list[tuple[float, float, str]] = []

    intro_img = cfg.get("video.short.intro_image") or cfg.get("video.intro_image")
    if intro_img and cfg.path_of(intro_img).exists():
        p = prepare_image(cfg.path_of(intro_img), w, h, workdir / "intro.jpg")
        if p:
            slides.append((p, 0.0))
            durations.append(INTRO_SECONDS)

    t = 0.0  # voice-timeline cursor
    n = 0
    for cat, name, imgs in groups:
        g_start = t
        for src in imgs:
            n += 1
            p = prepare_image(src, w, h, prep_dir / f"v{n:03d}.jpg")
            if not p:
                continue
            slides.append((p, 0.0))
            durations.append(per_img)
            t += per_img
        if t > g_start:
            banners.append((g_start, t, name[:40].upper()))

    outro_img = cfg.get("video.short.outro_image") or cfg.get("video.outro_image")
    if outro_img and cfg.path_of(outro_img).exists():
        p = prepare_image(cfg.path_of(outro_img), w, h, workdir / "outro.jpg")
        if p:
            slides.append((p, 0.0))
            durations.append(OUTRO_SECONDS)

    padded = _pad_voice(voice.voice_path, INTRO_SECONDS, OUTRO_SECONDS, workdir / "voice_final.m4a")
    ass = build_ass(voice.sub_segments, cfg, width=w, height=h, is_short=True, banners=banners)
    ass = _shift_ass(ass, INTRO_SECONDS)

    out = cfg.output_dir / f"vehicles_{_slug(cfg.get('channel.slug', ''))}_{_stamp()}.mp4"
    music = pick_music(cfg)
    render_video(slides, durations, padded, ass, cfg,
                 is_short=True, music_path=music, out_path=out)

    pseudo = [NewsItem(title=f"GTA 6 vehicles showcase: {name}", summary="", link="", source="showcase")
              for _c, name, _i in groups[:6]]
    seo = write_seo(cfg, script.title, pseudo, is_short=True)
    mc = music_credit(music)
    if mc:
        seo.description += "\n\n" + mc
    srt = build_srt(voice.sub_segments, out.with_suffix(".srt"), shift=INTRO_SECONDS)
    thumb = make_thumbnail(cfg, seo.title, _thumb_background(slides), out.with_suffix(".jpg"))

    hist = History(cfg)
    from .pipeline import _finish
    _finish(cfg, out, seo, pseudo, hist, kind="showcase", is_short=True,
            srt=srt, thumb=thumb, chapters=None)
    return out


# --------------------------------------------------------------------------
# the video
# --------------------------------------------------------------------------
def make_cars_video(cfg: Config, kind: str = "short",
                    initial_prompt: str | None = None) -> Path | None:
    is_short = kind != "long"
    n_cars = int(cfg.get("cars.per_short", 3)) if is_short else int(cfg.get("cars.per_long", 10))
    words_per_car = 35 if is_short else 70

    llm = get_llm(cfg)
    if llm is None:
        print("  Cars videos need a working LLM.")
        return None

    prompt_text = (initial_prompt or "").strip() or read_initial_prompt()
    if not prompt_text:
        print("  No input given.")
        return None

    print(f"== Building a CARS {'SHORT' if is_short else 'LONG video'} ({n_cars} cars) ==")

    references = fetch_references(cfg)
    ref_block = f"""
REFERENCE MATERIAL (community wiki — use for accurate vehicle names and
fan-identified real-life counterparts):
<<<
{references}
>>>
""" if references else ""

    parse_prompt = f"""You are preparing a YouTube {'Short' if is_short else 'video'} comparing
GTA 6 vehicles with their real-life counterpart cars, for hardcore GTA fans.

USER INPUT (research text or theme):
<<<
{prompt_text[:6000]}
>>>
{ref_block}

Pick the {n_cars} BEST car pairs (most famous/most hyped). If the input lists
cars, choose from it; otherwise use well-known confirmed GTA 6 vehicles.
For each car return:
- "gta_name": the in-game vehicle name (e.g. "Grotti Cheetah Classic")
- "art_keywords": 1-2 lowercase words from the vehicle name likely to appear in
  official art filenames (e.g. "cheetah", "stock 305", "squalo", "banshee")
- "real_name": the real car (e.g. "Ferrari Testarossa")
- "commons_search": search term for a photo (make + model, e.g. "Ferrari Testarossa")
- "narration": ~{words_per_car} words in ENGLISH, spoken style: which GTA car it is,
  what real car it's based on (say "fan-identified as" — these mappings are
  community research, not Rockstar-confirmed), then REAL specs that impress:
  top speed, approximate price, engine/power, one wow-fact. Use well-known
  approximate figures with "around/about" — never invent precise numbers.

Also return:
- "title": catchy YouTube title (max 70 chars) about GTA 6 cars vs real life
- "hook": 1 spoken sentence promising the comparison (tease the most expensive
  or fastest car without naming it)

Return ONLY JSON:
{{"title": "...", "hook": "...", "cars": [{{"gta_name": "...", "art_keywords": "...",
  "real_name": "...", "commons_search": "...", "narration": "..."}}]}}"""

    data = _ask_json(llm, parse_prompt)
    if not data or not data.get("cars"):
        print("  Could not structure the input into car pairs.")
        return None
    cars = data["cars"][:n_cars]
    print(f"  Cars: {', '.join(c.get('gta_name', '?') for c in cars)}")

    outro = cfg.get("script.outro", "Subscribe for more.")
    script = VideoScript(
        title=(data.get("title") or "GTA 6 Cars vs Real Life").strip()[:95],
        hook=(data.get("hook") or "These are the real cars behind GTA 6's rides.").strip(),
        sections=[ScriptSection(
            heading=f"{c.get('gta_name', '?')} = {c.get('real_name', '?')}",
            text=str(c.get("narration", "")).strip(),
            keyword=str(c.get("real_name", "")),
        ) for c in cars if str(c.get("narration", "")).strip()],
        outro=outro,
    )
    if not script.sections:
        print("  No usable narrations produced.")
        return None

    workdir = cfg.output_dir / "_work"
    _reset_dir(workdir)

    # Voice first (defines timing).
    voice = _synthesize_script(script, cfg, workdir)
    if is_short:
        max_s = float(cfg.get("video.short.max_seconds", 58))
        voice = _fit_voice(voice, max_s - INTRO_SECONDS - OUTRO_SECONDS, workdir)

    fmt = "short" if is_short else "long"
    w = int(cfg.get(f"video.{fmt}.width"))
    h = int(cfg.get(f"video.{fmt}.height"))

    # --- visuals per block: [hook] + one block per car + [outro] -----------
    credits: list[str] = []
    generic = gather_local_images(cfg, "vertical" if is_short else "horizontal")
    prep_dir = workdir / "prepared"
    prep_n = 0

    def prep(src: Path) -> Path | None:
        nonlocal prep_n
        prep_n += 1
        return prepare_image(src, w, h, prep_dir / f"car_{prep_n:03d}.jpg")

    block_visuals: list[list[Path]] = []
    # hook block: cover art / any art
    hook_art = find_gta_art(cfg, "cover art official") or generic[:1]
    block_visuals.append([p for p in (prep(s) for s in hook_art[:1]) if p])

    for c in cars:
        vis: list[Path] = []
        for art in find_gta_art(cfg, c.get("art_keywords", "")):
            p = prep(art)
            if p:
                vis.append(p)
        got = commons_photo(c.get("commons_search", c.get("real_name", "")), workdir / "commons")
        if got:
            p = prep(got[0])
            if p:
                vis.append(p)
                credits.append(got[1])
        else:
            # Commons unavailable -> AI-generated scene of the real car.
            from .imagen import generate_image
            ai = generate_image(
                cfg,
                f"A {c.get('real_name', 'sports car')} on a scenic road, "
                f"automotive photography style",
                workdir / "commons" / f"ai_{prep_n:03d}.png",
            )
            if ai:
                p = prep(ai)
                if p:
                    vis.append(p)
        if not vis and generic:
            p = prep(generic[prep_n % len(generic)])
            if p:
                vis.append(p)
        block_visuals.append(vis)

    # outro block
    block_visuals.append(block_visuals[0][:1])

    # --- assemble slides timed to blocks ------------------------------------
    slides: list[tuple[Path, float]] = []
    durations: list[float] = []
    intro_img = cfg.get(f"video.{fmt}.intro_image") or cfg.get("video.intro_image")
    if intro_img and cfg.path_of(intro_img).exists():
        p = prepare_image(cfg.path_of(intro_img), w, h, workdir / "intro.jpg")
        if p:
            slides.append((p, 0.0))
            durations.append(INTRO_SECONDS)

    banners: list[tuple[float, float, str]] = []
    for bi, (clip, _kw) in enumerate(voice.blocks):
        vis = block_visuals[bi] if bi < len(block_visuals) else []
        if not vis:
            vis = block_visuals[0] or [slides[0][0]]
        per = clip.duration / len(vis)
        off = voice.sub_segments[bi][1]
        for v in vis:
            slides.append((v, 0.0))
            durations.append(per)
        # banner: car pair name during its block (skip hook/outro)
        if 1 <= bi <= len(script.sections):
            heading = script.sections[bi - 1].heading
            banners.append((off, off + clip.duration, heading[:48]))

    outro_img = cfg.get(f"video.{fmt}.outro_image") or cfg.get("video.outro_image")
    if outro_img and cfg.path_of(outro_img).exists():
        p = prepare_image(cfg.path_of(outro_img), w, h, workdir / "outro.jpg")
        if p:
            slides.append((p, 0.0))
            durations.append(OUTRO_SECONDS)

    # --- render --------------------------------------------------------------
    padded = _pad_voice(voice.voice_path, INTRO_SECONDS, OUTRO_SECONDS, workdir / "voice_final.m4a")
    ass = build_ass(voice.sub_segments, cfg, width=w, height=h, is_short=is_short, banners=banners)
    ass = _shift_ass(ass, INTRO_SECONDS)

    out = cfg.output_dir / f"cars_{_slug(cfg.get('channel.slug', ''))}_{_stamp()}.mp4"
    music = pick_music(cfg)
    render_video(slides, durations, padded, ass, cfg,
                 is_short=is_short, music_path=music, out_path=out)

    # --- metadata / upload ----------------------------------------------------
    pseudo = [NewsItem(title=s.heading, summary=s.text[:150], link="", source="cars")
              for s in script.sections]
    seo = write_seo(cfg, script.title, pseudo, is_short=is_short)
    extra = []
    mc = music_credit(music)
    if mc:
        extra.append(mc)
    extra.extend(credits)
    if extra:
        seo.description += "\n\n" + "\n".join(extra)

    srt = build_srt(voice.sub_segments, out.with_suffix(".srt"), shift=INTRO_SECONDS)
    thumb = make_thumbnail(cfg, seo.title, _thumb_background(slides), out.with_suffix(".jpg"))

    chapters = None
    if not is_short:
        chapters = [(0.0, "Intro")]
        for i, s in enumerate(script.sections):
            bi = i + 1
            if bi < len(voice.sub_segments):
                chapters.append((INTRO_SECONDS + voice.sub_segments[bi][1], s.heading))

    hist = History(cfg)
    from .pipeline import _finish
    _finish(cfg, out, seo, pseudo, hist, kind="cars", is_short=is_short,
            srt=srt, thumb=thumb, chapters=chapters)
    return out
