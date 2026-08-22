"""One AI photo per story for the carousel's NEWS beats (Gemini image models).

Why a separate module from imagen.py: that one is the Cloudflare/FLUX pool
the promo posts use, with its own quota dance. This is a single billed Gemini
key (GEMINI_API_KEY_IMG), one call a day, and the rules are different:

  * News beats only. The app beats stay real screenshots — the brand brief's
    rule 3 ("never an AI-invented interface") is the right rule.
  * Cached per (day, story): the daily retry ladder re-runs make_carousel up
    to four times, and each run must NOT bill a new image.
  * Every generated image is ALSO kept in a permanent library
    (assets/images/ai/). When the API cannot deliver — no credits, quota,
    network — the library stands in: the closest match by keyword, else the
    least-used one. A story never goes out with a vintage apothecary again
    just because a key ran dry.
  * "no text, no logos, no signage" is in every prompt: image models love to
    invent pharmacy names, and a made-up brand on a real-news video is
    exactly what the brief forbids.

YouTube asks for realistic synthetic content to be flagged; the uploader sets
status.containsSyntheticMedia when a package used one of these images —
generated today or pulled from the library, it is AI either way.
"""
from __future__ import annotations

import base64
import hashlib
import os
import re
import shutil
import time
from datetime import date
from pathlib import Path

import requests

from .config import Config

_API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

_STYLE = ("Editorial photograph, realistic, natural light, shallow depth of field, "
          "modern Brazilian setting, candid. Medicines as cardboard boxes and blister "
          "packs (Brazilian pharmacy style), never orange pill bottles. No text, no "
          "letters, no logos, no brand names, no signage, no watermarks")


def enabled(cfg: Config) -> bool:
    return bool(cfg.get("media.ai_image.enabled", False)) and bool(_key())


def _key() -> str:
    return (os.getenv("GEMINI_API_KEY_IMG") or "").strip()


def build_prompt(cfg: Config, keyword: str, headline: str) -> str:
    """Scene from the slide's visual keyword, anchored by the story, in the
    channel's photographic style. The keyword is already English (the script
    prompt insists on it for the stock search); the headline gives context so
    "person reading price label" becomes a pharmacy, not a supermarket."""
    scene = (keyword or "").strip() or "person reading the price label on a medicine box in a pharmacy"
    extra = str(cfg.get("media.ai_image.style", "") or "").strip()
    return (f"{scene}. Context of the story (do not render any text from it): {headline}. "
            f"{_STYLE}{', ' + extra if extra else ''}")


def _cache_path(cfg: Config, headline: str, when: date) -> Path:
    h = hashlib.sha1(headline.strip().lower().encode("utf-8")).hexdigest()[:10]
    d = cfg.output_dir / "_work_carousel_feed" / "ai"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"ai_{when:%Y%m%d}_{h}.png"


# --------------------------------------------------------------------------
# permanent library
# --------------------------------------------------------------------------
def _library_dir(cfg: Config) -> Path:
    d = cfg.path_of(str(cfg.get("media.ai_image.library_dir", "assets/images/ai")))
    d.mkdir(parents=True, exist_ok=True)
    return d


def _slug(text: str, n: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s[:n] or "scene"


def _keep(cfg: Config, img: Path, keyword: str, when: date) -> None:
    """Copy a fresh image into the library, named by date + keyword so a
    later fallback can match it by content."""
    try:
        dest = _library_dir(cfg) / f"ai_{when:%Y%m%d}_{_slug(keyword)}{img.suffix}"
        if not dest.exists():
            shutil.copyfile(img, dest)
    except OSError as exc:
        print(f"  [ai-image] could not store in library ({exc})")


def from_library(cfg: Config, keyword: str) -> Path | None:
    """Best stand-in from past generations: most keyword words in common,
    then least-used (the project's existing rotation guarantee)."""
    lib = _library_dir(cfg)
    files = [p for p in lib.iterdir() if p.suffix.lower() in (".png", ".jpg", ".jpeg")]
    if not files:
        return None
    words = {w for w in re.split(r"[^a-z0-9]+", (keyword or "").lower()) if len(w) > 3}
    try:
        from .media_log import order_by_usage
        files = order_by_usage(cfg, files)          # least-used first
    except Exception:  # noqa: BLE001
        pass
    scored = sorted(files, key=lambda p: -len(words & set(p.stem.split("-"))))
    pick = scored[0]
    print(f"  [ai-image] using library image {pick.name}")
    return pick


# --------------------------------------------------------------------------
# generation
# --------------------------------------------------------------------------
def _generate(cfg: Config, prompt: str, out: Path) -> bool:
    models = [str(m) for m in (cfg.get("media.ai_image.models", []) or
                               ["gemini-3.1-flash-image", "gemini-2.5-flash-image"])]
    ratio = str(cfg.get("media.ai_image.aspect_ratio", "9:16"))
    for model in models:
        t0 = time.time()
        try:
            r = requests.post(_API.format(model=model), params={"key": _key()}, timeout=180, json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"responseModalities": ["IMAGE"],
                                     "imageConfig": {"aspectRatio": ratio}},
            })
        except requests.RequestException as exc:
            print(f"  [ai-image] {model} failed ({exc}) — trying next")
            continue
        if not r.ok:
            msg = r.text[:120].replace("\n", " ")
            print(f"  [ai-image] {model} -> {r.status_code} {msg} — trying next")
            continue
        try:
            parts = r.json()["candidates"][0]["content"]["parts"]
            blob = next(p["inlineData"] for p in parts if "inlineData" in p)
        except (KeyError, IndexError, StopIteration):
            print(f"  [ai-image] {model} answered without an image — trying next")
            continue
        out.write_bytes(base64.b64decode(blob["data"]))
        print(f"  [ai-image] {model}: {out.name} ({out.stat().st_size // 1024} KB, "
              f"{time.time() - t0:.0f}s)")
        return True
    return False


def news_image(cfg: Config, keyword: str, headline: str,
               when: date | None = None) -> Path | None:
    """The day's AI photo for this story — generated once, then reused;
    library stand-in when the API cannot deliver."""
    if not enabled(cfg):
        return None
    when = when or date.today()
    out = _cache_path(cfg, headline, when)
    if out.exists() and out.stat().st_size > 10_000:
        print(f"  [ai-image] reusing today's image for this story ({out.name})")
        return out
    if _generate(cfg, build_prompt(cfg, keyword, headline), out):
        _keep(cfg, out, keyword, when)
        return out
    print("  [ai-image] no model produced an image — looking in the library")
    stand_in = from_library(cfg, keyword)
    if stand_in:
        # Mark the story as AI-illustrated today (cache + synthetic flag) even
        # though the image is a reuse: it is still a generated picture.
        shutil.copyfile(stand_in, out)
        return out
    return None


def used_today(cfg: Config, headline: str, when: date | None = None) -> bool:
    """Did this story get an AI image? (The uploader asks, to flag the video.)"""
    p = _cache_path(cfg, headline, when or date.today())
    return p.exists() and p.stat().st_size > 10_000
