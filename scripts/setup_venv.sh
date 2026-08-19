# Roast Studio - venv自動セットアップ(共通処理・Mac用)
# start_app.command / start_app_https.command から `source` される。
# 呼び出し側が先にリポジトリ直下へ `cd` 済みであることが前提
# (このファイル自体はここで`exit`すると呼び出し元のシェルごと終了するため、
#  独立実行ではなく必ずsourceで使うこと)。

# 指定したpython3が、このMacで問題なく使えるかを確認する:
#   (1) このMacの実際のCPUでネイティブ動作するか
#       (Apple SiliconでIntel版のみのPythonだとRosetta経由になり、
#        「Intelプロセッサ用アプリのサポートは終了します」という警告が出る。
#        Intel Macでは常に該当なし)
#   (2) TLS1.3をサーバーとして提供できるモダンなSSLライブラリか
#       (macOS標準の/usr/bin/python3は、Intel・Apple Silicon問わずmacOS付属の
#        古いLibreSSLにリンクされており、TLS1.3のサーバー終端ができず、
#        スマートフォン等からHTTPS接続できなくなることを実機で確認済み。
#        CPUの種類に関係なく確認する)
python_is_usable() {
    local bin="$1"
    [ -x "$bin" ] || return 1
    # 2026-07修正: 以前は `uname -m` でApple Siliconか判定していたが、Rosetta下では
    # `uname -m` が x86_64 を返すため、この判定ごとすり抜けてしまっていた。
    # 実機の判定には hw.optional.arm64 を使う(ensure_native_arch.sh で定義)。
    if command -v roast_is_apple_silicon >/dev/null 2>&1 && roast_is_apple_silicon \
       && ! file "$bin" 2>/dev/null | grep -q "arm64"; then
        return 1
    fi
    "$bin" -c "import ssl,sys; sys.exit(0 if 'LibreSSL' not in ssl.OPENSSL_VERSION else 1)" 2>/dev/null
}

if [ ! -d "venv" ]; then
    echo "初回起動のセットアップを行います(数分かかることがあります)..."
    echo ""
    if ! command -v python3 >/dev/null 2>&1; then
        echo "エラー: python3 が見つかりません。"
        echo "先にPython 3をインストールしてください(python.org、またはHomebrewの"
        echo "\`brew install python3\`)。インストール後、このファイルをもう一度"
        echo "ダブルクリックしてください。"
        open "https://www.python.org/downloads/" 2>/dev/null
        read -p "何かキーを押すと閉じます..."
        exit 1
    fi

    PYTHON_BIN=python3
    if ! python_is_usable "$(command -v python3)"; then
        echo "検出したpython3はこのMacに適していません"
        echo "(Apple SiliconでIntel版のみ、または古いSSLライブラリのためTLS1.3の"
        echo "サーバー機能が使えません)。他のPythonを探しています..."
        if python_is_usable /usr/local/bin/python3; then
            PYTHON_BIN=/usr/local/bin/python3
            echo "python.org版のPythonを使用します: $PYTHON_BIN"
        elif python_is_usable /opt/homebrew/bin/python3; then
            PYTHON_BIN=/opt/homebrew/bin/python3
            echo "Apple Silicon版のPythonを使用します: $PYTHON_BIN"
        else
            echo "適したPythonが見つかりませんでした。"
            echo "python.org (https://www.python.org/downloads/macos/) から"
            echo "「macOS 64-bit universal2 installer」をインストールしてから"
            echo "もう一度お試しください。"
            open "https://www.python.org/downloads/macos/" 2>/dev/null
            read -p "何かキーを押すと閉じます..."
            exit 1
        fi
    fi

    echo "venv(Python仮想環境)を作成しています..."
    if ! "$PYTHON_BIN" -m venv venv; then
        echo "エラー: venvの作成に失敗しました。"
        read -p "何かキーを押すと閉じます..."
        exit 1
    fi
fi

# 既にあるvenvが上記の条件に合わない場合も、作り直しを促す(自動では削除しない)。
if [ -f "venv/bin/python3" ] && ! python_is_usable venv/bin/python3; then
    echo "警告: venv内のPythonが、このMacに適していません"
    echo "(Apple SiliconでIntel版のみ、または古いSSLライブラリのためTLS1.3の"
    echo "サーバー機能が使えず、スマートフォン等からHTTPSで接続できない場合があります)。"
    echo "解消するには、一度終了してから venv フォルダを削除し、このファイルを"
    echo "もう一度実行してください(python.org版などで自動的に作り直されます)。"
fi

source venv/bin/activate

# 依存パッケージが揃っているか確認し、足りなければ自動で入れる。
# (別端末での機能追加でrequirements.txtに新しいパッケージが増えても、
#  既存のvenvのまま起動できず「動かない」状態になるのを防ぐ。
#  uvicorn・pywebpush・segnoが入っていれば最新とみなしてスキップする)
if ! python3 -c "import uvicorn, pywebpush, segno" >/dev/null 2>&1; then
    echo "必要なパッケージをインストールしています(初回・更新時のみ、数分かかることがあります)..."
    pip install -q --upgrade pip
    if ! pip install -q -r requirements.txt; then
        echo "エラー: パッケージのインストールに失敗しました。"
        read -p "何かキーを押すと閉じます..."
        exit 1
    fi
fi

# 2026-07追加: venv内のパッケージが、いま動いているPythonと別のCPU向けに
# インストールされていると、pipは「インストール済み」と判断して入れ直さないため、
# 上のpip installを通っても import は失敗し続ける。その場合、uvicorn起動時に
# 長いTracebackだけが出て原因が分かりにくいので、ここで先に検出して案内する。
# (よくある原因: 以前ターミナルをRosettaで起動していた時に作られたvenv)
VENV_IMPORT_ERROR=$(python3 -c "import fastapi, uvicorn" 2>&1)
if [ $? -ne 0 ]; then
    echo ""
    if echo "$VENV_IMPORT_ERROR" | grep -q "incompatible architecture"; then
        echo "エラー: venv内のパッケージが、このMacのCPU向けではありません。"
        echo "(以前Rosetta(Intel互換)で起動した際に作られたvenvが残っている等が原因です)"
        echo ""
        echo "次のコマンドでvenvを作り直すと解消します:"
        echo "  cd \"$(pwd)\" && rm -rf venv"
        echo "  そのあと、このファイルをもう一度ダブルクリックしてください。"
    else
        echo "エラー: 必要なパッケージを読み込めませんでした。"
        echo "$VENV_IMPORT_ERROR" | tail -3
    fi
    echo ""
    read -p "何かキーを押すと閉じます..."
    exit 1
fi

if [ ! -f "nhm.sqlite" ]; then
    echo "警告: nhm.sqlite がリポジトリ直下に見つかりません。"
    echo "起動はしますが、プリセット一覧は表示されません。"
fi
