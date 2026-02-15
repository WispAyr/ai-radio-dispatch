"""Central configuration for the Radio Dispatch Operator System."""
import sounddevice as sd

# Audio settings
SAMPLE_RATE = 16000  # Hz - Whisper expects 16kHz
CHANNELS = 1
BLOCK_SIZE = 1024  # Audio frames per buffer

# Auto-detect the real microphone (skip virtual audio devices, but keep BlackHole for system audio)
_VIRTUAL_DEVICES = {"instashare", "immersed", "teams audio", "soundflower", "loopback"}

def _find_real_mic():
    """Find the real microphone, preferring MacBook Pro Microphone."""
    devices = sd.query_devices()
    real_mics = []
    for i, d in enumerate(devices):
        if d['max_input_channels'] > 0:
            name = d['name'].lower()
            if not any(v in name for v in _VIRTUAL_DEVICES):
                real_mics.append((i, d['name']))
    # Prefer MacBook Pro Microphone
    for idx, name in real_mics:
        if 'macbook' in name.lower():
            return idx
    # Fall back to first real mic
    if real_mics:
        return real_mics[0][0]
    return None  # Will use system default

INPUT_DEVICE = _find_real_mic()
print(f"[Config] Using input device: {INPUT_DEVICE} = {sd.query_devices(INPUT_DEVICE)['name'] if INPUT_DEVICE is not None else 'system default'}")

# Operating mode
MODE = "transcribe"  # "transcribe" (Phase 1) or "dispatch" (Phase 2)


# Voice Activity Detection
VAD_ENERGY_THRESHOLD = 0.02  # RMS energy threshold to detect speech
VAD_SILENCE_DURATION = 1.5   # Seconds of silence before utterance is considered complete
VAD_MIN_SPEECH_DURATION = 0.5  # Minimum seconds of speech to trigger processing

# Speech-to-Text (faster-whisper)
WHISPER_MODEL = "medium.en"  # Options: tiny.en, base.en, small.en, medium.en, large-v3
WHISPER_DEVICE = "cpu"

# LLM (Ollama)
OLLAMA_URL = "http://localhost:11434"
OLLAMA_MODEL = "llama3.1:8b"
OLLAMA_CONTEXT_WINDOW = 20  # Number of conversation turns to keep

# Text-to-Speech (Piper)
PIPER_VOICE = "en_US-lessac-medium"  # Will be downloaded on first run
PIPER_VOICES_DIR = "voices"
PIPER_SPEAKER_ID = None  # None = default speaker

# TTS output sample rate (Piper outputs at this rate)
TTS_SAMPLE_RATE = 22050

# Web server
SERVER_HOST = "0.0.0.0"
SERVER_PORT = 8080

# Dispatch persona
DISPATCH_SYSTEM_PROMPT = """You are a professional radio dispatch operator. You handle communications between field units and the dispatch center.

Your responsibilities:
- Acknowledge incoming radio messages clearly and concisely
- Assign and track unit designations
- Provide status updates and coordinate responses
- Use standard radio protocol language (e.g., "Copy that", "10-4", "Roger")
- Keep responses brief and professional — radio time is precious
- Log key details: locations, unit numbers, incident types, times

Communication style:
- Speak in short, clear sentences
- Use radio callsigns and codes where appropriate
- Be calm and authoritative, even in emergencies
- Confirm critical information by repeating it back
- Always acknowledge transmissions

You are currently on active duty monitoring the radio channel. Respond to all incoming transmissions professionally."""
