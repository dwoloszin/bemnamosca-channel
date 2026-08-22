"""Turn news items into a spoken narration script using an LLM.

Provider order (see src/llm.py): Claude -> Groq key pool -> template fallback,
so the pipeline always runs end-to-end even with no keys at all.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .config import Config
from .llm import get_llm
from .news import NewsItem
from .product_context import bridge_block, choose_outro, product_block


@dataclass
class ScriptSection:
    """One narrated segment (maps to one news item)."""
    heading: str
    text: str
    keyword: str  # used to pick matching stock visuals


@dataclass
class VideoScript:
    title: str
    hook: str                 # first spoken line (grabs attention)
    sections: list[ScriptSection]
    outro: str

    def spoken_blocks(self) -> list[tuple[str, str]]:
        """Return (text, keyword) blocks in speaking order."""
        blocks: list[tuple[str, str]] = [(self.hook, self.sections[0].keyword if self.sections else "")]
        for s in self.sections:
            blocks.append((s.text, s.keyword))
        blocks.append((self.outro, ""))
        return blocks

    @property
    def full_text(self) -> str:
        return " ".join(t for t, _ in self.spoken_blocks())


_LANG_NAMES = {
    "en": "English", "pt": "Brazilian Portuguese", "pt-br": "Brazilian Portuguese",
    "es": "Spanish", "fr": "French", "de": "German", "it": "Italian",
}


def _lang_block(cfg: Config) -> str:
    """Instruction forcing the output language (channel.language).

    Without this every prompt defaults to English, so a Portuguese channel
    would get English narration read by a Portuguese voice.
    """
    code = str(cfg.get("channel.language", "en") or "en").lower()
    if code.startswith("en"):
        return ""
    name = _LANG_NAMES.get(code, code)
    return (f"\nLANGUAGE: write EVERYTHING (title, hook, body, tags, description)\n"
            f"in {name}. Natural, native phrasing — never translate literally.\n"
            f"Keep product names, brands and proper nouns as they are.\n")


def _avoid_block(avoid: list[str] | None) -> str:
    """Prompt fragment listing recently covered content the model must skip."""
    if not avoid:
        return ""
    lines = "\n".join(f"- {a}" for a in avoid[:15])
    return f"""
ALREADY COVERED in this channel's recent videos (do NOT repeat these stories
or facts — pick different ones; skip any source that only re-tells these):
{lines}
"""


def _keyword_for(item: NewsItem, cfg: Config) -> str:
    topic = cfg.get("theme.topic", "")
    # Try to lift a concrete noun from the headline, else fall back to topic.
    # Accented letters must survive: stripping them turned "remédio" into two
    # fragments and the media search got "rem" as a keyword.
    words = re.findall(r"[\wÀ-ÿ']+", item.clean_title)
    stop = {"the", "a", "an", "of", "to", "in", "on", "for", "and", "new",
            "o", "os", "as", "um", "uma", "de", "da", "do", "das", "dos",
            "em", "no", "na", "para", "por", "com", "que", "e"}
    picked = [w for w in words if w.lower() not in stop][:3]
    return " ".join([topic] + picked) if picked else topic


def write_short_script(item: NewsItem, cfg: Config) -> VideoScript:
    """A single-item, ~30-55s vertical script."""
    kw = _keyword_for(item, cfg)
    client = get_llm(cfg)
    words = cfg.get("script.short_words", 90)
    outro = cfg.get("script.outro", "Subscribe for more.")

    if client is None:
        body = _template_short(item, cfg)
        # body[0] is the hook — the section must NOT repeat it (spoken_blocks
        # already yields the hook first).
        return VideoScript(
            title=_short_title(item, cfg),
            hook=body[0],
            sections=[ScriptSection(item.clean_title, " ".join(body[1:]), kw)],
            outro=outro,
        )

    prompt = f"""You are writing a YouTube SHORT narration about {cfg.get('theme.topic')}
for this audience: {cfg.get('script.audience', 'a general audience')}
Editorial voice: {cfg.get('script.style')}
{product_block(cfg)}
News headline: {item.clean_title}
Summary: {item.clean_summary or '(no summary provided)'}
Source: {item.source}
{bridge_block(cfg, f"{item.clean_title} {item.clean_summary}")}
Write a spoken script of BETWEEN {int(words*0.9)} AND {words + 10} words — count
them; scripts outside that range get rejected. Requirements:
- Open with a 1-sentence hook that makes the viewer stop scrolling (the most
  striking fact of THIS story — don't give it all away in line one).
- STRICTLY factual to the headline/summary; do NOT invent details, dates,
  numbers or quotes.
- If the story is unconfirmed, SAY so — never present a rumor as fact.
  Misinformation about health is not a style mistake, it is a real harm.
- Never give medical advice, never name a dose, never promise a cure.
- Short, punchy sentences that sound natural read aloud.
- Do NOT include the outro (added separately).

{_lang_block(cfg)}
Return ONLY JSON: {{"title": "...", "hook": "...", "body": "..."}}"""

    data = _ask_json(client, prompt)
    if not data:
        body = _template_short(item, cfg)
        return VideoScript(_short_title(item, cfg), body[0],
                           [ScriptSection(item.clean_title, " ".join(body[1:]), kw)], outro)

    hook = data.get("hook", "").strip()
    body = data.get("body", "").strip()
    # The hook is spoken via spoken_blocks(); the section holds only the body.
    return VideoScript(
        title=(data.get("title") or _short_title(item, cfg)).strip()[:95],
        hook=hook or item.clean_title,
        sections=[ScriptSection(item.clean_title, body or item.clean_summary or item.clean_title, kw)],
        outro=choose_outro(cfg, f"{hook} {body}"),
    )


def write_listicle_script(sources: list[NewsItem], cfg: Config,
                          count: int = 5,
                          avoid: list[str] | None = None
                          ) -> tuple[VideoScript, list[int]] | None:
    """Viral countdown Short: "Top {count} ..." built ONLY from real sources
    (news + community posts). `avoid` lists recently covered titles/stories so
    the model doesn't re-tell the same facts. Returns (script,
    used_source_indices) or None when no LLM is available."""
    client = get_llm(cfg)
    if client is None or not sources:
        return None

    topic = cfg.get("theme.topic", "")
    outro = cfg.get("script.outro", "Subscribe for more.")
    src_lines = []
    for i, s in enumerate(sources[:24]):
        tag = "COMMUNITY" if s.source.startswith("r/") else "NEWS"
        line = f"{i}. [{tag}] {s.clean_title}"
        if s.clean_summary:
            line += f" — {s.clean_summary[:140]}"
        line += f" (source: {s.source})"
        src_lines.append(line)
    src_block = "\n".join(src_lines)

    prompt = f"""You are creating a VIRAL YouTube Short: a countdown "TOP {count}" list about
{topic} for this audience: {cfg.get('script.audience', 'a general audience')}
Voice: {cfg.get('script.style')}
{product_block(cfg)}
REAL source material (recent news + what the community is upvoting).
Use ONLY facts from these — invent NOTHING:
{src_block}
{_avoid_block(avoid)}{bridge_block(cfg, src_block)}
Build the {count} most attention-grabbing DISTINCT facts:
- Order for retention: interesting first, the MOST striking saved for #1.
- Each item: 18-25 spoken words, and START with its number ("Number {count}:", ... "Number 1:").
- If an item is unconfirmed, SAY so. Never present a rumor about health as fact.
- Never give medical advice, never name a dose, never promise a cure.
- Hook (1 sentence): promise the list and tease #1 without revealing it
  ("...and number 1 changes everything" style — only if true to the content).
- "keyword": 2-4 search words describing the item's visual, IN ENGLISH.
- "close": the closing lines described in THE CLOSE above (25-40 spoken words).
  Return "" for close if no honest bridge exists — that is a valid answer.
{_lang_block(cfg)}
IMPORTANT EXCEPTION to the language rule: "keyword" stays in ENGLISH — it
queries a stock photo library indexed in English, so a Portuguese term returns
photos from the wrong country. Use concrete, photographable nouns.
Return ONLY JSON:
{{"title": "max 70 chars, starts with TOP {count}",
  "hook": "...",
  "items": [{{"rank": {count}, "text": "...", "keyword": "..."}}, ...],
  "close": "...",
  "used_source_indices": [numbers of the sources you used]}}"""

    data = _ask_json(client, prompt)
    if not data or not data.get("items"):
        return None
    items_json = sorted(data["items"], key=lambda x: -int(x.get("rank", 0)))
    sections = [
        ScriptSection(
            heading=f"#{int(it.get('rank', 0))}",
            text=str(it.get("text", "")).strip(),
            keyword=str(it.get("keyword", "")).strip() or topic,
        )
        for it in items_json if str(it.get("text", "")).strip()
    ]
    if len(sections) < 3:
        return None
    # The product close rides as a final section so it is narrated after #1.
    # An empty string is the model exercising the "no honest bridge" escape —
    # respect it instead of falling back to a canned pitch.
    close = str(data.get("close", "")).strip()
    if close:
        sections.append(ScriptSection(heading="close", text=close, keyword=topic))
    used = [int(i) for i in (data.get("used_source_indices") or [])
            if isinstance(i, (int, float, str)) and str(i).isdigit() and int(i) < len(sources)]
    script = VideoScript(
        title=(data.get("title") or f"TOP {count} {topic} facts").strip()[:95],
        hook=(data.get("hook") or f"Here are the top {count} {topic} facts.").strip(),
        sections=sections,
        outro=choose_outro(cfg, " ".join(s.text for s in sections)),
    )
    return script, used


def write_long_script(items: list[NewsItem], cfg: Config) -> VideoScript:
    """A multi-item news-roundup script for a long (monetizable) video."""
    client = get_llm(cfg)
    outro = cfg.get("script.outro", "Subscribe for more.")
    topic = cfg.get("theme.topic", "the topic")
    per = cfg.get("script.long_section_words", 160)

    sections: list[ScriptSection] = []
    if client is None:
        hook = cfg.get("script.long_hook", "") or (
            f"Here is your latest {topic} news roundup. Let's get into it.")
        for it in items:
            sections.append(ScriptSection(it.clean_title, " ".join(_template_section(it, per)), _keyword_for(it, cfg)))
        return VideoScript(f"{topic} News Roundup", hook, sections, outro)

    hook = cfg.get("script.long_hook", "") or (
        f"Welcome back. Here is everything new in {topic} right now. Let's break it down.")
    for idx, it in enumerate(items):
        kw = _keyword_for(it, cfg)
        # Only the LAST segment carries the product close. Pitching in every
        # segment turns a news roundup into an infomercial.
        is_last = idx == len(items) - 1
        tail = (bridge_block(cfg, f"{it.clean_title} {it.clean_summary}") if is_last
                else "\nRules: end with a smooth transition into the next story.\n")
        prompt = f"""Write ~{per} words of spoken narration for ONE segment of a {topic}
news roundup video. Voice: {cfg.get('script.style')}
{product_block(cfg) if is_last else ''}
Headline: {it.clean_title}
Summary: {it.clean_summary or '(no summary)'}
Source: {it.source}
{tail}
Rules: factual to the headline, natural spoken sentences. Never give medical
advice, never name a dose, never promise a cure, never invent a number.
{_lang_block(cfg)}
Return ONLY JSON: {{"text": "..."}}"""
        data = _ask_json(client, prompt)
        text = (data or {}).get("text", "").strip() if data else ""
        if not text:
            text = " ".join(_template_section(it, per))
        sections.append(ScriptSection(it.clean_title, text, kw))

    # Ask the model for a strong title.
    title = _long_title(client, cfg, items) or f"{topic} News Roundup"
    return VideoScript(title, hook, sections,
                       choose_outro(cfg, " ".join(s.text for s in sections)))


@dataclass
class SeoPack:
    title: str
    description: str
    tags: list[str]


def write_seo(cfg: Config, working_title: str, items: list[NewsItem],
              *, is_short: bool) -> SeoPack:
    """SEO-optimized title/description/tags. Falls back to templates."""
    topic = cfg.get("theme.topic", "")
    keywords = cfg.get("theme.keywords", []) or []
    client = get_llm(cfg)

    fallback = _seo_fallback(cfg, working_title, items, is_short=is_short)
    if client is None:
        return fallback

    # Pass the FACTS, not just headlines: given only titles the model invents
    # plausible-sounding content ("we analyze the new gameplay footage") for
    # stories that are merely an announcement of something not released yet.
    heads = "\n".join(
        f"- {i.clean_title}" + (f"\n    facts: {i.clean_summary[:220]}" if i.clean_summary else "")
        for i in items[:8]
    )
    kind = "YouTube Short (vertical, under 60s)" if is_short else "long-form YouTube video"
    prompt = f"""You are a YouTube SEO expert for a {topic} news channel.
Video type: {kind}
Stories covered (these facts are ALL the video contains):
{heads}

Write metadata that maximizes click-through and search ranking.

TRUTH RULES (breaking these gets the channel reported for misleading metadata):
- Describe ONLY what the stories above actually say. Invent NOTHING.
- If a story ANNOUNCES a future event (a trailer/showcase with a date), the
  video reports the ANNOUNCEMENT. Never write as if that content already
  aired: no "hidden details", "we analyze the footage", "everything you
  missed", no invented gameplay/physics/graphics/map claims.
- Never promise analysis, leaks or reveals the video does not contain.
- Keep dates, times and names exactly as given.

- "title": max 70 chars, front-load the main keyword ("{topic}"), create
  curiosity from the REAL fact (a date, a place, a number) — not from a
  promise the video can't deliver.
- "description": 2 short paragraphs. First 150 chars must hook + contain
  "{topic}" (that's what shows in search) and state the real news. Then the
  concrete details (date/time/where). No timestamps (added separately).
  End with a subscribe call-to-action.
- "tags": 15-20 search tags, mixing broad ({topic}, gaming) and specific
  (exact story keywords). Lowercase.

{_lang_block(cfg)}
Return ONLY JSON: {{"title": "...", "description": "...", "tags": ["...", "..."]}}"""

    data = _ask_json(client, prompt)
    if not data or not data.get("title"):
        return fallback
    tags = [str(t).strip() for t in (data.get("tags") or []) if str(t).strip()]
    return SeoPack(
        title=str(data["title"]).strip()[:95],
        description=str(data.get("description", "")).strip() or fallback.description,
        tags=tags or fallback.tags,
    )


def _seo_fallback(cfg: Config, working_title: str, items: list[NewsItem],
                  *, is_short: bool) -> SeoPack:
    topic = cfg.get("theme.topic", "")
    tagline = cfg.get("channel.brand_tagline", "")
    heads = "\n".join(f"• {i.clean_title}" for i in items[:8])
    description = (
        f"{working_title}\n\n{tagline}\n\nIn this video:\n{heads}\n\n"
        f"Subscribe for daily {topic} news!"
    )
    tags = [k.lower() for k in (cfg.get("theme.keywords", []) or [])]
    tags += [w.lower() for i in items[:4] for w in re.findall(r"[A-Za-z0-9]{4,}", i.clean_title)]
    seen, uniq = set(), []
    for t in tags:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    return SeoPack(working_title[:95], description, uniq[:20])


# --------------------------------------------------------------------------
# Claude helpers
# --------------------------------------------------------------------------
def _ask_json(client, prompt: str) -> dict | None:
    """client is any LLM from get_llm() — has .ask(prompt) -> str | None."""
    try:
        text = client.ask(prompt)
        if not text:
            return None
        data = _extract_json(text)
        if data is None:
            print(f"  [script] LLM returned unparseable JSON (starts: {text[:80]!r})")
            # One repair round: ask the model to rewrite its own output as strict JSON.
            fix_prompt = (
                "Rewrite the following response as STRICT JSON only. "
                "No markdown, no commentary, no code fences.\n\n"
                f"{text}"
            )
            fixed = client.ask(fix_prompt)
            if fixed:
                data = _extract_json(fixed)
        return data
    except Exception as exc:  # noqa: BLE001
        print(f"  [script] LLM call failed: {exc}")
        return None


def _long_title(client, cfg: Config, items: list[NewsItem]) -> str | None:
    # client: LLM object from get_llm()
    heads = "\n".join(f"- {i.clean_title}" for i in items[:6])
    prompt = f"""Write ONE catchy but honest YouTube title (max 80 chars) for a
{cfg.get('theme.topic')} news roundup covering:
{heads}
Return ONLY JSON: {{"title": "..."}}"""
    data = _ask_json(client, prompt)
    if data and data.get("title"):
        return data["title"].strip()[:95]
    return None


def _extract_json(text: str) -> dict | None:
    text = text.strip().lstrip("\ufeff")
    # Strip markdown fences if present.
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    # LLMs emit \' inside strings ("won\'t") — an INVALID JSON escape that
    # kills json.loads. A bare ' needs no escaping, so strip the backslash.
    text = text.replace("\\'", "'")
    # Also neutralize other invalid escapes like \_ or \# that models often emit.
    text = re.sub(r'\\(?!["\\/bfnrtu])', r"\\\\", text)

    # strict=False tolerates literal newlines/control chars inside strings.
    decoder = json.JSONDecoder(strict=False)
    candidates = [text]

    obj_start = text.find("{")
    if obj_start >= 0:
        candidates.append(text[obj_start:])

    for candidate in candidates:
        try:
            data, _end = decoder.raw_decode(candidate)
            if isinstance(data, dict):
                return data
        except Exception:  # noqa: BLE001
            pass
        try:
            data = json.loads(candidate, strict=False)
            if isinstance(data, dict):
                return data
        except Exception:  # noqa: BLE001
            pass

    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        candidate = m.group(0)
        try:
            data, _end = decoder.raw_decode(candidate)
            if isinstance(data, dict):
                return data
        except Exception:  # noqa: BLE001
            pass
        try:
            data = json.loads(candidate, strict=False)
            if isinstance(data, dict):
                return data
        except Exception:  # noqa: BLE001
            pass

    # Last resort: repair truncated JSON (close open strings/brackets).
    try:
        data = json.loads(_repair_json(text), strict=False)
        return data if isinstance(data, dict) else None
    except Exception:  # noqa: BLE001
        return None


def _repair_json(text: str) -> str:
    """Close unterminated strings/brackets in truncated LLM JSON."""
    start = text.find("{")
    if start > 0:
        text = text[start:]
    stack: list[str] = []
    in_str = esc = False
    for ch in text:
        if esc:
            esc = False
            continue
        if ch == "\\":
            esc = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch in "{[":
            stack.append(ch)
        elif ch in "}]" and stack:
            stack.pop()
    if in_str:
        text += '"'
    text = text.rstrip().rstrip(",")
    return text + "".join("}" if c == "{" else "]" for c in reversed(stack))


# --------------------------------------------------------------------------
# Template fallbacks (used when no API key / model declines)
# --------------------------------------------------------------------------
def _short_title(item: NewsItem, cfg: Config) -> str:
    return f"{cfg.get('theme.topic')}: {item.clean_title}"[:95]


def _template_short(item: NewsItem, cfg: Config) -> list[str]:
    topic = cfg.get("theme.topic", "this")
    return [
        f"Big {topic} news just dropped.",
        item.clean_title + ".",
        (item.clean_summary[:220] + ".") if item.clean_summary else
        f"Here is what we know so far about {topic}.",
        f"According to {item.source}, this is a story worth watching.",
    ]


def _template_section(item: NewsItem, target_words: int) -> list[str]:
    parts = [
        f"Next up: {item.clean_title}.",
        item.clean_summary or "Details are still coming in on this one.",
        f"Reported by {item.source}.",
    ]
    return parts
