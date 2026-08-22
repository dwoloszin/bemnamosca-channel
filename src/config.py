"""Load configuration from config.yaml and secrets from .env."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:  # dotenv is optional at runtime
    pass

ROOT = Path(__file__).resolve().parent.parent


class Config:
    """Thin wrapper around the parsed YAML with dotted-path access."""

    def __init__(self, data: dict[str, Any], path: Path):
        self._data = data
        self.path = path

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self._data
        for key in dotted.split("."):
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __contains__(self, key: str) -> bool:
        return key in self._data

    @property
    def raw(self) -> dict[str, Any]:
        return self._data

    # --- secrets from environment ------------------------------------------
    @property
    def anthropic_key(self) -> str | None:
        return os.getenv("ANTHROPIC_API_KEY") or None

    @property
    def pexels_keys(self) -> list[str]:
        """All Pexels keys, in order. PEXELS_API_KEYS (comma-separated) wins;
        PEXELS_API_KEY stays valid so an existing .env keeps working."""
        raw = os.getenv("PEXELS_API_KEYS", "") or os.getenv("PEXELS_API_KEY", "")
        seen: set[str] = set()
        keys: list[str] = []
        for part in raw.split(","):
            k = part.strip()
            if k and k not in seen:
                seen.add(k)
                keys.append(k)
        return keys

    @property
    def pexels_key(self) -> str | None:
        keys = self.pexels_keys
        return keys[0] if keys else None

    @property
    def newsapi_key(self) -> str | None:
        return os.getenv("NEWSAPI_KEY") or None

    # --- resolved paths ----------------------------------------------------
    def path_of(self, relative: str) -> Path:
        p = Path(relative)
        return p if p.is_absolute() else (ROOT / p)

    @property
    def output_dir(self) -> Path:
        d = self.path_of(self.get("output.dir", "output"))
        d.mkdir(parents=True, exist_ok=True)
        return d


def load_config(path: str | Path = "config.yaml") -> Config:
    p = Path(path)
    if not p.is_absolute():
        p = ROOT / p
    if not p.exists():
        raise FileNotFoundError(f"Config not found: {p}")
    with open(p, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return Config(data, p)
