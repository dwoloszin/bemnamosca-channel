"""LLM provider layer for script/SEO generation.

Priority (script.provider: "auto"):
  1. Claude  — if ANTHROPIC_API_KEY is set (best quality)
  2. Gemini  — GEMINI_API_KEYS pool (strong writing, generous free tier)
  3. Groq    — GROQ_API_KEY(S) pool (fast Llama models)
  4. None    — callers fall back to templates

All pooled tiers rotate keys automatically: a rate-limited key gets a 1-hour
cooldown (state kept in output/*_state.json) and the next key takes over; when
a whole tier is down, the next tier is used.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path

import requests

from .config import Config

GROQ_API = "https://api.groq.com/openai/v1/chat/completions"
_COOLDOWN_S = 3600


def _key_id(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()[:12]


class GroqLLM:
    def __init__(self, keys: list[str], model: str, state_path: Path):
        self.keys = keys
        self.model = model
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

    def _usable(self) -> list[str]:
        now = time.time()
        out = []
        for k in self.keys:
            st = self.state.get(_key_id(k), {})
            if st.get("invalid"):
                continue
            if now < st.get("cooldown_until", 0):
                continue
            out.append(k)
        return out

    def ask(self, prompt: str) -> str | None:
        usable = self._usable()
        if not usable:
            print("  [groq] all keys cooling down or invalid")
            return None
        for key in usable:
            for use_json_mode in (True, False):
                try:
                    body = {
                        "model": self.model,
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0.7,
                        "max_tokens": 4096,
                    }
                    if use_json_mode:
                        body["response_format"] = {"type": "json_object"}
                    r = requests.post(
                        GROQ_API,
                        headers={"Authorization": f"Bearer {key}",
                                 "Content-Type": "application/json"},
                        json=body, timeout=60,
                    )
                    if r.status_code in (401, 403):
                        print(f"  [groq] key ...{key[-4:]} invalid — retiring")
                        self.state.setdefault(_key_id(key), {})["invalid"] = True
                        self._save()
                        break  # next key
                    if r.status_code == 429:
                        print(f"  [groq] key ...{key[-4:]} rate-limited — 1h cooldown, rotating")
                        self.state.setdefault(_key_id(key), {})["cooldown_until"] = \
                            time.time() + _COOLDOWN_S
                        self._save()
                        break  # next key
                    if r.status_code == 400 and use_json_mode:
                        continue  # retry same key without json mode
                    r.raise_for_status()
                    return r.json()["choices"][0]["message"]["content"]
                except requests.RequestException as exc:
                    print(f"  [groq] key ...{key[-4:]} failed ({exc}) — trying next")
                    break  # next key
        return None


class GeminiLLM:
    """Google Gemini via API-key pool with rotation on rate limits."""

    def __init__(self, keys: list[str], model: str, state_path: Path):
        self.keys = keys
        self.model = model
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

    def ask(self, prompt: str) -> str | None:
        now = time.time()
        for key in self.keys:
            st = self.state.get(_key_id(key), {})
            if st.get("invalid") or now < st.get("cooldown_until", 0):
                continue
            try:
                r = requests.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent",
                    headers={"x-goog-api-key": key, "Content-Type": "application/json"},
                    json={
                        "contents": [{"parts": [{"text": prompt}]}],
                        "generationConfig": {"temperature": 0.7, "maxOutputTokens": 16384,
                                              "responseMimeType": "application/json"},
                    },
                    timeout=60,
                )
                if r.status_code in (400, 401, 403) and "api key" in r.text.lower():
                    print(f"  [gemini] key ...{key[-4:]} invalid — retiring")
                    self.state.setdefault(_key_id(key), {})["invalid"] = True
                    self._save()
                    continue
                if r.status_code == 429:
                    print(f"  [gemini] key ...{key[-4:]} rate-limited — 1h cooldown")
                    self.state.setdefault(_key_id(key), {})["cooldown_until"] = \
                        time.time() + _COOLDOWN_S
                    self._save()
                    continue
                r.raise_for_status()
                cands = r.json().get("candidates") or []
                parts = ((cands[0].get("content") or {}).get("parts") or []) if cands else []
                text = "".join(p.get("text", "") for p in parts)
                if text.strip():
                    return text
            except requests.RequestException as exc:
                print(f"  [gemini] key ...{key[-4:]} failed ({exc}) — trying next")
                continue
        return None


class ClaudeLLM:
    def __init__(self, api_key: str, model: str):
        import anthropic
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model

    def ask(self, prompt: str) -> str | None:
        try:
            resp = self.client.messages.create(
                model=self.model,
                max_tokens=2048,
                messages=[{"role": "user", "content": prompt}],
            )
            if getattr(resp, "stop_reason", None) == "refusal":
                return None
            return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        except Exception as exc:  # noqa: BLE001
            print(f"  [claude] call failed: {exc}")
            return None


def _groq_keys() -> list[str]:
    raw = os.getenv("GROQ_API_KEYS", "") or os.getenv("GROQ_API_KEY", "")
    return [k.strip() for k in re.split(r"[,;\s]+", raw) if k.strip().startswith("gsk_")]


def _gemini_keys() -> list[str]:
    raw = os.getenv("GEMINI_API_KEYS", "") or os.getenv("GEMINI_API_KEY", "")
    return [k.strip() for k in re.split(r"[,;\s]+", raw) if k.strip()]


class _ChainLLM:
    """Try each tier in order; remember which one answered last (for logs)."""

    def __init__(self, tiers: list):
        self.tiers = tiers

    def ask(self, prompt: str) -> str | None:
        for name, llm in self.tiers:
            text = llm.ask(prompt)
            if text:
                return text
            print(f"  [llm] {name} gave no answer — trying next tier")
        return None


def get_llm(cfg: Config):
    """Return an object with .ask(prompt)->str|None, or None (use templates)."""
    provider = cfg.get("script.provider", "auto")
    tiers: list = []

    if provider in ("auto", "claude") and cfg.anthropic_key:
        try:
            tiers.append(("claude", ClaudeLLM(cfg.anthropic_key,
                                              cfg.get("script.model", "claude-opus-4-8"))))
        except Exception as exc:  # noqa: BLE001
            print(f"  [llm] Claude unavailable: {exc}")

    if provider in ("auto", "gemini"):
        keys = _gemini_keys()
        if keys:
            tiers.append(("gemini", GeminiLLM(keys,
                                              cfg.get("script.gemini_model", "gemini-2.5-flash"),
                                              cfg.output_dir / "gemini_state.json")))

    if provider in ("auto", "groq"):
        keys = _groq_keys()
        if keys:
            tiers.append(("groq", GroqLLM(keys,
                                          cfg.get("script.groq_model", "llama-3.3-70b-versatile"),
                                          cfg.output_dir / "groq_state.json")))

    if not tiers:
        return None
    if len(tiers) == 1:
        return tiers[0][1]
    return _ChainLLM(tiers)
