@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
rem ============================================================
rem Roast Studio - 起動スクリプト(Windows用)
rem ------------------------------------------------------------
rem このファイルをダブルクリックすると、
rem   1. venvを有効化
rem   2. サーバーを起動(このコマンドプロンプトにログが表示され続けます)
rem   3. 2秒待ってからブラウザで画面を自動的に開く
rem を1アクションで行います。
rem
rem 同じWi-Fiに繋がっているAndroid/iPhone端末からも、下に表示されるURL
rem (http://<このPCのアドレス>:8765)をブラウザで開けば、同じ画面を
rem 同じサーバーに対してそのまま操作できます(このPC自身も今まで通り
rem localhostで使えます)。
rem
rem サーバーを止めたい場合は、このウィンドウで Ctrl+C を押してください。
rem
rem GitHub等からこのフォルダをダウンロード/git cloneしただけの、Python環境が
rem まだ無いPCでも、このファイルをダブルクリックするだけでvenv作成・依存
rem パッケージのインストールまで自動で行います(初回だけ数分かかります)。
rem ============================================================

cd /d "%~dp0"

call scripts\setup_venv.bat
if errorlevel 1 exit /b 1

rem 同じWi-Fi内のLAN IPv4アドレスを調べる(複数のネットワークアダプタがある場合、
rem 想定と違うアドレスが表示されることがあります。その場合は`ipconfig`で
rem 確認してください)
set LAN_IP=
for /f "usebackq delims=" %%I in (`powershell -NoProfile -Command "(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object { $_.IPAddress -notlike '169.254.*' -and $_.IPAddress -ne '127.0.0.1' -and $_.PrefixOrigin -ne 'WellKnown' } | Select-Object -First 1 -ExpandProperty IPAddress)"`) do set LAN_IP=%%I

echo サーバーを起動しています... (このウィンドウを閉じるとサーバーも止まります)
echo 終了するには Ctrl+C を押してください。
echo.
echo このPCから: http://localhost:8765
if defined LAN_IP (
    echo 同じWi-FiのAndroid/iPhone端末から: http://!LAN_IP!:8765
) else (
    echo (同じWi-Fi内の他端末からアクセスしたい場合は、コマンドプロンプトで`ipconfig`を実行し、
    echo  「IPv4 アドレス」を確認して http://そのIPアドレス:8765 を開いてください)
)
echo.
echo 初回、Windows セキュリティの「Windows Defender ファイアウォールでブロック」という
echo 確認が出た場合は「アクセスを許可する」を選んでください(プライベートネットワークで可)。
echo.

call scripts\cleanup_stale_port.bat

rem サーバー起動を少し待ってから、ブラウザを自動で開く(このPC自身はlocalhostのまま)
start "" powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 2; Start-Process 'http://localhost:8765'"

rem --host 0.0.0.0 で、同じWi-Fi内の他端末からもアクセスできるようにする
python -m uvicorn app.server:app --host 0.0.0.0 --port 8765
