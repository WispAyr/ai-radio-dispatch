"""Audio capture with Voice Activity Detection (VAD)."""

import numpy as np
import sounddevice as sd
import threading
import time
from config import (
    SAMPLE_RATE, CHANNELS, BLOCK_SIZE, INPUT_DEVICE,
    VAD_ENERGY_THRESHOLD, VAD_SILENCE_DURATION, VAD_MIN_SPEECH_DURATION
)


class AudioCapture:
    """Captures audio from the microphone and detects speech segments."""

    def __init__(self, on_utterance=None, on_level=None):
        """
        Args:
            on_utterance: Callback(np.ndarray) called with complete speech audio.
            on_level: Callback(float) called with current RMS level for metering.
        """
        self.on_utterance = on_utterance
        self.on_level = on_level
        self._running = False
        self._stream = None
        self._audio_buffer = []
        self._silence_start = None
        self._is_speaking = False
        self._speech_start = None
        self._lock = threading.Lock()
        self._device = INPUT_DEVICE
        # Expose as instance attrs for runtime changes
        self.energy_threshold = VAD_ENERGY_THRESHOLD
        self.silence_duration = VAD_SILENCE_DURATION

    def start(self):
        """Start capturing audio from the configured input device."""
        self._running = True
        self._audio_buffer = []
        self._silence_start = None
        self._is_speaking = False
        self._speech_start = None

        self._stream = sd.InputStream(
            device=self._device,
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            blocksize=BLOCK_SIZE,
            dtype='float32',
            callback=self._audio_callback
        )
        self._stream.start()

    def stop(self):
        """Stop capturing audio."""
        self._running = False
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    @property
    def is_running(self):
        return self._running

    @property
    def current_device(self):
        return self._device

    def set_device(self, device_id):
        """Set the input device (by index)."""
        self._device = device_id

    def list_devices(self):
        """List available audio input devices."""
        devices = sd.query_devices()
        result = []
        for i, d in enumerate(devices):
            if d['max_input_channels'] > 0:
                result.append({
                    "id": i,
                    "name": d['name'],
                    "channels": d['max_input_channels'],
                    "sample_rate": d['default_samplerate'],
                    "active": i == self._device
                })
        return result

    def _audio_callback(self, indata, frames, time_info, status):
        """Called by sounddevice for each audio block."""
        if not self._running:
            return

        audio = indata[:, 0].copy()  # Mono
        rms = np.sqrt(np.mean(audio ** 2))

        # Report level for metering
        if self.on_level:
            self.on_level(float(rms))

        now = time.time()
        is_speech = rms > self.energy_threshold

        with self._lock:
            if is_speech:
                if not self._is_speaking:
                    # Speech just started
                    self._is_speaking = True
                    self._speech_start = now
                    self._audio_buffer = []

                self._audio_buffer.append(audio)
                self._silence_start = None

            elif self._is_speaking:
                # We were speaking, now silence
                self._audio_buffer.append(audio)  # Include trailing silence

                if self._silence_start is None:
                    self._silence_start = now
                elif now - self._silence_start >= self.silence_duration:
                    # Silence long enough — utterance complete
                    speech_duration = now - self._speech_start
                    if speech_duration >= VAD_MIN_SPEECH_DURATION and self._audio_buffer:
                        utterance = np.concatenate(self._audio_buffer)
                        if self.on_utterance:
                            # Fire callback in a thread to not block audio
                            threading.Thread(
                                target=self.on_utterance,
                                args=(utterance,),
                                daemon=True
                            ).start()

                    # Reset
                    self._is_speaking = False
                    self._audio_buffer = []
                    self._silence_start = None
                    self._speech_start = None
