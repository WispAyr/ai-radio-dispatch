"""Dispatch orchestrator — wires the full pipeline together (multi-channel)."""

import os
import time
import wave
import queue
import threading
import numpy as np
from enum import Enum
from stt import SpeechToText
from db import DispatchDB
from callsign import CallsignRegistry
from alerts import AlertEngine
from channel import (
    Channel, load_channel_configs, save_channel_configs,
    generate_channel_id, CHANNEL_COLORS, RECORDINGS_DIR
)
from config import MODE, SAMPLE_RATE


class DispatchState(Enum):
    IDLE = "idle"
    LISTENING = "listening"
    TRANSCRIBING = "transcribing"
    PROCESSING = "processing"
    SPEAKING = "speaking"


class DispatchEntry:
    """A single entry in the dispatch log."""
    def __init__(self, speaker: str, text: str, timestamp: float = None,
                 audio_file: str = None, waveform: list = None, duration: float = 0,
                 channel_id: str = None, db_id: int = None, callsigns: list = None):
        self.speaker = speaker
        self.text = text
        self.timestamp = timestamp or time.time()
        self.audio_file = audio_file  # filename in recordings/
        self.waveform = waveform or []  # list of peak values for visualization
        self.duration = duration  # seconds
        self.channel_id = channel_id
        self.db_id = db_id  # SQLite row ID
        self.callsigns = callsigns or []  # detected callsigns

    def to_dict(self):
        d = {
            "speaker": self.speaker,
            "text": self.text,
            "timestamp": self.timestamp,
            "time_str": time.strftime("%H:%M:%S", time.localtime(self.timestamp)),
            "duration": round(self.duration, 1),
        }
        if self.db_id:
            d["id"] = self.db_id
        if self.channel_id:
            d["channel_id"] = self.channel_id
        if self.audio_file:
            d["audio_file"] = self.audio_file
        if self.waveform:
            d["waveform"] = self.waveform
        if self.callsigns:
            d["callsigns"] = self.callsigns
        return d


def _save_wav(audio_data: np.ndarray, filepath: str):
    """Save float32 audio data as a 16-bit WAV file."""
    audio_int16 = (audio_data * 32767).astype(np.int16)
    with wave.open(filepath, 'w') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(audio_int16.tobytes())


def _generate_waveform_peaks(audio_data: np.ndarray, num_bars: int = 80) -> list:
    """Generate waveform peak data for visualization."""
    if len(audio_data) < num_bars:
        return [0.0] * num_bars

    chunk_size = len(audio_data) // num_bars
    peaks = []
    for i in range(num_bars):
        start = i * chunk_size
        end = start + chunk_size
        chunk = audio_data[start:end]
        peak = float(np.max(np.abs(chunk)))
        peaks.append(round(peak, 4))

    # Normalize to 0-1 range
    max_peak = max(peaks) if peaks else 1.0
    if max_peak > 0:
        peaks = [round(p / max_peak, 3) for p in peaks]
    return peaks


class DispatchSystem:
    """Main dispatch system orchestrator (multi-channel)."""

    def __init__(self):
        self.stt = SpeechToText()
        self.db = DispatchDB()
        self.callsign_registry = CallsignRegistry(db=self.db)
        self.alert_engine = AlertEngine(db=self.db)
        self.channels = {}  # channel_id -> Channel

        self.llm = None
        self.tts = None

        self.state = DispatchState.IDLE
        self._muted = False
        self._entry_counter = 0
        self._current_session_id = None

        # Transcription queue: (channel_id, audio_data)
        self._transcription_queue = queue.Queue()
        self._transcription_thread = None
        self._running = False

        # Silence check timer
        self._silence_check_thread = None

        self._on_state_change = None
        self._on_log_entry = None
        self._on_level_update = None
        self._on_channel_state_change = None
        self._on_callsign_detected = None
        self._on_alert = None

        # Wire alert callback
        self.alert_engine.set_callback(on_alert=self._handle_alert)

        # Load channels from config
        self._load_channels()

    def _load_channels(self):
        """Load channels from channels.json."""
        configs = load_channel_configs()
        for cfg in configs:
            ch = Channel(
                channel_id=cfg["id"],
                name=cfg["name"],
                device_id=cfg.get("device_id"),
                color=cfg.get("color", CHANNEL_COLORS[0])
            )
            ch.set_callbacks(
                on_utterance=self._on_channel_utterance,
                on_level=self._on_channel_level,
                on_state_change=self._on_channel_state,
            )
            self.channels[ch.id] = ch
        print(f"[Dispatch] Loaded {len(self.channels)} channel(s)")

    def _save_channels(self):
        """Save current channels to channels.json."""
        configs = [ch.to_config_dict() for ch in self.channels.values()]
        save_channel_configs(configs)

    def set_callbacks(self, on_state_change=None, on_log_entry=None,
                      on_level_update=None, on_channel_state_change=None,
                      on_callsign_detected=None, on_alert=None):
        self._on_state_change = on_state_change
        self._on_log_entry = on_log_entry
        self._on_level_update = on_level_update
        self._on_channel_state_change = on_channel_state_change
        self._on_callsign_detected = on_callsign_detected
        self._on_alert = on_alert

    def load_models(self):
        print(f"[Dispatch] Loading models (mode={MODE})...")
        self.stt.load()
        if MODE == "dispatch":
            from llm import DispatchLLM
            from tts import TextToSpeech
            self.llm = DispatchLLM()
            self.tts = TextToSpeech()
            self.tts.load()
        print("[Dispatch] All models loaded. Ready.")

    # ===== Session management =====

    def start_session(self, name: str = None) -> int:
        """Start a named session. All transcripts will be tagged with this session."""
        if name is None:
            name = time.strftime("Session %Y-%m-%d %H:%M")
        self._current_session_id = self.db.start_session(name)
        print(f"[Dispatch] Session started: {name} (id={self._current_session_id})")
        return self._current_session_id

    def end_session(self):
        """End the current session."""
        if self._current_session_id:
            self.db.end_session(self._current_session_id)
            print(f"[Dispatch] Session ended (id={self._current_session_id})")
            self._current_session_id = None

    def get_sessions(self) -> list:
        return self.db.get_sessions()

    # ===== Channel management =====

    def add_channel(self, name: str, device_id=None, color: str = None) -> dict:
        """Add a new channel. Returns the channel dict."""
        if color is None:
            idx = len(self.channels) % len(CHANNEL_COLORS)
            color = CHANNEL_COLORS[idx]
        ch_id = generate_channel_id()
        ch = Channel(ch_id, name, device_id, color)
        ch.set_callbacks(
            on_utterance=self._on_channel_utterance,
            on_level=self._on_channel_level,
            on_state_change=self._on_channel_state,
        )
        self.channels[ch_id] = ch
        self._save_channels()
        return ch.to_dict()

    def remove_channel(self, channel_id: str) -> bool:
        """Remove a channel. Returns True if found and removed."""
        ch = self.channels.get(channel_id)
        if not ch:
            return False
        ch.stop()
        del self.channels[channel_id]
        self._save_channels()
        return True

    def update_channel(self, channel_id: str, name: str = None,
                       device_id=None, color: str = None) -> dict:
        """Update channel properties."""
        ch = self.channels.get(channel_id)
        if not ch:
            return None
        if name is not None:
            ch.name = name
        if color is not None:
            ch.color = color
        if device_id is not None:
            ch.set_device(int(device_id))
        self._save_channels()
        return ch.to_dict()

    def get_channels(self) -> list:
        return [ch.to_dict() for ch in self.channels.values()]

    def get_channel_logs(self, channel_id: str) -> list:
        ch = self.channels.get(channel_id)
        if ch:
            return [e.to_dict() for e in ch.log]
        return []

    def get_all_logs(self) -> list:
        """Return all log entries across all channels, sorted by timestamp."""
        all_entries = []
        for ch in self.channels.values():
            all_entries.extend(ch.log)
        all_entries.sort(key=lambda e: e.timestamp)
        return [e.to_dict() for e in all_entries]

    # ===== Search =====

    def search_transcripts(self, query: str, channel_id: str = None,
                           speaker: str = None, from_ts: float = None,
                           to_ts: float = None, flagged_only: bool = False,
                           limit: int = 100, offset: int = 0) -> list:
        return self.db.search_transcripts(query, channel_id, speaker,
                                          from_ts, to_ts, flagged_only, limit, offset)

    # ===== Bookmarks =====

    def add_bookmark(self, transcript_id: int, label: str = "") -> int:
        return self.db.add_bookmark(transcript_id, label)

    def remove_bookmark(self, bookmark_id: int):
        self.db.remove_bookmark(bookmark_id)

    def get_bookmarks(self) -> list:
        return self.db.get_bookmarks()

    def flag_transcript(self, transcript_id: int, flagged: bool = True, notes: str = None):
        self.db.flag_transcript(transcript_id, flagged, notes)

    # ===== Export =====

    def export_transcripts(self, format: str = "json", channel_id: str = None,
                           from_ts: float = None, to_ts: float = None) -> str:
        return self.db.export_transcripts(format, channel_id, from_ts, to_ts)

    # ===== Stats =====

    def get_stats(self) -> dict:
        stats = self.db.get_stats()
        stats["active_channels"] = sum(1 for ch in self.channels.values() if ch.is_active)
        stats["total_channels"] = len(self.channels)
        stats["active_callsigns"] = len(self.callsign_registry.get_active(30))
        return stats

    # ===== Start / Stop =====

    def start(self, channel_id: str = None):
        """Start one or all channels."""
        # Start the transcription worker if not running
        if not self._running:
            self._running = True
            self._transcription_thread = threading.Thread(
                target=self._transcription_worker, daemon=True
            )
            self._transcription_thread.start()

            # Start silence check thread
            self._silence_check_thread = threading.Thread(
                target=self._silence_checker, daemon=True
            )
            self._silence_check_thread.start()

        # Auto-start a session if none active
        if not self._current_session_id:
            self.start_session()

        if channel_id:
            ch = self.channels.get(channel_id)
            if ch:
                ch.start()
                print(f"[Dispatch] Channel '{ch.name}' listening...")
        else:
            for ch in self.channels.values():
                ch.start()
            print("[Dispatch] All channels listening...")

        self._set_state(DispatchState.LISTENING)

    def stop(self, channel_id: str = None):
        """Stop one or all channels."""
        if channel_id:
            ch = self.channels.get(channel_id)
            if ch:
                ch.stop()
                print(f"[Dispatch] Channel '{ch.name}' stopped.")
        else:
            for ch in self.channels.values():
                ch.stop()
            self._running = False
            self.end_session()
            print("[Dispatch] All channels stopped.")

        # Update global state
        any_active = any(ch.is_active for ch in self.channels.values())
        if not any_active:
            self._set_state(DispatchState.IDLE)

    def toggle_mute(self, channel_id: str = None):
        """Toggle mute for one channel or globally."""
        if channel_id:
            ch = self.channels.get(channel_id)
            if ch:
                return ch.toggle_mute()
            return False
        else:
            self._muted = not self._muted
            return self._muted

    def clear_log(self, channel_id: str = None):
        """Clear log for one channel or all."""
        if channel_id:
            ch = self.channels.get(channel_id)
            if ch:
                ch.clear_log()
        else:
            for ch in self.channels.values():
                ch.clear_log()
            if self.llm:
                self.llm.clear_history()

    def get_input_devices(self):
        # All channels share the same device list
        if self.channels:
            return next(iter(self.channels.values())).audio.list_devices()
        from audio_capture import AudioCapture
        temp = AudioCapture()
        return temp.list_devices()

    def set_input_device(self, device_id, channel_id: str = None):
        """Set input device for a specific channel."""
        if channel_id:
            ch = self.channels.get(channel_id)
            if ch:
                ch.set_device(int(device_id))
                self._save_channels()

    def get_settings(self):
        """Return current transcription settings."""
        import config
        return {
            "model": config.WHISPER_MODEL,
            "models_available": ["tiny.en", "base.en", "small.en", "medium.en", "large-v3"],
            "beam_size": getattr(self.stt, '_beam_size', 5),
            "vad_threshold": config.VAD_ENERGY_THRESHOLD,
            "vad_silence": config.VAD_SILENCE_DURATION,
            "mode": config.MODE,
        }

    def apply_settings(self, settings: dict):
        """Apply new settings. Returns status dict."""
        import config
        result = {"status": "ok", "reloaded": False}

        # Model change — requires reload
        new_model = settings.get("model")
        if new_model and new_model != config.WHISPER_MODEL:
            # Stop all active channels
            active_channels = [ch for ch in self.channels.values() if ch.is_active]
            for ch in active_channels:
                ch.stop()

            config.WHISPER_MODEL = new_model
            print(f"[Dispatch] Switching model to {new_model}...")
            self.stt.load()
            result["reloaded"] = True
            print(f"[Dispatch] Model switched to {new_model}")

            # Restart channels that were active
            for ch in active_channels:
                ch.start()

        # Beam size
        if "beam_size" in settings:
            self.stt._beam_size = int(settings["beam_size"])

        # VAD thresholds — apply to all channels
        if "vad_threshold" in settings:
            config.VAD_ENERGY_THRESHOLD = float(settings["vad_threshold"])
            for ch in self.channels.values():
                ch.audio.energy_threshold = config.VAD_ENERGY_THRESHOLD

        if "vad_silence" in settings:
            config.VAD_SILENCE_DURATION = float(settings["vad_silence"])
            for ch in self.channels.values():
                ch.audio.silence_duration = config.VAD_SILENCE_DURATION

        return result

    # ===== Internal callbacks =====

    def _set_state(self, new_state: DispatchState):
        self.state = new_state
        if self._on_state_change:
            self._fire_callback(self._on_state_change, new_state.value)

    def _on_channel_state(self, channel_id: str, state: str):
        """Called when a channel's state changes."""
        if self._on_channel_state_change:
            self._fire_callback(self._on_channel_state_change, channel_id, state)

    def _on_channel_level(self, channel_id: str, level: float):
        """Called by a channel with its audio level."""
        if self._on_level_update:
            self._fire_callback(self._on_level_update, channel_id, level)
        # Check volume alerts
        self.alert_engine.check_volume(channel_id, level)

    def _on_channel_utterance(self, channel_id: str, audio_data):
        """Called by a channel when speech is detected — queues for transcription."""
        self.alert_engine.record_activity(channel_id)
        self._transcription_queue.put((channel_id, audio_data))

    def _handle_alert(self, alert_data: dict):
        """Called by AlertEngine when an alert triggers."""
        if self._on_alert:
            self._fire_callback(self._on_alert, alert_data)

    def _silence_checker(self):
        """Periodically check for silence on active channels."""
        while self._running:
            time.sleep(5)
            self.alert_engine.check_silence()

    def _transcription_worker(self):
        """Background thread that processes queued utterances one at a time."""
        while self._running:
            try:
                channel_id, audio_data = self._transcription_queue.get(timeout=0.5)
            except queue.Empty:
                continue

            ch = self.channels.get(channel_id)
            if not ch:
                continue

            try:
                ch._set_state("transcribing")
                text = self.stt.transcribe(audio_data)

                if not text or len(text.strip()) < 2:
                    if ch.is_active:
                        ch._set_state("listening")
                    continue

                # Identify speaker
                speaker = ch.speaker_tracker.identify(audio_data)

                # Save recording
                self._entry_counter += 1
                ts = time.strftime("%H%M%S", time.localtime())
                filename = f"{channel_id}_{self._entry_counter:04d}_{ts}.wav"
                filepath = os.path.join(RECORDINGS_DIR, filename)
                _save_wav(audio_data, filepath)

                # Generate waveform
                waveform = _generate_waveform_peaks(audio_data)
                duration = len(audio_data) / SAMPLE_RATE

                # Extract callsigns
                detected_callsigns = self.callsign_registry.process_transcript(
                    text, channel_id=channel_id
                )
                callsign_list = [cs["callsign"] for cs in detected_callsigns]

                # Persist to database
                db_id = self.db.insert_transcript(
                    channel_id=channel_id, speaker=speaker, text=text,
                    timestamp=time.time(), audio_file=filename,
                    duration=duration, waveform=waveform,
                    session_id=self._current_session_id,
                    callsigns=callsign_list if callsign_list else None
                )

                print(f"[{ch.name}/{speaker}] {text} ({duration:.1f}s → {filename})"
                      + (f" [callsigns: {', '.join(callsign_list)}]" if callsign_list else ""))

                entry = DispatchEntry(speaker, text, audio_file=filename,
                                      waveform=waveform, duration=duration,
                                      channel_id=channel_id, db_id=db_id,
                                      callsigns=callsign_list)
                ch.log.append(entry)
                if self._on_log_entry:
                    self._fire_callback(self._on_log_entry, entry.to_dict())

                # Notify about detected callsigns
                if detected_callsigns and self._on_callsign_detected:
                    self._fire_callback(self._on_callsign_detected, detected_callsigns, channel_id)

                # Check keyword alerts
                self.alert_engine.check_text(text, channel_id)

                # In dispatch mode, generate a response
                if MODE == "dispatch" and self.llm and self.tts:
                    response = self.llm.generate(text)
                    print(f"[Dispatch] {response}")

                    dispatch_db_id = self.db.insert_transcript(
                        channel_id=channel_id, speaker="dispatch", text=response,
                        timestamp=time.time(), session_id=self._current_session_id
                    )

                    dispatch_entry = DispatchEntry("dispatch", response,
                                                   channel_id=channel_id, db_id=dispatch_db_id)
                    ch.log.append(dispatch_entry)
                    if self._on_log_entry:
                        self._fire_callback(self._on_log_entry, dispatch_entry.to_dict())

                    if not self._muted and not ch.is_muted and response:
                        self.tts.speak(response)

                if ch.is_active:
                    ch._set_state("listening")

            except Exception as e:
                print(f"[Dispatch] Error on channel {channel_id}: {e}")
                import traceback
                traceback.print_exc()
                if ch and ch.is_active:
                    ch._set_state("listening")

    def _fire_callback(self, callback, *args):
        if callable(callback):
            try:
                callback(*args)
            except Exception:
                pass
