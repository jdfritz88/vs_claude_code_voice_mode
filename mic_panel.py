"""
Claude Code Voice Mode Microphone Control Panel
Always-on-top floating window with:
- Push to Talk (hold button)
- Toggle to Talk (click to start/stop)
- Mic volume slider
- Minimize to system tray
"""
import ctypes
import ctypes.wintypes
import io
import json
import logging
import os
import queue
import re
import socket
import struct
import subprocess
import sys
import threading
import time
import tkinter as tk
import wave
from tkinter import ttk, messagebox
from pathlib import Path

import numpy as np
import requests
import sounddevice as sd

try:
    import pystray
    from PIL import Image, ImageDraw
    HAS_TRAY = True
except ImportError:
    HAS_TRAY = False

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
WHISPER_URL = "http://127.0.0.1:8787"
SAMPLE_RATE = 16000
CHANNELS = 1
STATE_FILE = Path("F:/Apps/freedom_system/REPO_claude_code_voice_mode/mic_state.json")
LOG_FILE = Path("F:/Apps/freedom_system/log/claude_code_voice_mode_mic_panel.log")
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

LOG_FORMAT = "[MIC_PANEL] [%(levelname)s] %(message)s"
logging.basicConfig(
    level=logging.INFO,
    format=LOG_FORMAT,
    handlers=[
        logging.FileHandler(str(LOG_FILE), encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


class TextHandler(logging.Handler):
    """Logging handler that writes to a tkinter Text widget (thread-safe)."""

    def __init__(self, text_widget):
        super().__init__()
        self.text_widget = text_widget

    def emit(self, record):
        msg = self.format(record) + "\n"
        self.text_widget.after(0, self._append, msg)

    def _append(self, msg):
        self.text_widget.config(state=tk.NORMAL)
        self.text_widget.insert(tk.END, msg)
        self.text_widget.see(tk.END)
        self.text_widget.config(state=tk.DISABLED)


# ---------------------------------------------------------------------------
# Shared mic state (read by MCP server)
# ---------------------------------------------------------------------------
def save_mic_state(state: dict):
    """Save mic state to file for MCP server to read."""
    try:
        STATE_FILE.write_text(json.dumps(state), encoding="utf-8")
    except Exception as e:
        logger.error(f"Failed to save mic state: {e}")


def create_tray_icon_image(color="green"):
    """Create a small colored circle icon for system tray."""
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    colors = {
        "green": (0, 200, 0, 255),
        "red": (200, 0, 0, 255),
        "yellow": (200, 200, 0, 255),
        "gray": (128, 128, 128, 255),
    }
    fill = colors.get(color, colors["gray"])
    draw.ellipse([4, 4, 60, 60], fill=fill, outline=(255, 255, 255, 255), width=2)
    return img


class MicControlPanel:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Claude Code Voice Mode Mic")
        self.root.geometry("280x820")
        self.root.resizable(False, True)
        self.root.attributes("-topmost", True)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        # State
        self.mode = tk.StringVar(value="push_to_talk")
        self.is_recording = False
        self.is_muted = True
        self.volume = tk.IntVar(value=50)
        self.audio_queue = queue.Queue()
        self.audio_stream = None
        self.level_value = 0.0
        self.tray_icon = None
        self.hidden = False
        self.tts_paused = False
        self.selected_device = tk.StringVar(value="Windows Default")
        self._level_monitor_stop = threading.Event()
        self._input_devices = self._query_input_devices()
        self._recording_frames = []
        self._recording_lock = threading.Lock()
        self._processing = False
        self._discovered_terminals = []       # list of (name, pid) tuples
        self._selected_terminal = tk.StringVar(value="")

        self._build_ui()
        self._setup_console_logging()
        self._update_state()
        self._start_level_monitor()
        self._refresh_terminals()

    def _query_input_devices(self):
        """Query available input devices, filtered to the default host API (MME on Windows)."""
        devices = sd.query_devices()
        default_hostapi = sd.query_hostapis(0)  # MME is typically index 0
        default_hostapi_idx = 0

        input_devices = []
        for i, d in enumerate(devices):
            if d["max_input_channels"] > 0 and d["hostapi"] == default_hostapi_idx:
                # Skip the "Microsoft Sound Mapper" which IS the Windows default
                if "sound mapper" in d["name"].lower():
                    continue
                input_devices.append({"index": i, "name": d["name"]})
        return input_devices

    def _get_selected_device_index(self):
        """Resolve the selected device name to a sounddevice index, or None for default."""
        name = self.selected_device.get()
        if name == "Windows Default":
            return None
        for d in self._input_devices:
            if d["name"] == name:
                return d["index"]
        return None

    def _build_ui(self):
        """Build the tkinter UI."""
        # Target Terminal selector (very top)
        terminal_frame = tk.LabelFrame(
            self.root, text="Target Terminal", font=("Segoe UI", 9), padx=10, pady=5
        )
        terminal_frame.pack(fill=tk.X, padx=10, pady=(5, 0))

        terminal_inner = tk.Frame(terminal_frame)
        terminal_inner.pack(fill=tk.X)

        self.terminal_combo = ttk.Combobox(
            terminal_inner, textvariable=self._selected_terminal,
            values=[], state="readonly", font=("Segoe UI", 8)
        )
        self.terminal_combo.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.terminal_refresh_btn = tk.Button(
            terminal_inner, text="\u21bb", font=("Segoe UI", 10),
            width=3, command=self._refresh_terminals
        )
        self.terminal_refresh_btn.pack(side=tk.RIGHT, padx=(5, 0))

        # Input device selector
        device_frame = tk.LabelFrame(
            self.root, text="Input Device", font=("Segoe UI", 9), padx=10, pady=5
        )
        device_frame.pack(fill=tk.X, padx=10, pady=(5, 0))

        device_names = ["Windows Default"] + [d["name"] for d in self._input_devices]
        self.device_combo = ttk.Combobox(
            device_frame, textvariable=self.selected_device,
            values=device_names, state="readonly", font=("Segoe UI", 8)
        )
        self.device_combo.pack(fill=tk.X)
        self.device_combo.bind("<<ComboboxSelected>>", self._on_device_change)

        # Title
        title_frame = tk.Frame(self.root, bg="#2b2b2b")
        title_frame.pack(fill=tk.X, padx=0, pady=0)
        tk.Label(
            title_frame, text="Claude Code Voice Mode", font=("Segoe UI", 12, "bold"),
            bg="#2b2b2b", fg="white", pady=8
        ).pack()

        # Status indicator
        self.status_frame = tk.Frame(self.root, bg="#1e1e1e")
        self.status_frame.pack(fill=tk.X, padx=10, pady=(10, 5))
        self.status_label = tk.Label(
            self.status_frame, text="Muted", font=("Segoe UI", 10),
            bg="#1e1e1e", fg="#ff8800", pady=4
        )
        self.status_label.pack()

        # Audio level meter
        level_frame = tk.Frame(self.root)
        level_frame.pack(fill=tk.X, padx=10, pady=5)
        tk.Label(level_frame, text="Level:", font=("Segoe UI", 9)).pack(side=tk.LEFT)
        self.level_bar = ttk.Progressbar(level_frame, length=200, mode="determinate", maximum=100)
        self.level_bar.pack(side=tk.LEFT, padx=(5, 0), fill=tk.X, expand=True)

        # Mode selection
        mode_frame = tk.LabelFrame(self.root, text="Mode", font=("Segoe UI", 9), padx=10, pady=5)
        mode_frame.pack(fill=tk.X, padx=10, pady=5)

        tk.Radiobutton(
            mode_frame, text="Push to Talk", variable=self.mode, value="push_to_talk",
            font=("Segoe UI", 9), command=self._on_mode_change
        ).pack(anchor=tk.W)

        self._rb_toggle = tk.Radiobutton(
            mode_frame, text="Toggle to Talk", variable=self.mode, value="toggle",
            font=("Segoe UI", 9), command=self._on_mode_change
        )
        self._rb_toggle.pack(anchor=tk.W)

        self._update_mode_labels()

        # Main action button
        btn_frame = tk.Frame(self.root)
        btn_frame.pack(fill=tk.X, padx=10, pady=10)

        self.action_btn = tk.Button(
            btn_frame, text="Hold to Talk", font=("Segoe UI", 11, "bold"),
            bg="#4CAF50", fg="white", activebackground="#45a049",
            relief=tk.RAISED, bd=2, height=2
        )
        self.action_btn.pack(fill=tk.X)
        self.action_btn.bind("<ButtonPress-1>", self._on_button_press)
        self.action_btn.bind("<ButtonRelease-1>", self._on_button_release)

        # Mute button
        self.mute_btn = tk.Button(
            btn_frame, text="Unmute", font=("Segoe UI", 9),
            bg="#f44336", fg="white", command=self._toggle_mute
        )
        self.mute_btn.pack(fill=tk.X, pady=(5, 0))

        # Volume slider
        vol_frame = tk.LabelFrame(self.root, text="Mic Volume", font=("Segoe UI", 9), padx=10, pady=5)
        vol_frame.pack(fill=tk.X, padx=10, pady=5)

        self.vol_slider = tk.Scale(
            vol_frame, from_=0, to=100, resolution=1,
            orient=tk.HORIZONTAL, variable=self.volume,
            font=("Segoe UI", 8), command=self._on_volume_change
        )
        self.vol_slider.pack(fill=tk.X)

        # TTS Control
        tts_frame = tk.LabelFrame(self.root, text="TTS Control", font=("Segoe UI", 9), padx=10, pady=5)
        tts_frame.pack(fill=tk.X, padx=10, pady=5)

        self.tts_pause_btn = tk.Button(
            tts_frame, text="Pause TTS", font=("Segoe UI", 10, "bold"),
            bg="#FF9800", fg="white", activebackground="#F57C00",
            command=self._toggle_tts_pause
        )
        self.tts_pause_btn.pack(fill=tk.X)

        # Embedded console log
        console_frame = tk.LabelFrame(self.root, text="Console", font=("Segoe UI", 9), padx=5, pady=5)
        console_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        console_scroll = tk.Scrollbar(console_frame)
        console_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.console_text = tk.Text(
            console_frame, height=8, font=("Consolas", 8),
            bg="#1e1e1e", fg="#cccccc", insertbackground="#cccccc",
            state=tk.DISABLED, wrap=tk.WORD,
            yscrollcommand=console_scroll.set
        )
        self.console_text.pack(fill=tk.BOTH, expand=True)
        console_scroll.config(command=self.console_text.yview)

        # Shutdown Services dropdown
        shutdown_frame = tk.Frame(self.root)
        shutdown_frame.pack(fill=tk.X, padx=10, pady=(5, 0))

        self.shutdown_mb = tk.Menubutton(
            shutdown_frame, text="Shutdown Services \u25bc", font=("Segoe UI", 9, "bold"),
            bg="#8e44ad", fg="white", activebackground="#7d3c98",
            relief=tk.RAISED, bd=2, padx=8, pady=4,
        )
        self.shutdown_mb.pack(fill=tk.X)

        shutdown_menu = tk.Menu(self.shutdown_mb, tearoff=0, font=("Segoe UI", 9))
        shutdown_menu.add_command(label="Close All (AllTalk + Whisper + Mic)", command=self._shutdown_all_services)
        shutdown_menu.add_separator()
        shutdown_menu.add_command(label="Close AllTalk Only", command=self._shutdown_alltalk)
        shutdown_menu.add_command(label="Close Whisper Only", command=self._shutdown_whisper)
        self.shutdown_mb.config(menu=shutdown_menu)

        # Bottom buttons
        bottom_frame = tk.Frame(self.root)
        bottom_frame.pack(fill=tk.X, padx=10, pady=(5, 10))

        if HAS_TRAY:
            tk.Button(
                bottom_frame, text="Minimize to Tray", font=("Segoe UI", 9),
                padx=8, pady=4, command=self._minimize_to_tray
            ).pack(side=tk.LEFT)

        tk.Button(
            bottom_frame, text="Quit Mic Panel", font=("Segoe UI", 9),
            bg="#c0392b", fg="white", activebackground="#a93226",
            padx=8, pady=4, command=self._quit
        ).pack(side=tk.RIGHT)

    def _on_mode_change(self):
        """Handle mode radio button change."""
        mode = self.mode.get()
        logger.info(f"Mode changed to: {mode}")

        if mode == "push_to_talk":
            self.action_btn.config(text="Hold to Talk")
            self._stop_recording()
        elif mode == "toggle":
            self.action_btn.config(text="Click to Talk")
            self._stop_recording()

        self._update_state()

    def _on_device_change(self, event=None):
        """Handle input device dropdown change."""
        device_name = self.selected_device.get()
        logger.info(f"Input device changed to: {device_name}")
        self._update_state()
        # Restart level monitor with the new device
        self._level_monitor_stop.set()
        self._level_monitor_stop = threading.Event()
        self._start_level_monitor()

    def _on_button_press(self, event=None):
        mode = self.mode.get()
        if mode == "push_to_talk":
            self._start_recording()
        elif mode == "toggle":
            if self.is_recording:
                self._stop_recording()
            else:
                self._start_recording()

    def _on_button_release(self, event=None):
        mode = self.mode.get()
        if mode == "push_to_talk":
            self._stop_recording()

    def _start_recording(self):
        """Start capturing microphone audio via the shared stream."""
        if self.is_recording:
            return
        if self._processing:
            logger.warning("Still processing previous recording, ignoring")
            return

        with self._recording_lock:
            self._recording_frames.clear()
        self._rms_log_counter = 0

        self.is_recording = True
        self.action_btn.config(bg="#f44336")
        self.status_label.config(text="Recording...", fg="#ff4444")
        self._update_state()
        logger.info("Recording started (using shared stream)")

    def _stop_recording(self):
        """Stop capturing microphone audio and begin processing."""
        if not self.is_recording:
            return
        self.is_recording = False

        mode = self.mode.get()
        if mode == "toggle":
            self.action_btn.config(bg="#4CAF50", text="Click to Talk")
        else:
            self.action_btn.config(bg="#4CAF50", text="Hold to Talk")

        self._update_state()

        with self._recording_lock:
            frame_count = len(self._recording_frames)
            has_frames = frame_count > 0

        if has_frames:
            self._processing = True
            self.status_label.config(text="Processing...", fg="#ffcc00")
            logger.info(f"Recording stopped, {frame_count} frames captured, processing...")
            thread = threading.Thread(target=self._process_recording, daemon=True)
            thread.start()
        else:
            self.status_label.config(text="Ready", fg="#00cc00")
            logger.info("Recording stopped (no frames captured)")

    def _toggle_mute(self):
        """Toggle microphone mute."""
        self.is_muted = not self.is_muted
        if self.is_muted:
            self.mute_btn.config(text="Unmute", bg="#f44336")
            self.status_label.config(text="Muted", fg="#ff8800")
        else:
            self.mute_btn.config(text="Mute", bg="#666")
            self.status_label.config(text="Ready", fg="#00cc00")
        self._update_state()
        self._update_mode_labels()
        logger.info(f"Mute: {self.is_muted}")

    def _update_mode_labels(self):
        """Update Toggle to Talk radio button label to show mute state."""
        state = "(Muted)" if self.is_muted else "(Unmuted)"
        self._rb_toggle.config(text=f"Toggle to Talk {state}")

    def _on_volume_change(self, value):
        """Handle volume slider change."""
        self._update_state()

    def _toggle_tts_pause(self):
        """Toggle TTS pause state."""
        self.tts_paused = not self.tts_paused
        if self.tts_paused:
            self.tts_pause_btn.config(text="Continue TTS", bg="#4CAF50")
            logger.info("TTS paused")
            try:
                requests.put("http://127.0.0.1:7851/api/stop-generation", timeout=2)
            except Exception as e:
                logger.warning(f"Failed to stop AllTalk generation: {e}")
        else:
            self.tts_pause_btn.config(text="Pause TTS", bg="#FF9800")
            logger.info("TTS resumed")
        self._update_state()

    def _setup_console_logging(self):
        """Attach a TextHandler to the logger so logs appear in the embedded console."""
        handler = TextHandler(self.console_text)
        handler.setFormatter(logging.Formatter(LOG_FORMAT))
        logger.addHandler(handler)

    def _update_state(self):
        """Save current state to file for MCP server."""
        device_name = self.selected_device.get()
        state = {
            "mode": self.mode.get(),
            "recording": self.is_recording,
            "muted": self.is_muted,
            "volume": self.volume.get(),
            "tts_paused": self.tts_paused,
            "input_device": None if device_name == "Windows Default" else device_name,
        }
        save_mic_state(state)

    # -----------------------------------------------------------------------
    # Hold to Talk: recording, transcription, and terminal injection
    # -----------------------------------------------------------------------
    def _set_status(self, text, color):
        """Update the status label (must be called from main thread)."""
        self.status_label.config(text=text, fg=color)

    def _reset_status_if_idle(self):
        """Reset status to Ready if not recording or processing."""
        if not self.is_recording and not self._processing:
            if self.is_muted:
                self.status_label.config(text="Muted", fg="#ff8800")
            else:
                self.status_label.config(text="Ready", fg="#00cc00")

    def _numpy_to_wav_bytes(self, audio_int16):
        """Convert numpy int16 audio array to WAV bytes."""
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(2)
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(audio_int16.tobytes())
        return buf.getvalue()

    def _transcribe_audio_direct(self, wav_bytes):
        """Send WAV bytes to Whisper STT and return transcribed text."""
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

    def _discover_claude_terminals(self):
        """Discover all Claude Code terminals by searching cmd.exe command lines.
        Returns list of (terminal_name, pid) tuples."""
        terminals = []
        try:
            result = subprocess.run(
                ['wmic', 'process', 'where', "name='cmd.exe'", 'get', 'processid,commandline'],
                capture_output=True, text=True, stdin=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            for line in result.stdout.splitlines():
                line = line.strip()
                if not line:
                    continue
                # Match "title FOLDER_NN" pattern in command line
                match = re.search(r'title\s+(\w+_\d{2})\b', line, re.IGNORECASE)
                if match:
                    terminal_name = match.group(1)
                    # PID is the last number sequence on the line (wmic default format)
                    pid_match = re.search(r'(\d+)\s*$', line)
                    if pid_match:
                        pid = int(pid_match.group(1))
                        terminals.append((terminal_name, pid))
                        logger.info(f"Discovered terminal: {terminal_name} (PID {pid})")
        except Exception as e:
            logger.error(f"Terminal discovery failed: {e}")
        return terminals

    def _get_selected_terminal_pid(self):
        """Get the PID of the currently selected terminal.
        Returns int PID or None."""
        name = self._selected_terminal.get()
        if not name:
            return None
        for term_name, pid in self._discovered_terminals:
            if term_name == name:
                return pid
        return None

    def _refresh_terminals(self):
        """Re-scan for Claude Code terminals and update the dropdown."""
        self._discovered_terminals = self._discover_claude_terminals()
        names = [name for name, pid in self._discovered_terminals]
        self.terminal_combo['values'] = names

        current = self._selected_terminal.get()
        if names:
            if current not in names:
                self._selected_terminal.set(names[0])
                logger.info(f"Auto-selected terminal: {names[0]}")
        else:
            self._selected_terminal.set("")
            logger.info("No Claude Code terminals found")

    def _inject_text_into_terminal(self, text):
        """Inject transcribed text into the selected Claude Code Terminal and press Enter.
        Returns True on success, False on failure."""
        if not text.strip():
            logger.warning("No text to inject")
            return False

        pid = self._get_selected_terminal_pid()
        if pid is None:
            # Try refreshing terminals first
            self.root.after(0, self._refresh_terminals)
            time.sleep(0.5)
            pid = self._get_selected_terminal_pid()
            if pid is None:
                logger.error("No Claude Code Terminal selected or found")
                return False

        kernel32 = ctypes.windll.kernel32
        kernel32.FreeConsole()

        if not kernel32.AttachConsole(pid):
            error_code = ctypes.GetLastError()
            logger.warning(f"AttachConsole({pid}) failed (error {error_code})")
            # Terminal might have closed — refresh and retry
            self.root.after(0, self._refresh_terminals)
            time.sleep(0.5)
            pid = self._get_selected_terminal_pid()
            if pid is None or not kernel32.AttachConsole(pid):
                logger.error("Cannot attach to Claude Code Terminal after retry")
                return False

        try:
            ok = self._write_console_keys(text)
            if not ok:
                logger.error("Failed to write text characters to console")
                return False

            time.sleep(0.05)

            ok = self._write_console_keys("\r")
            if not ok:
                logger.error("Failed to write Enter key to console")
                return False

            terminal_name = self._selected_terminal.get()
            logger.info(f"Injected {len(text)} chars + Enter into {terminal_name} (PID {pid})")
            return True
        finally:
            self._detach_console()

    def _process_recording(self):
        """Process accumulated recording: transcribe and inject into Claude Code terminal.
        Runs in a background thread."""
        try:
            with self._recording_lock:
                frames = self._recording_frames.copy()
                self._recording_frames.clear()

            if not frames:
                logger.warning("No audio frames captured")
                self.root.after(0, self._set_status, "No audio captured", "#ff8800")
                return

            self.root.after(0, self._set_status, "Transcribing...", "#ffcc00")

            audio = np.concatenate(frames).flatten()
            duration = len(audio) / SAMPLE_RATE
            rms = np.sqrt(np.mean(audio.astype(np.float32) ** 2))
            peak = np.max(np.abs(audio.astype(np.float32)))
            logger.info(
                f"Processing {duration:.1f}s of recorded audio "
                f"({len(frames)} frames, RMS={rms:.1f}, peak={peak:.0f})"
            )

            if rms < 50:
                logger.info(f"Recording was silence (RMS {rms:.1f} < 50), skipping transcription")
                self.root.after(0, self._set_status, "No speech detected", "#ff8800")
                return

            wav_bytes = self._numpy_to_wav_bytes(audio)
            logger.info(f"WAV size: {len(wav_bytes)} bytes")

            text = self._transcribe_audio_direct(wav_bytes)

            if not text:
                logger.warning("Transcription returned empty text")
                self.root.after(0, self._set_status, "No speech recognized", "#ff8800")
                return

            self.root.after(0, self._set_status, f"Sending: {text[:40]}...", "#00ccff")

            success = self._inject_text_into_terminal(text)

            if success:
                self.root.after(0, self._set_status, "Sent!", "#00cc00")
                logger.info(f"Successfully sent to terminal: '{text}'")
            else:
                self.root.after(0, self._set_status, "No terminal selected", "#ff4444")
                logger.error("Failed to inject text into terminal")

        except Exception as e:
            logger.error(f"Recording processing failed: {e}")
            self.root.after(0, self._set_status, f"Error: {e}", "#ff4444")
        finally:
            self._processing = False
            self.root.after(3000, self._reset_status_if_idle)

    def _start_level_monitor(self):
        """Start a background thread with a shared InputStream for level metering AND recording.

        One persistent stream avoids Bluetooth conflicts from opening multiple
        concurrent InputStreams on the same device.
        """
        stop_event = self._level_monitor_stop
        device_index = self._get_selected_device_index()
        device_name = self.selected_device.get()
        self._rms_log_counter = 0

        def shared_callback(indata, frames, time_info, status):
            if status:
                logger.warning(f"Audio stream status: {status}")
            if indata is None or len(indata) == 0:
                return

            multiplier = self.volume.get() / 50.0

            # Always: update level meter
            level = np.sqrt(np.mean(indata.astype(np.float32) ** 2)) * multiplier
            self.level_value = min(100, level * 500)

            # When recording: accumulate frames
            if self.is_recording:
                current_mode = self.mode.get()
                # In push_to_talk mode, pressing the button IS the unmute action
                if current_mode != "push_to_talk" and self.is_muted:
                    return
                scaled = (indata.copy().astype(np.float32) * multiplier).astype(np.int16)
                with self._recording_lock:
                    self._recording_frames.append(scaled)

                # Diagnostic: log RMS every ~1 second (every 16 callbacks at 1024 blocksize / 16kHz)
                self._rms_log_counter += 1
                if self._rms_log_counter % 16 == 0:
                    rms = np.sqrt(np.mean(scaled.astype(np.float32) ** 2))
                    logger.debug(f"Recording RMS: {rms:.1f}")

        def monitor():
            try:
                kwargs = {
                    "samplerate": SAMPLE_RATE, "channels": CHANNELS,
                    "dtype": "int16", "blocksize": 1024, "callback": shared_callback,
                }
                if device_index is not None:
                    kwargs["device"] = device_index

                logger.info(f"Shared audio stream opened on device: {device_name} (index={device_index})")
                with sd.InputStream(**kwargs):
                    while not stop_event.is_set():
                        sd.sleep(50)
                logger.info("Shared audio stream closed")
            except Exception as e:
                logger.error(f"Shared audio stream error: {e}")

        thread = threading.Thread(target=monitor, daemon=True)
        thread.start()

        def update_meter():
            self.level_bar["value"] = self.level_value
            self.root.after(50, update_meter)

        # Only start the meter updater once (first call)
        if not hasattr(self, '_meter_updater_started'):
            self._meter_updater_started = True
            self.root.after(50, update_meter)

    def _minimize_to_tray(self):
        """Hide window and show system tray icon."""
        if not HAS_TRAY:
            self.root.iconify()
            return

        self.root.withdraw()
        self.hidden = True

        icon_image = create_tray_icon_image("green")
        menu = pystray.Menu(
            pystray.MenuItem("Show", self._restore_from_tray),
            pystray.MenuItem("Quit", self._quit_from_tray),
        )
        self.tray_icon = pystray.Icon("claude_code_voice_mode", icon_image, "Claude Code Voice Mode", menu)

        tray_thread = threading.Thread(target=self.tray_icon.run, daemon=True)
        tray_thread.start()

    def _restore_from_tray(self, icon=None, item=None):
        """Restore window from system tray."""
        if self.tray_icon:
            self.tray_icon.stop()
            self.tray_icon = None
        self.hidden = False
        self.root.after(0, self.root.deiconify)

    def _quit_from_tray(self, icon=None, item=None):
        """Quit from tray icon menu."""
        if self.tray_icon:
            self.tray_icon.stop()
        self.root.after(0, self._quit)

    def on_close(self):
        """Handle window close button — minimize to tray instead of quitting."""
        if HAS_TRAY:
            self._minimize_to_tray()
        else:
            self._quit()

    def _find_pid_on_port(self, port):
        """Find the PID of the process listening on the given port."""
        try:
            result = subprocess.run(
                ['netstat', '-ano'],
                capture_output=True, text=True, stdin=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            for line in result.stdout.splitlines():
                if f':{port} ' in line and 'LISTENING' in line:
                    return line.strip().split()[-1]
        except Exception as e:
            logger.error(f"Failed to find PID on port {port}: {e}")
        return None

    def _find_console_ancestor_pid(self, pid):
        """Walk up the process tree (max 5 levels) to find the ancestor cmd.exe.
        Returns (cmd_pid, 'cmd.exe') or (None, None) if not found."""
        current_pid = str(pid)
        for _ in range(5):
            try:
                result = subprocess.run(
                    ['wmic', 'process', 'where', f'processid={current_pid}', 'get', 'parentprocessid'],
                    capture_output=True, text=True, stdin=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                parent_pid = None
                for line in result.stdout.strip().splitlines()[1:]:
                    line = line.strip()
                    if line.isdigit():
                        parent_pid = line
                        break
                if not parent_pid:
                    return None, None

                # Check parent's process name
                name_result = subprocess.run(
                    ['wmic', 'process', 'where', f'processid={parent_pid}', 'get', 'name'],
                    capture_output=True, text=True, stdin=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                parent_name = ""
                for name_line in name_result.stdout.strip().splitlines()[1:]:
                    name_line = name_line.strip()
                    if name_line:
                        parent_name = name_line.lower()
                        break

                if parent_name == 'cmd.exe':
                    return parent_pid, parent_name

                # Not cmd.exe yet — continue up the tree
                current_pid = parent_pid
            except Exception as e:
                logger.error(f"Failed to find ancestor of PID {current_pid}: {e}")
                return None, None
        logger.warning(f"No cmd.exe ancestor found within 5 levels of PID {pid}")
        return None, None

    def _attach_and_send_ctrl_c(self, pid):
        """Attach to a process's console and send Ctrl+C. Returns True if attached (caller must FreeConsole)."""
        kernel32 = ctypes.windll.kernel32

        # Detach from any previous console first (important when shutting down multiple services)
        kernel32.FreeConsole()

        if not kernel32.AttachConsole(int(pid)):
            logger.warning(f"Could not attach to console of PID {pid} (error {ctypes.GetLastError()})")
            return False

        # Prevent Ctrl+C from killing our own process
        kernel32.SetConsoleCtrlHandler(None, True)

        # Send Ctrl+C to all processes on that console
        kernel32.GenerateConsoleCtrlEvent(0, 0)
        logger.info(f"Ctrl+C sent to console of PID {pid}")
        return True

    def _detach_console(self):
        """Detach from the currently attached console."""
        kernel32 = ctypes.windll.kernel32
        kernel32.FreeConsole()
        kernel32.SetConsoleCtrlHandler(None, False)

    def _write_console_keys(self, text):
        """Write keystrokes to the attached console's input buffer via CONIN$."""
        kernel32 = ctypes.windll.kernel32

        # Open CONIN$ directly — works for pythonw which has no std handles
        GENERIC_READ_WRITE = 0x80000000 | 0x40000000
        FILE_SHARE_READ_WRITE = 0x01 | 0x02
        OPEN_EXISTING = 3

        kernel32.CreateFileW.restype = ctypes.wintypes.HANDLE
        kernel32.CreateFileW.argtypes = [
            ctypes.wintypes.LPCWSTR, ctypes.wintypes.DWORD, ctypes.wintypes.DWORD,
            ctypes.c_void_p, ctypes.wintypes.DWORD, ctypes.wintypes.DWORD, ctypes.wintypes.HANDLE,
        ]
        kernel32.WriteConsoleInputW.restype = ctypes.wintypes.BOOL
        kernel32.WriteConsoleInputW.argtypes = [
            ctypes.wintypes.HANDLE, ctypes.c_void_p,
            ctypes.wintypes.DWORD, ctypes.POINTER(ctypes.wintypes.DWORD),
        ]
        kernel32.CloseHandle.argtypes = [ctypes.wintypes.HANDLE]

        conin = kernel32.CreateFileW("CONIN$", GENERIC_READ_WRITE, FILE_SHARE_READ_WRITE, None, OPEN_EXISTING, 0, None)
        INVALID_HANDLE = ctypes.wintypes.HANDLE(-1).value
        if conin == INVALID_HANDLE:
            logger.warning(f"CreateFileW CONIN$ failed (error {ctypes.GetLastError()})")
            return False

        KEY_EVENT = 0x0001

        class KEY_EVENT_RECORD(ctypes.Structure):
            _fields_ = [
                ("bKeyDown", ctypes.wintypes.BOOL),
                ("wRepeatCount", ctypes.wintypes.WORD),
                ("wVirtualKeyCode", ctypes.wintypes.WORD),
                ("wVirtualScanCode", ctypes.wintypes.WORD),
                ("uChar", ctypes.c_wchar),
                ("dwControlKeyState", ctypes.wintypes.DWORD),
            ]

        class INPUT_RECORD(ctypes.Structure):
            _fields_ = [
                ("EventType", ctypes.wintypes.WORD),
                ("_padding", ctypes.wintypes.WORD),
                ("Event", KEY_EVENT_RECORD),
            ]

        written = ctypes.wintypes.DWORD()
        ok = True
        for ch in text:
            vk = 0x0D if ch == '\r' else 0  # VK_RETURN for Enter, 0 for others
            for key_down in (True, False):
                record = INPUT_RECORD()
                record.EventType = KEY_EVENT
                record._padding = 0
                record.Event.bKeyDown = key_down
                record.Event.wRepeatCount = 1
                record.Event.wVirtualKeyCode = vk
                record.Event.wVirtualScanCode = 0
                record.Event.uChar = ch
                record.Event.dwControlKeyState = 0
                success = kernel32.WriteConsoleInputW(conin, ctypes.byref(record), 1, ctypes.byref(written))
                if not success:
                    logger.warning(f"WriteConsoleInputW failed for '{ch}' (error {ctypes.GetLastError()})")
                    ok = False

        kernel32.CloseHandle(conin)
        return ok

    def _wait_for_port_free(self, port, timeout=10):
        """Poll until nothing is listening on the port, or timeout."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                result = sock.connect_ex(('127.0.0.1', port))
                if result != 0:
                    return True  # Port is free
            finally:
                sock.close()
            time.sleep(0.5)
        return False  # Timed out, port still in use

    def _graceful_shutdown_service(self, port, service_name, timeout=10):
        """Gracefully shut down a service by port: Ctrl+C, wait, answer batch prompt, close terminal."""
        pid = self._find_pid_on_port(port)
        if not pid:
            logger.warning(f"No process found on port {port} for {service_name}")
            return

        logger.info(f"Shutting down {service_name} (PID {pid} on port {port})...")

        # Find ancestor cmd.exe before killing — we'll need it to close the terminal
        parent_pid, parent_name = self._find_console_ancestor_pid(pid)

        # Attach to the server's console and send Ctrl+C (stays attached)
        attached = self._attach_and_send_ctrl_c(int(pid))

        if attached:
            # Wait for the port to become free (server shutting down gracefully)
            if self._wait_for_port_free(port, timeout):
                logger.info(f"{service_name} shut down gracefully")
                # Server is down. The "Terminate batch job (Y/N)?" prompt may be showing now.
                # Send "y" + Enter while still attached to the console.
                time.sleep(1)
                self._write_console_keys("y\r")
                time.sleep(0.5)
            else:
                # Force kill as fallback
                logger.warning(f"{service_name} did not stop in {timeout}s, force-killing PID {pid}")
                subprocess.run(
                    ['taskkill', '/pid', pid, '/t', '/f'],
                    capture_output=True, stdin=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )

            # Detach from the console
            self._detach_console()
        else:
            # Couldn't attach to console — force kill
            logger.warning(f"Could not send Ctrl+C to {service_name}, force-killing PID {pid}")
            subprocess.run(
                ['taskkill', '/pid', pid, '/t', '/f'],
                capture_output=True, stdin=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )

        # Close the ancestor cmd.exe terminal window if found
        if parent_pid and parent_name == 'cmd.exe':
            logger.info(f"Closing terminal window (cmd.exe PID {parent_pid})")
            subprocess.run(
                ['taskkill', '/pid', parent_pid, '/t', '/f'],
                capture_output=True, stdin=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )

        logger.info(f"{service_name} shutdown complete")

    def _shutdown_alltalk(self):
        """Gracefully shut down AllTalk TTS and its console window."""
        if not messagebox.askyesno("Confirm", "Close AllTalk TTS server and its console?"):
            return
        threading.Thread(target=self._graceful_shutdown_service, args=(7851, "AllTalk TTS"), daemon=True).start()

    def _shutdown_whisper(self):
        """Gracefully shut down Whisper STT and its console window."""
        if not messagebox.askyesno("Confirm", "Close Whisper STT server and its console?"):
            return
        threading.Thread(target=self._graceful_shutdown_service, args=(8787, "Whisper STT"), daemon=True).start()

    def _shutdown_all_services(self):
        """Gracefully shut down AllTalk, Whisper, and their consoles, then quit mic panel."""
        if not messagebox.askyesno("Confirm", "Close ALL voice services (AllTalk + Whisper + Mic Panel)?"):
            return

        def shutdown_all():
            self._graceful_shutdown_service(7851, "AllTalk TTS")
            self._graceful_shutdown_service(8787, "Whisper STT")
            logger.info("All services shut down — closing mic panel")
            self.root.after(0, self._quit)

        threading.Thread(target=shutdown_all, daemon=True).start()

    def _quit(self):
        """Clean shutdown."""
        logger.info("Mic panel shutting down")
        self._level_monitor_stop.set()
        if self.tray_icon:
            self.tray_icon.stop()
        self.root.destroy()

    def run(self):
        """Start the tkinter main loop."""
        logger.info("Mic Control Panel starting")
        self.root.mainloop()


if __name__ == "__main__":
    panel = MicControlPanel()
    panel.run()
