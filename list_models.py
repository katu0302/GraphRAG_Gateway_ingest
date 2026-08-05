import os
from google import genai

GOOGLE_API_KEY = "AIzaSyAWyHaqe0UT60kfFLtfUq3NS3d5vG1BRxo"
client = genai.Client(api_key=GOOGLE_API_KEY)

print("🔍 アリアドネが全モデルをスキャンしています...")
print("-" * 50)

try:
    models = list(client.models.list())
    if not models:
        print("🔴 モデルが一つも見つかりません。APIキーの設定やプロジェクトを確認してください。")
    else:
        for model in models:
            # モデル名と、そのモデルができること(actions)をすべて表示
            print(f"Name: {model.name}")
            print(f"Actions: {model.supported_actions}")
            print("-" * 30)
except Exception as e:
    print(f"❌ 通信エラー: {e}")