"""Render the final MP4 with ffmpeg: slideshow + Ken Burns + burned subtitles
+ new voiceover + royalty-free music + watermark.

No original audio from any source is ever kept — a brand-new soundtrack (TTS
voice + licensed music) is composited, which is a core copyright-avoidance step.
"""
from __future__ import annotations

import random
import subprocess
from pathlib import Path

from . import ffmpeg
from .config import Config
from .media import is_video


def _run_cwd(args: list[str], cwd: Path | None = None) -> None:
    cmd = [ffmpeg.ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error"] + args
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(cwd) if cwd else None)
    if proc.returncode != 0:
        raise RuntimeError("ffmpeg failed:\n" + " ".join(cmd) + "\n\n" + proc.stderr[-4000:])


def _video_slide_clip(src: Path, dur: float, w: int, h: int, fps: int, out: Path,
                      start: float = 0.0) -> Path:
    """Turn a source VIDEO into a slide: play a continuous segment starting at
    `start`, cover-crop to the frame, and DROP its original audio (-an) —
    narration/music replace it. Looping only happens as a last resort when the
    whole clip is shorter than the slot."""
    src_dur = ffmpeg.probe_duration(src)
    args: list[str] = []
    if start > 0.01:
        args += ["-ss", f"{start:.3f}"]
    elif 0 < src_dur < dur:
        args += ["-stream_loop", "-1"]          # safety net for short clips
    args += ["-i", str(src)]
    vf = (
        f"scale={w}:{h}:force_original_aspect_ratio=increase,"
        f"crop={w}:{h},setsar=1,fps={fps},format=yuv420p"
    )
    _run_cwd(args + [
        "-t", f"{dur:.3f}", "-vf", vf, "-an", "-dn", "-sn",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p", str(out),
    ])
    return out


def _slide_clip(img: Path, dur: float, w: int, h: int, fps: int,
                ken_burns: bool, out: Path, index: int) -> Path:
    if ken_burns:
        # Fast Ken-Burns-style motion: upscale once, then slide a crop window
        # across the frame. This is ~120x faster than ffmpeg's zoompan (which
        # makes multi-minute videos take hours) and reads as smooth motion.
        up = 1.15
        su, sh = int(w * up), int(h * up)
        # Four alternating pan directions for variety (start->end as fractions).
        dirs = [((0, 0), (1, 1)), ((1, 1), (0, 0)), ((1, 0), (0, 1)), ((0, 1), (1, 0))]
        (sx0, sy0), (sx1, sy1) = dirs[index % 4]
        xexpr = f"(iw-{w})*({sx0}+({sx1 - sx0})*(t/{dur:.3f}))"
        yexpr = f"(ih-{h})*({sy0}+({sy1 - sy0})*(t/{dur:.3f}))"
        vf = (
            f"scale={su}:{sh},crop={w}:{h}:x='{xexpr}':y='{yexpr}',"
            f"setsar=1,format=yuv420p"
        )
    else:
        vf = f"scale={w}:{h},setsar=1,format=yuv420p"

    # Intermediate clips are re-encoded in the final mux anyway, so use a fast
    # preset here — quality is set at the final stage.
    _run_cwd([
        "-loop", "1", "-t", f"{dur:.3f}", "-i", str(img),
        "-vf", vf, "-r", str(fps),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p", str(out),
    ])
    return out


def _concat_clips(clips: list[Path], out: Path, workdir: Path) -> Path:
    listfile = workdir / "concat.txt"
    listfile.write_text(
        "\n".join(f"file '{c.as_posix()}'" for c in clips) + "\n", encoding="utf-8"
    )
    _run_cwd([
        "-f", "concat", "-safe", "0", "-i", str(listfile),
        "-c", "copy", str(out),
    ])
    return out


# xfade transitions that read well on a fast vertical cut. Deliberately no
# spins/zooms: they draw attention to the edit instead of the content.
_TRANSITIONS = ["fade", "fadeblack", "dissolve", "wipeleft", "wiperight",
                "slideup", "slidedown", "smoothleft", "smoothright",
                "circleopen", "radial", "pixelize"]


def _concat_clips_xfade(clips: list[Path], durations: list[float], out: Path,
                        workdir: Path, cross: float, rng: random.Random) -> Path:
    """Concatenate with a DIFFERENT random transition at every cut.

    xfade overlaps the two clips, so each transition eats `cross` seconds of
    total runtime. The caller pads the clips beforehand; here we only have to
    place each xfade at the right offset, which is the running length of
    everything already merged.
    """
    if len(clips) < 2:
        return _concat_clips(clips, out, workdir)

    args: list[str] = []
    for c in clips:
        args += ["-i", str(c)]

    filters: list[str] = []
    prev = "[0:v]"
    elapsed = durations[0]
    for i in range(1, len(clips)):
        name = rng.choice(_TRANSITIONS)
        offset = max(0.0, elapsed - cross)
        label = f"[x{i}]" if i < len(clips) - 1 else "[vout]"
        filters.append(f"{prev}[{i}:v]xfade=transition={name}:"
                       f"duration={cross:.3f}:offset={offset:.3f}{label}")
        prev = label
        elapsed = offset + cross + (durations[i] - cross)
    _run_cwd(args + [
        "-filter_complex", ";".join(filters),
        "-map", "[vout]", "-an",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p", str(out),
    ])
    return out


def render_video(
    slides: list[tuple[Path, float]],   # (source, start_offset_for_videos)
    durations: list[float],
    voice_path: Path,
    ass_path: Path | None,
    cfg: Config,
    *,
    is_short: bool,
    music_path: Path | None,
    out_path: Path,
    sfx_at: list[float] | None = None,
) -> Path:
    kind = "short" if is_short else "long"
    w = int(cfg.get(f"video.{kind}.width"))
    h = int(cfg.get(f"video.{kind}.height"))
    fps = int(cfg.get("video.fps", 30))
    ken = bool(cfg.get("media.ken_burns", True))
    # Breathing room after the last word. The carousel's video is exactly as
    # long as its narration, so the close ("...em qual farmácia.") used to end
    # on the very last frame — and the old music fade, written for the shorts
    # pipeline where narration stops 3 s before the end, silenced it outright.
    tail = max(0.0, float(cfg.get("video.tail_seconds", 0.8) or 0))
    if tail > 0 and durations:
        durations = list(durations)
        durations[-1] += tail

    workdir = cfg.output_dir / "_work"
    clips_dir = workdir / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)

    # 1) Build one short clip per slide (image OR video source) — encoded in
    #    PARALLEL (each is an independent ffmpeg process; 4 workers ≈ 3-4x
    #    faster renders on montages like the vehicles showcase) — then
    #    concatenate into a silent slideshow.
    import os
    from concurrent.futures import ThreadPoolExecutor

    # Transitions overlap neighbouring clips, so every clip except the last
    # must be LONGER by exactly the crossfade — otherwise the slideshow ends
    # up (n-1)*cross shorter than the narration and the two drift apart.
    cross = float(cfg.get("video.transition_seconds", 0.0) or 0.0)
    use_xfade = cross > 0 and len(slides) > 1
    enc_durations = list(durations)
    if use_xfade:
        enc_durations = [d + cross if i < len(durations) - 1 else d
                         for i, d in enumerate(durations)]

    def _encode(i: int, src: Path, offset: float, dur: float) -> Path:
        dest = clips_dir / f"c{i:03d}.mp4"
        if is_video(src):
            return _video_slide_clip(src, dur, w, h, fps, dest, start=offset)
        return _slide_clip(src, dur, w, h, fps, ken, dest, i)

    workers = min(4, max(2, (os.cpu_count() or 4) // 2))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(_encode, i, src, offset, dur)
                   for i, ((src, offset), dur) in enumerate(zip(slides, enc_durations))]
        clips = [f.result() for f in futures]   # order preserved
    if use_xfade:
        slideshow = _concat_clips_xfade(
            clips, enc_durations, workdir / "slideshow.mp4", workdir, cross,
            random.Random(cfg.get("video.transition_seed") or None))
    else:
        slideshow = _concat_clips(clips, workdir / "slideshow.mp4", workdir)

    # 2) Compose: burn subtitles, overlay watermark, mux voice + music.
    inputs: list[str] = ["-i", str(slideshow), "-i", str(voice_path)]
    idx_voice = 1
    idx = 2
    idx_music = None
    if music_path:
        inputs = ["-i", str(slideshow), "-stream_loop", "-1", "-i", str(music_path), "-i", str(voice_path)]
        # re-order so loop applies to music: slideshow(0) music(1) voice(2)
        idx_music, idx_voice = 1, 2
        idx = 3

    watermark = cfg.get("video.watermark")
    idx_wm = None
    if watermark and cfg.path_of(watermark).exists():
        inputs += ["-i", str(cfg.path_of(watermark))]
        idx_wm = idx
        idx += 1
    # Transition sound: one short whoosh at each time in sfx_at (the caller
    # passes BEAT changes, not every visual cut — a swoosh every 5 s is a
    # tic, one per section is punctuation).
    sfx_file = cfg.get("video.transition_sfx")
    sfx_times = [t for t in (sfx_at or []) if t > 0.2]
    idx_sfx = None
    if sfx_file and sfx_times and cfg.path_of(sfx_file).exists():
        inputs += ["-i", str(cfg.path_of(sfx_file))]
        idx_sfx = idx
        idx += 1

    filters: list[str] = []
    cur = "[0:v]"
    if ass_path is not None:
        # Run with cwd = ass parent so we can pass a bare filename (dodges the
        # Windows drive-letter escaping problems of the subtitles filter).
        # fontsdir: the bundled faces (assets/fonts) so captions look the same
        # on Windows and on a bare Linux runner. Relative to the cwd below,
        # which also dodges the drive-letter escaping problem.
        import os as _os
        fonts_dir = Path(__file__).resolve().parent.parent / "assets" / "fonts"
        rel = _os.path.relpath(fonts_dir, ass_path.parent).replace("\\", "/")
        filters.append(f"{cur}subtitles={ass_path.name}:fontsdir={rel}[subv]")
        cur = "[subv]"
    if idx_wm is not None:
        wm_w = max(64, int(w * 0.12))
        filters.append(f"[{idx_wm}:v]scale={wm_w}:-1,format=rgba,colorchannelmixer=aa=0.85[wm]")
        filters.append(f"{cur}[wm]overlay=W-w-{int(w*0.03)}:{int(h*0.03)}[wmv]")
        cur = "[wmv]"

    total = sum(durations)
    # Progress bar along the bottom edge, growing with time. Cheap and one of
    # the few on-screen elements with a measurable effect on watch-through in
    # shorts: it tells the viewer the end is close, so they stay for it.
    bar_h = int(cfg.get("video.progress_bar_height", 12))
    if bar_h > 0 and total > 0:
        color = str(cfg.get("video.progress_bar_color", "0xD05010")) + "@0.92"
        filters.append(f"{cur}drawbox=x=0:y=ih-{bar_h}:w='iw*min(t/{total:.3f},1)':h={bar_h}:"
                       f"color={color}:t=fill[vout]")
    else:
        filters.append(f"{cur}null[vout]")
    cur = "[vout]"

    # Audio graph
    # The voice gets the same tail of silence as the video, so amix
    # (duration=first) and -shortest keep the full picture.
    duck = idx_music is not None and bool(cfg.get("music.duck", True))
    if duck:
        # A label feeds ONE filter; the sidechain and the mix both need the
        # voice, hence the split.
        filters.append(f"[{idx_voice}:a]apad=pad_dur={tail:.2f},asplit=2[voice][voice_sc]")
    else:
        filters.append(f"[{idx_voice}:a]apad=pad_dur={tail:.2f}[voice]")
    if idx_music is not None:
        mv = float(cfg.get("music.volume", 0.15))
        # Fade the MUSIC out over the last 1.6 s. Never the mix: fading the mix
        # faded the narration with it and cut the last words of the close.
        fade_start = max(0.0, total - 1.6)
        filters.append(f"[{idx_music}:a]volume={mv},afade=t=out:st={fade_start:.2f}:d=1.6[mus]")
        if duck:
            filters.append(
                f"[mus][voice_sc]sidechaincompress="
                f"threshold=0.02:ratio=8:attack=5:release=300[duck]"
            )
            filters.append(f"[voice][duck]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[faded]")
        else:
            filters.append(f"[voice][mus]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[faded]")
    else:
        filters.append(f"[voice]anull[faded]")
    a_cur = "[faded]"
    if idx_sfx is not None:
        vol = float(cfg.get("video.transition_sfx_volume", 0.35))
        n = len(sfx_times)
        labels = "".join(f"[w{i}]" for i in range(n))
        filters.append(f"[{idx_sfx}:a]volume={vol},asplit={n}{labels}")
        delayed = []
        for i, t in enumerate(sfx_times):
            ms = int(round((t - 0.12) * 1000))        # whoosh peaks ~120 ms in
            filters.append(f"[w{i}]adelay={ms}|{ms}[d{i}]")
            delayed.append(f"[d{i}]")
        filters.append(f"{a_cur}{''.join(delayed)}amix=inputs={n + 1}:duration=first:"
                       f"dropout_transition=0:normalize=0[sfxmix]")
        a_cur = "[sfxmix]"
    # Loudness: narration alone came out around -22 dB, quiet on a phone
    # next to everything else in the feed. Normalise to the social-media
    # standard (-14 LUFS) so the video plays at the same level as its
    # neighbours. 0 disables.
    lufs = float(cfg.get("music.loudness_lufs", -14) or 0)
    if lufs:
        filters.append(f"{a_cur}loudnorm=I={lufs}:TP=-1.5:LRA=11[aout]")
    else:
        filters.append(f"{a_cur}anull[aout]")
    amap = "[aout]"

    filter_complex = ";".join(filters)

    cmd = inputs + [
        "-filter_complex", filter_complex,
        "-map", cur, "-map", amap,
        "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-r", str(fps), "-shortest", "-movflags", "+faststart",
        str(out_path),
    ]
    cwd = ass_path.parent if ass_path is not None else None
    _run_cwd(cmd, cwd=cwd)

    # Media-usage log: credit every LIBRARY file this video used (workdir
    # temporaries like the intro/outro/AI scenes are not rotating assets).
    from .media import origin_of
    from .media_log import record_usage
    out_root = cfg.output_dir.resolve()
    used: list[tuple[Path, float | None]] = []
    for src, offset in slides:
        orig = origin_of(src)
        if out_root in orig.resolve().parents:
            continue
        used.append((orig, offset if is_video(orig) else None))
    record_usage(cfg, out_path, used, music_path)
    return out_path
