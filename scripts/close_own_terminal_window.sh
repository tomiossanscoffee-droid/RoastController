# Roast Studio - 自分を開いているTerminalウィンドウだけを閉じる(共通処理・Mac用)
# ------------------------------------------------------------
# 起動スクリプト(start_app*.command)の最後に呼ぶ。Terminalアプリ全体は終了させず、
# このスクリプトが動いているウィンドウだけを閉じる(他の作業で開いているウィンドウ・
# タブはそのまま残す)。
#
# 仕組み: 自分の端末デバイス名(例: /dev/ttys002)を控えておき、Terminalの各タブが
# 持つ tty プロパティと突き合わせて、一致したウィンドウを閉じる。
#
# スクリプトが動いたままウィンドウを閉じようとすると「実行中のプロセスを終了
# しますか?」の確認ダイアログが出るため、少し待ってから実行する(その頃には
# 呼び出し元のスクリプト自体が終了している)。バックグラウンドの待ち処理が
# 端末に紐付いたままにならないよう、標準入出力はすべて端末から切り離す。
close_own_terminal_window() {
    local my_tty
    my_tty="$(tty 2>/dev/null)"
    # パイプ経由などで端末が特定できない場合は、何もしない(誤って別のウィンドウを
    # 閉じないようにするため)。
    case "$my_tty" in
        /dev/*) ;;
        *) return 0 ;;
    esac

    (
        sleep 1
        osascript <<OSA
tell application "Terminal"
    repeat with w in windows
        repeat with t in tabs of w
            try
                if (tty of t) is "$my_tty" then close w
            end try
        end repeat
    end repeat
end tell
OSA
    ) >/dev/null 2>&1 </dev/null &
}
