"""
Claude Code Voice Mode Microphone Control Panel
Always-on-top floating window with:
- Push to Talk (hold button)
- Toggle to Talk (click to start/stop)
- Always On with Mute button
- Mic volume slider
- Minimize to system tray
"""
import json
import logging
import os
import queue
import socket
import struct
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk
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
        self.root.geometry("280x650")
        self.root.resizable(False, False)
        self.root.attributes("-topmost", True)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        # State
        self.mode = tk.StringVar(value="push_to_talk")
        self.is_recording = False
        self.is_muted = False
        self.volume = tk.DoubleVar(value=1.0)
        self.audio_queue = queue.Queue()
        self.audio_stream = None
        self.level_value = 0.0
        self.tray_icon = None
        self.hidden = False
        self.tts_paused = False

        self._build_ui()
        self._setup_console_logging()
        self._update_state()
        self._start_level_monitor()

    def _build_ui(self):
        """Build the tkinter UI."""
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
            self.status_frame, text="Ready", font=("Segoe UI", 10),
            bg="#1e1e1e", fg="#00cc00", pady=4
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

        modes = [
            ("Push to Talk", "push_to_talk"),
            ("Toggle to Talk", "toggle"),
            ("Always On", "always_on"),
        ]
        for text, value in modes:
            rb = tk.Radiobutton(
                mode_frame, text=text, variable=self.mode, value=value,
                font=("Segoe UI", 9), command=self._on_mode_change
            )
            rb.pack(anchor=tk.W)

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
            btn_frame, text="Mute", font=("Segoe UI", 9),
            bg="#666", fg="white", command=self._toggle_mute
        )
        self.mute_btn.pack(fill=tk.X, pady=(5, 0))

        # Volume slider
        vol_frame = tk.LabelFrame(self.root, text="Mic Volume", font=("Segoe UI", 9), padx=10, pady=5)
        vol_frame.pack(fill=tk.X, padx=10, pady=5)

        self.vol_slider = tk.Scale(
            vol_frame, from_=0.0, to=2.0, resolution=0.1,
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

        # Bottom buttons
        bottom_frame = tk.Frame(self.root)
        bottom_frame.pack(fill=tk.X, padx=10, pady=(5, 10))

        if HAS_TRAY:
            tk.Button(
                bottom_frame, text="Minimize to Tray", font=("Segoe UI", 8),
                command=self._minimize_to_tray
            ).pack(side=tk.LEFT)

        tk.Button(
            bottom_frame, text="Quit", font=("Segoe UI", 8),
            command=self._quit
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
        elif mode == "always_on":
            self.action_btn.config(text="Listening...")
            self._start_recording()

        self._update_state()

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
        """Start capturing microphone audio."""
        if self.is_recording:
            return
        self.is_recording = True
        self.action_btn.config(bg="#f44336")
        self.status_label.config(text="Recording...", fg="#ff4444")
        self._update_state()
        logger.info("Recording started")

    def _stop_recording(self):
        """Stop capturing microphone audio."""
        if not self.is_recording:
            return
        self.is_recording = False

        mode = self.mode.get()
        if mode == "always_on":
            self.action_btn.config(bg="#4CAF50", text="Listening...")
        elif mode == "toggle":
            self.action_btn.config(bg="#4CAF50", text="Click to Talk")
        else:
            self.action_btn.config(bg="#4CAF50", text="Hold to Talk")

        self.status_label.config(text="Ready", fg="#00cc00")
        self._update_state()
        logger.info("Recording stopped")

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
        logger.info(f"Mute: {self.is_muted}")

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
        state = {
            "mode": self.mode.get(),
            "recording": self.is_recording,
            "muted": self.is_muted,
            "volume": self.volume.get(),
            "tts_paused": self.tts_paused,
        }
        save_mic_state(state)

    def _start_level_monitor(self):
        """Start a background thread to monitor mic level."""
        def monitor():
            def callback(indata, frames, time_info, status):
                if indata is not None and len(indata) > 0:
                    volume = self.volume.get()
                    level = np.sqrt(np.mean(indata.astype(np.float32) ** 2)) * volume
                    self.level_value = min(100, level * 500)

            try:
                with sd.InputStream(
                    samplerate=SAMPLE_RATE, channels=CHANNELS,
                    dtype="int16", blocksize=1024, callback=callback
                ):
                    while True:
                        sd.sleep(50)
            except Exception as e:
                logger.error(f"Level monitor error: {e}")

        thread = threading.Thread(target=monitor, daemon=True)
        thread.start()

        def update_meter():
            self.level_bar["value"] = self.level_value
            self.root.after(50, update_meter)

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

    def _quit(self):
        """Clean shutdown."""
        logger.info("Mic panel shutting down")
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
