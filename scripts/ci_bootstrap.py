"""Prepare a GitHub Actions runner to behave like the local machine.

Run by .github/workflows/daily.yml before `python main.py daily`.

  * Writes the secret files the code expects on disk (.env, client_secret.json,
    token.json, output/instagram_token.json) from environment variables the
    workflow maps from repository secrets.
  * Nothing here is printed: a secret that reaches the job log is a secret
    that reaches the public Internet.

The loop pitches (videos/loop/*.mp4, ~900 MB) are NOT handled here: the
workflow downloads them from the `loop-v1` release into an actions/cache, so
they cost one download, not one per day.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

ENV_KEYS = [
    "ELEVENLABS_API_KEYS", "GEMINI_API_KEYS", "GEMINI_API_KEY_IMG", "GROQ_API_KEYS",
    "PEXELS_API_KEYS", "SERPER_API_KEYS", "SERPAPI_API_KEYS", "NEWSAPI_KEY",
    "INSTAGRAM_ACCESS_TOKEN", "INSTAGRAM_APP_ID", "INSTAGRAM_APP_SECRET",
    "META_APP_ID", "META_APP_SECRET", "DB_ARCHIVE_GITHUB_TOKEN",
    "CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN",
]

FILE_SECRETS = {
    "CLIENT_SECRET_JSON": ROOT / "client_secret.json",
    "TOKEN_JSON": ROOT / "token.json",
    "INSTAGRAM_TOKEN_JSON": ROOT / "output" / "instagram_token.json",
}


def main() -> int:
    missing: list[str] = []
    lines = []
    for k in ENV_KEYS:
        v = os.getenv(k, "")
        if v:
            lines.append(f"{k}={v}")
        else:
            missing.append(k)
    (ROOT / ".env").write_text("\n".join(lines) + "\n", encoding="utf-8")

    for k, path in FILE_SECRETS.items():
        v = os.getenv(k, "")
        if not v:
            missing.append(k)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(v, encoding="utf-8")

    (ROOT / "output").mkdir(exist_ok=True)
    required = {"ELEVENLABS_API_KEYS", "GEMINI_API_KEYS", "CLIENT_SECRET_JSON", "TOKEN_JSON",
                "INSTAGRAM_ACCESS_TOKEN", "DB_ARCHIVE_GITHUB_TOKEN"}
    hard = sorted(required & set(missing))
    soft = sorted(set(missing) - required)
    print(f"bootstrap: {len(ENV_KEYS) + len(FILE_SECRETS) - len(missing)} secrets written"
          + (f"; optional missing: {', '.join(soft)}" if soft else ""))
    if hard:
        print(f"bootstrap: REQUIRED secrets missing: {', '.join(hard)}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
