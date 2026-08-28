@echo off
REM ==========================================================================
REM  Rotina diaria do Bem na Mosca. Chamado pelo Agendador de Tarefas.
REM
REM    1/3 oneuse    publica videos/oneuse/ e arquiva em posted/
REM    2/3 loop      publica 1 pitch do rodizio
REM    3/3 carousel  gera slides + legenda + post unico + video, sobe no YT
REM
REM  Roda as 11:00 para o agendamento do YouTube alcancar o slot das 12:00
REM  (o agendador exige 20 minutos de margem).
REM
REM  Se algo falhar, um arquivo de ALERTA aparece na Area de Trabalho.
REM ==========================================================================
setlocal EnableDelayedExpansion
set "PROJECT_DIR=%~dp0.."
cd /d "%PROJECT_DIR%"

if exist ".venv\Scripts\python.exe" (
    set "PY=.venv\Scripts\python.exe"
) else (
    set "PY=python"
)

set "LOG=output\daily_log.txt"
set "RUNLOG=%TEMP%\bemnamosca_run.txt"

REM Retaguarda do GitHub Actions: antes de rodar, puxa o estado que o runner
REM gravou. Se o GitHub ja publicou hoje, daily_state.json diz "feito" e o
REM `python main.py daily` termina sem publicar nada — por isso esta tarefa
REM pode ficar LIGADA junto com os crons sem risco de post duplicado.
git pull --rebase origin main >nul 2>&1

if not exist "output" mkdir "output"

echo ===== %date% %time% ===== > "%RUNLOG%"

REM UTF-8 no console: a narracao e as legendas tem acento, e o cp1252 padrao
REM do Windows quebrava o log com UnicodeEncodeError no meio da execucao.
REM
REM -u (sem buffer): com o stdout ligado a um pipe, o Python guarda a saida em
REM blocos de 8 KB e so descarrega no fim. Em 18/08 a rotina travou as 10:05 e
REM ficou 1h20 sem escrever UMA linha no RUNLOG; quando o processo travado foi
REM encerrado, tudo o que ele tinha impresso se perdeu junto e o dia nao deixou
REM registro nenhum. Com a escada de retentativas a execucao pode durar 2h, e
REM sem -u nao ha como acompanhar nada enquanto ela roda.
powershell -NoProfile -Command ^
  "$OutputEncoding = [Console]::OutputEncoding = [System.Text.Encoding]::UTF8; & '%PY%' -u main.py daily 2>&1 | ForEach-Object { $_; $_ | Out-File -FilePath '%RUNLOG%' -Append -Encoding utf8 }"
set "RC=%errorlevel%"

type "%RUNLOG%" >> "%LOG%"

REM Falhou de duas formas possiveis: codigo de saida != 0, ou uma plataforma
REM especifica que falhou sem derrubar a execucao inteira (o oneuse arquiva o
REM video mesmo com falha parcial, entao so o log denuncia).
set "ALERTA="
if not "%RC%"=="0" set "ALERTA=1"
findstr /I /C:"FAILED" /C:"Traceback" /C:"upload failed" "%RUNLOG%" >nul 2>&1
if not errorlevel 1 set "ALERTA=1"

if defined ALERTA (
    set "ALERTFILE=%USERPROFILE%\Desktop\ALERTA_BEMNAMOSCA.txt"
    echo Rotina do Bem na Mosca com problema em %date% %time% > "!ALERTFILE!"
    echo Codigo de saida: %RC% >> "!ALERTFILE!"
    echo. >> "!ALERTFILE!"
    echo Log completo: %PROJECT_DIR%\%LOG% >> "!ALERTFILE!"
    echo. >> "!ALERTFILE!"
    echo ---- ultimas linhas ---- >> "!ALERTFILE!"
    powershell -NoProfile -Command "Get-Content '%RUNLOG%' -Tail 25" >> "!ALERTFILE!"
    echo [%date% %time%] FALHOU - veja !ALERTFILE!
) else (
    echo [%date% %time%] OK. Log em %LOG%
    REM Sucesso apaga o alerta antigo, senao um erro de semana passada fica
    REM na Area de Trabalho parecendo problema de hoje.
    if exist "%USERPROFILE%\Desktop\ALERTA_BEMNAMOSCA.txt" del "%USERPROFILE%\Desktop\ALERTA_BEMNAMOSCA.txt"
)

echo.
echo LEMBRETE: postar A MAO o carrossel (slide_1..5.jpg + carousel.txt)
echo no Instagram e LinkedIn, e o video.mp4 no TikTok e LinkedIn.
echo Pasta: output\carousels\
endlocal
