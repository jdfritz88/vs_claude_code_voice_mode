"""
Claude Code Voice Mode MCP Server
Custom MCP server providing voice I/O for Claude Code.
- speak(text): Sends text to AllTalk TTS, plays audio
- listen(): Captures mic audio, sends to Whisper STT, returns text
- converse(message): Full loop - speak message, listen for response
- set_voice(voice): Changes AllTalk voice
"""
import asyncio
import io
import json
import logging
import struct
import tempfile
import threading
import time
import wave
from pathlib import Path
from typing import Optional

import numpy as np
import requests
import sounddevice as sd
from mcp.server import Server
from mcp.types import TextContent, Tool

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
ALLTALK_URL = "http://127.0.0.1:7851"
WHISPER_URL = "http://127.0.0.1:8787"
DEFAULT_VOICE = "Freya.wav"
SAMPLE_RATE = 16000
CHANNELS = 1
VAD_AGGRESSIVENESS = 2  # 0-3, higher = more aggressive filtering

LOG_FILE = Path("F:/Apps/freedom_system/log/claude_code_voice_mode.log")
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="[CLAUDE_CODE_VOICE_MODE] [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(str(LOG_FILE), encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Global state
# ---------------------------------------------------------------------------
current_voice = DEFAULT_VOICE
mic_muted = False
mic_mode = "push_to_talk"  # push_to_talk, toggle, always_on
STATE_FILE = Path("F:/Apps/freedom_system/REPO_claude_code_voice_mode/mic_state.json")


def is_tts_paused() -> bool:
    """Check if TTS is paused by reading the shared state file."""
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return state.get("tts_paused", False)
    except Exception:
        return False


def _wait_for_playback_or_pause():
    """Wait for audio playback to complete, or stop if TTS is paused."""
    while True:
        try:
            stream = sd.get_stream()
            if not stream.active:
                break
        except RuntimeError:
            break
        if is_tts_paused():
            sd.stop()
            logger.info("Playback stopped: TTS paused")
            break
        time.sleep(0.1)


# ---------------------------------------------------------------------------
# Audio helpers
# ---------------------------------------------------------------------------
def numpy_to_wav_bytes(audio: np.ndarray, sample_rate: int = SAMPLE_RATE) -> bytes:
    """Convert numpy float32 audio array to WAV bytes."""
    audio_int16 = (audio * 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(audio_int16.tobytes())
    return buf.getvalue()


def record_audio(duration: float = 5.0, silence_timeout: float = 2.0) -> np.ndarray:
    """
    Record audio from microphone with Voice Activity Detection.
    Stops when silence is detected for silence_timeout seconds,
    or when max duration is reached.
    """
    import webrtcvad

    vad = webrtcvad.Vad(VAD_AGGRESSIVENESS)
    frame_duration_ms = 30  # ms per VAD frame
    frame_size = int(SAMPLE_RATE * frame_duration_ms / 1000)
    max_frames = int(duration * SAMPLE_RATE / frame_size)
    silence_frames_threshold = int(silence_timeout * 1000 / frame_duration_ms)

    logger.info(f"Recording... (max {duration}s, silence timeout {silence_timeout}s)")

    all_frames = []
    silence_count = 0
    speech_detected = False

    def audio_callback(indata, frames, time_info, status):
        nonlocal silence_count, speech_detected
        if status:
            logger.warning(f"Audio status: {status}")
        if mic_muted:
            return
        all_frames.append(indata.copy())

    with sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=CHANNELS,
        dtype="int16",
        blocksize=frame_size,
        callback=audio_callback,
    ):
        for _ in range(max_frames):
            sd.sleep(frame_duration_ms)
            if not all_frames:
                continue

            # Check last frame for voice activity
            last_frame = all_frames[-1].flatten().tobytes()
            if len(last_frame) == frame_size * 2:  # 2 bytes per int16 sample
                is_speech = vad.is_speech(last_frame, SAMPLE_RATE)
                if is_speech:
                    speech_detected = True
                    silence_count = 0
                else:
                    silence_count += 1

                # Stop if we had speech and now have enough silence
                if speech_detected and silence_count >= silence_frames_threshold:
                    logger.info("Silence detected, stopping recording.")
                    break

    if not all_frames:
        return np.array([], dtype=np.float32)

    audio = np.concatenate(all_frames).flatten().astype(np.float32) / 32768.0
    logger.info(f"Recorded {len(audio) / SAMPLE_RATE:.1f}s of audio")
    return audio


def play_audio_from_url(url: str):
    """Download audio from URL and play it through speakers."""
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()

        # Write to temp file and play
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp.write(response.content)
            tmp_path = tmp.name

        import av
        container = av.open(tmp_path)
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
            _wait_for_playback_or_pause()

        Path(tmp_path).unlink(missing_ok=True)
    except Exception as e:
        logger.error(f"Audio playback failed: {e}")


def play_audio_bytes(audio_bytes: bytes):
    """Play raw audio bytes (WAV format) through speakers."""
    try:
        buf = io.BytesIO(audio_bytes)
        import av
        container = av.open(buf)
        audio_stream = next(s for s in container.streams if s.type == "audio")

        frames = []
        target_rate = 24000
        resampler = av.AudioResampler(format="s16", layout="mono", rate=target_rate)
        for frame in container.decode(audio_stream):
            resampled = resampler.resample(frame)
            for r in resampled:
                frames.append(r.to_ndarray().flatten())

        if frames:
            audio = np.concatenate(frames).astype(np.float32) / 32768.0
            sd.play(audio, samplerate=target_rate)
            _wait_for_playback_or_pause()
    except Exception as e:
        logger.error(f"Audio playback failed: {e}")


# ---------------------------------------------------------------------------
# TTS: Send text to AllTalk
# ---------------------------------------------------------------------------
def speak_text(text: str, voice: Optional[str] = None) -> dict:
    """Send text to AllTalk TTS and play the result."""
    if is_tts_paused():
        logger.info("TTS is paused, skipping speech")
        return {"status": "paused", "message": "TTS is currently paused via mic panel"}
    voice = voice or current_voice
    logger.info(f"Speaking: '{text[:80]}...' with voice={voice}")

    # Try OpenAI-compatible endpoint first
    try:
        response = requests.post(
            f"{ALLTALK_URL}/v1/audio/speech",
            json={
                "input": text,
                "voice": voice,
                "model": "tts-1",
                "response_format": "wav",
            },
            timeout=30,
        )
        if response.status_code == 200:
            play_audio_bytes(response.content)
            return {"status": "spoken", "voice": voice, "length": len(text)}
    except Exception as e:
        logger.warning(f"OpenAI endpoint failed, trying legacy: {e}")

    # Fallback to AllTalk native endpoint
    try:
        payload = {
            "text_input": text,
            "text_filtering": "standard",
            "character_voice_gen": voice,
            "narrator_enabled": "false",
            "narrator_voice_gen": "",
            "text_not_inside": "character",
            "language": "en",
            "output_file_name": "claude_code_voice_mode",
            "output_file_timestamp": "true",
            "autoplay": "false",
            "autoplay_volume": "0.8",
            "speed": "1.0",
            "pitch": "1.0",
            "temperature": "0.75",
            "repetition_penalty": "1.0",
        }
        response = requests.post(
            f"{ALLTALK_URL}/api/tts-generate",
            data=payload,
            timeout=30,
        )
        if response.status_code == 200:
            result = response.json()
            audio_url = result.get("output_file_url", "")
            if audio_url:
                if not audio_url.startswith("http"):
                    audio_url = f"{ALLTALK_URL}{audio_url}"
                play_audio_from_url(audio_url)
                return {"status": "spoken", "voice": voice, "length": len(text)}
    except Exception as e:
        logger.error(f"TTS generation failed: {e}")

    return {"status": "error", "message": "TTS generation failed"}


# ---------------------------------------------------------------------------
# STT: Send audio to Whisper
# ---------------------------------------------------------------------------
def transcribe_audio(audio: np.ndarray) -> str:
    """Send audio to Whisper STT and return transcribed text."""
    wav_bytes = numpy_to_wav_bytes(audio)
    logger.info(f"Sending {len(wav_bytes)} bytes to Whisper STT...")

    try:
        response = requests.post(
            f"{WHISPER_URL}/v1/audio/transcriptions",
            files={"file": ("recording.wav", wav_bytes, "audio/wav")},
            data={"model": "whisper-1", "language": "en"},
            timeout=30,
        )
        if response.status_code == 200:
            result = response.json()
            text = result.get("text", "").strip()
            logger.info(f"Transcribed: '{text}'")
            return text
        else:
            logger.error(f"Whisper returned {response.status_code}: {response.text}")
            return ""
    except Exception as e:
        logger.error(f"Transcription failed: {e}")
        return ""


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------
server = Server("claude-code-voice-mode")


@server.list_tools()
async def list_tools():
    return [
        Tool(
            name="speak",
            description="Convert text to speech using AllTalk TTS and play through speakers. Use this to read responses aloud.",
            inputSchema={
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "The text to speak aloud",
                    },
                    "voice": {
                        "type": "string",
                        "description": f"Voice to use (default: {DEFAULT_VOICE})",
                    },
                },
                "required": ["text"],
            },
        ),
        Tool(
            name="listen",
            description="Record audio from microphone and transcribe using Whisper STT. Returns the transcribed text. Use this when the user wants to speak instead of type.",
            inputSchema={
                "type": "object",
                "properties": {
                    "duration": {
                        "type": "number",
                        "description": "Max recording duration in seconds (default: 10)",
                    },
                    "silence_timeout": {
                        "type": "number",
                        "description": "Stop after this many seconds of silence (default: 2)",
                    },
                },
            },
        ),
        Tool(
            name="converse",
            description="Full voice conversation: speak a message through TTS, then listen for the user's spoken response via STT. Returns the user's transcribed speech.",
            inputSchema={
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "Message to speak before listening",
                    },
                    "listen_duration": {
                        "type": "number",
                        "description": "Max listen duration in seconds (default: 10)",
                    },
                },
                "required": ["message"],
            },
        ),
        Tool(
            name="set_voice",
            description="Change the TTS voice used by AllTalk. List available voices at http://127.0.0.1:7851/api/voices",
            inputSchema={
                "type": "object",
                "properties": {
                    "voice": {
                        "type": "string",
                        "description": "Voice filename (e.g., 'Freya.wav', 'Arnold.wav')",
                    },
                },
                "required": ["voice"],
            },
        ),
        Tool(
            name="voice_status",
            description="Check status of AllTalk TTS and Whisper STT services, and list available voices.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict):
    global current_voice

    if name == "speak":
        text = arguments.get("text", "")
        voice = arguments.get("voice")
        result = await asyncio.to_thread(speak_text, text, voice)
        return [TextContent(type="text", text=json.dumps(result))]

    elif name == "listen":
        duration = arguments.get("duration", 10.0)
        silence_timeout = arguments.get("silence_timeout", 2.0)
        audio = await asyncio.to_thread(record_audio, duration, silence_timeout)
        if len(audio) == 0:
            return [TextContent(type="text", text="No audio captured.")]
        text = await asyncio.to_thread(transcribe_audio, audio)
        return [TextContent(type="text", text=text if text else "Could not transcribe audio.")]

    elif name == "converse":
        message = arguments.get("message", "")
        listen_duration = arguments.get("listen_duration", 10.0)

        # Speak the message first
        if message:
            await asyncio.to_thread(speak_text, message)

        # Then listen for response
        audio = await asyncio.to_thread(record_audio, listen_duration, 2.0)
        if len(audio) == 0:
            return [TextContent(type="text", text="No response heard.")]
        text = await asyncio.to_thread(transcribe_audio, audio)
        return [TextContent(type="text", text=text if text else "Could not transcribe response.")]

    elif name == "set_voice":
        voice = arguments.get("voice", DEFAULT_VOICE)
        current_voice = voice
        logger.info(f"Voice changed to: {current_voice}")
        return [TextContent(type="text", text=f"Voice set to: {current_voice}")]

    elif name == "voice_status":
        status = {"alltalk": "unknown", "whisper": "unknown", "voice": current_voice, "voices": [], "tts_paused": is_tts_paused()}
        try:
            r = requests.get(f"{ALLTALK_URL}/api/ready", timeout=3)
            status["alltalk"] = "ready" if r.status_code == 200 else f"error ({r.status_code})"
        except Exception as e:
            status["alltalk"] = f"offline ({e})"
        try:
            r = requests.get(f"{WHISPER_URL}/health", timeout=3)
            status["whisper"] = "ready" if r.status_code == 200 else f"error ({r.status_code})"
        except Exception as e:
            status["whisper"] = f"offline ({e})"
        try:
            r = requests.get(f"{ALLTALK_URL}/api/voices", timeout=3)
            if r.status_code == 200:
                status["voices"] = r.json() if isinstance(r.json(), list) else r.json().get("voices", [])
        except Exception:
            pass
        return [TextContent(type="text", text=json.dumps(status, indent=2))]

    return [TextContent(type="text", text=f"Unknown tool: {name}")]


async def main():
    from mcp.server.stdio import stdio_server

    logger.info("Claude Code Voice Mode MCP server starting...")
    logger.info(f"AllTalk TTS: {ALLTALK_URL}")
    logger.info(f"Whisper STT: {WHISPER_URL}")
    logger.info(f"Default voice: {DEFAULT_VOICE}")

    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
