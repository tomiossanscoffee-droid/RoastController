# Roast Studio - サーバーが応答してからブラウザを開く(共通処理・Mac用)
# start_app.command / start_app_https.command から `source` される。
#
# 以前は `(sleep 2 && open URL) &` と固定2秒待ちで開いていたため、サーバーの
# 起動が2秒に間に合わないとブラウザだけ先に開いてしまい、Safariの
# 「サーバに接続できません」が表示されていた。2秒で足りるかは、その時の
# ディスクキャッシュの状態・初回起動かどうか・HTTPS(TLSの初期化)かどうかで
# 変わるため、時間で待つのをやめ、実際に応答が返るまで待ってから開く。
#
# 使い方(uvicornを起動する直前に呼ぶ):
#   open_browser_when_ready "http://localhost:8765"

open_browser_when_ready() {
    local url="$1"
    local timeout_sec="${2:-90}"
    (
        local deadline=$(( $(date +%s) + timeout_sec ))
        while [ "$(date +%s)" -lt "$deadline" ]; do
            # -k: HTTPS起動直後で証明書の検証に失敗する場合でも「応答はある」と判定する
            #     (ここで見たいのはサーバーが待ち受けを始めたかどうかだけ)
            if curl -sk -o /dev/null --max-time 2 "$url" 2>/dev/null; then
                open "$url"
                exit 0
            fi
            sleep 0.2
        done
        echo ""
        echo "警告: サーバーの起動確認がタイムアウトしました(${timeout_sec}秒)。"
        echo "上のログにエラーが出ていないか確認し、必要なら次のURLを手動で開いてください:"
        echo "  $url"
    ) &
}
