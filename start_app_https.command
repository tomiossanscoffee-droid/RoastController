#!/bin/bash
# ============================================================
# Roast Studio - HTTPS起動スクリプト(Tailscale経由)
# ------------------------------------------------------------
# 画面ロック中でも届くプッシュ通知(Web Push)を使うには、HTTPS
# (secure context)でのアクセスが必須。このスクリプトはTailscaleの
# 証明書機能(tailscale cert)を使ってこのMacのHTTPS証明書を発行し、
# uvicornをHTTPSで起動する。
#
# 事前準備: 通常は不要です。Tailscale未導入・未ログインの場合、このスクリプトが
# 検出してインストール(Homebrewがあれば)・ログイン(ブラウザでの認証のみ手動)を
# 案内します。あらかじめ済ませておきたい場合の手順:
#   1. Tailscaleをインストール: brew install --cask tailscale
#   2. tailscale up でこのMacをtailnetに参加させる(ブラウザでログイン)
#   3. スマホにTailscaleアプリを入れ、同じtailnetにログイン
#   4. https://login.tailscale.com/admin/dns で
#      「HTTPS Certificates」を有効化する
#
# 上記が済んでいれば、このファイルをダブルクリックするだけで
# 証明書の発行(初回)・更新(2回目以降)とサーバー起動を行います。
#
# 初回だけ、ターミナルで以下を実行して実行権限を与えてください:
#   chmod +x start_app_https.command
# ============================================================

cd "$(dirname "$0")"

# 他の処理より先に。Rosetta起動ならarm64で起動し直す(理由はスクリプト内のコメント参照)。
source "scripts/ensure_native_arch.sh"

source "scripts/setup_venv.sh"

# ---- Tailscaleの確認・未導入なら導入を提案 ----
if ! command -v tailscale >/dev/null 2>&1; then
    echo "Tailscaleが見つかりません(スマホからのHTTPSアクセスに使います)。"
    if command -v brew >/dev/null 2>&1; then
        read -p "Homebrewでインストールしますか? [y/N]: " ans
        if [[ "$ans" =~ ^[Yy]$ ]]; then
            echo "Tailscaleをインストールしています..."
            brew install --cask tailscale
        fi
    fi
    if ! command -v tailscale >/dev/null 2>&1; then
        echo ""
        echo "エラー: tailscale コマンドが見つかりません。"
        echo "手動でインストールしてください: brew install --cask tailscale"
        echo "(またはダウンロードページから: https://tailscale.com/download/mac )"
        open "https://tailscale.com/download/mac" 2>/dev/null
        read -p "何かキーを押すと閉じます..."
        exit 1
    fi
fi

# ---- Tailscaleが「本当に繋がっている」ことを確認する ----
# 名前が取れるだけでは不十分。切断中(Stopped)でもログイン済みなら名前は返るため、
# ここを名前だけで判断していると、Tailscaleが切れたままサーバーが起動してしまい
# スマホから繋がらない(手でオフ→オンすると繋がる)。詳細はスクリプト内のコメント参照。
source "scripts/wait_for_tailscale.sh"

if ! ensure_tailscale_up; then
    echo ""
    echo "エラー: Tailscaleが接続状態になりませんでした。"
    echo "  ・Tailscaleアプリでログイン・接続(オン)になっているか"
    echo "  ・「tailscale status」で BackendState が Running か"
    echo "を確認してから、もう一度起動してください。"
    read -p "何かキーを押すと閉じます..."
    exit 1
fi

HOSTNAME=$(tailscale_dns_name)
if [ -z "$HOSTNAME" ]; then
    echo "エラー: TailscaleのMagicDNS名を取得できませんでした。"
    echo "管理コンソールでMagicDNSが有効か確認してください: https://login.tailscale.com/admin/dns"
    read -p "何かキーを押すと閉じます..."
    exit 1
fi

mkdir -p certs

echo "証明書を発行・更新しています ($HOSTNAME) ..."
tailscale cert --cert-file=certs/ts.crt --key-file=certs/ts.key "$HOSTNAME"
if [ $? -ne 0 ]; then
    echo "エラー: 証明書の発行に失敗しました。"
    echo "Tailscale管理コンソールで「HTTPS Certificates」が有効になっているか確認してください:"
    echo "  https://login.tailscale.com/admin/dns"
    read -p "何かキーを押すと閉じます..."
    exit 1
fi

echo ""
echo "サーバーを起動しています... (このウィンドウを閉じるとサーバーも止まります)"
echo "終了するには Ctrl+C を押してください。"
echo ""
echo "スマホ(同じtailnetに参加済み)から: https://$HOSTNAME:8765/mobile"
echo "Macから: https://$HOSTNAME:8765"
echo ""

source "scripts/cleanup_stale_port.sh"

# サーバーが実際に応答してからブラウザを開く(理由はopen_when_ready.sh参照)。
# HTTPSはTLSの初期化がある分、平文HTTPより起動が遅くなりやすい。
source "scripts/open_when_ready.sh"
open_browser_when_ready "https://$HOSTNAME:8765"

python3 -m uvicorn app.server:app --host 0.0.0.0 --port 8765 \
    --ssl-keyfile certs/ts.key --ssl-certfile certs/ts.crt

# ---- 終了時の後始末 ----
# サーバーが止まったら(アプリの「終了」ボタン・Ctrl+Cのどちらでも、uvicornが
# 終了するとここに進む)、この起動に使ったTailscaleを終了し、このスクリプトを
# 開いているTerminalウィンドウだけを閉じる。
# 注意: Tailscaleはこのスクリプト以外の用途で使っていても切断・終了される。
echo ""
echo "終了処理: Tailscaleを切断・終了し、このウィンドウを閉じます..."
tailscale down >/dev/null 2>&1
osascript -e 'quit app "Tailscale"' >/dev/null 2>&1

source "scripts/close_own_terminal_window.sh"
close_own_terminal_window
