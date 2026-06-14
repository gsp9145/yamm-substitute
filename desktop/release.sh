#!/usr/bin/env bash
# Cut a new CreatorCRM Mac release + auto-update manifest.
#
# Steps before running:
#   1. Bump "version" in src-tauri/tauri.conf.json AND "version" in src-tauri/Cargo.toml (keep in sync).
#   2. Make sure the updater signing key exists at desktop/.tauri/creatorcrm-updater.key
#      (gitignored — back it up somewhere safe; losing it breaks updates for installed apps).
#
# Then: ./release.sh
#
# It rebuilds the backend + signed app, generates latest.json, and uploads the
# DMG (stable name), the updater artifact, and latest.json to the `mac-beta`
# release — so the download URL and updater endpoint stay constant across versions.
set -euo pipefail
cd "$(dirname "$0")"

REPO="gsp9145/yamm-substitute"
TAG="mac-beta"
VERSION=$(sed -n 's/.*"version": *"\([^"]*\)".*/\1/p' src-tauri/tauri.conf.json | head -1)
echo "▶ Releasing CreatorCRM $VERSION"

# 1. Rebuild the Python backend sidecar
cd ..
rm -rf desktop/backend-dist
pyinstaller --noconfirm --clean --onedir --name creatorcrm-backend \
  --distpath desktop/backend-dist --workpath /tmp/pyi-build --specpath /tmp/pyi-build \
  --add-data "$PWD/templates:templates" --add-data "$PWD/static:static" --add-data "$PWD/landing:landing" \
  --hidden-import apscheduler.schedulers.background --hidden-import apscheduler.triggers.interval \
  --hidden-import requests --hidden-import oauth_config \
  app.py >/dev/null
cd desktop

# 2. Build the signed app + updater artifacts (.app.tar.gz + .sig)
export TAURI_SIGNING_PRIVATE_KEY="$(cat .tauri/creatorcrm-updater.key)"
export TAURI_SIGNING_PRIVATE_KEY_PASSWORD=""
npx tauri build

B="src-tauri/target/release/bundle"
SIG="$(cat "$B/macos/CreatorCRM.app.tar.gz.sig")"
PUBDATE="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

# 3. Build the updater manifest (Tauri "dynamic" format)
cat > /tmp/latest.json <<JSON
{
  "version": "$VERSION",
  "notes": "CreatorCRM $VERSION",
  "pub_date": "$PUBDATE",
  "platforms": {
    "darwin-aarch64": {
      "signature": "$SIG",
      "url": "https://github.com/$REPO/releases/download/$TAG/CreatorCRM.app.tar.gz"
    }
  }
}
JSON

# 4. Stable-named DMG for the website download button
cp "$B/dmg/CreatorCRM_${VERSION}_aarch64.dmg" /tmp/CreatorCRM-arm64.dmg

# 5. Upload everything to the fixed tag (clobber keeps URLs stable)
gh release upload "$TAG" --repo "$REPO" --clobber \
  /tmp/CreatorCRM-arm64.dmg \
  "$B/macos/CreatorCRM.app.tar.gz" \
  /tmp/latest.json

echo "✅ Released $VERSION — installed apps will auto-update on next launch."
