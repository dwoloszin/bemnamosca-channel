"""Fetch news headlines about the configured theme.

Copyright-safe by design: we read public RSS feeds (headlines + short summaries
+ links) and DO NOT copy full articles or download article images unless the
user explicitly opts in via config (media.use_article_images).
"""
from __future__ import annotations

import html
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import feedparser
import requests

from .config import Config

_TAG_RE = re.compile(r"<[^>]+>")


@dataclass
class NewsItem:
    title: str
    summary: str
    link: str
    source: str
    published: float = 0.0  # epoch seconds
    image_url: str | None = None
    keyword: str = ""

    @property
    def clean_title(self) -> str:
        return _strip(self.title)

    @property
    def clean_summary(self) -> str:
        return _strip(self.summary)


def _strip(text: str) -> str:
    text = html.unescape(text or "")
    text = _TAG_RE.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def _matches(item_title: str, must_match: list[str], block: list[str]) -> bool:
    t = item_title.lower()
    if block and any(b.lower() in t for b in block):
        return False
    if must_match and not any(m.lower() in t for m in must_match):
        return False
    return True


def fetch_news(cfg: Config, limit: int = 20) -> list[NewsItem]:
    """Return de-duplicated, recency-sorted news items for the theme.

    Sources, in priority order:
      1. Google News search (Serper -> SerpAPI key pools) with the curiosity/
         leak/feature queries from theme.search_queries — real indexed news.
      2. RSS feeds (free, keyless).
      3. NewsAPI (optional).
    """
    feeds = cfg.get("theme.rss_feeds", []) or []
    must_match = cfg.get("theme.must_match_any", []) or []
    block = cfg.get("theme.block_words", []) or []

    seen: set[str] = set()
    items: list[NewsItem] = []

    # 1) search tier — fresh, attention-grabbing angles (leaks, features...)
    queries = cfg.get("theme.search_queries", []) or []
    if queries:
        from .search_news import search_news
        for it in search_news(cfg, queries):
            if not _matches(it.title, must_match, block):
                continue
            key = re.sub(r"[^a-z0-9]", "", it.clean_title.lower())[:60]
            if key in seen:
                continue
            seen.add(key)
            items.append(it)

    for feed_url in feeds:
        try:
            parsed = feedparser.parse(feed_url)
        except Exception as exc:  # noqa: BLE001
            print(f"  [news] feed failed: {feed_url} ({exc})")
            continue
        source = _strip(getattr(parsed.feed, "title", "")) or feed_url
        for entry in parsed.entries:
            title = _strip(getattr(entry, "title", ""))
            if not title or not _matches(title, must_match, block):
                continue
            key = re.sub(r"[^a-z0-9]", "", title.lower())[:60]
            if key in seen:
                continue
            seen.add(key)

            published = 0.0
            for attr in ("published_parsed", "updated_parsed"):
                tp = getattr(entry, attr, None)
                if tp:
                    published = time.mktime(tp)
                    break

            items.append(
                NewsItem(
                    title=title,
                    summary=_strip(getattr(entry, "summary", "")),
                    link=getattr(entry, "link", ""),
                    source=source,
                    published=published,
                    image_url=_entry_image(entry) if cfg.get("media.use_article_images") else None,
                )
            )

    # NewsAPI is an optional extra source.
    if cfg.newsapi_key:
        items.extend(_fetch_newsapi(cfg, block, must_match, seen))

    items.sort(key=lambda i: i.published, reverse=True)
    return items[:limit]


def _entry_image(entry) -> str | None:
    media = getattr(entry, "media_content", None) or getattr(entry, "media_thumbnail", None)
    if media:
        try:
            return media[0].get("url")
        except Exception:  # noqa: BLE001
            return None
    for link in getattr(entry, "links", []):
        if link.get("type", "").startswith("image"):
            return link.get("href")
    return None


def _fetch_newsapi(cfg: Config, block, must_match, seen) -> list[NewsItem]:
    out: list[NewsItem] = []
    query = " OR ".join(f'"{k}"' for k in cfg.get("theme.keywords", [])[:5])
    try:
        resp = requests.get(
            "https://newsapi.org/v2/everything",
            params={
                "q": query,
                "language": cfg.get("channel.language", "en"),
                "sortBy": "publishedAt",
                "pageSize": 20,
            },
            headers={"X-Api-Key": cfg.newsapi_key},
            timeout=20,
        )
        resp.raise_for_status()
        for a in resp.json().get("articles", []):
            title = _strip(a.get("title", ""))
            if not title or not _matches(title, must_match, block):
                continue
            key = re.sub(r"[^a-z0-9]", "", title.lower())[:60]
            if key in seen:
                continue
            seen.add(key)
            published = 0.0
            if a.get("publishedAt"):
                try:
                    published = datetime.fromisoformat(
                        a["publishedAt"].replace("Z", "+00:00")
                    ).replace(tzinfo=timezone.utc).timestamp()
                except Exception:  # noqa: BLE001
                    pass
            out.append(
                NewsItem(
                    title=title,
                    summary=_strip(a.get("description", "")),
                    link=a.get("url", ""),
                    source=_strip((a.get("source") or {}).get("name", "NewsAPI")),
                    published=published,
                    image_url=a.get("urlToImage") if cfg.get("media.use_article_images") else None,
                )
            )
    except Exception as exc:  # noqa: BLE001
        print(f"  [news] NewsAPI failed: {exc}")
    return out
