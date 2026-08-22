"""Groq TTS (PlayAI voices) — middle fallback between ElevenLabs and edge-tts.

Reuses the same GROQ_API_KEY pool as script generation, rotating keys on rate
limits. Groq's speech endpoint returns no word timestamps, so word timings for
subtitles are estimated proportionally from the audio duration — accurate
enough for 3-word captions.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path

import requests

from .config import Config
from .tts import SpeechClip, Word

API = "https://api.groq.com/openai/v1/audio/speech"
_COOLDOWN_S = 3600


def _key_id(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()[:12]


def _keys() -> list[str]:
    import os
    raw = os.getenv("GROQ_API_KEYS", "") or os.getenv("GROQ_API_KEY", "")
    return [k.strip() for k in re.split(r"[,;\s]+", raw) if k.strip().startswith("gsk_")]


def _estimate_words(text: str, duration: float) -> list[Word]:
    """Distribute the audio duration across words, weighted by word length."""
    tokens = re.findall(r"\S+", text)
    if not tokens or duration <= 0:
        return []
    weights = [len(t) + 2.0 for t in tokens]   # +2 ~ inter-word pause share
    total = sum(weights)
    words: list[Word] = []
    t = 0.0
    for tok, wgt in zip(tokens, weights):
        span = duration * (wgt / total)
        words.append(Word(tok, t, t + span * 0.9))
        t += span
    return words


def synthesize_groq(text: str, out_path: str | Path, cfg: Config) -> SpeechClip:
    keys = _keys()
    if not keys:
        raise RuntimeError("no GROQ_API_KEY configured")

    text = (text or "").strip()
    if not text:
        raise ValueError("Cannot synthesize empty text")

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    state_path = cfg.output_dir / "groq_tts_state.json"
    state: dict[str, dict] = {}
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            state = {}

    def save() -> None:
        state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")

    model = cfg.get("tts.groq.model", "playai-tts")
    voice = cfg.get("tts.groq.voice", "Fritz-PlayAI")
    now = time.time()

    last_err: Exception | None = None
    for key in keys:
        st = state.get(_key_id(key), {})
        if st.get("invalid") or now < st.get("cooldown_until", 0):
            continue
        try:
            r = requests.post(
                API,
                headers={"Authorization": f"Bearer {key}",
                         "Content-Type": "application/json"},
                json={"model": model, "voice": voice, "input": text,
                      "response_format": "mp3"},
                timeout=120,
            )
            if r.status_code in (401, 403):
                print(f"  [groq-tts] key ...{key[-4:]} invalid — retiring")
                state.setdefault(_key_id(key), {})["invalid"] = True
                save()
                continue
            if r.status_code == 429:
                print(f"  [groq-tts] key ...{key[-4:]} rate-limited — 1h cooldown")
                state.setdefault(_key_id(key), {})["cooldown_until"] = time.time() + _COOLDOWN_S
                save()
                continue
            if r.status_code == 400 and ("decommissioned" in r.text or "does not exist" in r.text):
                # Model-level problem, not key-level — no point rotating.
                raise RuntimeError(
                    f"Groq TTS model '{model}' unavailable (decommissioned/unknown). "
                    "Update tts.groq.model in config.yaml if Groq ships a new TTS model."
                )
            r.raise_for_status()
            out_path.write_bytes(r.content)
            from .ffmpeg import probe_duration
            duration = probe_duration(out_path)
            return SpeechClip(out_path, duration, _estimate_words(text, duration))
        except requests.RequestException as exc:
            last_err = exc
            print(f"  [groq-tts] key ...{key[-4:]} failed ({exc}) — trying next")
            continue

    raise RuntimeError(f"no Groq TTS key succeeded (last error: {last_err})")
