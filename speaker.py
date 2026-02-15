"""Speaker differentiation using SpeechBrain ECAPA-TDNN neural embeddings.

Falls back to basic spectral features if SpeechBrain is not available.
"""

import numpy as np
from config import SAMPLE_RATE

# Lazy-loaded globals
_sb_model = None
_sb_available = None


def _load_speechbrain():
    """Lazy-load the SpeechBrain speaker embedding model."""
    global _sb_model, _sb_available
    if _sb_available is not None:
        return _sb_available

    try:
        import torch
        from speechbrain.inference.speaker import EncoderClassifier
        _sb_model = EncoderClassifier.from_hparams(
            source="speechbrain/spkrec-ecapa-voxceleb",
            savedir="/tmp/speechbrain_ecapa",
            run_opts={"device": "cpu"}
        )
        _sb_available = True
        print("[Speaker] SpeechBrain ECAPA-TDNN model loaded")
        return True
    except Exception as e:
        print(f"[Speaker] SpeechBrain unavailable, using basic features: {e}")
        _sb_available = False
        return False


class SpeakerTracker:
    """
    Tracks and differentiates speakers using neural speaker embeddings.

    Uses SpeechBrain's ECAPA-TDNN model to produce 192-dimensional embeddings
    that capture unique vocal characteristics. Falls back to basic spectral
    features if SpeechBrain is not installed.
    """

    def __init__(self, similarity_threshold=0.70):
        """
        Args:
            similarity_threshold: Cosine similarity threshold for matching
                                  a speaker to an existing profile (0-1).
                                  Neural embeddings work well at 0.70.
        """
        self.profiles = []  # List of (label, embedding, count)
        self.similarity_threshold = similarity_threshold
        self._next_id = 1
        self._model_loaded = False

    def identify(self, audio: np.ndarray) -> str:
        """
        Identify or assign a speaker label for the given audio segment.

        Args:
            audio: numpy float32 array of audio samples at 16kHz.

        Returns:
            Speaker label string like "Speaker 1", "Speaker 2", etc.
        """
        # Lazy-load model on first call
        if not self._model_loaded:
            _load_speechbrain()
            self._model_loaded = True

        embedding = self._extract_embedding(audio)

        if embedding is None:
            return "Unknown"

        # Compare against known profiles
        best_match = None
        best_similarity = -1

        for i, (label, profile, count) in enumerate(self.profiles):
            sim = self._cosine_similarity(embedding, profile)
            if sim > best_similarity:
                best_similarity = sim
                best_match = i

        if best_match is not None and best_similarity >= self.similarity_threshold:
            # Update existing profile with running average
            label, profile, count = self.profiles[best_match]
            new_count = count + 1
            updated_profile = (profile * count + embedding) / new_count
            self.profiles[best_match] = (label, updated_profile, new_count)
            return label
        else:
            # New speaker
            label = f"Speaker {self._next_id}"
            self._next_id += 1
            self.profiles.append((label, embedding, 1))
            return label

    def reset(self):
        """Clear all speaker profiles."""
        self.profiles = []
        self._next_id = 1

    def _extract_embedding(self, audio: np.ndarray) -> np.ndarray:
        """Extract speaker embedding from audio."""
        if len(audio) < 1600:  # Less than 0.1s at 16kHz
            return None

        if _sb_available:
            return self._extract_neural_embedding(audio)
        else:
            return self._extract_basic_features(audio)

    def _extract_neural_embedding(self, audio: np.ndarray) -> np.ndarray:
        """Extract 192-dim ECAPA-TDNN speaker embedding using SpeechBrain."""
        try:
            import torch
            # Ensure audio is float32 mono
            if audio.dtype != np.float32:
                audio = audio.astype(np.float32)

            # Normalize audio
            max_val = np.abs(audio).max()
            if max_val > 0:
                audio = audio / max_val

            # Convert to torch tensor: shape (1, num_samples)
            waveform = torch.tensor(audio).unsqueeze(0)

            # Extract embedding
            with torch.no_grad():
                embedding = _sb_model.encode_batch(waveform)

            # embedding shape: (1, 1, 192) → flatten to (192,)
            return embedding.squeeze().numpy()

        except Exception as e:
            print(f"[Speaker] Neural embedding error: {e}")
            return self._extract_basic_features(audio)

    def _extract_basic_features(self, audio: np.ndarray) -> np.ndarray:
        """Fallback: extract basic spectral features."""
        try:
            if len(audio) < 1024:
                return None

            features = []

            # Spectral centroid
            fft = np.fft.rfft(audio)
            magnitude = np.abs(fft)
            freqs = np.fft.rfftfreq(len(audio), 1.0 / SAMPLE_RATE)
            if np.sum(magnitude) > 0:
                spectral_centroid = np.sum(freqs * magnitude) / np.sum(magnitude)
                features.append(spectral_centroid / (SAMPLE_RATE / 2))
            else:
                features.append(0.0)

            # Spectral bandwidth
            if np.sum(magnitude) > 0:
                centroid_hz = features[0] * (SAMPLE_RATE / 2)
                spectral_bandwidth = np.sqrt(
                    np.sum(((freqs - centroid_hz) ** 2) * magnitude) / np.sum(magnitude)
                )
                features.append(spectral_bandwidth / (SAMPLE_RATE / 2))
            else:
                features.append(0.0)

            # Spectral rolloff
            cumulative_energy = np.cumsum(magnitude ** 2)
            total_energy = cumulative_energy[-1] if len(cumulative_energy) > 0 else 1
            if total_energy > 0:
                rolloff_idx = np.searchsorted(cumulative_energy, 0.85 * total_energy)
                rolloff_freq = freqs[min(rolloff_idx, len(freqs) - 1)]
                features.append(rolloff_freq / (SAMPLE_RATE / 2))
            else:
                features.append(0.0)

            # Zero crossing rate
            zero_crossings = np.sum(np.abs(np.diff(np.sign(audio)))) / (2 * len(audio))
            features.append(zero_crossings)

            # RMS energy
            rms = np.sqrt(np.mean(audio ** 2))
            features.append(rms)

            # Spectral flatness
            magnitude_positive = magnitude[magnitude > 0]
            if len(magnitude_positive) > 0:
                geometric_mean = np.exp(np.mean(np.log(magnitude_positive + 1e-10)))
                arithmetic_mean = np.mean(magnitude_positive)
                flatness = geometric_mean / (arithmetic_mean + 1e-10)
                features.append(flatness)
            else:
                features.append(0.0)

            # Band energies
            n_bands = 8
            band_size = len(magnitude) // n_bands
            if band_size > 0:
                for b in range(n_bands):
                    start = b * band_size
                    end = start + band_size
                    band_energy = np.mean(magnitude[start:end] ** 2)
                    features.append(np.log(band_energy + 1e-10))
            else:
                features.extend([0.0] * n_bands)

            return np.array(features, dtype=np.float32)

        except Exception as e:
            print(f"[Speaker] Feature extraction error: {e}")
            return None

    def _cosine_similarity(self, a: np.ndarray, b: np.ndarray) -> float:
        """Compute cosine similarity between two vectors."""
        dot = np.dot(a, b)
        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return float(dot / (norm_a * norm_b))
