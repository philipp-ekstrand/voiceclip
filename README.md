# voiceClip (macOS, offline)

Local speech-to-text transcription in your macOS menubar. Click to record, click to stop, click to copy. Powered by whisper.cpp with the `large-v3` model — 100% offline, no API costs.

## Features

- **Menubar workflow:** Record → Stop → Copy → Cmd+V
- **Streaming transcription:** Audio is transcribed during recording, not after. ~2-3s wait regardless of recording length.
- **Fully offline:** All processing on-device (Apple Silicon, Metal GPU)
- **High quality:** Whisper large-v3 with beam search, chain prompting, tuned for German/English mixed speech
- **Single-instance:** Only one voiceClip runs at a time

## Quick Start

```bash
# 1. Setup (one-time)
./setup.sh

# 2. Run from source
./run.sh
```

On first launch, the whisper model (~2.9 GB) is downloaded automatically.

## Build .app

```bash
# Build native macOS app
./scripts/build_app.sh

# Sign locally (development)
./scripts/sign_local.sh

# Sign + notarize (distribution)
./scripts/sign_notarize.sh /Applications/voiceClip.app \
  "Developer ID Application: YOUR NAME (TEAMID)" \
  my-notary-profile
```

See [architecture/BUILD_RELEASE.md](architecture/BUILD_RELEASE.md) for details.

## User Flow

1. Click menubar icon → recording starts
2. Click again → recording stops, transcription finalizes (~2-3s)
3. Click copy icon → text copied to clipboard
4. Cmd+V → paste anywhere

The menubar menu also provides:
- **Session zuruecksetzen** — reset if something gets stuck
- **Engine neu starten** — restart whisper-server

## How It Works

VoiceClip uses **streaming transcription**: audio is split into 5-second chunks and sent to a local whisper-server during recording. By the time you stop, most audio is already transcribed. Only the final chunk needs processing.

If whisper-server is unavailable, VoiceClip falls back to whisper-cli (slower, processes all audio after stop).

## Architecture Documentation

| Document | Description |
|----------|-------------|
| [architecture/README.md](architecture/README.md) | Overview, class map, tech stack |
| [architecture/STATE_MACHINE.md](architecture/STATE_MACHINE.md) | UI states, transitions, error recovery |
| [architecture/AUDIO_PIPELINE.md](architecture/AUDIO_PIPELINE.md) | Audio capture, streaming vs HQ mode |
| [architecture/STREAMING.md](architecture/STREAMING.md) | Streaming deep dive, quality tuning |
| [architecture/BUILD_RELEASE.md](architecture/BUILD_RELEASE.md) | Build, sign, notarize, deploy |
| [architecture/CONFIGURATION.md](architecture/CONFIGURATION.md) | Env vars, settings, runtime files |

## Model

- **File:** `~/.whisper/ggml-large-v3.bin` (2.9 GB)
- **Quality:** Full precision (no quantization)
- **Auto-download:** On first launch if missing

## Requirements

- macOS (Apple Silicon)
- Python 3
- Homebrew
- `whisper-cpp` (`brew install whisper-cpp`)
- `portaudio` (`brew install portaudio`)

## Configuration

All settings are optional. See [architecture/CONFIGURATION.md](architecture/CONFIGURATION.md) for the full reference.

Key environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `VOICECLIP_CHUNK_MS` | `5000` | Streaming chunk size (ms) |
| `VOICECLIP_OVERLAP_MS` | `600` | Chunk overlap (ms) |
| `VOICECLIP_SERVER_PORT` | dynamic | Fixed port for whisper-server |

## Runtime Files

- **Logs:** `~/Library/Logs/voiceClip/voiceclip.log`
- **Server registry:** `~/Library/Application Support/voiceClip/whisper_servers.json`
- **Models:** `~/.whisper/`
