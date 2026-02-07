@echo off
echo ========================================
echo  Claude Code Voice Mode Launcher
echo  AllTalk TTS  :  port 7851
echo  Whisper STT  :  port 8787
echo ========================================
echo.
echo  Choose launch mode:
echo    1. Terminal (manual CLI)
echo    2. VS Code (workspace)
echo.
choice /C 12 /N /M "Enter choice (1 or 2): "
if errorlevel 2 goto vscode_mode
if errorlevel 1 goto terminal_mode

:terminal_mode
set LAUNCH_MODE=terminal
goto start_services

:vscode_mode
set LAUNCH_MODE=vscode
goto start_services

:start_services

REM --- Start AllTalk TTS ---
echo [1/3] Starting AllTalk TTS on port 7851...
start "AllTalk TTS" cmd /k "cd /d F:\Apps\freedom_system\app_cabinet\alltalk_tts && call start_alltalk.bat"

REM --- Start Whisper STT ---
echo [2/3] Starting Whisper STT on port 8787...
start "Whisper STT" cmd /k "cd /d F:\Apps\freedom_system\app_cabinet\whisper_stt && call venv\Scripts\activate.bat && python server.py"

REM --- Start Microphone Control Panel (no console window) ---
echo [3/3] Starting Microphone Control Panel...
start "" cmd /c "cd /d F:\Apps\freedom_system\REPO_claude_code_voice_mode && call venv\Scripts\activate.bat && pythonw mic_panel.py"

REM --- Wait for services ---
echo.
echo Waiting for services to be ready...
:wait_loop
timeout /t 3 /nobreak >nul 2>&1

REM Check AllTalk
curl -s http://127.0.0.1:7851/api/ready >nul 2>&1
if errorlevel 1 (
    echo   Waiting for AllTalk TTS...
    goto wait_loop
)

REM Check Whisper
curl -s http://127.0.0.1:8787/health >nul 2>&1
if errorlevel 1 (
    echo   Waiting for Whisper STT...
    goto wait_loop
)

echo.
echo ========================================
echo  All services ready!
echo  AllTalk TTS:  http://127.0.0.1:7851
echo  Whisper STT:  http://127.0.0.1:8787
echo  Mic Panel:    Running
echo ========================================
echo.

if "%LAUNCH_MODE%"=="vscode" goto launch_vscode
goto launch_terminal

:launch_vscode
echo Starting VS Code with Claude Code...
"F:\Apps\VSCode\bin\code.cmd" "F:\Apps\freedom_system"
goto running

:launch_terminal
setlocal enabledelayedexpansion
echo.
echo ========================================
echo  Select working directory:
echo ========================================
echo.
echo   1. F:\Apps\freedom_system
set "DIR_1=F:\Apps\freedom_system"
set "DIR_COUNT=1"

for /d %%D in ("F:\Apps\freedom_system\REPO_*") do (
    set /a DIR_COUNT+=1
    echo   !DIR_COUNT!. %%~nxD
    set "DIR_!DIR_COUNT!=%%D"
)

echo.
set "DIR_CHOICE="
set /p "DIR_CHOICE=Enter choice (1-!DIR_COUNT!): "
if not defined DIR_CHOICE set "DIR_CHOICE=1"
call set "SELECTED_DIR=%%DIR_!DIR_CHOICE!%%"
if not defined SELECTED_DIR (
    echo Invalid choice. Defaulting to F:\Apps\freedom_system
    set "SELECTED_DIR=F:\Apps\freedom_system"
)

echo.
echo Opening Terminal in: !SELECTED_DIR!
endlocal & set "SELECTED_DIR=%SELECTED_DIR%"

start "Claude Code Terminal" cmd /k "cd /d %SELECTED_DIR% && echo. && echo  Claude Code Voice Mode is ready. && echo  AllTalk TTS: http://127.0.0.1:7851 && echo  Whisper STT: http://127.0.0.1:8787 && echo. && echo  Type your commands below. && echo."
goto running

:running
echo.
echo ========================================
echo  Claude Code Voice Mode is running.
echo  Close this window to shut down all
echo  voice services.
echo ========================================
echo.
echo Press any key to shut down all services...
pause >nul

echo.
echo Shutting down services...
taskkill /fi "WINDOWTITLE eq AllTalk TTS" /t /f >nul 2>&1
taskkill /fi "WINDOWTITLE eq Whisper STT" /t /f >nul 2>&1
taskkill /fi "WINDOWTITLE eq Claude Code Terminal" /t /f >nul 2>&1
echo Done.
