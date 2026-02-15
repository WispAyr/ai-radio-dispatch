"""Dispatch orchestrator — wires the full pipeline together."""

import os
import time
import wave
import threading
import numpy as np
from enum import Enum
from audio_capture import AudioCapture
from stt import SpeechToText
from speaker import SpeakerTracker
from config import MODE, SAMPLE_RATE

RECORDINGS_DIR = os.path.join(os.path.dirname(__file__), "recordings")
os.makedirs(RECORDINGS_DIR, exist_ok=True)


class DispatchState(Enum):
    IDLE = "idle"
    LISTENING = "listening"
    TRANSCRIBING = "transcribing"
    PROCESSING = "processing"
    SPEAKING = "speaking"


class DispatchEntry:
    """A single entry in the dispatch log."""
    def __init__(self, speaker: str, text: str, timestamp: float = None,
                 audio_file: str = None, waveform: list = None, duration: float = 0):
        self.speaker = speaker
        self.text = text
        self.timestamp = timestamp or time.time()
        self.audio_file = audio_file  # filename in recordings/
        self.waveform = waveform or []  # list of peak values for visualization
        self.duration = duration  # seconds

    def to_dict(self):
        d = {
            "speaker": self.speaker,
            "text": self.text,
            "timestamp": self.timestamp,
            "time_str": time.strftime("%H:%M:%S", time.localtime(self.timestamp)),
            "duration": round(self.duration, 1),
        }
        if self.audio_file:
            d["audio_file"] = self.audio_file
        if self.waveform:
            d["waveform"] = self.waveform
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
    """Main dispatch system orchestrator."""

    def __init__(self):
        self.stt = SpeechToText()
        self.speaker_tracker = SpeakerTracker()
        self.audio = AudioCapture(
            on_utterance=self._on_utterance,
            on_level=self._on_level
        )

        self.llm = None
        self.tts = None

        self.state = DispatchState.IDLE
        self.log = []
        self.current_level = 0.0
        self._muted = False
        self._entry_counter = 0

        self._on_state_change = None
        self._on_log_entry = None
        self._on_level_update = None

    def set_callbacks(self, on_state_change=None, on_log_entry=None, on_level_update=None):
        self._on_state_change = on_state_change
        self._on_log_entry = on_log_entry
        self._on_level_update = on_level_update

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

    def start(self):
        if self.state != DispatchState.IDLE:
            return
        self._set_state(DispatchState.LISTENING)
        self.audio.start()
        print("[Dispatch] Listening...")

    def stop(self):
        self.audio.stop()
        self._set_state(DispatchState.IDLE)
        print("[Dispatch] Stopped.")

    def toggle_mute(self):
        self._muted = not self._muted
        return self._muted

    def clear_log(self):
        self.log = []
        self.speaker_tracker.reset()
        if self.llm:
            self.llm.clear_history()

    def get_input_devices(self):
        return self.audio.list_devices()

    def set_input_device(self, device_id):
        was_running = self.audio.is_running
        if was_running:
            self.audio.stop()
        self.audio.set_device(device_id)
        if was_running:
            self.audio.start()
            self._set_state(DispatchState.LISTENING)

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
            was_running = self.audio.is_running
            if was_running:
                self.audio.stop()
                self._set_state(DispatchState.IDLE)

            config.WHISPER_MODEL = new_model
            print(f"[Dispatch] Switching model to {new_model}...")
            self.stt.load()
            result["reloaded"] = True
            print(f"[Dispatch] Model switched to {new_model}")

            if was_running:
                self.audio.start()
                self._set_state(DispatchState.LISTENING)

        # Beam size
        if "beam_size" in settings:
            self.stt._beam_size = int(settings["beam_size"])

        # VAD thresholds
        if "vad_threshold" in settings:
            config.VAD_ENERGY_THRESHOLD = float(settings["vad_threshold"])
            self.audio.energy_threshold = config.VAD_ENERGY_THRESHOLD

        if "vad_silence" in settings:
            config.VAD_SILENCE_DURATION = float(settings["vad_silence"])
            self.audio.silence_duration = config.VAD_SILENCE_DURATION

        return result

    def _set_state(self, new_state: DispatchState):
        self.state = new_state
        if self._on_state_change:
            self._fire_callback(self._on_state_change, new_state.value)

    def _on_level(self, level: float):
        self.current_level = level
        if self._on_level_update:
            self._fire_callback(self._on_level_update, level)

    def _on_utterance(self, audio_data):
        try:
            self._set_state(DispatchState.TRANSCRIBING)
            text = self.stt.transcribe(audio_data)

            if not text or len(text.strip()) < 2:
                self._set_state(DispatchState.LISTENING)
                return

            # Identify speaker
            speaker = self.speaker_tracker.identify(audio_data)

            # Save recording
            self._entry_counter += 1
            ts = time.strftime("%H%M%S", time.localtime())
            filename = f"entry_{self._entry_counter:04d}_{ts}.wav"
            filepath = os.path.join(RECORDINGS_DIR, filename)
            _save_wav(audio_data, filepath)

            # Generate waveform
            waveform = _generate_waveform_peaks(audio_data)
            duration = len(audio_data) / SAMPLE_RATE

            print(f"[{speaker}] {text} ({duration:.1f}s → {filename})")

            entry = DispatchEntry(speaker, text, audio_file=filename,
                                  waveform=waveform, duration=duration)
            self.log.append(entry)
            if self._on_log_entry:
                self._fire_callback(self._on_log_entry, entry.to_dict())

            # In dispatch mode, generate a response
            if MODE == "dispatch" and self.llm and self.tts:
                self._set_state(DispatchState.PROCESSING)
                response = self.llm.generate(text)
                print(f"[Dispatch] {response}")

                dispatch_entry = DispatchEntry("dispatch", response)
                self.log.append(dispatch_entry)
                if self._on_log_entry:
                    self._fire_callback(self._on_log_entry, dispatch_entry.to_dict())

                if not self._muted and response:
                    self._set_state(DispatchState.SPEAKING)
                    self.tts.speak(response)

            self._set_state(DispatchState.LISTENING)

        except Exception as e:
            print(f"[Dispatch] Error: {e}")
            import traceback
            traceback.print_exc()
            self._set_state(DispatchState.LISTENING)

    def _fire_callback(self, callback, *args):
        if callable(callback):
            try:
                callback(*args)
            except Exception:
                pass
