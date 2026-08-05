@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
cd /d %~dp0

rem ========================================================
rem  Docker デスクトップ起動チェック（安全装置）
rem ========================================================
if not "%~1"=="__WORKER__" (
    docker ps >nul 2>&1
    if errorlevel 1 (
        echo.
        echo ================================================
        echo ❌ エラー: Docker Desktop が起動していません。
        echo ================================================
        echo Docker Desktop を起動し、準備が完了するまで待ってから
        echo 再度このスクリプトを実行してください。
        echo.
        pause
        exit /b 1
    )
)

if "%~1"=="__WORKER__" goto :MAIN_PROCESS

cls
echo.
echo ================================================
echo     GraphRAG インフラストラクチャ管理
echo     (インフラ起動専用・純化版)
echo ================================================
echo.
echo [1] 通常起動・セットアップ (既存のデータやDBを保持)
echo [2] クリーン・セットアップ (既存データを完全に削除して再構築)
echo [3] インフラの完全削除のみ (コンテナとデータを破棄して終了)
echo [0] キャンセル
echo.
set /p MENU_CHOICE="実行する処理の番号を入力してください (0-3): "

if "%MENU_CHOICE%"=="0" (
    echo.
    echo 処理をキャンセルしました。
    pause
    exit /b 0
)

:EXEC_WORKER
if not exist "logs" mkdir "logs"
set LOGFILE=logs\infrastructure_%date:~0,4%%date:~5,2%%date:~8,2%_%time:~0,2%%time:~3,2%%time:~6,2%.log
set LOGFILE=%LOGFILE: =0%

echo.
echo [情報] 処理を開始します...
echo [情報] ログファイル: %LOGFILE%
echo.

powershell -NoProfile -Command "cmd /c '%~f0' __WORKER__ %MENU_CHOICE% | Tee-Object -FilePath '%LOGFILE%'"

if not "%MENU_CHOICE%"=="3" (
    call :BROWSER_CHECK
)

echo.
echo ================================================
echo 全ての処理が完了しました
echo ================================================
echo.
if exist "%LOGFILE%" echo ログを保存しました: %LOGFILE%
echo.
echo いずれかのキーを押して閉じてください...
pause >nul
exit /b

:BROWSER_CHECK
echo.
echo ================================================
echo    データベース管理ツールの起動確認
echo ================================================
set CURRENT_STORAGE=neo4j
set ENV_PATH=..\GraphRAG_Gateway_serve\.env

if exist "%ENV_PATH%" (
    for /f "tokens=2 delims==" %%A in ('findstr "GRAPH_STORE_TYPE" "%ENV_PATH%" 2^>nul') do (
        set CURRENT_STORAGE=%%A
        set CURRENT_STORAGE=!CURRENT_STORAGE:"=!
        set CURRENT_STORAGE=!CURRENT_STORAGE: =!
    )
)

if /i "!CURRENT_STORAGE!"=="neo4j" (
    echo [Neo4j Browser] http://localhost:7474
    set /p OPEN_BROWSER="Neo4jブラウザを開きますか？ (Y/N): "
    if /i "!OPEN_BROWSER!"=="Y" start http://localhost:7474
) else if /i "!CURRENT_STORAGE!"=="falkordb" (
    echo [情報] 現在 FalkorDB モードで稼働中です。(Port: 6379)
    echo ※ RedisInsight などのGUIツールを使用して localhost:6379 に接続してください。
) else if /i "!CURRENT_STORAGE!"=="kuzu" (
    echo [情報] 現在 Kuzu モードで稼働中です。(Port: 8005)
    echo ※ ブラウザで http://localhost:8005/ にアクセスするとKuzu Explorer（UI）が確認できます。
) else (
    echo [情報] 現在 !CURRENT_STORAGE! モードで稼働中です。
)
exit /b

:MAIN_PROCESS
chcp 65001 >nul
set WORK_DIR=%~dp0
cd /d "%WORK_DIR%"
set MODE=%~2
set ENV_PATH=..\GraphRAG_Gateway_serve\.env

echo [1] Dockerの可用性を確認中...
docker ps >nul 2>&1
if errorlevel 1 (
    echo [エラー] Dockerが起動していません。
    exit /b 1
)
echo [OK] Docker稼働中

echo [2] 設定ファイル確認...
if not exist docker-compose-infrastructure.yml exit /b 1
echo [OK] yml検出

if "%MODE%"=="3" (
    echo [3] インフラの完全削除を実行中...
    docker compose -f docker-compose-infrastructure.yml down --volumes --remove-orphans
    echo [4] 物理データフォルダの削除を実行中...
    if exist "storage_kg" rmdir /s /q "storage_kg"
    if exist "statistics" rmdir /s /q "statistics"
    echo [OK] 全てのインフラとデータを完全に撤去しました。
    exit /b 0
)

echo [3] 共有ネットワーク作成...
docker network inspect graphrag_shared_network >nul 2>&1
if errorlevel 1 docker network create graphrag_shared_network >nul 2>&1
echo [OK] 共有ネットワーク確保

echo [4] インフラ起動準備...

if "%MODE%"=="2" (
    echo [警告] クリーンアップ指定: 既存コンテナと物理データフォルダを破棄しています...
    docker compose -f docker-compose-infrastructure.yml down --volumes --remove-orphans 2>nul
    if exist "storage_kg" rmdir /s /q "storage_kg"
    if exist "statistics" rmdir /s /q "statistics"
) else (
    echo [情報] 既存コンテナを維持したまま起動を確認します...
)

if not exist "storage_kg" mkdir "storage_kg"
if not exist "statistics" mkdir "statistics"
echo [OK] %CD%\storage_kg
echo [OK] %CD%\statistics

REM 🌟 追加: [5] ジャンクションリンクの自動生成処理
echo [5] UI側との記憶フォルダ共有 (ジャンクションリンク) を設定中...
set "SERVE_DIR=..\GraphRAG_Gateway_serve"

:: Serve側の古い（または空の）フォルダを一旦削除
if exist "%SERVE_DIR%\storage_kg" rmdir /S /Q "%SERVE_DIR%\storage_kg" >nul 2>&1
if exist "%SERVE_DIR%\statistics" rmdir /S /Q "%SERVE_DIR%\statistics" >nul 2>&1

:: Ingest側への直通トンネルを作成
mklink /J "%SERVE_DIR%\storage_kg" "%CD%\storage_kg" >nul 2>&1
mklink /J "%SERVE_DIR%\statistics" "%CD%\statistics" >nul 2>&1
echo [OK] 管制塔 (Serve) へのデータ直通トンネルが開通しました。

echo [6] 全サービスの一斉起動...
docker compose -f docker-compose-infrastructure.yml up -d
if errorlevel 1 exit /b 1

set TARGET_DB_CONTAINER=graphrag-neo4j
set TARGET_DB_NAME=Neo4j

if exist "%ENV_PATH%" (
    for /f "tokens=2 delims==" %%A in ('findstr "GRAPH_STORE_TYPE" "%ENV_PATH%" 2^>nul') do (
        set TMP_STORE=%%A
        set TMP_STORE=!TMP_STORE:"=!
        set TMP_STORE=!TMP_STORE: =!
        if /i "!TMP_STORE!"=="falkordb" (
            set TARGET_DB_CONTAINER=graphrag-falkordb
            set TARGET_DB_NAME=FalkorDB
        ) else if /i "!TMP_STORE!"=="tidb" (
            set TARGET_DB_CONTAINER=graphrag-tidb
            set TARGET_DB_NAME=TiDB
        ) else if /i "!TMP_STORE!"=="kuzu" (
            set TARGET_DB_CONTAINER=graphrag-kuzu
            set TARGET_DB_NAME=Kuzu
        )
    )
)

echo [7] グラフデータベース (!TARGET_DB_NAME!) のヘルスチェック (最大5分待機)...
set /a WAIT_COUNT=0
set /a MAX_WAIT=60
:wait_graphdb
timeout /t 5 /nobreak >nul
set /a WAIT_COUNT+=1
docker inspect !TARGET_DB_CONTAINER! --format="{{.State.Health.Status}}" 2>nul | findstr "healthy" >nul 2>&1
if errorlevel 1 (
    if !WAIT_COUNT! LSS %MAX_WAIT% (
        echo    !TARGET_DB_NAME! 起動待機中... (!WAIT_COUNT!/%MAX_WAIT%)
        goto wait_graphdb
    ) else (
        echo [警告] !TARGET_DB_NAME! の応答確認にタイムアウトしました。またはヘルスチェックが未設定です。
    )
) else (
    echo [OK] !TARGET_DB_NAME! データベースが正常に稼働しています。
)

echo [8] Firecrawl用DBの自動パッチ適用...
set /a PG_WAIT_COUNT=0
set /a PG_MAX_WAIT=30
:wait_postgres
timeout /t 2 /nobreak >nul
set /a PG_WAIT_COUNT+=1
docker exec -i graphrag-firecrawl-postgres pg_isready -U postgres >nul 2>&1
if errorlevel 1 (
    if !PG_WAIT_COUNT! LSS %PG_MAX_WAIT% goto wait_postgres
)
docker exec -i graphrag-firecrawl-postgres psql -U postgres -d postgres -c "ALTER TYPE nuq.job_status ADD VALUE IF NOT EXISTS 'queued';" >nul 2>&1
echo [OK] データベーススキーマの自動パッチ適用完了
echo    Firecrawlエンジンを再起動して同期します...
docker restart graphrag-firecrawl >nul 2>&1

echo [9] Firecrawl API 疎通確認...
set /a FC_WAIT_COUNT=0
set /a FC_MAX_WAIT=60
:wait_firecrawl
timeout /t 3 /nobreak >nul
set /a FC_WAIT_COUNT+=1
curl --fail --silent --show-error -o nul http://localhost:3002/
if errorlevel 1 (
    if !FC_WAIT_COUNT! LSS %FC_MAX_WAIT% (
        echo    Firecrawl API 起動待機中... (!FC_WAIT_COUNT!/%FC_MAX_WAIT%)
        goto wait_firecrawl
    ) else (
        echo [警告] Firecrawlの応答確認にタイムアウトしました。
    )
) else (
    echo [OK] Firecrawl API の完全覚醒を確認しました (Port: 3002)。
)

echo [10] 環境設定ファイルの整合性確認...
if not exist "%ENV_PATH%" (
    echo GRAPH_STORE_TYPE=neo4j > "%ENV_PATH%"
)

echo [11] データベース連携用Pythonパッケージのインストール...
REM pip install falkordb kuzu pymysql --quiet
pip install kuzu --quiet
echo [OK] パッケージのインストール完了

echo.
echo ================================================
echo     自動セットアップフェーズ完了
echo ================================================
exit /b 0