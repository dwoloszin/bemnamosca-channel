#!/usr/bin/env python
"""Clone this project into a NEW folder for a DIFFERENT channel.

Copying the folder by hand is risky: token.json would upload to the WRONG
YouTube channel, output/history.json would make the new channel think it
already covered everything, and the Instagram/TikTok tokens would post to the
wrong accounts. This copies only what is safe to share and leaves every
per-channel credential and state file to be created fresh.

    python clone_channel.py "C:\\path\\to\\new-channel" \
        --name "F1 News Daily" --handle "@f1news" --slug "f1" --topic "Formula 1"

What is COPIED   : code (src/, main.py, daily.bat), requirements, scheduler,
                   README, .gitignore, and the SHARED provider keys from .env
                   (Groq/Gemini/Serper/SerpAPI/Cloudflare/ElevenLabs/Pexels).
What is NOT      : token.json, output/ (history, schedules, IG/TikTok tokens),
                   assets media, img-channel media, .git, .venv.
client_secret.json is copied only with --copy-google (same Google project =
shared 10k/day quota; a separate project keeps the quotas independent).
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# Provider keys: one account serves every channel (quota is shared).
SHARED_ENV = {
    "ANTHROPIC_API_KEY", "GROQ_API_KEYS", "GEMINI_API_KEYS", "PEXELS_API_KEY",
    "SERPER_API_KEYS", "SERPAPI_API_KEYS", "NEWSAPI_KEY",
    "CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN", "ELEVENLABS_API_KEYS",
    # App-level (not account-level) credentials:
    "META_APP_ID", "META_APP_SECRET", "INSTAGRAM_APP_ID", "INSTAGRAM_APP_SECRET",
    "TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET", "DB_ARCHIVE_GITHUB_TOKEN",
}
# Account-level: these bind to ONE channel and must be re-authorized.
PER_CHANNEL_ENV = {"INSTAGRAM_ACCESS_TOKEN", "META_USER_TOKEN"}

COPY_FILES = ["main.py", "daily.bat", "requirements.txt", "README.md",
              ".gitignore", ".env.example", ".pre-commit-config.yaml",
              "clone_channel.py"]
COPY_DIRS = ["src", "scheduler", ".github"]
MAKE_DIRS = ["assets/images", "assets/videos", "assets/music", "assets/compare",
             "img-channel/horizontal", "img-channel/vertical", "img-channel/logo",
             "output"]


def build_env(dest: Path) -> None:
    src_env = ROOT / ".env"
    lines_out: list[str] = [
        "# Shared provider keys were copied from the source channel.",
        "# NOTE: quotas are shared across channels using the same keys.",
        "",
    ]
    shared, blanked = 0, 0
    if src_env.exists():
        for raw in src_env.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\s*([A-Z0-9_]+)\s*=(.*)$", raw)
            if not m:
                continue
            name, value = m.group(1), m.group(2)
            if name in SHARED_ENV:
                lines_out.append(f"{name}={value}")
                shared += 1
            elif name in PER_CHANNEL_ENV:
                lines_out.append(f"# {name}=   <- authorize THIS channel, then paste here")
                blanked += 1
    lines_out.append("")
    (dest / ".env").write_text("\n".join(lines_out), encoding="utf-8")
    print(f"  .env: {shared} shared keys copied, {blanked} per-channel keys left blank")


def patch_config(dest: Path, name: str, handle: str, slug: str, topic: str) -> None:
    """Rewrite only the identity fields; every other tuning stays as-is so the
    new channel starts from a configuration that is already known to work."""
    text = (ROOT / "config.yaml").read_text(encoding="utf-8")

    def sub(pattern: str, repl: str, s: str) -> str:
        return re.sub(pattern, repl, s, count=1, flags=re.MULTILINE)

    text = sub(r'^(\s*name:\s*).*$', rf'\g<1>"{name}"', text)
    text = sub(r'^(\s*handle:\s*).*$', rf'\g<1>"{handle}"', text)
    text = sub(r'^(\s*slug:\s*).*$', rf'\g<1>"{slug}"', text)
    text = sub(r'^(\s*topic:\s*).*$', rf'\g<1>"{topic}"', text)
    # Instagram caption suffix carries the old handle.
    text = re.sub(r'^(\s*caption_suffix:\s*).*$',
                  rf'\g<1>"Follow {handle} for daily {topic} news!"',
                  text, count=1, flags=re.MULTILINE)

    banner = (
        "# ==========================================================================\n"
        f"# {name} — cloned project. BEFORE THE FIRST RUN, review:\n"
        "#   theme.keywords / search_queries / rss_feeds / subreddits\n"
        "#   theme.must_match_any / block_words   (they still filter the OLD topic)\n"
        "#   posts.topics, youtube.tag_pool, youtube.default_tags, description_template\n"
        "# ==========================================================================\n"
    )
    (dest / "config.yaml").write_text(banner + text, encoding="utf-8")
    print(f"  config.yaml: identity set (name/handle/slug/topic); theme left to review")


def main() -> int:
    ap = argparse.ArgumentParser(description="Clone this project for another channel")
    ap.add_argument("dest", help="target folder for the new channel")
    ap.add_argument("--name", required=True, help='channel name, e.g. "F1 News Daily"')
    ap.add_argument("--handle", required=True, help='channel handle, e.g. "@f1news"')
    ap.add_argument("--slug", required=True, help='short id used in filenames, e.g. "f1"')
    ap.add_argument("--topic", required=True, help='main topic, e.g. "Formula 1"')
    ap.add_argument("--copy-google", action="store_true",
                    help="copy client_secret.json (same Google project shares the "
                         "10k/day API quota with the source channel)")
    args = ap.parse_args()

    dest = Path(args.dest).resolve()
    if dest == ROOT:
        print("Destination is the source project itself — aborting.")
        return 1
    if dest.exists() and any(dest.iterdir()):
        print(f"Destination is not empty: {dest}\nPick an empty/new folder.")
        return 1

    print(f"Cloning -> {dest}")
    dest.mkdir(parents=True, exist_ok=True)

    for d in COPY_DIRS:
        src = ROOT / d
        if src.exists():
            shutil.copytree(src, dest / d,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for f in COPY_FILES:
        if (ROOT / f).exists():
            shutil.copyfile(ROOT / f, dest / f)
    print(f"  code copied ({len(COPY_DIRS)} dirs, {len(COPY_FILES)} files)")

    for d in MAKE_DIRS:
        (dest / d).mkdir(parents=True, exist_ok=True)
    for d in ("assets/images", "assets/videos", "assets/music"):
        (dest / d / "README.md").write_text(
            f"Put this channel's {Path(d).name} here.\n", encoding="utf-8")
    print(f"  empty asset folders created (add YOUR media for this channel)")

    build_env(dest)
    patch_config(dest, args.name, args.handle, args.slug, args.topic)

    if args.copy_google and (ROOT / "client_secret.json").exists():
        shutil.copyfile(ROOT / "client_secret.json", dest / "client_secret.json")
        print("  client_secret.json copied (SHARED Google quota: ~10k units/day total)")

    print(f"""
Done. Next steps in {dest}:

  1. MEDIA   put images/clips in assets/images, assets/videos, music in
             assets/music; logo at img-channel/logo/logo.png and the
             intro/outro banners in img-channel/vertical|horizontal.
  2. THEME   edit config.yaml: keywords, search_queries, rss_feeds,
             subreddits, must_match_any, block_words, posts.topics and the
             YouTube tag_pool — these still describe the OLD topic.
  3. YOUTUBE {'client_secret.json is in place' if args.copy_google else 'download client_secret.json (Google Cloud > OAuth Desktop)'};
             then run:  python main.py youtube-test
             Sign in with the NEW channel's account -> creates token.json.
  4. SOCIAL  Instagram: authorize the new account, paste INSTAGRAM_ACCESS_TOKEN
             in .env.  TikTok: python main.py tiktok-auth  (logs the new
             account in; token lands in output/tiktok_token.json).
  5. TEST    python main.py news        (are the headlines on-topic?)
             set youtube.enabled/instagram.enabled/tiktok.enabled to false
             first, build one short, watch it, then turn uploads back on.
""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
