"""Upload finished videos to YouTube via the YouTube Data API v3.

Uploads the video, sets the auto-generated thumbnail, and attaches the .srt
closed captions (captions boost accessibility and search indexing).

Setup (once):
  1. Google Cloud Console -> create project -> enable "YouTube Data API v3".
  2. Create OAuth client credentials (type: Desktop app).
  3. Download as client_secret.json into the project root.
  4. Set youtube.enabled: true in config.yaml.
  5. First run opens a browser to authorize; a token is cached in token.json.

Quota notes: each upload costs ~1600 quota units of the default 10,000/day —
roughly 6 uploads/day max on the default quota. A daily short + long is fine.
"""
from __future__ import annotations

from pathlib import Path
import re

from .config import Config

# force-ssl scope is required for captions; it also covers uploads/thumbnails.
SCOPES = ["https://www.googleapis.com/auth/youtube.force-ssl"]
ROOT = Path(__file__).resolve().parent.parent


def _get_service(cfg: Config):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    token_path = ROOT / "token.json"
    secret_path = ROOT / "client_secret.json"
    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not secret_path.exists():
                raise FileNotFoundError(
                    "client_secret.json not found in the project root. "
                    "See src/youtube_upload.py setup notes (or README)."
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(secret_path), SCOPES)
            creds = flow.run_local_server(port=0)
        token_path.write_text(creds.to_json(), encoding="utf-8")
    return build("youtube", "v3", credentials=creds)


def test_auth(cfg: Config) -> bool:
    """Verify OAuth + API access with a read-only call. Uploads NOTHING.

    Runs the browser authorization on first use (cached in token.json), then
    asks the API which channel these credentials belong to (1 quota unit).
    """
    try:
        service = _get_service(cfg)
    except Exception as exc:  # noqa: BLE001
        print(f"AUTH FAILED: {exc}")
        return False
    try:
        resp = service.channels().list(part="snippet,statistics", mine=True).execute()
        items = resp.get("items", [])
        if not items:
            print("Auth OK, but no channel found on this Google account.")
            print("Make sure you picked the account that owns your YouTube channel.")
            return False
        ch = items[0]
        sn, st = ch["snippet"], ch.get("statistics", {})
        print("AUTH OK — connected channel:")
        print(f"  Name       : {sn.get('title')}")
        print(f"  Channel ID : {ch.get('id')}")
        print(f"  Subscribers: {st.get('subscriberCount', '?')}")
        print(f"  Videos     : {st.get('videoCount', '?')}")
        print("Nothing was uploaded. Token cached in token.json for future runs.")
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"API CALL FAILED: {exc}")
        print("Check that 'YouTube Data API v3' is enabled for this Cloud project.")
        return False


def next_publish_slot(cfg: Config) -> str | None:
    """Next free go-live slot (youtube.schedule.times, local timezone) as an
    ISO-8601 UTC string for publishAt. Slots taken by earlier uploads are
    remembered in output/schedule_state.json, so two shorts uploaded in the
    same morning take 12:00 and 18:00 in order."""
    import json
    from datetime import datetime, timedelta, timezone as tz_utc
    try:
        from zoneinfo import ZoneInfo
        zone = ZoneInfo(cfg.get("youtube.schedule.timezone", "America/Sao_Paulo"))
    except Exception as exc:  # noqa: BLE001
        # NEVER silently degrade to unscheduled uploads: fall back to a fixed
        # UTC-3 offset (Brasília has no DST since 2019). This bit us once when
        # tzdata went missing and a whole morning run lost its schedule.
        print(f"  [youtube] tzdata unavailable ({exc}) — using fixed UTC-3 fallback")
        zone = tz_utc(timedelta(hours=-3))
    times = cfg.get("youtube.schedule.times", ["12:00", "18:00"]) or []
    if not times:
        return None

    state_path = cfg.output_dir / "schedule_state.json"
    taken: list[str] = []
    if state_path.exists():
        try:
            taken = json.loads(state_path.read_text(encoding="utf-8")).get("taken", [])
        except Exception:  # noqa: BLE001
            taken = []

    now = datetime.now(zone)
    # prune past reservations
    taken = [t for t in taken
             if datetime.fromisoformat(t) > now.astimezone(tz_utc.utc) - timedelta(hours=1)]

    margin = now + timedelta(minutes=20)  # YouTube needs review margin
    hourly = bool(cfg.get("youtube.schedule.hourly_fallback", True))
    parsed = []
    for hhmm in times:
        try:
            h, m = (int(x) for x in str(hhmm).split(":"))
        except ValueError:
            print(f"  [youtube] ignoring malformed schedule time {hhmm!r}")
            continue
        parsed.append((h, m))
    if not parsed:
        return None

    def candidates(day: int) -> list[datetime]:
        """Slots to try for `day`, in order of preference.

        A missed slot is replaced by ONE substitute — the next whole hour that
        still fits before the following configured slot. One substitute, not
        the whole range: offering 13:00..17:00 would make a late run put both
        of the day's videos back to back (13:00 and 14:00) instead of keeping
        the 12:00/18:00 spread, which is the entire point of the two slots.
        The retry ladder makes this routine: a run that only succeeds on the
        11:30 attempt can no longer reach 12:00, and 13:00 beats waiting for
        18:00 or (worse) tomorrow.
        """
        date = (now + timedelta(days=day)).date()
        out: list[datetime] = []
        for i, (h, m) in enumerate(parsed):
            local = datetime(date.year, date.month, date.day, h, m, tzinfo=zone)
            if local >= margin:
                out.append(local)
                continue
            if not hourly or day != 0:
                continue          # a past slot on a future day cannot happen
            if i + 1 < len(parsed):
                nh, nm = parsed[i + 1]
                limit = datetime(date.year, date.month, date.day, nh, nm, tzinfo=zone)
            else:
                limit = datetime(date.year, date.month, date.day,
                                 tzinfo=zone) + timedelta(days=1)
            sub = margin.replace(minute=0, second=0, microsecond=0)
            if sub < margin:
                sub += timedelta(hours=1)
            if sub < limit and sub not in out:
                out.append(sub)
        return out

    for day in range(0, 8):
        for local in candidates(day):
            iso_utc = local.astimezone(tz_utc.utc).replace(microsecond=0).isoformat()
            if iso_utc in taken:
                continue
            taken.append(iso_utc)
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(json.dumps({"taken": taken}, indent=2), encoding="utf-8")
            if (local.hour, local.minute) not in parsed:
                print(f"  [youtube] {parsed[0][0]:02d}:{parsed[0][1]:02d} slot "
                      f"no longer reachable — using {local:%H:%M} instead")
            return iso_utc
    return None


def _fill_tag_budget(cfg: Config, video_tags: list[str] | None, is_short: bool) -> list[str]:
    """Fill YouTube's tag budget (500 chars TOTAL across all tags) to the brim.

    Priority: video-specific tags (strongest relevance signal) -> default_tags
    -> the curated tag_pool, until the character budget is spent. Tags with
    spaces cost 2 extra chars (YouTube quotes them internally), so we account
    for that and stop at a safe 470.
    """
    budget = 470
    spent = 0
    seen: set[str] = set()
    final: list[str] = []

    def sanitize(tag: str) -> str:
        # Keep tags conservative/portable for the YouTube API.
        t = (tag or "").strip().lower().replace("#", "")
        t = re.sub(r"[\r\n\t]+", " ", t)
        t = re.sub(r"[^a-z0-9 _-]", "", t)
        t = re.sub(r"\s+", " ", t).strip(" ,-_")
        # Keep single tag length short to avoid API rejections.
        return t[:30]

    def add(tag: str) -> None:
        nonlocal spent
        t = sanitize(tag)
        if len(t) < 2 or t in seen:
            return
        # YouTube counts: tag chars + 2 quote chars for spaced tags + a
        # separator per tag. Under-counting by the separators got a real
        # upload's tags rejected at 501/500 chars.
        cost = len(t) + (2 if " " in t else 0) + 1
        if spent + cost > budget:
            return
        seen.add(t)
        final.append(t)
        spent += cost

    if is_short:
        add("shorts")
    for t in video_tags or []:
        add(t)
    for t in cfg.get("youtube.default_tags", []) or []:
        add(t)
    for t in cfg.get("youtube.tag_pool", []) or []:
        add(t)
    return final


_LINK_LABELS = {"site": "🌐 App", "youtube": "▶️ YouTube", "instagram": "📸 Instagram",
                "tiktok": "🎵 TikTok", "linkedin": "💼 LinkedIn"}


def channel_links_block(cfg: Config) -> str:
    """Official links as description lines, in the order declared in config.

    The app link goes FIRST and alone, carrying its UTM tag; the profile links
    follow after a blank line. In a YouTube description the first link takes
    almost every click, and four profile links directly beneath it only
    compete with the one that leads to the product.
    """
    from .links import cta_block, social_block
    return "\n\n".join(x for x in (cta_block(cfg, "youtube", content="short"),
                                   social_block(cfg)) if x)


def build_metadata(cfg: Config, title: str, description: str | None,
                   tags: list[str] | None, links: list[str] | None,
                   chapters: list[tuple[float, str]] | None,
                   is_short: bool,
                   synthetic_media: bool = False) -> dict:
    topic = cfg.get("theme.topic", "")
    tagline = cfg.get("channel.brand_tagline", "")

    if description is None:
        template = cfg.get("youtube.description_template", "{title}\n\n{tagline}")
        fields = {"title": title, "topic": topic, "tagline": tagline,
                  "links": channel_links_block(cfg)}
        try:
            description = template.format(**fields)
        except KeyError as exc:
            # A typo'd placeholder in config must never abort a finished upload.
            print(f"  [youtube] unknown placeholder {exc} in description_template "
                  f"— using the raw template")
            description = template

    # Chapters (long videos): "0:00 Intro" lines make YouTube segment the video.
    if chapters:
        lines = ["", "⏱ Chapters:"]
        for t, name in chapters:
            m, s = int(t // 60), int(t % 60)
            lines.append(f"{m}:{s:02d} {name[:80]}")
        description += "\n" + "\n".join(lines)

    if links:
        description += "\n\nSources:\n" + "\n".join(links[:12])

    if is_short:
        description += "\n\n#Shorts"

    all_tags = _fill_tag_budget(cfg, tags, is_short)

    privacy = cfg.get("youtube.privacy", "private")
    status: dict = {"selfDeclaredMadeForKids": bool(cfg.get("youtube.made_for_kids", False))}
    if synthetic_media:
        # Realistic AI-generated imagery: YouTube asks creators to disclose it
        # (the "altered or synthetic content" label). Set automatically on the
        # days the carousel used an AI photo — see src/ai_image.py.
        status["containsSyntheticMedia"] = True
    if privacy == "scheduled":
        slot = next_publish_slot(cfg)
        if slot:
            # YouTube scheduling = private + publishAt; auto-publishes then.
            status["privacyStatus"] = "private"
            status["publishAt"] = slot
            print(f"  [youtube] scheduled to go PUBLIC at {slot} (UTC)")
        else:
            print("  [youtube] no schedule slot available — uploading as private")
            status["privacyStatus"] = "private"
    else:
        status["privacyStatus"] = privacy

    return {
        "snippet": {
            "title": title[:100],
            "description": description[:4900],
            "tags": all_tags,
            "categoryId": str(cfg.get("youtube.category_id", "20")),
            "defaultLanguage": cfg.get("channel.language", "en"),
            "defaultAudioLanguage": cfg.get("channel.language", "en"),
        },
        "status": status,
    }


def upload_video(cfg: Config, video_path: Path, title: str, *,
                 description: str | None = None,
                 tags: list[str] | None = None,
                 links: list[str] | None = None,
                 chapters: list[tuple[float, str]] | None = None,
                 thumbnail: Path | None = None,
                 captions_srt: Path | None = None,
                 is_short: bool = False,
                 synthetic_media: bool = False) -> str | None:
    if not cfg.get("youtube.enabled", False):
        print("  [youtube] upload disabled (set youtube.enabled: true in config.yaml).")
        return None
    try:
        from googleapiclient.errors import HttpError
        from googleapiclient.http import MediaFileUpload
        service = _get_service(cfg)
        body = build_metadata(cfg, title, description, tags, links, chapters, is_short, synthetic_media=synthetic_media)

        def run_insert(req):
            resp = None
            while resp is None:
                status, resp = req.next_chunk()
                if status:
                    print(f"  [youtube] uploading… {int(status.progress() * 100)}%")
            return resp

        media = MediaFileUpload(str(video_path), chunksize=-1, resumable=True,
                                mimetype="video/mp4")
        request = service.videos().insert(part="snippet,status", body=body, media_body=media)
        try:
            response = run_insert(request)
        except HttpError as exc:
            # Some generated tags can still violate YouTube validation.
            if "invalidTags" in str(exc) and body.get("snippet", {}).get("tags"):
                print("  [youtube] tags rejected by API; retrying upload without tags.")
                body["snippet"]["tags"] = []
                media = MediaFileUpload(str(video_path), chunksize=-1, resumable=True,
                                        mimetype="video/mp4")
                request = service.videos().insert(part="snippet,status", body=body, media_body=media)
                response = run_insert(request)
            else:
                raise

        vid = response.get("id")
        print(f"  [youtube] uploaded: https://youtu.be/{vid}")

        # Custom thumbnail (long-form; ignored by YouTube for Shorts surfaces).
        if thumbnail and thumbnail.exists():
            try:
                service.thumbnails().set(
                    videoId=vid,
                    media_body=MediaFileUpload(str(thumbnail), mimetype="image/jpeg"),
                ).execute()
                print("  [youtube] thumbnail set.")
            except Exception as exc:  # noqa: BLE001
                print(f"  [youtube] thumbnail failed (channel may need verification): {exc}")

        # Closed captions from the word-timed SRT.
        if captions_srt and captions_srt.exists() and cfg.get("youtube.upload_captions", True):
            try:
                service.captions().insert(
                    part="snippet",
                    body={"snippet": {
                        "videoId": vid,
                        "language": cfg.get("channel.language", "en"),
                        "name": "",
                        "isDraft": False,
                    }},
                    media_body=MediaFileUpload(str(captions_srt), mimetype="application/octet-stream"),
                ).execute()
                print("  [youtube] captions attached.")
            except Exception as exc:  # noqa: BLE001
                print(f"  [youtube] captions failed: {exc}")

        # First comment from the channel — early comment velocity is a strong
        # ranking signal, and it seeds the discussion (pin it in the app for
        # extra effect; the API can't pin). YouTube does NOT allow comments on
        # PRIVATE videos, so this only fires on public/unlisted uploads.
        template = cfg.get("youtube.first_comment", "")
        if template and cfg.get("youtube.privacy", "private") in ("private", "scheduled"):
            print("  [youtube] first comment skipped (video not public yet — "
                  "YouTube blocks comments until it goes live)")
            template = ""
        if template:
            try:
                text = template.format(title=title, topic=cfg.get("theme.topic", ""))
                service.commentThreads().insert(
                    part="snippet",
                    body={"snippet": {
                        "videoId": vid,
                        "topLevelComment": {"snippet": {"textOriginal": text[:900]}},
                    }},
                ).execute()
                print("  [youtube] first comment posted.")
            except Exception as exc:  # noqa: BLE001
                print(f"  [youtube] first comment failed: {exc}")

        return vid
    except Exception as exc:  # noqa: BLE001
        print(f"  [youtube] upload failed: {exc}")
        return None
