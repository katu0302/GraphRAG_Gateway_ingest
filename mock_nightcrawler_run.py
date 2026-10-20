import json
import os
import datetime
import uuid
import yaml
from neo4j import GraphDatabase


def get_db_credentials():
    neo4j_url = "bolt://127.0.0.1:7687"
    neo4j_user = "neo4j"
    neo4j_password = "p@ssw0rd"  # 🌟 ここを実際のパスワードに変更してください

    config_path = "config.yaml"
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            c_data = yaml.safe_load(f) or {}
            neo4j_password = c_data.get("NEO4J_PASSWORD", neo4j_password)
            target_ip = c_data.get("SHARED_SERVER_IP", "127.0.0.1")
            neo4j_url = c_data.get("NEO4J_URL", f"bolt://{target_ip}:7687")

    return neo4j_url, neo4j_user, neo4j_password


def run_nightcrawler_simulation():
    print("🦇 Nightcrawler: パトロールを開始します...")

    # 1. 外界からのダミー知見 (Insight) の入力
    dummy_insight = {
        "id": "insight-firecrawl-bug-001",
        "title": "Firecrawl v0.x 通信バグ報告",
        "content": "Firecrawlにおいて、PostgresとRedisの負荷が高まった際、ポート3002番の通信がタイムアウトする重大なバグが報告されました。"
    }
    print(f"📡 外部情報の受信: {dummy_insight['title']}")

    # 2. Neo4jへの探索 (Graph Query)
    neo4j_url, neo4j_user, neo4j_password = get_db_credentials()
    driver = GraphDatabase.driver(neo4j_url, auth=(neo4j_user, neo4j_password))

    target_container = None
    with driver.session() as session:
        # ポート3002を開放している、あるいは名前に'firecrawl'を含むコンテナを探す
        query = """
            MATCH (c:Container)
            WHERE c.service_name CONTAINS 'firecrawl' OR EXISTS {
              MATCH (c)-[:EXPOSES]->(p:Port {port_number: 3002})
            }
            RETURN c.service_name AS service_name LIMIT 1
        """
        result = session.run(query)
        record = result.single()
        if record:
            target_container = record["service_name"]

    driver.close()

    # 3. 影響評価と提案の生成 (Reasoning & Acting)
    if target_container:
        print(f"⚠️ 警告: 自環境のコンテナ [{target_container}] に影響するリスクを発見しました。")

        # 承認キュー(offline_queue)へのJSON生成
        proposal_id = f"NC-WARN-{uuid.uuid4().hex[:8]}"
        timestamp = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

        cypher_proposal = f"""
        MERGE (i:Insight {{id: '{dummy_insight["id"]}'}})
        SET i.title = '{dummy_insight["title"]}', i.content = '{dummy_insight["content"]}'
        WITH i
        MATCH (c:Container {{service_name: '{target_container}'}})
        MERGE (i)-[:WARN_CONFLICT {{risk: 'HIGH', reason: 'ポート3002の通信タイムアウトバグ', date_detected: '{timestamp}'}}]->(c)
        """

        payload = {
            "id": proposal_id,
            "timestamp": timestamp,
            "action": "SUBMIT_GRAPH_CHANGES",
            "status": "pending",
            "agent": "Nightcrawler",
            "description": f"外部のバグ報告と自環境の [{target_container}] コンテナの衝突リスクを検知しました",
            "proposed_changes": {
                "cypher": cypher_proposal.strip()
            }
        }

        # ファイルの保存
        staging_dir = r"C:\PycharmProjects\ariadne-rag\harvest_staging\offline_queue"
        os.makedirs(staging_dir, exist_ok=True)

        file_path = os.path.join(staging_dir, f"{proposal_id}.json")
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)

        print(f"✅ マスターの承認キューに提案を提出しました: {file_path}")
    else:
        print("✅ 自環境への影響は見つかりませんでした。パトロールを終了します。")


if __name__ == "__main__":
    run_nightcrawler_simulation()