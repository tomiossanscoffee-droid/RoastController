rem Roast Studio - 残骸サーバー後始末(共通処理・Windows用)
rem start_app.bat / start_app_https.bat から `call` される。
rem 呼び出し側が `setlocal enabledelayedexpansion` 済みであることが前提。
rem ポート8765を握っているプロセスが、このアプリ自身の前回起動分(python)
rem であれば終了させる。無関係な別プロセスの場合は警告するだけで何もしない。

set STALE_PID=
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":8765 " ^| findstr "LISTENING"') do set STALE_PID=%%P
if defined STALE_PID (
    tasklist /fi "PID eq !STALE_PID!" | findstr /i "python" >nul
    if not errorlevel 1 (
        echo 前回のサーバー(PID !STALE_PID!)が残っていたため終了します...
        taskkill /PID !STALE_PID! /F >nul 2>&1
        timeout /t 2 /nobreak >nul
    ) else (
        echo 警告: ポート8765を別のプロセス(PID !STALE_PID!)が使用しています。終了してから再実行してください。
    )
)
