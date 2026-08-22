"""Collect and prepare visuals (images) and background music.

Copyright strategy:
  * Prefer the user's own asset library (img-channel / assets/images).
  * Optionally pull ROYALTY-FREE stock from Pexels (needs PEXELS_API_KEYS —
    one or more keys, comma-separated; spent keys rotate out automatically).
  * Article images are only used if media.use_article_images is explicitly true.
  * All original audio from any source clip is dropped; a NEW soundtrack + voice
    is added instead.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import requests
from PIL import Image, ImageOps

from .config import Config

_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
_VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}

# prepared/workdir copy -> original library file, so the media-usage log can
# credit the real asset (workdir files are deleted after every render).
_ORIGIN: dict[Path, Path] = {}


def origin_of(p: Path) -> Path:
    return _ORIGIN.get(Path(p), Path(p))


def is_video(path: Path) -> bool:
    return path.suffix.lower() in _VIDEO_EXT


def _branding_paths(cfg: Config) -> set[Path]:
    """Intro/outro/watermark images — branding, never content slides."""
    out: set[Path] = set()
    for key in ("video.intro_image", "video.outro_image", "video.watermark",
                "video.short.intro_image", "video.short.outro_image",
                "video.long.intro_image", "video.long.outro_image"):
        v = cfg.get(key)
        if v:
            out.add(cfg.path_of(v).resolve())
    return out


def gather_local_images(cfg: Config, orientation: str | None = None) -> list[Path]:
    """Images from the general asset dirs plus the orientation-specific dirs
    (media.asset_dirs_vertical for Shorts, media.asset_dirs_horizontal for
    long videos) so art is never awkwardly cropped. Branding images (intro/
    outro/watermark) are excluded even when they live inside these folders.

    media.override_dir (set by the CLI's folder argument) replaces ALL of the
    above: only that folder — subfolders included — is used this run."""
    override = cfg.get("media.override_dir")
    if override:
        dirs = [override]
    else:
        dirs = list(cfg.get("media.asset_dirs", []) or [])
        if orientation == "vertical":
            dirs += cfg.get("media.asset_dirs_vertical", []) or []
        elif orientation == "horizontal":
            dirs += cfg.get("media.asset_dirs_horizontal", []) or []
    branding = _branding_paths(cfg)
    images: list[Path] = []
    for d in dirs:
        base = cfg.path_of(d)
        if not base.exists():
            continue
        # rglob: subfolders (e.g. assets/images/vehicles/cars/) count too.
        for p in sorted(base.rglob("*")):
            if not p.is_file() or p.suffix.lower() not in _IMAGE_EXT:
                continue
            # Branding by config reference or by naming convention.
            if p.resolve() in branding or p.name.lower().startswith("channelbanner"):
                continue
            images.append(p)
    return images


def gather_local_videos(cfg: Config) -> list[Path]:
    """Video clips from the asset folders (your own / licensed footage).

    Their ORIGINAL AUDIO IS ALWAYS STRIPPED at render time — the narration and
    royalty-free music are the only soundtrack.

    media.override_dir replaces the asset folders and is searched RECURSIVELY.
    """
    videos: list[Path] = []
    override = cfg.get("media.override_dir")
    if override:
        base = cfg.path_of(override)
        if base.exists():
            videos = [p for p in sorted(base.rglob("*"))
                      if p.is_file() and p.suffix.lower() in _VIDEO_EXT]
        return videos
    for d in cfg.get("media.asset_dirs", []) or []:
        base = cfg.path_of(d)
        if not base.exists():
            continue
        for p in sorted(base.iterdir()):
            if p.suffix.lower() in _VIDEO_EXT:
                videos.append(p)
    return videos


# Keys that answered 429/401/403 this run. Kept for the process lifetime so a
# spent key is not retried on every keyword — one carousel alone issues five
# searches, and re-probing a dead key each time just adds latency.
_pexels_spent: set[str] = set()


def _pexels_search(cfg: Config, query: str, count: int):
    """Search Pexels, rotating through the key pool on quota/auth failures.

    Returns the successful response, or None when every key is spent. A key is
    retired only for the reasons that will not fix themselves (quota exhausted,
    key rejected); a timeout or a 5xx is the SERVICE failing, not the key, so
    that one moves on without burning it.
    """
    keys = [k for k in cfg.pexels_keys if k not in _pexels_spent]
    if not keys:
        if cfg.pexels_keys:
            print("  [media] all Pexels keys are spent for this run")
        return None

    for i, key in enumerate(keys):
        try:
            resp = requests.get(
                "https://api.pexels.com/v1/search",
                params={"query": query, "per_page": count,
                        "orientation": "landscape"},
                headers={"Authorization": key},
                timeout=20,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  [media] Pexels key ...{key[-4:]} error ({exc}) — next key")
            continue
        if resp.status_code in (401, 403, 429):
            reason = "quota exhausted" if resp.status_code == 429 else "key rejected"
            _pexels_spent.add(key)
            print(f"  [media] Pexels key ...{key[-4:]} {reason} "
                  f"({len(keys) - i - 1} left) — rotating")
            continue
        if not resp.ok:
            print(f"  [media] Pexels HTTP {resp.status_code} for '{query}' — next key")
            continue
        remaining = resp.headers.get("X-Ratelimit-Remaining")
        if remaining is not None and str(remaining).isdigit() and int(remaining) == 0:
            # Served this one, but it was the last call on that key.
            _pexels_spent.add(key)
            print(f"  [media] Pexels key ...{key[-4:]} hit its limit with this call")
        return resp
    return None


def pexels_images(cfg: Config, query: str, count: int, workdir: Path) -> list[Path]:
    if not cfg.get("media.use_pexels", False):
        return []
    resp = _pexels_search(cfg, query, count)
    if resp is None:
        return []

    out: list[Path] = []
    workdir.mkdir(parents=True, exist_ok=True)
    for photo in resp.json().get("photos", []):
        url = (photo.get("src") or {}).get("large2x") or (photo.get("src") or {}).get("large")
        if not url:
            continue
        name = hashlib.md5(url.encode()).hexdigest()[:16] + ".jpg"
        dest = workdir / name
        if not dest.exists():
            try:
                img = requests.get(url, timeout=30)
                img.raise_for_status()
                dest.write_bytes(img.content)
            except Exception as exc:  # noqa: BLE001
                print(f"  [media] Pexels download failed: {exc}")
                continue
        out.append(dest)
    return out


def prepare_image(src: Path, width: int, height: int, dest: Path) -> Path | None:
    """Fit `src` to width x height (cover + center crop). Strips metadata."""
    try:
        with Image.open(src) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")
            fitted = ImageOps.fit(im, (width, height), method=Image.LANCZOS, centering=(0.5, 0.5))
            dest.parent.mkdir(parents=True, exist_ok=True)
            # Re-save without metadata -> removes any embedded EXIF/rights tags.
            fitted.save(dest, "JPEG", quality=90)
        return dest
    except Exception as exc:  # noqa: BLE001
        print(f"  [media] could not prepare {src.name}: {exc}")
        return None


def build_image_pool(
    cfg: Config,
    keywords: list[str],
    width: int,
    height: int,
    workdir: Path,
    *,
    want: int,
) -> list[Path]:
    """Return prepared visuals to use as slides.

    The pool mixes the user's VIDEO CLIPS (used as-is, muted/cropped at render
    time) with normalized images (own library + royalty-free stock).
    Ordering is LEAST-USED-FIRST (persistent media-usage log) — every library
    file appears once before any file is picked a second time.
    """
    from .media_log import order_by_usage

    videos = order_by_usage(cfg, gather_local_videos(cfg))

    orientation = "vertical" if height > width else "horizontal"

    raw_images: list[Path] = []
    # 1) royalty-free stock keyed to the story (skipped when the user pinned a
    #    specific media folder — they want THOSE visuals, not stock)
    if not cfg.get("media.override_dir"):
        seen_query: set[str] = set()
        for kw in keywords:
            kw = (kw or "").strip()
            if not kw or kw.lower() in seen_query:
                continue
            seen_query.add(kw.lower())
            raw_images.extend(pexels_images(cfg, kw, cfg.get("media.pexels_per_query", 5), workdir / "pexels"))
    # 2) the user's own library (always available, copyright-safe),
    #    orientation-matched to the video format
    raw_images.extend(order_by_usage(cfg, gather_local_images(cfg, orientation)))

    # Normalize images (videos are handled by the renderer directly).
    prepared_imgs: list[Path] = []
    prep_dir = workdir / "prepared"
    need_imgs = max(0, want - len(videos))
    for i, src in enumerate(raw_images):
        if len(prepared_imgs) >= need_imgs:
            break
        dest = prep_dir / f"slide_{i:03d}.jpg"
        out = prepare_image(src, width, height, dest)
        if out:
            _ORIGIN[out] = src
            prepared_imgs.append(out)

    # Interleave: video, image, video, image... then leftovers.
    prepared: list[Path] = []
    vi, ii = 0, 0
    while len(prepared) < want and (vi < len(videos) or ii < len(prepared_imgs)):
        if vi < len(videos):
            prepared.append(videos[vi]); vi += 1
        if len(prepared) < want and ii < len(prepared_imgs):
            prepared.append(prepared_imgs[ii]); ii += 1

    # If still short, cycle what we have so every slot is filled.
    if prepared and len(prepared) < want:
        j = 0
        while len(prepared) < want:
            prepared.append(prepared[j % len(prepared)])
            j += 1
    return prepared


def music_credit(track: Path | None) -> str | None:
    """Attribution line for a track, from CREDITS.json next to it (CC-BY etc.)."""
    if track is None:
        return None
    import json
    credits_file = track.parent / "CREDITS.json"
    if not credits_file.exists():
        return None
    try:
        data = json.loads(credits_file.read_text(encoding="utf-8"))
        return data.get(track.name)
    except Exception:  # noqa: BLE001
        return None


def pick_music(cfg: Config) -> Path | None:
    if not cfg.get("music.enabled", False):
        return None
    d = cfg.path_of(cfg.get("music.dir", "assets/music"))
    if not d.exists():
        return None
    tracks = [p for p in d.iterdir() if p.suffix.lower() in {".mp3", ".m4a", ".wav", ".ogg"}]
    if not tracks:
        return None
    # Rotate: the least-recently/least-often used track goes first.
    from .media_log import order_by_usage
    return order_by_usage(cfg, tracks)[0]
