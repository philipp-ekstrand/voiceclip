# VoiceClip

## Was ist VoiceClip?

macOS Menubar-App fuer Sprachtranskription. Click → Record → Click → Stop → Checkmark → Copy → Cmd+V. Basiert auf whisper.cpp mit dem `large-v3` Modell (lokal, offline).

## Tech Stack

- **App:** Python 3 + PyQt6, kompiliert mit PyInstaller zu nativer .app
- **Transkription:** whisper.cpp (`whisper-cli` via Homebrew)
- **Modell:** `ggml-large-v3.bin` (2.9GB, volle Praezision)
- **Audio:** sounddevice (PortAudio) via Subprocess, 16kHz mono PCM
- **Plattform:** macOS only (Apple Silicon, Metal GPU)

## Architektur

Alles in `main.py` (~3200 Zeilen). Single-File-App.

### Kernklassen

| Klasse | Funktion |
|--------|----------|
| `VoiceClipWidget` | Hauptwidget, State Machine, gesamte UI-Logik |
| `VoiceClipApp` | Tray-Icon, Menubar-Integration, System-Events |
| `AudioRecorder` | Subprocess-basierte Audio-Aufnahme (WAV-Datei) |
| `TranscribeThread` | QThread: ruft `whisper-cli` auf, gibt Text zurueck |
| `RemoteTranscribeThread` | QThread: sendet WAV an Remote-Server (optional) |
| `FinalizeRecordingThread` | QThread: konvertiert Audio-Chunks zu WAV-Datei |

### User Flow (HQ-Modus, der EINZIGE funktionierende Modus)

```
User klickt Tray → IDLE
  → start_recording() → STARTING (Mic-Subprocess startet)
  → RECORDING (Puls-Animation, Audio wird in WAV geschrieben)

User klickt nochmal → stop_and_transcribe_hq()
  → recorder.stop() → Subprocess beendet, WAV-Datei gelesen
  → STOPPING → FinalizeRecordingThread (Audio → temp WAV)
  → PROCESSING → TranscribeThread (whisper-cli -m model -f wav)
  → CHECK (Checkmark, 500ms Flash)
  → COPY_READY (Copy-Icon + "Kopieren" Button)

User klickt Copy → Text in Clipboard → IDLE
```

### State Machine

```
BOOT → DOWNLOADING → IDLE → STARTING → RECORDING → STOPPING → PROCESSING → CHECK → COPY_READY → IDLE
                                                                                         ↓
                                                                                       ERROR → IDLE
```

## KRITISCHE REGELN

### Modell
- **NUR `large-v3` verwenden.** Turbo-Modelle (q5_0 und voll) haben schlechtere Qualitaet.
- Modell liegt unter `~/.whisper/ggml-large-v3.bin`

### Streaming-Modus ("fast")
- **IST NICHT FUNKTIONAL.** `AudioRecorder.consume_pending_samples()` ist ein Stub (gibt immer leeres Array zurueck).
- Der Audio-Worker ist ein Subprocess der direkt in eine WAV-Datei schreibt. Es gibt keinen IPC-Kanal fuer Live-Audio.
- `self.mode` MUSS auf `"hq"` bleiben bis der Streaming-Code komplett neu implementiert wird.
- **NIEMALS den Mode auf "fast" setzen ohne eine funktionierende AudioRecorder-Pipeline.**

### Testen
- **Jede Aenderung an main.py muss getestet werden** bevor sie dem User praesentiert wird.
- Benchmark-Tests am whisper-server sind NICHT gleichbedeutend mit einem funktionierenden App-Flow.
- Test-Flow: App starten → Aufnahme → Stopp → Checkmark erscheint → Copy funktioniert.

### Performance (gemessene Werte, M3 Pro)
- whisper-cli (HQ-Modus): 7.7s fuer 33.5s Audio
- whisper-server (Modell im RAM): 4.6s fuer 33.5s Audio, 2.3s fuer 10s Chunk
- Hetzner CPU-Server: 28s fuer 33.5s Audio (LANGSAMER als lokal!)
- Apple Metal GPU ist 3-4x schneller als Hetzner CPX42 CPU fuer Whisper

## Env-Variablen

| Variable | Default | Beschreibung |
|----------|---------|-------------|
| `VOICECLIP_HQ_MODEL_PATH` | `~/.whisper/ggml-large-v3.bin` | Pfad zum Modell |
| `VOICECLIP_WHISPER_CLI` | auto-detect | Pfad zu whisper-cli |
| `VOICECLIP_REMOTE_SERVER_URL` | - | Remote-Server URL (optional) |
| `VOICECLIP_REMOTE_API_KEY` | - | Remote-Server API Key (optional) |

## Build & Run

```bash
# Setup (einmalig)
./setup.sh

# Dev-Modus (aus Source)
./run.sh

# App bauen (.app Bundle)
./scripts/build_app.sh

# Lokal signieren
./scripts/sign_local.sh
```

## Runtime-Dateien

- Logs: `~/Library/Logs/voiceClip/voiceclip.log`
- Server-Registry: `~/Library/Application Support/voiceClip/whisper_servers.json`
- PID: `~/Library/Application Support/voiceClip/voiceclip.pid`
- Modelle: `~/.whisper/`
- Settings: macOS QSettings (`com.voiceclip.voiceClip`)

## Bekannte Limitierungen

1. **Geschwindigkeit:** ~7.7s Verarbeitung fuer 33.5s Audio (whisper-cli). Fuer lange Aufnahmen (10 Min) ca. 2 Minuten Wartezeit.
2. **Luefter:** Waehrend der Transkription dreht die GPU hoch. Dauert aber nur wenige Sekunden.
3. **Gelegentliche Abstuerze:** Audio-Worker-Subprocess kann in Timeout-Situationen haengen bleiben.
4. **Streaming nicht implementiert:** Der "fast" Modus existiert als Code, ist aber ein nicht-funktionaler Stub.

## Verbesserungsmoeglichkeiten (Zukunft)

1. **whisper-server statt whisper-cli fuer HQ:** Modell bleibt im RAM, spart Ladezeit pro Transkription. Braucht: TranscribeThread umschreiben auf HTTP POST statt subprocess.
2. **Echtes Streaming:** AudioRecorder muesste auf IPC-Pipe umgestellt werden statt Subprocess+WAV-File. Groesserer Umbau.
3. **GPU-Server:** Nur mit NVIDIA GPU sinnvoll (~50-100 EUR/mo). CPU-Server sind langsamer als lokaler M3 Pro.
