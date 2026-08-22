@echo off
setlocal EnableDelayedExpansion
title GTA 6 Channel - Daily Runner
cd /d "%~dp0"

REM ---- single-instance lock (double-clicking twice opened 2 windows once,
REM      which raced the schedule slots and duplicated uploads) --------------
REM Lock name derives from THIS project folder, so a cloned channel in another
REM folder can run at the same time without blocking this one.
for %%I in ("%~dp0.") do set "PROJDIR=%%~nxI"
set "LOCKDIR=%TEMP%\daily_%PROJDIR%.lock"
mkdir "%LOCKDIR%" 2>nul
if errorlevel 1 (
    echo.
    echo   Another daily.bat window is ALREADY RUNNING.
    echo   Close this window and use the other one.
    echo   ^(If you are sure nothing is running, delete: %LOCKDIR%^)
    pause
    exit /b 1
)

REM Pick the venv Python if one exists.
if exist ".venv\Scripts\python.exe" (set "PY=.venv\Scripts\python.exe") else (set "PY=python")

REM ---- log everything the pack does (for diagnosing failed uploads) --------
if not exist "output" mkdir output
for /f %%I in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd"') do set "STAMP=%%I"
set "LOG=output\daily_%STAMP%.log"

echo ============================================================
echo   GTA 6 CHANNEL - DAILY RUNNER
echo ============================================================
echo   [1] DAILY PACK (auto in 10s):
echo       2 Shorts scheduled for the next free slots
echo       (12:00 / 18:00 Brasilia - see config youtube.schedule)
echo       + 1 community post package
echo       + Instagram photo auto-post if instagram.post_enabled=true
echo   [2] Short only            (news Short)
echo   [3] Listicle only         (TOP 5 countdown)
echo   [4] Post - trending topic (auto)
echo   [5] Post - MY topic       (type it here)
echo   [6] Cars comparison       (paste prompt, end with END)
echo   [7] Vehicles showcase
echo   [8] Long video
echo   [9] Setup / key status
echo   [I] Instagram - publish LAST post package (photo, LIVE now)
echo   [Q] Quit
echo ============================================================

choice /C 123456789IQ /T 10 /D 1 /N /M "Choose [1-9,I,Q] (auto-runs 1 in 10s): "
set "PICK=%ERRORLEVEL%"

if "%PICK%"=="11" goto :end
if "%PICK%"=="10" ("%PY%" main.py igpost & goto :done)

if "%PICK%"=="1" (
    REM ---- same-day guard: refuse a second pack on the same day -----------
    set "TODAY=%date%"
    set "LASTRUN="
    if exist "output\last_pack_day.txt" set /p LASTRUN=<"output\last_pack_day.txt"
    if "!LASTRUN!"=="!TODAY!" (
        echo.
        echo   The DAILY PACK already ran today ^(!TODAY!^).
        echo   Running it again would upload duplicate videos.
        choice /C SN /T 15 /D N /N /M "  Run AGAIN anyway? [S/N] (auto-No in 15s): "
        if errorlevel 2 goto :done
    )
    echo !TODAY!>"output\last_pack_day.txt"
    echo.
    echo === [%date% %time%] DAILY PACK ===============================
    echo --- 1/3 Short #1 - news  [next slot: 12:00 or 18:00 BRT] ---
    call :runlog main.py short
    echo --- 2/3 Short #2 - TOP 5 [following slot] ---
    call :runlog main.py listicle
    echo --- 3/3 Community post package [paste manually] ---
    call :runlog main.py post
    goto :done
)
if "%PICK%"=="2" ("%PY%" main.py short & goto :done)
if "%PICK%"=="3" ("%PY%" main.py listicle & goto :done)
if "%PICK%"=="4" ("%PY%" main.py post & goto :done)
if "%PICK%"=="5" (
    set /p TOPIC="Type your post topic (e.g. GTA 6 trailer 3 release date): "
    "%PY%" main.py post "!TOPIC!"
    goto :done
)
if "%PICK%"=="6" ("%PY%" main.py cars short & goto :done)
if "%PICK%"=="7" ("%PY%" main.py cars showcase & goto :done)
if "%PICK%"=="8" ("%PY%" main.py long & goto :done)
if "%PICK%"=="9" ("%PY%" main.py setup & goto :done)

:done
echo.
echo ============================================================
echo   Finished. Shorts are SCHEDULED (12:00 / 18:00 Brasilia) -
echo   review them in YouTube Studio before the slot; they go
echo   public automatically. Post package: output\posts\
echo   Instagram post goes LIVE immediately when the API accepts it.
echo   Full log: %LOG%
echo ============================================================
rmdir "%LOCKDIR%" 2>nul
pause
goto :end

:runlog
REM run a python command showing output on screen AND appending to the log
echo === [%time%] %PY% %* >>"%LOG%"
powershell -NoProfile -Command "$OutputEncoding = [Console]::OutputEncoding = [System.Text.Encoding]::UTF8; & '%PY%' %* 2>&1 | ForEach-Object { $_; $_ | Out-File -FilePath '%LOG%' -Append -Encoding utf8 }"
goto :eof

:end
rmdir "%LOCKDIR%" 2>nul
endlocal
