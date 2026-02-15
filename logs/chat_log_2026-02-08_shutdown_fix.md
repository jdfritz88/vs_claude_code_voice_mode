# Chat Log: Shutdown Fix for AllTalk & Whisper Services
**Date:** 2026-02-08 to 2026-02-09
**Branch:** Branch04_claude_code_voice_mode-streaming_fixed

---

## Session Summary

User requested fixing the "Shutdown Services" feature in the mic panel (`mic_panel.py`). The shutdown buttons were not working — servers remained running and terminals stayed open.

---

## Timeline of Changes & Findings

### 1. Initial Problem Discovery

**Original code** used `taskkill /fi "WINDOWTITLE eq AllTalk TTS"` to kill services. This never worked because the Python server processes have **blank window titles** (verified via PowerShell). The titles set by `start "AllTalk TTS" cmd /k ...` in the bat file don't persist once Python takes over.

### 2. Investigation: Server Architecture

**AllTalk TTS (port 7851):**
- Two-process architecture: `cmd.exe → start_alltalk.bat → script.py → tts_server.py` (subprocess)
- `script.py` has a SIGINT handler that calls `process.terminate()` + `process.wait()` on tts_server.py, saves config, then exits
- AllTalk warns: "Please use Ctrl+C when exiting AllTalk otherwise a subprocess may continue running in the background"
- After script.py exits, cmd.exe shows "Terminate batch job (Y/N)?" prompt requiring "y" + Enter

**Whisper STT (port 8787):**
- Single process: `cmd.exe → server.py` (uvicorn directly)
- No custom signal handlers, relies on uvicorn's built-in Ctrl+C handling
- No "Terminate batch job" prompt — server exits cleanly, cmd returns to prompt

**Key files investigated:**
- `F:\Apps\freedom_system\app_cabinet\alltalk_tts\script.py` — signal handler at line 1071
- `F:\Apps\freedom_system\app_cabinet\alltalk_tts\tts_server.py` — uvicorn server
- `F:\Apps\freedom_system\app_cabinet\whisper_stt\server.py` — simple uvicorn server
- `F:\Apps\freedom_system\app_cabinet\alltalk_tts\start_alltalk.bat` — conda env + python script.py

### 3. Iteration 1: Port-based killing with Ctrl+C

**Approach:** Find server PID via `netstat -ano`, send Ctrl+C via Windows console API (`AttachConsole` + `GenerateConsoleCtrlEvent`), type "y" + Enter via `WriteConsoleInputW`, wait for port to free, kill parent cmd.exe.

**Methods added to `mic_panel.py`:**
- `_find_pid_on_port(port)` — runs netstat, parses PID
- `_find_parent_pid(pid)` — uses wmic to find parent PID and name
- `_send_console_ctrl_c(pid)` — AttachConsole + GenerateConsoleCtrlEvent + WriteConsoleInputW
- `_write_console_keys(text)` — writes key events to console input buffer
- `_wait_for_port_free(port, timeout)` — polls with socket.connect_ex
- `_graceful_shutdown_service(port, service_name)` — orchestrates full shutdown

**Result:** Ctrl+C was sent successfully to AllTalk (showed "Received Ctrl+C, terminating subprocess") but WriteConsoleInputW failed silently — the "y" was never typed. Whisper received no Ctrl+C at all.

### 4. Iteration 2: Fix 64-bit handle truncation

**Hypothesis:** `kernel32.GetStdHandle()` returns a HANDLE (pointer-sized, 8 bytes) but ctypes defaults to `c_int` (4 bytes). Handle gets truncated on 64-bit Python.

**Fix applied:** Added proper `restype`/`argtypes` declarations:
```python
kernel32.GetStdHandle.restype = ctypes.wintypes.HANDLE
kernel32.WriteConsoleInputW.restype = ctypes.wintypes.BOOL
```

Also added `kernel32.FreeConsole()` before `AttachConsole()` to handle sequential service shutdown.

**Result:** "y" appeared in Whisper's terminal (typed as command since no batch prompt), but still didn't appear in AllTalk's terminal. The Whisper server shut down cleanly but typed "y" as a command at the cmd prompt.

### 5. Iteration 3: Fix timing + use CreateFileW

**Two problems identified:**
1. **Timing:** "y" was sent 0.5s after Ctrl+C, but AllTalk's signal handler takes longer (terminate subprocess + wait + save config). The batch prompt hadn't appeared yet.
2. **GetStdHandle doesn't work for pythonw:** Since mic_panel runs as `pythonw` (no console), `GetStdHandle(STD_INPUT_HANDLE)` returns invalid handle even after AttachConsole.

**Fixes applied:**
- Replaced `GetStdHandle` with `CreateFileW("CONIN$")` — opens console input directly
- Restructured to stay attached to console: send Ctrl+C → wait for port to free → THEN send "y\r" → detach
- Renamed `_send_console_ctrl_c` to `_attach_and_send_ctrl_c` (returns True, caller must FreeConsole)
- Added `_detach_console()` method

**Result (from screenshots):**
- AllTalk: Ctrl+C worked, "y" was typed successfully! Shows `Terminate batch job (Y/N)? y` then returns to cmd prompt. **Server turned off.** But terminal stayed open.
- Whisper: Server was NOT turned off at all.

### 6. DMAIC Investigation of Remaining Bugs

**Read mic panel log** (`F:\Apps\freedom_system\log\claude_code_voice_mode_mic_panel.log`):

```
Line 60: [INFO] Shutting down AllTalk TTS (PID 14180 on port 7851)...
Line 61: [INFO] Ctrl+C sent to console of PID 14180
Line 62: [INFO] AllTalk TTS shut down gracefully
Line 63: [INFO] AllTalk TTS shutdown complete
Line 64: [ERROR] Failed to find PID on port 8787: [WinError 6] The handle is invalid
Line 65: [WARNING] No process found on port 8787 for Whisper STT
```

**Root Cause A — Whisper never shuts down:**
After AllTalk shutdown, `_detach_console()` calls `FreeConsole()` which invalidates pythonw's stdin handle. Then `_find_pid_on_port(8787)` runs `subprocess.run(['netstat', '-ano'])` which inherits the now-invalid stdin handle → `[WinError 6]`. The `subprocess.run` call has `capture_output=True` (redirects stdout/stderr) but does NOT set `stdin` — it inherits from parent.

**Root Cause B — AllTalk terminal stays open:**
Log shows NO "Closing terminal window" message. `_find_parent_pid` only goes up 1 level. AllTalk's tree: `cmd.exe → script.py → tts_server.py`. We find tts_server.py (port 7851), its parent is script.py (python.exe), NOT cmd.exe. The `parent_name == 'cmd.exe'` check fails, terminal kill is skipped.

**Root Cause C — Blank terminal during launch:**
Bat line 45: `start "" cmd /c "cd /d ... && pythonw mic_panel.py"` opens a visible cmd window.

---

## Current State of Code (as of end of session)

### mic_panel.py — Current shutdown methods:

```python
def _find_pid_on_port(self, port):
    # Uses subprocess.run(['netstat', '-ano']) with capture_output=True, CREATE_NO_WINDOW
    # BUG: Missing stdin=subprocess.DEVNULL

def _find_parent_pid(self, pid):
    # Uses wmic to get parent PID, then wmic again to get parent name
    # BUG: Only goes up 1 level (finds python.exe for AllTalk, not cmd.exe)
    # BUG: Missing stdin=subprocess.DEVNULL

def _attach_and_send_ctrl_c(self, pid):
    # FreeConsole → AttachConsole → SetConsoleCtrlHandler → GenerateConsoleCtrlEvent
    # Returns True if attached (caller must FreeConsole)
    # WORKS correctly

def _detach_console(self):
    # FreeConsole + SetConsoleCtrlHandler(None, False)
    # WORKS but invalidates stdin for subsequent subprocess.run calls

def _write_console_keys(self, text):
    # Uses CreateFileW("CONIN$") + WriteConsoleInputW with proper 64-bit types
    # WORKS correctly (verified: "y" appeared in AllTalk terminal)

def _wait_for_port_free(self, port, timeout=10):
    # Polls with socket.connect_ex
    # WORKS correctly

def _graceful_shutdown_service(self, port, service_name, timeout=10):
    # Find PID → find parent → attach+ctrl_c → wait for port → send "y\r" → detach → kill parent
    # Partially works: AllTalk server shuts down, but terminal not closed, Whisper not reached
```

### start_claude_code_voice_mode.bat changes:
- Lines 150-162: Port-based shutdown using `for /f` + `netstat` + `taskkill /pid`
- BUG: Line 45 still uses `cmd /c` wrapper for mic panel launch

---

## Approved Plan (PENDING IMPLEMENTATION)

### Fix A: Add `stdin=subprocess.DEVNULL` to all subprocess.run calls
- `_find_pid_on_port` line 441
- `_find_parent_pid` lines 456, 466
- `_graceful_shutdown_service` force-kill calls lines 617, 627, 635

### Fix B: Rewrite `_find_parent_pid` → `_find_console_ancestor_pid`
Walk up the process tree (max 5 levels) until cmd.exe is found.

### Fix C: Fix blank terminal
Change bat line 45 to: `start "" /d "F:\Apps\freedom_system\REPO_claude_code_voice_mode" pythonw mic_panel.py`

### Cleanup: Delete `logs/b01.py`

---

## Other Notes

- Created branch: `Branch04_claude_code_voice_mode-streaming_fixed`
- Deleted junk files: `%TEMP%branch01_server.py`, `F:Appsfreedom_systemREPO_claude_code_voice_modelogsbranch01_server.py`, `logsb01.py`, `nul`
- Chrome tabs (AllTalk Gradio on 7852, Whisper on 8787) remain open after shutdown — not yet addressed
- AllTalk opens browser via Gradio's `app.launch()` — configurable via `confignew.json` `launch_gradio` setting
- The bat file's own "press any key" shutdown (line 144-164) also uses port-based killing but without Ctrl+C (just taskkill)
- `logs/b01.py` still exists in logs folder — needs deletion

## Key Files
- `F:\Apps\freedom_system\REPO_claude_code_voice_mode\mic_panel.py` — main file being modified
- `F:\Apps\freedom_system\REPO_claude_code_voice_mode\start_claude_code_voice_mode.bat` — launcher
- `F:\Apps\freedom_system\log\claude_code_voice_mode_mic_panel.log` — mic panel log (critical for debugging)
- `F:\Apps\freedom_system\REPO_claude_code_voice_mode\logs\claude_code_voice_mode.log` — conversation log
- `F:\Apps\freedom_system\standards\DMAIC_coding_process_tool.md` — debugging process reference
- Plan file: `C:\Users\jespe\.claude\plans\moonlit-drifting-seahorse.md`
