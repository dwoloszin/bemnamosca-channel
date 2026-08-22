"""Product/brand context injected into script prompts.

This channel is not a news channel that happens to have a sponsor: the news is
the HOOK and the product is the CLOSE. Every script opens on a real story about
health/economy/medicine and lands on one concrete thing the app does.

Two things live here:

  product_block(cfg)  — WHAT the product is (loaded from the curated docs)
  bridge_block(cfg)   — HOW to pivot from the story to the product, honestly

Safety by construction: only files inside `product.docs_dir` are ever read, and
that directory is `assets/docs/conteudo` — the tier cleared for public use. The
engineering docs in `assets/docs/interno` (Firestore internals, per-user
analytics, billing incidents) must never reach a prompt, so the loader refuses
any path that resolves outside the configured directory.
"""
from __future__ import annotations

import re
from pathlib import Path

from .config import Config


def _docs_dir(cfg: Config) -> Path:
    return cfg.path_of(cfg.get("product.docs_dir", "assets/docs/conteudo"))


def load_docs(cfg: Config) -> str:
    """Concatenate the allow-listed brand docs, bounded by product.max_chars.

    Returns "" when disabled, missing or empty — callers degrade to the plain
    news script rather than failing the run.
    """
    if not cfg.get("product.enabled", False):
        return ""
    base = _docs_dir(cfg).resolve()
    if not base.is_dir():
        print(f"  [product] docs dir not found: {base} — skipping product context")
        return ""

    names = cfg.get("product.files", ["BRIEF_DE_MARCA.md"]) or []
    budget = int(cfg.get("product.max_chars", 7000))
    parts: list[str] = []
    spent = 0
    for name in names:
        path = (base / name).resolve()
        # Refuse traversal: a stray "../interno/BILLING.md" in config must not
        # be able to pull an engineering doc into a public script.
        if base not in path.parents and path.parent != base:
            print(f"  [product] refused (outside docs dir): {name}")
            continue
        if not path.is_file():
            print(f"  [product] missing: {name}")
            continue
        text = path.read_text(encoding="utf-8").strip()
        if spent + len(text) > budget:
            text = text[: max(0, budget - spent)]
        if not text:
            break
        parts.append(f"--- {path.name} ---\n{text}")
        spent += len(text)
        if spent >= budget:
            break
    return "\n\n".join(parts)


def product_block(cfg: Config) -> str:
    """Prompt fragment describing the product. Empty when disabled."""
    docs = load_docs(cfg)
    if not docs:
        return ""
    name = cfg.get("product.name", "o aplicativo")
    return f"""
========================= THE PRODUCT ({name}) =========================
This channel belongs to {name}. Below is its official brand brief — the ONLY
source for what the product does. Never attribute a feature it does not list.

{docs}
====================== END OF PRODUCT INFORMATION ======================
"""


def choose_outro(cfg: Config, script_text: str) -> str:
    """The outro to speak, given what the model actually wrote.

    `script.outro` is a product call to action and used to be appended
    unconditionally — which quietly defeated the "do not pitch" rule: the model
    could correctly decline to mention the app on a story about a sick child,
    and the pipeline would then bolt a sales line onto the end anyway.

    So: if the narration never names the product, the model declined, and we
    honour that with a neutral outro instead.
    """
    outro = cfg.get("script.outro", "Subscribe for more.")
    neutral = cfg.get("script.outro_neutral", "")
    if not neutral or not cfg.get("product.bridge.enabled", False):
        return outro
    name = str(cfg.get("product.name", "")).strip()
    if not name:
        return outro
    if name.lower() in (script_text or "").lower():
        return outro
    print("  [product] no product mention in the script — using the neutral outro")
    return neutral


def _fold(text: str) -> str:
    """Lowercase and strip accents, so "prognóstico" matches "prognostico"."""
    import unicodedata
    nfkd = unicodedata.normalize("NFKD", (text or "").lower())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def bridge_allowed(cfg: Config, story: str) -> tuple[bool, str]:
    """Whether this story may carry a product pitch at all.

    The prompt asks the model not to pitch on stories about a suffering person.
    Testing showed it obeys inconsistently: it correctly declined on a death in
    a treatment queue, then pitched anyway on a hospitalised four-year-old with
    a poor prognosis. A rule the model follows most of the time is not a control
    for the case that would do the most damage.

    So the decision is made here, in code, before the model is ever shown the
    instruction to close with a product. Over-blocking is the safe direction:
    the cost is a missed mention, while the cost of under-blocking is a video
    that sells an app next to a sick child.

    Returns (allowed, matched_term).
    """
    terms = cfg.get("product.bridge.block_terms", []) or []
    folded = _fold(story)
    for term in terms:
        t = _fold(str(term)).strip()
        if not t:
            continue
        words = t.split()
        if len(words) == 1:
            # Anchored at a WORD START, not anywhere in the string. A bare
            # substring test blocked "Anvisa aprova o genérico da semaglutida"
            # because "uti" sits inside "semaglUTIda" — killing one of the most
            # relevant stories this channel can run. Prefix (not full word) is
            # deliberate: the list carries stems like "internad" and "arrecad"
            # that must still catch internado / internação / arrecada.
            if re.search(r"\b" + re.escape(t), folded):
                return False, str(term)
        # A multi-word term matches when ALL its words appear, in any order and
        # with anything in between. Exact-substring matching let a real story
        # through: the term was "campanha para custear" and the headline read
        # "campanha DE SOLIDARIEDADE PARA custear o tratamento" — three words
        # inserted in the middle, and the guard silently allowed the pitch.
        elif all(w in folded for w in words):
            return False, str(term)
    return True, ""


def bridge_block(cfg: Config, story: str = "") -> str:
    """The editorial rule that turns a news script into a news->product script.

    The escape hatch in rule 5 is deliberate and load-bearing. Without an
    explicit permission to NOT pitch, the model forces a tie-in onto stories
    where none exists — which is exactly how a channel about medicine prices
    ends up making a tasteless joke out of somebody's cancer diagnosis.
    """
    if not cfg.get("product.bridge.enabled", False):
        return ""
    allowed, term = bridge_allowed(cfg, story)
    if not allowed:
        print(f"  [product] sensitive story (matched '{term}') — no product close")
        return """
--------------------------- THE CLOSE (required) ---------------------------
This story is about a person in a difficult situation. Do NOT mention any app,
product, service or call to action anywhere in this script — not in the body,
not at the end. Tell the story with respect and stop.
---------------------------------------------------------------------------
"""
    name = cfg.get("product.name", "o aplicativo")
    cta = cfg.get("product.bridge.cta", "")
    return f"""
--------------------------- THE CLOSE (required) ---------------------------
Structure this script in TWO parts:

  1. THE STORY (about the first three quarters) — the real news, told straight.
     It must stand on its own and be worth watching even for someone who never
     installs anything.
  2. THE CLOSE (roughly the last quarter) — connect that story to ONE concrete
     thing {name} does. One feature, not a tour of the app.

Rules for the close — these are hard limits, not style preferences:

1. {name} compares PRICES of pharmacy products. It does not treat, diagnose,
   prevent or cure anything, and it never replaces a doctor, a pharmacist or a
   health plan. Never imply otherwise, in any wording.
2. For stories about a serious illness (cancer, diabetes, rare disease), the
   ONLY honest bridge is the COST of the treatment or medication — "this
   treatment is expensive, here is how to pay less for what you already buy".
   Never suggest the app helps with the disease itself.
3. Never invent a number. No "save up to X%", no invented averages, no made-up
   price. If a figure did not come from the news source, it does not exist.
4. Name no real pharmacy, and never present the app as a seller. Purchases
   happen at the pharmacy; the app only shows where things cost less.
5. DO NOT PITCH AT ALL — no product name, no feature, no call to action — when
   the story centres on an identifiable person or family in distress. Apply this
   whenever the story involves any of:
     - a named or described sick person, especially a child
     - a crowdfunding campaign, a "vaquinha", a family asking for help
     - somebody hospitalised, in intensive care, or with a poor prognosis
     - a death, a funeral, a grieving family
     - a tragedy, an accident, a contamination or an outbreak with victims
   In those cases write ONLY the story, with respect, and stop. Mentioning the
   app beside a suffering child reads as profiting from that child, no matter
   how gentle the wording. This rule OVERRIDES the instruction to close with a
   product: returning no close is the CORRECT output, not a failure.
   The bridge is for stories about prices, policy, rules and markets — not for
   stories about a person's misfortune.
6. Respect anyone the story is about. Their situation is not a setup for an ad.
{f'7. The final call to action, only when a close was made: "{cta}"' if cta else ''}
---------------------------------------------------------------------------
"""
