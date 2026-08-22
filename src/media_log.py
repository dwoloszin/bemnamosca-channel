"""Persistent media rotation + usage log (output/media_usage.json).

Every video/post records WHICH library media it used. Selection is
least-used-first, so the whole library rotates: with 5 videos, all 5 appear
before any repeats. Videos additionally persist a play CURSOR across runs —
when a long clip is reused, it continues from a NEW timeframe instead of
replaying the same opening seconds.

File layout:
    {
      "usage": {"assets/videos/trailer.mp4": {"uses": 4, "last": "...", "cursor": 33.2}},
      "log":   [{"time": "...", "output": "short_x.mp4",
                 "media": ["assets/videos/a.mp4 @ 28.5s", "assets/images/b.jpg"],
                 "music": "assets/music/Misuse.mp3"}]
    }
"""
from __future__ import annotations

import json
import random
import time
from pathlib import Path

from .config import ROOT, Config

_LOG_CAP = 300


def _file(cfg: Config) -> Path:
    return cfg.output_dir / "media_usage.json"


def _load(cfg: Config) -> dict:
    try:
        d = json.loads(_file(cfg).read_text(encoding="utf-8"))
        d.setdefault("usage", {})
        d.setdefault("log", [])
        return d
    except Exception:  # noqa: BLE001
        return {"usage": {}, "log": []}


def _save(cfg: Config, data: dict) -> None:
    _file(cfg).write_text(json.dumps(data, indent=2), encoding="utf-8")


def _key(p: Path) -> str:
    p = Path(p).resolve()
    try:
        return p.relative_to(ROOT).as_posix()
    except ValueError:
        return p.as_posix()


def order_by_usage(cfg: Config, paths: list[Path]) -> list[Path]:
    """Least-used first (ties in random order) — the rotation guarantee:
    nothing is picked a 2nd time before every other item was picked once."""
    usage = _load(cfg)["usage"]

    def sort_key(p: Path):
        e = usage.get(_key(p), {})
        return (int(e.get("uses", 0)), e.get("last", ""), random.random())

    return sorted(paths, key=sort_key)


def video_cursor(cfg: Config, p: Path) -> float:
    """Last persisted play position for a video (0.0 when never used)."""
    e = _load(cfg)["usage"].get(_key(p), {})
    try:
        return float(e.get("cursor", 0.0))
    except (TypeError, ValueError):
        return 0.0


def set_video_cursors(cfg: Config, cursors: dict[Path, float]) -> None:
    data = _load(cfg)
    for p, pos in cursors.items():
        data["usage"].setdefault(_key(p), {})["cursor"] = round(float(pos), 2)
    _save(cfg, data)


def record_usage(cfg: Config, output: Path,
                 media: list[tuple[Path, float | None]],
                 music: Path | None = None) -> None:
    """Log one creation: bump each unique library file's use count and append
    a human-readable entry. `media` is (path, video_start_offset_or_None)."""
    data = _load(cfg)
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    lines: list[str] = []
    seen: set[str] = set()
    for p, off in media:
        k = _key(p)
        lines.append(f"{k} @ {off:.1f}s" if off is not None else k)
        if k not in seen:
            seen.add(k)
            e = data["usage"].setdefault(k, {})
            e["uses"] = int(e.get("uses", 0)) + 1
            e["last"] = now
    if music is not None:
        mk = _key(music)
        e = data["usage"].setdefault(mk, {})
        e["uses"] = int(e.get("uses", 0)) + 1
        e["last"] = now
    entry = {"time": now, "output": output.name, "media": lines}
    if music is not None:
        entry["music"] = _key(music)
    data["log"].append(entry)
    data["log"] = data["log"][-_LOG_CAP:]
    _save(cfg, data)
    print(f"  [media-log] {output.name}: {len(lines)} visual(s)"
          + (f" + music {Path(music).name}" if music else "")
          + f" -> {_file(cfg).name}")
