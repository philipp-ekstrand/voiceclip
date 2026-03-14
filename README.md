# voiceClip (macOS)

Sprach-Transkription in der macOS Menubar. Klick → Aufnahme → Klick → Stop → Klick → Kopieren → Cmd+V. Nutzt Groq's Whisper API fuer instant-Transkription (<1 Sekunde). Offline-Fallback auf lokales whisper-cli.

## Setup (fuer Claude Code Agents)

> **WICHTIG:** Diese Anleitung ist so geschrieben, dass ein Claude Code Agent sie Schritt fuer Schritt ausfuehren kann. Alle Befehle muessen auf macOS mit Apple Silicon laufen.

### Schritt 1: Repository klonen

```bash
git clone https://github.com/philipp-ekstrand/voiceclip.git
cd voiceclip
```

### Schritt 2: Dependencies installieren

```bash
./setup.sh
```

Das installiert: Homebrew-Pakete (`portaudio`, `whisper-cpp`), Python venv, pip-Pakete.

### Schritt 3: Groq API Key einrichten

Erstelle die Datei `~/.config/voiceClip/.env.local` (oder `~/Library/Application Support/voiceClip/.env.local`):

```bash
mkdir -p ~/Library/Application\ Support/voiceClip
cat > ~/Library/Application\ Support/voiceClip/.env.local << 'EOF'
GROQ_API_KEY=dein-api-key-hier
EOF
```

API Key bekommst du unter https://console.groq.com → API Keys → Create API Key.

### Schritt 4: App bauen und installieren

```bash
./scripts/build_app.sh
./scripts/sign_local.sh
```

Die App liegt danach unter `/Applications/voiceClip.app`.

### Schritt 5: Starten

Doppelklick auf `/Applications/voiceClip.app` oder:

```bash
open /Applications/voiceClip.app
```

Das Mikrofon-Icon erscheint in der Menubar (oben rechts).

### Schritt 6 (optional): Autostart

```bash
osascript -e 'tell application "System Events" to make login item at end with properties {path:"/Applications/voiceClip.app", hidden:false}'
```

## User Flow

1. Menubar-Icon klicken → Aufnahme startet
2. Nochmal klicken → Aufnahme stoppt, Transkription (<1s)
3. Copy-Icon klicken → Text im Clipboard
4. Cmd+V → Einfuegen

## Transcription Backends (Prioritaet)

| Prioritaet | Backend | Wann | Speed | Kosten |
|-----------|---------|------|-------|--------|
| 1 | **Groq API** | `GROQ_API_KEY` gesetzt | <1s | ~$5-7/Monat |
| 2 | Lokales whisper-cli | Kein API Key oder offline | ~8-11s/Min | Kostenlos |

Bei Netzwerkausfall faellt die App automatisch auf lokales whisper-cli zurueck (Notification wird angezeigt).

## Requirements

- macOS (Apple Silicon)
- Python 3
- Homebrew
- Internetverbindung (fuer Groq API; ohne gehts lokal weiter)

## Dateien

| Datei | Beschreibung |
|-------|-------------|
| `main.py` | Gesamte App (~3400 Zeilen, Single-File) |
| `.env.local` | Groq API Key (NICHT committen!) |
| `voiceClip.spec` | PyInstaller Build-Config |
| `setup.sh` | Dependency-Installation |
| `run.sh` | Dev-Modus (aus Source) |
| `scripts/build_app.sh` | Baut `/Applications/voiceClip.app` |
| `scripts/sign_local.sh` | Lokales Code-Signing |
| `scripts/sign_notarize.sh` | Apple Notarisierung (Distribution) |

## Logs & Diagnose

```bash
# Aktuelle Logs
tail -50 ~/Library/Logs/voiceClip/voiceclip.log

# Pruefen ob Groq aktiv
grep "groq_api_configured" ~/Library/Logs/voiceClip/voiceclip.log
```

## Architecture Documentation

Detaillierte technische Docs unter [architecture/](architecture/README.md).
