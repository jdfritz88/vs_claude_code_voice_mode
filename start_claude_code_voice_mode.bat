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

REM --- Detect already-running services ---
set MIC_RUNNING=0
set WHISPER_RUNNING=0
set ALLTALK_RUNNING=0

tasklist /fi "WINDOWTITLE eq Claude Code Voice Mode Mic*" 2>nul | find /i "python" >nul 2>&1
if not errorlevel 1 set MIC_RUNNING=1

curl -s http://127.0.0.1:8787/health >nul 2>&1
if not errorlevel 1 set WHISPER_RUNNING=1

curl -s http://127.0.0.1:7851/api/ready >nul 2>&1
if not errorlevel 1 set ALLTALK_RUNNING=1

REM --- Start Microphone Control Panel (no console window) ---
if "%MIC_RUNNING%"=="1" (
    echo [1/3] Microphone Control Panel already running - skipping
) else (
    echo [1/3] Starting Microphone Control Panel...
    start "" /d "F:\Apps\freedom_system\REPO_claude_code_voice_mode" venv\Scripts\pythonw.exe mic_panel.py
)

REM --- Start Whisper STT ---
if "%WHISPER_RUNNING%"=="1" (
    echo [2/3] Whisper STT already running on port 8787 - skipping
) else (
    echo [2/3] Starting Whisper STT on port 8787...
    start "Whisper STT" cmd /k "cd /d F:\Apps\freedom_system\app_cabinet\whisper_stt && call venv\Scripts\activate.bat && python server.py"
)

REM --- Start AllTalk TTS (delay so its window appears in front) ---
if "%ALLTALK_RUNNING%"=="1" (
    echo [3/3] AllTalk TTS already running on port 7851 - skipping
) else (
    timeout /t 4 /nobreak >nul 2>&1
    echo [3/3] Starting AllTalk TTS on port 7851...
    start "AllTalk TTS" cmd /k "cd /d F:\Apps\freedom_system\app_cabinet\alltalk_tts && call start_alltalk.bat"
)

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

REM --- Extract folder name from selected directory ---
for %%F in ("!SELECTED_DIR!") do set "FOLDER_NAME=%%~nxF"

REM --- Capture all cmd.exe command lines for searching ---
set "WMIC_DUMP="
for /f "usebackq skip=1 tokens=*" %%L in (`wmic process where "name='cmd.exe'" get commandline 2^>nul`) do (
    set "WMIC_DUMP=!WMIC_DUMP! %%L"
)

REM --- Find first available instance number (01-99) ---
set "NEXT_NUM="
for /l %%I in (1,1,99) do (
    if not defined NEXT_NUM (
        if %%I lss 10 (set "TEST_NUM=0%%I") else (set "TEST_NUM=%%I")
        echo !WMIC_DUMP! | findstr /i /c:"title !FOLDER_NAME!_!TEST_NUM!" >nul 2>&1
        if errorlevel 1 set "NEXT_NUM=!TEST_NUM!"
    )
)
if not defined NEXT_NUM set "NEXT_NUM=01"

set "TERMINAL_NAME=!FOLDER_NAME!_!NEXT_NUM!"
echo  Terminal name: !TERMINAL_NAME!

endlocal & set "SELECTED_DIR=%SELECTED_DIR%" & set "TERMINAL_NAME=%TERMINAL_NAME%"

start "%TERMINAL_NAME%" cmd /k "title %TERMINAL_NAME% && cd /d %SELECTED_DIR% && echo. && echo  Claude Code Voice Mode is ready. && echo  Terminal: %TERMINAL_NAME% && echo  AllTalk TTS: http://127.0.0.1:7851 && echo  Whisper STT: http://127.0.0.1:8787 && echo. && echo  Type your commands below. && echo."
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

REM --- Kill by port: find PID, try graceful then force ---
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":7851 " ^| findstr "LISTENING"') do (
    echo   Stopping AllTalk TTS (PID %%P)...
    taskkill /pid %%P >nul 2>&1
    timeout /t 5 /nobreak >nul 2>&1
    taskkill /pid %%P /t /f >nul 2>&1
)
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":8787 " ^| findstr "LISTENING"') do (
    echo   Stopping Whisper STT (PID %%P)...
    taskkill /pid %%P >nul 2>&1
    timeout /t 5 /nobreak >nul 2>&1
    taskkill /pid %%P /t /f >nul 2>&1
)
REM --- Kill all Claude Code terminals (by command line pattern) ---
setlocal enabledelayedexpansion
for /f "tokens=2 delims=," %%A in ('tasklist /fi "IMAGENAME eq cmd.exe" /fo csv /nh 2^>nul') do (
    set "CPID=%%~A"
    if defined CPID (
        wmic process where "processid=!CPID!" get commandline 2>nul | findstr /i /c:"title " | findstr /r "_[0-9][0-9]" >nul 2>&1
        if not errorlevel 1 (
            echo   Stopping Claude Code terminal ^(PID !CPID!^)...
            taskkill /pid !CPID! /t /f >nul 2>&1
        )
    )
)
endlocal
echo Done.
