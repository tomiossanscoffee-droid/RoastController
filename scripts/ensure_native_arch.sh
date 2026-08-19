# Roast Studio - Apple SiliconでRosetta起動された場合にarm64で起動し直す(共通処理・Mac用)
# start_app.command / start_app_https.command から、他の処理より前に `source` される。
#
# 背景(2026-07):
#   ターミナル.appの「情報を見る」で「Rosettaを使用して開く」がオンになっていると、
#   そこから起動したこのスクリプトも、その子プロセスのpython3も x86_64 で動く。
#   一方 venv 内のパッケージ(pydantic_core等のネイティブ拡張)は arm64 で
#   インストールされているため、起動時に次のエラーで落ちる:
#     ImportError: dlopen(..._pydantic_core...so):
#       (mach-o file, but is an incompatible architecture (have 'arm64', need 'x86_64'))
#
#   厄介なのは、Rosetta下では `uname -m` が x86_64 を返すため、CPUの種類を
#   `uname -m` で見ている判定がすべてすり抜けてしまう点。実機がApple Siliconか
#   どうかは hw.optional.arm64、いま翻訳実行中かどうかは sysctl.proc_translated で
#   判定する(どちらもRosetta下でも正しい値が返る)。
#
#   ユーザーにターミナルの設定変更を求めなくても済むよう、検知したら arm64 で
#   自分自身を起動し直す(ROAST_ARCH_REEXECで二重起動を防ぐ)。

if [ -z "$ROAST_ARCH_REEXEC" ] \
   && [ "$(sysctl -n hw.optional.arm64 2>/dev/null)" = "1" ] \
   && [ "$(sysctl -n sysctl.proc_translated 2>/dev/null)" = "1" ]; then
    echo "Rosetta(Intel互換モード)で起動されたため、Apple Silicon本来のモードで起動し直します..."
    echo "(ターミナル.appの「情報を見る」で「Rosettaを使用して開く」をオフにすると、この処理は不要になります)"
    echo ""
    export ROAST_ARCH_REEXEC=1
    exec arch -arm64 "$0" "$@"
fi

# 実機がApple Siliconかどうか(Rosetta下でも正しく判定できる)。
# setup_venv.sh の python_is_usable() から使う。
roast_is_apple_silicon() {
    [ "$(sysctl -n hw.optional.arm64 2>/dev/null)" = "1" ]
}
