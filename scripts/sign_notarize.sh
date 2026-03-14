#!/usr/bin/env bash
set -euo pipefail

APP_PATH="${1:-/Applications/voiceClip.app}"
IDENTITY="${2:-}"
NOTARY_PROFILE="${3:-}"

if [[ ! -d "$APP_PATH" ]]; then
  echo "App nicht gefunden: $APP_PATH"
  exit 1
fi

if [[ -z "$IDENTITY" ]]; then
  cat <<'EOF'
Fehlende Signier-Identity.
Beispiel:
  ./scripts/sign_notarize.sh /Applications/voiceClip.app "Developer ID Application: Dein Name (TEAMID)" my-notary-profile
EOF
  exit 1
fi

if [[ -z "$NOTARY_PROFILE" ]]; then
  cat <<'EOF'
Fehlendes notarytool-Profil.
Lege zuerst ein Keychain-Profil an, z.B.:
  xcrun notarytool store-credentials my-notary-profile --apple-id <APPLE_ID> --team-id <TEAM_ID> --password <APP_SPECIFIC_PASSWORD>
EOF
  exit 1
fi

ZIP_PATH="/tmp/voiceClip-notary.zip"

codesign --force --deep --options runtime --timestamp --sign "$IDENTITY" "$APP_PATH"
codesign --verify --deep --strict --verbose=2 "$APP_PATH"

rm -f "$ZIP_PATH"
ditto -c -k --keepParent "$APP_PATH" "$ZIP_PATH"

xcrun notarytool submit "$ZIP_PATH" --keychain-profile "$NOTARY_PROFILE" --wait
xcrun stapler staple "$APP_PATH"

spctl -a -vv "$APP_PATH"

echo ""
echo "Notarisierung abgeschlossen: $APP_PATH"
