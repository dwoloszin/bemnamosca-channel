"""Thin wrapper around the ffmpeg binary bundled by imageio-ffmpeg.

Using imageio-ffmpeg means the user does NOT need to install ffmpeg system-wide;
a static binary is downloaded automatically on first use.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

# Hard ceiling on any single ffmpeg call. On 18/08 two ken-burns encoders
# wedged: 800+ seconds of CPU, ZERO bytes written, output file never even
# opened. subprocess.run had no timeout, so the daily routine sat blocked
# behind them from 10:05 until the Task Scheduler was due to kill it 4h later
# — no video, no carousel, and a log that stayed empty because it is only
# written when the run ends. A stuck encoder must lose its own clip, never the
# whole day. Generous on purpose: a clip takes seconds, the final mux a couple
# of minutes. Override with FFMPEG_TIMEOUT_S for an unusually long render.
TIMEOUT_S = int(os.getenv("FFMPEG_TIMEOUT_S", "900"))


def ffmpeg_exe() -> str:
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def run(args: list[str], *, quiet: bool = True,
        timeout: int | None = None) -> None:
    """Run ffmpeg with the given args (excluding the executable itself)."""
    cmd = [ffmpeg_exe(), "-y", "-hide_banner"]
    if quiet:
        cmd += ["-loglevel", "error"]
    cmd += args
    limit = timeout or TIMEOUT_S
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=limit)
    except subprocess.TimeoutExpired:
        # subprocess.run kills the child before re-raising, so the wedged
        # encoder is gone by the time this line runs.
        raise RuntimeError(
            f"ffmpeg timed out after {limit}s and was killed: "
            + " ".join(cmd)
        ) from None
    if proc.returncode != 0:
        raise RuntimeError(
            "ffmpeg failed:\n" + " ".join(cmd) + "\n\n" + proc.stderr[-4000:]
        )


def concat_audio(paths: list[str | Path], out: str | Path) -> Path:
    """Concatenate audio files into one track (re-encoded, robust across headers)."""
    out = Path(out)
    if len(paths) == 1:
        run(["-i", str(paths[0]), "-c:a", "aac", "-b:a", "192k", str(out)])
        return out
    args: list[str] = []
    for p in paths:
        args += ["-i", str(p)]
    streams = "".join(f"[{i}:a]" for i in range(len(paths)))
    filt = f"{streams}concat=n={len(paths)}:v=0:a=1[a]"
    run(args + ["-filter_complex", filt, "-map", "[a]", "-c:a", "aac", "-b:a", "192k", str(out)])
    return out


_DUR_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)")


def probe_duration(path: str | Path) -> float:
    """Return media duration in seconds by parsing ffmpeg's own output."""
    try:
        proc = subprocess.run(
            [ffmpeg_exe(), "-hide_banner", "-i", str(path)],
            capture_output=True, text=True, timeout=120,
        )
    except subprocess.TimeoutExpired:
        print(f"  [ffmpeg] probe timed out on {path}")
        return 0.0
    m = _DUR_RE.search(proc.stderr)
    if not m:
        return 0.0
    h, mn, s = m.groups()
    return int(h) * 3600 + int(mn) * 60 + float(s)
