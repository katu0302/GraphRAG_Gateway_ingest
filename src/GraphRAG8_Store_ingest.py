# ============================================================
# PropertyGraphIndex版 - 高品質ナレッジグラフ構築システム
# 4層フィルタリング統合版（PropertyGraphIndex対応）
# ============================================================

# ============================================================
# 標準ライブラリのインポート
# ============================================================
import logging
import os
import sys
import time
from pathlib import Path
import re
import yaml  # ★追加
from pydantic import Field
from typing import Optional, Dict, Any, List, Tuple, Sequence, Callable
from dotenv import load_dotenv

# ★★★ PropertyGraphIndex用インポート ★★★
from llama_index.core import PropertyGraphIndex
from llama_index.core import SimpleDirectoryReader, StorageContext
from llama_index.core import Document
from llama_index.core.node_parser import SentenceSplitter
from llama_index.core.schema import BaseNode, TextNode
from llama_index.core.llms import LLM

from neo4j import GraphDatabase, exceptions

from llama_index.llms.openai import utils
from llama_index.llms.openai import OpenAI
from llama_index.embeddings.openai import OpenAIEmbedding
from llama_index.core import Settings
from llama_index.core.postprocessor import SimilarityPostprocessor
from llama_index.core.postprocessor.metadata_replacement import MetadataReplacementPostProcessor

# ★★★ PropertyGraphIndex用の追加インポート ★★★
from llama_index.core.indices.property_graph import SimpleLLMPathExtractor
from llama_index.core.callbacks import CallbackManager, LlamaDebugHandler

# ログレベルの設定
logging.basicConfig(stream=sys.stdout, level=logging.INFO, force=True)
logger = logging.getLogger()
logger.addHandler(logging.StreamHandler(sys.stdout))

# ========================================
# Graph Store imports（条件付きインポート）
# ========================================

# Neo4j: PropertyGraph対応版を優先
try:
    from llama_index.graph_stores.neo4j import Neo4jPropertyGraphStore
except ImportError:
    Neo4jPropertyGraphStore = None
    logger.warning(
        "⚠️ Neo4jPropertyGraphStore が利用できません。\n"
        "   インストール: pip install llama-index-graph-stores-neo4j"
    )

# FalkorDB: PropertyGraph対応版があるか確認
try:
    from llama_index.graph_stores.falkordb import FalkorDBPropertyGraphStore
    FalkorDBGraphStore = FalkorDBPropertyGraphStore  # PropertyGraph版を優先
except ImportError:
    try:
        # フォールバック: 従来版
        from llama_index.graph_stores.falkordb import FalkorDBGraphStore
    except ImportError:
        FalkorDBGraphStore = None

# Kuzu: PropertyGraph対応版があるか確認
try:
    from llama_index.graph_stores.kuzu import KuzuPropertyGraphStore
    KuzuGraphStore = KuzuPropertyGraphStore  # PropertyGraph版を優先
except ImportError:
    try:
        # フォールバック: 従来版
        from llama_index.graph_stores.kuzu import KuzuGraphStore
    except ImportError:
        KuzuGraphStore = None

# TiDB: PropertyGraph対応版があるか確認
try:
    from llama_index.graph_stores.tidb import TiDBPropertyGraphStore
    TiDBGraphStore = TiDBPropertyGraphStore  # PropertyGraph版を優先
except ImportError:
    try:
        # フォールバック: 従来版
        from llama_index.graph_stores.tidb import TiDBGraphStore
    except ImportError:
        TiDBGraphStore = None

# Simple: PropertyGraph対応版が別クラス
# try:
#     from llama_index.core.graph_stores import SimplePropertyGraphStore
# except ImportError:
#     try:
#         # フォールバック: 従来版（PropertyGraphIndexとは非互換）
#         from llama_index.graph_stores.simple import SimpleGraphStore
#         SimplePropertyGraphStore = SimpleGraphStore
#         logger.warning(
#             "⚠️ SimplePropertyGraphStore が見つかりません。SimpleGraphStore を使用します。\n"
#             "   PropertyGraphIndex との互換性問題が発生する可能性があります。"
#         )
#     except ImportError:
#         SimplePropertyGraphStore = None
#         logger.warning("⚠️ SimpleGraphStore が利用できません。")


# ========================================
# 設定ファイルの読み込み (config.yaml)
# ========================================
CONFIG_PATH = "/app/config.yaml"
try:
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
    if config is None:
        config = {}
    logging.info(f"✅ 設定ファイルを読み込みました: {CONFIG_PATH}")
except FileNotFoundError:
    logging.error(f"❌ 設定ファイルが見つかりません: {CONFIG_PATH}")
    config = {}
except Exception as e:
    logging.error(f"❌ 設定ファイルの読み込み中にエラーが発生しました: {e}")
    config = {}

# ========================================
# リトライ用デコレータ（クラス定義の外に配置）
# ========================================
from functools import wraps

# デフォルト値を定数として定義
# DEFAULT_MAX_RETRIES = int(os.getenv("GRAPH_STORE_MAX_RETRIES", "5"))
# DEFAULT_RETRY_DELAY = int(os.getenv("GRAPH_STORE_RETRY_DELAY", "5"))
# DEFAULT_BACKOFF_FACTOR = float(os.getenv("GRAPH_STORE_BACKOFF_FACTOR", "2.0"))

# ★ os.getenv から config.get へ変更
DEFAULT_MAX_RETRIES = int(config.get("GRAPH_STORE_MAX_RETRIES", 5))
DEFAULT_RETRY_DELAY = int(config.get("GRAPH_STORE_RETRY_DELAY", 5))
DEFAULT_BACKOFF_FACTOR = float(config.get("GRAPH_STORE_BACKOFF_FACTOR", 2.0))

def _retry_with_backoff(
        operation_name: str,
        max_retries: int = None,
        retry_delay: int = None,
        backoff_factor: float = None,
        retriable_exceptions: tuple = (Exception,)
):
    """
    リトライロジック付きデコレータ（指数バックオフ対応）

    Args:
        operation_name: 操作名（ログ出力用）
        max_retries: 最大リトライ回数
        retry_delay: 初回リトライ待機時間（秒）
        backoff_factor: バックオフ係数（2.0なら2倍ずつ増加）
        retriable_exceptions: リトライ対象の例外タプル
    """
    # デフォルト値を設定
    if max_retries is None:
        max_retries = DEFAULT_MAX_RETRIES
    if retry_delay is None:
        retry_delay = DEFAULT_RETRY_DELAY
    if backoff_factor is None:
        backoff_factor = DEFAULT_BACKOFF_FACTOR

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            current_delay = retry_delay

            for attempt in range(max_retries):
                try:
                    logger.info(f"🔄 {operation_name} 接続試行 ({attempt + 1}/{max_retries})...")
                    result = func(*args, **kwargs)
                    logger.info(f"✅ {operation_name} 接続成功")
                    return result

                except retriable_exceptions as e:
                    last_exception = e

                    if attempt < max_retries - 1:
                        logger.warning(
                            f"⚠️ {operation_name} 接続失敗 (試行 {attempt + 1}/{max_retries}): {e}"
                        )
                        logger.info(f"   {current_delay}秒後に再試行します...")
                        time.sleep(current_delay)

                        # 指数バックオフ
                        current_delay = int(current_delay * backoff_factor)
                    else:
                        logger.error(
                            f"❌ {operation_name} 接続失敗 (最大試行回数到達): {e}"
                        )

            # すべてのリトライが失敗
            logger.error(f"❌ {operation_name} 初期化に失敗しました")
            raise last_exception

        return wrapper

    return decorator


# ========================================
# Graph Store Factory（リトライロジック付き）
# ========================================
class GraphStoreFactory:
    """グラフストレージの抽象化ファクトリ（リトライ機能付き）"""

    SUPPORTED_STORES = {
        'neo4j': {
            'name': 'Neo4j',
            'package': 'llama-index-graph-stores-neo4j',
            'description': 'PropertyGraph対応グラフデータベース（Cypher）',
            'property_graph_support': True
        },
        'falkordb': {
            'name': 'FalkorDB',
            'package': 'llama-index-graph-stores-falkordb',
            'description': 'Redis上で動作する高速グラフデータベース（Cypher）',
            'property_graph_support': 'auto'  # 自動検出
        },
        'kuzu': {
            'name': 'Kuzu',
            'package': 'llama-index-graph-stores-kuzu',
            'description': '組み込み型グラフデータベース（Cypher）',
            'property_graph_support': 'auto'  # 自動検出
        },
        'tidb': {
            'name': 'TiDB',
            'package': 'llama-index-graph-stores-tidb',
            'description': '分散SQL + グラフ機能を持つハイブリッドデータベース',
            'property_graph_support': 'auto'  # 自動検出
        },
#        'simple': {
#            'name': 'SimplePropertyGraphStore',
#            'package': 'llama-index-core',
#            'description': 'PropertyGraph対応メモリ内グラフストア（開発・小規模用）',
#            'property_graph_support': True
#        }
    }

    # クラス変数としてリトライ設定を保持
    DEFAULT_MAX_RETRIES = DEFAULT_MAX_RETRIES
    DEFAULT_RETRY_DELAY = DEFAULT_RETRY_DELAY
    DEFAULT_BACKOFF_FACTOR = DEFAULT_BACKOFF_FACTOR

    @staticmethod
    def create_graph_store(store_type: str, config: dict):
        """GraphStoreインスタンスを生成"""
        store_type = store_type.lower()

        if store_type not in GraphStoreFactory.SUPPORTED_STORES:
            raise ValueError(f"Unsupported graph store type: {store_type}")

        store_info = GraphStoreFactory.SUPPORTED_STORES[store_type]
        logger.info("=" * 80)
        logger.info(f"📊 {store_info['name']} Graph Store 初期化開始")
        logger.info(f"   タイプ: {store_info['description']}")
        logger.info(f"   最大リトライ回数: {DEFAULT_MAX_RETRIES}")
        logger.info(f"   初回リトライ待機: {DEFAULT_RETRY_DELAY}秒")
        logger.info("=" * 80)

        try:
            if store_type == 'neo4j':
                graph_store = GraphStoreFactory._create_neo4j(config)
            elif store_type == 'falkordb':
                graph_store = GraphStoreFactory._create_falkordb(config)
            elif store_type == 'kuzu':
                graph_store = GraphStoreFactory._create_kuzu(config)
            elif store_type == 'tidb':
                graph_store = GraphStoreFactory._create_tidb(config)
#            elif store_type == 'simple':
#                graph_store = GraphStoreFactory._create_simple(config)

            # PropertyGraph対応チェック
            logger.info("=" * 80)
            logger.info("🔍 PropertyGraph対応チェック")
            logger.info("=" * 80)
            GraphStoreFactory.check_property_graph_support(graph_store)
            logger.info("=" * 80)

            return graph_store

        except Exception as e:
            logger.error(f"❌ GraphStore初期化に失敗しました: {e}", exc_info=True)
            raise

    @staticmethod
    @_retry_with_backoff(
        operation_name="Neo4j",
        retriable_exceptions=(ValueError, Exception)
    )
    def _create_neo4j(config: dict):
        """Neo4jPropertyGraphStoreの作成（リトライ付き）"""

        # Neo4jPropertyGraphStoreが利用できない場合のチェック
        if Neo4jPropertyGraphStore is None:
            raise ImportError(
                "Neo4j support not installed.\n"
                "Install with: pip install llama-index-graph-stores-neo4j"
            )

        logger.info(f"   接続先: {config.get('url')}")
        logger.info(f"   データベース: {config.get('database', 'neo4j')}")
        logger.info(f"   ユーザー: {config.get('user')}")

        graph_store = Neo4jPropertyGraphStore(
            username=config.get('user'),
            password=config.get('password'),
            url=config.get('url'),
            database=config.get('database', 'neo4j')
        )

        logger.info("   接続テスト実行中...")

        # 接続テスト
        if hasattr(graph_store, '_driver'):
            database = config.get('database', 'neo4j')
            with graph_store._driver.session(database=database) as session:
                result = session.run("RETURN 1 as test")
                _ = result.single()

        return graph_store

    @staticmethod
    @_retry_with_backoff(
        operation_name="FalkorDB",
        retriable_exceptions=(Exception,)
    )
    def _create_falkordb(config: dict):
        """FalkorDBGraphStoreの作成（リトライ付き）"""
        if FalkorDBGraphStore is None:
            raise ImportError(
                "FalkorDB support not installed.\n"
                "Install with: pip install llama-index-graph-stores-falkordb redis"
            )

        url = config.get('url', 'redis://graphrag-falkordb:6379')
        graph_name = config.get('graph_name', 'knowledge_graph')

        logger.info(f"   接続先: {url}")
        logger.info(f"   グラフ名: {graph_name}")

        graph_store = FalkorDBGraphStore(
            url=url,
            graph_name=graph_name,
            decode_responses=True
        )

        logger.info("   接続テスト実行中...")
        return graph_store

    @staticmethod
    @_retry_with_backoff(
        operation_name="Kuzu",
        max_retries=3,
        retry_delay=2,
        retriable_exceptions=(OSError, IOError, Exception)
    )
    def _create_kuzu(config: dict):
        """KuzuGraphStoreの作成（リトライ付き）"""
        if KuzuGraphStore is None:
            raise ImportError(
                "Kuzu support not installed.\n"
                "Install with: pip install llama-index-graph-stores-kuzu kuzu"
            )

        db_path = config.get('db_path', '/app/storage_kg/kuzu_db')
        logger.info(f"   データベースパス: {db_path}")

        os.makedirs(db_path, exist_ok=True)
        logger.info(f"   ディレクトリ確認完了: {db_path}")

        graph_store = KuzuGraphStore(database_path=db_path)
        logger.info("   データベース初期化完了")
        return graph_store

    @staticmethod
    @_retry_with_backoff(
        operation_name="TiDB",
        retriable_exceptions=(Exception,)
    )
    def _create_tidb(config: dict):
        """TiDBGraphStoreの作成（リトライ付き）"""
        if TiDBGraphStore is None:
            raise ImportError(
                "TiDB support not installed.\n"
                "Install with: pip install llama-index-graph-stores-tidb pymysql"
            )

        host = config.get('host', 'graphrag-tidb')
        port = config.get('port', 4000)
        database = config.get('database', 'graphrag')

        logger.info(f"   接続先: {host}:{port}")
        logger.info(f"   データベース: {database}")
        logger.info(f"   ユーザー: {config.get('user', 'root')}")

        graph_store = TiDBGraphStore(
            host=host,
            port=port,
            user=config.get('user', 'root'),
            password=config.get('password', ''),
            database=database,
            table_name=config.get('table_name', 'knowledge_graph')
        )

        logger.info("   接続テスト実行中...")
        return graph_store

#    @staticmethod
#    @_retry_with_backoff(
#        operation_name="SimplePropertyGraphStore",
#        max_retries=3,
#        retry_delay=1,
#        retriable_exceptions=(OSError, IOError, Exception)
#    )
#    def _create_simple(config: dict):
#        """SimplePropertyGraphStoreの作成（リトライ付き）"""
#
#        if SimplePropertyGraphStore is None:
#            raise ImportError(
#                "SimplePropertyGraphStore not available in your LlamaIndex version.\n"
#                "Update with: pip install --upgrade llama-index-core"
#            )
#
#        logger.info("   PropertyGraph対応メモリ内グラフストアを使用")
#
#        storage_dir = config.get('storage_dir', '/app/storage_kg')
#        if storage_dir:
#            os.makedirs(storage_dir, exist_ok=True)
#            logger.info(f"   ストレージディレクトリ: {storage_dir}")
#
#        graph_store = SimplePropertyGraphStore()
#        logger.info("   グラフストア初期化完了")
#        return graph_store

    # ========== データクリア関連メソッド ==========

    @staticmethod
    def clear_graph_data(store_type: str, config: dict):
        """Graph Storeのデータをクリア"""
        store_type = store_type.lower()

        logger.info("=" * 80)
        logger.info(f"🗑️  {store_type.upper()} Graph Store データクリア開始")
        logger.info("=" * 80)

        try:
            # 一時的にGraph Storeを作成してクリア
            graph_store = GraphStoreFactory.create_graph_store(store_type, config)

            if store_type == 'neo4j':
                GraphStoreFactory._clear_neo4j(graph_store)
            elif store_type == 'falkordb':
                GraphStoreFactory._clear_falkordb(graph_store)
            elif store_type == 'kuzu':
                GraphStoreFactory._clear_kuzu(graph_store)
            elif store_type == 'tidb':
                GraphStoreFactory._clear_tidb(graph_store)
#            elif store_type == 'simple':
#                GraphStoreFactory._clear_simple(graph_store)
            else:
                logger.warning(f"⚠️ 未対応のストアタイプ: {store_type}")
                return

            logger.info("=" * 80)
            logger.info(f"✅ {store_type.upper()} データクリア完了")
            logger.info("=" * 80)

        except Exception as e:
            logger.error(f"❌ データクリアエラー ({store_type}): {e}", exc_info=True)
            raise

    @staticmethod
    def _clear_neo4j(graph_store):
        """Neo4jPropertyGraphStoreのノードとリレーションを削除"""
        logger.info("   Neo4jのノードとリレーションを削除中...")

        try:
            # 🔧 修正: Neo4jPropertyGraphStoreは_driver属性を持っている
            if hasattr(graph_store, '_driver'):
                driver = graph_store._driver
                database = getattr(graph_store, 'database', 'neo4j')

                logger.debug(f"   データベース: {database}")

                with driver.session(database=database) as session:
                    # バッチ削除（大量データに対応）
                    batch_size = 10000
                    deleted_total = 0

                    while True:
                        result = session.run(
                            f"""
                            MATCH (n)
                            WITH n LIMIT {batch_size}
                            DETACH DELETE n
                            RETURN count(n) as deleted
                            """
                        )
                        record = result.single()
                        deleted = record["deleted"] if record else 0
                        deleted_total += deleted

                        if deleted == 0:
                            break

                        if deleted_total > 0 and deleted_total % 10000 == 0:
                            logger.debug(f"   削除済み: {deleted_total:,}ノード")

                    logger.info(f"   ✅ 全ノードとリレーションを削除しました（{deleted_total:,}ノード）")
            else:
                # フォールバック: 旧方式
                logger.warning("   ⚠️ _driver属性が見つかりません。フォールバック方式を使用します")

                from neo4j import GraphDatabase

                url = getattr(graph_store, 'url', None) or getattr(graph_store, '_url', None)
                username = getattr(graph_store, 'username', None) or getattr(graph_store, '_username', None)
                password = getattr(graph_store, 'password', None) or getattr(graph_store, '_password', None)
                database = getattr(graph_store, 'database', None) or getattr(graph_store, '_database', 'neo4j')

                if not all([url, username, password]):
                    logger.error("❌ Neo4j接続情報が取得できません")
                    raise ValueError("Neo4j接続情報が不足しています")

                driver = GraphDatabase.driver(url, auth=(username, password))

                try:
                    with driver.session(database=database) as session:
                        batch_size = 10000
                        deleted_total = 0

                        while True:
                            result = session.run(
                                f"""
                                MATCH (n)
                                WITH n LIMIT {batch_size}
                                DETACH DELETE n
                                RETURN count(n) as deleted
                                """
                            )
                            record = result.single()
                            deleted = record["deleted"] if record else 0
                            deleted_total += deleted

                            if deleted == 0:
                                break

                            if deleted_total > 0 and deleted_total % 10000 == 0:
                                logger.debug(f"   削除済み: {deleted_total:,}ノード")

                        logger.info(f"   ✅ 全ノードとリレーションを削除しました（{deleted_total:,}ノード）")
                finally:
                    driver.close()

        except Exception as e:
            logger.error(f"   ❌ Neo4jデータクリアエラー: {e}")
            raise

    @staticmethod
    def _clear_falkordb(graph_store):
        """FalkorDBのデータをクリア"""
        logger.info("   FalkorDBのグラフデータを削除中...")

        try:
            if hasattr(graph_store, 'client'):
                graph_name = graph_store._graph_name if hasattr(graph_store, '_graph_name') else 'knowledge_graph'

                try:
                    graph_store.client.delete(graph_name)
                    logger.info(f"   ✅ グラフ '{graph_name}' を削除しました")
                except Exception as e:
                    logger.warning(f"   ⚠️ グラフ削除スキップ: {e}")

        except Exception as e:
            logger.error(f"   ❌ FalkorDBクリアエラー: {e}")
            raise

    @staticmethod
    def _clear_kuzu(graph_store):
        """Kuzuのデータをクリア"""
        logger.info("   Kuzuのデータベースをクリア中...")

        try:
            if hasattr(graph_store, '_database'):
                conn = graph_store._database

                try:
                    node_tables = conn.execute("CALL show_tables() RETURN *;").get_as_df()

                    for _, row in node_tables.iterrows():
                        table_name = row['name']
                        table_type = row['type']

                        try:
                            if table_type in ['NODE', 'REL']:
                                conn.execute(f"DROP TABLE {table_name};")
                                logger.info(f"   🗑️  テーブル削除: {table_name}")
                        except Exception as e:
                            logger.warning(f"   ⚠️ テーブル削除スキップ ({table_name}): {e}")

                    logger.info("   ✅ Kuzuデータベースをクリアしました")

                except Exception as e:
                    logger.warning(f"   ⚠️ テーブルリスト取得エラー: {e}")

        except Exception as e:
            logger.error(f"   ❌ Kuzuクリアエラー: {e}")
            raise

    @staticmethod
    def _clear_tidb(graph_store):
        """TiDBのデータをクリア"""
        logger.info("   TiDBのテーブルデータを削除中...")

        try:
            if hasattr(graph_store, '_conn'):
                cursor = graph_store._conn.cursor()
                table_name = graph_store._table_name if hasattr(graph_store, '_table_name') else 'knowledge_graph'

                cursor.execute(f"TRUNCATE TABLE {table_name}")
                graph_store._conn.commit()

                logger.info(f"   ✅ テーブル '{table_name}' のデータを削除しました")

        except Exception as e:
            logger.error(f"   ❌ TiDBクリアエラー: {e}")
            raise

#    @staticmethod
#    def _clear_simple(graph_store: SimpleGraphStore):
#        """SimpleGraphStoreのデータをクリア"""
#        logger.info("   SimpleGraphStoreのメモリデータをクリア中...")
#
#        try:
#            graph_store.__init__()
#            logger.info("   ✅ SimpleGraphStore を再初期化しました")
#
#        except Exception as e:
#            logger.error(f"   ❌ SimpleGraphStoreクリアエラー: {e}")
#            raise

    @staticmethod
    def check_property_graph_support(graph_store) -> bool:
        """
        GraphStoreがPropertyGraphIndexに対応しているか確認

        Args:
            graph_store: チェックするgraph store

        Returns:
            bool: PropertyGraph対応の場合True
        """
        # supports_vector_queries属性の存在をチェック
        if hasattr(graph_store, 'supports_vector_queries'):
            logger.info(f"   ✅ PropertyGraph対応: {type(graph_store).__name__}")
            return True
        else:
            logger.warning(
                f"   ⚠️ PropertyGraph非対応: {type(graph_store).__name__}\n"
                f"   PropertyGraphIndexで問題が発生する可能性があります"
            )
            return False


# ========================================
# Layer 1: LlamaParseクリーニング層
# ========================================
class LlamaParseOutputCleaner:
    """LlamaParseの出力を高品質化するクリーニング層"""

    # ログから検出された不要パターン
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
        r'^[\d\.]+\s*$',  # "1.", "2." だけの行
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

        # 番号リストブロック全体を削除（行単位でなくブロック単位）
        text = re.sub(
            r'^\s*\d+[\.\)]\s*\n(?:^\s*\d+[\.\)]\s*\n){2,}',  # 3行以上連続
            '',
            text,
            flags=re.MULTILINE
        )
        # + 単独番号行も個別に削除
        text = re.sub(r'^\s*\d+[\.\)]\s*$', '', text, flags=re.MULTILINE)

        # Step 2: 連続空行の正規化 (3行以上 → 2行)
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
        if len(text) < 30:
            logger.warning(f"Document too short after cleaning: {len(text)} chars")
            return None

        # クリーニング済みドキュメント作成
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
        if len(text.strip()) < self.min_chars:
            return False, f"Too short: {len(text)} chars"

        # Check 2: 単語数チェック
        words = [w for w in text.split() if len(w) > 1]
        if len(words) < self.min_words:
            return False, f"Too few words: {len(words)}"

        # Check 3: 空白文字比率
        text_chars = len(text)
        whitespace_chars = text.count(' ') + text.count('\n') + text.count('\t')
        empty_ratio = whitespace_chars / text_chars if text_chars > 0 else 1.0

        if empty_ratio > self.max_empty_ratio:
            return False, f"Too much whitespace: {empty_ratio:.1%}"

        # Check 4: 数字のみチェック
        if text.strip().replace('.', '').replace(' ', '').isdigit():
            return False, "Numbers only"

        # Check 5: 記号のみチェック
        symbols_only = all(not c.isalnum() for c in text.strip())
        if symbols_only:
            return False, "Symbols only"

        # Check 6: エラーメッセージ検出
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

        # Check 0: 空文字列チェック（強化版）
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

        # Check 4: 単独"Is系述語"をブロック
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
# PropertyGraphIndex用: カスタムフィルタリング Extractor
# ========================================
class FilteredLLMPathExtractor(SimpleLLMPathExtractor):
    """
    SimpleLLMPathExtractorの拡張版（4層品質フィルタリング統合）
    - Layer 3: トリプレット品質フィルター（高品質なグラフデータのみを抽出）

    フィルタリング戦略:
    - _aextract/_extractはオーバーライドせず、親クラスの実装を使用
    - acall/__call__メソッドで抽出結果にフィルタリングを適用
    """

    # ========================================
    # Pydanticフィールド定義
    # ========================================
    min_confidence: float = Field(
        default=0.7,
        description="トリプレットの最小信頼度（品質フィルタ用）"
    )
    min_description_length: int = Field(
        default=10,
        description="説明文の最小長（品質フィルタ用）"
    )
    show_progress: bool = Field(
        default=False,
        description="プログレス表示フラグ（将来的な使用のため）"
    )

    def __init__(
            self,
            llm: Optional[LLM] = None,
            extract_prompt: Optional[str] = None,
            parse_fn: Optional[Callable] = None,
            max_paths_per_chunk: int = 10,
            num_workers: int = 4,
            show_progress: bool = False,
            min_confidence: float = 0.7,
            min_description_length: int = 10,
            **kwargs
    ):
        """
        初期化メソッド

        Args:
            llm: 使用する言語モデル
            extract_prompt: 抽出用プロンプト
            parse_fn: パース関数
            max_paths_per_chunk: チャンクあたりの最大パス数
            num_workers: ワーカー数
            show_progress: プログレス表示（SimpleLLMPathExtractorは非対応）
            min_confidence: 最小信頼度（品質フィルタ用）
            min_description_length: 最小説明文長（品質フィルタ用）
            **kwargs: 追加のパラメータ
        """
        # ========================================
        # 親クラスに渡す引数を準備
        # ========================================
        parent_kwargs = {
            'llm': llm,
            'extract_prompt': extract_prompt,
            'parse_fn': parse_fn,
            'max_paths_per_chunk': max_paths_per_chunk,
            'num_workers': num_workers
        }

        # Noneでない値のみを渡す
        parent_kwargs = {k: v for k, v in parent_kwargs.items() if v is not None}

        # ========================================
        # 親クラスの初期化
        # ========================================
        super().__init__(**parent_kwargs)

        # ========================================
        # 品質フィルタ設定を明示的に設定
        # ========================================
        # Pydanticの制約を回避してフィールドに値を設定
        object.__setattr__(self, 'min_confidence', min_confidence)
        object.__setattr__(self, 'min_description_length', min_description_length)
        object.__setattr__(self, 'show_progress', show_progress)

        # ========================================
        # 初期化完了ログ
        # ========================================
        logger.info("=" * 80)
        logger.info("✅ FilteredLLMPathExtractor初期化完了（Layer 3品質フィルタ）")
        logger.info(f"   最小信頼度: {self.min_confidence}")
        logger.info(f"   最小説明文長: {self.min_description_length}")
        logger.info(f"   プログレス表示: {self.show_progress}")
        logger.info(f"   max_paths_per_chunk: {max_paths_per_chunk}")
        logger.info(f"   num_workers: {num_workers}")
        logger.info("=" * 80)

    # ========================================
    # 非同期バッチ処理メソッド（PropertyGraphIndexから呼ばれる）
    # ========================================
    async def acall(
            self,
            nodes: List[BaseNode],
            **kwargs: Any
    ) -> List[BaseNode]:
        """
        非同期バッチ処理でノードからグラフ要素を抽出（品質フィルタ適用）

        このメソッドはPropertyGraphIndexから呼び出されます。
        親クラスの抽出処理を実行した後、結果にフィルタリングを適用します。

        Args:
            nodes: 処理対象のノードリスト
            **kwargs: 追加のパラメータ

        Returns:
            List[BaseNode]: フィルタリング済みのノードリスト
        """
        logger.info("=" * 80)
        logger.info("🔍 Layer 3: トリプレット品質フィルタリング開始（非同期）")
        logger.info(f"   入力ノード数: {len(nodes)}")
        logger.info("=" * 80)

        try:
            # ========================================
            # 親クラスの抽出処理を実行
            # ========================================
            extracted_nodes = await super().acall(nodes, **kwargs)

            # 型チェック
            if not isinstance(extracted_nodes, list):
                logger.warning(f"⚠️ 予期しない戻り値の型: {type(extracted_nodes)}")
                extracted_nodes = [extracted_nodes] if extracted_nodes else []

            logger.info(f"📊 抽出されたノード数: {len(extracted_nodes)}")

            # ========================================
            # 品質フィルタリングを適用
            # ========================================
            filtered_nodes = self._filter_nodes_by_quality(extracted_nodes)

            # ========================================
            # 統計情報を出力
            # ========================================
            filtered_count = len(filtered_nodes)
            removed_count = len(extracted_nodes) - filtered_count
            removal_rate = (removed_count / len(extracted_nodes) * 100) if extracted_nodes else 0

            logger.info("=" * 80)
            logger.info("✅ Layer 3フィルタリング完了（非同期）")
            logger.info(f"   フィルタ前: {len(extracted_nodes)}ノード")
            logger.info(f"   フィルタ後: {filtered_count}ノード")
            logger.info(f"   除外数: {removed_count}ノード ({removal_rate:.1f}%)")
            logger.info("=" * 80)

            return filtered_nodes

        except Exception as e:
            logger.error("=" * 80)
            logger.error(f"❌ トリプレット抽出エラー（非同期）: {e}")
            logger.error("=" * 80)
            logger.error("スタックトレース:", exc_info=True)
            # エラー時は空リストを返す
            return []

    # ========================================
    # 同期バッチ処理メソッド（フォールバック用）
    # ========================================
    def __call__(
            self,
            nodes: List[BaseNode],
            **kwargs: Any
    ) -> List[BaseNode]:
        """
        同期バッチ処理でノードからグラフ要素を抽出（品質フィルタ適用）

        通常はacallが使用されますが、同期処理が必要な場合に使用されます。

        Args:
            nodes: 処理対象のノードリスト
            **kwargs: 追加のパラメータ

        Returns:
            List[BaseNode]: フィルタリング済みのノードリスト
        """
        logger.info("=" * 80)
        logger.info("🔍 Layer 3: トリプレット品質フィルタリング開始（同期）")
        logger.info(f"   入力ノード数: {len(nodes)}")
        logger.info("=" * 80)

        try:
            # ========================================
            # 親クラスの抽出処理を実行
            # ========================================
            extracted_nodes = super().__call__(nodes, **kwargs)

            # 型チェック
            if not isinstance(extracted_nodes, list):
                logger.warning(f"⚠️ 予期しない戻り値の型: {type(extracted_nodes)}")
                extracted_nodes = [extracted_nodes] if extracted_nodes else []

            logger.info(f"📊 抽出されたノード数: {len(extracted_nodes)}")

            # ========================================
            # 品質フィルタリングを適用
            # ========================================
            filtered_nodes = self._filter_nodes_by_quality(extracted_nodes)

            # ========================================
            # 統計情報を出力
            # ========================================
            filtered_count = len(filtered_nodes)
            removed_count = len(extracted_nodes) - filtered_count
            removal_rate = (removed_count / len(extracted_nodes) * 100) if extracted_nodes else 0

            logger.info("=" * 80)
            logger.info("✅ Layer 3フィルタリング完了（同期）")
            logger.info(f"   フィルタ前: {len(extracted_nodes)}ノード")
            logger.info(f"   フィルタ後: {filtered_count}ノード")
            logger.info(f"   除外数: {removed_count}ノード ({removal_rate:.1f}%)")
            logger.info("=" * 80)

            return filtered_nodes

        except Exception as e:
            logger.error("=" * 80)
            logger.error(f"❌ トリプレット抽出エラー（同期）: {e}")
            logger.error("=" * 80)
            logger.error("スタックトレース:", exc_info=True)
            # エラー時は空リストを返す
            return []

    # ========================================
    # フィルタリングロジック
    # ========================================
    def _filter_nodes_by_quality(self, nodes: List[BaseNode]) -> List[BaseNode]:
        """
        品質基準に基づいてノードをフィルタリング

        各ノードのメタデータやプロパティをチェックして、
        品質基準を満たすノードのみを返します。

        Args:
            nodes: フィルタリング対象のノードリスト

        Returns:
            List[BaseNode]: 高品質なノードのみ
        """
        if not nodes:
            logger.debug("フィルタリング対象のノードが空です")
            return []

        filtered = []
        rejection_reasons = {
            'low_confidence': 0,
            'short_description': 0,
            'empty_content': 0,
            'invalid_format': 0,
            'passed': 0
        }

        for idx, node in enumerate(nodes):
            try:
                # ========================================
                # ノードの基本的な妥当性チェック
                # ========================================

                # テキストコンテンツの取得
                if hasattr(node, 'text'):
                    content = node.text
                elif hasattr(node, 'get_content'):
                    content = node.get_content()
                else:
                    content = str(node)

                # 空のコンテンツチェック
                if not content or len(content.strip()) < 5:
                    rejection_reasons['empty_content'] += 1
                    logger.debug(f"   ノード{idx}: 除外（内容が空または短すぎる）")
                    continue

                # ========================================
                # メタデータベースの品質チェック
                # ========================================
                metadata = getattr(node, 'metadata', {})

                # 1. 信頼度チェック（メタデータに存在する場合）
                confidence = metadata.get('confidence', 1.0)
                if confidence < self.min_confidence:
                    rejection_reasons['low_confidence'] += 1
                    logger.debug(f"   ノード{idx}: 除外（信頼度不足）: {confidence:.3f} < {self.min_confidence}")
                    continue

                # 2. 説明文の長さチェック（メタデータに存在する場合）
                description = metadata.get('description', content)
                if len(description) < self.min_description_length:
                    rejection_reasons['short_description'] += 1
                    logger.debug(
                        f"   ノード{idx}: 除外（説明文が短い）: {len(description)}文字 < {self.min_description_length}文字")
                    continue

                # ========================================
                # トリプレット情報のチェック（PropertyGraphノードの場合）
                # ========================================
                if 'triplet' in metadata or 'kg_rel_map' in metadata:
                    # トリプレット情報が存在する場合の詳細チェック
                    triplet_info = metadata.get('triplet', {})

                    if isinstance(triplet_info, dict):
                        subject = triplet_info.get('subject', '')
                        predicate = triplet_info.get('predicate', '')
                        obj = triplet_info.get('object', '')

                        # 空の要素チェック
                        if not subject or not predicate or not obj:
                            rejection_reasons['empty_content'] += 1
                            logger.debug(f"   ノード{idx}: 除外（トリプレット要素が空）")
                            continue

                        # 述語の妥当性チェック
                        if len(str(predicate)) < 2:
                            rejection_reasons['invalid_format'] += 1
                            logger.debug(f"   ノード{idx}: 除外（述語が短すぎる）: {predicate}")
                            continue

                # ========================================
                # すべてのチェックを通過
                # ========================================
                rejection_reasons['passed'] += 1
                filtered.append(node)

            except Exception as e:
                rejection_reasons['invalid_format'] += 1
                logger.warning(f"   ノード{idx}: 処理エラー: {e}")
                continue

        # ========================================
        # フィルタリング統計の出力
        # ========================================
        total_rejected = sum(v for k, v in rejection_reasons.items() if k != 'passed')

        if total_rejected > 0:
            logger.info("📊 フィルタリング統計:")
            logger.info(f"   - 合格: {rejection_reasons['passed']}ノード")
            for reason, count in rejection_reasons.items():
                if reason != 'passed' and count > 0:
                    logger.info(f"   - {reason}: {count}ノード")
        else:
            logger.info(f"📊 全ノードが品質基準を満たしています: {rejection_reasons['passed']}ノード")

        return filtered

    # ========================================
    # 注記: _aextractと_extractはオーバーライドしない
    # ========================================
    # 親クラスのSimpleLLMPathExtractorの_aextractと_extractメソッドを
    # そのまま使用します。フィルタリングはacall/__call__レイヤーで
    # 適用されるため、低レベルメソッドの変更は不要です。


# ========================================
# PropertyGraphIndex作成（フィルタリング付き）
# ========================================
def create_filtered_property_graph_index(
    documents: List[Document],
    llm: LLM,
    embed_model,
    graph_store,  # ← 型ヒントを削除（Neo4jPropertyGraphStoreがNoneの場合があるため）
    storage_context: StorageContext,
    embed_kg_nodes: bool = True,
    show_progress: bool = True,
    num_workers: int = 4
) -> PropertyGraphIndex:
    """
    フィルタリング機能付きPropertyGraphIndexの作成

    Args:
        documents: ドキュメントリスト
        llm: 言語モデル
        embed_model: 埋め込みモデル
        graph_store: グラフストア
        storage_context: ストレージコンテキスト
        show_progress: プログレス表示フラグ
        num_workers: ワーカー数

    Returns:
        PropertyGraphIndex
        :param embed_kg_nodes:
    """
    logger.info("=" * 80)
    logger.info("🚀 Creating FilteredPropertyGraphIndex with custom extractor")
    logger.info("=" * 80)

    # カスタムExtractorの初期化
    kg_extractor = FilteredLLMPathExtractor(
        llm=llm,
        max_paths_per_chunk=10,
        num_workers=num_workers,
        show_progress=show_progress,
        min_confidence=0.7,
        min_description_length=10
    )

    # 🔧 修正: property_graph_storeを明示的に渡す
    # PropertyGraphIndexの作成
    index = PropertyGraphIndex.from_documents(
        documents=documents,
        llm=llm,
        embed_model=embed_model,
        property_graph_store=graph_store,  # ← 明示的にNeo4jGraphStoreを渡す
        kg_extractors=[kg_extractor],
        storage_context=storage_context,  # これも残す（vector store等のため）
        embed_kg_nodes=embed_kg_nodes,
        show_progress=show_progress,
        include_embeddings=True,
    )

    logger.info("=" * 80)
    logger.info("✅ FilteredPropertyGraphIndex creation complete!")
    logger.info("=" * 80)

    return index


# ============================================================
# PropertyGraphIndex作成後の統計情報表示
# ============================================================
def get_graph_statistics_universal(index, graph_store) -> dict:
    """
    全てのGraph Storeから統計情報を取得（汎用版）

    対応ストレージ:
    - Neo4jPropertyGraphStore / Neo4jGraphStore
    - FalkorDB (Redis-based)
    - Kuzu (Embedded)
    - TiDB (Distributed SQL + Graph)
#    - SimplePropertyGraphStore

    Args:
        index: PropertyGraphIndex
        graph_store: 元のgraph_storeインスタンス

    Returns:
        dict: 統計情報
    """
    logger.info("=" * 80)
    logger.info("📊 Graph Store統計情報を取得中...")
    logger.info("=" * 80)

    # ========================================
    # 1. PropertyGraphStoreのタイプを確認
    # ========================================
    property_graph_store = index.property_graph_store
    store_type = type(property_graph_store).__name__
    logger.info(f"Property Graph Store Type: {store_type}")

    statistics = {
        "store_type": store_type,
        "node_count": 0,
        "relationship_count": 0,
        "node_labels": {},
        "relationship_types": {},
        "average_score": None,
        "properties_summary": {},
        "storage_backend": "unknown"
    }

    # ========================================
    # 2. ストレージタイプ別に処理を分岐
    # ========================================
    try:
#        if "SimplePropertyGraphStore" in store_type:
#            statistics = _get_simple_store_statistics(property_graph_store, statistics)
#        elif "Neo4j" in store_type:
        if "Neo4j" in store_type:
            statistics = _get_neo4j_statistics(index, graph_store, property_graph_store, statistics)
        elif "FalkorDB" in store_type or "Falkor" in store_type:
            statistics = _get_falkordb_statistics(property_graph_store, graph_store, statistics)
        elif "Kuzu" in store_type:
            statistics = _get_kuzu_statistics(property_graph_store, graph_store, statistics)
        elif "TiDB" in store_type or "Tidb" in store_type:
            statistics = _get_tidb_statistics(property_graph_store, graph_store, statistics)
        else:
            logger.warning(f"⚠️ 未対応のストアタイプ: {store_type}")
            logger.warning("   基本的な統計情報のみ取得を試みます...")
            statistics = _get_generic_statistics(property_graph_store, statistics)

    except Exception as e:
        logger.error(f"❌ 統計情報取得エラー: {e}", exc_info=True)

    return statistics


# ========================================
# SimplePropertyGraphStore用
# ========================================
#def _get_simple_store_statistics(property_graph_store, statistics: dict) -> dict:
#    """SimplePropertyGraphStoreから統計情報を取得"""
#    logger.info("SimplePropertyGraphStoreからトリプレット情報を取得します...")
#    statistics["storage_backend"] = "memory"
#
#    try:
#        # トリプレット取得
#        triplets = property_graph_store.get_triplets()
#        statistics["triplet_count"] = len(triplets)
#        logger.info(f"   トリプレット総数: {len(triplets)}")
#
#        # エンティティとリレーションの統計
#        entities = set()
#        relations = {}
#
#        for triplet in triplets:
#            # Subject Entity
#            if hasattr(triplet, 'subj'):
#                entities.add(triplet.subj.name if hasattr(triplet.subj, 'name') else str(triplet.subj))
#            # Object Entity
#            if hasattr(triplet, 'obj'):
#                entities.add(triplet.obj.name if hasattr(triplet.obj, 'name') else str(triplet.obj))
#            # Relation
#            if hasattr(triplet, 'rel'):
#                rel_type = triplet.rel.label if hasattr(triplet.rel, 'label') else str(triplet.rel)
#                relations[rel_type] = relations.get(rel_type, 0) + 1
#
#        statistics["entity_count"] = len(entities)
#        statistics["relationship_types"] = relations
#        statistics["relationship_count"] = sum(relations.values())
#        statistics["node_count"] = len(entities)
#
#        logger.info(f"   エンティティ総数: {len(entities)}")
#        logger.info(f"   リレーション総数: {statistics['relationship_count']}")
#        logger.info("✅ SimplePropertyGraphStore統計取得完了")
#
#    except Exception as e:
#        logger.warning(f"⚠️ SimplePropertyGraphStore統計取得エラー: {e}")
#
#    return statistics


# ========================================
# Neo4j用
# ========================================
def _get_neo4j_statistics(index, graph_store, property_graph_store, statistics: dict) -> dict:
    """Neo4j (PropertyGraphStore / GraphStore) から統計情報を取得"""
    logger.info(f"Neo4jベースのストアから統計情報を取得します...")
    statistics["storage_backend"] = "neo4j"

    try:
        # ドライバーまたはクライアントを取得
        driver = None
        database = 'neo4j'

        # 複数の属性名を試す
        for obj in [property_graph_store, graph_store]:
            if driver is None:
                driver = getattr(obj, '_driver', None) or getattr(obj, '_client', None) or getattr(obj, 'driver', None)
            if database == 'neo4j':
                database = getattr(obj, '_database', None) or getattr(obj, 'database', None) or 'neo4j'

        # ドライバーが見つからない場合は新規接続
        should_close_driver = False
        if driver is None:
            logger.info("   既存のドライバーが見つからないため、新規接続を試みます...")
            from neo4j import GraphDatabase

            url = None
            username = None
            password = None

            for obj in [property_graph_store, graph_store]:
                if url is None:
                    url = getattr(obj, '_url', None) or getattr(obj, 'url', None)
                if username is None:
                    username = getattr(obj, '_username', None) or getattr(obj, 'username', None)
                if password is None:
                    password = getattr(obj, '_password', None) or getattr(obj, 'password', None)

            if not all([url, username, password]):
                logger.error("❌ Neo4j接続情報が取得できません")
                return statistics

            driver = GraphDatabase.driver(url, auth=(username, password))
            should_close_driver = True

        # 統計情報をクエリで取得
        try:
            with driver.session(database=database) as session:
                # ノード総数
                result = session.run("MATCH (n) RETURN count(n) as count")
                record = result.single()
                if record:
                    statistics["node_count"] = record["count"]

                # リレーション総数
                result = session.run("MATCH ()-[r]->() RETURN count(r) as count")
                record = result.single()
                if record:
                    statistics["relationship_count"] = record["count"]

                # ラベル別ノード数
                result = session.run(
                    """
                    MATCH (n)
                    UNWIND labels(n) as label
                    RETURN label, count(*) as count
                    ORDER BY count DESC
                    """
                )
                for record in result:
                    label = record["label"]
                    count = record["count"]
                    statistics["node_labels"][label] = statistics["node_labels"].get(label, 0) + count

                # リレーションタイプ別数
                result = session.run(
                    """
                    MATCH ()-[r]->()
                    RETURN type(r) as type, count(*) as count
                    ORDER BY count DESC
                    """
                )
                for record in result:
                    rel_type = record["type"]
                    count = record["count"]
                    statistics["relationship_types"][rel_type] = count

                # プロパティサマリー
                result = session.run(
                    """
                    MATCH (n)
                    UNWIND keys(n) as key
                    RETURN key, count(*) as count
                    ORDER BY count DESC
                    LIMIT 10
                    """
                )
                for record in result:
                    key = record["key"]
                    count = record["count"]
                    statistics["properties_summary"][key] = count

                # 平均信頼度スコア
                try:
                    result = session.run(
                        """
                        MATCH (n)
                        WHERE n.score IS NOT NULL
                        RETURN avg(n.score) as avg_score, count(n) as count
                        """
                    )
                    record = result.single()
                    if record and record["count"] > 0:
                        statistics["average_score"] = record["avg_score"]
                        statistics["nodes_with_score"] = record["count"]
                except Exception:
                    pass

        finally:
            if should_close_driver:
                driver.close()

        logger.info("✅ Neo4j統計情報の取得完了")

        if statistics["node_count"] == 0:
            logger.warning("⚠️ Neo4jにノードが登録されていません")

    except Exception as e:
        logger.error(f"❌ Neo4j統計情報取得エラー: {e}", exc_info=True)

    return statistics


# ========================================
# FalkorDB用（修正版）
# ========================================
def _get_falkordb_statistics(property_graph_store, graph_store, statistics: dict) -> dict:
    """FalkorDB (Redis-based graph) から統計情報を取得"""
    logger.info("FalkorDBから統計情報を取得します...")
    statistics["storage_backend"] = "falkordb"

    try:
        # FalkorDB専用ライブラリをインポート
        try:
            import falkordb
            use_falkordb = True
            logger.debug("   falkordbライブラリを使用")
        except ImportError:
            try:
                # フォールバック: 古いredisgraphライブラリ
                import redis
                from redisgraph import Graph
                use_falkordb = False
                logger.debug("   redisgraphライブラリを使用（レガシー）")
            except ImportError:
                logger.error("❌ falkordbまたはredisgraphライブラリがインストールされていません")
                logger.error("   pip install falkordb を実行してください")
                return statistics

        # 接続情報を取得
        host = 'localhost'
        port = 6379
        password = None
        graph_name = "knowledge_graph"

        for obj in [property_graph_store, graph_store]:
            if host == 'localhost':
                host = getattr(obj, '_host', None) or getattr(obj, 'host', None) or 'localhost'
            if port == 6379:
                port = getattr(obj, '_port', None) or getattr(obj, 'port', None) or 6379
            if password is None:
                password = getattr(obj, '_password', None) or getattr(obj, 'password', None)
            if graph_name == "knowledge_graph":
                graph_name = getattr(obj, '_graph_name', None) or getattr(obj, 'graph_name', None) or "knowledge_graph"

        logger.debug(f"   接続先: {host}:{port}")
        logger.debug(f"   グラフ名: {graph_name}")

        # ========================================
        # FalkorDBライブラリを使用する場合
        # ========================================
        if use_falkordb:
            try:
                # FalkorDB接続
                db = falkordb.FalkorDB(host=host, port=port, password=password)
                graph = db.select_graph(graph_name)

                # ノード総数
                result = graph.query("MATCH (n) RETURN count(n) as count")
                if result.result_set:
                    statistics["node_count"] = result.result_set[0][0]
                    logger.debug(f"   ノード総数: {result.result_set[0][0]}")

                # リレーション総数
                result = graph.query("MATCH ()-[r]->() RETURN count(r) as count")
                if result.result_set:
                    statistics["relationship_count"] = result.result_set[0][0]
                    logger.debug(f"   リレーション総数: {result.result_set[0][0]}")

                # ラベル別ノード数
                try:
                    result = graph.query("MATCH (n) RETURN labels(n)[0] as label, count(n) as count")
                    if result.result_set:
                        for record in result.result_set:
                            if record[0]:  # labelがNoneでない場合
                                label = record[0]
                                count = record[1]
                                statistics["node_labels"][label] = statistics["node_labels"].get(label, 0) + count
                except Exception as e:
                    logger.debug(f"   ラベル別統計スキップ: {e}")

                # リレーションタイプ別数
                try:
                    result = graph.query("MATCH ()-[r]->() RETURN type(r) as type, count(r) as count")
                    if result.result_set:
                        for record in result.result_set:
                            rel_type = record[0]
                            count = record[1]
                            statistics["relationship_types"][rel_type] = count
                except Exception as e:
                    logger.debug(f"   リレーションタイプ別統計スキップ: {e}")

                # プロパティキー（サンプリング）
                try:
                    result = graph.query("MATCH (n) RETURN keys(n) as keys LIMIT 100")
                    if result.result_set:
                        property_counts = {}
                        for record in result.result_set:
                            keys = record[0]
                            if keys:
                                for key in keys:
                                    property_counts[key] = property_counts.get(key, 0) + 1
                        statistics["properties_summary"] = dict(
                            sorted(property_counts.items(), key=lambda x: x[1], reverse=True)[:10])
                except Exception as e:
                    logger.debug(f"   プロパティサマリースキップ: {e}")

                logger.info("✅ FalkorDB統計情報の取得完了")

            except Exception as e:
                logger.error(f"❌ FalkorDBクエリエラー: {e}", exc_info=True)

        # ========================================
        # RedisGraphライブラリを使用する場合（レガシー）
        # ========================================
        else:
            try:
                import redis
                redis_client = redis.Redis(host=host, port=port, password=password, decode_responses=True)
                graph = Graph(graph_name, redis_client)

                # ノード総数
                result = graph.query("MATCH (n) RETURN count(n) as count")
                if result.result_set:
                    statistics["node_count"] = result.result_set[0][0]

                # リレーション総数
                result = graph.query("MATCH ()-[r]->() RETURN count(r) as count")
                if result.result_set:
                    statistics["relationship_count"] = result.result_set[0][0]

                # ラベル別ノード数
                try:
                    result = graph.query("MATCH (n) RETURN labels(n)[0] as label, count(n) as count")
                    if result.result_set:
                        for record in result.result_set:
                            if record[0]:
                                label = record[0]
                                count = record[1]
                                statistics["node_labels"][label] = statistics["node_labels"].get(label, 0) + count
                except Exception:
                    pass

                # リレーションタイプ別数
                try:
                    result = graph.query("MATCH ()-[r]->() RETURN type(r) as type, count(r) as count")
                    if result.result_set:
                        for record in result.result_set:
                            rel_type = record[0]
                            count = record[1]
                            statistics["relationship_types"][rel_type] = count
                except Exception:
                    pass

                redis_client.close()
                logger.info("✅ FalkorDB統計情報の取得完了（レガシーモード）")

            except Exception as e:
                logger.error(f"❌ RedisGraphクエリエラー: {e}", exc_info=True)

        # データ未登録チェック
        if statistics["node_count"] == 0:
            logger.warning("⚠️ FalkorDBにノードが登録されていません")

    except Exception as e:
        logger.error(f"❌ FalkorDB統計情報取得エラー: {e}", exc_info=True)

    return statistics


# ========================================
# Kuzu用
# ========================================
def _get_kuzu_statistics(property_graph_store, graph_store, statistics: dict) -> dict:
    """Kuzu (Embedded graph database) から統計情報を取得"""
    logger.info("Kuzuから統計情報を取得します...")
    statistics["storage_backend"] = "kuzu"

    try:
        # Kuzu接続を取得
        conn = None

        for obj in [property_graph_store, graph_store]:
            if conn is None:
                conn = getattr(obj, '_conn', None) or getattr(obj, 'conn', None) or getattr(obj, '_connection', None)

        if conn is None:
            logger.error("❌ Kuzu接続が取得できません")
            return statistics

        # Kuzuクエリを実行
        try:
            # ノードテーブル一覧を取得
            result = conn.execute("CALL show_tables() RETURN *;")
            node_tables = []
            rel_tables = []

            while result.has_next():
                row = result.get_next()
                table_name = row[0]
                table_type = row[1]

                if table_type == 'NODE':
                    node_tables.append(table_name)
                elif table_type == 'REL':
                    rel_tables.append(table_name)

            # ノード総数（テーブルごとにカウント）
            total_nodes = 0
            for table in node_tables:
                result = conn.execute(f"MATCH (n:{table}) RETURN count(n) as count;")
                if result.has_next():
                    count = result.get_next()[0]
                    total_nodes += count
                    statistics["node_labels"][table] = count

            statistics["node_count"] = total_nodes

            # リレーション総数（テーブルごとにカウント）
            total_rels = 0
            for table in rel_tables:
                result = conn.execute(f"MATCH ()-[r:{table}]->() RETURN count(r) as count;")
                if result.has_next():
                    count = result.get_next()[0]
                    total_rels += count
                    statistics["relationship_types"][table] = count

            statistics["relationship_count"] = total_rels

            # プロパティサマリー（最初のノードテーブルから）
            if node_tables:
                first_table = node_tables[0]
                result = conn.execute(f"MATCH (n:{first_table}) RETURN n LIMIT 1;")
                if result.has_next():
                    node = result.get_next()[0]
                    if hasattr(node, 'get_properties'):
                        properties = node.get_properties()
                        statistics["properties_summary"] = {k: "sample" for k in properties.keys()}

            logger.info("✅ Kuzu統計情報の取得完了")

            if statistics["node_count"] == 0:
                logger.warning("⚠️ Kuzuにノードが登録されていません")

        except Exception as e:
            logger.error(f"❌ Kuzuクエリエラー: {e}", exc_info=True)

    except Exception as e:
        logger.error(f"❌ Kuzu統計情報取得エラー: {e}", exc_info=True)

    return statistics


# ========================================
# TiDB用
# ========================================
def _get_tidb_statistics(property_graph_store, graph_store, statistics: dict) -> dict:
    """TiDB (Distributed SQL + Graph) から統計情報を取得"""
    logger.info("TiDBから統計情報を取得します...")
    statistics["storage_backend"] = "tidb"

    try:
        import pymysql

        # MySQL接続情報を取得
        connection = None

        for obj in [property_graph_store, graph_store]:
            if connection is None:
                connection = getattr(obj, '_connection', None) or getattr(obj, 'connection', None)

        # 接続が見つからない場合は新規接続
        should_close_connection = False
        if connection is None:
            logger.info("   既存の接続が見つからないため、新規接続を試みます...")

            host = None
            port = None
            user = None
            password = None
            database = None

            for obj in [property_graph_store, graph_store]:
                if host is None:
                    host = getattr(obj, '_host', None) or getattr(obj, 'host', None) or 'localhost'
                if port is None:
                    port = getattr(obj, '_port', None) or getattr(obj, 'port', None) or 4000
                if user is None:
                    user = getattr(obj, '_user', None) or getattr(obj, 'user', None) or 'root'
                if password is None:
                    password = getattr(obj, '_password', None) or getattr(obj, 'password', None) or ''
                if database is None:
                    database = getattr(obj, '_database', None) or getattr(obj, 'database', None) or 'graphrag'

            connection = pymysql.connect(
                host=host,
                port=port,
                user=user,
                password=password,
                database=database,
                charset='utf8mb4'
            )
            should_close_connection = True

        # TiDBクエリを実行
        try:
            with connection.cursor() as cursor:
                # ノード総数（knowledge_graphテーブル想定）
                cursor.execute("SELECT COUNT(DISTINCT id) FROM knowledge_graph WHERE subject IS NOT NULL")
                result = cursor.fetchone()
                if result:
                    statistics["node_count"] = result[0]

                # リレーション総数
                cursor.execute("SELECT COUNT(*) FROM knowledge_graph WHERE subject IS NOT NULL AND object IS NOT NULL")
                result = cursor.fetchone()
                if result:
                    statistics["relationship_count"] = result[0]

                # リレーションタイプ別数
                cursor.execute(
                    """
                    SELECT predicate, COUNT(*) as count 
                    FROM knowledge_graph 
                    WHERE predicate IS NOT NULL 
                    GROUP BY predicate 
                    ORDER BY count DESC
                    """
                )
                for row in cursor.fetchall():
                    rel_type = row[0]
                    count = row[1]
                    statistics["relationship_types"][rel_type] = count

                # メタデータキー（サンプリング）
                cursor.execute(
                    """
                    SELECT metadata 
                    FROM knowledge_graph 
                    WHERE metadata IS NOT NULL 
                    LIMIT 100
                    """
                )
                import json
                property_counts = {}
                for row in cursor.fetchall():
                    if row[0]:
                        try:
                            metadata = json.loads(row[0])
                            for key in metadata.keys():
                                property_counts[key] = property_counts.get(key, 0) + 1
                        except:
                            pass

                statistics["properties_summary"] = dict(
                    sorted(property_counts.items(), key=lambda x: x[1], reverse=True)[:10])

            logger.info("✅ TiDB統計情報の取得完了")

            if statistics["node_count"] == 0:
                logger.warning("⚠️ TiDBにノードが登録されていません")

        finally:
            if should_close_connection:
                connection.close()

    except ImportError:
        logger.error("❌ pymysqlライブラリがインストールされていません")
        logger.error("   pip install pymysql を実行してください")
    except Exception as e:
        logger.error(f"❌ TiDB統計情報取得エラー: {e}", exc_info=True)

    return statistics


# ========================================
# 汎用的な統計取得（フォールバック）
# ========================================
def _get_generic_statistics(property_graph_store, statistics: dict) -> dict:
    """未対応のストアから基本的な統計情報を取得（ベストエフォート）"""
    logger.info("汎用的な統計取得を試みます...")
    statistics["storage_backend"] = "generic"

    try:
        # get_triplets()メソッドがあれば使用
        if hasattr(property_graph_store, 'get_triplets'):
            triplets = property_graph_store.get_triplets()
            statistics["triplet_count"] = len(triplets)
            statistics["node_count"] = len(triplets) * 2  # 推定
            statistics["relationship_count"] = len(triplets)
            logger.info(f"   トリプレット総数（推定）: {len(triplets)}")

        logger.info("✅ 汎用統計取得完了")

    except Exception as e:
        logger.warning(f"⚠️ 汎用統計取得エラー: {e}")

    return statistics


# ========================================
# 統計情報の表示（全ストレージ対応版）
# ========================================
def print_graph_statistics_universal(statistics: dict):
    """
    Graph Store統計情報を整形して出力（全ストレージ対応版）

    Args:
        statistics: get_graph_statistics_universal()から取得した統計情報
    """
    logger.info("=" * 80)
    logger.info("📊 Graph Store統計情報")
    logger.info("=" * 80)

    store_type = statistics.get("store_type", "Unknown")
    backend = statistics.get("storage_backend", "unknown")
    logger.info(f"【ストアタイプ】")
    logger.info(f"   Store Class: {store_type}")
    logger.info(f"   Backend: {backend.upper()}")
    logger.info("")

    # ========================================
    # 基本統計
    # ========================================
    logger.info(f"【基本統計】")

    if "SimplePropertyGraphStore" in store_type:
        logger.info(f"   エンティティ総数: {statistics.get('entity_count', 0):,}")
        logger.info(f"   トリプレット総数: {statistics.get('triplet_count', 0):,}")
    else:
        logger.info(f"   ノード総数: {statistics.get('node_count', 0):,}")

    logger.info(f"   リレーション総数: {statistics.get('relationship_count', 0):,}")

    # 平均信頼度スコア
    avg_score = statistics.get("average_score")
    if avg_score is not None:
        nodes_with_score = statistics.get("nodes_with_score", 0)
        logger.info(f"   平均信頼度スコア: {avg_score:.4f} （{nodes_with_score:,}ノード）")

    # ========================================
    # ラベル/エンティティ別統計
    # ========================================
    node_labels = statistics.get("node_labels", {})
    if node_labels:
        logger.info("")
        logger.info("【ノードラベル別数】")
        for label, count in sorted(node_labels.items(), key=lambda x: x[1], reverse=True):
            total_nodes = statistics.get('node_count', 1)
            percentage = (count / total_nodes) * 100 if total_nodes > 0 else 0
            logger.info(f"   - {label}: {count:,} ({percentage:.1f}%)")

    # ========================================
    # リレーションタイプ別統計
    # ========================================
    rel_types = statistics.get("relationship_types", {})
    if rel_types:
        logger.info("")
        logger.info("【リレーションタイプ別数】")
        for rel_type, count in sorted(rel_types.items(), key=lambda x: x[1], reverse=True)[:20]:
            total_rels = statistics.get('relationship_count', 1)
            percentage = (count / total_rels) * 100 if total_rels > 0 else 0
            logger.info(f"   - {rel_type}: {count:,} ({percentage:.1f}%)")

    # ========================================
    # プロパティサマリー
    # ========================================
    properties = statistics.get("properties_summary", {})
    if properties:
        logger.info("")
        logger.info("【頻出プロパティ（TOP 10）】")
        for prop, count in sorted(properties.items(), key=lambda x: x[1] if isinstance(x[1], int) else 0, reverse=True)[
            :10]:
            if isinstance(count, int):
                logger.info(f"   - {prop}: {count:,}")
            else:
                logger.info(f"   - {prop}: {count}")

    # ========================================
    # グラフ構造指標
    # ========================================
    node_count = statistics.get('node_count', 0)
    rel_count = statistics.get('relationship_count', 0)

    if node_count > 1 and rel_count > 0:
        max_edges = node_count * (node_count - 1)
        density = (rel_count / max_edges) * 100 if max_edges > 0 else 0
        avg_degree = (rel_count * 2) / node_count if node_count > 0 else 0
        connectivity = (rel_count / node_count)

        logger.info("")
        logger.info("【グラフ構造指標】")
        logger.info(f"   グラフ密度: {density:.6f}%")
        logger.info(f"   平均次数: {avg_degree:.2f}")
        logger.info(f"   接続性（リレーション/ノード比）: {connectivity:.2f}")

    # ========================================
    # ストレージ固有の注意事項
    # ========================================
    if "SimplePropertyGraphStore" in store_type:
        logger.info("")
        logger.info("⚠️ 注意: SimplePropertyGraphStoreはメモリ内ストアです")
        logger.info("   永続化されません。本番環境ではNeo4j等の使用を推奨します。")

    if backend == "generic":
        logger.info("")
        logger.info("⚠️ 注意: 汎用統計取得のため、詳細情報が限定的です")

    # データ未登録の警告
    if node_count == 0:
        logger.info("")
        logger.warning("⚠️ グラフストアにデータが登録されていません")
        logger.warning("   データ登録処理が完了しているか確認してください")

    logger.info("=" * 80)


def save_statistics_to_file(statistics: dict, output_dir: str = "./") -> str:
    """
    統計情報をJSONファイルに保存

    Args:
        statistics: 統計情報の辞書
        output_dir: 出力ディレクトリ（デフォルト: カレントディレクトリ）

    Returns:
        str: 保存したファイルのパス
    """
    from datetime import datetime
    import json
    import os

    # 出力ディレクトリの作成
    os.makedirs(output_dir, exist_ok=True)

    # ファイル名の生成
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    storage_backend = statistics.get("storage_backend", "unknown")
    filename = f"{storage_backend}_statistics_{timestamp}.json"
    filepath = os.path.join(output_dir, filename)

    # JSON保存
    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(statistics, f, ensure_ascii=False, indent=2)

        logger.info(f"📄 統計情報をファイルに保存: {filepath}")
        return filepath

    except Exception as e:
        logger.error(f"❌ 統計情報の保存エラー: {e}", exc_info=True)
        return None


## ↓↓　Main Proc　ここから　↓↓

# ========================================
# 環境変数の読み込みと設定
# ========================================

# .envファイルのパスを指定
project_folder = Path(__file__).parent.parent
env_path = project_folder / '.env'

# .envファイルを読み込む
logger.info(f"＃読み込む .envファイル： {env_path}")
load_dotenv(dotenv_path=env_path, override=True)

# Graph Store設定の読み込み
# graph_store_type = os.getenv("GRAPH_STORE_TYPE", "neo4j").lower()
# logger.info("=" * 80)
# logger.info(f"📊 Graph Store Type: {graph_store_type.upper()}")
# logger.info("=" * 80)

# Graph Store設定の読み込み (★ config から取得)
graph_store_type = config.get("GRAPH_STORE_TYPE", "neo4j").lower()
logger.info("=" * 80)
logger.info(f"📊 Graph Store Type: {graph_store_type.upper()} (from YAML)")
logger.info("=" * 80)

# 各ストレージタイプの設定を読み込み
graph_store_config = {}

if graph_store_type == 'neo4j':
    graph_store_config = {
        'url': os.getenv("NEO4J_URL"),
        'user': os.getenv("NEO4J_USER"),
        'password': os.getenv("NEO4J_PASSWORD"),
        'database': os.getenv("NEO4J_DATABASE", 'neo4j')
    }

elif graph_store_type == 'falkordb':
    graph_store_config = {
        'url': os.getenv("FALKORDB_URL", "redis://graphrag-falkordb-ingest:6379"),
        'graph_name': os.getenv("FALKORDB_GRAPH_NAME", "knowledge_graph")
    }

elif graph_store_type == 'kuzu':
    graph_store_config = {
        'db_path': os.getenv("KUZU_DB_PATH", "/app/storage_kg/kuzu_db")
    }

elif graph_store_type == 'tidb':
    graph_store_config = {
        'host': os.getenv("TIDB_HOST", "graphrag-tidb-ingest"),
        'port': int(os.getenv("TIDB_PORT", "4000")),
        'user': os.getenv("TIDB_USER", "root"),
        'password': os.getenv("TIDB_PASSWORD", ""),
        'database': os.getenv("TIDB_DATABASE", "graphrag"),
        'table_name': os.getenv("TIDB_TABLE_NAME", "knowledge_graph")
    }

elif graph_store_type == 'simple':
    graph_store_config = {
        'storage_dir': os.getenv("SIMPLE_GRAPH_STORE_DIR", "/app/storage_kg")
    }

# OpenAI 関連
os.environ["OPENAI_API_KEY"] = os.getenv("OPENAI_API_KEY")
openai_llm = os.getenv("OPENAI_LLM")
openai_embed = os.getenv("OPENAI_EMBED")

# データをロードするフォルダを設定
# load_fld = os.getenv("LOAD_FLD")
# 修正前
# load_fld = config.get("LOAD_FLD")

# 修正後（もし取得できなかったら "data1" を使う）
load_fld = config.get("LOAD_FLD", "data1")

# パスを結合する前にチェックを入れるとより安全です
if load_fld is None:
    logger.error("❌ LOAD_FLD が設定ファイルに見つかりません。デフォルトの 'data1' を使用します。")
    load_fld = "data1"

# ChatBot Port
# chat_port = int(os.getenv("CHAT_PORT"))
chat_port = int(config.get("CHAT_PORT", 7860))

# chunk_size、chunk_overlap
# chunk_size = int(os.getenv("CHUNK_SIZE"))
# chunk_overlap = int(os.getenv("CHUNK_OVERLAP"))
chunk_size = int(config.get("CHUNK_SIZE", 2048))
chunk_overlap = int(config.get("CHUNK_OVERLAP", 200))

# ========================================
# グラフデータのクリア
# ========================================
logger.info("")
try:
    GraphStoreFactory.clear_graph_data(graph_store_type, graph_store_config)
except Exception as e:
    logger.error(f"❌ データクリアに失敗しました: {e}")
    logger.error("   処理を続行しますが、古いデータが残っている可能性があります")


# ========================================
# GraphStoreの初期化
# ========================================
logger.info("")
try:
    graph_store = GraphStoreFactory.create_graph_store(graph_store_type, graph_store_config)
    logger.info(f"✅ {graph_store_type.upper()} graph store initialized successfully")
    logger.info("=" * 80)
except Exception as e:
    logger.error(f"❌ GraphStore初期化に失敗: {e}")
    import traceback
    logger.error(traceback.format_exc())
    sys.exit(1)


# ========================================
# LLMとEmbeddingモデルの設定
# ========================================

# Embeddingの設定
embed_model = OpenAIEmbedding(
    model=openai_embed,
    embed_batch_size=1,
    max_retries=3
)

# LLMの設定
llm = OpenAI(
    model=openai_llm,
    temperature=0,
    max_tokens=32000,
    api_key=os.getenv("OPENAI_API_KEY")
)

# テキストスプリッターの設定
text_splitter = SentenceSplitter(
    chunk_size=chunk_size,
    chunk_overlap=chunk_overlap,
    paragraph_separator="\n\n",
)


# ========================================
# ドキュメントの読み込みと処理
# ========================================
logger.info("＃サンプルドキュメントの読込")
logger.info("＃LlamaParseを使用してファイルの高度な構造解析を実行")

import nest_asyncio
nest_asyncio.apply()

from llama_parse import LlamaParse

# LlamaParseの初期化
llama_api_key = os.getenv("LLAMA_CLOUD_API_KEY")
# parse_model = os.getenv("PARSE_MODEL")
# parse_mode = os.getenv("PARSE_MODE")
parse_model = config.get("PARSE_MODEL")
parse_mode = config.get("PARSE_MODE")

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

# text_splitterを直接使用
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
# ★★★ Layer 3 & 4: PropertyGraphIndex作成 ★★★
# ========================================
logger.info("\n" + "=" * 80)
logger.info("【Layer 3 & 4】FilteredPropertyGraphIndex の作成")
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

# ============================================================
# PropertyGraphIndex作成
# ============================================================
logger.info("=" * 80)
logger.info("【Layer 3 & 4】FilteredPropertyGraphIndex の作成")
logger.info("=" * 80)

index = create_filtered_property_graph_index(
    documents=documents,
    llm=llm,
    embed_model=embed_model,
    graph_store=graph_store,
    storage_context=storage_context,
    embed_kg_nodes=True,
    show_progress=True,
    num_workers=4
)
logger.info("")
logger.info("=" * 80)
logger.info("✅ FilteredPropertyGraphIndex作成完了")
logger.info("=" * 80)

# ========================================
# インデックスの永続化
# ========================================
persist_dir = "/app/storage_kg"

# ディレクトリが存在するか確認
Path(persist_dir).mkdir(parents=True, exist_ok=True)

# 明示的に永続化
try:
    print(f"💾 {persist_dir} に保存中...")
    storage_context.persist(persist_dir=persist_dir)
    print(f"✅ 保存完了")

    # デバッグ: 保存されたファイルを確認
    files = os.listdir(persist_dir)
    print(f"📁 保存されたファイル: {files}")

    # デバッグ: 各ファイルのサイズも確認
    for file in files:
        file_path = os.path.join(persist_dir, file)
        size = os.path.getsize(file_path)
        print(f"  - {file}: {size} bytes")

except Exception as e:
    print(f"❌ 保存エラー: {e}")
    import traceback
    traceback.print_exc()


# ========================================
# インデックス作成後の確認
# ========================================
logger.info("\n" + "=" * 80)
logger.info("【検証】PropertyGraphIndex 作成後の確認")
logger.info("=" * 80)

try:
    # PropertyGraphIndexの統計情報を取得
    if hasattr(index, 'property_graph_store'):
        graph_store_instance = index.property_graph_store
        logger.info(f"Property Graph Store: {type(graph_store_instance).__name__}")

        # グラフの統計情報（ストア依存）
        if hasattr(graph_store_instance, 'get_triplets'):
            try:
                triplets = graph_store_instance.get_triplets(limit=10)
                logger.info(f"サンプルトリプレット数: {len(triplets)}")

                for i, triplet in enumerate(triplets[:3]):
                    logger.info(f"  Triplet {i}: {triplet}")
            except Exception as e:
                logger.warning(f"トリプレット取得エラー: {e}")

    # ドキュメントストアの確認
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
    logger.error(f"インデックス情報の取得に失敗: {e}")
    import traceback
    logger.error(traceback.format_exc())


logger.info("=" * 80)

# 🔧 重要: ここで少し待機してNeo4jへの書き込みが完了するのを待つ
import time
logger.info("⏳ データ書き込み完了を待機中...")
time.sleep(3)  # 3秒待機

print("\n🎉 登録処理が完了しました！")
logger.info("✅ Ingest finished!")
logger.info("=" * 80)


# ============================================================
# PropertyGraphIndex作成後の統計情報表示
# ============================================================

# ========================================
# メイン処理
# ========================================
from datetime import datetime
import json

# 統計情報の取得
logger.info("=" * 80)
logger.info("📊 統計情報を取得しています...")
logger.info("=" * 80)

statistics = get_graph_statistics_universal(index, graph_store)

# 統計情報の表示
print_graph_statistics_universal(statistics)

# JSON保存（方法1: 関数を使用）
save_statistics_to_file(statistics, output_dir="./statistics")

# JSON保存（方法2: 直接記述）
timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
storage_backend = statistics.get("storage_backend", "unknown")
json_file = f"{storage_backend}_statistics_{timestamp}.json"

with open(json_file, 'w', encoding='utf-8') as f:
    json.dump(statistics, f, ensure_ascii=False, indent=2)

logger.info(f"📄 統計情報をファイルに保存: {json_file}")
