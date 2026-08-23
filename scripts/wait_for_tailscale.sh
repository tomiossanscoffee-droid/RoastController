#!/bin/bash
# ============================================================
# Roast Studio
# scripts/wait_for_tailscale.sh
# ------------------------------------------------------------
# Tailscaleが「本当に繋がっている」状態になるまで待つ。
#
# ■ なぜ必要か(2026-08、スマホから繋がらない事象の対策)
# 従来 start_app_https.command は、MagicDNS名(Self.DNSName)が取れたかどうかだけで
# 「Tailscaleは使える」と判断していた。ところが DNSName は、ログイン済みでさえあれば
# 切断中(BackendState=Stopped)でも返ってくる。
# このスクリプト自身が終了時に `tailscale down` を実行するため、次に起動したときは
# たいてい Stopped の状態から始まる。その状態でも
#   ・DNSNameは取れるので `tailscale up` の分岐に入らない
#   ・証明書はキャッシュから発行できてしまう
# ため、Tailscaleが切断されたままサーバーだけが起動していた。スマホからは当然
# 繋がらず、手でTailscaleをオフ→オンすると繋がる、という症状になっていた。
# 接続中(Starting)のうちに起動してしまう場合も同じ見え方になる。
#
# そこで BackendState が Running で、かつTailscale IPが割り当てられていることを
# 確認し、そうでなければ tailscale up してから待つ。
# ============================================================

# BackendState を返す(取れなければ空)
tailscale_backend_state() {
    tailscale status --json 2>/dev/null | python3 -c "
import json,sys
try: print(json.load(sys.stdin).get('BackendState',''))
except Exception: pass
" 2>/dev/null
}

# MagicDNS名を返す(末尾のドットは除く。取れなければ空)
tailscale_dns_name() {
    tailscale status --json 2>/dev/null | python3 -c "
import json,sys
try: print((json.load(sys.stdin).get('Self') or {}).get('DNSName','').rstrip('.'))
except Exception: pass
" 2>/dev/null
}

# 実際に通信できる状態か(Running かつ Tailscale IP が付いている)
tailscale_is_connected() {
    tailscale status --json 2>/dev/null | python3 -c "
import json,sys
try:
    d = json.load(sys.stdin)
    ok = d.get('BackendState') == 'Running' and bool((d.get('Self') or {}).get('TailscaleIPs'))
    sys.exit(0 if ok else 1)
except Exception:
    sys.exit(1)
" 2>/dev/null
}

# 接続が完了するまで待つ。第1引数は最大待ち秒数(既定30)。
wait_for_tailscale() {
    local limit="${1:-30}"
    local waited=0
    while ! tailscale_is_connected; do
        if [ "$waited" -ge "$limit" ]; then
            return 1
        fi
        if [ "$waited" -eq 0 ]; then
            echo -n "Tailscaleの接続を待っています"
        fi
        echo -n "."
        sleep 1
        waited=$((waited + 1))
    done
    if [ "$waited" -gt 0 ]; then
        echo " 接続しました(${waited}秒)"
    fi
    return 0
}

# 接続していなければ tailscale up してから待つ。成功なら0。
ensure_tailscale_up() {
    if tailscale_is_connected; then
        return 0
    fi
    local state
    state=$(tailscale_backend_state)
    case "$state" in
        Running|Starting)
            # 接続処理の途中。IPが付くまで待てばよい
            echo "Tailscaleを接続しています(状態: ${state:-不明})..." ;;
        *)
            # Stopped(切断中)・NeedsLogin(未ログイン)・NoState など。up で立ち上げる。
            # ログインが必要な場合はブラウザが開く(ログイン済みならそのまま繋がる)。
            echo "Tailscaleが切断されています(状態: ${state:-不明})。接続します..."
            tailscale up ;;
    esac
    wait_for_tailscale 30
}
