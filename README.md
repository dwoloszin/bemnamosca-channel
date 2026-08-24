# Bem na Mosca — canal de conteúdo automático

Rotina diária que transforma **a notícia do dia sobre medicamentos** em um Short
vertical narrado (YouTube + Instagram Reels), um card para o LinkedIn e um
pacote para postagem manual — e publica sozinha, todo dia, no GitHub Actions.
O PC fica como backup.

> O app promovido é o [Bem na Mosca](https://bemnamosca.com/), um buscador de
> preços de medicamentos. Perfis: [@bemnamosc4](https://www.instagram.com/bemnamosc4)
> no Instagram, [Bem na Mosca](https://www.youtube.com/@bemnamosc4) no YouTube.

---

## O que sai todo dia

| Etapa | O que é | Onde vai |
|---|---|---|
| **1/3 oneuse** | promos prontos em `videos/oneuse/` (inbox) | YouTube agendado + Reel; arquivados depois |
| **2/3 loop** | o próximo pitch do rodízio em `videos/loop/` (16 vídeos) | YouTube 12:00 + Reel |
| **3/3 carrossel** | vídeo gerado da notícia do dia (5 partes, ~55 s) | YouTube 18:00 + Reel |
| pacote manual | `linkedin.jpg` + `linkedin.txt`, 5 slides 4:5, `carousel.txt`, `post.txt` | você posta no LinkedIn / TikTok |

### Anatomia do vídeo do carrossel

```
1  GANCHO        manchete em tela cheia, narração com número/pergunta/contradição e um "open loop"
2  O QUE MUDOU   a notícia, fatos           } fotos geradas por IA (Gemini) — sem texto, sem logos
3  O QUE ISSO    o que custa para o viewer  } ou foto de banco (Pexels)
   CUSTA
4  BEM NA MOSCA  o app em uma frase          } telas REAIS do app (lista curada, só remédios)
5  COMO USAR     passo a passo + payoff do gancho + convite
```

Por cima: voz Alice (ElevenLabs, pt-BR forçado, contexto entre blocos),
legenda karaokê por frase com glow verde, palavras-chave em laranja, chip de
seção no topo, 2 imagens por bloco, whoosh na troca de bloco, trilha em
rodízio, normalização a -14 LUFS, barra de progresso, guard de 58 s.

---

## Sistemas usados

| Função | Serviço | Chave (secret) | Custo |
|---|---|---|---|
| Notícias | Google News RSS · Serper / SerpAPI · NewsAPI | `SERPER_API_KEYS`, `SERPAPI_API_KEYS`, `NEWSAPI_KEY` | grátis |
| Roteiro (LLM) | Gemini (`gemini-3.6-flash`) → Groq (`gpt-oss-120b`) | `GEMINI_API_KEYS`, `GROQ_API_KEYS` | grátis |
| Narração (TTS) | ElevenLabs (Alice, `eleven_turbo_v2_5`) → edge-tts (Thalita) | `ELEVENLABS_API_KEYS` | grátis (pool de chaves) |
| Fotos de IA | Gemini Image (`gemini-3.1-flash-image`) | `GEMINI_API_KEY_IMG` | ~US$0,04/imagem (billing ligado) |
| Fotos de banco | Pexels | `PEXELS_API_KEYS` | grátis |
| Música / whoosh | ElevenLabs Sound Effects (gerados uma vez, em `assets/music`, `assets/sfx`) | — | — |
| YouTube | YouTube Data API v3 (OAuth, app *In production*) | `CLIENT_SECRET_JSON`, `TOKEN_JSON` | quota 10k/dia (~6 uploads) |
| Instagram | Graph API (Reels); staging público temporário via repo `bemnamosca-media` | `INSTAGRAM_*`, `META_*`, `INSTAGRAM_TOKEN_JSON`, `DB_ARCHIVE_GITHUB_TOKEN` | grátis |
| Render | ffmpeg + libass, Pillow, fontes OFL em `assets/fonts` | — | — |
| Execução | GitHub Actions (repo público = minutos grátis) | `GH_PAT` (renova o token do Instagram) | grátis |

---

## Como roda

### GitHub Actions (principal)

`.github/workflows/daily.yml` dispara às **10:00, 10:30, 11:00 e 11:30** (Brasília).
Cada disparo é uma máquina nova; `python main.py daily` lê
`output/daily_state.json` (commitado de volta no repo) e só executa as etapas
que ainda não saíram hoje — o primeiro disparo que dá certo faz tudo, os
seguintes terminam em segundos, e se algo falhar o próximo disparo é a
retentativa. O estado (rodízio, histórico de notícias, slots do YouTube,
ganchos usados, biblioteca de fotos IA) volta para o repo a cada execução.

- Vídeos do rodízio e promos avulsos ficam em **releases** (`loop-v1`, `oneuse`);
  o runner baixa só o que falta e guarda em cache.
- O pacote do dia fica como **artefato** da execução (14 dias).
- *Dry run* (gera tudo, não publica, não grava estado): *Actions → daily → Run workflow → dry_run*.
- Falhou? O GitHub manda e-mail; o log está na aba Actions.

### PC (backup)

Tarefa do Windows `BemNaMoscaDaily` (`scheduler/register_task.ps1`), **desabilitada**
enquanto o GitHub é o principal. Nunca os dois no mesmo dia. Trocar:

```powershell
# GitHub -> PC
python scripts\sync.py            # traz o estado
Enable-ScheduledTask BemNaMoscaDaily
gh workflow disable 340009580 --repo dwoloszin/bemnamosca-channel
# PC -> GitHub: o inverso
```

---

## Uso no dia a dia

```powershell
python scripts\sync.py              # PC <-> GitHub: estado, vídeos novos, promos, pacote do dia
python scripts\sync.py --dry        # só mostra o que faria
python scripts\fetch_today.py       # só baixa o pacote do dia (ou de uma data)
```

- **Pitch novo no rodízio:** coloque o `.mp4` em `videos\loop\` (opcional: `nome.txt`
  ou `nome.json` com título/descrição) e rode `sync.py`. Entra no dia seguinte.
- **Promo avulso:** `videos\oneuse\` + `sync.py`. O runner posta e tira do release.
- **Postar no LinkedIn/TikTok:** `sync.py` (ou `fetch_today.py`) → `output\carousels\carousel_<data>\`
  → `video.mp4` (TikTok, página), `linkedin.jpg` + `linkedin.txt` (perfil; o link vai no 1º comentário).
- **Desligar algo sem mexer em código:** `config.yaml` — `youtube.enabled`,
  `instagram.enabled`, `carousel.publish_reel`, `media.ai_image.enabled`,
  `linkedin.enabled`, `subtitles.*`, `music.*`, `video.*`.

### Comandos do `main.py`

| Comando | Faz |
|---|---|
| `python main.py daily` | a rotina completa (oneuse → loop → carrossel), idempotente por dia |
| `python main.py carousel ["termo"]` | só o carrossel (publica!) — `"termo"` filtra a notícia |
| `python main.py loop` / `oneuse` | só os promos; `loop ig` / `oneuse yt` restringe a plataforma |
| `python main.py news` | lista as manchetes que ele encontraria hoje |
| `python main.py youtube-test` | confere a autorização do YouTube (não sobe nada) |
| `python main.py setup` | pastas, ffmpeg, estado das chaves |
| `python main.py reels <video>` | sobe um vídeo qualquer como Reel |

`short`, `listicle`, `long`, `post`, `cars`, `comparison` são do template de
origem (canal de notícias de games) e não fazem parte da rotina.

---

## Mapa do código

```
main.py                 comandos; `daily` = a rotina
src/carousel.py         notícia -> roteiro (prompt + guards de gancho/tamanho) -> slides -> vídeo -> uploads
src/ai_image.py         foto IA por notícia (Gemini), cache diário + biblioteca offline
src/linkedin_card.py    card 4:5 e texto do LinkedIn
src/promo.py            oneuse e loop (rodízio least-used-first)
src/pipeline.py         síntese da narração por blocos (contexto + respiro), ajuste de duração
src/tts_elevenlabs.py   pool de chaves ElevenLabs, language_code, previous/next_text
src/subtitles.py        legenda ASS: karaokê por frase, glow, ênfase, chips, hook card
src/video.py            clipes (ken burns), xfade, música/ducking, whoosh, loudnorm, barra
src/youtube_upload.py   upload agendado, slots 12:00/18:00 + fallback de hora cheia, synthetic flag
src/instagram_upload.py Reels via staging público temporário
scripts/sync.py         PC <-> GitHub;  scripts/fetch_today.py;  scripts/ci_bootstrap.py
.github/workflows/daily.yml
config.yaml             TUDO que é ajustável, comentado
ROTINA.md               manual operacional
```

### Estado (versionado, em `output/`)

`daily_state.json` (etapas do dia) · `loop_rotation.json` · `schedule_state.json`
(slots reservados) · `history.json` (notícias já usadas) · `hook_history.json`
(aberturas já usadas) · `media_usage.json` (rodízio de mídia) · `keyword_cache.json`.

### O que NÃO está no repo

`.env`, `token.json`, `client_secret.json`, `output/instagram_token.json`
(secrets do GitHub) · `assets/docs/` (procedimentos internos e brief) ·
`videos/*.mp4` (fontes dos promos) · os vídeos do rodízio (release).

---

## Instalação local (backup)

```powershell
pip install -r requirements.txt
copy .env.example .env          # preencha as chaves
python main.py setup
python main.py youtube-test     # autoriza o YouTube uma vez (token.json)
```

ffmpeg vem embutido (`imageio-ffmpeg`). Fontes em `assets/fonts/` (Archivo Black,
Poppins — OFL), iguais no Windows e no runner.

---

## Regras editoriais (resumo do brief)

Nenhuma marca de farmácia real · nenhum número de economia sem dado · nenhuma
interface inventada por IA (telas do app são capturas reais; fotos IA só nos
blocos de notícia, sem texto nem logos, e o vídeo é marcado como
*synthetic media* no YouTube) · sem promessa de cura · sempre o selo
*"Buscador de preços. A compra é feita na farmácia."* · notícias sobre pessoas
em sofrimento saem sem o app.

## Problemas comuns

| Sintoma | Onde olhar |
|---|---|
| não publicou hoje | aba Actions; as 4 janelas retentam sozinhas; e-mail do GitHub em falha |
| `[carousel] rejected (weak hook)` 3× | o guard de gancho (`carousel.hook_*` no config); afrouxe ou ajuste a lista |
| Gemini 503 em todas as chaves | normal em horário de pico; o próximo disparo resolve; Groq é o fallback |
| ElevenLabs sem crédito | cai para edge-tts (Thalita) sozinho; `python main.py setup` mostra o pool |
| Instagram `code 190` | token invalidado (troca de senha): gere outro, atualize o secret `INSTAGRAM_ACCESS_TOKEN` |
| YouTube `uploadLimitExceeded` | quota diária do app Cloud (dividida com outro canal); espera o reset |



# run this cmd to fetch processed file from todAY
python scripts\fetch_today.py
