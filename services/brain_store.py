"""
BrainStore — 星轨智库向量存储服务
基于 Milvus，支持多模态知识注入（text/url/markdown）
带去重逻辑（MD5精确匹配 + 语义相似度检查）
"""
import hashlib
import logging
import uuid
from datetime import datetime
from typing import Optional

from pymilvus import MilvusClient, DataType

from config import config

logger = logging.getLogger(__name__)


class BrainStore:
    """向量库管理：知识的向量化存储、检索与去重"""

    def __init__(self):
        self.client = MilvusClient(uri=config.milvus_uri)
        self.collection_name = config.milvus_collection
        self._init_collection()
        self._content_hash_cache: dict[str, str] = {}
        logger.info(f"BrainStore 已连接 Milvus: {config.milvus_uri}, 集合={self.collection_name}")

    def _init_collection(self):
        """初始化 Milvus 集合（brain_knowledge）"""
        if self.client.has_collection(self.collection_name):
            logger.info(f"集合 {self.collection_name} 已存在，跳过创建")
            return

        # 创建集合
        schema = self.client.create_schema(
            auto_id=False,
            enable_dynamic_field=True,
        )
        schema.add_field("id", DataType.VARCHAR, max_length=64, is_primary=True)
        schema.add_field("text", DataType.VARCHAR, max_length=65535)
        schema.add_field("embedding", DataType.FLOAT_VECTOR, dim=config.embedding_dim)
        schema.add_field("source_type", DataType.VARCHAR, max_length=32)
        schema.add_field("source_url", DataType.VARCHAR, max_length=1024)
        schema.add_field("category", DataType.VARCHAR, max_length=64)
        schema.add_field("chunk_index", DataType.INT64)
        schema.add_field("hash", DataType.VARCHAR, max_length=64)
        schema.add_field("created_at", DataType.INT64)

        self.client.create_collection(
            collection_name=self.collection_name,
            schema=schema,
            metric_type="COSINE",
        )

        # 创建索引
        self.client.create_index(
            collection_name=self.collection_name,
            field_name="embedding",
            index_type="IVF_FLAT",
            metric_type="COSINE",
            params={"nlist": 128},
        )
        logger.info(f"集合 {self.collection_name} 创建完成，维度={config.embedding_dim}")

    def is_connected(self) -> bool:
        """检查 Milvus 连接状态"""
        try:
            return self.client.has_collection(self.collection_name)
        except Exception:
            return False

    def close(self):
        """关闭连接"""
        try:
            self.client.close()
        except Exception:
            pass

    def insert(
        self,
        text: str,
        embedding: list[float],
        source_type: str = "text",
        source_url: str = "",
        category: str = "personal",
        chunk_index: int = 0,
    ) -> dict:
        """
        插入知识块到向量库（带去重检查）

        Returns:
            {"id": str, "duplicate": bool}
        """
        # MD5 精确去重
        content_hash = hashlib.md5(text.encode()).hexdigest()
        if content_hash in self._content_hash_cache:
            logger.debug(f"跳过重复内容: hash={content_hash[:8]}")
            return {"id": self._content_hash_cache[content_hash], "duplicate": True}

        # 语义近似去重
        if len(text) >= 10:
            search_result = self.client.search(
                collection_name=self.collection_name,
                data=[embedding],
                limit=1,
                output_fields=["id", "text"],
                search_params={"metric_type": "COSINE", "params": {"nprobe": 8}},
            )
            if search_result and search_result[0]:
                top = search_result[0][0]
                if top.get("distance", 0) >= config.dedup_threshold:
                    logger.debug(f"语义重复: similarity={top['distance']:.3f}")
                    return {"id": top["id"], "duplicate": True}

        # 插入
        doc_id = str(uuid.uuid4())[:16]
        now = int(datetime.now().timestamp())

        data = [{
            "id": doc_id,
            "text": text,
            "embedding": embedding,
            "source_type": source_type,
            "source_url": source_url,
            "category": category,
            "chunk_index": chunk_index,
            "hash": content_hash,
            "created_at": now,
        }]

        self.client.insert(collection_name=self.collection_name, data=data)
        self._content_hash_cache[content_hash] = doc_id

        return {"id": doc_id, "duplicate": False}

    def search(
        self,
        query_embedding: list[float],
        top_k: int = 20,
        category: str = None,
    ) -> list[dict]:
        """
        语义搜索

        Returns:
            [{id, text, source_type, category, distance (余弦距离), score}]
        """
        filter_expr = f'category == "{category}"' if category else None

        results = self.client.search(
            collection_name=self.collection_name,
            data=[query_embedding],
            limit=top_k,
            output_fields=["id", "text", "source_type", "source_url", "category"],
            search_params={"metric_type": "COSINE", "params": {"nprobe": 16}},
            filter=filter_expr,
        )

        hits = []
        if results and results[0]:
            for r in results[0]:
                entity = r.get("entity", {})
                hits.append({
                    "id": entity.get("id", r.get("id")),
                    "text": entity.get("text", ""),
                    "source_type": entity.get("source_type", ""),
                    "source_url": entity.get("source_url", ""),
                    "category": entity.get("category", ""),
                    "distance": r.get("distance", 0),
                    "score": max(0, min(1, r.get("distance", 0))),
                })

        return hits

    def get_stats(self) -> dict:
        """获取集合统计"""
        try:
            stats = self.client.get_collection_stats(self.collection_name)
            return {"collection": self.collection_name, "row_count": stats.get("row_count", 0)}
        except Exception as e:
            return {"collection": self.collection_name, "error": str(e), "row_count": 0}
