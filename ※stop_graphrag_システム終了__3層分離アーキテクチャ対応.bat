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
set TITLE_MSG=Tower of GraphRAG - System Shutdown Process
set STOPPING_MSG=%MSG_SUFFIX%
set STOP_PY= [1/3] Terminating Control Tower (Python)...
set STOP_DOCKER= [2/3] Processing Infrastructure (Docker) using '%DOCKER_CMD%'...
set STOP_DIFY= [3/3] Processing Dify Infrastructure (Docker) using '%DOCKER_CMD%'...
set COMPLETE_MSG=COMPLETE: %COMPLETE_SUFFIX%

cls
echo ================================================
echo    %TITLE_MSG%
echo    Execution Mode: %MODE_MSG%
echo ================================================
echo.
echo ================================================
echo    %STOPPING_MSG%
echo ================================================

:: --- [1/3] Terminate Python Processes ---
echo %STOP_PY%

:: 1. 独立して立ち上げたコマンドプロンプト画面をタイトルで狙い撃ちして終了 (/T で子プロセスごとキル)
taskkill /F /FI "WINDOWTITLE eq Kuzu Approval API Server*" /T >nul 2>&1
taskkill /F /FI "WINDOWTITLE eq Ariadne API (Dify Gateway)*" /T >nul 2>&1
taskkill /F /FI "WINDOWTITLE eq Ariadne Night Crawler*" /T >nul 2>&1

:: 2. ポートを占有しているプロセスを終了 (8501: UI, 8001: Dify API, 8002: Kuzu API)
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8501 "') do taskkill /F /PID %%a /T >nul 2>&1
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8001 "') do taskkill /F /PID %%a /T >nul 2>&1
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8002 "') do taskkill /F /PID %%a /T >nul 2>&1
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":7860 "') do taskkill /F /PID %%a /T >nul 2>&1

:: 3. バックグラウンドに残ったPythonプロセスをクリーンアップ (ariadne-rag フォルダも対象に追加)
powershell -Command "Get-Process python -ErrorAction SilentlyContinue | Where-Object { $_.Path -like '*GraphRAG*' -or $_.Path -like '*ariadne-rag*' } | Stop-Process -Force" >nul 2>&1

echo    - Ariadne UI and background processes terminated.

:: --- [2/3] Process Docker Infrastructure ---
echo %STOP_DOCKER%
set "TARGET_DIR="
set "REL_DIR=%~dp0..\GraphRAG_Gateway_ingest"
set "ABS_DIR=C:\PycharmProjects\GraphRAG_Gateway_ingest"

if exist "%REL_DIR%\docker-compose-infrastructure.yml" set "TARGET_DIR=%REL_DIR%"
if exist "%ABS_DIR%\docker-compose-infrastructure.yml" set "TARGET_DIR=%ABS_DIR%"

if "%TARGET_DIR%"=="" goto INFRA_ERROR

pushd "%TARGET_DIR%"
docker compose -f docker-compose-infrastructure.yml %DOCKER_CMD%
popd
echo    - Infrastructure processed.
goto DIFY_START

:INFRA_ERROR
echo    - [WARNING] Infrastructure configuration file not found.
echo      1: %REL_DIR%\docker-compose-infrastructure.yml
echo      2: %ABS_DIR%\docker-compose-infrastructure.yml

:: --- [3/3] Process Dify Infrastructure ---
:DIFY_START
echo %STOP_DIFY%
set "DIFY_DIR=C:\PycharmProjects\dify\docker"

if not exist "%DIFY_DIR%\docker-compose.yaml" if not exist "%DIFY_DIR%\docker-compose.yml" goto DIFY_ERROR

pushd "%DIFY_DIR%"
docker compose %DOCKER_CMD%
popd
echo    - Dify Infrastructure processed.
goto END_PROCESS

:DIFY_ERROR
echo    - [WARNING] Dify Infrastructure configuration file not found: %DIFY_DIR%

:: --- Finish ---
:END_PROCESS
echo.
echo ================================================
echo    %COMPLETE_MSG%
echo ================================================
echo.
pause
exit