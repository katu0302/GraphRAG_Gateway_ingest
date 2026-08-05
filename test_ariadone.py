import os
from qdrant_client import QdrantClient
from qdrant_client.http.models import Distance, VectorParams
from google import genai  # 新しいSDKを使用

# 1. 設定
GOOGLE_API_KEY = "AIzaSyAWyHaqe0UT60kfFLtfUq3NS3d5vG1BRxo"
COLLECTION_NAME = "ariadne_test"
DIMENSION = 3072

# 2. 新しい Google GenAI クライアントの準備
client_google = genai.Client(api_key=GOOGLE_API_KEY)

# 3. Qdrant クライアントの準備
client_qdrant = QdrantClient(host="localhost", port=6333)


def test_ingest():
    print("🚀 最新SDKでのテストインジェスト開始...")

    # Qdrantコレクション作成
    if not client_qdrant.collection_exists(COLLECTION_NAME):
        client_qdrant.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=VectorParams(size=DIMENSION, distance=Distance.COSINE),
        )
        print(f"✅ コレクション '{COLLECTION_NAME}' を作成しました。")

    # テキストをベクトル化 (新しいSDKの書き方)
    text = "アリアドネ、新しい身体（Qdrant）での居心地はどうですか？"

    # モデル名を 'text-embedding-004' に修正（models/ は不要な場合が多いです）
    result = client_google.models.embed_content(
        model='models/gemini-embedding-001',
        contents=text
    )

    # 結果の取り出し方が少し変わります
    vector = result.embeddings[0].values

    # Qdrant へ保存
    client_qdrant.upsert(
        collection_name=COLLECTION_NAME,
        points=[
            {
                "id": 1,
                "vector": vector,
                "payload": {"text": text, "source": "morning_test_new_sdk"}
            }
        ]
    )
    print("✨ Qdrant への書き込みに成功しました！")


if __name__ == "__main__":
    test_ingest()