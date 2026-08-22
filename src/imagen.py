"""AI image generation via Cloudflare Workers AI (FLUX.1 schnell).

Supports a POOL of Cloudflare accounts (CLOUDFLARE_ACCOUNT_ID and
CLOUDFLARE_API_TOKEN each hold comma-separated values, paired by position).
Accounts are used in sequence: when one hits its daily quota (10k Neurons,
resets at midnight UTC), the next takes over automatically; the exhausted one
is retried after the reset. 5 accounts = ~50k Neurons/day of headroom.

Callers must always handle None (whole pool exhausted / network down) and
fall back to local art.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from .config import Config

# "no logos/lettering" matters: FLUX renders garbled fake logos when the
# prompt evokes a brand (learned the hard way on the first generated post).
_STYLE_SUFFIX = (", cinematic lighting, photorealistic, highly detailed, "
                 "vibrant neon Miami color grade, no text, no logos, "
                 "no lettering, no signage words, no watermarks")


def _pairs() -> list[tuple[str, str]]:
    ids = [v.strip() for v in re.split(r"[,;\s]+", os.getenv("CLOUDFLARE_ACCOUNT_ID", "")) if v.strip()]
    toks = [v.strip() for v in re.split(r"[,;\s]+", os.getenv("CLOUDFLARE_API_TOKEN", "")) if v.strip()]
    if ids and toks and len(ids) != len(toks):
        print(f"  [imagen] WARNING: {len(ids)} account ids vs {len(toks)} tokens — "
              f"pairing the first {min(len(ids), len(toks))} by position")
    return list(zip(ids, toks))


def _next_utc_midnight() -> float:
    now = datetime.now(timezone.utc)
    nxt = (now + timedelta(days=1)).replace(hour=0, minute=5, second=0, microsecond=0)
    return nxt.timestamp()


def _acc_id(acc: str) -> str:
    return hashlib.sha256(acc.encode()).hexdigest()[:12]


class _Pool:
    def __init__(self, cfg: Config):
        self.path = cfg.output_dir / "cloudflare_state.json"
        self.state: dict[str, dict] = {}
        if self.path.exists():
            try:
                self.state = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                self.state = {}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.state, indent=2), encoding="utf-8")

    def usable(self, acc: str) -> bool:
        st = self.state.get(_acc_id(acc), {})
        if st.get("invalid"):
            return False
        return time.time() >= st.get("cooldown_until", 0)

    def mark_quota(self, acc: str) -> None:
        # Neurons reset at midnight UTC — retry shortly after.
        self.state.setdefault(_acc_id(acc), {})["cooldown_until"] = _next_utc_midnight()
        self._save()

    def mark_invalid(self, acc: str) -> None:
        self.state.setdefault(_acc_id(acc), {})["invalid"] = True
        self._save()


def available(cfg: Config) -> bool:
    return bool(_pairs()) and bool(cfg.get("images.enabled", True))


def _call_model(acc: str, tok: str, model: str, prompt: str, steps: int):
    """One model call, handling the per-model request schema.
    Returns ('ok', bytes) | ('quota', None) | ('auth', None) | ('fail', None)."""
    url = f"https://api.cloudflare.com/client/v4/accounts/{acc}/ai/run/{model}"
    auth = {"Authorization": f"Bearer {tok}"}
    if "/flux-2" in model:
        # FLUX 2 models require multipart/form-data (they accept input images).
        r = requests.post(url, headers=auth,
                          files={"prompt": (None, prompt[:2000])}, timeout=300)
    elif "flux-1-schnell" in model:
        r = requests.post(url, headers={**auth, "Content-Type": "application/json"},
                          json={"prompt": prompt[:2000], "steps": steps}, timeout=180)
    else:  # lucid-origin, phoenix, SDXL... plain JSON prompt
        r = requests.post(url, headers={**auth, "Content-Type": "application/json"},
                          json={"prompt": prompt[:2000]}, timeout=300)
    if r.status_code == 429:
        return "quota", None
    if r.status_code in (401, 403):
        return "auth", None
    if not r.ok:
        return "fail", None
    if "json" in (r.headers.get("content-type") or ""):
        res = r.json().get("result") or {}
        img_b64 = res.get("image") or (res.get("images") or [None])[0]
        return ("ok", base64.b64decode(img_b64)) if img_b64 else ("fail", None)
    return "ok", r.content  # some models return the binary directly


def generate_image(cfg: Config, prompt: str, out_path: str | Path,
                   *, styled: bool = True) -> Path | None:
    """Generate one image: try the primary model, then the fallback model,
    rotating through the account pool on quota limits."""
    pairs = _pairs()
    if not pairs or not cfg.get("images.enabled", True):
        return None
    steps = int(cfg.get("images.steps", 6))
    models = [cfg.get("images.model", "@cf/leonardo/lucid-origin")]
    fb = cfg.get("images.fallback_model", "@cf/black-forest-labs/flux-1-schnell")
    if fb and fb not in models:
        models.append(fb)
    if styled:
        prompt = prompt.rstrip(". ") + _STYLE_SUFFIX

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pool = _Pool(cfg)

    for i, (acc, tok) in enumerate(pairs, start=1):
        if not pool.usable(acc):
            continue
        for model in models:
            try:
                status, data = _call_model(acc, tok, model, prompt, steps)
            except Exception as exc:  # noqa: BLE001
                print(f"  [imagen] account #{i} {model.split('/')[-1]} failed ({exc})")
                continue
            if status == "ok" and data:
                out_path.write_bytes(data)
                return out_path
            if status == "quota":
                print(f"  [imagen] account #{i} daily quota reached — rotating "
                      f"(resets at midnight UTC)")
                pool.mark_quota(acc)
                break  # next account
            if status == "auth":
                print(f"  [imagen] account #{i} credentials rejected — retiring")
                pool.mark_invalid(acc)
                break  # next account
            # 'fail' -> try the fallback model on the same account

    print("  [imagen] whole Cloudflare pool exhausted — falling back to local art")
    return None


def pool_status(cfg: Config) -> str:
    """Per-account status for `main.py setup`."""
    pairs = _pairs()
    if not pairs:
        return "  no Cloudflare accounts configured"
    pool = _Pool(cfg)
    lines = []
    for i, (acc, _tok) in enumerate(pairs, start=1):
        st = pool.state.get(_acc_id(acc), {})
        flag = ("INVALID" if st.get("invalid")
                else "quota until UTC midnight" if time.time() < st.get("cooldown_until", 0)
                else "ok")
        lines.append(f"  account #{i} ...{acc[-4:]}: {flag}")
    return "\n".join(lines)
