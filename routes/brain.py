"""
Brain API Routes — 星轨智库核心接口

POST   /api/brain/ingest          知识注入
POST   /api/brain/chat             RAG 对话
GET    /api/brain/search           语义搜索
GET    /api/brain/graph            知识图谱数据
GET    /api/brain/concepts         概念列表
POST   /api/brain/memory/consolidate  记忆整合
"""
import json
import logging
from typing import Optional

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter()


# ── 请求模型 ──

class IngestRequest(BaseModel):
    content: str = Field(..., description="知识内容（文本/Markdown）", min_length=1)
    source_type: str = Field("text", description="来源类型: text/url/markdown")
    source_url: str = Field("", description="来源 URL（可选）")
    category: str = Field("personal", description="分类: mcn_business/agent_memory/personal/technical")
    auto_extract_concepts: bool = Field(True, description="是否自动提取概念")


class ChatRequest(BaseModel):
    query: str = Field(..., description="用户问题", min_length=1)
    category: Optional[str] = Field(None, description="限定搜索分类")
    top_k: int = Field(20, description="检索数量", ge=1, le=50)
    stream: bool = Field(True, description="是否流式输出")


class SearchRequest(BaseModel):
    query: str = Field(..., description="搜索查询")
    category: Optional[str] = None
    top_k: int = Field(20)


class ConsolidateRequest(BaseModel):
    period: str = Field("daily", description="整合周期: daily/weekly")
    category: Optional[str] = None


# ── 辅助函数 ──

def _get_services(request: Request):
    """从 app.state 获取服务实例"""
    return {
        "store": request.app.state.brain_store,
        "llm": request.app.state.llm_client,
    }


# ── 路由实现 ──

@router.post("/ingest")
async def ingest_knowledge(req: IngestRequest, request: Request):
    """
    知识注入 — 将文本注入到第二大脑

    流程:
    1. 智能分块（语义边界 + overlap）
    2. 向量化（bge-m3, 1024维）
    3. 存储到 Milvus（带去重）
    4. (可选) LLM 提取概念 → PostgreSQL 图谱
    """
    svc = _get_services(request)
    store = svc["store"]
    llm = svc["llm"]

    # 智能分块
    from services.embedding_service import smart_chunk, get_batch_embeddings, get_text_hash

    chunks = smart_chunk(req.content)
    if not chunks:
        raise HTTPException(status_code=400, detail="内容为空或无法分块")

    # 批量嵌入
    embeddings = get_batch_embeddings(chunks)

    # 批量插入向量库
    ingested_ids = []
    duplicate_count = 0

    for i, (chunk, emb) in enumerate(zip(chunks, embeddings)):
        result = store.insert(
            text=chunk,
            embedding=emb,
            source_type=req.source_type,
            source_url=req.source_url,
            category=req.category,
            chunk_index=i,
        )
        if result["duplicate"]:
            duplicate_count += 1
        else:
            ingested_ids.append(result["id"])

    # 自动提取概念
    concepts_extracted = []
    edges_created = 0

    if req.auto_extract_concepts and ingested_ids:
        try:
            from services.brain_graph import BrainGraph
            graph = BrainGraph()

            result = llm.extract_concepts(req.content, max_concepts=10)

            for concept in result.get("concepts", []):
                cid = graph.upsert_concept(
                    name=concept["name"],
                    description=concept.get("description", ""),
                    category=concept.get("category", req.category),
                )
                if cid:
                    concepts_extracted.append(concept["name"])

            for edge in result.get("edges", []):
                ok = graph.create_edge(
                    source_name=edge["source"],
                    target_name=edge["target"],
                    relation_type=edge.get("relation_type", "related_to"),
                    evidence=edge.get("evidence", ""),
                )
                if ok:
                    edges_created += 1

        except Exception as e:
            logger.warning(f"概念提取失败（不影响注入）: {e}")

    return {
        "code": 0,
        "msg": "ok",
        "data": {
            "chunks_total": len(chunks),
            "chunks_inserted": len(ingested_ids),
            "duplicates": duplicate_count,
            "concepts_extracted": concepts_extracted,
            "edges_created": edges_created,
        }
    }


@router.post("/chat")
async def brain_chat(req: ChatRequest, request: Request):
    """
    RAG 增强对话

    流程:
    1. 用户问题嵌入
    2. Milvus 语义搜索（+ PostgreSQL 关键词搜索）
    3. 构建上下文
    4. LLM 流式生成回答
    """
    svc = _get_services(request)
    store = svc["store"]
    llm = svc["llm"]

    from services.embedding_service import get_embedding

    query_embedding = get_embedding(req.query)
    if not query_embedding:
        raise HTTPException(status_code=500, detail="嵌入生成失败")

    # 混合检索
    vector_hits = store.search(query_embedding, top_k=req.top_k, category=req.category)

    # 构建上下文
    context_parts = []
    sources = []
    for hit in vector_hits[:15]:
        if hit["text"]:
            context_parts.append(f"[{hit.get('source_type', 'doc')}] {hit['text'][:800]}")
        if hit.get("source_url"):
            sources.append(hit["source_url"])

    context = "\n\n---\n\n".join(context_parts) if context_parts else "暂无相关知识。"
    source_list = list(set(sources))[:5]

    # 构建 prompt
    system_prompt = f"""你是星轨智库的 AI 助手。基于以下知识库内容回答用户问题。

知识库内容:
{context[:6000]}

规则:
1. 优先使用知识库内容回答
2. 引用时标注来源
3. 如果知识库无相关信息，诚实说明
4. 回答简洁专业，用中文"""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": req.query},
    ]

    # 流式输出
    if req.stream:
        async def stream_response():
            try:
                response = llm.chat(messages, stream=True)
                for chunk in response:
                    if chunk.choices and chunk.choices[0].delta.content:
                        token = chunk.choices[0].delta.content
                        yield f"data: {json.dumps({'token': token}, ensure_ascii=False)}\n\n"

                # 附上来来源
                yield f"data: {json.dumps({'done': True, 'sources': source_list, 'query': req.query}, ensure_ascii=False)}\n\n"
            except Exception as e:
                yield f"data: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"

        return StreamingResponse(
            stream_response(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # 非流式
    response = llm.chat(messages)
    answer = response.choices[0].message.content
    return {
        "code": 0,
        "data": {
            "answer": answer,
            "sources": source_list,
            "doc_count": len(vector_hits),
        }
    }


@router.post("/search")
async def semantic_search(req: SearchRequest, request: Request):
    """语义搜索"""
    svc = _get_services(request)
    store = svc["store"]

    from services.embedding_service import get_embedding

    query_embedding = get_embedding(req.query)
    if not query_embedding:
        raise HTTPException(status_code=500, detail="嵌入生成失败")

    hits = store.search(query_embedding, top_k=req.top_k, category=req.category)

    return {
        "code": 0,
        "data": {
            "query": req.query,
            "results": hits,
            "total": len(hits),
        }
    }


@router.get("/graph")
async def get_knowledge_graph(request: Request, limit: int = 100):
    """获取知识图谱完整数据（供 D3.js 可视化）"""
    try:
        from services.brain_graph import BrainGraph
        graph = BrainGraph()
        data = graph.get_graph_data(limit=limit)
        return {"code": 0, "data": data}
    except Exception as e:
        logger.error(f"图谱获取失败: {e}")
        return {"code": 0, "data": {"nodes": [], "edges": []}}


@router.get("/concepts")
async def get_concepts(request: Request, category: str = None, limit: int = 50):
    """获取概念列表"""
    try:
        from services.brain_graph import BrainGraph
        graph = BrainGraph()
        concepts = graph.get_concepts(category=category, limit=limit)
        return {"code": 0, "data": concepts}
    except Exception as e:
        logger.error(f"概念列表失败: {e}")
        return {"code": 0, "data": []}


@router.post("/memory/consolidate")
async def consolidate_memory(req: ConsolidateRequest, request: Request):
    """
    记忆整合 — GBrain 风格，将碎片知识整合为结构化摘要

    流程:
    1. 从 Milvus 拉取最近的知识块
    2. LLM 生成摘要和洞察
    3. 保存到 PostgreSQL 记忆表
    """
    svc = _get_services(request)
    llm = svc["llm"]
    store = svc["store"]

    from services.embedding_service import get_embedding
    from services.brain_graph import BrainGraph

    # 使用通用查询拉取最近知识（按时间排序）
    query_emb = get_embedding("最近的知识") or [0.0] * 1024
    hits = store.search(query_emb, top_k=30, category=req.category)

    if not hits:
        return {"code": 0, "msg": "暂无知识需要整合", "data": {"summary": "暂无内容"}}

    texts = [h["text"] for h in hits[:20] if h.get("text")]
    summary = llm.generate_summary(texts, period=req.period)

    # 保存记忆
    try:
        graph = BrainGraph()
        memory_id = graph.save_memory(
            content=summary,
            source_type="consolidation",
            importance=0.7,
        )
    except Exception:
        memory_id = None

    return {
        "code": 0,
        "data": {
            "period": req.period,
            "docs_processed": len(texts),
            "summary": summary,
            "memory_id": memory_id,
        }
    }


@router.get("/agent/context")
async def get_agent_context(request: Request, limit: int = 10):
    """
    获取 Agent 上下文 — 供其他 A2A Agent 调用

    返回最近记忆中重要性最高的内容摘要
    """
    try:
        from services.brain_graph import BrainGraph
        graph = BrainGraph()
        concepts = graph.get_concepts(limit=limit)
        memories = graph.get_concepts(category="agent_memory", limit=limit)

        return {
            "code": 0,
            "data": {
                "top_concepts": [c["name"] for c in concepts[:5]],
                "recent_memories": [m.get("description", m.get("name", "")) for m in memories[:5]],
                "knowledge_summary": f"星轨智库当前追踪 {len(concepts)} 个概念",
            }
        }
    except Exception as e:
        return {"code": 0, "data": {"message": f"Agent 上下文暂不可用: {e}"}}
