@echo off
setlocal enabledelayedexpansion

:: ================================================
:: 0. Shutdown Mode Selection Menu
:: ================================================
:MENU
cls
echo ================================================
echo    Tower of GraphRAG - System Shutdown Process
echo ================================================
echo.
echo Please select a shutdown mode:
echo.
echo   [1] Standard Shutdown (STOP) - For daily use. Suspends containers.
echo   [2] Complete Teardown (DOWN) - For applying config changes. Destroys containers.
echo   [3] Cancel                   - Abort the process.
echo.

set "USER_INPUT="
set /p USER_INPUT="Enter your choice (1, 2, or 3): "

if "%USER_INPUT%"=="3" goto CANCEL
if "%USER_INPUT%"=="2" goto MODE_DOWN
if "%USER_INPUT%"=="1" goto MODE_STOP

goto MENU

:MODE_STOP
set "DOCKER_CMD=stop"
set "MODE_MSG=Suspend (Sleep) Mode"
set "MSG_SUFFIX=Safely suspending system state..."
set "COMPLETE_SUFFIX=System suspended safely. Data and networks preserved."
goto EXECUTE

:MODE_DOWN
set "DOCKER_CMD=down"
set "MODE_MSG=Complete Teardown (Down) Mode"
set "MSG_SUFFIX=Tearing down infrastructure containers..."
set "COMPLETE_SUFFIX=System teardown complete. Ready for configuration reload."
goto EXECUTE

:CANCEL
echo.
echo Process canceled. Closing window.
timeout /t 2 >nul
exit /b

:: ================================================
:: 1. Execution Main Process
:: ================================================
:EXECUTE
set "TITLE_MSG=Tower of GraphRAG - System Shutdown Process"
set "STOPPING_MSG=%MSG_SUFFIX%"
set "STOP_PY=[1/3] Terminating Control Tower, API Servers and Gateways (Python)..."
set "STOP_DOCKER=[2/3] Processing Infrastructure (Docker) using '%DOCKER_CMD%'..."
set "STOP_DIFY=[3/3] Processing Dify Infrastructure (Docker) using '%DOCKER_CMD%'..."
set "COMPLETE_MSG=COMPLETE: %COMPLETE_SUFFIX%"

cls
echo ================================================
echo    %TITLE_MSG%
echo    Execution Mode: %MODE_MSG%
echo ================================================
echo.
echo ================================================
echo    %STOPPING_MSG%
echo ================================================

:: --- [1/3] Terminate Python & API Processes & Browsers ---
echo %STOP_PY%

:: 🌟 追加: 開いているブラウザ（ChromeやEdgeなど）の該当タブやセッションを綺麗に閉じる
taskkill /F /FI "WINDOWTITLE eq *Ariadne*" >nul 2>&1

taskkill /F /FI "WINDOWTITLE eq Dify Config API Server*" /T >nul 2>&1
taskkill /F /FI "WINDOWTITLE eq Kuzu Approval API Server*" /T >nul 2>&1
taskkill /F /FI "WINDOWTITLE eq Ariadne API (Dify Gateway)*" /T >nul 2>&1
taskkill /F /FI "WINDOWTITLE eq Ariadne Night Crawler*" /T >nul 2>&1
taskkill /F /FI "WINDOWTITLE eq Ariadne Deep Search Console*" /T >nul 2>&1

taskkill /F /FI "WINDOWTITLE eq *Dify*API*" /T >nul 2>&1
taskkill /F /FI "WINDOWTITLE eq *Key*Server*" /T >nul 2>&1
taskkill /F /FI "WINDOWTITLE eq *API Server*" /T >nul 2>&1
taskkill /F /FI "WINDOWTITLE eq *Gateway*" /T >nul 2>&1

for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8501 "') do taskkill /F /PID %%a /T >nul 2>&1
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8091 "') do taskkill /F /PID %%a /T >nul 2>&1
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8000 "') do taskkill /F /PID %%a /T >nul 2>&1
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8001 "') do taskkill /F /PID %%a /T >nul 2>&1
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8002 "') do taskkill /F /PID %%a /T >nul 2>&1
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":7860 "') do taskkill /F /PID %%a /T >nul 2>&1

powershell -Command "Get-Process python -ErrorAction SilentlyContinue | Where-Object { $_.Path -like '*GraphRAG*' -or $_.Path -like '*ariadne-rag*' } | Stop-Process -Force" >nul 2>&1

echo    Ariadne UI, Deep Search Console (Port 8091), API Servers and background processes terminated.
echo.

:: --- [2/3] Process Docker Infrastructure ---
echo %STOP_DOCKER%

:: 🌟 バッチファイルの場所を基準に正しくインフラフォルダを特定する
set "SERVE_DIR=%~dp0"
if "%SERVE_DIR:~-1%"=="\" set "SERVE_DIR=%SERVE_DIR:~0,-1%"
set "TARGET_DIR=%SERVE_DIR%\..\GraphRAG_Gateway_ingest"

if exist "%TARGET_DIR%\docker-compose-infrastructure.yml" (
    echo    Target Directory: %TARGET_DIR%
    pushd "%TARGET_DIR%"
    docker compose -f docker-compose-infrastructure.yml %DOCKER_CMD%
    popd
    echo    [OK] Infrastructure processed.
) else (
    echo    [WARNING] docker-compose-infrastructure.yml not found in:
    echo    %TARGET_DIR%
)
echo.

:: --- [3/3] Process Dify Infrastructure ---
echo %STOP_DIFY%
set "DIFY_DIR=%SERVE_DIR%\..\dify\docker"

if exist "%DIFY_DIR%\docker-compose.yaml" (
    set "DIFY_COMPOSE_FILE=docker-compose.yaml"
) else if exist "%DIFY_DIR%\docker-compose.yml" (
    set "DIFY_COMPOSE_FILE=docker-compose.yml"
) else (
    set "DIFY_COMPOSE_FILE="
)

if not "%DIFY_COMPOSE_FILE%"=="" (
    echo    Target Directory: %DIFY_DIR%
    pushd "%DIFY_DIR%"
    docker compose -f %DIFY_COMPOSE_FILE% %DOCKER_CMD%
    popd
    echo    [OK] Dify Infrastructure processed.
) else (
    echo    [WARNING] Dify docker-compose file not found in:
    echo    %DIFY_DIR%
)

:: --- Finish ---
:END_PROCESS

echo.
echo ================================================
echo    %COMPLETE_MSG%
echo ================================================
echo.
pause
exit