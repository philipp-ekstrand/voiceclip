# Build & Release

## Prerequisites

| Dependency | Install | Purpose |
|-----------|---------|---------|
| Python 3 | pre-installed on macOS | Runtime |
| Homebrew | [brew.sh](https://brew.sh) | Package manager |
| portaudio | `brew install portaudio` | Audio I/O (sounddevice backend) |
| whisper-cpp | `brew install whisper-cpp` | Provides `whisper-cli` and `whisper-server` |

## Setup (One-Time)

```bash
./setup.sh
```

What it does:
1. Checks macOS, Python 3, Homebrew
2. Installs `portaudio` and `whisper-cpp` via Homebrew
3. Creates Python virtual environment (`.venv/`)
4. Installs Python packages from `requirements.txt`

### Python Dependencies

```
PyQt6>=6.6.0              # GUI framework
numpy>=1.26.0             # PCM array handling
sounddevice>=0.4.6        # Audio recording (PortAudio)
pyperclip>=1.9.0          # Clipboard access
requests>=2.31.0          # HTTP client (whisper-server)
urllib3<2                 # HTTP compatibility
pyinstaller>=6.6.0        # App packaging
pyobjc-framework-Cocoa>=10.2  # macOS native integration
```

## Development

```bash
./run.sh
```

Runs `main.py` directly from source using the `.venv` Python. No build step needed. Changes take effect immediately on restart.

## Build Pipeline

### Step 1: Build .app Bundle

```bash
./scripts/build_app.sh
```

What it does:
1. Generates app icon (`scripts/generate_icon.py` → `assets/voiceClip.icns`)
2. Cleans `build/` and `dist/` directories
3. Runs PyInstaller with `voiceClip.spec`
4. Kills any running voiceClip instances
5. Copies `.app` to `/Applications/voiceClip.app` and `~/Applications/voiceClip.app`
6. Removes quarantine attribute

### PyInstaller Configuration (`voiceClip.spec`)

| Setting | Value |
|---------|-------|
| Bundle ID | `com.voiceclip.voiceClip` |
| Version | `1.0.0` |
| LSUIElement | `True` (menubar-only, no Dock icon) |
| Console | `False` |
| Icon | `assets/voiceClip.icns` |
| Data files | `assets/` directory bundled |
| Hidden imports | AppKit, Cocoa, Foundation, objc |
| Microphone usage | "voiceClip braucht Mikrofonzugriff fuer lokale Offline-Transkription." |

### Step 2: Code Signing

#### Option A: Local Signing (Development/Testing)

```bash
./scripts/sign_local.sh
```

Uses ad-hoc signing (`codesign --sign -`). The app works on the machine it was built on but triggers Gatekeeper warnings on other machines.

#### Option B: Notarized Release (Distribution)

```bash
# One-time: store Apple credentials
xcrun notarytool store-credentials my-notary-profile \
  --apple-id <APPLE_ID> \
  --team-id <TEAM_ID> \
  --password <APP_SPECIFIC_PASSWORD>

# Sign + notarize
./scripts/sign_notarize.sh \
  /Applications/voiceClip.app \
  "Developer ID Application: YOUR NAME (TEAMID)" \
  my-notary-profile
```

What `sign_notarize.sh` does:
1. Code signs with Developer ID (hardened runtime + timestamp)
2. Verifies signature
3. Creates ZIP for notarization
4. Submits to Apple notary service (`xcrun notarytool submit --wait`)
5. Staples notarization ticket (`xcrun stapler staple`)
6. Verifies with Gatekeeper (`spctl -a -vv`)

After notarization, the app runs on any Mac without Gatekeeper warnings.

## Release Checklist

```
[ ] Code changes tested locally (./run.sh → record → stop → copy)
[ ] git commit + push to main
[ ] ./scripts/build_app.sh
[ ] Test built .app (not just source):
    [ ] Launch from /Applications/
    [ ] Record → Stop → Checkmark → Copy
    [ ] Verify whisper-server starts (check ~/Library/Logs/voiceClip/voiceclip.log)
[ ] ./scripts/sign_local.sh (or sign_notarize.sh for distribution)
[ ] Verify signature: codesign --verify --deep --strict /Applications/voiceClip.app
```

## Autostart at Login

To start VoiceClip automatically when the Mac boots:

1. Open **System Settings** → **General** → **Login Items**
2. Click **+** under "Open at Login"
3. Select `/Applications/voiceClip.app`

Or via command line:
```bash
osascript -e 'tell application "System Events" to make login item at end with properties {path:"/Applications/voiceClip.app", hidden:false}'
```

## Troubleshooting

### Build fails with "whisper-cli not found"
```bash
brew install whisper-cpp
```

### App crashes on launch
Check logs:
```bash
tail -50 ~/Library/Logs/voiceClip/voiceclip.log
```

### "App is damaged" on another Mac
The app needs notarization for distribution. Use `sign_notarize.sh` instead of `sign_local.sh`.

### Microphone permission denied
Go to **System Settings** → **Privacy & Security** → **Microphone** and enable voiceClip.

### whisper-server won't start
```bash
# Check if whisper-server is available
which whisper-server

# Check if model exists
ls -la ~/.whisper/ggml-large-v3.bin

# Check for orphaned server processes
ps aux | grep whisper-server
```
