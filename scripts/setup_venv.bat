rem Roast Studio - venv自動セットアップ(共通処理・Windows用)
rem start_app.bat / start_app_https.bat から `call` される。
rem 呼び出し側が先にリポジトリ直下へ `cd` 済みで、
rem `setlocal enabledelayedexpansion` 済みであることが前提。
rem 呼び出し側は `call scripts\setup_venv.bat` の直後に
rem `if errorlevel 1 exit /b 1` でエラーを伝播させること
rem (`call`先の`exit /b`は、呼び出し元まで自動では終了させないため)。

if not exist "venv\Scripts\python.exe" (
    echo 初回起動のセットアップを行います(数分かかることがあります)...
    echo.
    where python >nul 2>&1
    if errorlevel 1 (
        echo エラー: python が見つかりません。
        echo 先にPython 3をインストールしてください(https://www.python.org/downloads/ )。
        echo インストーラの「Add python.exe to PATH」に必ずチェックを入れてください。
        echo インストール後、このファイルをもう一度ダブルクリックしてください。
        start "" "https://www.python.org/downloads/"
        pause
        exit /b 1
    )
    echo venv(Python仮想環境)を作成しています...
    python -m venv venv
    if errorlevel 1 (
        echo エラー: venvの作成に失敗しました。
        pause
        exit /b 1
    )
)

call venv\Scripts\activate.bat

rem 依存パッケージが揃っているか確認し、足りなければ自動で入れる。
rem (別端末での機能追加でrequirements.txtに新しいパッケージが増えても、
rem  既存のvenvのまま起動できず「動かない」状態になるのを防ぐ。
rem  uvicorn・pywebpush・segnoが入っていれば最新とみなしてスキップする)
python -c "import uvicorn, pywebpush, segno" >nul 2>&1
if errorlevel 1 (
    echo 必要なパッケージをインストールしています(初回・更新時のみ、数分かかることがあります)...
    python -m pip install -q --upgrade pip
    pip install -q -r requirements.txt
    if errorlevel 1 (
        echo エラー: パッケージのインストールに失敗しました。
        pause
        exit /b 1
    )
)

if not exist "nhm.sqlite" (
    echo 警告: nhm.sqlite がリポジトリ直下に見つかりません。
    echo 起動はしますが、プリセット一覧は表示されません。
)
