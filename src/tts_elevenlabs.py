"""ElevenLabs TTS with a managed pool of API keys.

Strategy:
  * ELEVENLABS_API_KEYS in .env holds several free-tier keys (comma-separated).
  * Before each synthesis we ask the API how many credits the current key has
    left; if not enough for this text, we rotate to the next key.
  * Keys that are exhausted/invalid are remembered in output/elevenlabs_state.json
    and skipped for 30 days (free-tier credits reset monthly).
  * The /with-timestamps endpoint returns character-level timing, which we
    convert to word timings — so subtitles stay perfectly synced.

If every key is exhausted, the caller falls back to edge-tts automatically.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path

import requests

from .config import Config
from .tts import SpeechClip, Word

API = "https://api.elevenlabs.io/v1"

# Credit cost per character by model family (turbo/flash are half price).
_MODEL_COST = {
    "eleven_turbo_v2_5": 0.5,
    "eleven_flash_v2_5": 0.5,
    "eleven_multilingual_v2": 1.0,
}

_EXHAUST_DAYS = 30


class AllKeysExhausted(RuntimeError):
    pass


def _key_id(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()[:12]


@dataclass
class _KeyState:
    exhausted_at: float | None = None
    invalid: bool = False


class KeyPool:
    def __init__(self, keys: list[str], state_path: Path):
        self.keys = keys
        self.state_path = state_path
        self.state: dict[str, dict] = {}
        if state_path.exists():
            try:
                self.state = json.loads(state_path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                self.state = {}

    def _save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(self.state, indent=2), encoding="utf-8")

    def usable_keys(self) -> list[str]:
        out = []
        now = time.time()
        for k in self.keys:
            st = self.state.get(_key_id(k), {})
            if st.get("invalid"):
                continue
            ex = st.get("exhausted_at")
            if ex and now - ex < _EXHAUST_DAYS * 86400:
                continue
            out.append(k)
        return out

    def mark_exhausted(self, key: str) -> None:
        self.state.setdefault(_key_id(key), {})["exhausted_at"] = time.time()
        self._save()

    def mark_invalid(self, key: str) -> None:
        self.state.setdefault(_key_id(key), {})["invalid"] = True
        self._save()

    def remaining_credits(self, key: str) -> float | None:
        """Ask the API for the key's remaining credits.

        Returns None when unknown — including on 401, because keys can be
        permission-scoped to TTS-only and then can't read the subscription.
        Only a 401 from the TTS endpoint itself proves a key is invalid.
        """
        try:
            r = requests.get(f"{API}/user/subscription",
                             headers={"xi-api-key": key}, timeout=15)
            if not r.ok:
                return None
            d = r.json()
            return float(d.get("character_limit", 0)) - float(d.get("character_count", 0))
        except Exception:  # noqa: BLE001
            return None


def load_pool(cfg: Config) -> KeyPool | None:
    import os
    raw = os.getenv("ELEVENLABS_API_KEYS", "") or os.getenv("ELEVENLABS_API_KEY", "")
    keys = [k.strip() for k in re.split(r"[,;\s]+", raw) if k.strip().startswith("sk_")]
    if not keys:
        return None
    return KeyPool(keys, cfg.output_dir / "elevenlabs_state.json")


# --------------------------------------------------------------------------
# synthesis
# --------------------------------------------------------------------------
def _words_from_alignment(text: str, alignment: dict) -> list[Word]:
    starts = alignment.get("character_start_times_seconds") or []
    ends = alignment.get("character_end_times_seconds") or []
    chars = alignment.get("characters") or []
    aligned = "".join(chars)
    # The API echoes the input text; index words directly into the timing arrays.
    source = aligned if len(aligned) == len(starts) else text
    words: list[Word] = []
    for m in re.finditer(r"\S+", source):
        s, e = m.start(), m.end() - 1
        if s < len(starts) and e < len(ends):
            words.append(Word(m.group(0), float(starts[s]), float(ends[e])))
    return words


def synthesize_elevenlabs(text: str, out_path: str | Path, cfg: Config, *,
                          previous_text: str = "", next_text: str = "") -> SpeechClip:
    """Synthesize with the first key that has enough credits. Raises
    AllKeysExhausted when the whole pool is spent (caller falls back)."""
    pool = load_pool(cfg)
    if pool is None:
        raise AllKeysExhausted("no ELEVENLABS_API_KEYS configured")

    text = (text or "").strip()
    if not text:
        raise ValueError("Cannot synthesize empty text")

    voice_id = cfg.get("tts.elevenlabs.voice_id", "pNInz6obpgDQGcFmaJgB")  # Adam
    model_id = cfg.get("tts.elevenlabs.model", "eleven_turbo_v2_5")
    cost = len(text) * _MODEL_COST.get(model_id, 1.0)

    # Force the language. Without this the model auto-detects it per request,
    # and an English-library voice reading a short Portuguese line slipped
    # into English phonetics — "preço" came out as "preco" (21/08 samples).
    # Only the v2.5 models accept the field; multilingual_v2 rejects it.
    payload_extra: dict = {}
    lang = str(cfg.get("tts.elevenlabs.language_code", "pt") or "").strip()
    if lang and model_id in ("eleven_turbo_v2_5", "eleven_flash_v2_5"):
        payload_extra["language_code"] = lang
    # The narration is synthesized one block at a time. Without context each
    # block opens with a fresh, flat "first sentence" prosody and the joins
    # sound like five different takes. previous_text / next_text let the
    # model carry the intonation across the cut.
    if previous_text:
        payload_extra["previous_text"] = previous_text[-600:]
    if next_text:
        payload_extra["next_text"] = next_text[:600]
    speed = cfg.get("tts.elevenlabs.speed", None)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    usable = pool.usable_keys()
    if not usable:
        raise AllKeysExhausted("all ElevenLabs keys exhausted or invalid")

    DEFAULT_FREE_VOICE = "pNInz6obpgDQGcFmaJgB"  # Adam — premade, works on free keys

    last_err: Exception | None = None
    i = 0
    while i < len(usable):
        key = usable[i]
        remaining = pool.remaining_credits(key)
        # None = credits unknown (e.g. TTS-only scoped key) -> try anyway;
        # the TTS call itself is the authority on quota/validity.
        if remaining is not None and remaining < cost + 50:  # keep a small buffer
            print(f"  [11labs] key ...{key[-4:]} has {remaining:.0f} credits "
                  f"(need ~{cost:.0f}) — rotating")
            pool.mark_exhausted(key)
            i += 1
            continue
        try:
            r = requests.post(
                f"{API}/text-to-speech/{voice_id}/with-timestamps",
                params={"output_format": "mp3_44100_128"},
                headers={"xi-api-key": key, "Content-Type": "application/json"},
                json={
                    "text": text,
                    "model_id": model_id,
                    **payload_extra,
                    "voice_settings": {
                        "stability": float(cfg.get("tts.elevenlabs.stability", 0.5)),
                        "similarity_boost": float(cfg.get("tts.elevenlabs.similarity", 0.75)),
                        "style": float(cfg.get("tts.elevenlabs.style", 0.2)),
                        **({"speed": float(speed)} if speed else {}),
                    },
                },
                timeout=120,
            )
            if r.status_code == 401:
                print(f"  [11labs] key ...{key[-4:]} invalid — skipping permanently")
                pool.mark_invalid(key)
                i += 1
                continue
            if r.status_code == 429 or (r.status_code == 400 and "quota" in r.text.lower()):
                print(f"  [11labs] key ...{key[-4:]} out of quota — rotating")
                pool.mark_exhausted(key)
                i += 1
                continue
            if r.status_code == 402:
                # "Free users cannot use library voices via the API" — the
                # chosen voice needs a paid plan. Retry the SAME key with a
                # premade voice that free keys can use.
                if voice_id != DEFAULT_FREE_VOICE:
                    print(f"  [11labs] voice {voice_id} needs a PAID plan "
                          f"— switching to the free premade voice (Adam)")
                    voice_id = DEFAULT_FREE_VOICE
                    continue  # same key, new voice
                pool.mark_exhausted(key)
                i += 1
                continue
            r.raise_for_status()
            data = r.json()
            out_path.write_bytes(base64.b64decode(data["audio_base64"]))
            words = _words_from_alignment(text, data.get("alignment") or {})
            from .ffmpeg import probe_duration
            duration = probe_duration(out_path)
            if words:
                duration = max(duration, words[-1].end)
            return SpeechClip(out_path, duration, words)
        except (requests.RequestException, KeyError, ValueError) as exc:
            last_err = exc
            print(f"  [11labs] key ...{key[-4:]} failed ({exc}) — trying next")
            i += 1
            continue

    raise AllKeysExhausted(f"no ElevenLabs key succeeded (last error: {last_err})")


def pool_status(cfg: Config) -> str:
    """Human-readable credit status for every key (for `main.py setup`)."""
    pool = load_pool(cfg)
    if pool is None:
        return "no keys configured"
    lines = []
    for k in pool.keys:
        rem = pool.remaining_credits(k)
        st = pool.state.get(_key_id(k), {})
        flag = "INVALID" if st.get("invalid") else (
            "exhausted" if st.get("exhausted_at") else "ok")
        lines.append(f"  ...{k[-4:]}: {flag}, credits left: "
                     f"{'?' if rem is None else int(rem)}")
    return "\n".join(lines)
