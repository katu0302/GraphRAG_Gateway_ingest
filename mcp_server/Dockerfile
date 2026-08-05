FROM python:3.11-slim
WORKDIR /app

# 1. 先に requirements.txt をコンテナにコピーする
COPY requirements.txt .

# 2. 古いmcpを削除しつつ、requirements.txt の中身をすべてインストール
RUN pip install --no-cache-dir --upgrade pip && \
    pip uninstall -y mcp && \
    pip install --no-cache-dir -r requirements.txt

# 3. 実行するPythonプログラムをコピー
COPY server.py .

CMD ["python", "server.py"]