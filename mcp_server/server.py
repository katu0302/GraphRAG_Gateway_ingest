import os
import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel
from pathlib import Path
import shutil  # 🌟 ファイル移動のために追加
from pathlib import Path

app = FastAPI(title="Ariadne API Tool")


class KnowledgeRequest(BaseModel):
    filename: str


@app.post("/read")
async def read_knowledge(req: KnowledgeRequest):
    try:
        # 起点を raw フォルダに固定
        base_dir = Path("/app/knowledge/raw")

        # 🌟 処理完了後に移動させる先のフォルダ (knowledge/raw/processed)
        # base_dir のフォルダ（knowledge/raw）の中に processed を作成します
        processed_dir = base_dir / "processed"
        # processedフォルダが存在しなければ自動で作成
        processed_dir.mkdir(parents=True, exist_ok=True)

        clean_filename = req.filename.strip()
        target_name = os.path.basename(clean_filename)

        files = list(base_dir.glob(f"*{target_name}*"))

        if not files:
            return {
                "content": f"【検索失敗】ファイル '{clean_filename}' が raw フォルダ直下に見つかりませんでした。すでに処理済み（processedフォルダに移動済み）の可能性があります。"}

        if len(files) > 1:
            file_list = "\n".join([str(f.relative_to(base_dir)) for f in files])
            return {
                "content": f"【複数ヒット】同じような名前のファイルが複数あります。どれか一つに絞れる名前を指定してください：\n{file_list}"}

        found_path = files[0]

        if not found_path.is_file() or found_path.parent != base_dir:
            return {"content": f"【アクセス拒否】指定されたパスは raw フォルダ直下のファイルではありません。"}

        ext = found_path.suffix.lower()
        extracted_text = ""

        # ▼ ファイルの読み込み処理
        if ext == ".pdf":
            import pypdf
            with open(found_path, "rb") as f:
                pdf_reader = pypdf.PdfReader(f)
                for page in pdf_reader.pages:
                    text = page.extract_text()
                    if text:
                        extracted_text += text + "\n"

        elif ext in [".txt", ".md", ".csv", ".json"]:
            try:
                with open(found_path, "r", encoding="utf-8") as f:
                    extracted_text = f.read()
            except UnicodeDecodeError:
                return {
                    "content": f"【エラー】ファイル '{found_path.name}' は文字コードがUTF-8ではないか、テキストとして読み込めません。"}
        else:
            return {"content": f"【非対応】ファイル形式 {ext} は現在読み取りに対応していません。"}

        # 🌟 ここが追加のメイン部分！ 🌟
        # 読み込みが正常に完了した場合のみ、ファイルを移動する
        if extracted_text:
            destination_path = processed_dir / found_path.name

            # 💡 注意: processed内に同名のファイルが既に存在する場合は上書きします
            # （上書きしたくない場合は、ファイル名に日時を付けるなどの処理が必要ですが、今回は上書きで進めます）
            shutil.move(str(found_path), str(destination_path))

            # AIへの返却テキストの先頭に、移動した旨を添えておくと親切です
            notice = f"（※システム通知：対象ファイルは読み取り完了後、安全のため processed フォルダに移動されました。）\n\n"
            return {"content": notice + extracted_text}

        return {"content": "【エラー】ファイルの読み込みは成功しましたが、中身が空でした。ファイルは移動していません。"}

    except Exception as e:
        return {"content": f"【システムエラー】ファイルの読み込み中に内部エラーが発生しました: {str(e)}"}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)