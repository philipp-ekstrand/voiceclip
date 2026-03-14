#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "Dieses Projekt ist nur fuer macOS gedacht."
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 fehlt. Bitte zuerst Python 3 installieren."
  exit 1
fi

if ! command -v brew >/dev/null 2>&1; then
  cat <<'EOF'
Homebrew wurde nicht gefunden.
Bitte installiere Homebrew und fuehre setup.sh erneut aus.
EOF
  exit 1
fi

echo "Installiere native Abhaengigkeiten via Homebrew ..."
brew list portaudio >/dev/null 2>&1 || brew install portaudio
brew list whisper-cpp >/dev/null 2>&1 || brew install whisper-cpp

echo "Erstelle Python venv ..."
python3 -m venv .venv
source .venv/bin/activate

echo "Installiere Python Pakete ..."
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

cat <<'EOF'
Setup fertig.

Build der echten App:
  ./scripts/build_app.sh

Lokales Signieren:
  ./scripts/sign_local.sh

Danach Start per Doppelklick:
  /Applications/voiceClip.app
EOF
