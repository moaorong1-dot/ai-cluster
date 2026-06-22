"""
EmbeddingService — 嵌入向量生成服务
使用 bge-m3 模型（1024维），与 AI 集群保持一致
"""
import logging
import hashlib
from functools import lru_cache
from typing import Optional

from config import config

logger = logging.getLogger(__name__)

# 惰性加载 SentenceTransformer（首次调用时才加载模型）
_model = None


def _get_model():
    """惰性加载嵌入模型"""
    global _model
    if _model is None:
        try:
            from sentence_transformers import SentenceTransformer
            _model = SentenceTransformer(
                config.embedding_model,
                device=config.embedding_device,
            )
            logger.info(f"Embedding 模型加载完成: {config.embedding_model} ({config.embedding_dim}维)")
        except ImportError:
            logger.warning("sentence-transformers 未安装，将使用零向量作为 fallback")
            _model = True  # 标记已尝试加载
    return _model


def get_embedding(text: str) -> Optional[list[float]]:
    """生成文本嵌入向量"""
    model = _get_model()
    if model is True or model is None:
        # Fallback: 零向量
        return [0.0] * config.embedding_dim

    try:
        embedding = model.encode(text, normalize_embeddings=True)
        return embedding.tolist()
    except Exception as e:
        logger.error(f"嵌入生成失败: {e}")
        return None


def get_batch_embeddings(texts: list[str]) -> list[list[float]]:
    """批量生成嵌入向量"""
    model = _get_model()
    if model is True or model is None:
        return [[0.0] * config.embedding_dim] * len(texts)

    try:
        embeddings = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        return embeddings.tolist()
    except Exception as e:
        logger.error(f"批量嵌入生成失败: {e}")
        return [[0.0] * config.embedding_dim] * len(texts)


def smart_chunk(text: str, chunk_size: int = 512, overlap: int = 128) -> list[str]:
    """
    智能分块：按语义边界（句号、换行）切分，保持 chunk 完整性
    借鉴 Quivr 的分块策略
    """
    if len(text) <= chunk_size:
        return [text]

    chunks = []
    # 按段落切分
    paragraphs = text.split("\n\n")
    current = ""

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue

        if len(current) + len(para) + 2 <= chunk_size:
            current = (current + "\n\n" + para).strip() if current else para
        else:
            if current:
                chunks.append(current[:chunk_size])

            # 如果当前段落仍然太长，按句子切分
            if len(para) > chunk_size:
                sentences = para.replace("。", "。\n").replace("！", "！\n").replace("？", "？\n").split("\n")
                sub_chunk = ""
                for sent in sentences:
                    sent = sent.strip()
                    if not sent:
                        continue
                    if len(sub_chunk) + len(sent) + 1 <= chunk_size:
                        sub_chunk = (sub_chunk + "。" + sent).strip() if sub_chunk else sent
                    else:
                        if sub_chunk:
                            chunks.append(sub_chunk[:chunk_size])
                        sub_chunk = sent
                current = sub_chunk
            else:
                current = para

    if current:
        chunks.append(current[:chunk_size])

    return chunks


def get_text_hash(text: str) -> str:
    """计算文本 MD5 哈希"""
    return hashlib.md5(text.encode()).hexdigest()
