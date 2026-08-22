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
    run = runs[0]
    rid = run["databaseId"]
    print(f"execucao {rid} ({run['createdAt'][:16].replace('T', ' ')} UTC, {run['event']})")
    tmp = DEST / "_artifact"
    tmp.mkdir(parents=True, exist_ok=True)
    subprocess.run(["gh", "run", "download", str(rid), "--repo", REPO, "--dir", str(tmp)],
                   check=True)
    # gh already unzips: tmp/pacote-<id>/carousels/<pkg>/...
    pkgs = sorted(tmp.glob("pacote-*/carousels/carousel_*"))
    if not pkgs:
        zips = list(tmp.glob("**/*.zip"))
        for z in zips:
            zipfile.ZipFile(z).extractall(z.parent)
        pkgs = sorted(tmp.glob("**/carousels/carousel_*"))
    if not pkgs:
        print("o artefato nao tem pacote (o carrossel nao saiu nesse dia?)")
        return 1
    import shutil
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
