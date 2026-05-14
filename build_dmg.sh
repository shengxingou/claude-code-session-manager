#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DIST_DIR="$SCRIPT_DIR/dist"
BUILD_DIR="$SCRIPT_DIR/build"
STAGE_DIR="$DIST_DIR/dmg-stage"
APP_NAME="ccSession"
APP_BUNDLE="$DIST_DIR/${APP_NAME}.app"
RW_DMG="$DIST_DIR/${APP_NAME}-temp.dmg"
FINAL_DMG="$DIST_DIR/${APP_NAME}-installer.dmg"
SOURCE_APP="$HOME/Desktop/ccSession.app"
ICON_SOURCE="$SOURCE_APP/Contents/Resources/applet.icns"
VENV_PYTHON="$SCRIPT_DIR/.venv-app/bin/python"
PYINSTALLER="$SCRIPT_DIR/.venv-app/bin/pyinstaller"
VOLUME_NAME="Install ${APP_NAME}"
SPEC_FILE="$SCRIPT_DIR/${APP_NAME}.spec"

mkdir -p "$DIST_DIR"
rm -rf "$APP_BUNDLE" "$STAGE_DIR" "$RW_DMG" "$FINAL_DMG" "$BUILD_DIR" "$SPEC_FILE"
mkdir -p "$STAGE_DIR"

PYI_ARGS=(
  --noconfirm
  --clean
  --windowed
  --name "$APP_NAME"
  --distpath "$DIST_DIR"
  --workpath "$BUILD_DIR"
  --specpath "$SCRIPT_DIR"
  --add-data "$SCRIPT_DIR/static:static"
  --collect-all webview
)

if [[ -f "$ICON_SOURCE" ]]; then
  PYI_ARGS+=(--icon "$ICON_SOURCE")
fi

"$PYINSTALLER" "${PYI_ARGS[@]}" "$SCRIPT_DIR/session-manager.py"

cp -R "$APP_BUNDLE" "$STAGE_DIR/"
ln -s /Applications "$STAGE_DIR/Applications"

hdiutil create \
    -volname "$VOLUME_NAME" \
    -srcfolder "$STAGE_DIR" \
    -ov \
    -format UDRW \
    "$RW_DMG" >/dev/null

hdiutil convert "$RW_DMG" -ov -format UDZO -imagekey zlib-level=9 -o "$FINAL_DMG" >/dev/null
rm -f "$RW_DMG"

echo "$FINAL_DMG"
