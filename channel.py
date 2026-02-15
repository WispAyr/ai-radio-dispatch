"""Channel — a named audio input pipeline with its own capture, VAD, and transcript."""

import os
import json
import time
import uuid
import threading
import numpy as np
from audio_capture import AudioCapture
from speaker import SpeakerTracker
from config import SAMPLE_RATE

CHANNELS_FILE = os.path.join(os.path.dirname(__file__), "channels.json")
RECORDINGS_DIR = os.path.join(os.path.dirname(__file__), "recordings")
os.makedirs(RECORDINGS_DIR, exist_ok=True)

# Default channel colours
CHANNEL_COLORS = [
    "#3b82f6", "#ef4444", "#f59e0b", "#10b981", "#8b5cf6",
    "#ec4899", "#06b6d4", "#f97316", "#14b8a6", "#a855f7",
]


class Channel:
    """A single named audio capture channel with its own pipeline."""

    def __init__(self, channel_id: str, name: str, device_id=None, color: str = "#3b82f6"):
        self.id = channel_id
        self.name = name
        self.device_id = device_id
        self.color = color
        self.is_active = False
        self.is_muted = False
        self.state = "idle"  # idle, listening, transcribing
        self.log = []        # list of DispatchEntry
        self.level = 0.0
        self.speaker_tracker = SpeakerTracker()
        self._entry_counter = 0

        # Callbacks set by DispatchSystem
        self._on_utterance_cb = None   # Called with (channel_id, audio_data)
        self._on_level_cb = None       # Called with (channel_id, level)
        self._on_state_change_cb = None  # Called with (channel_id, state)

        self.audio = AudioCapture(
            on_utterance=self._on_utterance,
            on_level=self._on_level
        )
        if device_id is not None:
            self.audio.set_device(device_id)

    def set_callbacks(self, on_utterance=None, on_level=None, on_state_change=None):
        self._on_utterance_cb = on_utterance
        self._on_level_cb = on_level
        self._on_state_change_cb = on_state_change

    def start(self):
        if self.is_active:
            return
        if self.device_id is None:
            print(f"[Channel] '{self.name}' has no audio device assigned — skipping start")
            return
        self.is_active = True
        self._set_state("listening")
        self.audio.start()

    def stop(self):
        if not self.is_active:
            return
        self.audio.stop()
        self.is_active = False
        self._set_state("idle")

    def toggle_mute(self):
        self.is_muted = not self.is_muted
        return self.is_muted

    def set_device(self, device_id):
        was_running = self.audio.is_running
        if was_running:
            self.audio.stop()
        self.device_id = device_id
        self.audio.set_device(device_id)
        if was_running:
            self.audio.start()

    def clear_log(self):
        self.log = []
        self.speaker_tracker.reset()

    def _set_state(self, new_state: str):
        self.state = new_state
        if self._on_state_change_cb:
            try:
                self._on_state_change_cb(self.id, new_state)
            except Exception:
                pass

    def _on_utterance(self, audio_data):
        """Called by AudioCapture when speech ends — forwards to DispatchSystem."""
        if self._on_utterance_cb:
            self._on_utterance_cb(self.id, audio_data)

    def _on_level(self, level: float):
        self.level = level
        if self._on_level_cb:
            try:
                self._on_level_cb(self.id, level)
            except Exception:
                pass

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "device_id": self.device_id,
            "color": self.color,
            "state": self.state,
            "is_active": self.is_active,
            "is_muted": self.is_muted,
        }

    def to_config_dict(self):
        """Minimal dict for saving to channels.json."""
        return {
            "id": self.id,
            "name": self.name,
            "device_id": self.device_id,
            "color": self.color,
        }


# ===== Persistence =====

def load_channel_configs() -> list:
    """Load channel definitions from channels.json. Returns list of dicts."""
    if os.path.exists(CHANNELS_FILE):
        try:
            with open(CHANNELS_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            print(f"[Channels] Error loading {CHANNELS_FILE}: {e}")
    # Default: one channel
    return [{"id": "ch-1", "name": "Channel 1", "device_id": None, "color": CHANNEL_COLORS[0]}]


def save_channel_configs(channels: list):
    """Save channel definitions to channels.json."""
    try:
        with open(CHANNELS_FILE, "w") as f:
            json.dump(channels, f, indent=2)
    except Exception as e:
        print(f"[Channels] Error saving {CHANNELS_FILE}: {e}")


def generate_channel_id() -> str:
    return f"ch-{uuid.uuid4().hex[:8]}"
