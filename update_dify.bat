@echo off
chcp 65001 >nul
:: ==========================================
:: ⚙️ 設定 (ご自身の環境に合わせて変更してください)
:: ==========================================
:: Difyのソースコード（dockerディレクトリ）がある絶対パスを指定してください。
:: 例: C:\dify\docker
set DIFY_DOCKER_PATH=C:\PycharmProjects\dify\docker

:: ==========================================
:: 🚀 処理開始
:: ==========================================
echo ========================================
echo [ Dify 更新プロセスを開始します ]
echo ========================================

:: 1. ディレクトリの存在確認
if not exist "%DIFY_DOCKER_PATH%" (
echo [エラー] 指定されたディレクトリが見つかりません: %DIFY_DOCKER_PATH%
echo スクリプト内の DIFY_DOCKER_PATH を正しいパスに修正してください。
pause
exit /b 1
)

:: ディレクトリへ移動 (ドライブが異なる場合も考慮して /d を付与)
cd /d "%DIFY_DOCKER_PATH%"
echo フォルダを移動しました: %CD%

:: 2. 現在のコンテナを停止
echo.
echo 停止中: 現在稼働中のDifyコンテナを安全に停止しています...
docker compose down

:: 3. 最新ソースの取得 (git pull は1つ上の階層で実行)
echo.
echo ダウンロード中: 公式リポジトリから最新のソースコードを取得しています...
cd ..
git pull origin main
cd docker

:: 4. 最新のDockerイメージをPull
echo.
echo 取得中: 最新のDockerイメージをダウンロードしています...
echo (※回線速度によって数分かかる場合があります)
docker compose pull

:: 5. コンテナの再起動
echo.
echo 起動中: Difyを最新バージョンで再起動しています...
docker compose up -d

echo.
echo ========================================
echo [ Difyのアップデートが正常に完了しました！ ]
echo ブラウザから http://localhost にアクセスして確認してください。
echo ========================================
pause