"""Comparison videos: e.g. GTA 5 vs GTA 6 scenes, side-by-side or top/bottom.

Each source clip has its ORIGINAL AUDIO STRIPPED; a new narration + music is
added, and big labels + captions are burned in. Works with images or videos.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from . import ffmpeg
from .config import Config
from .tts import synthesize_auto


def _run(args: list[str]) -> None:
    cmd = [ffmpeg.ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error"] + args
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError("ffmpeg failed:\n" + " ".join(cmd) + "\n\n" + proc.stderr[-4000:])


def _is_video(path: Path) -> bool:
    return path.suffix.lower() in {".mp4", ".mov", ".mkv", ".webm", ".avi"}


def _escape_drawtext(text: str) -> str:
    return text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "’")


def _pair_segment(left: Path, right: Path, caption: str, cfg: Config,
                  w: int, h: int, fps: int, seconds: float, out: Path) -> Path:
    layout = cfg.get("comparison.layout", "side_by_side")
    if layout == "top_bottom":
        cell_w, cell_h = w, h // 2
        stack = "vstack"
    else:
        cell_w, cell_h = w // 2, h
        stack = "hstack"

    left_lbl = _escape_drawtext(cfg.get("comparison.left_label", "BEFORE"))
    right_lbl = _escape_drawtext(cfg.get("comparison.right_label", "AFTER"))
    font_sz = max(24, int(h * 0.05))

    def _input_args(p: Path) -> list[str]:
        if _is_video(p):
            return ["-t", f"{seconds:.2f}", "-i", str(p)]
        return ["-loop", "1", "-t", f"{seconds:.2f}", "-i", str(p)]

    args = _input_args(left) + _input_args(right)
    lbl = (
        f"drawtext=text='{left_lbl}':x=(w-tw)/2:y=h-th-{int(cell_h*0.06)}:"
        f"fontsize={font_sz}:fontcolor=white:box=1:boxcolor=black@0.55:boxborderw=12"
    )
    rbl = lbl.replace(left_lbl, right_lbl)
    vf = (
        f"[0:v]scale={cell_w}:{cell_h}:force_original_aspect_ratio=increase,"
        f"crop={cell_w}:{cell_h},setsar=1,{lbl}[l];"
        f"[1:v]scale={cell_w}:{cell_h}:force_original_aspect_ratio=increase,"
        f"crop={cell_w}:{cell_h},setsar=1,{rbl}[r];"
        f"[l][r]{stack}=inputs=2,format=yuv420p[v]"
    )
    _run(args + [
        "-filter_complex", vf, "-map", "[v]",
        "-r", str(fps), "-t", f"{seconds:.2f}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
        "-an", str(out),
    ])
    return out


def build_comparison(cfg: Config, out_path: Path) -> Path | None:
    pairs = cfg.get("comparison.pairs", []) or []
    if not pairs:
        print("  [comparison] no pairs configured.")
        return None

    kind = "long"
    w = int(cfg.get(f"video.{kind}.width"))
    h = int(cfg.get(f"video.{kind}.height"))
    fps = int(cfg.get("video.fps", 30))
    workdir = cfg.output_dir / "_work_cmp"
    workdir.mkdir(parents=True, exist_ok=True)

    # Narrate the captions so the comparison has a voiceover.
    captions = [p.get("caption", "") for p in pairs if p.get("caption")]
    narration = " ".join(
        [f"{cfg.get('comparison.left_label')} versus {cfg.get('comparison.right_label')}."]
        + captions
    )
    voice_clip = synthesize_auto(narration, workdir / "voice.mp3", cfg)
    per_pair = max(4.0, voice_clip.duration / max(1, len(pairs)))

    segments: list[Path] = []
    for i, pair in enumerate(pairs):
        left = cfg.path_of(pair["left"])
        right = cfg.path_of(pair["right"])
        if not left.exists() or not right.exists():
            print(f"  [comparison] missing pair files, skipping: {left} / {right}")
            continue
        seg = _pair_segment(left, right, pair.get("caption", ""), cfg, w, h, fps,
                            per_pair, workdir / f"seg_{i:03d}.mp4")
        segments.append(seg)

    if not segments:
        return None

    # Concat segments, then mux the narration.
    listfile = workdir / "concat.txt"
    listfile.write_text("\n".join(f"file '{s.as_posix()}'" for s in segments) + "\n", encoding="utf-8")
    silent = workdir / "silent.mp4"
    _run(["-f", "concat", "-safe", "0", "-i", str(listfile), "-c", "copy", str(silent)])

    _run([
        "-i", str(silent), "-i", str(voice_clip.audio_path),
        "-map", "0:v", "-map", "1:a",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        "-shortest", "-movflags", "+faststart", str(out_path),
    ])
    return out_path
