"""Text-to-Speech using Piper TTS."""

import io
import os
import wave
import numpy as np
import sounddevice as sd
from config import PIPER_VOICE, PIPER_VOICES_DIR, PIPER_SPEAKER_ID, TTS_SAMPLE_RATE


class TextToSpeech:
    """Converts text to speech using Piper and plays it."""

    def __init__(self):
        self._voice_path = None
        self._voice = None

    def load(self):
        """Load the Piper voice model. Call once at startup."""
        model_path = os.path.join(PIPER_VOICES_DIR, f"{PIPER_VOICE}.onnx")
        config_path = os.path.join(PIPER_VOICES_DIR, f"{PIPER_VOICE}.onnx.json")

        if not os.path.exists(model_path):
            raise FileNotFoundError(
                f"Piper voice model not found at {model_path}. "
                f"Download it first."
            )

        self._voice_path = model_path
        print(f"[TTS] Piper voice model: {model_path}")

    def speak(self, text: str) -> np.ndarray:
        """
        Convert text to speech and play it.

        Args:
            text: The text to speak.

        Returns:
            numpy array of audio samples.
        """
        if not self._voice_path:
            raise RuntimeError("TTS not loaded. Call load() first.")

        # Use piper CLI-style synthesis via the Python API
        from piper import PiperVoice

        voice = PiperVoice.load(self._voice_path)

        # Synthesize to a WAV buffer
        wav_buffer = io.BytesIO()
        with wave.open(wav_buffer, 'wb') as wav_file:
            voice.synthesize(text, wav_file, speaker_id=PIPER_SPEAKER_ID)

        # Read back the WAV data
        wav_buffer.seek(0)
        with wave.open(wav_buffer, 'rb') as wav_file:
            sample_rate = wav_file.getframerate()
            n_frames = wav_file.getnframes()
            audio_data = wav_file.readframes(n_frames)

        # Convert to numpy float32
        audio = np.frombuffer(audio_data, dtype=np.int16).astype(np.float32) / 32768.0

        # Play the audio
        sd.play(audio, samplerate=sample_rate)
        sd.wait()

        return audio

    def synthesize_to_bytes(self, text: str) -> bytes:
        """
        Convert text to WAV bytes without playing.

        Args:
            text: The text to speak.

        Returns:
            WAV file bytes.
        """
        if not self._voice_path:
            raise RuntimeError("TTS not loaded. Call load() first.")

        from piper import PiperVoice
        voice = PiperVoice.load(self._voice_path)

        wav_buffer = io.BytesIO()
        with wave.open(wav_buffer, 'wb') as wav_file:
            voice.synthesize(text, wav_file, speaker_id=PIPER_SPEAKER_ID)

        return wav_buffer.getvalue()
