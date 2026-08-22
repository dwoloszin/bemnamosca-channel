# Rotina diária — Bem na Mosca

## O comando

```
python main.py daily
```

Roda sozinho todo dia às **11:00** (tarefa `BemNaMoscaDaily`). Ele faz três etapas, nesta ordem — uploads primeiro,
render lento por último, para que uma falha no vídeo não impeça as publicações.

---

## 1/3 `oneuse` — promos finalizados

Lê `videos/oneuse/`. Para **cada** vídeo lá dentro:

- sobe no YouTube como Short (agendado)
- publica no Instagram como Reel (**ao vivo na hora**)
- move o arquivo para `videos/oneuse/posted/` — nunca repete

Pasta vazia = etapa ignorada sem erro. É onde você joga um promo que deve
sair **uma vez só**.

## 2/3 `loop` — promos perenes

Lê `videos/loop/` (hoje: 16 pitches). Publica **um** por execução:

- escolhe entre os que ainda não saíram no ciclo atual
- quando todos rodaram, começa um ciclo novo e todos voltam a ficar disponíveis
- o arquivo **não** é movido

Com 16 vídeos, cada um reaparece a cada 16 dias.

Título e legenda vêm de `promo.default_titles` (8 títulos, distribuídos por
posição) e `promo.default_caption`. Um `meu_video.txt` ao lado do arquivo
sobrescreve os dois.

## 3/3 `carousel` — o conteúdo de notícia do dia

Busca a notícia, escreve os 5 blocos (hook / notícia / solução / aplicação /
conclusão) e gera em `output/carousels/carousel_<data>/`:

| arquivo | destino |
|---|---|
| `slide_1..5.jpg` | **manual** — Instagram e LinkedIn |
| `carousel.txt` | legenda do carrossel |
| `post.txt` | post único, texto completo |
| `video.mp4` | YouTube (automático) + **manual** no TikTok e LinkedIn |
| `linkedin.jpg` | **manual** — card único para o LinkedIn (cores do logo, manchete real, 4 pontos, tela do app). |
| `linkedin.txt` | **manual** — texto do post do LinkedIn + o link para colar no 1º comentário |

---

## O que é automático e o que é seu

**Automático:** YouTube (agendado) e Instagram Reels.

**Manual, todo dia:**
1. os 5 slides + `carousel.txt` → Instagram e LinkedIn
2. o `video.mp4` → TikTok e LinkedIn
3. o `linkedin.jpg` + `linkedin.txt` → LinkedIn (1 imagem + texto; o link vai no **primeiro comentário**, nunca no corpo. Telas do app em rodízio, só remédios — `linkedin.screens`)

---

## Agendamento do YouTube

`youtube.privacy: scheduled` sobe o vídeo **privado** com hora marcada. O
YouTube publica sozinho. São **dois horários por dia: 12:00 e 18:00**
(Brasília), e cada upload pega o próximo livre, registrado em
`output/schedule_state.json`. Exige 20 minutos de margem.

Enquanto está privado dá para revisar no Studio e deletar ou desagendar.

### ⚠️ A conta de slots

São **2 slots por dia** e a rotina gera **2 vídeos por dia** (loop + carrossel)
quando `videos/oneuse/` está vazio. Bate exato.

**Cada vídeo em `oneuse/` quebra esse equilíbrio.** Três uploads num dia com
dois slots empurra a fila para frente, e ela não volta sozinha. O agendador
procura só **8 dias à frente**; passando disso o vídeo sobe privado e **sem
data**, ficando parado até você publicar à mão.

Se for usar `oneuse` com frequência, some um terceiro horário em
`youtube.schedule.times`.

## Instagram não agenda

Não existe agendamento nem modo privado na API. **Reel publicado está no ar.**
A rotina publica **2 Reels por dia** (loop + carrossel). Se for demais para a
conta, desligue `carousel.publish_reel` e deixe o carrossel só no YouTube.

## Quota do YouTube

Cada upload custa ~1.600 das 10.000 unidades/dia do projeto no Google Cloud,
que é **compartilhado com o canal GTA News**. Teto de ~6 uploads/dia somando os
dois canais.

---

## Interruptores

| chave | o que controla |
|---|---|
| `youtube.enabled` | mestre do YouTube |
| `carousel.publish_youtube` | o vídeo do carrossel vai para o YouTube |
| `instagram.enabled` | mestre do Instagram |
| `carousel.publish_reel` | o vídeo do carrossel vira Reel |
| `instagram.post_enabled` | post de imagem no feed (via `main.py post`) |

Todos **ligados** desde 14/08/2026.

---

## Vozes da narração

**Em uso hoje:** `pt-BR-FranciscaNeural` (edge-tts) — feminina, **nativa
pt-BR**, gratuita e sem consumo de créditos. Definida em `tts.voice`, com
`tts.provider: edge`.

### edge-tts (grátis, nativas)

| voz | |
|---|---|
| `pt-BR-FranciscaNeural` | clássica, calorosa, estável — **em uso** |
| `pt-BR-ThalitaMultilingualNeural` | modelo mais novo, mais expressiva, oscila mais |
| `pt-BR-AntonioNeural` | masculina (era o padrão herdado do canal antigo) |

`python main.py voices pt` lista o que estiver instalado.

### ElevenLabs (alternativa, consome créditos)

Só entra em uso se `tts.provider` virar `elevenlabs`. **Definido em
`tts.elevenlabs.voice_id`; hoje aponta para a Alice como alternativa.**

Testadas gerando áudio real na chave gratuita em 14/08/2026:

| voz | voice_id | perfil |
|---|---|---|
| **Alice** | `Xb7hH8MSUJpSbSDYk0k2` | britânica, firme — **alternativa escolhida** |
| Matilda | `XrExE9yKIg1WjnnlVkGX` | calorosa |
| Bella | `EXAVITQu4vr4xnSDxMaL` | suave |
| Lily | `pFZP5JQG7iQjIQuC4Bku` | britânica, jovem |

Responderam `402 paid_plan_required` (exigem plano pago): **Rachel**
`21m00Tcm4TlvDq8ikWAM`, **Domi** `AZnzlk1XvdvUeBnXmlld`, **Elli**
`MF3mGyEYCl7XYWbV9V6O`, **Charlotte** `XB0fDUnXU5powFXDhCwa`.

> ⚠️ Todas as da ElevenLabs são locutoras americanas ou britânicas. O modelo
> `eleven_turbo_v2_5` fala português, mas **com sotaque** — nenhuma soa nativa
> como a Francisca. Por isso o padrão continua no edge-tts.

Amostras de todas, dizendo a mesma frase, em `output/amostras_voz/`.

Para trocar, uma linha: `tts.provider: "elevenlabs"`.

As chaves ficam em `ELEVENLABS_API_KEYS` no `.env` (várias, separadas por
vírgula; o pool rotaciona quando os créditos de uma acabam). Nenhuma delas tem
a permissão `voices_read`, então não dá para listar o catálogo pela API — foi
por isso que os IDs acima foram descobertos testando síntese, um a um.

## Comandos avulsos

```
python main.py carousel            só o carrossel
python main.py carousel "diabetes" filtra a notícia por termo
python main.py oneuse              só a caixa de entrada
python main.py loop                só o próximo do rodízio
python main.py oneuse ig           limita a plataforma (ig | yt)
python main.py short               short de notícia (fora da rotina)
python main.py youtube-test        confere a conexão, não publica nada
```

## Verificação rápida

```
type output\schedule_state.json     horários já reservados
type output\loop_rotation.json      ciclo e quais pitches já saíram
dir videos\oneuse                   o que ainda vai sair
dir output\carousels                pacotes gerados
```
