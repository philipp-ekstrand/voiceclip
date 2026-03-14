#!/usr/bin/env bash
set -euo pipefail

APP_PATH="${1:-/Applications/voiceClip.app}"

if [[ ! -d "$APP_PATH" ]]; then
  echo "App nicht gefunden: $APP_PATH"
  exit 1
fi

codesign --force --deep --sign - "$APP_PATH"
codesign --verify --deep --strict --verbose=2 "$APP_PATH"

echo ""
echo "Gatekeeper Check:"
spctl -a -vv "$APP_PATH" || true

echo ""
echo "Lokale Signierung abgeschlossen: $APP_PATH"
