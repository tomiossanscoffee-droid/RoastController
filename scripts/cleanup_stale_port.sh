# Roast Studio - 残骸サーバー後始末(共通処理・Mac用)
# start_app.command / start_app_https.command から `source` される。
# ポート8765を握っているプロセスが、このアプリ自身の前回起動分(uvicorn/python)
# であれば終了させる。無関係な別プロセスの場合は警告するだけで何もしない。

STALE_PIDS=$(lsof -nP -iTCP:8765 -sTCP:LISTEN -t 2>/dev/null)
if [ -n "$STALE_PIDS" ]; then
    for pid in $STALE_PIDS; do
        if ps -p "$pid" -o command= | grep -qE "uvicorn|app.server|[Pp]ython"; then
            echo "前回のサーバー(PID $pid)が残っていたため終了します..."
            kill "$pid" 2>/dev/null
        else
            echo "警告: ポート8765を別のプロセス(PID $pid)が使用しています。終了してから再実行してください。"
        fi
    done
    sleep 2
fi
