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

# Story-tag vocabulary: (tag, trigger regexes matched on the FOLDED story
# text). Deterministic on purpose, like everything else in this module — the
# LLM writes the story, not the hashtags. Order = priority within one text.
# TikTok's blocklist still filters whatever comes out of here (a story about
# Ozempic yields "ozempic", which build_tiktok_caption drops and the LinkedIn
# texts keep — exactly the per-platform behaviour we want).
_STORY_TAG_PATTERNS: list[tuple[str, tuple[str, ...]]] = [
    # reguladores e programas
    ("anvisa", (r"\banvisa\b",)),
    ("sus", (r"\bsus\b",)),
    ("farmaciapopular", (r"farmacia popular",)),
    ("planodesaude", (r"plano de saude", r"\bans\b")),
    # produtos e categorias
    ("genericos", (r"\bgeneric",)),
    ("creatina", (r"\bcreatina",)),
    ("wheyprotein", (r"\bwhey\b",)),
    ("suplementos", (r"\bsuplement",)),
    ("vitaminas", (r"\bvitamina", r"polivitamin")),
    ("colageno", (r"\bcolageno",)),
    ("protetorsolar", (r"protetor solar", r"filtro solar")),
    ("skincare", (r"\bskincare\b", r"dermocosm", r"anti-?idade", r"antirrugas")),
    ("cabelo", (r"\bcapilar\b", r"queda de cabelo", r"\bshampoo\b")),
    ("insulina", (r"\binsulina",)),
    ("antibioticos", (r"\bantibiotic",)),
    ("vacina", (r"\bvacina",)),
    # marcas/principios em alta (o TikTok filtra; LinkedIn e IG aproveitam)
    ("ozempic", (r"\bozempic\b",)),
    ("mounjaro", (r"\bmounjaro\b",)),
    ("wegovy", (r"\bwegovy\b",)),
    ("semaglutida", (r"\bsemaglutida\b",)),
    ("tirzepatida", (r"\btirzepatida\b",)),
    # condicoes de saude
    ("diabetes", (r"\bdiabet",)),
    ("cancer", (r"\bcancer\b", r"\boncolog", r"quimioterapia")),
    ("obesidade", (r"\bobesidade\b",)),
    ("emagrecimento", (r"\bemagre",)),
    ("hipertensao", (r"\bhipertens", r"pressao alta")),
    ("colesterol", (r"\bcolesterol\b",)),
    ("saudemental", (r"\bdepress", r"\bansiedade\b", r"\bantidepressiv")),
    ("alzheimer", (r"\balzheimer\b",)),
    ("dengue", (r"\bdengue\b",)),
    ("gripe", (r"\bgripe\b", r"\binfluenza\b")),
    # treino (pauta de suplemento costuma citar)
    ("treino", (r"\btreino\b", r"\bmusculacao\b", r"\bacademia\b")),
]


def _fold(s: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", str(s).lower())
                   if not unicodedata.combining(c))


def story_hashtags(headline: str, body: str = "", max_n: int = 6) -> list[str]:
    """Tags the STORY itself earns, headline matches first. Lowercase slugs,
    no '#'. Empty when nothing in the vocabulary appears — callers top up
    with their own static pools, so a day without matches degrades to the
    old behaviour instead of inventing noise."""
    picked: list[str] = []
    for text in (_fold(headline), _fold(body)):
        for tag, pats in _STORY_TAG_PATTERNS:
            if tag not in picked and any(re.search(p, text) for p in pats):
                picked.append(tag)
                if len(picked) >= max_n:
                    return picked
    return picked


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


def build_linkedin_page_caption(cfg: Config, headline: str, fact: str = "",
                                question: str = "", tags: list[str] | None = None) -> str:
    """Company-page post for the day's VIDEO. Deliberately different from
    linkedin.txt (the personal-profile text): if page and profile post the
    same words on the same day, LinkedIn suppresses one of them. Short, no
    URL in the body (external links are down-ranked), a question for the
    comments, and a few hashtags — drug/ingredient tags are fine here, this
    is not TikTok's moderation."""
    head = headline.strip().rstrip(".")
    parts = [head + ("" if head.endswith(("?", "!", "…")) else ".")]
    if fact.strip():
        parts.append(fact.strip())
    parts.append(question.strip() or "Você compara o preço antes de comprar?")
    picked: list[str] = []
    for t in (tags or []) + ["medicamentos", "farmacia", "saude", "economia"]:
        c = _clean_tag(t)
        if c and c not in picked:
            picked.append(c)
        if len(picked) >= 4:
            break
    body = "\n\n".join(parts) + "\n\n" + " ".join(f"#{t}" for t in picked)
    return body.strip()[:1800]
