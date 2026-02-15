"""Simple speaker differentiation using acoustic features."""

import numpy as np
from config import SAMPLE_RATE


class SpeakerTracker:
    """
    Tracks and differentiates speakers using basic audio features.
    
    Uses pitch estimation, spectral characteristics, and energy patterns
    to build speaker profiles and assign labels.
    """

    def __init__(self, similarity_threshold=0.65):
        """
        Args:
            similarity_threshold: Cosine similarity threshold for matching
                                  a speaker to an existing profile (0-1).
        """
        self.profiles = []  # List of (label, feature_vector, count)
        self.similarity_threshold = similarity_threshold
        self._next_id = 1

    def identify(self, audio: np.ndarray) -> str:
        """
        Identify or assign a speaker label for the given audio segment.
        
        Args:
            audio: numpy float32 array of audio samples at 16kHz.
            
        Returns:
            Speaker label string like "Speaker 1", "Speaker 2", etc.
        """
        features = self._extract_features(audio)
        
        if features is None or np.all(features == 0):
            return "Unknown"
        
        # Compare against known profiles
        best_match = None
        best_similarity = -1
        
        for i, (label, profile, count) in enumerate(self.profiles):
            sim = self._cosine_similarity(features, profile)
            if sim > best_similarity:
                best_similarity = sim
                best_match = i
        
        if best_match is not None and best_similarity >= self.similarity_threshold:
            # Update existing profile with running average
            label, profile, count = self.profiles[best_match]
            new_count = count + 1
            updated_profile = (profile * count + features) / new_count
            self.profiles[best_match] = (label, updated_profile, new_count)
            return label
        else:
            # New speaker
            label = f"Speaker {self._next_id}"
            self._next_id += 1
            self.profiles.append((label, features, 1))
            return label

    def reset(self):
        """Clear all speaker profiles."""
        self.profiles = []
        self._next_id = 1

    def _extract_features(self, audio: np.ndarray) -> np.ndarray:
        """Extract a feature vector from an audio segment."""
        try:
            if len(audio) < 1024:
                return None
            
            features = []
            
            # 1. Spectral centroid (brightness of voice)
            fft = np.fft.rfft(audio)
            magnitude = np.abs(fft)
            freqs = np.fft.rfftfreq(len(audio), 1.0 / SAMPLE_RATE)
            if np.sum(magnitude) > 0:
                spectral_centroid = np.sum(freqs * magnitude) / np.sum(magnitude)
                features.append(spectral_centroid / (SAMPLE_RATE / 2))  # Normalize
            else:
                features.append(0.0)
            
            # 2. Spectral bandwidth (spread of frequencies)
            if np.sum(magnitude) > 0:
                centroid_hz = features[0] * (SAMPLE_RATE / 2)
                spectral_bandwidth = np.sqrt(
                    np.sum(((freqs - centroid_hz) ** 2) * magnitude) / np.sum(magnitude)
                )
                features.append(spectral_bandwidth / (SAMPLE_RATE / 2))
            else:
                features.append(0.0)
            
            # 3. Spectral rolloff (frequency below which 85% of energy is contained)
            cumulative_energy = np.cumsum(magnitude ** 2)
            total_energy = cumulative_energy[-1] if len(cumulative_energy) > 0 else 1
            if total_energy > 0:
                rolloff_idx = np.searchsorted(cumulative_energy, 0.85 * total_energy)
                rolloff_freq = freqs[min(rolloff_idx, len(freqs) - 1)]
                features.append(rolloff_freq / (SAMPLE_RATE / 2))
            else:
                features.append(0.0)
            
            # 4. Zero crossing rate (rough pitch indicator)
            zero_crossings = np.sum(np.abs(np.diff(np.sign(audio)))) / (2 * len(audio))
            features.append(zero_crossings)
            
            # 5. RMS energy
            rms = np.sqrt(np.mean(audio ** 2))
            features.append(rms)
            
            # 6. Spectral flatness (tonal vs noisy)
            magnitude_positive = magnitude[magnitude > 0]
            if len(magnitude_positive) > 0:
                geometric_mean = np.exp(np.mean(np.log(magnitude_positive + 1e-10)))
                arithmetic_mean = np.mean(magnitude_positive)
                flatness = geometric_mean / (arithmetic_mean + 1e-10)
                features.append(flatness)
            else:
                features.append(0.0)
            
            # 7. MFCCs approximation — spectral shape in bands
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
