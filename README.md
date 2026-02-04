# VS Claude Code Voice Mode

MCP server providing voice I/O for Claude Code in VS Code.

## Components

- **vs_claude_code_voice_mode_server.py** - MCP server with voice tools:
  - `speak(text)` - Send text to AllTalk TTS for audio playback
  - `listen()` - Capture mic audio via Whisper STT, return transcribed text
  - `converse(message)` - Speak a message then listen for a spoken response
  - `set_voice(voice)` - Change the AllTalk TTS voice
  - `voice_status()` - Check status of TTS/STT services
  - `service()` - Manage Whisper, Kokoro, VoiceMode, and Connect services

- **tts_hook.py** - Claude Code Stop hook that auto-speaks assistant responses via AllTalk TTS

- **mic_panel.py** - Floating microphone control panel with:
  - Push to Talk (hold button)
  - Toggle to Talk (click to start/stop)
  - Always On with Mute
  - Mic volume slider
  - Minimize to system tray

## Requirements

- [AllTalk TTS](https://github.com/erew123/alltalk_tts) running on `http://127.0.0.1:7851`
- [Whisper STT](https://github.com/openai/whisper) running on `http://127.0.0.1:8787`
- Python packages: `numpy`, `sounddevice`, `requests`, `mcp`
- Optional: `pystray`, `Pillow` (for system tray support)

## Setup

1. Install dependencies:
   ```
   pip install numpy sounddevice requests mcp
   ```

2. Start AllTalk TTS and Whisper STT services

3. Configure Claude Code to use this MCP server in your Claude Code settings
