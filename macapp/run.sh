#!/bin/bash
# ============================================================
# Roast Studio - macOSアプリを日常的に起動するためのスクリプト
# ------------------------------------------------------------
# `swift run` は開発中の簡易確認用で、SwiftPMが自動生成する別物の
# .app(.build/配下)を使うため、AppIcon.icnsやNSBluetoothAlwaysUsageDescription
# などInfo.plistの独自キーが一切反映されない(SwiftPM側の制約)。
# 日常の起動には、常にこちらを使うこと:
#   ./run.sh
# build_app.sh で正規の.app(build/RoastStudio.app)を組み立ててから開く。
# ============================================================
set -euo pipefail
cd "$(dirname "$0")"

./build_app.sh
open build/RoastStudio.app
