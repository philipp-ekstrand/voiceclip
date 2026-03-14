# VoiceClip

## Was ist VoiceClip?

macOS Menubar-App fuer Sprachtranskription. Aufnahme starten -> Stoppen -> Text im Clipboard. Primaer: Remote-Server mit `faster-whisper` (large-v3). Fallback: lokales whisper.cpp.

## Tech Stack

- **Client:** Python 3 + PyQt6, kompiliert mit PyInstaller zu nativer .app
- **Server:** FastAPI + faster-whisper (CTranslate2), Docker auf Hetzner VPS
- **Lokaler Fallback:** whisper.cpp (`whisper-cli` via Homebrew)
- **Modell:** `large-v3` (volle Praezision, sowohl lokal als auch remote)
- **Audio:** sounddevice (PortAudio), 16kHz mono PCM
- **Plattform:** macOS only (Apple Silicon optimiert, Metal GPU)

## Architektur

### Client (main.py, ~3200 Zeilen)

| Klasse | Funktion |
|--------|----------|
| `VoiceClipWidget` | Hauptwidget, State Machine, UI |
| `RemoteTranscribeThread` | Remote-Transkription via HTTPS POST an Server |
| `TranscribeThread` | Lokale Transkription via whisper-cli (Fallback) |
| `StreamingTranscriptionController` | Streaming-Chunks (fuer Phase 3) |
| `WhisperServerProcessManager` | Lokaler whisper-server Lifecycle |
| `AudioRecorder` | Audio-Aufnahme via Subprocess |
| `TranscriptAssembler` | Overlap-Deduplikation fuer Streaming |

### Server (server/)

| Datei | Funktion |
|-------|----------|
| `app.py` | FastAPI mit /health, /transcribe, /transcribe-chunk |
| `Dockerfile` | Python 3.11 + faster-whisper, Modell wird beim Build geladen |
| `docker-compose.yml` | Deployment-Konfiguration |

### Transkriptions-Flow

1. User klickt Menubar-Icon -> Aufnahme startet
2. User klickt nochmal -> Aufnahme stoppt, WAV wird erstellt
3. **Server verfuegbar?** WAV wird per HTTPS an `/transcribe` gesendet
4. **Server nicht verfuegbar?** Fallback auf lokales `whisper-cli`
5. Transkript -> Clipboard -> User kann mit Cmd+V einfuegen

## Env-Variablen

| Variable | Default | Beschreibung |
|----------|---------|-------------|
| `VOICECLIP_REMOTE_SERVER_URL` | - | URL des Transkriptions-Servers (z.B. `https://whisper.example.com`) |
| `VOICECLIP_REMOTE_API_KEY` | - | Bearer-Token fuer Server-Auth |
| `VOICECLIP_HQ_MODEL_PATH` | `~/.whisper/ggml-large-v3.bin` | Pfad zum lokalen Modell |
| `VOICECLIP_WHISPER_CLI` | auto-detect | Pfad zu whisper-cli |
| `VOICECLIP_WHISPER_SERVER` | auto-detect | Pfad zu whisper-server |
| `VOICECLIP_CHUNK_MS` | 2200 | Streaming Chunk-Groesse |
| `VOICECLIP_OVERLAP_MS` | 350 | Streaming Overlap |

## Build & Run

```bash
# Client: Setup (einmalig)
./setup.sh

# Client: Dev-Modus
./run.sh

# Client: App bauen
./scripts/build_app.sh

# Server: Lokal testen
cd server && docker compose up --build

# Server: Mit API-Key
VOICECLIP_API_KEY=mein-secret docker compose up --build
```

## Server-Deployment

```bash
# Auf dem Hetzner VPS
git clone <repo> && cd voiceclip/server
echo "VOICECLIP_API_KEY=<secret>" > .env
docker compose up -d --build
```

## Wichtige Regeln

- **Qualitaet geht vor Speed.** Immer `large-v3` Modell verwenden.
- **Keine API-Kosten.** Alles auf eigenem Server oder lokal.
- **Das quantisierte Turbo-Modell (q5_0) hat schlechte Qualitaet** -> Nicht verwenden!
- **Das volle Turbo-Modell hat ebenfalls messbar schlechtere Qualitaet** -> Nicht verwenden!
- **Server-first:** Wenn `VOICECLIP_REMOTE_SERVER_URL` gesetzt ist, wird der Server bevorzugt.
- **Automatischer Fallback:** Wenn Server nicht erreichbar, wird lokal transkribiert.

## Runtime-Dateien

- Logs: `~/Library/Logs/voiceClip/voiceclip.log`
- Server-Registry: `~/Library/Application Support/voiceClip/whisper_servers.json`
- PID: `~/Library/Application Support/voiceClip/voiceclip.pid`
- Modelle: `~/.whisper/`
