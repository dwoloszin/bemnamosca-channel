"""Captions + texts for finished promo videos that carry their own narration.

Default behaviour of the `oneuse` inbox (config promo.auto_caption): every
video dropped in videos/oneuse/ is, right before publishing,
  1. transcribed with word timestamps (Groq Whisper, free),
  2. burned with the channel's karaoke captions (same style as the carousel:
     phrase cuts, glow, orange emphasis) and the logo watermark — original
     audio untouched; the raw file is kept in videos/oneuse/_raw/,
  3. given a sidecar <name>.json {title, description, tags, captioned: true}
     written by the LLM from the transcript (+ promo.auto_caption_tags).

The sidecar's "captioned" flag is the idempotency marker: a video prepared on
the PC and uploaded by scripts/sync.py is not re-burned by the runner.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from .config import Config
from .tts import Word

ROOT = Path(__file__).resolve().parent.parent


def _env() -> dict:
    env = dict(os.environ)
    for l in (ROOT / ".env").read_text(encoding="utf-8", errors="ignore").splitlines():
        if "=" in l and not l.startswith("#"):
            k, v = l.split("=", 1)
            env.setdefault(k.strip(), v.strip())
    return env


def _ffmpeg() -> str:
    import imageio_ffmpeg
    return os.getenv("IMAGEIO_FFMPEG_EXE") or imageio_ffmpeg.get_ffmpeg_exe()


def transcribe(video: Path, work: Path) -> tuple[str, list[Word]]:
    import requests
    audio = work / (video.stem + ".mp3")
    subprocess.run([_ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", "-i", str(video),
                    "-vn", "-ac", "1", "-ar", "16000", "-b:a", "64k", str(audio)], check=True)
    keys = [k.strip() for k in _env().get("GROQ_API_KEYS", "").split(",") if k.strip()]
    last = ""
    for key in keys:
        with open(audio, "rb") as f:
            r = requests.post("https://api.groq.com/openai/v1/audio/transcriptions",
                              headers={"Authorization": f"Bearer {key}"},
                              data={"model": "whisper-large-v3", "language": "pt",
                                    "response_format": "verbose_json",
                                    "timestamp_granularities[]": "word"},
                              files={"file": (audio.name, f, "audio/mpeg")}, timeout=300)
        if r.ok:
            d = r.json()
            (work / (video.stem + ".transcript.json")).write_text(
                json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
            words = [Word(w["word"].strip(), float(w["start"]), float(w["end"]))
                     for w in d.get("words", []) if w.get("word", "").strip()]
            return d.get("text", "").strip(), words
        last = f"{r.status_code} {r.text[:100]}"
    raise SystemExit(f"transcricao falhou em todas as chaves Groq: {last}")


def _punctuate(words: list[Word], text: str) -> list[Word]:
    """Whisper's word list has no punctuation; the full text does. Copy the
    trailing punctuation onto each word so the phrase-aware caption cuts
    (on . , ! ?) work exactly as they do for the narrated videos."""
    tokens = text.split()
    out, j = [], 0
    for w in words:
        punct = ""
        if j < len(tokens):
            t = tokens[j]
            core = re.sub(r"[^\wÀ-ÿ]+", "", t).lower()
            if core == re.sub(r"[^\wÀ-ÿ]+", "", w.text).lower():
                m = re.search(r"[.,!?;:…]+$", t)
                punct = m.group(0) if m else ""
                j += 1
        # Whisper sometimes keeps the punctuation on the word already; never
        # double it ("notícia??", "marco..").
        base = w.text.rstrip(".,!?;:…")
        out.append(Word(base + (punct or w.text[len(base):]), w.start, w.end))
    return out


def burn(cfg, video: Path, words: list[Word], out: Path) -> Path:
    from src.subtitles import build_ass
    probe = subprocess.run([_ffmpeg(), "-hide_banner", "-i", str(video)], capture_output=True, text=True).stderr
    m = re.search(r"(\d{3,4})x(\d{3,4})", probe)
    w, h = (int(m.group(1)), int(m.group(2))) if m else (1080, 1920)
    ass = build_ass([(words, 0.0)], cfg, width=w, height=h, is_short=h > w)
    if ass is None:
        raise SystemExit("legendas desligadas no config (subtitles.enabled)")
    fonts = ROOT / "assets" / "fonts"
    rel_fonts = os.path.relpath(fonts, ass.parent).replace("\\", "/")
    filters = [f"[0:v]subtitles={ass.name}:fontsdir={rel_fonts}[sub]"]
    inputs = ["-i", str(video)]
    wm = cfg.get("video.watermark")
    cur = "[sub]"
    if wm and cfg.path_of(wm).exists():
        inputs += ["-i", str(cfg.path_of(wm))]
        wm_w = max(64, int(w * 0.12))
        filters.append(f"[1:v]scale={wm_w}:-1,format=rgba,colorchannelmixer=aa=0.85[wm]")
        filters.append(f"{cur}[wm]overlay=W-w-{int(w*0.03)}:{int(h*0.03)}[vout]")
        cur = "[vout]"
    else:
        filters.append(f"{cur}null[vout]")
        cur = "[vout]"
    cmd = [_ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", *inputs,
           "-filter_complex", ";".join(filters), "-map", cur, "-map", "0:a",
           "-c:v", "libx264", "-preset", "fast", "-crf", "19", "-pix_fmt", "yuv420p",
           "-c:a", "copy", "-movflags", "+faststart", str(out)]
    subprocess.run(cmd, check=True, cwd=ass.parent)
    return out


def write_texts(cfg, video: Path, transcript: str, extra_tags: list[str]) -> Path:
    from src.llm import get_llm
    from src.links import cta_block
    client = get_llm(cfg)
    prompt = f"""You write YouTube Shorts / Instagram Reels metadata for the Brazilian channel
"Bem na Mosca" (a free medicine price finder; the purchase is made at the pharmacy).
Voice: {cfg.get('script.style')}
Here is the narration of a finished {int(round(len(transcript.split())/2.5))}-second video:

\"\"\"{transcript}\"\"\"

Return ONLY JSON:
{{"title": "max 80 chars, Portuguese, a hook (number, question or contradiction), no clickbait, no emoji",
  "description": "2-3 short lines in Portuguese summarising the video for someone who has not watched it; factual; no hashtags, no links",
  "tags": ["8-12 short Portuguese search tags, lowercase, no #"]}}
Rules: invent no numbers, no medical advice, no cure claims, no real pharmacy names.
Mention the medicine and the regulator if the video does."""
    raw = client.ask(prompt) if client else None
    data = {}
    if raw:
        m = re.search(r"\{.*\}", raw, re.S)
        try:
            data = json.loads(m.group(0) if m else raw)
        except json.JSONDecodeError:
            data = {}
    title = str(data.get("title") or transcript.split(".")[0])[:95]
    desc = str(data.get("description") or transcript).strip()
    tags = [str(t).strip().lstrip("#").lower() for t in (data.get("tags") or []) if str(t).strip()]
    for t in extra_tags:
        if t.lower() not in tags:
            tags.insert(0, t.lower())
    hashtags = " ".join("#" + re.sub(r"[^\wÀ-ÿ]", "", t) for t in tags[:14])
    description = (f"{desc}\n\n{cta_block(cfg, 'youtube', content='promo')}\n\n"
                   f"{cfg.get('instagram.caption_suffix', '')}\n\n{hashtags}").strip()
    sidecar = video.with_suffix(".json")
    sidecar.write_text(json.dumps({"title": title, "description": description, "tags": tags},
                                  ensure_ascii=False, indent=2), encoding="utf-8")
    return sidecar




def prepare(cfg: Config, video: Path, extra_tags: list[str] | None = None, *,
            burn_captions: bool = True) -> dict | None:
    """Caption + texts for one video. Returns the sidecar dict, or None if
    the video was already prepared (sidecar says captioned: true)."""
    sidecar = video.with_suffix(".json")
    if sidecar.is_file():
        try:
            if json.loads(sidecar.read_text(encoding="utf-8")).get("captioned"):
                return None
        except json.JSONDecodeError:
            pass
    work = cfg.output_dir / "_work_oneuse"; work.mkdir(parents=True, exist_ok=True)
    print(f"  [prep] transcribing {video.name}…")
    text, words = transcribe(video, work)
    words = _punctuate(words, text)
    if not words:
        print("  [prep] no speech found — publishing as is")
        return None
    print(f"  [prep] {len(words)} words | {text[:80]}…")
    if burn_captions:
        raw_dir = video.parent / "_raw"; raw_dir.mkdir(exist_ok=True)
        raw = raw_dir / video.name
        if not raw.exists():
            shutil.copyfile(video, raw)
        tmp = work / (video.stem + ".captioned.mp4")
        burn(cfg, raw, words, tmp)
        shutil.move(str(tmp), str(video))
        print(f"  [prep] captions + watermark burned (original in {raw_dir.name}/)")
        # The final cut is also kept in output/promos/ — publishing moves the
        # inbox file to posted/, and this copy is what goes to TikTok/LinkedIn.
        final_dir = cfg.output_dir / "promos"; final_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(video, final_dir / video.name)
        print(f"  [prep] final copy -> {final_dir / video.name}")
    tags = list(extra_tags or []) + [str(t) for t in (cfg.get("promo.auto_caption_tags", []) or [])]
    write_texts(cfg, video, text, tags)
    meta = json.loads(sidecar.read_text(encoding="utf-8"))
    meta["captioned"] = bool(burn_captions)
    meta["transcript"] = text
    from .social_captions import build_tiktok_caption
    first = re.split(r"(?<=[.!?])", text.strip(), maxsplit=1)[0].strip()
    meta["tiktok"] = build_tiktok_caption(cfg, meta["title"], first, tags=meta.get("tags", [])[:6])
    (cfg.output_dir / "promos").mkdir(parents=True, exist_ok=True)
    (cfg.output_dir / "promos" / (video.stem + ".tiktok.txt")).write_text(meta["tiktok"], encoding="utf-8")
    from .social_captions import build_linkedin_page_caption
    meta["linkedin_page"] = build_linkedin_page_caption(cfg, meta["title"], first, tags=meta.get("tags", [])[:4])
    (cfg.output_dir / "promos" / (video.stem + ".linkedin_page.txt")).write_text(meta["linkedin_page"], encoding="utf-8")
    sidecar.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    final_dir = cfg.output_dir / "promos"; final_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(sidecar, final_dir / sidecar.name)      # texts travel with the video
    print(f"  [prep] title: {meta['title']}")
    return meta
