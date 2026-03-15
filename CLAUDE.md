# VoiceClip

## Was ist VoiceClip?

macOS Menubar-App fuer Sprachtranskription. Click → Record → Click → Stop → Text wird automatisch ins aktive Textfeld eingefuegt (Auto-Paste). Primaer Groq Whisper API, Fallback auf lokales whisper-cli.

## Tech Stack

- **App:** Python 3 + PyQt6, kompiliert mit PyInstaller zu nativer .app
- **Transkription:** whisper.cpp (`whisper-server` lokal, `whisper-cli` als Fallback)
- **Modell:** `ggml-large-v3.bin` (2.9GB, volle Praezision)
- **Audio:** sounddevice (PortAudio), In-Process InputStream fuer Streaming, Subprocess fuer HQ-Fallback
- **Plattform:** macOS only (Apple Silicon, Metal GPU)

## Architektur

Alles in `main.py` (~3300 Zeilen). Single-File-App.

### Kernklassen

| Klasse | Funktion |
|--------|----------|
| `VoiceClipWidget` | Hauptwidget, State Machine, gesamte UI-Logik |
| `VoiceClipApp` | Tray-Icon, Menubar-Integration, System-Events |
| `AudioRecorder` | Audio-Aufnahme: In-Process (Streaming) oder Subprocess (HQ) |
| `StreamingTranscriptionController` | Chunk-Queue, Worker-Thread, Chain-Prompting |
| `TranscriptAssembler` | Merged Chunk-Transkripte mit Overlap-Deduplizierung |
| `WhisperServerProcessManager` | Lifecycle: Start, Health, Warmup, Cleanup von whisper-server |
| `TranscribeThread` | QThread: ruft `whisper-cli` auf (HQ-Fallback) |
| `FinalizeRecordingThread` | QThread: konvertiert Audio-Chunks zu WAV-Datei |

### Modi

**Streaming-Modus ("fast") — Standard:**
- whisper-server startet beim App-Boot, Modell bleibt im RAM
- AudioRecorder laeuft In-Process (`sounddevice.InputStream` Callback)
- 5s Chunks mit 600ms Overlap werden waehrend der Aufnahme transkribiert
- Chain-Prompting: vorheriger Chunk-Text als Kontext fuer den naechsten
- Nach Stopp: nur letzter Chunk muss noch verarbeitet werden (~2-3s Wartezeit)

**HQ-Modus — Fallback (wenn whisper-server nicht verfuegbar):**
- AudioRecorder laeuft als Subprocess, schreibt direkt in WAV-Datei
- Nach Stopp wird gesamtes Audio per whisper-cli transkribiert
- Langsamer (~11s fuer 1 Min Audio), aber robuster

### User Flow (Streaming-Modus)

```
App Boot → Groq API Key geladen (oder Fallback auf whisper-cli)
User klickt in Textfeld (Slack, Mail, Browser, etc.)
User klickt Tray → IDLE
  → start_recording() → STARTING (In-Process InputStream startet)
  → RECORDING (Puls-Animation)

User klickt nochmal → stop_and_transcribe()
  → recorder.stop() → Audio wird an Groq API gesendet
  → STOPPING → PROCESSING (Transkription, <1s mit Groq)
  → CHECK (Checkmark, 500ms Flash)
  → Auto-Paste: Text wird in Clipboard kopiert + Cmd+V simuliert → direkt ins aktive Textfeld eingefuegt
  → Session geschlossen → IDLE
```

### State Machine

```
BOOT → IDLE → STARTING → RECORDING → STOPPING → PROCESSING → CHECK → (Auto-Paste) → IDLE
                                                                            ↓
                                                                          ERROR → IDLE
```

## Whisper-Server Qualitaetseinstellungen

Der lokale whisper-server startet mit folgenden Flags fuer maximale Transkriptionsqualitaet:

| Flag | Wert | Beschreibung |
|------|------|-------------|
| `-l de` | Deutsch | Feste Sprache, kein Auto-Detect pro Chunk |
| `-bo 5` | best-of 5 | 5 Kandidaten statt Server-Default 2 |
| `-bs 5` | beam-size 5 | Beam Search statt Greedy Decoding |
| `-sns` | suppress-nst | Unterdrueckt Non-Speech-Tokens (Artefakte) |
| `-fa` | flash-attn | Metal GPU-Beschleunigung |

Per-Request Parameter bei jedem Chunk:

| Parameter | Wert | Beschreibung |
|-----------|------|-------------|
| `temperature` | 0.0 | Greedy fuer hoechste Konfidenz |
| `temperature_inc` | 0.2 | Fallback-Retry bei unsicheren Segmenten |
| `language` | de | Redundanz zum Server-Flag |
| `prompt` | vorheriger Chunk-Text | Chain-Prompting fuer Kontext-Kontinuitaet |

Streaming-Parameter:

| Parameter | Wert | Beschreibung |
|-----------|------|-------------|
| Chunk-Groesse | 5000ms | Genug Kontext fuer Whisper pro Chunk |
| Overlap | 600ms | Vermeidet Worttrennung an Chunk-Grenzen |
| Chunk-Timeout | 10s | Ausreichend fuer Beam Search |
| Chain-Prompt-Laenge | 224 Zeichen | Letzte ~2 Saetze als Kontext |

## KRITISCHE REGELN

### Modell
- **NUR `large-v3` verwenden.** Turbo-Modelle (q5_0 und voll) haben schlechtere Qualitaet.
- Modell liegt unter `~/.whisper/ggml-large-v3.bin`

### Streaming-Modus
- Standardmodus ist `"fast"` (Streaming mit whisper-server)
- AudioRecorder nutzt In-Process `sounddevice.InputStream` (kein Subprocess)
- `consume_pending_samples()` liefert echte PCM-Daten aus dem Callback-Buffer
- Falls whisper-server nicht gefunden: automatischer Fallback auf `"hq"` (whisper-cli)

### Testen
- **Jede Aenderung an main.py muss getestet werden** bevor sie dem User praesentiert wird.
- Test-Flow: App starten → Server-Start abwarten → Aufnahme → Stopp → Checkmark erscheint → Copy funktioniert.

### Performance (gemessene Werte, M3 Pro)

| Modus | Audio-Laenge | Wartezeit nach Stopp |
|-------|-------------|---------------------|
| Streaming (fast) | beliebig | ~2-3s (nur letzter Chunk) |
| HQ (whisper-cli) | 33.5s | 7.7s |
| HQ (whisper-cli) | 63s | 11.2s |

## Env-Variablen

| Variable | Default | Beschreibung |
|----------|---------|-------------|
| `VOICECLIP_HQ_MODEL_PATH` | `~/.whisper/ggml-large-v3.bin` | Pfad zum Modell |
| `VOICECLIP_WHISPER_CLI` | auto-detect | Pfad zu whisper-cli |
| `VOICECLIP_CHUNK_MS` | 5000 | Chunk-Groesse in ms (Streaming) |
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

1. **Sprache:** Optimiert fuer Deutsch mit englischen Fachbegriffen. Rein englische Memos werden als Deutsch interpretiert (language=de fest).
2. **Luefter:** Waehrend der Transkription dreht die GPU kurz hoch. Bei Streaming verteilt sich die Last gleichmaessiger.
3. **Gelegentliche Abstuerze:** Audio-Worker kann in Timeout-Situationen haengen bleiben.
4. **Erster Start:** whisper-server braucht ~5-10s zum Starten und Warmup. Danach bleibt das Modell im RAM.
