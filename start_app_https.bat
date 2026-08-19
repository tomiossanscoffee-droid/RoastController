@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
rem ============================================================
rem Roast Studio - HTTPS起動スクリプト(Windows用、Tailscale経由)
rem ------------------------------------------------------------
rem 画面ロック中でも届くプッシュ通知(Web Push)を使うには、HTTPS
rem (secure context)でのアクセスが必須。このスクリプトはTailscaleの
rem 証明書機能(tailscale cert)を使ってこのPCのHTTPS証明書を発行し、
rem uvicornをHTTPSで起動する。
rem
rem 事前準備: 通常は不要です。Tailscale未導入・未ログインの場合、このスクリプトが
rem 検出してインストール(wingetがあれば)・ログイン(ブラウザでの認証のみ手動)を
rem 案内します。あらかじめ済ませておきたい場合の手順:
rem   1. Tailscaleをインストール: https://tailscale.com/download/windows
rem   2. tailscale up でこのPCをtailnetに参加させる(ブラウザでログイン)
rem   3. スマホにTailscaleアプリを入れ、同じtailnetにログイン
rem   4. https://login.tailscale.com/admin/dns で
rem      「HTTPS Certificates」を有効化する
rem
rem 上記が済んでいれば、このファイルをダブルクリックするだけで
rem 証明書の発行(初回)・更新(2回目以降)とサーバー起動を行います。
rem ============================================================

cd /d "%~dp0"

call scripts\setup_venv.bat
if errorlevel 1 exit /b 1

rem ---- Tailscaleの実行ファイルを探す(PATHに無ければ既定のインストール先を確認) ----
set TAILSCALE_EXE=tailscale
set TAILSCALE_FOUND=0
where tailscale >nul 2>&1
if not errorlevel 1 set TAILSCALE_FOUND=1
if "!TAILSCALE_FOUND!"=="0" if exist "%ProgramFiles%\Tailscale\tailscale.exe" (
    set "TAILSCALE_EXE=%ProgramFiles%\Tailscale\tailscale.exe"
    set TAILSCALE_FOUND=1
)

if "!TAILSCALE_FOUND!"=="0" (
    echo Tailscaleが見つかりません(スマホからのHTTPSアクセスに使います)。
    where winget >nul 2>&1
    if not errorlevel 1 (
        set /p ANS="wingetでインストールしますか? [y/N]: "
        if /i "!ANS!"=="y" (
            echo Tailscaleをインストールしています...
            winget install -e --id Tailscale.Tailscale
        )
    )
    rem インストール後、もう一度探し直す
    where tailscale >nul 2>&1
    if not errorlevel 1 set TAILSCALE_FOUND=1
    if "!TAILSCALE_FOUND!"=="0" if exist "%ProgramFiles%\Tailscale\tailscale.exe" (
        set "TAILSCALE_EXE=%ProgramFiles%\Tailscale\tailscale.exe"
        set TAILSCALE_FOUND=1
    )
)

if "!TAILSCALE_FOUND!"=="0" (
    echo.
    echo エラー: tailscale コマンドが見つかりません。
    echo 手動でインストールしてください: https://tailscale.com/download/windows
    start "" "https://tailscale.com/download/windows"
    pause
    exit /b 1
)

rem PowerShell 5.1(Windows既定)には三項演算子(?:)が無いため、常に
rem 呼び出し演算子(&)経由で実行する(バレコマンド名・フルパスどちらでも動く)。
set HOSTNAME=
for /f "usebackq delims=" %%H in (`powershell -NoProfile -Command "try { (& '!TAILSCALE_EXE!' status --json | ConvertFrom-Json).Self.DNSName.TrimEnd('.') } catch { '' }"`) do set HOSTNAME=%%H
if not defined HOSTNAME (
    echo Tailscaleにログインしていないため、ログイン手続きを開始します...
    echo (ブラウザが開きます。ログインを完了してからこの画面に戻ってきてください)
    "!TAILSCALE_EXE!" up
    for /f "usebackq delims=" %%H in (`powershell -NoProfile -Command "try { (& '!TAILSCALE_EXE!' status --json | ConvertFrom-Json).Self.DNSName.TrimEnd('.') } catch { '' }"`) do set HOSTNAME=%%H
)
if not defined HOSTNAME (
    echo エラー: TailscaleのMagicDNS名を取得できませんでした。
    echo 「tailscale up」でログイン済みか、確認してください(tailscale status で確認できます)。
    pause
    exit /b 1
)

if not exist "certs" mkdir certs

echo 証明書を発行・更新しています (!HOSTNAME!) ...
"!TAILSCALE_EXE!" cert --cert-file=certs\ts.crt --key-file=certs\ts.key "!HOSTNAME!"
if errorlevel 1 (
    echo エラー: 証明書の発行に失敗しました。
    echo Tailscale管理コンソールで「HTTPS Certificates」が有効になっているか確認してください:
    echo   https://login.tailscale.com/admin/dns
    pause
    exit /b 1
)

echo.
echo サーバーを起動しています... (このウィンドウを閉じるとサーバーも止まります)
echo 終了するには Ctrl+C を押してください。
echo.
echo スマホ(同じtailnetに参加済み)から: https://!HOSTNAME!:8765/mobile
echo このPCから: https://!HOSTNAME!:8765
echo.

call scripts\cleanup_stale_port.bat

start "" powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 2; Start-Process 'https://!HOSTNAME!:8765'"

python -m uvicorn app.server:app --host 0.0.0.0 --port 8765 --ssl-keyfile certs\ts.key --ssl-certfile certs\ts.crt
