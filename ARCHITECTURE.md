# 星轨智库 (StarTrack Brain) — 融合架构方案

> 取 GBrain·Quivr·Karpathy LLM Wiki·Neurite·Trilium 各家所长，为 MCN 业务打造的 AI 第二大脑

## 一、各家所长分析

| 项目 | 吸收特性 | 融合方式 |
|------|---------|---------|
| **GBrain** (10.7k⭐) | Agent 记忆持久化、可插拔 Skill、记忆整合周期 | `BrainMemory` 服务：每日/每周总结、重要性评分、记忆衰减|
| **Quivr** (35k⭐) | 多模态注入、按 Brain 隔离、RAG 对话+来源归因 | `BrainStore` 多模态 Pipeline、`brain_id` 隔离 |
| **Karpathy LLM Wiki** (625⭐) | LLM 自动概念提取、Wiki 风格自动链接 | `ConceptExtractor` 服务：从文本提取实体并链接 |
| **Neurite** (2.1k⭐) | 分形思维图谱、交互式知识图 | 前端 D3.js 力导向图 + 缩放/拖拽/Pin 操作 |
| **Trilium Notes** (28k⭐) | 层级笔记、关系链接、脚本自动化 | `BrainGraph` 服务：节点层级、关系边管理 |

## 二、系统架构

```
┌──────────────────────────────────────────────────────────────────────┐
│                          星轨智库 (Port 8792)                          │
├──────────────────────────────────────────────────────────────────────┤
│                                                                        │
│  ┌─────────────────────── API Layer ───────────────────────────┐     │
│  │  POST /api/brain/ingest      知识注入（文本/URL/文件/Markdown）│     │
│  │  POST /api/brain/chat         RAG 多轮对话                    │     │
│  │  GET  /api/brain/search       语义搜索+关键词混合检索           │     │
│  │  GET  /api/brain/concepts     概念列表+关系图                  │     │
│  │  GET  /api/brain/graph        知识图谱节点+边数据              │     │
│  │  POST /api/brain/memory/consolidate  记忆整合                  │     │
│  │  GET  /api/brain/agent/context       Agent 上下文注入          │     │
│  │  GET  /api/brain/health       健康检查                         │     │
│  └──────────────────────────────────────────────────────────────┘     │
│                                                                        │
│  ┌────────────────────── Service Layer ────────────────────────┐     │
│  │  BrainStore        Milvus 向量存储封装（复用现有 Milvus）       │     │
│  │  ConceptExtractor  LLM 概念提取+自动链接（DeepSeek）           │     │
│  │  BrainMemory       GBrain 风格记忆整合（每日/每周总结）         │     │
│  │  BrainGraph        知识图谱 CRUD（PostgreSQL 节点+边）         │     │
│  │  RAGEngine          混合检索（向量+关键词+图谱）               │     │
│  │  LLMRouter          复用现有 DeepSeek API（兼容 OpenAI）       │     │
│  └──────────────────────────────────────────────────────────────┘     │
│                                                                        │
│  ┌─────────────────────── Data Layer ──────────────────────────┐     │
│  │  Milvus       ── brain_knowledge 集合（1024维 bge-m3）         │     │
│  │  PostgreSQL   ── 概念节点表 + 关系边表 + 记忆缓存表              │     │
│  │  Redis        ── 短期记忆缓存 + 对话会话 + 任务队列             │     │
│  │  SQLite       ── 本地配置 + 索引状态（仅开发模式）               │     │
│  └──────────────────────────────────────────────────────────────┘     │
│                                                                        │
└──────────────────────────────────────────────────────────────────────┘
```

## 三、数据流

### 3.1 知识注入流程 (INGEST)

```
用户输入（文本/URL/Markdown）
  → IngestionRouter 接收
  → SmartChunker 智能分块（语义边界 + 512 token overlap）
  → 生成 MD5 哈希 + 元数据（source/timestamp/category）
  → ConceptExtractor.extract() 提取概念实体
  → Milvus BrainStore.insert() 向量化存储（bge-m3, 1024维）
  → PgBrainGraph.upsert_node() 概念节点写入 PostgreSQL
  → PgBrainGraph.create_edge() 概念关系边写入
  → Redis 添加索引时间戳
  → 返回 {chunks_inserted, concepts_extracted, edges_created}
```

### 3.2 RAG 对话流程 (CHAT)

```
用户问题
  → RAGEngine 执行混合检索:
     1. Milvus.search() 语义向量相似度搜索（top_k=20）
     2. PostgreSQL 关键词全文搜索
     3. BrainGraph 图谱邻域扩展（关联概念）
  → 融合排序（RRF 倒数排名融合）
  → LLM 生成回答 + 来源归因
  → 返回流式 SSE: {token} + 最终 {answer, sources, concepts}
```

### 3.3 记忆整合流程 (CONSOLIDATE)

```
触发：定时任务 / 手动调用
  → 从 Milvus 拉取最近24h/7d 新知识
  → LLM 生成每日摘要 + 关键洞察
  → 重要性评分（访问频率 × 概念中心度 × 时效衰减）
  → 低分记忆 → 归档到 PostgreSQL archive 表
  → 高分记忆 → 加强向量权重
  → 生成 Agent 上下文摘要（供 A2A 调用）
```

## 四、数据库设计

### 4.1 PostgreSQL 新表

```sql
-- 概念节点
CREATE TABLE brain_concepts (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL UNIQUE,
    description TEXT,
    category VARCHAR(64),       -- mcn_business / agent_memory / personal
    importance FLOAT DEFAULT 0.5,
    embedding_id VARCHAR(64),    -- Milvus ID
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- 概念关系边
CREATE TABLE brain_edges (
    id SERIAL PRIMARY KEY,
    source_id INTEGER REFERENCES brain_concepts(id),
    target_id INTEGER REFERENCES brain_concepts(id),
    relation_type VARCHAR(64),   -- related_to / depends_on / part_of / example_of
    weight FLOAT DEFAULT 1.0,
    evidence TEXT,               -- 关系来源文本
    created_at TIMESTAMP DEFAULT NOW()
);

-- 记忆条目
CREATE TABLE brain_memories (
    id SERIAL PRIMARY KEY,
    content TEXT NOT NULL,
    summary TEXT,
    importance FLOAT DEFAULT 0.5,
    access_count INTEGER DEFAULT 0,
    last_accessed TIMESTAMP,
    archived BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT NOW()
);

-- 概念-记忆关联
CREATE TABLE brain_concept_memories (
    concept_id INTEGER REFERENCES brain_concepts(id),
    memory_id INTEGER REFERENCES brain_memories(id),
    relevance FLOAT DEFAULT 0.5,
    PRIMARY KEY (concept_id, memory_id)
);
```

### 4.2 Milvus 新集合

```python
brain_knowledge = Collection(
    name="brain_knowledge",
    dimension=1024,          # bge-m3
    metric_type="COSINE",
    fields=[
        "id": VARCHAR (primary),
        "text": VARCHAR (max 65535),
        "embedding": FLOAT_VECTOR (1024),
        "source_type": VARCHAR,    # text/url/file/markdown
        "source_url": VARCHAR,
        "category": VARCHAR,       # mcn_business/agent_memory/personal
        "chunk_index": INT64,
        "hash": VARCHAR,           # MD5 去重
        "created_at": INT64        # Unix timestamp
    ]
)
```

## 五、API 契约

### POST /api/brain/ingest
```json
// Request
{
  "content": "string | required",
  "source_type": "text | url | markdown",  // default: text
  "source_url": "string | optional",
  "category": "mcn_business | agent_memory | personal", // default: personal
  "auto_extract_concepts": true  // default: true
}

// Response
{
  "ingested_id": "abc123",
  "chunks": 3,
  "concepts_extracted": ["网红运营", "素材分配", "ROI优化"],
  "edges_created": 2,
  "duplicate": false
}
```

### POST /api/brain/chat
```json
// Request
{
  "query": "string | required",
  "category": "string | optional",  // 限定搜索范围
  "top_k": 20,
  "stream": true
}

// Response (SSE stream)
// data: {"token": "根据"}
// data: {"token": "知识库"}
// ...
// data: {"done": true, "sources": [...], "concepts": [...]}
```

### GET /api/brain/graph
```json
// Response
{
  "nodes": [
    {"id": "n1", "name": "网红运营", "category": "mcn_business", "size": 5.2},
    {"id": "n2", "name": "素材分配", "category": "mcn_business", "size": 3.8}
  ],
  "edges": [
    {"source": "n1", "target": "n2", "relation": "depends_on", "weight": 0.9}
  ]
}
```

## 六、部署架构

```
134.175.175.142 (AI 集群)
│
├── /opt/startrack-brain/          # 后端代码
│   ├── main.py                    # FastAPI 入口 (port 8792)
│   ├── config.py                  # 配置管理
│   ├── routes/                    # API 路由
│   ├── services/                  # 核心服务
│   ├── middleware/                # JWT 认证中间件
│   ├── frontend/                  # SPA 静态文件
│   └── requirements.txt
│
├── systemd: startrack-brain       # 服务管理
│   ExecStart=/usr/bin/python3 -m uvicorn main:app --host 0.0.0.0 --port 8792
│
├── Nginx: ai.gxxgcl.xyz/brain/
│   location /brain/ {
│       proxy_pass http://127.0.0.1:8792/;
│   }
│
└── A2A Agent 注册
    agent_id: brain
    代号: 小智
    颜色: #7C3AED (紫色)
    capabilities: [knowledge_search, concept_extract, memory_consolidate, context_provide]
```

## 七、实施优先级

| 阶段 | 内容 | 预估工作量 | 依赖 |
|------|------|-----------|------|
| P0 | 后端骨架 + 配置 + JWT | 已完成 | 无 |
| P1 | 知识注入 + Milvus 存储 | 进行中 | Milvus 可用 |
| P2 | RAG 对话 + 混合检索 | 待开始 | P1 |
| P3 | 概念提取 + 知识图谱 | 待开始 | P2 |
| P4 | 记忆整合 + Agent 上下文 | 待开始 | P3 |
| P5 | 前端 SPA (图谱+聊天) | 待开始 | P2 |
| P6 | 部署 + systemd + Nginx + A2A | 待开始 | P5 |

---
**Architect**: ArchitectUX  
**Date**: 2026-06-19  
**Target Server**: 134.175.175.142 (4c8g Linux)
