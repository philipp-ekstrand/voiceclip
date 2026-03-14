# voiceClip (macOS, offline)

`voiceClip` ist eine echte macOS-Menubar-App:
- komplett offline (lokales `whisper.cpp`)
- Menubar-Workflow: Klick auf Statusleisten-Icon fuer `Record -> Stop -> Processing -> Copy`
- Qualitaetsmodus mit `large-v3` (hohe Genauigkeit)
- orange Branding + Microphone-App-Icon
- optionales Floating-Widget weiterhin per Menubar-Menue
- nativer macOS-Blur (Vibrancy)
- Aufnahme -> Transkription -> Kopieren ins Clipboard
- kein Python-Branding im Dock

## 1) Setup

```bash
cd "/Users/philipp.ekstrand/Downloads/Anitgravity Testing/voiceclip"
./setup.sh
```

## 2) App bauen (echte .app)

```bash
./scripts/build_app.sh
```

Ergebnis:
- `/Applications/voiceClip.app`
- `~/Applications/voiceClip.app`

## 3) Lokal signieren (empfohlen)

```bash
./scripts/sign_local.sh
```

## 4) Starten wie normale App

- Finder -> Programme -> `voiceClip.app` doppelklicken (System oder Benutzer-Programme)
- Danach laeuft `voiceClip` primär in der Menubar (oben rechts)

## Release ohne Gatekeeper-Warnungen (Developer ID + Notarize)

```bash
./scripts/sign_notarize.sh \
  /Applications/voiceClip.app \
  "Developer ID Application: DEIN NAME (TEAMID)" \
  my-notary-profile
```

Vorher ein Notary-Profil anlegen:

```bash
xcrun notarytool store-credentials my-notary-profile \
  --apple-id <APPLE_ID> \
  --team-id <TEAM_ID> \
  --password <APP_SPECIFIC_PASSWORD>
```

## User-Flow

1. Menubar-Icon klicken: Aufnahme starten
2. Erneut klicken: Aufnahme stoppen
3. Menubar zeigt kurz Processing-Animation
4. Menubar zeigt Copy-Status
5. Erneut klicken: Text ins Clipboard
6. `Cmd+V` in VS Code/Claude/Editor

Hinweis fuer lange Memos (2-10 Minuten):
- Standard ist durchgaengig `large-v3` (Qualitaetsmodus).
- Bei einem Problem gibt es im Menubar-Menue jetzt:
  - `Session zuruecksetzen`
  - `Engine neu starten`

## Modell-Download

- Standardmodell: `ggml-large-v3.bin` (wird beim ersten Start automatisch geladen)
- Gesuchte Pfade:
  - `~/.whisper/ggml-large-v3.bin`
  - `~/.cache/whisper/ggml-large-v3.bin`

## Konfigurierbare Env-Variablen

- `VOICECLIP_WHISPER_SERVER` (optional expliziter Pfad zu `whisper-server`)
- `VOICECLIP_WHISPER_CLI` (optional expliziter Pfad zu `whisper-cli`)
- `VOICECLIP_FAST_MODEL_PATH`, `VOICECLIP_FAST_MODEL_URL` (Legacy/Kompatibilitaet)
- `VOICECLIP_HQ_MODEL_PATH`, `VOICECLIP_HQ_MODEL_URL`
- `VOICECLIP_MODEL_PATH`, `VOICECLIP_MODEL_URL` (Legacy-Alias fuer HQ)
- `VOICECLIP_CHUNK_MS` (Default `2200`)
- `VOICECLIP_OVERLAP_MS` (Default `350`)
- `VOICECLIP_SERVER_PORT` (optional fixer Port; Standard ist dynamischer freier Port)
- `VOICECLIP_SERVER_CLEANUP_MODE` (`owned|global|off`, Default `owned`)
- `VOICECLIP_MAX_QUEUE_CHUNKS` (Default `120`)
- `VOICECLIP_ACTION_DEBOUNCE_MS` (Default `200`)
- `VOICECLIP_STOPPING_TIMEOUT_SECONDS` (Default `45`)
- `VOICECLIP_ENABLE_VIBRANCY=1` (optional: nativer macOS-Blur; standardmaessig aus fuer maximale Stabilitaet)

## Dateien

- App-Code: `main.py`
- PyInstaller-Spec: `voiceClip.spec`
- Build: `scripts/build_app.sh`
- Lokale Signierung: `scripts/sign_local.sh`
- Notarisierung: `scripts/sign_notarize.sh`
- App-Icon: `assets/voiceClip.icns` (automatisch generiert)

## Runtime-Diagnose

- PID-Registry: `~/Library/Application Support/voiceClip/whisper_servers.json`
- Logdatei: `~/Library/Logs/voiceClip/voiceclip.log`

Beim App-Start werden registrierte `voiceClip`-eigene `whisper-server` Prozesse automatisch bereinigt (`owned`-Modus).
