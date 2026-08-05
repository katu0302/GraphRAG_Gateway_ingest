# 基本アーキテクチャ
# コンポーネント構成
# LlamaIndex: ドキュメント処理とインデックス作成を担当
# 登録スクリプト
# ============================================================
# 標準ライブラリのインポート
# ============================================================
import logging
import os
import sys
import time
from pathlib import Path
import re

from dotenv import load_dotenv

from llama_index.core import KnowledgeGraphIndex
# from llama_index.core import Settings
from llama_index.core import SimpleDirectoryReader, StorageContext
# from llama_index.core.agent.legacy.react.base import ReActAgent
from llama_index.core.node_parser import SentenceSplitter
# from llama_index.embeddings.openai import OpenAIEmbedding
from llama_index.graph_stores.neo4j import Neo4jGraphStore
# from llama_index.llms.openai import OpenAI

from neo4j import GraphDatabase, exceptions

from llama_index.llms.openai import utils

from llama_index.llms.openai import OpenAI
from llama_index.embeddings.openai import OpenAIEmbedding
from llama_index.core import Settings
from llama_index.core.postprocessor import SimilarityPostprocessor
from llama_index.core.postprocessor.metadata_replacement import MetadataReplacementPostProcessor

# ログレベルの設定
# logging.basicConfig(stream=sys.stdout, level=logging.DEBUG, force=True)
logging.basicConfig(stream=sys.stdout, level=logging.INFO, force=True)
logger = logging.getLogger()
logger.addHandler(logging.StreamHandler(sys.stdout))

# .envファイルから環境変数を読み込む
# .envファイルのパスを指定
# env_path = Path("C:/Users/katu2/PycharmProjects/GraphRAG_Neo4j/.env")
# 特定のプロジェクトディレクトリの.envファイルを指定
project_folder = Path(__file__).parent.parent
env_path = project_folder / '.env'

# .envファイルを読み込む
logger.info(f"＃読み込む .envファイル： {env_path}")
load_dotenv(dotenv_path=env_path, override=True)

# Neo4jの接続情報
neo4j_url = os.getenv("NEO4J_URL")
neo4j_user = os.getenv("NEO4J_USER")
neo4j_password = os.getenv("NEO4J_PASSWORD")
neo4j_database = os.getenv("NEO4J_DATABASE")

# OpenAI 関連
# APIキー
os.environ["OPENAI_API_KEY"] = os.getenv("OPENAI_API_KEY")
openai_llm = os.getenv("OPENAI_LLM")
openai_embed = os.getenv("OPENAI_EMBED")

# データをロードするフォルダを設定
load_fld = os.getenv("LOAD_FLD")

# ChatBot Port
chat_port = int(os.getenv("CHAT_PORT"))

# chunk_size、chunk_overlap
chunk_size = int(os.getenv("CHUNK_SIZE"))
chunk_overlap = int(os.getenv("CHUNK_OVERLAP"))

# Neo4jサーバーに接続してdatabaseのデータを削除する
# URI と認証情報を設定
URL = neo4j_url
AUTH = (neo4j_user, neo4j_password)

def connect_with_retry(uri, auth, max_retries=5, initial_delay=1, max_delay=60):
    """
    Neo4jデータベースに接続を試み、一時的な障害時には再試行を行う

    Args:
        uri (str): Neo4jサーバーへの接続URI
        auth (tuple): 認証情報（通常はユーザー名とパスワードのタプル）
        max_retries (int): 最大試行回数
        initial_delay (int): 最初の再試行までの待機時間（秒）
        max_delay (int): 最大待機時間（秒）

    Returns:
        neo4j.Driver: 接続成功時のドライバーインスタンス

    Raises:
        exceptions.ServiceUnavailable: 最大試行回数後もサーバーが利用できない場合
        exceptions.AuthError: 認証情報が無効な場合
        Exception: その他のエラーが発生した場合
    """
    logger = logging.getLogger(__name__)
    driver = None
    retries = 0
    delay = initial_delay

    while retries < max_retries:
        try:
            if driver:
                try:
                    driver.close()  # 前回失敗したドライバーを確実にクローズ
                except:
                    pass

            driver = GraphDatabase.driver(uri, auth=auth)
            driver.verify_connectivity()
            logger.info("Neo4j サーバーへ接続しました。")
            return driver

        except exceptions.ServiceUnavailable as e:
            logger.warning(f"接続試行 {retries + 1}/{max_retries} 失敗: {e}")
            retries += 1

            if retries < max_retries:
                logger.info(f"{delay}秒後に再試行します...")
                time.sleep(delay)
                # 指数バックオフ（最大値で制限）
                delay = min(delay * 2, max_delay)
            else:
                logger.error("最大試行回数に達しました。接続を中止します。")
                if driver:
                    driver.close()
                raise

        except exceptions.AuthError as e:
            logger.error(f"認証エラーが発生しました: {e}")
            if driver:
                driver.close()
            raise

        except Exception as e:
            logger.error(f"予期しないエラーが発生しました: {e}")
            if driver:
                driver.close()
            raise

    # 明示的なreturn文を追加（実際には実行されない）
    return None  # このコードは論理的には到達不可能


# ドライバーを作成

try:
    # ドライバーを一度だけ初期化
    driver = connect_with_retry(URL, AUTH)

    # セッションを作成して操作を行う
    with driver.session(database=neo4j_database) as session:
        try:
            # クエリを実行
            query_del = "MATCH (n) OPTIONAL MATCH (n)-[r]-() DELETE n, r"
            result = session.run(query_del)
            logger.info("全てのグラフを削除しました。")
        except exceptions.CypherSyntaxError as e:
            logger.info(f"クエリの構文エラーが発生しました: {e}")
        except exceptions.TransactionError as e:
            logger.info(f"トランザクションエラーが発生しました: {e}")
        except Exception as e:
            logger.info(f"予期しないエラーが発生しました: {e}")

except Exception as e:
    logger.info(f"接続に失敗しました: {e}")
    exit(1)
finally:
    # すべての操作が完了した後、ドライバーを一度だけ閉じる
    if 'driver' in locals():
        try:
            driver.close()
            logger.info("ドライバーが正常に閉じられました。")
        except Exception as e:
            logger.info(f"ドライバーのクローズ中にエラーが発生しました: {e}")

# Neo4jグラフストアの設定
graph_store = ""
try:
    graph_store = Neo4jGraphStore(
        username=neo4j_user,
        password=neo4j_password,
        url=neo4j_url,
        database=neo4j_database  # デフォルトデータベースを明示的に指定 (.envで指定)
    )
    logger.info(f"boltサーバーに接続。database = {neo4j_database}")
except Exception as e:
    logger.info(f"予期せぬエラーが発生しました: {e}")
    sys.exit(1)

# 埋め込みモデルの設定準備
# embed_model = OpenAIEmbedding(model_name = openai_embed)
# Embeddingの設定
embed_model = OpenAIEmbedding(
    model="text-embedding-3-small",
    embed_batch_size=1,  # ← これ追加！
    max_retries=3        # ← これ追加！
)

# LLM の設定準備
# llm = OpenAI(model = openai_llm, temperature = 0.0)
# ✅ gpt-4.1-miniを明示的にコンテキストサイズ指定で使用
llm = OpenAI(
    model=openai_llm,
    temperature=0.7,
    max_tokens=32000,
    api_key=os.getenv("OPENAI_API_KEY")
)

"""
高品質ナレッジグラフ構築システム - 4層フィルタリング統合版
LlamaParseの品質問題を多層防御で解決
"""

import os
import re
import logging
from typing import List, Tuple, Optional
from llama_index.core import Document, SimpleDirectoryReader, StorageContext, Settings
from llama_index.core.schema import TextNode
from llama_index.core.node_parser import SentenceSplitter
from llama_index.core.indices.knowledge_graph import KnowledgeGraphIndex
from llama_index.core.callbacks import CallbackManager, LlamaDebugHandler
from llama_index.core.memory import ChatMemoryBuffer

logger = logging.getLogger(__name__)


# ========================================
# Layer 1: LlamaParseクリーニング層
# ========================================
class LlamaParseOutputCleaner:
    """LlamaParseの出力を高品質化するクリーニング層"""

    # ログから検出された不要パターン [file:20]
    NOISE_PATTERNS = [
        r'The text in the image reads',
        r'This translates to',
        r'I\'?m unable to assist with that\.?',
        r'Note[:\s]+This text is an example',
        r'Legal System Regarding Flying Cars',
    ]

    # 空白・記号のみの行パターン
    EMPTY_LINE_PATTERNS = [
        r'^\s*$',  # 完全空行
        r'^[\d\.]+\s*$',  # "1.", "2." だけの行 [file:20]
        r'^[・\-\*]+\s*$',  # 記号のみ
    ]

    def clean_document(self, doc: Document) -> Optional[Document]:
        """ドキュメントの包括的クリーニング"""
        if not hasattr(doc, 'text') or not doc.text:
            logger.warning(f"Document has no text attribute")
            return None

        text = doc.text

        # Step 1: ノイズパターンの除去
        for pattern in self.NOISE_PATTERNS:
            text = re.sub(pattern, '', text, flags=re.IGNORECASE)

        # clean_document() の Step 1直後に **置き換え**
        # 番号リストブロック全体を削除（行単位でなくブロック単位）
        text = re.sub(
            r'^\s*\d+[\.\)]\s*\n(?:^\s*\d+[\.\)]\s*\n){2,}',  # 3行以上連続
            '',
            text,
            flags=re.MULTILINE
        )
        # + 単独番号行も個別に削除
        text = re.sub(r'^\s*\d+[\.\)]\s*$', '', text, flags=re.MULTILINE)
        # ========================================

        # Step 2: 連続空行の正規化 (3行以上 → 2行)
        # ログで確認された5-6行の空行に対応 [file:20]
        text = re.sub(r'\n{3,}', '\n\n', text)

        # Step 3: 空白・記号のみ行の削除
        lines = text.split('\n')
        cleaned_lines = []
        for line in lines:
            is_noise = False
            for pattern in self.EMPTY_LINE_PATTERNS:
                if re.match(pattern, line):
                    is_noise = True
                    break
            if not is_noise:
                cleaned_lines.append(line)

        text = '\n'.join(cleaned_lines)

        # Step 4: 連続空白の正規化
        text = re.sub(r' {2,}', ' ', text)  # 複数スペース → 1スペース
        text = re.sub(r'\t+', ' ', text)  # タブ → スペース

        # Step 5: 前後の空白除去
        text = text.strip()

        # Step 6: 最小文字数チェック
        # ログでは150文字中148文字がノイズのケースあり [file:20]
        if len(text) < 30:
            logger.warning(f"Document too short after cleaning: {len(text)} chars")
            return None

        # クリーニング済みドキュメント作成
        # ✅ 新コード: 新しいDocumentオブジェクトを作成
        cleaned_doc = Document(
            text=text,
            metadata=doc.metadata.copy() if hasattr(doc, 'metadata') else {},
            id_=doc.id_ if hasattr(doc, 'id_') else None,
            excluded_embed_metadata_keys=doc.excluded_embed_metadata_keys if hasattr(doc,
'excluded_embed_metadata_keys') else None,
            excluded_llm_metadata_keys=doc.excluded_llm_metadata_keys if hasattr(doc,
'excluded_llm_metadata_keys') else None,
        )

        return cleaned_doc
        # ========================================

    def clean_documents(self, documents: List[Document]) -> List[Document]:
        """ドキュメントリスト全体のクリーニング"""
        cleaned = []
        rejected = 0

        for doc in documents:
            cleaned_doc = self.clean_document(doc)
            if cleaned_doc:
                cleaned.append(cleaned_doc)
            else:
                rejected += 1

        logger.info(f"[Layer 1] Document cleaning: {len(documents)} → {len(cleaned)} (rejected: {rejected})")
        return cleaned

# ========================================
# Layer 2: インテリジェント・チャンクバリデーター
# ========================================
class SmartChunkValidator:
    """意味のあるチャンクのみを通過させるバリデーター"""

    def __init__(self,
                 min_chars: int = 50,
                 min_words: int = 5,
                 max_empty_ratio: float = 0.3):
        self.min_chars = min_chars
        self.min_words = min_words
        self.max_empty_ratio = max_empty_ratio

    def is_valid_chunk(self, text: str) -> Tuple[bool, str]:
        """チャンクの妥当性を検証"""

        # Check 1: 最小文字数
        # ログの "Adding chunk 4" のようなケースを除外 [file:20]
        if len(text.strip()) < self.min_chars:
            return False, f"Too short: {len(text)} chars"

        # Check 2: 単語数チェック
        words = [w for w in text.split() if len(w) > 1]
        if len(words) < self.min_words:
            return False, f"Too few words: {len(words)}"

        # Check 3: 空白文字比率
        # ログで確認された空行過多のチャンクに対応 [file:20]
        text_chars = len(text)
        whitespace_chars = text.count(' ') + text.count('\n') + text.count('\t')
        empty_ratio = whitespace_chars / text_chars if text_chars > 0 else 1.0

        if empty_ratio > self.max_empty_ratio:
            return False, f"Too much whitespace: {empty_ratio:.1%}"

        # Check 4: 数字のみチェック
        # ログの "1", "2", "3" だけのチャンクを除外 [file:20]
        if text.strip().replace('.', '').replace(' ', '').isdigit():
            return False, "Numbers only"

        # Check 5: 記号のみチェック
        symbols_only = all(not c.isalnum() for c in text.strip())
        if symbols_only:
            return False, "Symbols only"

        # Check 6: エラーメッセージ検出
        # ログの "Im unable to assist with that" を検出 [file:20]
        error_keywords = ['unable to assist', 'cannot process', 'error']
        text_lower = text.lower()
        if any(keyword in text_lower for keyword in error_keywords):
            return False, "Error message detected"

        return True, "Valid"

    def filter_nodes(self, nodes: List[TextNode]) -> List[TextNode]:
        """ノードリストをフィルタリング"""
        valid_nodes = []
        rejected_count = {}

        for node in nodes:
            node_text = getattr(node, 'text', '') or getattr(node, 'get_content', lambda: '')()
            is_valid, reason = self.is_valid_chunk(node_text)

            if is_valid:
                valid_nodes.append(node)
            else:
                rejected_count[reason] = rejected_count.get(reason, 0) + 1
                logger.debug(f"Rejected chunk: {reason} - {node_text[:50]}...")

        # 統計出力
        logger.info(f"[Layer 2] Chunk validation: {len(nodes)} → {len(valid_nodes)}")
        for reason, count in sorted(rejected_count.items(), key=lambda x: x[1], reverse=True):
            logger.info(f"  - {reason}: {count}")

        return valid_nodes


# ========================================
# Layer 3: トリプレット品質フィルター
# ========================================
class TripletQualityFilter:
    """抽出されたトリプレットの品質を保証"""

    def __init__(self,
                 min_entity_length: int = 2,
                 min_predicate_length: int = 1,
                 max_entity_length: int = 100):
        self.min_entity_length = min_entity_length
        self.min_predicate_length = min_predicate_length
        self.max_entity_length = max_entity_length
        self.stats = {'total': 0, 'valid': 0, 'rejected': {}}

    def is_valid_triplet(self,
                         subj: str,
                         pred: str,
                         obj: str) -> Tuple[bool, str]:
        """トリプレットの妥当性を検証"""

        # Check 0: 空文字列チェック（強化版）【★NEW★】
        # ログの ('', 'Is', '') を確実に除外 [file:24]
        s, p, o = subj.strip(), pred.strip(), obj.strip()
        if not s or not p or not o:
            return False, "Empty component(s)"

        # Check 1: 最小長チェック
        if len(s) < self.min_entity_length:
            return False, f"Subject too short ({len(s)})"
        if len(o) < self.min_entity_length:
            return False, f"Object too short ({len(o)})"
        if len(p) < self.min_predicate_length:
            return False, f"Predicate too short ({len(p)})"

        # Check 2: 最大長チェック（異常に長いエンティティ）
        if len(subj) > self.max_entity_length or len(obj) > self.max_entity_length:
            return False, "Entity too long"

        # Check 3: 空白のみの述語チェック
        if p.replace(' ', '') == '':
            return False, "Predicate is whitespace"

        # ★★★ Check 4: 単独"Is系述語"をブロック 【NEW】★★★
        # ログの ", Is, " を除外 [file:24]
        single_word_verbs = {"is", "are", "was", "were", "be", "been"}
        if p.lower() in single_word_verbs and len(p.split()) == 1:
            return False, f"Single verb predicate: '{p}'"

        # Check 5: 数字のみエンティティ（単独の場合）
        if s.isdigit() and len(s) < 3:
            return False, f"Subject is single digit"

        # Check 6: エラーメッセージの混入チェック
        error_fragments = ['unable', 'assist', 'error', 'failed']
        combined = f"{s} {p} {o}".lower()
        if any(frag in combined for frag in error_fragments):
            return False, "Error message fragment"

        # Check 7: 重複エンティティチェック（自己ループ）
        if s.lower() == o.lower():
            return False, "Self-loop"

        # Check 8: メタ情報フレーズチェック
        meta_phrases = ['text in the image', 'translates to', 'reads as']
        if any(phrase in combined for phrase in meta_phrases):
            return False, "Meta information"

        return True, "Valid"

    def filter_triplets(self,
                        triplets: List[Tuple[str, str, str]]) -> List[Tuple[str, str, str]]:
        """トリプレットリストをフィルタリング"""
        valid_triplets = []

        self.stats['total'] += len(triplets)

        for subj, pred, obj in triplets:
            is_valid, reason = self.is_valid_triplet(subj, pred, obj)
            if is_valid:
                # 前後の空白を除去して正規化
                clean_triplet = (subj.strip(), pred.strip(), obj.strip())
                valid_triplets.append(clean_triplet)
                self.stats['valid'] += 1
            else:
                self.stats['rejected'][reason] = self.stats['rejected'].get(reason, 0) + 1

        return valid_triplets

    def print_stats(self):
        """統計情報の出力"""
        logger.info(f"[Layer 3] Triplet validation:")
        logger.info(f"  Total: {self.stats['total']}")
        logger.info(f"  Valid: {self.stats['valid']}")
        logger.info(f"  Rejected: {self.stats['total'] - self.stats['valid']}")
        for reason, count in sorted(self.stats['rejected'].items(),
                                    key=lambda x: x[1], reverse=True)[:5]:
            logger.info(f"    - {reason}: {count}")


# ========================================
# Layer 3統合: フィルタリング付きKGIndex
# ========================================
from typing import Sequence, List, Tuple, Any
from llama_index.core.schema import BaseNode
from llama_index.core.indices.knowledge_graph import KnowledgeGraphIndex


# IndexDictは戻り値として推論されるので明示インポート不要
# from llama_index.core import IndexDict  # この行は削除

import logging
logger = logging.getLogger(__name__)

class FilteredKnowledgeGraphIndex(KnowledgeGraphIndex):
    def __init__(self, *args, **kwargs):
        logger.info("🔥🔥 FilteredKnowledgeGraphIndex LOADED! 🔥🔥")
        # ✅ 修正1: 2文字に緩和（建築用語対応）
        self.triplet_filter = TripletQualityFilter(min_entity_length=2)
        super().__init__(*args, **kwargs)

    def _extract_triplets(self, text: str) -> List[Tuple[str, str, str]]:
        logger.warning(f"🔥 EXTRACT CALLED: {text[:30]}...")
        raw = super()._extract_triplets(text)
        # ✅ 修正2: 1文字も許可（"法","罰"対応）
        filtered = [t for t in raw if all(len(x.strip()) >= 1 for x in t)]
        logger.warning(f"🔥 FILTER: {len(raw)} → {len(filtered)}")
        return filtered

    def _build_index_from_nodes(self, nodes: Sequence[BaseNode]) -> dict:
        """ノードからインデックス構築時の完全フィルタリング"""
        logger.info(f"[Layer 3] Building index from {len(nodes)} nodes...")

        filtered_nodes = []
        for i, node in enumerate(nodes):
            filtered_triplets = self._extract_triplets(node.get_content())
            if filtered_triplets:
                filtered_nodes.append(node)
                logger.debug(f"Node {i}: {len(filtered_triplets)} valid triplets")
            else:
                logger.debug(f"Node {i}: ALL triplets rejected")

        logger.info(f"[Layer 3] Calling parent with {len(filtered_nodes)} filtered nodes")
        result = super()._build_index_from_nodes(filtered_nodes)

        logger.info(f"[Layer 3] FINAL Triplet validation:")
        self.triplet_filter.print_stats()

        return result

    def insert(self, nodes: Sequence[BaseNode], **insert_kwargs: Any) -> None:
        """insert時もフィルタリング"""
        logger.info(f"[Layer 3] Inserting {len(nodes)} nodes with filtering...")
        filtered_nodes = []

        for node in nodes:
            filtered_triplets = self._extract_triplets(node.get_content())
            if filtered_triplets:
                filtered_nodes.append(node)

        super().insert(filtered_nodes, **insert_kwargs)
        logger.info(f"[Layer 3] Inserted {len(filtered_nodes)}/{len(nodes)} filtered nodes")


# ========================================
# メイン処理開始
# ========================================

# テキストスプリッターの設定準備
text_splitter = SentenceSplitter(
    chunk_size=chunk_size,
    chunk_overlap=chunk_overlap,
    paragraph_separator="\n\n",
)

logger.info("＃サンプルドキュメントの読込")
logger.info("＃LlamaParseを使用してファイルの高度な構造解析を実行")

import nest_asyncio

nest_asyncio.apply()

from llama_parse import LlamaParse

# LlamaParseの初期化
llama_api_key = os.getenv("LLAMA_CLOUD_API_KEY")
parse_model = os.getenv("PARSE_MODEL")
parse_mode = os.getenv("PARSE_MODE")

# カスタムパース指示を追加（強化版）
custom_instructions = """
Extract complete sentences and paragraphs with full context.
CRITICAL RULES:
- Ignore standalone numbers without surrounding text
- Skip list item numbers (1., 2., 3., etc.) if they have no content
- DO NOT include meta-descriptions like "The text in the image reads"
- DO NOT include translation notes like "This translates to"
- If OCR fails, skip the section instead of adding error messages
- Preserve table structures with headers and data
- Maintain document hierarchy and relationships
- Extract only meaningful entities with descriptive context
- Do not extract empty strings or whitespace-only content
- Minimum 3 words per extracted segment
"""

parser = LlamaParse(
    api_key=llama_api_key,
    result_type="markdown",
    system_prompt=custom_instructions,
    model=parse_model,
    parse_mode=parse_mode,

    # 高品質パース設定
    high_res_ocr=True,
    adaptive_long_table=True,
    outlined_table_extraction=True,
    output_tables_as_HTML=True,

    # 2025年の新機能
    page_error_tolerance=0.1,
    replace_failed_page_mode="error_message",

    verbose=True,
)

# カスタムファイルリーダーの設定
file_extractor = {
    ".pdf": parser,
    ".docx": parser,
    ".pptx": parser,
    ".png": parser,
    ".jpg": parser,
    ".jpeg": parser,
}

# サンプルドキュメントの読込
current_dir = os.path.dirname(os.path.abspath(__file__))
data_dir = os.path.join(current_dir, "datadir", load_fld)

documents = []
try:
    documents = SimpleDirectoryReader(
        data_dir,
        file_extractor=file_extractor,
        recursive=True,
    ).load_data()

    logger.info(f"LlamaParse読込完了: {len(documents)} ドキュメント")

    # ========================================
    # ★★★ Layer 1適用: LlamaParseクリーニング ★★★
    # ========================================
    logger.info("\n" + "=" * 80)
    logger.info("【Layer 1】LlamaParse出力のクリーニング開始")
    logger.info("=" * 80)

    cleaner = LlamaParseOutputCleaner()
    documents = cleaner.clean_documents(documents)

    logger.info(f"クリーニング後のドキュメント数: {len(documents)}")

    # デバッグ出力: クリーニング後のドキュメント内容確認
    for i, doc in enumerate(documents[:3]):
        logger.info(f"\n--- Cleaned Document {i} ---")
        logger.info(f"テキスト長: {len(doc.text)}")
        logger.info(f"最初の300文字:\n{doc.text[:300]}")

    logger.info("=" * 80)

    # オプション: 信頼度スコアのログ出力
    for i, doc in enumerate(documents):
        if hasattr(doc, 'metadata') and 'confidence_score' in doc.metadata:
            score = doc.metadata['confidence_score']
            logger.info(f"Document {i}: confidence={score:.2f}")
            if score < 0.2:
                logger.warning(f"Document {i}: Low confidence detected!")

except Exception as e:
    err_msg = str(e)
    logger.error(f"ファイルの読込に失敗しました: {err_msg}")
    import traceback

    logger.error(traceback.format_exc())
    raise

# ========================================
# ★★★ Layer 2適用: チャンクバリデーション ★★★
# ========================================
logger.info("\n" + "=" * 80)
logger.info("【Layer 2】チャンク生成とバリデーション")
logger.info("=" * 80)

# ★★★ 修正: text_splitterを直接使用 ★★★
raw_nodes = text_splitter.get_nodes_from_documents(documents)
logger.info(f"生成された生ノード数: {len(raw_nodes)}")

# バリデーション適用
validator = SmartChunkValidator(
    min_chars=50,
    min_words=5,
    max_empty_ratio=0.3
)

valid_nodes = validator.filter_nodes(raw_nodes)
logger.info(f"バリデーション後のノード数: {len(valid_nodes)}")

# 全ドキュメントのテキスト総量を確認
total_text_length = sum(len(getattr(node, 'text', '')) for node in valid_nodes)
logger.info(f"有効ノードのテキスト総文字数: {total_text_length}")

if total_text_length < 1000:
    logger.warning("⚠️ テキスト総量が1000文字未満です。")

logger.info("=" * 80)

# ========================================
# ★★★ Layer 3 & 4: KGIndex作成 ★★★
# ========================================
logger.info("\n" + "=" * 80)
logger.info("【Layer 3 & 4】FilteredKnowledgeGraphIndex の作成")
logger.info("=" * 80)

Settings.llm = llm
Settings.embed_model = embed_model
Settings.text_splitter = text_splitter

# graph_store 設定
storage_context = StorageContext.from_defaults(graph_store=graph_store)

# デバッグハンドラーを設定
llama_debug = LlamaDebugHandler(print_trace_on_end=True)
callback_manager = CallbackManager([llama_debug])
Settings.callback_manager = callback_manager

# ★★★ フィルタリング付きインデックスを使用 ★★★
logger.info("🚀 Creating FilteredKnowledgeGraphIndex...")
index = FilteredKnowledgeGraphIndex.from_documents(
    documents,
    llm=llm,
    embed_model=embed_model,
    storage_context=storage_context,
    max_triplets_per_chunk=10,
    include_embeddings=True,
    show_progress=True
)
logger.info("✅ Index creation complete!")

# 必要ならローカルにも永続化（設定・メタ等）
persist_dir="/app/storage_kg"

# ★重要: ディレクトリが存在するか確認
Path(persist_dir).mkdir(parents=True, exist_ok=True)

# ★重要: 明示的に永続化
try:
    print(f"💾 {persist_dir} に保存中...")
    storage_context.persist(persist_dir=persist_dir)
    print(f"✅ 保存完了")

    # ★デバッグ: 保存されたファイルを確認
    files = os.listdir(persist_dir)
    print(f"📁 保存されたファイル: {files}")

    # ★デバッグ: 各ファイルのサイズも確認
    for file in files:
        file_path = os.path.join(persist_dir, file)
        size = os.path.getsize(file_path)
        print(f"  - {file}: {size} bytes")

except Exception as e:
    print(f"❌ 保存エラー: {e}")
    import traceback

    traceback.print_exc()

print("\n🎉 登録処理が完了しました！")

logger.info("✅ Ingest finished.!")

logger.info("=" * 80)

# ========================================
# インデックス作成後の確認
# ========================================
logger.info("\n" + "=" * 80)
logger.info("【検証】KnowledgeGraphIndex 作成後の確認")
logger.info("=" * 80)

try:
    docstore = index.docstore
    all_nodes = list(docstore.docs.values()) if hasattr(docstore, 'docs') else []
    logger.info(f"インデックス内のノード総数: {len(all_nodes)}")

    # 最初の3ノードの内容を確認
    for i, node in enumerate(all_nodes[:3]):
        node_text = getattr(node, 'text', '') or getattr(node, 'get_content', lambda: '')()
        logger.info(f"\n--- Node {i} ---")
        logger.info(f"ノードID: {getattr(node, 'id_', 'N/A')}")
        logger.info(f"テキスト長: {len(node_text)}")
        logger.info(f"テキスト (最初の200文字): {node_text[:200]}")

        # 品質チェック
        if not node_text.strip():
            logger.error(f"❌ Node {i} は空です！（これは発生してはいけません）")
        elif node_text.strip().replace('.', '').replace(' ', '').isdigit():
            logger.error(f"❌ Node {i} は数値のみです！（これは発生してはいけません）")
        else:
            logger.info(f"✓ Node {i} は正常です")

except Exception as e:
    logger.error(f"ノード情報の取得に失敗: {e}")

logger.info("=" * 80)

