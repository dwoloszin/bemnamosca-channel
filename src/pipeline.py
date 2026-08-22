"""Orchestrate the full pipeline: news -> script -> voice -> subtitles ->
visuals -> render -> (optional) upload.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import ffmpeg
from .config import Config, load_config
from .history import History
from .media import build_image_pool, music_credit, pick_music, prepare_image
from .news import NewsItem, fetch_news
from .script_writer import (SeoPack, VideoScript, write_listicle_script,
                            write_long_script, write_seo, write_short_script)
from .subtitles import build_ass, build_srt
from .thumbnail import make_thumbnail
from .tts import SpeechClip, synthesize_auto
from .video import render_video

INTRO_SECONDS = 2.5
OUTRO_SECONDS = 3.0


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:50] or "video"


# --------------------------------------------------------------------------
# voice + subtitles from a VideoScript
# --------------------------------------------------------------------------
@dataclass
class VoiceResult:
    voice_path: Path
    duration: float
    blocks: list[tuple[SpeechClip, str]]      # (clip, keyword)
    sub_segments: list[tuple[list, float]]     # (words, offset)


def _synthesize_script(script: VideoScript, cfg: Config, workdir: Path) -> VoiceResult:
    blocks: list[tuple[SpeechClip, str]] = []
    sub_segments: list[tuple[list, float]] = []
    part_paths: list[Path] = []
    offset = 0.0

    spoken = [((t or "").strip(), k) for t, k in script.spoken_blocks()]
    spoken = [(t, k) for t, k in spoken if t]
    gap = max(0.0, float(cfg.get("tts.block_gap_seconds", 0.35) or 0))
    for i, (text, keyword) in enumerate(spoken):
        prev_t = spoken[i - 1][0] if i > 0 else ""
        next_t = spoken[i + 1][0] if i + 1 < len(spoken) else ""
        clip = synthesize_auto(text, workdir / f"voice_{i:02d}.mp3", cfg,
                               previous_text=prev_t, next_text=next_t)
        # A breath between blocks. Clips used to be butted together, so the
        # last word of one beat ran straight into the first of the next —
        # nobody speaks like that. Padding goes at the END of the clip, so
        # word timings inside it stay valid; only the block gets longer.
        if gap > 0 and i + 1 < len(spoken):
            padded = workdir / f"voice_{i:02d}_gap.m4a"
            ffmpeg.run(["-i", str(clip.audio_path), "-af", f"apad=pad_dur={gap:.2f}",
                        "-c:a", "aac", "-b:a", "192k", str(padded)])
            clip.audio_path = padded
            clip.duration += gap
        blocks.append((clip, keyword))
        sub_segments.append((clip.words, offset))
        part_paths.append(clip.audio_path)
        offset += clip.duration

    voice_path = ffmpeg.concat_audio(part_paths, workdir / "voice.m4a")
    return VoiceResult(voice_path, offset, blocks, sub_segments)


def _fit_voice(voice: VoiceResult, allowed: float, workdir: Path) -> VoiceResult:
    """If the narration is longer than `allowed` seconds, speed it up just
    enough to fit (capped at 1.2x — barely noticeable for news delivery).
    All word timings and block durations are rescaled so subtitles stay
    perfectly synced."""
    if allowed <= 0 or voice.duration <= allowed:
        return voice
    factor = min(voice.duration / allowed, 1.2)
    print(f"  [fit] narration {voice.duration:.0f}s > {allowed:.0f}s budget — "
          f"speeding up {factor:.2f}x")
    fitted = workdir / "voice_fitted.m4a"
    ffmpeg.run(["-i", str(voice.voice_path), "-af", f"atempo={factor:.4f}",
                "-c:a", "aac", "-b:a", "192k", str(fitted)])
    inv = 1.0 / factor
    for clip, _kw in voice.blocks:
        clip.duration *= inv
        for w in clip.words:      # same Word objects referenced by sub_segments
            w.start *= inv
            w.end *= inv
    voice.sub_segments = [(words, off * inv) for words, off in voice.sub_segments]
    voice.duration *= inv
    voice.voice_path = fitted
    return voice


def _pad_voice(voice_path: Path, lead: float, tail: float, out: Path) -> Path:
    ffmpeg.run([
        "-i", str(voice_path),
        "-af", f"adelay={int(lead*1000)}|{int(lead*1000)},apad=pad_dur={tail}",
        "-c:a", "aac", "-b:a", "192k", str(out),
    ])
    return out


# --------------------------------------------------------------------------
# slide planning
# --------------------------------------------------------------------------
# A slide is (source_path, start_offset). start_offset > 0 means "play this
# video from that point" — so a long clip is consumed progressively across
# slots instead of restarting at the same opening scene every time.
Slide = tuple[Path, float]

_MIN_VIDEO_SEG = 1.2   # clips shorter than this are skipped (too jarring)


def _plan_slides(cfg: Config, voice: VoiceResult, w: int, h: int,
                 workdir: Path, seconds_per_slide: float) -> tuple[list[Slide], list[float]]:
    from .media import is_video

    theme_keywords = cfg.get("theme.keywords", []) or []

    # Rough upper bound of visuals needed (used to size the pool).
    total = sum(max(1, round(c.duration / seconds_per_slide)) for c, _ in voice.blocks)

    kw_pool: list[str] = [kw for _c, kw in voice.blocks if kw]
    kw_pool.extend(theme_keywords)

    pool = build_image_pool(cfg, kw_pool, w, h, workdir, want=max(total, 1))
    if not pool:
        raise RuntimeError(
            "No usable visuals found. Add images/clips to assets/images/ or "
            "assets/videos/, or set PEXELS_API_KEYS in .env."
        )

    # Probe every video's real duration once; track a play cursor per video.
    # Long clips get their first/last seconds trimmed (media.video_edge_trim,
    # default 6s) — trailers/imports often carry logo intros and end cards.
    edge_trim = float(cfg.get("media.video_edge_trim", 6.0))
    vdur: dict[Path, float] = {}
    vstart: dict[Path, float] = {}   # usable window start per video
    vend: dict[Path, float] = {}     # usable window end per video
    vpos: dict[Path, float] = {}
    from . import media_log
    for p in pool:
        if is_video(p) and p not in vdur:
            d = ffmpeg.probe_duration(p)
            vdur[p] = d
            # Only trim when enough usable middle remains (>= 2x min segment).
            if d - 2 * edge_trim >= _MIN_VIDEO_SEG * 2:
                vstart[p], vend[p] = edge_trim, d - edge_trim
            else:
                vstart[p], vend[p] = 0.0, d
            # Resume from the PERSISTED cursor: reusing a long clip on the
            # next video continues at a fresh timeframe, not the same scene.
            saved = media_log.video_cursor(cfg, p)
            if vstart[p] <= saved < vend[p] - _MIN_VIDEO_SEG:
                vpos[p] = saved

    slides: list[Slide] = []
    durations: list[float] = []

    # Intro slide — per-format (video.short.intro_image / video.long.intro_image),
    # falling back to the legacy shared video.intro_image.
    kind = "short" if h > w else "long"
    intro = cfg.get(f"video.{kind}.intro_image") or cfg.get("video.intro_image")
    if intro and cfg.path_of(intro).exists():
        p = prepare_image(cfg.path_of(intro), w, h, workdir / "intro.jpg")
        if p:
            slides.append((p, 0.0))
            durations.append(INTRO_SECONDS)

    # Content slides, timed to each spoken block — duration-aware:
    #  * videos play a CONTINUOUS segment as long as they can offer (no loops),
    #    resuming from the play cursor on the next use;
    #  * clips shorter than _MIN_VIDEO_SEG are skipped;
    #  * images get standard fixed slots.
    pi = 0
    for clip, _kw in voice.blocks:
        remaining = clip.duration
        skips = 0
        while remaining > 0.05:
            src = pool[pi % len(pool)]
            pi += 1
            if is_video(src):
                win_start = vstart.get(src, 0.0)
                win_end = vend.get(src, vdur.get(src, 0.0))
                if win_end - win_start < _MIN_VIDEO_SEG:
                    skips += 1
                    if skips >= len(pool):   # pool is only unusable micro-clips
                        raise RuntimeError(
                            "All video clips are shorter than "
                            f"{_MIN_VIDEO_SEG}s and no images are available."
                        )
                    continue
                pos = vpos.get(src, win_start)
                if win_end - pos < _MIN_VIDEO_SEG:
                    pos = win_start          # window exhausted — restart at its top
                seg = min(remaining, win_end - pos)
                # Don't leave a sub-second orphan at the end of the block.
                if 0 < remaining - seg < 1.0 and win_end - pos >= remaining:
                    seg = remaining
                slides.append((src, pos))
                durations.append(seg)
                vpos[src] = pos + seg
            else:
                seg = min(remaining, seconds_per_slide)
                if 0 < remaining - seg < 1.0:
                    seg = remaining          # merge the tail into this slide
                slides.append((src, 0.0))
                durations.append(seg)
            remaining -= seg
            skips = 0

    # Outro slide — per-format, same fallback rule as the intro.
    outro = cfg.get(f"video.{kind}.outro_image") or cfg.get("video.outro_image")
    if outro and cfg.path_of(outro).exists():
        p = prepare_image(cfg.path_of(outro), w, h, workdir / "outro.jpg")
        if p:
            slides.append((p, 0.0))
            durations.append(OUTRO_SECONDS)

    # Persist where each video stopped, for the NEXT run's fresh timeframes.
    if vpos:
        media_log.set_video_cursors(cfg, vpos)

    return slides, durations


# --------------------------------------------------------------------------
# public entry points
# --------------------------------------------------------------------------
def make_short(cfg: Config, item: NewsItem | None = None,
               topic: str | None = None) -> Path | None:
    """`topic` (optional): user-requested subject — real news is searched for
    it and the freshest hit becomes the story (an explicit request skips the
    already-covered check, like posts do). Facts still come only from news."""
    print("== Building a SHORT ==")
    hist = History(cfg)
    if item is None and topic:
        from .search_news import search_news
        found = search_news(cfg, [topic.strip()], per_query=6)
        if not found:
            print(f"  No real news found for '{topic}' — skipping (no invented content).")
            return None
        item = found[0]
    if item is None:
        items = fetch_news(cfg, limit=25)
        fresh = hist.filter_fresh(items)
        item = fresh[0] if fresh else (items[0] if items else None)
    if item is None:
        print("  No news found.")
        return None
    print(f"  Story: {item.clean_title}")

    workdir = cfg.output_dir / "_work"
    _reset_dir(workdir)

    script = write_short_script(item, cfg)
    voice = _synthesize_script(script, cfg, workdir)

    # Hard duration guard: keep the whole Short under the 60s Shorts cap.
    max_s = float(cfg.get("video.short.max_seconds", 58))
    voice = _fit_voice(voice, max_s - INTRO_SECONDS - OUTRO_SECONDS, workdir)

    w = int(cfg.get("video.short.width"))
    h = int(cfg.get("video.short.height"))
    slides, durations = _plan_slides(cfg, voice, w, h, workdir, seconds_per_slide=3.0)

    # Cap short length.
    padded_voice = _pad_voice(voice.voice_path, INTRO_SECONDS, OUTRO_SECONDS, workdir / "voice_final.m4a")

    ass = build_ass(voice.sub_segments, cfg, width=w, height=h, is_short=True)
    if ass is None:
        print("  WARNING: no subtitle events were generated (no word timings?).")
    ass = _shift_ass(ass, INTRO_SECONDS)

    out = cfg.output_dir / f"short_{_slug(cfg.get('channel.slug',''))}_{_stamp()}.mp4"
    music = pick_music(cfg)
    render_video(slides, durations, padded_voice, ass, cfg,
                 is_short=True, music_path=music, out_path=out)

    total = sum(durations)
    if total > max_s + 2:
        print(f"  Note: short is {total:.0f}s (> {max_s:.0f}s). Reduce script.short_words for < 60s.")

    # SEO metadata + sidecar files (SRT captions, thumbnail).
    seo = write_seo(cfg, script.title, [item], is_short=True)
    credit = music_credit(music)
    if credit:
        seo.description += "\n\n" + credit
    srt = build_srt(voice.sub_segments, out.with_suffix(".srt"), shift=INTRO_SECONDS)
    bg = _thumb_background(slides)
    thumb = make_thumbnail(cfg, seo.title, bg, out.with_suffix(".jpg"))

    _finish(cfg, out, seo, [item], hist, kind="short", is_short=True,
            srt=srt, thumb=thumb, chapters=None)
    return out


def make_listicle_short(cfg: Config, topic: str | None = None) -> Path | None:
    """Viral countdown Short ("TOP 5 ...") sourced from real news + what the
    community is upvoting on Reddit. Shows TOP-N / #rank banners on screen.

    `topic` (optional): builds the TOP-N around that subject — sources come
    from a news search for it (explicit request skips the covered check)."""
    print("== Building a TOP-N LISTICLE Short ==")
    hist = History(cfg)
    count = int(cfg.get("script.listicle_count", 5))
    if topic:
        from .search_news import search_news
        sources = search_news(cfg, [topic.strip()], per_query=14)
        if len(sources) < count:
            print(f"  Only {len(sources)} real stories found for '{topic}' — "
                  f"need {count} for a top-{count}. Skipping.")
            return None
        print(f"  Sources: {len(sources)} news hits for '{topic}'")
    else:
        news = hist.filter_fresh(fetch_news(cfg, limit=40))
        from .community import fetch_community
        community = hist.filter_fresh(fetch_community(cfg))
        sources = news[:14] + community[:10]
        if len(sources) < count:
            print(f"  Not enough fresh sources ({len(sources)}) for a top-{count}.")
            return None
        print(f"  Sources: {len(news[:14])} news + {len(community[:10])} community posts")

    res = write_listicle_script(sources, cfg, count, avoid=hist.recent_content())
    if res is None:
        print("  Listicles need a working LLM (no template fallback) — skipping.")
        return None
    script, used_idx = res
    print(f"  Script: {script.title}")

    workdir = cfg.output_dir / "_work"
    _reset_dir(workdir)

    voice = _synthesize_script(script, cfg, workdir)
    max_s = float(cfg.get("video.short.max_seconds", 58))
    voice = _fit_voice(voice, max_s - INTRO_SECONDS - OUTRO_SECONDS, workdir)

    w = int(cfg.get("video.short.width"))
    h = int(cfg.get("video.short.height"))
    slides, durations = _plan_slides(cfg, voice, w, h, workdir, seconds_per_slide=3.0)

    # Rank banners: TOP {count} during the hook, then #5..#1 per item.
    banners: list[tuple[float, float, str]] = []
    if voice.sub_segments:
        block_durs = [c.duration for c, _ in voice.blocks]
        offsets = [off for _w, off in voice.sub_segments]
        banners.append((offsets[0], offsets[0] + block_durs[0], f"TOP {count}"))
        for i, section in enumerate(script.sections):
            bi = i + 1  # blocks: [hook] + sections + [outro]
            if bi < len(offsets):
                banners.append((offsets[bi], offsets[bi] + block_durs[bi], section.heading))

    padded_voice = _pad_voice(voice.voice_path, INTRO_SECONDS, OUTRO_SECONDS, workdir / "voice_final.m4a")
    ass = build_ass(voice.sub_segments, cfg, width=w, height=h, is_short=True, banners=banners)
    if ass is None:
        print("  WARNING: no subtitle events were generated (no word timings?).")
    ass = _shift_ass(ass, INTRO_SECONDS)

    out = cfg.output_dir / f"top{count}_{_slug(cfg.get('channel.slug',''))}_{_stamp()}.mp4"
    music = pick_music(cfg)
    render_video(slides, durations, padded_voice, ass, cfg,
                 is_short=True, music_path=music, out_path=out)

    chosen = [sources[i] for i in used_idx] or sources[:count]
    seo = write_seo(cfg, script.title, chosen, is_short=True)
    credit = music_credit(music)
    if credit:
        seo.description += "\n\n" + credit
    srt = build_srt(voice.sub_segments, out.with_suffix(".srt"), shift=INTRO_SECONDS)
    thumb = make_thumbnail(cfg, seo.title, _thumb_background(slides), out.with_suffix(".jpg"))

    _finish(cfg, out, seo, chosen, hist, kind="listicle", is_short=True,
            srt=srt, thumb=thumb, chapters=None)
    return out


def make_long(cfg: Config) -> Path | None:
    print("== Building a LONG video ==")
    hist = History(cfg)
    items = fetch_news(cfg, limit=40)
    fresh = hist.filter_fresh(items) or items
    max_items = int(cfg.get("video.long.max_items", 8))
    chosen = fresh[:max_items]
    if not chosen:
        print("  No news found.")
        return None
    print(f"  Covering {len(chosen)} stories.")

    workdir = cfg.output_dir / "_work"
    _reset_dir(workdir)

    script = write_long_script(chosen, cfg)
    voice = _synthesize_script(script, cfg, workdir)

    min_s = float(cfg.get("video.long.min_seconds", 600))
    if voice.duration + INTRO_SECONDS + OUTRO_SECONDS < min_s:
        print(f"  Note: narration is {voice.duration:.0f}s; below the {min_s:.0f}s "
              f"monetization target. Add more feeds or raise script.long_section_words.")

    w = int(cfg.get("video.long.width"))
    h = int(cfg.get("video.long.height"))
    slides, durations = _plan_slides(cfg, voice, w, h, workdir, seconds_per_slide=5.0)

    padded_voice = _pad_voice(voice.voice_path, INTRO_SECONDS, OUTRO_SECONDS, workdir / "voice_final.m4a")
    ass = build_ass(voice.sub_segments, cfg, width=w, height=h, is_short=False)
    if ass is None:
        print("  WARNING: no subtitle events were generated (no word timings?).")
    ass = _shift_ass(ass, INTRO_SECONDS)

    out = cfg.output_dir / f"long_{_slug(cfg.get('channel.slug',''))}_{_stamp()}.mp4"
    music = pick_music(cfg)
    render_video(slides, durations, padded_voice, ass, cfg,
                 is_short=False, music_path=music, out_path=out)

    # Chapters: hook = Intro, then one chapter per story (YouTube needs the
    # first at 0:00 and 3+ chapters to show the segmented progress bar).
    chapters: list[tuple[float, str]] = [(0.0, "Intro")]
    for section, (_words, offset) in zip(script.sections, voice.sub_segments[1:]):
        chapters.append((INTRO_SECONDS + offset, section.heading))

    seo = write_seo(cfg, script.title, chosen, is_short=False)
    credit = music_credit(music)
    if credit:
        seo.description += "\n\n" + credit
    srt = build_srt(voice.sub_segments, out.with_suffix(".srt"), shift=INTRO_SECONDS)
    bg = _thumb_background(slides)
    thumb = make_thumbnail(cfg, seo.title, bg, out.with_suffix(".jpg"))

    _finish(cfg, out, seo, chosen, hist, kind="long", is_short=False,
            srt=srt, thumb=thumb, chapters=chapters)
    return out


def make_comparison(cfg: Config) -> Path | None:
    print("== Building a COMPARISON video ==")
    from .comparison import build_comparison
    out = cfg.output_dir / f"compare_{_slug(cfg.get('channel.slug',''))}_{_stamp()}.mp4"
    result = build_comparison(cfg, out)
    if result:
        title = f"{cfg.get('comparison.left_label')} vs {cfg.get('comparison.right_label')} — Graphics Comparison"
        seo = write_seo(cfg, title, [], is_short=False)
        thumb = make_thumbnail(cfg, seo.title, None, result.with_suffix(".jpg"))
        _write_metadata(cfg, result, seo, [], None, thumb, None)
        if cfg.get("youtube.enabled", False):
            from .youtube_upload import upload_video
            upload_video(cfg, result, seo.title, description=seo.description,
                         tags=seo.tags, thumbnail=thumb, is_short=False)
    return result


# --------------------------------------------------------------------------
# shared finish/upload/metadata
# --------------------------------------------------------------------------
def _finish(cfg: Config, out: Path, seo: SeoPack, items: list[NewsItem],
            hist: History, *, kind: str, is_short: bool,
            srt: Path | None = None, thumb: Path | None = None,
            chapters: list[tuple[float, str]] | None = None) -> None:
    links = [i.link for i in items if i.link]
    _write_metadata(cfg, out, seo, links, chapters, thumb, srt)
    hist.record(kind, seo.title, items)
    if cfg.get("youtube.enabled", False):
        from .youtube_upload import upload_video
        upload_video(cfg, out, seo.title,
                     description=seo.description, tags=seo.tags, links=links,
                     chapters=chapters, thumbnail=thumb, captions_srt=srt,
                     is_short=is_short)
    # Cross-post Shorts as Instagram Reels (opt-in — Reels go LIVE immediately).
    if is_short and cfg.get("instagram.enabled", False):
        from .instagram_upload import caption_from_metadata, upload_reel
        caption = caption_from_metadata(out.with_suffix(".json"), cfg)
        upload_reel(cfg, out, caption, publish=True)
    # Cross-post Shorts to TikTok (opt-in; SELF_ONLY until the app is audited).
    if is_short and cfg.get("tiktok.enabled", False):
        from .instagram_upload import caption_from_metadata
        from .tiktok_upload import upload_video as _tiktok_upload
        _tiktok_upload(cfg, out, caption_from_metadata(out.with_suffix(".json"), cfg))
    if not cfg.get("output.keep_workdir", False):
        _reset_dir(cfg.output_dir / "_work", remove=True)
    print(f"  DONE -> {out}")


def _write_metadata(cfg: Config, out: Path, seo: SeoPack, links: list[str],
                    chapters: list[tuple[float, str]] | None = None,
                    thumb: Path | None = None, srt: Path | None = None) -> None:
    meta = {
        "title": seo.title,
        "description": seo.description,
        "tags": seo.tags,
        "topic": cfg.get("theme.topic"),
        "created": datetime.now().isoformat(timespec="seconds"),
        "duration_s": round(ffmpeg.probe_duration(out), 1),
        "sources": links,
        "chapters": [[round(t, 1), name] for t, name in (chapters or [])],
        "thumbnail": thumb.name if thumb else None,
        "captions": srt.name if srt else None,
        "tagline": cfg.get("channel.brand_tagline"),
    }
    out.with_suffix(".json").write_text(json.dumps(meta, indent=2), encoding="utf-8")


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _thumb_background(slides: list[tuple[Path, float]]) -> Path | None:
    """First image slide (skipping the intro at index 0 when possible) —
    video slides can't be opened by Pillow for the thumbnail."""
    from .media import is_video
    images = [s for s, _off in slides if not is_video(s)]
    if not images:
        return None
    return images[1] if len(images) > 1 else images[0]


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _reset_dir(path: Path, remove: bool = False) -> None:
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
    if not remove:
        path.mkdir(parents=True, exist_ok=True)


def _shift_ass(ass_path: Path | None, seconds: float) -> Path | None:
    """Shift all subtitle timings later by `seconds` (to match padded intro)."""
    if ass_path is None or seconds <= 0:
        return ass_path
    lines = ass_path.read_text(encoding="utf-8").splitlines()
    out_lines = []
    for line in lines:
        if line.startswith("Dialogue:"):
            out_lines.append(_shift_dialogue(line, seconds))
        else:
            out_lines.append(line)
    ass_path.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    return ass_path


def _shift_dialogue(line: str, seconds: float) -> str:
    # Dialogue: Layer,Start,End,Style,...
    head, _, rest = line.partition(":")
    parts = rest.split(",", 9)
    if len(parts) < 10:
        return line
    parts[1] = _shift_time(parts[1].strip(), seconds)
    parts[2] = _shift_time(parts[2].strip(), seconds)
    return f"{head}:" + ",".join(parts)


def _shift_time(t: str, seconds: float) -> str:
    try:
        h, m, s = t.split(":")
        total = int(h) * 3600 + int(m) * 60 + float(s) + seconds
    except Exception:  # noqa: BLE001
        return t
    hh = int(total // 3600)
    mm = int((total % 3600) // 60)
    ss = total % 60
    return f"{hh}:{mm:02d}:{ss:05.2f}"
