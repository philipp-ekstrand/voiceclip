#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
APP_NAME="voiceClip"
DIST_APP="$PROJECT_DIR/dist/${APP_NAME}.app"
DIST_APP_ALT="$PROJECT_DIR/dist/${APP_NAME}/${APP_NAME}.app"
TARGET_APP="/Applications/${APP_NAME}.app"
USER_TARGET_APP="$HOME/Applications/${APP_NAME}.app"

cd "$PROJECT_DIR"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "Nur auf macOS verfuegbar."
  exit 1
fi

if [[ ! -x "$PROJECT_DIR/.venv/bin/python" ]]; then
  echo "Fehlt: .venv. Bitte zuerst ./setup.sh ausfuehren."
  exit 1
fi

if [[ ! -x "$PROJECT_DIR/.venv/bin/pyinstaller" ]]; then
  echo "PyInstaller fehlt in .venv. Bitte setup erneut ausfuehren."
  exit 1
fi

if ! command -v whisper-cli >/dev/null 2>&1 && [[ ! -x /opt/homebrew/bin/whisper-cli ]] && [[ ! -x /usr/local/bin/whisper-cli ]]; then
  cat <<'EOF'
WARNUNG: whisper-cli nicht gefunden.
Installiere bitte:
  brew install whisper-cpp
EOF
fi

"$PROJECT_DIR/.venv/bin/python" "$PROJECT_DIR/scripts/generate_icon.py" "$PROJECT_DIR/assets/voiceClip.icns"

rm -rf "$PROJECT_DIR/build" "$PROJECT_DIR/dist"
"$PROJECT_DIR/.venv/bin/pyinstaller" --noconfirm --clean "$PROJECT_DIR/voiceClip.spec"

if [[ -d "$DIST_APP_ALT" ]]; then
  DIST_APP="$DIST_APP_ALT"
fi

if [[ ! -d "$DIST_APP" ]]; then
  echo "Build fehlgeschlagen: keine App im dist-Verzeichnis gefunden."
  exit 1
fi

pkill -f '/Applications/voiceClip.app/Contents/MacOS/voiceClip' >/dev/null 2>&1 || true
pkill -f '/Applications/voiceClip.app/Contents/MacOS/applet' >/dev/null 2>&1 || true

rm -rf "$TARGET_APP"
cp -R "$DIST_APP" "$TARGET_APP"

xattr -dr com.apple.quarantine "$TARGET_APP" >/dev/null 2>&1 || true

mkdir -p "$HOME/Applications"
rm -rf "$USER_TARGET_APP"
cp -R "$TARGET_APP" "$USER_TARGET_APP"
xattr -dr com.apple.quarantine "$USER_TARGET_APP" >/dev/null 2>&1 || true

cat <<EOF
Build fertig.
App: $TARGET_APP
App: $USER_TARGET_APP

Naechster Schritt (lokales Signieren):
  "$PROJECT_DIR/scripts/sign_local.sh"
EOF
