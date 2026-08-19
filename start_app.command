#!/bin/bash
# ============================================================
# Roast Studio - 起動スクリプト(Mac用)
# ------------------------------------------------------------
# このファイルをFinderでダブルクリックすると、
#   1. venvを有効化
#   2. サーバーを起動(このターミナルウィンドウにログが表示され続けます)
#   3. 2秒待ってからブラウザで画面を自動的に開く
# を1アクションで行います。
#
# 同じWi-Fiに繋がっているAndroid端末からも、下に表示されるURL
# (http://<Macのアドレス>:8765)をブラウザで開けば、同じ画面を
# 同じサーバーに対してそのまま操作できます(Mac側も従来通りlocalhostで使えます)。
#
# 初回だけ、ターミナルで以下を実行して実行権限を与えてください:
#   chmod +x start_app.command
#
# GitHub等からこのフォルダをダウンロード/git cloneしただけの、Python環境が
# まだ無い端末でも、このファイルをダブルクリックするだけでvenv作成・依存
# パッケージのインストールまで自動で行います(初回だけ数分かかります)。
#
# サーバーを止めたい場合は、このターミナルウィンドウで Ctrl+C を押してください。
# ============================================================

cd "$(dirname "$0")"

# 他の処理より先に。Rosetta起動ならarm64で起動し直す(理由はスクリプト内のコメント参照)。
source "scripts/ensure_native_arch.sh"

source "scripts/setup_venv.sh"

# 同じWi-Fi内のLAN IPアドレスを調べる(Wi-Fi=en0が一般的だが、有線等の場合は
# 環境に応じて読み替えてください。取得できない場合は空欄のまま案内をスキップします)
LAN_IP=$(ipconfig getifaddr en0 2>/dev/null)

echo "サーバーを起動しています... (このウィンドウを閉じるとサーバーも止まります)"
echo "終了するには Ctrl+C を押してください。"
echo ""
echo "Macから: http://localhost:8765"
if [ -n "$LAN_IP" ]; then
    echo "同じWi-FiのAndroid端末から: http://$LAN_IP:8765"
else
    echo "(同じWi-Fi内の他端末からアクセスしたい場合は、システム設定 > Wi-Fi > 詳細 で"
    echo " このMacのIPアドレスを確認し、http://そのIPアドレス:8765 を開いてください)"
fi
echo ""
echo "初回、macOSから「着信接続を許可しますか」という確認が出た場合は「許可」を選んでください。"
echo ""

source "scripts/cleanup_stale_port.sh"

# サーバーが実際に応答してからブラウザを開く(Mac側はlocalhostのまま)。
# 固定秒数で待つと、起動が間に合わない時にブラウザだけ先に開いてしまい
# 「サーバに接続できません」になるため、応答を確認してから開く。
source "scripts/open_when_ready.sh"
open_browser_when_ready "http://localhost:8765"

# --host 0.0.0.0 で、同じWi-Fi内の他端末からもアクセスできるようにする
python3 -m uvicorn app.server:app --host 0.0.0.0 --port 8765

# ---- 終了時の後始末 ----
# サーバーが止まったら(アプリの「終了」ボタン・Ctrl+Cのどちらでも)、このスクリプトを
# 開いているTerminalウィンドウだけを閉じる(他のウィンドウ・タブはそのまま)。
# (この通常版はTailscaleを使わないため、Tailscaleには触れない)
echo ""
echo "終了処理: このウィンドウを閉じます..."
source "scripts/close_own_terminal_window.sh"
close_own_terminal_window
