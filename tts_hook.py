"""
TTS Stop Hook for Claude Code
Fires when Claude finishes a response. Reads the last assistant message
from the transcript and sends it to AllTalk TTS for audio playback.

Used as a Claude Code Stop hook to auto-speak all responses.
"""
import io
import json
import sys
import tempfile
from pathlib import Path

import time

import numpy as np
import requests
import sounddevice as sd

ALLTALK_URL = "http://127.0.0.1:7851"
DEFAULT_VOICE = "Freya.wav"
MAX_SPEAK_LENGTH = 2000  # Don't speak responses longer than this
STATE_FILE = Path("F:/Apps/freedom_system/REPO_claude_code_voice_mode/mic_state.json")


def is_tts_paused() -> bool:
    """Check if TTS is paused by reading the shared state file."""
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return state.get("tts_paused", False)
    except Exception:
        return False


def get_last_assistant_message(transcript_path: str) -> str:
    """Read the transcript JSONL file and extract the last assistant message."""
    try:
        lines = Path(transcript_path).read_text(encoding="utf-8").strip().split("\n")
        for line in reversed(lines):
            try:
                entry = json.loads(line)
                if entry.get("role") == "assistant":
                    # Extract text content
                    content = entry.get("content", "")
                    if isinstance(content, list):
                        text_parts = [
                            c.get("text", "")
                            for c in content
                            if isinstance(c, dict) and c.get("type") == "text"
                        ]
                        return " ".join(text_parts)
                    return str(content)
            except json.JSONDecodeError:
                continue
    except Exception:
        pass
    return ""


def speak(text: str):
    """Send text to AllTalk and play audio."""
    if not text or len(text) > MAX_SPEAK_LENGTH:
        return
    if is_tts_paused():
        return

    # Strip markdown formatting for cleaner speech
    import re
    text = re.sub(r'```[\s\S]*?```', ' code block omitted ', text)
    text = re.sub(r'`[^`]+`', '', text)
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
    text = re.sub(r'[#*_~|>]', '', text)
    text = re.sub(r'\n+', '. ', text)
    text = text.strip()

    if not text:
        return

    try:
        response = requests.post(
            f"{ALLTALK_URL}/v1/audio/speech",
            json={
                "input": text[:MAX_SPEAK_LENGTH],
                "voice": DEFAULT_VOICE,
                "model": "tts-1",
                "response_format": "wav",
            },
            timeout=30,
        )
        if response.status_code == 200:
            import av
            buf = io.BytesIO(response.content)
            container = av.open(buf)
            audio_stream = next(s for s in container.streams if s.type == "audio")
            frames = []
            resampler = av.AudioResampler(format="s16", layout="mono", rate=24000)
            for frame in container.decode(audio_stream):
                resampled = resampler.resample(frame)
                for r in resampled:
                    frames.append(r.to_ndarray().flatten())
            if frames:
                audio = np.concatenate(frames).astype(np.float32) / 32768.0
                sd.play(audio, samplerate=24000)
                while True:
                    try:
                        stream = sd.get_stream()
                        if not stream.active:
                            break
                    except RuntimeError:
                        break
                    if is_tts_paused():
                        sd.stop()
                        break
                    time.sleep(0.1)
    except Exception:
        pass  # Don't block Claude on TTS failure


def main():
    """Read hook input from stdin and speak the last response."""
    try:
        hook_input = json.loads(sys.stdin.read())
        transcript_path = hook_input.get("transcript_path", "")
        if transcript_path:
            text = get_last_assistant_message(transcript_path)
            speak(text)
    except Exception:
        pass  # Never block Claude

    sys.exit(0)


if __name__ == "__main__":
    main()
