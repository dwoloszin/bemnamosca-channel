#!/usr/bin/env python
"""Auto YouTube Channel — command-line entry point.

Usage:
    python main.py setup            # create folders, check ffmpeg
    python main.py news             # preview fetched headlines
    python main.py voices [lang]    # list available TTS voices
    python main.py short            # build one Short (9:16)
    python main.py long             # build one long video (16:9)

    short / listicle / post accept, in ANY order, a topic and/or a media folder:
    python main.py short "GTA 6 trailer 3 release date"
    python main.py short assets/myfolder                  # only these visuals
    python main.py listicle "GTA 6 map leaks" assets/myfolder
    python main.py post "GTA 6 price" assets/myfolder
    (the folder is searched recursively: .png/.jpg/.webp images + video clips)

    python main.py comparison       # build a GTA5-vs-GTA6 comparison
    python main.py igpost           # publish the LAST post package to Instagram
    python main.py run              # build a Short AND a long video
    python main.py --config other.yaml short

    Ready-made promo videos (nothing is generated — the file is posted as is):
    python main.py oneuse           # post everything in videos/oneuse/, then
                                    #   archive it to videos/oneuse/posted/
    python main.py loop             # post the next clip of videos/loop/ in
                                    #   rotation (the file stays, reusable)
    python main.py oneuse ig        # restrict platforms: "ig" and/or "yt"
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Windows consoles default to cp1252, which crashes on emoji (community posts
# use them). Force UTF-8 and never die on an unprintable character.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

from src.config import load_config


def _cmd_setup(cfg) -> int:
    for d in ["assets/images", "assets/music", "assets/compare", cfg.get("output.dir", "output")]:
        Path(cfg.path_of(d)).mkdir(parents=True, exist_ok=True)
    print("Created project folders.")
    # Verify the bundled ffmpeg works.
    try:
        from src import ffmpeg
        exe = ffmpeg.ffmpeg_exe()
        print(f"ffmpeg ready: {exe}")
    except Exception as exc:  # noqa: BLE001
        print(f"ffmpeg NOT ready: {exc}\n  Run: pip install imageio-ffmpeg")
        return 1
    print("Keys:")
    print(f"  ANTHROPIC_API_KEY : {'set' if cfg.anthropic_key else 'MISSING (template scripts will be used)'}")
    print(f"  PEXELS_API_KEY    : {'set' if cfg.pexels_key else 'not set (using local images only)'}")
    if cfg.get("tts.provider") == "elevenlabs":
        from src.tts_elevenlabs import pool_status
        print("ElevenLabs keys:")
        print(pool_status(cfg))
    from src.imagen import pool_status as cf_status
    print("Cloudflare image accounts:")
    print(cf_status(cfg))
    print("Setup complete. Add images to img-channel/ or assets/images/, music to assets/music/.")
    return 0


def _cmd_news(cfg) -> int:
    from src.news import fetch_news
    items = fetch_news(cfg, limit=25)
    if not items:
        print("No headlines found. Check theme.rss_feeds in config.yaml.")
        return 1
    for i, it in enumerate(items, 1):
        print(f"{i:2d}. {it.clean_title}\n     {it.source} — {it.link}")
    return 0


def _cmd_voices(cfg, lang: str | None) -> int:
    from src.tts import list_voices
    voices = list_voices(lang or cfg.get("channel.language"))
    for v in voices:
        print(f"{v['ShortName']:28s} {v['Gender']:7s} {v['Locale']}")
    print(f"\n{len(voices)} voices. Set one in config.yaml -> tts.voice")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Automated YouTube channel builder")
    parser.add_argument("--config", default="config.yaml", help="path to config file")
    parser.add_argument("command", choices=[
        "setup", "news", "voices", "short", "listicle", "long", "comparison",
        "post", "igpost", "cars", "reels", "tiktok", "tiktok-auth", "run",
        "youtube-test", "oneuse", "loop", "carousel", "daily",
    ])
    parser.add_argument("extra", nargs="*",
                        help="optional: a topic in quotes and/or a media folder "
                             '(e.g. short "GTA 6 trailer 3" assets\\myfolder)')
    args = parser.parse_args(argv)

    cfg = load_config(args.config)

    # short/listicle/post accept, in any order: a TOPIC ("GTA 6 trailer 3
    # release date") and/or a MEDIA FOLDER — an existing directory whose
    # images/videos (subfolders included) become the ONLY visuals this run.
    def _topic_and_media() -> tuple[str | None, Path | None]:
        topic_words: list[str] = []
        media: Path | None = None
        for e in args.extra:
            p = Path(e) if Path(e).is_absolute() else cfg.path_of(e)
            if p.is_dir():
                media = p
            else:
                topic_words.append(e)
        return (" ".join(topic_words).strip() or None), media

    first_extra = args.extra[0] if args.extra else None

    if args.command == "setup":
        return _cmd_setup(cfg)
    if args.command == "news":
        return _cmd_news(cfg)
    if args.command == "voices":
        return _cmd_voices(cfg, first_extra)
    if args.command == "youtube-test":
        from src.youtube_upload import test_auth
        print("Testing YouTube auth (read-only, nothing will be uploaded)...")
        print("A browser window will open — sign in with your channel's account.")
        return 0 if test_auth(cfg) else 1

    from src import pipeline
    if args.command in ("short", "listicle", "post"):
        topic, media = _topic_and_media()
        if media:
            cfg.raw.setdefault("media", {})["override_dir"] = str(media)
            print(f"Media folder for this run: {media} (subfolders included)")
        if topic:
            print(f"Requested topic: {topic}")
        if args.command == "short":
            return 0 if pipeline.make_short(cfg, topic=topic) else 1
        if args.command == "listicle":
            return 0 if pipeline.make_listicle_short(cfg, topic=topic) else 1
        from src.posts import make_post
        return 0 if make_post(cfg, topic=topic) else 1
    if args.command == "long":
        return 0 if pipeline.make_long(cfg) else 1
    if args.command == "comparison":
        return 0 if pipeline.make_comparison(cfg) else 1
    if args.command == "carousel":
        # 5-slide carousel: hook / news / solution / application / close.
        # Usage: python main.py carousel ["termo da noticia"]
        from src.carousel import make_carousel
        return 0 if make_carousel(cfg, first_extra) else 1

    if args.command == "daily":
        # The routine: publish the promos, then build the day's carousel.
        # Ordered so the time-sensitive uploads happen before the slow render.
        #
        # Every step records itself in output/daily_state.json, so a second
        # run on the same day SKIPS what already went out. That is what makes
        # the GitHub runner safe: four cron slots a morning, each one a fresh
        # machine, and still never a duplicate upload. Locally it also closes
        # the hole where a re-run after a crash re-posted the loop pitch.
        #
        # DAILY_CI=1 (set by the workflow) turns the 30-minute retry ladder
        # into a single attempt: on Actions the *next cron* is the retry, and a
        # sleeping job would just burn minutes.
        import json as _json
        import os as _os
        import time as _time
        from datetime import date as _date
        from src import promo
        from src.carousel import make_carousel

        state_path = cfg.output_dir / "daily_state.json"
        today = _date.today().isoformat()
        try:
            state = _json.loads(state_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            state = {}
        if state.get("date") != today:
            state = {"date": today, "done": []}

        def done(step: str) -> bool:
            return step in state["done"]

        def mark(step: str) -> None:
            state["done"].append(step)
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(_json.dumps(state, indent=2), encoding="utf-8")

        ci = bool(_os.getenv("DAILY_CI"))
        rc = 0
        print("=" * 60)
        print("1/3  oneuse — finished promos waiting in the inbox")
        print("=" * 60)
        if done("oneuse"):
            print("  already ran today — skipping")
        else:
            rc |= promo.run_oneuse(cfg)
            mark("oneuse")
        print("\n" + "=" * 60)
        print("2/3  loop — next evergreen promo in the rotation")
        print("=" * 60)
        if done("loop"):
            print("  already ran today — skipping")
        else:
            rc |= promo.run_loop(cfg)
            mark("loop")
        print("\n" + "=" * 60)
        print("3/3  carousel — slides + caption + single post + video")
        print("=" * 60)
        if done("carousel"):
            print("  already ran today — skipping")
        else:
            # Retry ladder (see config daily.*): the Gemini pool answers 503 on
            # all keys often enough to lose a whole day, and the story is still
            # there 30 minutes later. Retrying is safe ONLY because
            # make_carousel returns None exclusively on failures that happen
            # BEFORE any upload — once the video is published it returns the
            # package folder, even if a platform failed.
            attempts = 1 if ci else max(1, int(cfg.get("daily.carousel_attempts", 4)))
            wait_s = max(1, int(cfg.get("daily.retry_wait_minutes", 30))) * 60
            for attempt in range(1, attempts + 1):
                if attempt > 1:
                    print(f"\n  [daily] carousel attempt {attempt}/{attempts}")
                if make_carousel(cfg):
                    mark("carousel")
                    break
                if attempt >= attempts:
                    print(f"  [daily] carousel failed on all {attempts} attempts "
                          + ("— the next cron slot will retry." if ci else "— no carousel today."))
                    rc |= 1
                    break
                print(f"  [daily] carousel produced nothing (attempt "
                      f"{attempt}/{attempts}) — waiting "
                      f"{wait_s // 60} min before retrying…")
                _time.sleep(wait_s)
        print("\nDone. The carousel slides are for you to post by hand "
              "on Instagram and LinkedIn.")
        return rc

    if args.command in ("oneuse", "loop"):
        # Publish FINISHED promo videos — nothing is generated or re-rendered.
        #   oneuse -> everything in videos/oneuse/, archived to posted/ after
        #   loop   -> the next clip of the rotation in videos/loop/ (reusable)
        # Optional: "yt" / "ig" to restrict the platforms for this run.
        from src import promo
        picks = {e.lower() for e in args.extra}
        only = picks & {"yt", "youtube", "ig", "instagram"}
        to_yt = not only or bool(only & {"yt", "youtube"})
        to_ig = not only or bool(only & {"ig", "instagram"})
        if args.command == "oneuse":
            return promo.run_oneuse(cfg, to_youtube=to_yt, to_instagram=to_ig)
        return promo.run_loop(cfg, to_youtube=to_yt, to_instagram=to_ig)

    if args.command == "igpost":
        # Publish a community-post package to Instagram as a feed photo.
        # Usage: python main.py igpost [output\posts\post_YYYYMMDD_HHMMSS]
        #        (default: the most recent package)
        import glob as _glob
        import os as _os
        from src.instagram_upload import upload_post_package
        if first_extra:
            pkg = Path(first_extra)
        else:
            cands = [p for p in _glob.glob("output/posts/post_*")
                     if Path(p, "image.jpg").exists()]
            if not cands:
                print("No post packages found in output/posts/. Run: python main.py post")
                return 1
            pkg = Path(max(cands, key=_os.path.getmtime))
        print(f"Publishing LAST post package to Instagram: {pkg.name}")
        return 0 if upload_post_package(cfg, pkg) else 1

    if args.command == "reels":
        # Publish a Short as an Instagram Reel (LIVE immediately).
        # Usage: python main.py reels [path\to\video.mp4]  (default: newest short)
        import glob as _glob
        import os as _os
        from src.instagram_upload import caption_from_metadata, upload_reel
        if first_extra:
            video = Path(first_extra)
        else:
            cands = (_glob.glob("output/short_*.mp4") + _glob.glob("output/top5_*.mp4")
                     + _glob.glob("output/vehicles_*.mp4") + _glob.glob("output/cars_*.mp4"))
            if not cands:
                print("No shorts found in output/.")
                return 1
            video = Path(max(cands, key=_os.path.getmtime))
        meta = video.with_suffix(".json")
        caption = caption_from_metadata(meta, cfg) if meta.exists() else video.stem
        print(f"Publishing to Instagram Reels: {video.name}")
        print(f"Caption: {caption[:120]}...")
        return 0 if upload_reel(cfg, video, caption, publish=True) else 1

    if args.command == "tiktok-auth":
        from src.tiktok_upload import authorize
        return 0 if authorize(cfg) else 1

    if args.command == "tiktok":
        # Publish a Short to TikTok. Usage: python main.py tiktok [video.mp4]
        import glob as _glob
        import os as _os
        from src.instagram_upload import caption_from_metadata
        from src.tiktok_upload import upload_video as tiktok_upload
        if first_extra:
            video = Path(first_extra)
        else:
            cands = (_glob.glob("output/short_*.mp4") + _glob.glob("output/top5_*.mp4")
                     + _glob.glob("output/vehicles_*.mp4") + _glob.glob("output/cars_*.mp4"))
            if not cands:
                print("No shorts found in output/.")
                return 1
            video = Path(max(cands, key=_os.path.getmtime))
        meta = video.with_suffix(".json")
        title = caption_from_metadata(meta, cfg) if meta.exists() else video.stem
        print(f"Publishing to TikTok: {video.name}")
        return 0 if tiktok_upload(cfg, video, title) else 1

    if args.command == "cars":
        kind = (first_extra or "short").lower()
        if kind == "showcase":
            from src.cars import make_vehicles_showcase
            return 0 if make_vehicles_showcase(cfg) else 1
        from src.cars import make_cars_video
        return 0 if make_cars_video(cfg, kind) else 1
    if args.command == "run":
        # What to build is driven by the `publish` section in config.yaml.
        ok = True
        if cfg.get("publish.shorts", True):
            n = max(1, int(cfg.get("publish.shorts_per_run", 1)))
            listicles = bool(cfg.get("publish.listicle_shorts", True))
            for i in range(n):
                print(f"\n--- Short {i + 1}/{n} ---")
                # Alternate: news short, then a TOP-N listicle, then news...
                if listicles and i % 2 == 1:
                    made = pipeline.make_listicle_short(cfg) or pipeline.make_short(cfg)
                else:
                    made = pipeline.make_short(cfg)
                ok = bool(made) and ok
        if cfg.get("publish.long", True):
            ok = bool(pipeline.make_long(cfg)) and ok
        if cfg.get("publish.community_posts", False):
            from src.posts import make_post
            print("\n--- Community post package ---")
            make_post(cfg)  # best-effort; never fails the run
        if not cfg.get("publish.shorts", True) and not cfg.get("publish.long", True):
            print("Nothing to build: both publish.shorts and publish.long are false.")
            return 1
        return 0 if ok else 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
