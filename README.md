
> **Bem na Mosca — produção diária automática.** Este repositório roda a rotina
> do canal no GitHub Actions (`.github/workflows/daily.yml`): busca a notícia do
> dia, escreve o roteiro, narra, renderiza o Short e publica no YouTube
> (agendado) e no Instagram (Reel). Detalhes operacionais em [ROTINA.md](ROTINA.md).
> Segredos ficam em *Settings → Secrets*; nada sensível está versionado.

# Auto YouTube Channel (GTA 6 news, fully automated)

Builds and (optionally) uploads YouTube **Shorts** and **long videos** about a
theme you set in `config.yaml` (default: **GTA 6**). It searches news, writes a
narration, speaks it, adds big readable subtitles, layers new music, and renders
a finished MP4 — all automatically. It can also make **GTA 5 vs GTA 6
comparison** videos once you have footage.

The whole thing is driven by one config file, so switching theme (e.g. to
another game) is just editing `config.yaml`.

---

## What it does (pipeline)

```
news search (Serper/SerpAPI pools) + RSS + Reddit community
   ─▶ history filter (never repeat a story)
   ─▶ LLM writes script/SEO (Gemini pool ─▶ Groq pool ─▶ template)
   ─▶ ElevenLabs voice (6-key pool ─▶ edge-TTS fallback)
   ─▶ karaoke subtitles (word-synced, spoken word highlighted)
   ─▶ visuals: your trailer clips (edge-trimmed) + official art + AI images
      (Cloudflare FLUX/Lucid, 5-account pool) + Wikimedia Commons photos
   ─▶ ffmpeg render + CC-BY music (auto-ducked, auto-credited) + watermark
   ─▶ upload: SEO title/desc/tags + thumbnail + closed captions (private)
```

### Copyright avoidance (built in)
- **No original audio** from any source clip is kept — every video gets a
  brand-new soundtrack: a synthetic voice + royalty-free music.
- Visuals come from **your own asset library** and **royalty-free stock**
  (Pexels). Article images are **off by default** (`media.use_article_images`).
- News is used as **short factual summaries with source links**, not copied text.
- Big burned-in **subtitles** and Ken-Burns motion transform the stills.

> ⚠️ This reduces copyright risk but is not legal advice. Don't feed it
> copyrighted trailer footage or commercial music. Use the YouTube Audio Library
> or Pixabay for music, and only images you're licensed to use.

---

## Quick start (Windows)

```powershell
# 1. Install dependencies (ffmpeg is bundled — no system install needed)
python -m pip install -r requirements.txt

# 2. Add your keys
copy .env.example .env
notepad .env          # paste ANTHROPIC_API_KEY (and PEXELS_API_KEY if you have one)

# 3. Create folders + verify ffmpeg
python main.py setup

# 4. Preview what it found
python main.py news

# 5. Build your first Short and long video
python main.py short
python main.py long
```

Rendered files land in `output/` (an `.mp4` plus a matching `.json` with the
title, sources, and duration).

### No API key?
It still runs. Without `ANTHROPIC_API_KEY` it writes narration from a simple
template (lower quality wording). Without `PEXELS_API_KEY` it uses only the
images in `img-channel/` and `assets/images/`.

### Prevent Secret Leaks (Recommended)

This repo includes a pre-commit hook that scans commits for secrets.

```powershell
python -m pip install pre-commit detect-secrets
python -m detect_secrets scan > .secrets.baseline
python -m pre_commit install
python -m pre_commit run --all-files
```

After this, every `git commit` runs the secret scan automatically.

---

## Commands

`main.py` always needs a command (running it bare prints usage and exits).

### Content

| Command | What it does |
|---|---|
| `python main.py short` | One news Short (9:16, < 60s) — builds + uploads (private) |
| `python main.py listicle` | One viral "TOP 5" countdown Short (news + Reddit community sources) |
| `python main.py post` | Community-post package: viral text + AI image in `output/posts/` — opens the folder; you paste it on YouTube (~20s, the API can't post these). Set `posts.sandbox: true` in `config.yaml` to generate only and disable auto-publish side effects (e.g., Instagram cross-post). |
| `python main.py post "GTA 6 trailer 3 release date"` | Same, but about YOUR topic instead of the auto-picked trending one (searches real news for it; skips if none found) |
| `python main.py short "topic" assets\myfolder` | `short`, `listicle` and `post` all accept — in any order — a **topic** and/or a **media folder**. The folder (subfolders included, `.png/.jpg/.webp` + video clips) becomes the ONLY visual source for that run: no stock, no AI background, rotation still applies. Examples: `listicle "GTA 6 map leaks" assets\leaks`, `post assets\screens`, `short "GTA 6 trailer 3"` |
| `python main.py cars short` | 3 GTA cars vs their real-life counterparts (asks for your initial prompt, end input with `END`) |
| `python main.py cars long` | Up to 10 car comparisons, 16:9 with chapters |
| `python main.py cars showcase` | Fast montage of ALL vehicles in `assets/images/vehicles/**`, name banners per vehicle |
| `python main.py long` | One 16:9 news-roundup video (targets 10 min for monetization) |
| `python main.py comparison` | GTA5-vs-GTA6 side-by-side (needs footage + `comparison:` config) |
| `python main.py run` | Everything the `publish:` config section says — currently 2 Shorts (1 news + 1 listicle) + 1 community-post package. This is what the daily scheduler runs. |

### Utilities

| Command | What it does |
|---|---|
| `python main.py setup` | Create folders, verify ffmpeg, show key/pool status (ElevenLabs credits, Cloudflare accounts) |
| `python main.py news` | Print the headlines it found (no video) |
| `python main.py voices [lang]` | List edge-TTS voices (e.g. `voices pt` for Portuguese) |
| `python main.py youtube-test` | Read-only YouTube auth check — uploads nothing |

Use a different config with `python main.py --config other.yaml short`.

## Cloning this project for ANOTHER channel

The pipeline is theme-driven, so a second channel (any topic) reuses the whole
codebase. Do **not** copy the folder by hand: `token.json` would upload to the
wrong YouTube channel, `output/history.json` would make the new channel believe
it already covered every story, and the Instagram/TikTok tokens would post to
the wrong accounts. Use:

```bat
python clone_channel.py "C:\path\to\new-channel" ^
    --name "F1 News Daily" --handle "@f1newsdaily" --slug "f1" --topic "Formula 1"
```

| | Shared between channels | Per channel (never copied) |
|---|---|---|
| Keys | Groq, Gemini, Serper, SerpAPI, Cloudflare, ElevenLabs, Pexels, and the *app-level* Meta/TikTok credentials | `INSTAGRAM_ACCESS_TOKEN`, `META_USER_TOKEN` |
| Files | code, `daily.bat`, scheduler | `token.json`, `client_secret.json`, everything in `output/` |
| Media | — | `assets/**`, `img-channel/**` |

Add `--copy-google` to reuse `client_secret.json` (same Google Cloud project).
That works, but the **10,000 units/day YouTube quota is then shared** — about 6
uploads/day across both channels (~1,600 units each). For more headroom, create
a separate Google Cloud project and download a fresh `client_secret.json`.

Provider quotas are shared too (ElevenLabs credits, Serper searches, Cloudflare
images), so two channels consume them twice as fast.

After cloning, edit in the new `config.yaml`: `theme.keywords`,
`search_queries`, `rss_feeds`, `subreddits`, `must_match_any`, `block_words`,
`posts.topics` and `youtube.tag_pool` — those still describe the old topic.
Then `python main.py news` to confirm the headlines are on-topic, build one
short with the upload flags off, and only then enable publishing.

---

## Configuring the channel

Everything is in **`config.yaml`**. The most important knobs:

- **`theme`** — topic, keywords, RSS feeds. Change these to pivot to any subject.
- **`script.model`** — `claude-opus-4-8` (best), `claude-sonnet-5`, or
  `claude-haiku-4-5` (cheapest).
- **`tts.voice`** — run `python main.py voices` to see options.
- **`subtitles`** — size (percent of height), colors, words-on-screen.
- **`video.long.min_seconds`** — how long the long video must be. YouTube pays
  best on longer watch time; 8+ minutes also unlocks mid-roll ads. Default 600s.
- **`comparison`** — enable and list clip pairs once GTA 6 is out.
- **`youtube`** — set `enabled: true` and configure OAuth (below) to auto-upload.

### Making long videos long enough
The long video concatenates several news stories. If your narration comes up
short of `video.long.min_seconds`, the tool warns you. To lengthen: add more
`theme.rss_feeds`, raise `video.long.max_items`, or raise
`script.long_section_words`.

---

## Built-in growth features (SEO)

Every render now also produces, next to the `.mp4`:

- **`.jpg` thumbnail** — auto-generated: story image + dark gradient + huge
  uppercase title words + topic badge + your logo. Uploaded automatically.
- **`.srt` captions** — word-timed closed captions, attached to the upload
  (helps search indexing + accessibility, on top of the burned-in subtitles).
- **`.json` metadata** — SEO title (keyword front-loaded, ≤70 chars), a
  search-optimized description, 15-20 tags, and chapter timestamps.
- **Chapters** — long videos get `0:00 Intro` + one chapter per story in the
  description, so YouTube shows the segmented progress bar.
- **`#Shorts`** hashtag + tag are added to Shorts automatically.

With `ANTHROPIC_API_KEY` set, Claude writes the titles/descriptions/tags as a
YouTube SEO expert (curiosity without lying). Without a key, a solid template
fallback is used.

### Growth playbook (things the tool can't do for you)

1. **Consistency beats everything** — the daily scheduler exists for a reason.
   1 Short + 1 long video per day at the same hour trains both the algorithm
   and your audience.
2. **The first 3 seconds of a Short decide the swipe** — the script prompt
   already demands a hook; check the output and tighten `script.style` if
   hooks feel weak.
3. **Watch time > views** — longer retention on the long videos is what makes
   YouTube push them. Chapters help viewers skip to what interests them
   instead of leaving.
4. **Reply to early comments** (first hour) — engagement velocity is a strong
   ranking signal, and it's the one thing that can't be automated credibly.
5. **Check YouTube Studio → Analytics → "Traffic sources → YouTube search"**
   weekly; add the search terms you find to `theme.keywords` so future
   titles/tags target them.
6. **Verify your channel** (phone verification) to unlock custom thumbnails.
7. Once monetized targets matter: keep long videos **over 8 minutes** to
   unlock mid-roll ads (`video.long.min_seconds: 600` already targets 10 min).

## Auto-uploading to YouTube (optional)

1. In [Google Cloud Console](https://console.cloud.google.com/): create a
   project → enable **YouTube Data API v3**.
2. Create **OAuth client credentials** (type: *Desktop app*), download as
   `client_secret.json` into the project root.
3. Set `youtube.enabled: true` in `config.yaml` (start with `privacy: private`).
4. First run opens a browser to authorize; a `token.json` is cached for reuse.

Note: YouTube's API upload quota allows a limited number of uploads/day — fine
for a daily channel.

---

## Running it automatically every day

### Windows Task Scheduler (recommended for local)
```powershell
powershell -ExecutionPolicy Bypass -File scheduler\register_task.ps1
```
Runs `python main.py run` daily at 09:00 (edit the time in the script). Logs go
to `output\run.log`. Remove it with
`Unregister-ScheduledTask -TaskName "AutoYouTubeChannel" -Confirm:$false`.

---

## GTA 5 vs GTA 6 comparison mode

When GTA 6 lands:
1. Drop matching clips/images into `assets/compare/`.
2. In `config.yaml` set `comparison.enabled: true` and fill `comparison.pairs`
   (`left` = GTA 5, `right` = GTA 6, plus a `caption`).
3. `python main.py comparison`.

It stacks the two side-by-side (or top/bottom), labels them, strips original
audio, and adds a fresh narration + music.

---

## Project layout

```
config.yaml            # <- everything is configured here
main.py                # CLI entry point
.env                   # your API keys (copy from .env.example)
img-channel/           # your existing logos/banners (used as slides + watermark)
assets/
  images/              # your extra images
  music/               # royalty-free music (drop .mp3 here)
  compare/             # GTA5/GTA6 comparison clips
output/                # rendered videos + metadata (+ state.json, run.log)
scheduler/             # Windows Task Scheduler helpers
src/
  news.py  script_writer.py  tts.py  subtitles.py  media.py
  video.py  comparison.py  youtube_upload.py  pipeline.py  ffmpeg.py  config.py
```

## Troubleshooting

- **"No usable images found"** — add images to `img-channel/` or
  `assets/images/`, or set `PEXELS_API_KEY` in `.env`.
- **Short is longer than 60s** — lower `script.short_words`.
- **Subtitles look off** — tweak `subtitles.font_size_short/long` (percent of
  height) and `subtitles.margin_v`.
- **ffmpeg errors** — reinstall the bundled binary: `pip install -U imageio-ffmpeg`.



short theme
python main.py cars short    # 3 carros, formato Short
python main.py cars long     # até 10 carros, com capítulos


Comando	O que faz
python main.py short	1 Short de notícia
python main.py listicle	1 Short countdown "TOP 5"
python main.py post	1 pacote de post da comunidade (colar manualmente)
python main.py cars short	comparativo 3 carros vs reais (pede o prompt)
python main.py cars long	comparativo até 10 carros, com capítulos
python main.py cars showcase	montagem com todos os veículos das suas pastas
python main.py long	vídeo longo 16:9 (roundup de notícias)
python main.py run	tudo que o publish: do config mandar
python main.py news	só lista as manchetes (sem vídeo)
python main.py setup	status de pastas, ffmpeg e todas as chaves/pools





# main run
python main.py short
python main.py listicle
python main.py post
python main.py post "GTA 6 trailer 3 release date"

# short/listicle/post aceitam TOPICO e/ou PASTA de midia (em qualquer ordem).
# A pasta (com subpastas: .png/.jpg/.webp + videos) vira a UNICA fonte visual
# daquela execucao — sem stock, sem fundo de IA; a rotacao continua valendo.
python main.py short "GTA 6 ultimate edition see what you got" assets\gameversion
python main.py listicle "GTA 6 map leaks" assets\minhapasta
python main.py post "GTA 6 price" assets\minhapasta
python main.py short assets\gameversion


# instagram
# publicar o short mais recente como Reel (VAI AO AR na hora)
python main.py reels

# ou um arquivo específico
python main.py reels output\top5_gta6_20260712_183344.mp4


# POST INTAGRAM
python main.py igpost                          # publica o pacote MAIS RECENTE
python main.py igpost output\posts\post_...    # ou um específico





A legenda é montada automaticamente do .json do vídeo (título + hashtags + "Follow @gtanews_new..."). Para cross-post automático de todo Short no run diário: instagram.enabled: true no config — deixei desligado de propósito porque, diferente do YouTube, a API do Instagram não tem agendamento nem modo privado: Reel publicado = no ar imediatamente. Sugestão: rode python main.py reels manualmente por alguns dias e, quando confiar, ligue o automático.

Detalhes técnicos que valem saber
Token renovável para sempre: seu token dura 60 dias e o código renova sozinho a cada uso (persistido em output/instagram_token.json). Enquanto o canal postar regularmente, nunca expira.
Staging temporário: a API do Instagram nesse modo exige URL pública do vídeo (o upload direto/resumável não está disponível — testei, retorna video_url is required). Solução: o vídeo é publicado por git push num repo público (gtanews-media), o Instagram baixa em ~1 min, e o arquivo é removido imediatamente (force-push órfão — o repo volta a ter só um README, sem histórico acumulado). Transparência: durante esses ~2 minutos o MP4 fica acessível numa URL pública obscura — é o seu próprio conteúdo prestes a ser público, então risco zero na prática.
Três abordagens de hosting falharam antes desta (contents API e blobs API têm limites bem abaixo dos 47MB do vídeo) — a versão final usa git real, que aguenta até 100MB.










Campo	Valor
Platforms	✅ Web (só essa)
Website URL	https://dwoloszin.github.io/gtanews-legal/
Redirect URI / Callback URL	https://dwoloszin.github.io/gtanews-legal/tiktok-callback.html
Privacy Policy	https://dwoloszin.github.io/gtanews-legal/privacy-policy.html
Terms of Service	https://dwoloszin.github.io/gtanews-legal/terms-of-service.html