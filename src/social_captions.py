"""Platform-specific captions for MANUAL posting (TikTok today).

Why TikTok gets its own text instead of post.txt: several posts were locked
for "community guidelines violation", and post.txt carries everything TikTok's
moderation dislikes —
  * external links in the caption (the UTM'd site plus four profile links),
  * hard-sell wording ("Assine agora"),
  * hashtags of weight-loss drug BRANDS (#ozempic, #ozivy): promoting these
    drugs is an actively enforced policy area; a branded hashtag makes a news
    clip read as an ad for the drug.

So the TikTok caption is: short, factual, no URL at all, a question to drive
comments, the marketplace seal, and only SAFE hashtags (never a drug brand,
never "emagrecer"). Deterministic template — moderation is exactly where we do
not want creative variance from an LLM.
"""
from __future__ import annotations

import re

from .config import Config

# Drug brands and diet-promo terms that must never become hashtags on TikTok.
_TIKTOK_TAG_BLOCKLIST = {
    "ozempic", "mounjaro", "wegovy", "saxenda", "zepbound", "ozivy", "rybelsus",
    "victoza", "trulicity", "emagrecedor", "emagrecimento", "emagrecer",
    "pilula", "injecao", "injetavel",
    # active ingredients: fine in the TEXT of a news caption, but as a hashtag
    # they put the clip in the drug-promotion bucket
    "semaglutida", "tirzepatida", "liraglutida",
}

_SAFE_BASE_TAGS = ["noticia", "saude", "farmacia", "economia", "brasil", "dicas"]


def _clean_tag(tag: str) -> str:
    return re.sub(r"[^a-z0-9à-ÿ]", "", str(tag).lower())


def build_tiktok_caption(cfg: Config, headline: str, fact: str = "",
                         question: str = "", tags: list[str] | None = None) -> str:
    """~300 chars: headline + one factual line + question + safe hashtags."""
    head = headline.strip().rstrip(".")
    parts = [head + ("" if head.endswith(("?", "!", "…")) else ".")]
    if fact.strip():
        parts.append(fact.strip())
    parts.append(question.strip() or "Você já pesquisou o preço antes de comprar?")
    parts.append("Buscador de preços — a compra é feita na farmácia.")
    body = "\n\n".join(parts)

    picked: list[str] = []
    for t in (tags or []) + _SAFE_BASE_TAGS:
        c = _clean_tag(t)
        if not c or c in _TIKTOK_TAG_BLOCKLIST or any(b in c for b in _TIKTOK_TAG_BLOCKLIST):
            continue
        if c not in picked:
            picked.append(c)
        if len(picked) >= 6:
            break
    return (body + "\n\n" + " ".join(f"#{t}" for t in picked)).strip()[:2200]
