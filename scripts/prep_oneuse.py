"""Manual shortcut: caption + texts for one promo video, without publishing.

    python scripts/prep_oneuse.py videos/oneuse/ozivy.mp4 [--tags=ozivy,semaglutida] [--no-burn]

The same happens automatically inside `python main.py oneuse` / the runner
(config promo.auto_caption). Use this to preview the result first.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from src.config import load_config  # noqa: E402
from src.oneuse_prep import prepare  # noqa: E402


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__)
        return 1
    video = Path(args[0]).resolve()
    extra: list[str] = []
    for a in sys.argv[1:]:
        if a.startswith("--tags="):
            extra = [t.strip() for t in a.split("=", 1)[1].split(",") if t.strip()]
    meta = prepare(load_config(), video, extra, burn_captions="--no-burn" not in sys.argv)
    if meta is None:
        meta = json.loads(video.with_suffix(".json").read_text(encoding="utf-8"))
        print("(ja preparado — sidecar existente)")
    print()
    print("TÍTULO :", meta["title"])
    print("TAGS   :", ", ".join(meta["tags"]))
    print("DESCRIÇÃO:")
    print(meta["description"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
