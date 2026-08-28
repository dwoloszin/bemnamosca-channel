"""Text-to-speech via edge-tts (free, no API key).

Returns both the audio file and word-level timings, which we use to build
subtitles that stay perfectly in sync with the voice.
"""
from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass, field
from pathlib import Path

import edge_tts

# edge-tts uses aiohttp/aiodns, which on Windows requires a SelectorEventLoop
# (the default ProactorEventLoop crashes aiodns). Set it once at import.
if sys.platform.startswith("win"):
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    except Exception:  # noqa: BLE001
        pass

# edge-tts reports time in 100-nanosecond "ticks".
TICKS_PER_SECOND = 10_000_000


@dataclass
class Word:
    text: str
    start: float  # seconds, relative to the start of this clip
    end: float


@dataclass
class SpeechClip:
    audio_path: Path
    duration: float
    words: list[Word] = field(default_factory=list)


async def _synthesize(text: str, out_path: Path, voice: str, rate: str, volume: str) -> list[Word]:
    # boundary="WordBoundary" is required on edge-tts >= 7 (default changed to
    # SentenceBoundary) — without it we get no word timings and no subtitles.
    communicate = edge_tts.Communicate(
        text, voice=voice, rate=rate, volume=volume, boundary="WordBoundary"
    )
    words: list[Word] = []
    with open(out_path, "wb") as f:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                start = chunk["offset"] / TICKS_PER_SECOND
                dur = chunk["duration"] / TICKS_PER_SECOND
                words.append(Word(chunk["text"], start, start + dur))
    return words


def synthesize(text: str, out_path: str | Path, *, voice: str,
               rate: str = "+0%", volume: str = "+0%") -> SpeechClip:
    """Synthesize `text` to an mp3 at `out_path`. Returns clip + word timings."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    text = (text or "").strip()
    if not text:
        raise ValueError("Cannot synthesize empty text")

    words = asyncio.run(_synthesize(text, out_path, voice, rate, volume))
    # Use the REAL audio duration, not the last word's end: edge-tts pads each
    # file with trailing silence, and ignoring it makes subtitle/slide offsets
    # drift ahead of the voice a little more with every block.
    duration = _probe_duration(out_path)
    if words:
        duration = max(duration, words[-1].end)
    return SpeechClip(out_path, duration, words)


def _probe_duration(path: Path) -> float:
    """Fallback duration probe using the bundled ffprobe/ffmpeg."""
    from .ffmpeg import probe_duration
    return probe_duration(path)


def synthesize_auto(text: str, out_path: str | Path, cfg, *,
                    previous_text: str = "", next_text: str = "") -> SpeechClip:
    """Provider dispatcher with graceful degradation:
        elevenlabs -> groq (PlayAI) -> edge-tts
    Start the chain at whichever provider config selects."""
    provider = cfg.get("tts.provider", "edge")

    # Numbers as words: TTS voices (especially an English voice speaking
    # Portuguese) misread "3,81%" — see src/tts_text.py. Audio and captions
    # only; titles/descriptions keep the digits.
    if cfg.get("tts.normalize_numbers", True):
        try:
            from .tts_text import normalize_numbers_pt
            text = normalize_numbers_pt(text)
        except Exception as exc:  # noqa: BLE001
            print(f"  [tts] number normalization failed ({exc}) — using raw text")

    if provider == "elevenlabs":
        try:
            from .tts_elevenlabs import synthesize_elevenlabs
            return synthesize_elevenlabs(text, out_path, cfg,
                                         previous_text=previous_text, next_text=next_text)
        except Exception as exc:  # noqa: BLE001
            print(f"  [tts] ElevenLabs unavailable ({exc}) — trying Groq TTS")
            provider = "groq"

    if provider == "groq":
        try:
            from .tts_groq import synthesize_groq
            return synthesize_groq(text, out_path, cfg)
        except Exception as exc:  # noqa: BLE001
            print(f"  [tts] Groq TTS unavailable ({exc}) — falling back to edge-tts")

    return synthesize(
        text, out_path,
        voice=cfg.get("tts.voice", "en-US-GuyNeural"),
        rate=cfg.get("tts.rate", "+0%"),
        volume=cfg.get("tts.volume", "+0%"),
    )


async def _list_voices(language: str | None) -> list[dict]:
    voices = await edge_tts.list_voices()
    if language:
        voices = [v for v in voices if v["Locale"].lower().startswith(language.lower())]
    return voices


def list_voices(language: str | None = None) -> list[dict]:
    return asyncio.run(_list_voices(language))
