"""Download the day's package produced by GitHub Actions into output/carousels/.

    python scripts/fetch_today.py            # latest successful run
    python scripts/fetch_today.py 2026-08-23 # a specific day

What you get, same layout as a local run:
    output/carousels/carousel_<data>/video.mp4      -> TikTok, LinkedIn (pagina)
                                  /linkedin.jpg     -> LinkedIn (perfil), imagem
                                  /linkedin.txt     -> LinkedIn (perfil), texto + link p/ 1o comentario
                                  /slide_1..5.jpg + carousel.txt (se quiser postar os slides)
Needs the GitHub CLI logged in (`gh auth status`).
"""
from __future__ import annotations

import json
import subprocess
import sys
import zipfile
from pathlib import Path

REPO = "dwoloszin/bemnamosca-channel"
ROOT = Path(__file__).resolve().parent.parent
DEST = ROOT / "output"


def main() -> int:
    day = sys.argv[1] if len(sys.argv) > 1 else None
    out = subprocess.run(
        ["gh", "run", "list", "--repo", REPO, "--workflow", "daily", "--limit", "20",
         "--json", "databaseId,conclusion,createdAt,event"],
        capture_output=True, text=True, check=True).stdout
    runs = [r for r in json.loads(out) if r["conclusion"] == "success"]
    if day:
        runs = [r for r in runs if r["createdAt"].startswith(day)]
    if not runs:
        print("nenhuma execucao bem-sucedida" + (f" em {day}" if day else ""))
        return 1
    # Several windows run per morning; only one of them builds the package,
    # the others exit with "already ran today". Walk the successful runs of
    # the day, newest first, until one carries a carousel.
    import shutil
    tmp = DEST / "_artifact"
    pkgs: list[Path] = []
    for run in runs:
        rid = run["databaseId"]
        print(f"execucao {rid} ({run['createdAt'][:16].replace('T', ' ')} UTC, {run['event']})", end=" ")
        # Cheap pre-check: in a late-cron day the retries come FIRST in this
        # newest-first walk, and each used to be a full download just to learn
        # it only carried the log. The artifact listing already tells the
        # size — a log-only artifact is a few KB, a package with video >10MB.
        q = subprocess.run(["gh", "api", f"repos/{REPO}/actions/runs/{rid}/artifacts",
                            "--jq", "[.artifacts[].size_in_bytes] | add // 0"],
                           capture_output=True, text=True)
        if q.returncode == 0 and q.stdout.strip().isdigit() and int(q.stdout.strip()) < 1_000_000:
            print(f"— sem pacote (artefato de {int(q.stdout.strip()) // 1024} KB, so log)")
            continue
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(["gh", "run", "download", str(rid), "--repo", REPO, "--dir", str(tmp)],
                           capture_output=True, text=True)
        if r.returncode != 0:
            print("— sem artefato"); continue
        pkgs = sorted(tmp.glob("pacote-*/carousels/carousel_*"))
        if not pkgs:
            for z in tmp.glob("**/*.zip"):
                zipfile.ZipFile(z).extractall(z.parent)
            pkgs = sorted(tmp.glob("**/carousels/carousel_*"))
        if pkgs:
            print("— pacote encontrado"); break
        print("— sem pacote (execucao sem carrossel)")
        if not day:
            # without an explicit day, never walk past the day of the newest run
            if run["createdAt"][:10] != runs[0]["createdAt"][:10]:
                break
    if not pkgs:
        print("nenhuma execucao com pacote" + (f" em {day}" if day else " hoje"))
        return 1
    # promos prepared that day (captioned final cut + texts) travel too
    for pr in tmp.glob("**/promos/*"):
        dest = DEST / "promos" / pr.name; dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(pr, dest); print(f"-> {dest}")
    for pkg in pkgs:
        target = DEST / "carousels" / pkg.name
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(pkg, target)
        files = sorted(p.name for p in target.iterdir())
        print(f"-> {target}")
        print("   " + "  ".join(files))
    shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
