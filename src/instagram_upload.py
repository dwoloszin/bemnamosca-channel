"""Publish Shorts as Instagram Reels (Instagram API with Instagram Login).

Uses the resumable upload protocol — the video binary is sent directly, no
public URL hosting needed. The long-lived token (60 days) is auto-refreshed
and persisted in output/instagram_token.json, so after the first setup the
integration never expires as long as it's used at least once every 60 days.

Reels go LIVE immediately when published (Instagram's API has no scheduling /
private mode), so auto-posting is opt-in via config (instagram.enabled).
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import requests

from .config import Config

API = "https://graph.instagram.com/v21.0"


def _print_api_error(prefix: str, response: requests.Response) -> None:
    """Print the raw API error plus a concrete hint for the common Meta block."""
    text = response.text[:250]
    print(f"  [instagram] {prefix}: {text}")
    try:
        payload = response.json() or {}
    except Exception:  # noqa: BLE001
        payload = {}
    err = payload.get("error") if isinstance(payload, dict) else None
    if not isinstance(err, dict):
        return
    msg = str(err.get("message", ""))
    code = err.get("code")
    if code == 200 and "API access blocked" in msg:
        print("  [instagram] Meta is blocking this app/token before publish.")
        print("  [instagram] Check app Live status, Instagram API access, and re-authorize the token.")
        print("  [instagram] If you issued a fresh token, delete output/instagram_token.json so the new seed is used.")


# --------------------------------------------------------------------------
# token management (auto-refresh, persisted)
# --------------------------------------------------------------------------
def _token_state_path(cfg: Config) -> Path:
    return cfg.output_dir / "instagram_token.json"


def get_token(cfg: Config) -> str | None:
    """Current token: persisted refreshed one if present, else .env seed.
    Auto-refreshes when older than 30 days (tokens last 60)."""
    seed = (os.getenv("INSTAGRAM_ACCESS_TOKEN") or "").strip()
    state_path = _token_state_path(cfg)
    token, refreshed_at = seed, 0.0
    if state_path.exists():
        try:
            d = json.loads(state_path.read_text(encoding="utf-8"))
            # A token pasted into .env must win over the persisted one. The
            # cached file used to take precedence unconditionally, so after a
            # password change — which invalidates every existing token — a
            # freshly generated seed was ignored and every upload kept failing
            # with the dead cached token until the file was deleted by hand.
            if d.get("seed") and seed and d["seed"] != seed:
                print("  [instagram] new token found in .env — discarding the cached one")
                state_path.unlink(missing_ok=True)
            else:
                token = d.get("token") or seed
                refreshed_at = float(d.get("refreshed_at", 0))
        except Exception:  # noqa: BLE001
            pass
    if not token:
        return None
    if time.time() - refreshed_at > 30 * 86400:
        try:
            r = requests.get("https://graph.instagram.com/refresh_access_token",
                             params={"grant_type": "ig_refresh_token",
                                     "access_token": token}, timeout=30)
            if r.ok:
                token = r.json()["access_token"]
                state_path.parent.mkdir(parents=True, exist_ok=True)
                state_path.write_text(json.dumps(
                    {"token": token, "refreshed_at": time.time(),
                     "seed": seed}, indent=2),
                    encoding="utf-8")
                print("  [instagram] token refreshed (+60 days)")
        except Exception as exc:  # noqa: BLE001
            print(f"  [instagram] token refresh failed ({exc}) — using current token")
    return token


def account_info(cfg: Config) -> dict | None:
    tok = get_token(cfg)
    if not tok:
        return None
    r = requests.get(f"{API}/me", params={
        "fields": "user_id,username,account_type", "access_token": tok}, timeout=30)
    if r.ok:
        return r.json()
    _print_api_error("account lookup failed", r)
    return None


# --------------------------------------------------------------------------
# temporary public hosting (Instagram requires a public video_url; the file
# lives on GitHub only for the minutes Instagram needs to fetch it)
# --------------------------------------------------------------------------
_MEDIA_REPO = "bemnamosca-media"


def _gh_headers() -> dict | None:
    tok = (os.getenv("DB_ARCHIVE_GITHUB_TOKEN") or "").strip()
    if not tok:
        return None
    return {"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json"}


def _gh_user(h: dict) -> str | None:
    r = requests.get("https://api.github.com/user", headers=h, timeout=30)
    return r.json().get("login") if r.ok else None


def _git_force_push(repo: str, files: dict[str, Path | str], message: str) -> bool:
    """Force-push an ORPHAN single commit containing exactly `files` to main.
    Every staging replaces the whole history, so the public repo never grows —
    videos vanish from it the moment cleanup pushes the empty commit."""
    import shutil
    import subprocess
    import tempfile
    tok = (os.getenv("DB_ARCHIVE_GITHUB_TOKEN") or "").strip()
    tmp = Path(tempfile.mkdtemp(prefix="igstage_"))
    try:
        for name, src in files.items():
            dest = tmp / name
            if isinstance(src, Path):
                shutil.copyfile(src, dest)
            else:
                dest.write_text(src, encoding="utf-8")
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
        def git(*args):
            r = subprocess.run(["git", *args], cwd=tmp, capture_output=True,
                               text=True, env=env, timeout=600)
            if r.returncode != 0:
                raise RuntimeError(r.stderr[-300:])
        git("init", "-b", "main")
        git("config", "user.email", "bot@bemnamosca.local")
        git("config", "user.name", "bemnamosca-bot")
        git("add", "-A")
        git("commit", "-m", message)
        git("push", "--force",
            f"https://x-access-token:{tok}@github.com/{repo}.git", "main:main")
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"  [instagram] git staging failed: {exc}")
        return False
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _stageable(video_path: Path) -> Path:
    """A copy that fits GitHub's 100 MB push limit, when the original does not.

    26/08: videosPromocional.mp4 (158 MB) hit the limit and the Reel silently
    became a post-by-hand. A CRF-26 re-encode at the same resolution is
    indistinguishable on a phone and lands well under the cap; the transcode
    is cached next to the work files so a retry does not pay it again."""
    limit = 95 * 1024 * 1024
    if video_path.stat().st_size <= limit:
        return video_path
    from .ffmpeg import ffmpeg_exe
    import subprocess
    out = video_path.parent / f"_stage_{video_path.stem}.mp4"
    if out.exists() and 0 < out.stat().st_size <= limit:
        return out
    print(f"  [instagram] {video_path.name} is "
          f"{video_path.stat().st_size // 1048576} MB (> 95 MB git limit) — compressing a staging copy")
    r = subprocess.run([ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error",
                        "-i", str(video_path), "-c:v", "libx264", "-preset", "fast",
                        "-crf", "26", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k",
                        "-movflags", "+faststart", str(out)],
                       capture_output=True, text=True, timeout=900)
    if r.returncode != 0 or not out.exists():
        print("  [instagram] compression failed — trying the original anyway")
        return video_path
    print(f"  [instagram] staging copy: {out.stat().st_size // 1048576} MB")
    return out


def _host_temp_video(video_path: Path) -> tuple[str, str, str] | None:
    """Stage the mp4 in the public media repo via a real git push (the JSON
    APIs cap out far below video sizes). Returns (raw_url, repo, filename)."""
    video_path = _stageable(Path(video_path))
    h = _gh_headers()
    if not h:
        print("  [instagram] DB_ARCHIVE_GITHUB_TOKEN missing (needed for temp hosting)")
        return None
    user = _gh_user(h)
    if not user:
        return None
    repo = f"{user}/{_MEDIA_REPO}"

    # ensure the repo exists (idempotent)
    r = requests.get(f"https://api.github.com/repos/{repo}", headers=h, timeout=30)
    if r.status_code == 404:
        requests.post("https://api.github.com/user/repos", headers=h, json={
            "name": _MEDIA_REPO, "private": False, "auto_init": True,
            "description": "Temporary staging for Reels uploads (files are deleted after publishing)",
        }, timeout=30)
        time.sleep(2)

    name = f"{int(time.time())}_{video_path.name}"
    readme = "# Staging\nTemporary staging for Reels uploads; files are removed after publishing.\n"
    if not _git_force_push(repo, {name: video_path, "README.md": readme}, "stage reel"):
        return None
    raw_url = f"https://raw.githubusercontent.com/{repo}/main/{name}"
    return raw_url, repo, name


def _delete_temp_video(repo: str, name: str) -> None:
    """Replace the repo content with an empty commit — the video disappears
    from the branch (and from raw URLs) immediately."""
    readme = "# Staging\nTemporary staging for Reels uploads; files are removed after publishing.\n"
    _git_force_push(repo, {"README.md": readme}, "cleanup staged reel")


# --------------------------------------------------------------------------
# Reels publishing
# --------------------------------------------------------------------------
def upload_reel(cfg: Config, video_path: str | Path, caption: str,
                *, publish: bool = True) -> str | None:
    """Publish a 9:16 MP4 as a Reel. The file is staged at a public URL just
    long enough for Instagram to fetch it, then deleted. publish=False stops
    before going live (container discarded by IG after 24h)."""
    tok = get_token(cfg)
    if not tok:
        print("  [instagram] no INSTAGRAM_ACCESS_TOKEN configured")
        return None
    video_path = Path(video_path)
    if not video_path.exists():
        print(f"  [instagram] file not found: {video_path}")
        return None

    retries = max(1, int(cfg.get("instagram.processing_retries", 2)))
    last_status: str | None = None
    last_payload: dict | None = None
    last_url: str | None = None

    for attempt in range(1, retries + 1):
        hosted = _host_temp_video(video_path)
        if not hosted:
            return None
        video_url, repo, name = hosted
        last_url = video_url
        print(f"  [instagram] staged at {video_url}")

        # Give GitHub raw a brief moment to propagate before IG fetches it.
        ok_probe = False
        for _ in range(6):  # up to ~15s
            try:
                probe = requests.head(video_url, timeout=15)
                if probe.ok:
                    ok_probe = True
                    break
            except Exception:  # noqa: BLE001
                pass
            time.sleep(2.5)
        if not ok_probe:
            print("  [instagram] warning: staged URL did not answer HEAD yet; continuing.")

        # 1) create media container from staged URL
        r = requests.post(f"{API}/me/media", data={
            "media_type": "REELS",
            "video_url": video_url,
            "caption": caption[:2200],
            "access_token": tok,
        }, timeout=120)
        if not r.ok:
            _print_api_error("container failed", r)
            _delete_temp_video(repo, name)
            print("  [instagram] staged file deleted")
            return None

        container = r.json().get("id")
        if not container:
            _delete_temp_video(repo, name)
            print("  [instagram] staged file deleted")
            return None
        print("  [instagram] container created — Instagram is fetching/processing...")

        # 2) poll processing status
        status = None
        payload: dict | None = None
        for _ in range(90):  # up to ~7.5 min
            r = requests.get(f"{API}/{container}", params={
                "fields": "status_code,status,error_message", "access_token": tok}, timeout=30)
            if r.ok:
                payload = r.json() or {}
                status = payload.get("status_code") or payload.get("status")
            else:
                status = None
            if status in ("FINISHED", "ERROR", "EXPIRED"):
                break
            time.sleep(5)

        if status == "FINISHED":
            _delete_temp_video(repo, name)
            print("  [instagram] staged file deleted")

            if not publish:
                print("  [instagram] dry run OK (container ready; NOT published)")
                return container

            # 3) go live
            r = requests.post(f"{API}/me/media_publish", data={
                "creation_id": container, "access_token": tok}, timeout=60)
            if not r.ok:
                _print_api_error("publish failed", r)
                return None
            media_id = r.json().get("id")
            print(f"  [instagram] Reel LIVE — media id {media_id}")
            return media_id

        # Non-finished processing path.
        last_status, last_payload = status, payload
        if attempt < retries:
            print(f"  [instagram] processing attempt {attempt}/{retries} ended as {status}; retrying...")
            _delete_temp_video(repo, name)
            print("  [instagram] staged file deleted")
            time.sleep(4)
            continue

        # Final attempt failed: keep URL for manual inspection.
        if status == "ERROR":
            print(f"  [instagram] processing failed detail: {payload}")
        print("  [instagram] keeping staged file for debugging/retry:")
        print(f"  [instagram] {video_url}")

    print(f"  [instagram] processing did not finish (status: {last_status})")
    if last_payload:
        print(f"  [instagram] last status payload: {last_payload}")
    if last_url:
        print(f"  [instagram] last staged URL: {last_url}")
    return None


def upload_photo(cfg: Config, image_path: str | Path, caption: str,
                 *, publish: bool = True) -> str | None:
    """Publish a single image as an Instagram feed post. Same staging flow as
    Reels (temporary public URL -> container -> publish); images process in
    seconds. publish=False stops before going live."""
    tok = get_token(cfg)
    if not tok:
        print("  [instagram] no INSTAGRAM_ACCESS_TOKEN configured")
        return None
    image_path = Path(image_path)
    if not image_path.exists():
        print(f"  [instagram] file not found: {image_path}")
        return None

    hosted = _host_temp_video(image_path)  # works for any binary file
    if not hosted:
        return None
    image_url, repo, name = hosted
    print(f"  [instagram] staged at {image_url}")

    for _ in range(6):  # let GitHub raw propagate (~15s max)
        try:
            if requests.head(image_url, timeout=15).ok:
                break
        except Exception:  # noqa: BLE001
            pass
        time.sleep(2.5)

    try:
        r = requests.post(f"{API}/me/media", data={
            "image_url": image_url,
            "caption": caption[:2200],
            "access_token": tok,
        }, timeout=120)
        if not r.ok:
            _print_api_error("container failed", r)
            return None
        container = r.json().get("id")
        if not container:
            return None

        status = None
        for _ in range(24):  # up to ~1 min — images are fast
            r = requests.get(f"{API}/{container}", params={
                "fields": "status_code,status,error_message", "access_token": tok}, timeout=30)
            payload = r.json() if r.ok else {}
            status = payload.get("status_code") or payload.get("status")
            if status in ("FINISHED", "ERROR", "EXPIRED"):
                break
            time.sleep(2.5)
        if status != "FINISHED":
            print(f"  [instagram] image processing ended as {status}: {payload}")
            return None

        if not publish:
            print("  [instagram] dry run OK (container ready; NOT published)")
            return container
        r = requests.post(f"{API}/me/media_publish", data={
            "creation_id": container, "access_token": tok}, timeout=60)
        if not r.ok:
            _print_api_error("publish failed", r)
            return None
        media_id = r.json().get("id")
        print(f"  [instagram] photo post LIVE — media id {media_id}")
        return media_id
    finally:
        _delete_temp_video(repo, name)
        print("  [instagram] staged file deleted")


def caption_from_post_package(pkg: Path, cfg: Config) -> str:
    """Instagram caption from a community-post package (post.txt already has
    the hook, facts, engagement question and hashtags)."""
    text = ""
    try:
        text = (pkg / "post.txt").read_text(encoding="utf-8").strip()
    except Exception:  # noqa: BLE001
        pass
    follow = cfg.get("instagram.caption_suffix", "Follow for daily GTA 6 news!")
    return f"{text}\n\n{follow}".strip()


def upload_post_package(cfg: Config, pkg: str | Path,
                        *, publish: bool = True) -> str | None:
    """Publish a community-post package (image.jpg + post.txt) as an
    Instagram feed photo post."""
    pkg = Path(pkg)
    image = pkg / "image.jpg"
    if not image.exists():
        print(f"  [instagram] no image.jpg in {pkg}")
        return None
    caption = caption_from_post_package(pkg, cfg)
    print(f"  [instagram] posting package {pkg.name} to Instagram feed...")
    return upload_photo(cfg, image, caption, publish=publish)


def caption_from_metadata(meta_path: Path, cfg: Config) -> str:
    """Build a Reels caption from a video's .json sidecar (title + hashtags)."""
    title, tags = "", []
    try:
        d = json.loads(meta_path.read_text(encoding="utf-8"))
        title = d.get("title", "")
        tags = d.get("tags", [])[:12]
    except Exception:  # noqa: BLE001
        pass
    hashtags = " ".join("#" + t.replace(" ", "").replace("-", "")[:28]
                        for t in tags if t)
    follow = cfg.get("instagram.caption_suffix",
                     "Follow for daily GTA 6 news!")
    return f"{title}\n\n{follow}\n\n{hashtags}".strip()
