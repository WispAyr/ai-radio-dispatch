# 📻 AI Radio Dispatch Console

A local AI-powered radio dispatch transcription and monitoring system. Captures live audio, transcribes speech in real-time using Whisper, differentiates speakers, records every utterance, and displays interactive waveforms — all from a slick web dashboard.

**Everything runs locally on your Mac. Zero cloud costs.**

![Dashboard Screenshot](docs/dashboard.png)

## Features

- 🎙️ **Live Transcription** — Real-time speech-to-text via [faster-whisper](https://github.com/guillaumekln/faster-whisper) (CTranslate2)
- 👥 **Speaker Differentiation** — Acoustic feature-based speaker identification (spectral centroid, bandwidth, MFCCs)
- 🎵 **Waveform Recording** — Every utterance saved as WAV with interactive waveform playback
- 📻 **System Audio Capture** — Monitor Broadcastify/scanner feeds via BlackHole virtual audio
- ⚙️ **Live Settings** — Switch Whisper models, beam size, and VAD thresholds from the dashboard
- 🖥️ **Professional Dashboard** — Dark-themed dispatch console with WebSocket real-time updates

## Architecture

```
Audio Input → VAD → faster-whisper STT → Speaker ID → Dashboard
     ↓                    ↓
  BlackHole          WAV Recording
(system audio)       + Waveform
```

## Quick Start

### Prerequisites

- **macOS** (Apple Silicon recommended)
- **Python 3.9+**
- **BlackHole 2ch** (optional, for system audio capture)

### Install

```bash
# Clone
git clone https://github.com/WispAyr/ai-radio-dispatch.git
cd ai-radio-dispatch

# Install dependencies
pip3 install sounddevice numpy aiohttp faster-whisper

# Optional: BlackHole for system audio
brew install --cask blackhole-2ch
```

### Run

```bash
python3 main.py
```

Open **http://localhost:8080** and click **START**.

## Configuration

All settings are configurable from the dashboard UI:

| Setting | Description | Default |
|---------|-------------|---------|
| **Model** | Whisper model size | `medium.en` |
| **Beam Size** | Search beam width (accuracy vs speed) | `5` |
| **VAD Threshold** | Speech detection sensitivity | `0.02` |
| **Silence Duration** | Silence before utterance ends | `1.5s` |
| **Audio Source** | Input device selection | Auto-detect |

### Available Models

| Model | Size | Accuracy | Speed |
|-------|------|----------|-------|
| `tiny.en` | 39MB | ★☆☆☆☆ | Instant |
| `base.en` | 139MB | ★★☆☆☆ | Very fast |
| `small.en` | 461MB | ★★★☆☆ | Fast |
| `medium.en` | 1.5GB | ★★★★☆ | Good |
| `large-v3` | 3.1GB | ★★★★★ | Moderate |

## System Audio Setup (Broadcastify)

To capture audio from Chrome/Broadcastify:

1. Install BlackHole: `brew install --cask blackhole-2ch`
2. Reboot your Mac
3. Open **Audio MIDI Setup** → Create **Multi-Output Device**
4. Check **MacBook Pro Speakers** + **BlackHole 2ch**
5. Set Multi-Output as system output
6. Select **BlackHole 2ch** in the dashboard Audio Source dropdown

## Project Structure

```
├── main.py              # Entry point
├── config.py            # Configuration & auto-detection
├── audio_capture.py     # Mic input, VAD, device management
├── stt.py               # faster-whisper speech-to-text
├── speaker.py           # Speaker differentiation
├── dispatch.py          # Pipeline orchestrator
├── server.py            # Web server + WebSocket + API
├── recordings/          # Saved WAV files
└── static/
    ├── index.html       # Dashboard HTML
    ├── styles.css       # Dark theme styles
    └── app.js           # WebSocket client & waveform renderer
```

## Tech Stack

- **STT**: [faster-whisper](https://github.com/guillaumekln/faster-whisper) (CTranslate2)
- **Audio**: sounddevice + numpy
- **Web**: aiohttp (async HTTP + WebSocket)
- **Speaker ID**: Acoustic feature extraction + cosine similarity
- **Audio Routing**: BlackHole 2ch (virtual audio loopback)

## License

MIT
