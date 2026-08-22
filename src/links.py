"""Links that can be measured.

Every surface published the same bare URL, so the app's analytics could not
tell a visitor from YouTube apart from one from Instagram — which made every
content decision a guess. Each surface now gets its own UTM-tagged link.

The social links stay untagged: they point at profiles, not at the product,
and the only link whose origin matters is the one to the app.
"""
from __future__ import annotations

from urllib.parse import urlencode, urlparse, urlunparse, parse_qsl

from .config import Config

# Emoji labels for the social block. The app link is rendered separately,
# above and apart, because in a YouTube description the FIRST link takes
# almost every click and four social links right under it only dilute.
_LABELS = {"youtube": "▶️ YouTube", "instagram": "📸 Instagram",
           "tiktok": "🎵 TikTok", "linkedin": "💼 LinkedIn"}


def app_url(cfg: Config, source: str, *, content: str | None = None) -> str:
    """The app link, tagged with where it was published.

    `source` is the platform (youtube / instagram / linkedin / tiktok);
    `content` optionally distinguishes the format (short, carousel, reel).
    """
    base = str(cfg.get("channel.links.site")
               or cfg.get("channel.site") or "").strip()
    if not base or not cfg.get("channel.tracking.enabled", True):
        return base

    parts = urlparse(base)
    query = dict(parse_qsl(parts.query))
    query.update({
        "utm_source": source,
        "utm_medium": str(cfg.get("channel.tracking.medium", "social")),
        "utm_campaign": str(cfg.get("channel.tracking.campaign", "conteudo")),
    })
    if content:
        query["utm_content"] = content
    return urlunparse(parts._replace(query=urlencode(query)))


def social_block(cfg: Config) -> str:
    """The other profiles, one per line. No app link here — see app_url."""
    links = cfg.get("channel.links", {}) or {}
    if not isinstance(links, dict):
        return ""
    return "\n".join(f"{_LABELS.get(k, k.title())}: {v}"
                     for k, v in links.items()
                     if k != "site" and str(v or "").strip())


def cta_block(cfg: Config, source: str, *, content: str | None = None) -> str:
    """The call to action: one concrete reason, then the tracked link.

    The old close was "Compare antes de comprar" — true, but it gives nobody a
    reason to act today. product.cta_reason carries the specific one.
    """
    reason = str(cfg.get("product.cta_reason", "") or "").strip()
    url = app_url(cfg, source, content=content)
    lines = [l for l in (reason, f"👉 {url}" if url else "") if l]
    return "\n".join(lines)
