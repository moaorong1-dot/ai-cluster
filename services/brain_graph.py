"""
ConceptExtractor — 概念提取与知识图谱构建服务
Karpathy LLM Wiki 模式: LLM 自动提取概念 → 建立关联 → 更新图谱
"""
import hashlib
import logging
from typing import Optional
from datetime import datetime

import psycopg2
import psycopg2.extras

from config import config

logger = logging.getLogger(__name__)


class BrainGraph:
    """基于 PostgreSQL 的知识图谱存储（概念节点+关系边）"""

    def __init__(self):
        self._conn = None
        self._init_db()

    def _get_conn(self):
        if self._conn is None or self._conn.closed:
            self._conn = psycopg2.connect(
                host=config.pg_host,
                port=config.pg_port,
                user=config.pg_user,
                password=config.pg_password,
                dbname=config.pg_database,
            )
            self._conn.autocommit = True
        return self._conn

    def _init_db(self):
        """初始化 PostgreSQL 表和索引"""
        try:
            conn = self._get_conn()
            with conn.cursor() as cur:
                # 概念节点表
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS brain_concepts (
                        id SERIAL PRIMARY KEY,
                        name VARCHAR(255) NOT NULL UNIQUE,
                        description TEXT,
                        category VARCHAR(64) DEFAULT 'personal',
                        importance FLOAT DEFAULT 0.5,
                        embedding_id VARCHAR(64),
                        search_count INTEGER DEFAULT 0,
                        created_at TIMESTAMP DEFAULT NOW(),
                        updated_at TIMESTAMP DEFAULT NOW()
                    )
                """)

                # 关系边表
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS brain_edges (
                        id SERIAL PRIMARY KEY,
                        source_id INTEGER REFERENCES brain_concepts(id) ON DELETE CASCADE,
                        target_id INTEGER REFERENCES brain_concepts(id) ON DELETE CASCADE,
                        relation_type VARCHAR(64) DEFAULT 'related_to',
                        weight FLOAT DEFAULT 1.0,
                        evidence TEXT,
                        created_at TIMESTAMP DEFAULT NOW()
                    )
                """)

                # 记忆条目表
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS brain_memories (
                        id SERIAL PRIMARY KEY,
                        content TEXT NOT NULL,
                        summary TEXT,
                        importance FLOAT DEFAULT 0.5,
                        access_count INTEGER DEFAULT 0,
                        last_accessed TIMESTAMP,
                        archived BOOLEAN DEFAULT FALSE,
                        source_type VARCHAR(32),
                        source_url VARCHAR(1024),
                        created_at TIMESTAMP DEFAULT NOW()
                    )
                """)

                # 概念-记忆关联表
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS brain_concept_memories (
                        concept_id INTEGER REFERENCES brain_concepts(id) ON DELETE CASCADE,
                        memory_id INTEGER REFERENCES brain_memories(id) ON DELETE CASCADE,
                        relevance FLOAT DEFAULT 0.5,
                        PRIMARY KEY (concept_id, memory_id)
                    )
                """)

                # 索引
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_concepts_name ON brain_concepts(name);
                    CREATE INDEX IF NOT EXISTS idx_concepts_category ON brain_concepts(category);
                    CREATE INDEX IF NOT EXISTS idx_edges_source ON brain_edges(source_id);
                    CREATE INDEX IF NOT EXISTS idx_edges_target ON brain_edges(target_id);
                    CREATE INDEX IF NOT EXISTS idx_memories_created ON brain_memories(created_at);
                    CREATE INDEX IF NOT EXISTS idx_memories_importance ON brain_memories(importance DESC);
                """)
                logger.info("BrainGraph PostgreSQL 表初始化完成")
        except Exception as e:
            logger.warning(f"BrainGraph 初始化失败 (PostgreSQL 不可用?): {e}")

    def upsert_concept(self, name: str, description: str = "", category: str = "personal") -> Optional[int]:
        """插入或更新概念节点，返回 ID"""
        try:
            conn = self._get_conn()
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO brain_concepts (name, description, category)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (name) DO UPDATE SET
                        description = COALESCE(NULLIF(%s, ''), brain_concepts.description),
                        updated_at = NOW()
                    RETURNING id
                """, (name, description, category, description))
                row = cur.fetchone()
                return row[0] if row else None
        except Exception as e:
            logger.error(f"概念插入失败: {e}")
            return None

    def create_edge(self, source_name: str, target_name: str,
                    relation_type: str = "related_to", evidence: str = "") -> bool:
        """创建概念关系边"""
        try:
            conn = self._get_conn()
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM brain_concepts WHERE name = %s", (source_name,))
                s = cur.fetchone()
                cur.execute("SELECT id FROM brain_concepts WHERE name = %s", (target_name,))
                t = cur.fetchone()

                if not s or not t:
                    return False

                # 检查边是否已存在
                cur.execute("""
                    SELECT id FROM brain_edges
                    WHERE source_id = %s AND target_id = %s AND relation_type = %s
                """, (s[0], t[0], relation_type))
                if cur.fetchone():
                    return False

                cur.execute("""
                    INSERT INTO brain_edges (source_id, target_id, relation_type, evidence)
                    VALUES (%s, %s, %s, %s)
                """, (s[0], t[0], relation_type, evidence))
                return True
        except Exception as e:
            logger.error(f"边创建失败: {e}")
            return False

    def get_graph_data(self, limit: int = 100) -> dict:
        """获取知识图谱完整数据（用于前端 D3.js 可视化）"""
        try:
            conn = self._get_conn()
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT id, name, description, category,
                           COALESCE(importance, 0.5) as importance,
                           COALESCE(search_count, 0) as search_count
                    FROM brain_concepts
                    ORDER BY importance DESC
                    LIMIT %s
                """, (limit,))
                nodes = cur.fetchall()

                # 计算节点大小（基于 importance + search_count）
                for n in nodes:
                    n["size"] = round(n["importance"] * 10 + min(n["search_count"] / 10, 5), 1)

                # 获取边
                cur.execute("""
                    SELECT e.id, e.source_id, e.target_id, e.relation_type, e.weight,
                           sc.name as source_name, tc.name as target_name
                    FROM brain_edges e
                    JOIN brain_concepts sc ON e.source_id = sc.id
                    JOIN brain_concepts tc ON e.target_id = tc.id
                    LIMIT %s
                """, (limit * 2,))
                edges = cur.fetchall()

                return {"nodes": nodes, "edges": edges}
        except Exception as e:
            logger.error(f"图谱数据获取失败: {e}")
            return {"nodes": [], "edges": []}

    def get_concepts(self, category: str = None, limit: int = 50) -> list[dict]:
        """获取概念列表"""
        try:
            conn = self._get_conn()
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                if category:
                    cur.execute("""
                        SELECT * FROM brain_concepts
                        WHERE category = %s
                        ORDER BY importance DESC
                        LIMIT %s
                    """, (category, limit))
                else:
                    cur.execute("""
                        SELECT * FROM brain_concepts
                        ORDER BY importance DESC
                        LIMIT %s
                    """, (limit,))
                return cur.fetchall()
        except Exception as e:
            logger.error(f"概念列表获取失败: {e}")
            return []

    def save_memory(self, content: str, source_type: str = "text",
                    source_url: str = "", importance: float = 0.5) -> Optional[int]:
        """保存记忆条目"""
        try:
            conn = self._get_conn()
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO brain_memories (content, source_type, source_url, importance)
                    VALUES (%s, %s, %s, %s)
                    RETURNING id
                """, (content, source_type, source_url, importance))
                row = cur.fetchone()
                return row[0] if row else None
        except Exception as e:
            logger.error(f"记忆保存失败: {e}")
            return None

    def link_concept_memory(self, concept_id: int, memory_id: int, relevance: float = 0.5):
        """关联概念与记忆"""
        try:
            conn = self._get_conn()
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO brain_concept_memories (concept_id, memory_id, relevance)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (concept_id, memory_id) DO UPDATE SET relevance = %s
                """, (concept_id, memory_id, relevance, relevance))
        except Exception as e:
            logger.error(f"概念-记忆关联失败: {e}")

    def search_by_keyword(self, keyword: str, limit: int = 10) -> list[dict]:
        """关键词搜索 PostgreSQL"""
        try:
            conn = self._get_conn()
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT id, name, description, category
                    FROM brain_concepts
                    WHERE name ILIKE %s OR description ILIKE %s
                    LIMIT %s
                """, (f"%{keyword}%", f"%{keyword}%", limit))
                return cur.fetchall()
        except Exception as e:
            logger.error(f"关键词搜索失败: {e}")
            return []
