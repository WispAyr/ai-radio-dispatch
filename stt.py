"""Speech-to-Text using faster-whisper (CTranslate2 backend)."""

import numpy as np
from config import WHISPER_MODEL, SAMPLE_RATE


# Dispatch/police radio vocabulary prompt — helps Whisper recognize jargon
DISPATCH_PROMPT = (
    "10-4, 10-8, 10-20, 10-42, copy, dispatch, en route, "
    "signal, code, unit, badge, officer, suspect, vehicle, "
    "responding, clear, standby, over, roger, affirmative, negative, "
    "ambulance, fire, EMS, DUI, PI accident, plates, "
    "Adam, Boy, Charles, David, Edward, Frank, George, Henry, "
    "Ida, John, King, Lincoln, Mary, Nora, Ocean, Paul, Queen, "
    "Robert, Sam, Tom, Union, Victor, William, X-ray, Young, Zebra"
)


class SpeechToText:
    """Transcribes audio using faster-whisper for better speed and accuracy."""

    def __init__(self):
        self._model = None
        self._beam_size = 5

    def load(self):
        """Load the Whisper model. First run downloads the model."""
        from faster_whisper import WhisperModel
        import config

        model_name = config.WHISPER_MODEL
        print(f"[STT] Loading faster-whisper model '{model_name}'...")
        self._model = WhisperModel(
            model_name,
            device="cpu",
            compute_type="int8",
        )
        print(f"[STT] Model loaded ({model_name}).")

    def transcribe(self, audio: np.ndarray) -> str:
        """
        Transcribe audio to text.

        Args:
            audio: numpy float32 array of audio samples at 16kHz.

        Returns:
            Transcribed text string.
        """
        if self._model is None:
            raise RuntimeError("STT model not loaded. Call load() first.")

        segments, info = self._model.transcribe(
            audio,
            language="en",
            beam_size=self._beam_size,
            best_of=self._beam_size,
            initial_prompt=DISPATCH_PROMPT,
            vad_filter=True,
            vad_parameters=dict(
                min_silence_duration_ms=300,
                speech_pad_ms=200,
            ),
            no_speech_threshold=0.6,
            log_prob_threshold=-1.0,
            condition_on_previous_text=False,
        )

        text_parts = []
        for segment in segments:
            text_parts.append(segment.text.strip())

        return " ".join(text_parts).strip()
