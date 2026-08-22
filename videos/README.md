# videos/ — videos PRONTOS de divulgacao

Nada aqui e gerado pelo pipeline. Sao os seus videos finalizados, publicados
como estao (com o audio e a montagem originais).

| pasta | comando | comportamento |
|---|---|---|
| `oneuse/` | `python main.py oneuse` | publica TUDO e move para `oneuse/posted/`. Nunca repete. |
| `loop/` | `python main.py loop` | publica UM do rodizio. O arquivo fica; quando todos rodaram, comeca novo ciclo. |

Limitar plataforma: `python main.py oneuse ig` (ou `yt`).

## Legenda e titulo

Opcional, num arquivo com o mesmo nome do video:

    meu_video.mp4
    meu_video.txt     <- texto inteiro vira a legenda; a 1a linha vira o titulo
    meu_video.json    <- {"title": "...", "description": "...", "tags": [...]}

Sem isso, o titulo sai do nome do arquivo.

## Nao confundir com assets/videos/

`assets/videos/` e pool de **b-roll**: aqueles clipes entram MUDOS e cortados,
como imagem de fundo atras da narracao das noticias. Aqui o video sobe inteiro.
