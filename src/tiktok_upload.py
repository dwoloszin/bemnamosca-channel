"""Publish Shorts to TikTok via the Content Posting API (direct post).

Auth flow (one time): `python main.py tiktok-auth` opens TikTok's login page;
after approving, the browser lands on our callback page showing an
authorization code, which you paste back into the terminal. Tokens are stored
in output/tiktok_token.json — access tokens last 24h and are auto-refreshed
(refresh tokens last ~1 year).

Note: while the app is unaudited / in sandbox, TikTok forces posts to
privacy_level SELF_ONLY (visible only to the account). After the app review
passes, set tiktok.privacy_level: "PUBLIC_TO_EVERYONE" in config.yaml.
"""
from __future__ import annotations

import json
import os
import time
import urllib.parse
from pathlib import Path

import requests

from .config import Config

AUTH_PAGE = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
API = "https://open.tiktokapis.com/v2"
REDIRECT_URI = "https://dwoloszin.github.io/gtanews-legal/tiktok-callback.html"
# Must exactly match the scopes granted to the app/sandbox in the portal.
# Override without code changes: TIKTOK_SCOPES=user.info.basic,video.upload
SCOPES = (os.getenv("TIKTOK_SCOPES") or "user.info.basic,video.upload,video.publish").strip()


def _creds() -> tuple[str, str] | None:
    key = (os.getenv("TIKTOK_CLIENT_KEY") or "").strip()
    sec = (os.getenv("TIKTOK_CLIENT_SECRET") or "").strip()
    return (key, sec) if key and sec else None


def _state_path(cfg: Config) -> Path:
    return cfg.output_dir / "tiktok_token.json"


def _save_tokens(cfg: Config, d: dict) -> None:
    d["obtained_at"] = time.time()
    _state_path(cfg).parent.mkdir(parents=True, exist_ok=True)
    _state_path(cfg).write_text(json.dumps(d, indent=2), encoding="utf-8")


def _load_tokens(cfg: Config) -> dict | None:
    p = _state_path(cfg)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def authorize(cfg: Config) -> bool:
    """Interactive one-time OAuth: open browser, paste the code back here."""
    creds = _creds()
    if not creds:
        print("TIKTOK_CLIENT_KEY / TIKTOK_CLIENT_SECRET missing in .env")
        return False
    key, _sec = creds
    url = AUTH_PAGE + "?" + urllib.parse.urlencode({
        "client_key": key,
        "response_type": "code",
        "scope": SCOPES,
        "redirect_uri": REDIRECT_URI,
        "state": "gtanews",
    })
    print("=" * 60)
    print("1. A browser window will open — log in with the channel's TikTok")
    print("   account and click Authorize.")
    print("2. You'll land on our callback page showing an authorization code.")
    print("3. Copy it and paste it below.")
    print("=" * 60)
    print("If the browser doesn't open, use this URL:")
    print(url)
    try:
        import webbrowser
        webbrowser.open(url)
    except Exception:  # noqa: BLE001
        pass
    code = input("\nPaste the authorization code here: ").strip()
    if not code:
        print("No code given.")
        return False
    # The callback page URL-decodes for display; the token endpoint wants it raw.
    return _exchange_code(cfg, code)


def _exchange_code(cfg: Config, code: str) -> bool:
    key, sec = _creds()
    r = requests.post(TOKEN_URL, data={
        "client_key": key, "client_secret": sec,
        "code": code, "grant_type": "authorization_code",
        "redirect_uri": REDIRECT_URI,
    }, headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=30)
    d = r.json()
    if "access_token" not in d:
        print(f"Token exchange failed: {d}")
        return False
    _save_tokens(cfg, d)
    print(f"Authorized! Connected TikTok open_id: {d.get('open_id', '?')}")
    print("Tokens saved — future runs refresh automatically.")
    return True


def get_access_token(cfg: Config) -> str | None:
    d = _load_tokens(cfg)
    if not d:
        return None
    age = time.time() - d.get("obtained_at", 0)
    if age < d.get("expires_in", 86400) - 600:
        return d["access_token"]
    # refresh
    creds = _creds()
    if not creds:
        return None
    key, sec = creds
    r = requests.post(TOKEN_URL, data={
        "client_key": key, "client_secret": sec,
        "grant_type": "refresh_token",
        "refresh_token": d.get("refresh_token", ""),
    }, headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=30)
    nd = r.json()
    if "access_token" not in nd:
        print(f"  [tiktok] token refresh failed: {nd} — run: python main.py tiktok-auth")
        return None
    _save_tokens(cfg, nd)
    return nd["access_token"]


def account_info(cfg: Config) -> dict | None:
    tok = get_access_token(cfg)
    if not tok:
        return None
    r = requests.get(f"{API}/user/info/",
                     params={"fields": "open_id,display_name"},
                     headers={"Authorization": f"Bearer {tok}"}, timeout=30)
    return (r.json().get("data") or {}).get("user") if r.ok else None


def upload_video(cfg: Config, video_path: str | Path, title: str) -> str | None:
    """Direct-post a video. Returns the publish_id on success."""
    tok = get_access_token(cfg)
    if not tok:
        print("  [tiktok] not authorized — run: python main.py tiktok-auth")
        return None
    video_path = Path(video_path)
    if not video_path.exists():
        print(f"  [tiktok] file not found: {video_path}")
        return None
    data = video_path.read_bytes()
    size = len(data)
    privacy = cfg.get("tiktok.privacy_level", "SELF_ONLY")

    # 1) init the direct post (single chunk for videos <= 64MB)
    r = requests.post(f"{API}/post/publish/video/init/", headers={
        "Authorization": f"Bearer {tok}",
        "Content-Type": "application/json; charset=UTF-8",
    }, json={
        "post_info": {
            "title": title[:2200],
            "privacy_level": privacy,
            "disable_comment": False,
            "disable_duet": False,
            "disable_stitch": False,
        },
        "source_info": {
            "source": "FILE_UPLOAD",
            "video_size": size,
            "chunk_size": size,
            "total_chunk_count": 1,
        },
    }, timeout=60)
    d = r.json()
    if (d.get("error") or {}).get("code") not in (None, "ok"):
        print(f"  [tiktok] init failed: {d.get('error')}")
        return None
    publish_id = (d.get("data") or {}).get("publish_id")
    upload_url = (d.get("data") or {}).get("upload_url")
    if not publish_id or not upload_url:
        print(f"  [tiktok] unexpected init response: {d}")
        return None

    # 2) upload the binary
    r = requests.put(upload_url, headers={
        "Content-Type": "video/mp4",
        "Content-Range": f"bytes 0-{size - 1}/{size}",
    }, data=data, timeout=900)
    if r.status_code not in (200, 201):
        print(f"  [tiktok] upload failed: HTTP {r.status_code} {r.text[:200]}")
        return None
    print(f"  [tiktok] uploaded {size // 1024} KB — processing...")

    # 3) poll status
    for _ in range(60):
        r = requests.post(f"{API}/post/publish/status/fetch/", headers={
            "Authorization": f"Bearer {tok}",
            "Content-Type": "application/json; charset=UTF-8",
        }, json={"publish_id": publish_id}, timeout=30)
        st = (r.json().get("data") or {}).get("status", "")
        if st == "PUBLISH_COMPLETE":
            print(f"  [tiktok] published! (privacy: {privacy}) publish_id={publish_id}")
            return publish_id
        if st in ("FAILED",):
            print(f"  [tiktok] publish failed: {r.json()}")
            return None
        time.sleep(5)
    print("  [tiktok] processing timed out (check the TikTok app — it may still land)")
    return None
