"""
星轨智库配置管理
复用 AI 集群 config.yaml 模式，支持环境变量覆盖
"""
import os
import yaml
from functools import lru_cache
from pathlib import Path

CONFIG_PATH = Path(__file__).parent / "config.yaml"


@lru_cache()
def _load_yaml() -> dict:
    """加载 YAML 配置文件"""
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    return {}


class _Config:
    """统一配置，支持环境变量覆盖"""

    def __init__(self):
        self._data = _load_yaml()

    def _get(self, *keys, default=None):
        node = self._data
        for k in keys:
            if isinstance(node, dict) and k in node:
                node = node[k]
            else:
                return default
        return node

    @property
    def debug(self) -> bool:
        return os.environ.get("BRAIN_DEBUG", str(self._get("debug", default=False))).lower() in ("1", "true", "yes")

    @property
    def log_level(self) -> str:
        return os.environ.get("BRAIN_LOG_LEVEL", self._get("log_level", default="INFO"))

    @property
    def host(self) -> str:
        return self._get("server", "host", default="0.0.0.0")

    @property
    def brain_port(self) -> int:
        return int(os.environ.get("BRAIN_PORT", self._get("server", "port", default=8792)))

    @property
    def cors_origins(self) -> list:
        return self._get("cors", "origins", default=["*"])

    # ── LLM (DeepSeek) ──
    @property
    def llm_api_key(self) -> str:
        return os.environ.get("DEEPSEEK_API_KEY",
            self._get("llm", "api_key", default=""))

    @property
    def llm_base_url(self) -> str:
        return self._get("llm", "base_url", default="https://api.deepseek.com/v1")

    @property
    def llm_model(self) -> str:
        return self._get("llm", "model", default="deepseek-chat")

    @property
    def llm_max_tokens(self) -> int:
        return self._get("llm", "max_tokens", default=4096)

    @property
    def llm_temperature(self) -> float:
        return self._get("llm", "temperature", default=0.7)

    # ── Embedding (bge-m3) ──
    @property
    def embedding_model(self) -> str:
        return self._get("embedding", "model", default="BAAI/bge-m3")

    @property
    def embedding_dim(self) -> int:
        return self._get("embedding", "dim", default=1024)

    @property
    def embedding_device(self) -> str:
        return self._get("embedding", "device", default="cpu")

    # ── Milvus ──
    @property
    def milvus_uri(self) -> str:
        return os.environ.get("MILVUS_URI",
            self._get("milvus", "uri", default="http://127.0.0.1:19530"))

    @property
    def milvus_collection(self) -> str:
        return self._get("milvus", "collection", default="brain_knowledge")

    # ── Redis ──
    @property
    def redis_host(self) -> str:
        return os.environ.get("REDIS_HOST",
            self._get("redis", "host", default="127.0.0.1"))

    @property
    def redis_port(self) -> int:
        return int(os.environ.get("REDIS_PORT",
            self._get("redis", "port", default=6379)))

    @property
    def redis_password(self) -> str:
        return os.environ.get("REDIS_PASSWORD",
            self._get("redis", "password", default=""))

    @property
    def redis_db(self) -> int:
        return self._get("redis", "db", default=2)

    # ── PostgreSQL ──
    @property
    def pg_host(self) -> str:
        return os.environ.get("PG_HOST",
            self._get("postgresql", "host", default="127.0.0.1"))

    @property
    def pg_port(self) -> int:
        return int(os.environ.get("PG_PORT",
            self._get("postgresql", "port", default=5432)))

    @property
    def pg_user(self) -> str:
        return self._get("postgresql", "user", default="cs_admin")

    @property
    def pg_password(self) -> str:
        return os.environ.get("PG_PASSWORD",
            self._get("postgresql", "password", default="cs_pg_2026_secret"))

    @property
    def pg_database(self) -> str:
        return self._get("postgresql", "database", default="brain_db")

    # ── JWT ──
    @property
    def jwt_secret(self) -> str:
        return os.environ.get("JWT_SECRET",
            self._get("auth", "jwt_secret", default="brain-dev-secret-change-me"))

    @property
    def jwt_algorithm(self) -> str:
        return self._get("auth", "jwt_algorithm", default="HS256")

    @property
    def access_token_expire_minutes(self) -> int:
        return self._get("auth", "access_token_expire_minutes", default=120)

    # ── Brain 特有配置 ──
    @property
    def chunk_size(self) -> int:
        return self._get("brain", "chunk_size", default=512)

    @property
    def chunk_overlap(self) -> int:
        return self._get("brain", "chunk_overlap", default=128)

    @property
    def search_top_k(self) -> int:
        return self._get("brain", "search_top_k", default=20)

    @property
    def dedup_threshold(self) -> float:
        return self._get("brain", "dedup_threshold", default=0.98)

    @property
    def memory_consolidation_interval_hours(self) -> int:
        return self._get("brain", "memory_consolidation_interval_hours", default=24)

    @property
    def max_concepts_per_ingest(self) -> int:
        return self._get("brain", "max_concepts_per_ingest", default=10)


config = _Config()
