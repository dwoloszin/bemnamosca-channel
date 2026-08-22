"""What people actually type — from Google's autocomplete.

The channel's own keywords are how WE describe the subject ("medicamento de
alto custo"). Autocomplete shows how the audience describes it, and in this
niche that difference is concrete: the seeds return real drug names — forxiga,
tamiflu, pyridium, pregabalina — because those are what somebody with a
prescription actually searches for.

That list is used to RANK the day's news: when a story mentions something
people are already looking up, it goes first.

Why only autocomplete, out of the four sources considered (16/08/2026):

  Google autocomplete   free, no key, instant            -> used
  YouTube autocomplete  works, but this niche has almost no volume there
                        ("medicamento alto custo" suggests only itself)
  Serper related/PAA    the plan's response carries only `organic`
  Google Trends         the internal endpoint answered 429 on the first call;
                        the public RSS is national and off-topic
  Keyword Planner       needs an approved Google Ads developer token

No new key, no fragile dependency.
"""
from __future__ import annotations

import json
import re
import time
import unicodedata
from pathlib import Path

import requests

from .config import Config

_ENDPOINT = "https://suggestqueries.google.com/complete/search"

# Words that carry no demand signal: the seeds themselves are added at runtime.
_STOP = {
    "de", "da", "do", "das", "dos", "e", "o", "a", "os", "as", "um", "uma",
    "em", "no", "na", "para", "por", "com", "que", "mais", "menos", "onde",
    "quanto", "custa", "preco", "precos", "qual", "quais", "como", "mg", "ml",
    "comprar", "barato", "barata", "generico", "remedio", "remedios",
    "medicamento", "medicamentos", "farmacia", "farmacias", "valor", "tem",
}


def _fold(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", (text or "").lower())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def suggestions(query: str, *, hl: str = "pt-BR", gl: str = "br",
                timeout: int = 15) -> list[str]:
    """Google's autocomplete for one query. [] on any failure — this is a
    ranking hint, never a reason to fail the day's run."""
    try:
        r = requests.get(_ENDPOINT, timeout=timeout, params={
            "client": "firefox", "q": query, "hl": hl, "gl": gl})
        if not r.ok:
            return []
        return [str(s) for s in json.loads(r.text)[1]]
    except Exception:  # noqa: BLE001
        return []


def _cache_path(cfg: Config) -> Path:
    return cfg.output_dir / "keyword_cache.json"


def demand_terms(cfg: Config, *, max_age_hours: float = 20.0) -> list[str]:
    """Terms people are typing around this channel's subject, most frequent
    first. Cached, because the answer barely moves within a day and the daily
    routine would otherwise re-query on every run."""
    if not cfg.get("keywords.enabled", True):
        return []

    path = _cache_path(cfg)
    if path.exists():
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
            if time.time() - float(d.get("at", 0)) < max_age_hours * 3600:
                return list(d.get("terms", []))
        except Exception:  # noqa: BLE001
            pass

    seeds = list(cfg.get("theme.keywords", []) or [])
    patterns = cfg.get("keywords.patterns", [
        "{seed}", "preço {seed}", "quanto custa {seed}", "{seed} mais barato",
    ]) or ["{seed}"]

    seed_words = {w for s in seeds for w in _fold(s).split()}
    # Autocomplete surfaces what people type, and that includes competitor
    # pharmacy names and neighbourhood names. The brand doc forbids naming a
    # real pharmacy, and "farmacia perto de mim" is useless for national
    # content, so both are dropped before anything downstream sees them.
    blocked = {_fold(str(b)) for b in (cfg.get("keywords.block_terms", []) or [])}
    counts: dict[str, int] = {}
    queries = 0
    for seed in seeds[:6]:
        for pat in patterns:
            queries += 1
            for sug in suggestions(str(pat).format(seed=seed)):
                for w in re.findall(r"[a-zà-ÿ0-9]{4,}", _fold(sug)):
                    if (w in _STOP or w in seed_words or w in blocked
                            or w.isdigit()):
                        continue
                    counts[w] = counts.get(w, 0) + 1

    terms = [w for w, _ in sorted(counts.items(), key=lambda kv: -kv[1])][:60]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"at": time.time(), "queries": queries,
                                "terms": terms}, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    print(f"  [keywords] {len(terms)} termos em demanda ({queries} consultas)")
    return terms


def rank_items(cfg: Config, items: list) -> list:
    """Order news items by how much they overlap with what people search for.

    Stable: items with no overlap keep their original order behind the ones
    that match, so a quiet day still produces the same list as before.
    """
    terms = demand_terms(cfg)
    if not terms:
        return items
    weight = {t: len(terms) - i for i, t in enumerate(terms)}

    def score(it) -> int:
        text = _fold(f"{it.clean_title} {it.clean_summary}")
        return sum(w for t, w in weight.items() if t in text)

    scored = [(score(it), -i, it) for i, it in enumerate(items)]
    scored.sort(key=lambda x: (-x[0], -x[1]))
    top = scored[0]
    if top[0] > 0:
        hits = [t for t in terms
                if t in _fold(f"{top[2].clean_title} {top[2].clean_summary}")]
        print(f"  [keywords] prioridade: {', '.join(hits[:4])}")
    return [it for _, _, it in scored]
