#!/bin/bash
# ============================================================
# Roast Studio - macOS .app ビルドスクリプト
# ------------------------------------------------------------
# Sources/RoastStudio を `swift build -c release` でビルドし、
# Info.plist と組み合わせて build/RoastStudio.app を作る。
# 署名はアドホック(codesign --sign -)。このMac上でBluetooth/通知の
# 権限プロンプトが正しく機能するために必要な最低限の署名で、
# Developer ID配布(README「残タスク」参照)とは別の話。
#
# 使い方: macapp/build_app.sh
# 実行後: open build/RoastStudio.app
# ============================================================
set -euo pipefail
cd "$(dirname "$0")"

APP_NAME="RoastStudio"
BUILD_DIR="build"
APP_BUNDLE="$BUILD_DIR/$APP_NAME.app"

echo "==> swift build -c release"
swift build -c release
BIN_PATH="$(swift build -c release --show-bin-path)/$APP_NAME"

if [ ! -f "$BIN_PATH" ]; then
  echo "エラー: ビルド成果物が見つかりません: $BIN_PATH" >&2
  exit 1
fi

echo "==> .appバンドルを組み立て中: $APP_BUNDLE"
rm -rf "$APP_BUNDLE"
mkdir -p "$APP_BUNDLE/Contents/MacOS"
mkdir -p "$APP_BUNDLE/Contents/Resources"
cp "$BIN_PATH" "$APP_BUNDLE/Contents/MacOS/$APP_NAME"
cp Info.plist "$APP_BUNDLE/Contents/Info.plist"
if [ -f "AppIcon.icns" ]; then
  cp AppIcon.icns "$APP_BUNDLE/Contents/Resources/AppIcon.icns"
fi

echo "==> アドホック署名中"
codesign --force --deep --sign - "$APP_BUNDLE"

echo "==> 完了: $APP_BUNDLE"
codesign -dv "$APP_BUNDLE" 2>&1 | sed 's/^/    /'
echo ""
echo "起動するには: open \"$APP_BUNDLE\""
