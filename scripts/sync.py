"""One command to keep the PC and GitHub Actions in step.

    python scripts/sync.py            # everything below
    python scripts/sync.py --no-fetch # skip downloading the day's package
    python scripts/sync.py --dry      # show what would change, touch nothing

  1. git pull            the runner's state (rotation, history, slots, AI photos)
  2. videos/loop   <->   release `loop-v1`   (upload new/changed, delete removed)
  3. videos/oneuse  ->   release `oneuse`    (upload new; the runner deletes
                                              each asset after posting it)
  4. fetch                the latest package into output/carousels/ (video,
                          linkedin.jpg/.txt, slides) for TikTok / LinkedIn

Sidecars next to a video (pitch17.json or pitch17.txt = title/description)
travel with it. Needs the GitHub CLI logged in (`gh auth status`).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = "dwoloszin/bemnamosca-channel"
ROOT = Path(__file__).resolve().parent.parent
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm"}
SIDECAR_EXT = {".json", ".txt"}


def gh(*args: str, check: bool = True) -> str:
    r = subprocess.run(["gh", *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        raise SystemExit(f"gh {' '.join(args[:3])}... falhou: {r.stderr.strip()[:200]}")
    return r.stdout


def release_assets(tag: str) -> dict[str, int]:
    """{name: size} for a release; {} if the release does not exist."""
    out = gh("release", "view", tag, "--repo", REPO, "--json", "assets", check=False)
    if not out.strip():
        return {}
    return {a["name"]: int(a["size"]) for a in json.loads(out).get("assets", [])}


def ensure_release(tag: str, title: str, dry: bool) -> None:
    if gh("release", "view", tag, "--repo", REPO, "--json", "tagName", check=False).strip():
        return
    print(f"  release {tag} nao existe — criando")
    if not dry:
        gh("release", "create", tag, "--repo", REPO, "--title", title,
           "--notes", "Arquivos gerenciados por scripts/sync.py")


def local_files(folder: Path) -> dict[str, Path]:
    """Videos directly in `folder` plus their sidecars (never posted/)."""
    if not folder.is_dir():
        return {}
    files = {p.name: p for p in folder.iterdir()
             if p.is_file() and p.suffix.lower() in VIDEO_EXT}
    for name in list(files):
        stem = Path(name).stem
        for ext in SIDECAR_EXT:
            sc = folder / f"{stem}{ext}"
            if sc.is_file() and sc.name.lower() != "readme.txt":
                files[sc.name] = sc
    return files


def sync_folder(folder: Path, tag: str, title: str, *, delete_missing: bool, dry: bool) -> None:
    print(f"\n== {folder.relative_to(ROOT)}  ->  release {tag}")
    ensure_release(tag, title, dry)
    remote = release_assets(tag)
    local = local_files(folder)
    to_upload = [p for n, p in local.items() if remote.get(n) != p.stat().st_size]
    to_delete = [n for n in remote if n not in local] if delete_missing else []
    if not to_upload and not to_delete:
        print(f"  em dia ({len(local)} arquivo(s))")
        return
    for p in to_upload:
        print(f"  {'(dry) ' if dry else ''}upload  {p.name}  ({p.stat().st_size // 1048576} MB)")
        if not dry:
            gh("release", "upload", tag, str(p), "--repo", REPO, "--clobber")
    for n in to_delete:
        print(f"  {'(dry) ' if dry else ''}remove  {n}  (nao existe mais localmente)")
        if not dry:
            gh("release", "delete-asset", tag, n, "--repo", REPO, "--yes")


def git_pull(dry: bool) -> None:
    print("== git pull (estado do runner)")
    if dry:
        print("  (dry) pulado")
        return
    r = subprocess.run(["git", "pull", "-q", "--rebase", "origin", "main"], cwd=ROOT,
                       capture_output=True, text=True)
    if r.returncode != 0:
        print("  AVISO: git pull falhou — " + r.stderr.strip().splitlines()[-1][:120])
        print("  (alteracoes locais nao commitadas? `git status`)")
    else:
        print("  ok")


def fetch_latest() -> None:
    print("\n== pacote do dia")
    subprocess.run([sys.executable, str(ROOT / "scripts" / "fetch_today.py")], cwd=ROOT)


def main() -> int:
    dry = "--dry" in sys.argv
    git_pull(dry)
    sync_folder(ROOT / "videos" / "loop", "loop-v1", "Loop pitches v1",
                delete_missing=True, dry=dry)
    sync_folder(ROOT / "videos" / "oneuse", "oneuse", "One-off promos (inbox)",
                delete_missing=False, dry=dry)
    if "--no-fetch" not in sys.argv and not dry:
        fetch_latest()
    return 0


if __name__ == "__main__":
    sys.exit(main())
