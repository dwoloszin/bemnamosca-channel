"""Community signals: what fans are upvoting/discussing this week.

Primary: Reddit's public JSON endpoints (free, no key). Reddit sometimes blocks
non-browser clients (403), so the fallback searches Google for recent Reddit
threads via the Serper key pool. Either way, only REAL posts are used.
"""
from __future__ import annotations

import time

import requests

from .config import Config
from .news import NewsItem, _strip

# Reddit blocks obvious script UAs from some networks; a browser UA works.
_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"),
    "Accept": "application/json",
}


def _fetch_subreddit(sub: str, limit: int) -> list[NewsItem]:
    r = requests.get(
        f"https://www.reddit.com/r/{sub}/top.json",
        params={"t": "week", "limit": limit},
        headers=_HEADERS, timeout=20,
    )
    r.raise_for_status()
    items: list[NewsItem] = []
    for child in r.json().get("data", {}).get("children", []):
        d = child.get("data", {})
        title = _strip(d.get("title", ""))
        if not title or d.get("stickied") or d.get("over_18"):
            continue
        items.append(NewsItem(
            title=title,
            summary=_strip(d.get("selftext", ""))[:300],
            link="https://www.reddit.com" + d.get("permalink", ""),
            source=f"r/{sub} ({d.get('score', 0):,} upvotes)",
            published=float(d.get("created_utc", time.time())),
            keyword="",
        ))
    return items


def _serper_reddit_fallback(cfg: Config, subs: list[str]) -> list[NewsItem]:
    """Find recent Reddit threads through Google (Serper organic search)."""
    from .search_news import _State, _env_keys
    topic = cfg.get("theme.topic", "")
    state = _State(cfg.output_dir / "search_keys_state.json")
    keys = [k for k in _env_keys("SERPER_API_KEYS") if state.usable(k)]
    if not keys:
        return []
    items: list[NewsItem] = []
    for sub in subs[:2]:  # keep credit usage modest
        try:
            r = requests.post(
                "https://google.serper.dev/search",
                headers={"X-API-KEY": keys[0], "Content-Type": "application/json"},
                json={"q": f"site:reddit.com/r/{sub} {topic}", "num": 10, "tbs": "qdr:w"},
                timeout=30,
            )
            if r.status_code in (403, 429):
                state.mark(keys[0])
                break
            r.raise_for_status()
            for res in r.json().get("organic", []):
                title = _strip(res.get("title", "")).removesuffix(" : r/" + sub)
                if not title:
                    continue
                items.append(NewsItem(
                    title=title,
                    summary=_strip(res.get("snippet", "")),
                    link=res.get("link", ""),
                    source=f"r/{sub}",
                    published=time.time() - 86400,  # "past week" filter; assume fresh
                    keyword="",
                ))
        except Exception as exc:  # noqa: BLE001
            print(f"  [community] serper fallback for r/{sub} failed: {exc}")
    return items


def fetch_community(cfg: Config, limit_per_sub: int = 15) -> list[NewsItem]:
    subs = cfg.get("theme.subreddits", []) or []
    items: list[NewsItem] = []
    blocked = False
    for sub in subs:
        try:
            items.extend(_fetch_subreddit(sub, limit_per_sub))
        except Exception as exc:  # noqa: BLE001
            print(f"  [community] r/{sub} failed: {exc}")
            blocked = True
    if blocked and not items:
        print("  [community] Reddit blocked — falling back to Google-indexed threads")
        items = _serper_reddit_fallback(cfg, subs)
    items.sort(key=lambda i: i.published, reverse=True)
    return items
