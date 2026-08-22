"""Google News search via Serper.dev, with SerpAPI as the reserve tier.

Key strategy (as configured in .env):
  1. SERPER_API_KEYS  — pool of keys, ~2500 credits each, 1 credit/search.
  2. SERPAPI_API_KEYS — used only after every Serper key is exhausted.

Exhausted/invalid keys are remembered in output/search_keys_state.json and
skipped for 30 days. All results are REAL indexed news (title/snippet/link) —
nothing is generated.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path

import requests

from .config import Config
from .news import NewsItem, _strip  # reuse the HTML-stripping helper

_EXHAUST_DAYS = 30
_RATELIMIT_SECONDS = 900   # 15 min — a burst of searches, NOT a dead key


def _key_id(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()[:12]


class _State:
    def __init__(self, path: Path):
        self.path = path
        self.data: dict[str, dict] = {}
        if path.exists():
            try:
                self.data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                self.data = {}

    def usable(self, key: str) -> bool:
        st = self.data.get(_key_id(key), {})
        if st.get("invalid"):
            return False
        if time.time() < float(st.get("cooldown_until", 0)):
            return False
        ex = st.get("exhausted_at")
        return not (ex and time.time() - ex < _EXHAUST_DAYS * 86400)

    def mark(self, key: str, *, invalid: bool = False, ratelimited: bool = False) -> None:
        """invalid: dead key (permanent). ratelimited: HTTP 429, a temporary
        burst limit — only a short cooldown, NEVER the 30-day exhaustion path
        (that once blocked every key for weeks after one busy run)."""
        st = self.data.setdefault(_key_id(key), {})
        if invalid:
            st["invalid"] = True
        elif ratelimited:
            st["cooldown_until"] = time.time() + _RATELIMIT_SECONDS
        else:
            st["exhausted_at"] = time.time()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=2), encoding="utf-8")


def _env_keys(name: str) -> list[str]:
    import os
    raw = os.getenv(name, "")
    return [k.strip() for k in re.split(r"[,;\s]+", raw) if k.strip()]


def _parse_relative_date(s: str) -> float:
    """'3 hours ago' / '2 days ago' / ISO-ish dates -> epoch (best effort)."""
    s = (s or "").strip().lower()
    now = time.time()
    m = re.match(r"(\d+)\s+(minute|hour|day|week|month)s?\s+ago", s)
    if m:
        n = int(m.group(1))
        unit = {"minute": 60, "hour": 3600, "day": 86400,
                "week": 604800, "month": 2592000}[m.group(2)]
        return now - n * unit
    for fmt in ("%m/%d/%Y, %I:%M %p", "%Y-%m-%d", "%b %d, %Y"):
        try:
            import datetime as dt
            return dt.datetime.strptime(s[:20].strip(", +0000 utc"), fmt).timestamp()
        except Exception:  # noqa: BLE001
            continue
    return now - 43200  # unknown -> assume ~half a day old


# --------------------------------------------------------------------------
# providers
# --------------------------------------------------------------------------
def _serper_search(key: str, query: str, num: int = 10,
                   locale: dict | None = None) -> list[dict] | str:
    """Returns result list, or 'exhausted'/'invalid'/'ratelimited' sentinels.

    `locale` carries gl/hl (country/language) — without it Google answers in
    English, which returns almost nothing for a non-English channel.
    """
    try:
        body = {"q": query, "num": num, "tbs": "qdr:w"}
        body.update(locale or {})
        r = requests.post(
            "https://google.serper.dev/news",
            headers={"X-API-KEY": key, "Content-Type": "application/json"},
            # tbs=qdr:w -> only results from the past week (keeps stories fresh)
            json=body,
            timeout=30,
        )
        if r.status_code in (401,):
            return "invalid"
        # 429 is a per-minute burst limit — the key is fine, just wait.
        if r.status_code == 429:
            return "ratelimited"
        # Out of credits is an ERROR status, never a 200. The old test also
        # scanned the body for "credit", which silently retired healthy keys:
        # a query with zero news results returns a ~200-char success body whose
        # trailing "credits":1 field lands inside that window. Every empty
        # search burned one key, so the pool drained by itself.
        if r.status_code in (402, 403):
            return "exhausted"
        if not r.ok and "credit" in r.text.lower()[:300]:
            return "exhausted"
        r.raise_for_status()
        return r.json().get("news", [])
    except requests.RequestException:
        return []


def _serpapi_search(key: str, query: str, num: int = 10,
                    locale: dict | None = None) -> list[dict] | str:
    try:
        params = {"engine": "google_news", "q": query, "api_key": key, "num": num}
        params.update(locale or {})
        r = requests.get(
            "https://serpapi.com/search.json",
            params=params,
            timeout=30,
        )
        if r.status_code == 401:
            return "invalid"
        if "run out of searches" in r.text.lower()[:500]:
            return "exhausted"
        if r.status_code == 429:
            return "ratelimited"
        r.raise_for_status()
        return r.json().get("news_results", [])
    except requests.RequestException:
        return []


def locale_of(cfg: Config) -> dict:
    """Google gl/hl for the channel's language, so a non-English channel gets
    results from its own country/language. Overridable via search.gl/search.hl."""
    lang = str(cfg.get("channel.language", "en") or "en").lower()
    defaults = {
        "pt": ("br", "pt-br"), "pt-br": ("br", "pt-br"),
        "es": ("es", "es"), "fr": ("fr", "fr"),
        "de": ("de", "de"), "it": ("it", "it"), "en": ("us", "en"),
    }
    gl, hl = defaults.get(lang, ("us", "en"))
    return {"gl": cfg.get("search.gl", gl), "hl": cfg.get("search.hl", hl)}


def search_news(cfg: Config, queries: list[str], per_query: int = 10) -> list[NewsItem]:
    """Search all queries through the key tiers. Returns real news items."""
    state = _State(cfg.output_dir / "search_keys_state.json")
    locale = locale_of(cfg)
    tiers = [
        ("serper", _env_keys("SERPER_API_KEYS"), _serper_search),
        ("serpapi", _env_keys("SERPAPI_API_KEYS"), _serpapi_search),
    ]

    items: list[NewsItem] = []
    for query in queries:
        results = None
        for tier_name, keys, fn in tiers:
            for key in keys:
                if not state.usable(key):
                    continue
                res = fn(key, query, per_query, locale)
                if res == "invalid":
                    print(f"  [search] {tier_name} key ...{key[-4:]} invalid — retiring")
                    state.mark(key, invalid=True)
                    continue
                if res == "ratelimited":
                    print(f"  [search] {tier_name} key ...{key[-4:]} rate-limited — "
                          f"pausing it for 15 min, rotating")
                    state.mark(key, ratelimited=True)
                    continue
                if res == "exhausted":
                    print(f"  [search] {tier_name} key ...{key[-4:]} out of credits — rotating")
                    state.mark(key)
                    continue
                results = res
                break
            if results is not None:
                break
        if not results:
            continue

        for r in results:
            title = _strip(r.get("title", ""))
            link = r.get("link", "")
            if not title or not link:
                continue
            items.append(NewsItem(
                title=title,
                summary=_strip(r.get("snippet", "") or r.get("description", "")),
                link=link,
                source=_strip(r.get("source", "") if isinstance(r.get("source"), str)
                              else (r.get("source") or {}).get("name", "")) or "Google News",
                published=_parse_relative_date(str(r.get("date", ""))),
                keyword=query,
            ))
    return items
