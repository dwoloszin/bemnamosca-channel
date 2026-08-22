"""Content history — prevents repetitive videos on a daily schedule.

Three layers of protection:
  1. EXACT: a headline that was already used is never used again (permanent).
  2. FUZZY: a new headline that mostly overlaps a story covered in the last
     N days is treated as already covered (same news, different outlet).
  3. LLM AWARENESS: recent video titles/stories are handed to the script
     prompts so the model actively avoids re-telling the same facts even
     from technically-new sources.

Stored in output/history.json. The legacy output/state.json "used" keys are
migrated automatically on first load.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from .config import Config
from .news import NewsItem

_FUZZY_DAYS = 14          # fuzzy-match window (stories resurface legitimately later)
_FUZZY_THRESHOLD = 0.5    # overlap coefficient above this = same story
_MIN_SHARED = 3           # ...but only when at least this many meaningful words match

_STOP = {
    "the", "a", "an", "of", "to", "in", "on", "for", "and", "is", "are", "at",
    "by", "with", "as", "its", "it", "this", "that", "will", "be", "has", "have",
    "was", "were", "from", "into", "after", "before", "over", "under", "about",
    "gta", "grand", "theft", "auto", "vi", "6", "rockstar", "games", "game",
    "new", "news", "update", "video", "youtube", "reveals", "revealed", "says",
}


def _key(title: str) -> str:
    return re.sub(r"[^a-z0-9]", "", title.lower())[:60]


def _tokens(title: str) -> set[str]:
    words = re.findall(r"[a-z0-9']+", title.lower())
    return {w for w in words if w not in _STOP and len(w) > 2}


def _similarity(a: set[str], b: set[str]) -> float:
    """Overlap coefficient: robust when one headline is much longer than the
    other (common across outlets). Guarded by a minimum shared-word count so
    two short unrelated titles can't trip it."""
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if inter < _MIN_SHARED:
        return 0.0
    return inter / min(len(a), len(b))


class History:
    def __init__(self, cfg: Config):
        self.path = cfg.output_dir / "history.json"
        self.data: dict = {"keys": {}, "entries": []}
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                pass
        self.data.setdefault("keys", {})
        self.data.setdefault("entries", [])
        self._migrate_legacy(cfg)

    def _migrate_legacy(self, cfg: Config) -> None:
        legacy = cfg.output_dir / "state.json"
        if not legacy.exists():
            return
        try:
            used = json.loads(legacy.read_text(encoding="utf-8")).get("used", [])
            changed = False
            for k in used:
                if k not in self.data["keys"]:
                    self.data["keys"][k] = 0  # unknown timestamp -> exact-only
                    changed = True
            if changed:
                self._save()
            legacy.rename(legacy.with_suffix(".json.migrated"))
            print(f"  [history] migrated {len(used)} keys from state.json")
        except Exception:  # noqa: BLE001
            pass

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=2), encoding="utf-8")

    # ------------------------------------------------------------------ #
    def is_covered(self, item: NewsItem) -> bool:
        """Exact key ever used, or fuzzy-similar to a story from the window."""
        if _key(item.clean_title) in self.data["keys"]:
            return True
        cand = _tokens(item.clean_title)
        cutoff = time.time() - _FUZZY_DAYS * 86400
        for entry in reversed(self.data["entries"]):
            if entry.get("ts", 0) < cutoff:
                break  # entries are chronological
            for story in entry.get("stories", []):
                if _similarity(cand, set(story.get("tokens", []))) >= _FUZZY_THRESHOLD:
                    return True
        return False

    def filter_fresh(self, items: list[NewsItem]) -> list[NewsItem]:
        fresh = [i for i in items if not self.is_covered(i)]
        skipped = len(items) - len(fresh)
        if skipped:
            print(f"  [history] skipped {skipped} already-covered stories")
        return fresh

    def recent_content(self, days: int = 7, limit: int = 15) -> list[str]:
        """Recent video titles + covered story headlines, for LLM avoid-lists."""
        cutoff = time.time() - days * 86400
        lines: list[str] = []
        for entry in reversed(self.data["entries"]):
            if entry.get("ts", 0) < cutoff or len(lines) >= limit:
                break
            lines.append(entry.get("title", ""))
            for story in entry.get("stories", [])[:3]:
                if story.get("title"):
                    lines.append(f"  - {story['title']}")
        return [l for l in lines if l][:limit]

    # --- post TOPIC rotation ------------------------------------------- #
    # Topics are evergreen ("GTA 6 price" is always trending), so they can't
    # be banned forever like a headline — they're ROTATED instead: the least
    # recently used topic wins, which is why every subject gets its turn.
    def topic_used_at(self, topic: str) -> float:
        return float(self.data.setdefault("topics", {}).get(_key(topic), 0.0))

    def rank_topics(self, topics: list[str]) -> list[str]:
        """Least-recently-used first; never-used topics come first of all."""
        return sorted(topics, key=self.topic_used_at)

    def topic_cooling(self, topic: str, days: int = 10) -> bool:
        """True when this topic was posted about within the last `days`."""
        used = self.topic_used_at(topic)
        return bool(used) and (time.time() - used) < days * 86400

    def record_topic(self, topic: str) -> None:
        self.data.setdefault("topics", {})[_key(topic)] = time.time()
        self._save()

    def record(self, kind: str, video_title: str, items: list[NewsItem]) -> None:
        now = time.time()
        stories = []
        for i in items:
            k = _key(i.clean_title)
            self.data["keys"][k] = now
            stories.append({
                "key": k,
                "title": i.clean_title[:120],
                "tokens": sorted(_tokens(i.clean_title)),
            })
        self.data["entries"].append({
            "ts": now,
            "date": time.strftime("%Y-%m-%d %H:%M"),
            "kind": kind,
            "title": video_title[:120],
            "stories": stories,
        })
        # Keep the entries list bounded (keys stay forever for exact dedup).
        if len(self.data["entries"]) > 400:
            self.data["entries"] = self.data["entries"][-400:]
        self._save()
