"""Publish ready-made promo videos — no generation, no narration, no re-render.

Two inboxes, two behaviours (ported from the `influencer` project):

  videos/oneuse/   post ONCE, then the file moves to videos/oneuse/posted/
  videos/loop/     evergreen clips, rotated: each used once per cycle, then
                   the cycle restarts

The pipeline elsewhere in this project BUILDS a video from news. This module
does the opposite: it takes a finished file you already made and ships it as
is. Both are Shorts/Reels, so the same file serves YouTube and Instagram.

Why oneuse dedups by MOVING the file: state files get restored from backups,
edited by hand and copied between machines, and every one of those makes a
video post twice. A file that is no longer in the inbox cannot be posted
again, whatever happens to the state.

Captions/titles come from an optional sidecar next to the video:

  meu_video.mp4
  meu_video.txt    -> whole file is the caption/description
  meu_video.json   -> {"title": "...", "description": "...", "tags": [...]}

With no sidecar the title is derived from the filename.
"""
from __future__ import annotations

import json
import random
import re
import shutil
from pathlib import Path

from .config import Config

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}


# --------------------------------------------------------------------------
# folders
# --------------------------------------------------------------------------
def _dir(cfg: Config, key: str, default: str) -> Path:
    return cfg.path_of(cfg.get(f"promo.{key}", default))


def oneuse_dir(cfg: Config) -> Path:
    return _dir(cfg, "oneuse_dir", "videos/oneuse")


def loop_dir(cfg: Config) -> Path:
    return _dir(cfg, "loop_dir", "videos/loop")


def _videos_in(folder: Path) -> list[Path]:
    """Video files directly in `folder` — never recursing, so the archive
    subfolder (posted/) can live inside the inbox without being re-posted."""
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir()
                  if p.is_file() and p.suffix.lower() in VIDEO_EXT)


# --------------------------------------------------------------------------
# metadata
# --------------------------------------------------------------------------
def _title_from_name(path: Path) -> str:
    """Filename -> readable title: drop trailing timestamps, underscores out."""
    stem = re.sub(r"[_\-\s]*\d{8,14}$", "", path.stem)
    stem = re.sub(r"[_\-]+", " ", stem).strip()
    return (stem[:1].upper() + stem[1:]) if stem else path.stem


def _default_title(cfg: Config, video: Path) -> str:
    """A generic title for a promo clip that has no sidecar.

    promo.default_titles is a POOL rather than one string on purpose: sixteen
    Shorts published under a single identical title read as duplicates to
    YouTube and as repetition to anyone who has already seen one.

    The clip's POSITION among its siblings picks the title, so the pool is
    spread evenly — sixteen files over eight titles give two each. Hashing the
    filename was tried first and clustered badly: names as similar as
    pitch2/pitch3/pitch7 collided into six distinct titles out of eight, one of
    them used five times.

    Position is not stable if files are added, and that is fine here: the title
    only matters at upload time, an already-published video keeps whatever
    YouTube stored, and a loop clip coming round again in a later cycle is
    better off NOT repeating its previous title.
    """
    pool = cfg.get("promo.default_titles", []) or []
    if isinstance(pool, list) and pool:
        siblings = sorted(p.name.lower() for p in video.parent.iterdir()
                          if p.is_file() and p.suffix.lower() in VIDEO_EXT)
        try:
            idx = siblings.index(video.name.lower())
        except ValueError:
            idx = 0
        return str(pool[idx % len(pool)]).strip()
    single = str(cfg.get("promo.default_title", "") or "").strip()
    if single:
        return single
    # Nothing configured — the caller falls back to the FILE NAME, and a Short
    # published as "Pitch2" is what that looks like on the channel. Say so
    # loudly instead of letting it ship quietly.
    print(f"  [promo] WARNING: no promo.default_titles configured — "
          f"the title will be derived from the file name ({video.stem})")
    return ""


def load_meta(cfg: Config, video: Path) -> dict:
    """Title/description/tags for one video, from its sidecar or its name."""
    meta: dict = {}
    sidecar_json = video.with_suffix(".json")
    sidecar_txt = video.with_suffix(".txt")
    if sidecar_json.is_file():
        try:
            meta = json.loads(sidecar_json.read_text(encoding="utf-8")) or {}
        except Exception as exc:  # noqa: BLE001
            print(f"    [promo] {sidecar_json.name} is not valid JSON ({exc}) — ignoring")
    elif sidecar_txt.is_file():
        text = sidecar_txt.read_text(encoding="utf-8").strip()
        if text:
            # First line doubles as the title; the whole text is the caption.
            meta = {"title": text.splitlines()[0][:95], "description": text}

    # Order: the video's own sidecar wins, then the project-wide default, then
    # the filename. Promo clips are shot in batches and rarely get individual
    # copy, so a fixed default is what actually ships — "Pitch2" as a YouTube
    # title is what happens without it.
    default_title = _default_title(cfg, video)
    default_caption = str(cfg.get("promo.default_caption", "") or "").strip()
    tagline = cfg.get("channel.brand_tagline", "")

    title = str(meta.get("title") or default_title or _title_from_name(video))[:95]
    description = str(meta.get("description") or default_caption
                      or f"{title}\n\n{tagline}")
    tags = meta.get("tags")
    if not isinstance(tags, list):
        tags = list(cfg.get("youtube.default_tags", []) or [])
    return {"title": title, "description": description, "tags": tags}


def _caption(cfg: Config, meta: dict) -> str:
    """Instagram caption: the description, the tracked CTA, then the suffix."""
    from .links import cta_block
    suffix = cfg.get("instagram.caption_suffix", "")
    text = meta["description"]
    cta = cta_block(cfg, "instagram", content="reel")
    if cta and "utm_source" not in text:
        text = f"{text}\n\n{cta}"
    if suffix and suffix.lower() not in text.lower():
        text = f"{text}\n\n{suffix}"
    return text[:2100]


# --------------------------------------------------------------------------
# publishing
# --------------------------------------------------------------------------
def _resolution(video: Path) -> tuple[int, int]:
    """(width, height) parsed from ffmpeg's own output; (0, 0) if unreadable."""
    import subprocess
    from .ffmpeg import ffmpeg_exe
    try:
        proc = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", str(video)],
                              capture_output=True, text=True, timeout=60)
        m = re.search(r"Video:.*?(\d{2,5})x(\d{2,5})", proc.stderr)
        return (int(m.group(1)), int(m.group(2))) if m else (0, 0)
    except Exception:  # noqa: BLE001
        return (0, 0)


def preflight(cfg: Config, video: Path, *, to_youtube: bool,
              to_instagram: bool) -> list[str]:
    """Warn about anything the platforms will reject or mangle.

    Instagram publishes every feed video as a Reel (media_type=REELS), and the
    API only reports the problem after the upload has already been staged — so
    it is worth catching duration and orientation here, in the terminal.
    """
    from .ffmpeg import probe_duration
    lim = cfg.get("promo.limits", {}) or {}
    warnings: list[str] = []

    seconds = probe_duration(video)
    if seconds <= 0:
        warnings.append("could not read the duration — file may be corrupt")
        return warnings

    min_s = float(lim.get("min_seconds", 3))
    if seconds < min_s:
        warnings.append(f"only {seconds:.1f}s — Instagram rejects Reels under {min_s:g}s")
    if to_youtube:
        yt_max = float(lim.get("youtube_short_max_seconds", 180))
        if seconds > yt_max:
            warnings.append(f"{seconds:.0f}s is over {yt_max:g}s — YouTube will publish "
                            "it as a normal video, NOT as a Short")
    if to_instagram:
        ig_max = float(lim.get("instagram_max_seconds", 900))
        if seconds > ig_max:
            warnings.append(f"{seconds:.0f}s is over {ig_max:g}s — too long for a Reel")

    w, h = _resolution(video)
    if w and h and w >= h:
        warnings.append(f"{w}x{h} is not vertical — Shorts and Reels expect 9:16; "
                        "it will be letterboxed or cropped")
    return warnings


def publish_one(cfg: Config, video: Path, *, to_youtube: bool = True,
                to_instagram: bool = True) -> dict:
    """Publish one finished video. Returns {platform: bool|None}.

    None means "not attempted" (switch off in config); False means it was
    attempted and failed. The caller needs that distinction: a file must not be
    archived because every platform was simply disabled.
    """
    meta = load_meta(cfg, video)
    results: dict[str, bool | None] = {"youtube": None, "instagram": None}
    print(f"  title: {meta['title']}")
    for warning in preflight(cfg, video, to_youtube=to_youtube,
                             to_instagram=to_instagram):
        print(f"  ⚠ {warning}")

    if to_youtube and cfg.get("youtube.enabled", False):
        from .youtube_upload import upload_video
        vid = upload_video(cfg, video, meta["title"],
                           description=meta["description"],
                           tags=meta["tags"], is_short=True)
        results["youtube"] = bool(vid)
    elif to_youtube:
        print("  [youtube] skipped (youtube.enabled is false)")

    if to_instagram and cfg.get("instagram.enabled", False):
        from .instagram_upload import upload_reel
        ok = upload_reel(cfg, video, _caption(cfg, meta), publish=True)
        results["instagram"] = bool(ok)
    elif to_instagram:
        print("  [instagram] skipped (instagram.enabled is false)")

    return results


def _archive(video: Path) -> None:
    """Move the video and its sidecar into posted/ so it can never repeat."""
    posted = video.parent / "posted"
    posted.mkdir(parents=True, exist_ok=True)
    for p in (video, video.with_suffix(".json"), video.with_suffix(".txt")):
        if p.exists():
            target = posted / p.name
            if target.exists():          # never clobber an earlier archive
                target = posted / f"{p.stem}_{int(p.stat().st_mtime)}{p.suffix}"
            shutil.move(str(p), str(target))
    print(f"  archived -> {posted.name}/{video.name}")


def run_oneuse(cfg: Config, *, to_youtube: bool = True,
               to_instagram: bool = True) -> int:
    """Publish everything in videos/oneuse/, archiving what went out."""
    folder = oneuse_dir(cfg)
    videos = _videos_in(folder)
    if not videos:
        print(f"Nothing to post: {folder} is empty (or missing).")
        return 0

    print(f"Found {len(videos)} video(s) in {folder}")
    failures = 0
    for video in videos:
        print(f"\n[oneuse] {video.name}")
        # Default: a promo with its own narration gets the channel's captions
        # and LLM-written texts before it goes out (idempotent via the
        # sidecar's "captioned" flag). A failure here must not block the post.
        if cfg.get("promo.auto_caption", True):
            try:
                from .oneuse_prep import prepare
                prepare(cfg, video)
            except Exception as exc:  # noqa: BLE001
                print(f"  [prep] failed ({exc}) — publishing without captions")
        results = publish_one(cfg, video, to_youtube=to_youtube,
                              to_instagram=to_instagram)
        attempted = {k: v for k, v in results.items() if v is not None}
        if not attempted:
            print("  nothing was attempted — file kept in the inbox.")
            print("  enable youtube.enabled / instagram.enabled to publish.")
            continue
        if any(attempted.values()):
            # Archive on partial success by design: re-running would duplicate
            # the platform that DID work, and a published Reel cannot be
            # un-published. Losing one platform beats double-posting another.
            for name, ok in attempted.items():
                if not ok:
                    failures += 1
                    print(f"  !! {name} FAILED — post this one by hand: {video.name}")
            _archive(video)
        else:
            failures += 1
            print("  all platforms failed — file kept in the inbox for a retry.")
    return 1 if failures else 0


# --------------------------------------------------------------------------
# loop rotation
# --------------------------------------------------------------------------
def _state_path(cfg: Config) -> Path:
    return cfg.output_dir / "loop_rotation.json"


def _load_state(cfg: Config) -> dict:
    path = _state_path(cfg)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            data.setdefault("used", [])
            data.setdefault("cycle", 1)
            return data
        except Exception:  # noqa: BLE001
            pass
    return {"used": [], "cycle": 1}


def _save_state(cfg: Config, state: dict) -> None:
    _state_path(cfg).write_text(json.dumps(state, indent=2, ensure_ascii=False),
                                encoding="utf-8")


def pick_loop_video(cfg: Config) -> tuple[Path, dict] | None:
    """Next clip in the rotation, plus the state to commit IF it publishes.

    The state is deliberately NOT written here. Saving at pick time burns a
    clip's turn on a run that posted nothing — a dry run, or the normal case
    of both platforms being switched off — and the clip would then sit out the
    rest of the cycle despite never having been seen by anyone.

    Matching is by FILE NAME, not path, so the state survives the folder being
    moved. A clip dropped in mid-cycle joins the current cycle immediately.
    """
    candidates = _videos_in(loop_dir(cfg))
    if not candidates:
        return None
    state = _load_state(cfg)
    used = {str(n).lower() for n in state["used"]}
    available = [p for p in candidates if p.name.lower() not in used]
    if not available:
        state["cycle"] += 1
        state["used"] = []
        available = candidates
        print(f"  [loop] every clip used — starting cycle {state['cycle']}")
    choice = random.choice(available)
    state["used"].append(choice.name)
    print(f"  [loop] cycle {state['cycle']}: "
          f"{len(state['used'])}/{len(candidates)} used this cycle")
    return choice, state


def run_loop(cfg: Config, *, to_youtube: bool = True,
             to_instagram: bool = True) -> int:
    """Publish the next clip from the rotation. The file is never moved."""
    folder = loop_dir(cfg)
    pick = pick_loop_video(cfg)
    if pick is None:
        print(f"Nothing to post: {folder} is empty (or missing).")
        return 0
    video, state = pick
    print(f"\n[loop] {video.name}")
    results = publish_one(cfg, video, to_youtube=to_youtube,
                          to_instagram=to_instagram)
    attempted = {k: v for k, v in results.items() if v is not None}
    if not attempted:
        print("  nothing was attempted — enable youtube.enabled / instagram.enabled.")
        print("  rotation NOT advanced — this clip keeps its turn.")
        return 0
    if any(attempted.values()):
        _save_state(cfg, state)      # only a real post consumes the turn
        for name, ok in attempted.items():
            if not ok:
                print(f"  !! {name} FAILED — post this one by hand: {video.name}")
        return 0
    print("  all platforms failed — rotation NOT advanced.")
    return 1
