import yaml
from neo4j import GraphDatabase
import os

# Neo4jの接続情報（環境に合わせて変更してください）
NEO4J_URI = "bolt://127.0.0.1:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = "p@ssw0rd"

COMPOSE_FILE = "docker-compose-infrastructure.yml"


def parse_and_import(tx, compose_data):
    services = compose_data.get('services', {})

    for service_name, config in services.items():
        image = config.get('image', 'unknown')

        # 1. コンテナノードの作成
        print(f"📦 コンテナを登録中: {service_name}")
        tx.run("""
            MERGE (c:Container {id: $service_name})
            SET c.service_name = $service_name, 
                c.image = $image, 
                c.status = "Parsed from Compose"
        """, service_name=service_name, image=image)

        # 2. 依存関係 (DEPENDS_ON) の作成
        depends_on = config.get('depends_on', [])
        if isinstance(depends_on, dict):
            depends_on = list(depends_on.keys())  # docker-compose v3 syntax support

        for dep in depends_on:
            print(f"  🔗 依存関係をリンク: {service_name} -> {dep}")
            tx.run("""
                MERGE (c1:Container {id: $service_name})
                MERGE (c2:Container {id: $dep})
                MERGE (c1)-[:DEPENDS_ON]->(c2)
            """, service_name=service_name, dep=dep)

        # 3. ポート (Port) と露出関係 (EXPOSES) の作成
        ports = config.get('ports', [])
        for port_mapping in ports:
            # "8080:80" のような文字列からポート番号を抽出
            parts = str(port_mapping).split(':')
            if len(parts) >= 2:
                host_port = int(parts[0].replace('-', ''))  # - 8080:80 のような表記揺れ対応
                container_port = int(parts[1])
            else:
                host_port = container_port = int(parts[0])

            port_id = f"port-{host_port}"
            print(f"  🌐 ポートを開放: {host_port}")
            tx.run("""
                MERGE (p:Port {id: $port_id})
                SET p.port_number = $host_port, p.protocol = "TCP"

                WITH p
                MATCH (c:Container {id: $service_name})
                MERGE (c)-[:EXPOSES {host_port: $host_port, container_port: $container_port}]->(p)
            """, port_id=port_id, host_port=host_port, container_port=container_port, service_name=service_name)

        # 4. ボリューム (Volume) とマウント関係 (MOUNTS) の作成
        volumes = config.get('volumes', [])
        for vol_mapping in volumes:
            parts = str(vol_mapping).split(':')
            if len(parts) >= 2:
                source = parts[0]
                target = parts[1]
                vol_id = f"vol-{source.replace('/', '_').replace('.', '')}"

                print(f"  💾 ボリュームをマウント: {source} -> {target}")
                tx.run("""
                    MERGE (v:Volume {id: $vol_id})
                    SET v.name = $source, v.mount_path = $target, v.storage_type = "Compose Volume/Bind"

                    WITH v
                    MATCH (c:Container {id: $service_name})
                    MERGE (c)-[:MOUNTS {target_dir: $target}]->(v)
                """, vol_id=vol_id, source=source, target=target, service_name=service_name)


def main():
    if not os.path.exists(COMPOSE_FILE):
        print(f"エラー: {COMPOSE_FILE} が見つかりません。")
        return

    with open(COMPOSE_FILE, 'r', encoding='utf-8') as f:
        compose_data = yaml.safe_load(f)

    print("🚀 Docker Composeの解析とNeo4jへのインポートを開始します...")

    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    with driver.session() as session:
        session.execute_write(parse_and_import, compose_data)
    driver.close()

    print("✅ インポートが完了しました！Ariadneの知識として定着しました。")


if __name__ == "__main__":
    main()