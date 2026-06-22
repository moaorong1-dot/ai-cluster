"""
星轨智库 (StarTrack Brain) — AI 第二大脑
取 GBrain·Quivr·Karpathy LLM Wiki·Neurite·Trilium 各家所长
端口: 8792 (独立服务)
"""
import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import config

# ── 日志 ──
logging.basicConfig(
    level=getattr(logging, config.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("startrack-brain")

# ── 确保数据目录 ──
DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：初始化/清理服务"""
    logger.info(f"🧠 星轨智库 v1.0 启动中... port={config.brain_port}")

    # 初始化服务（惰性导入避免循环依赖）
    from services.brain_store import BrainStore
    from services.llm_client import LLMClient

    app.state.brain_store = BrainStore()
    app.state.llm_client = LLMClient()

    logger.info("✅ 星轨智库已就绪")
    yield

    # 清理
    logger.info("🧠 星轨智库关闭中...")
    if hasattr(app.state, "brain_store"):
        app.state.brain_store.close()
    logger.info("👋 星轨智库已关闭")


# ── 创建应用 ──
app = FastAPI(
    title="星轨智库 (StarTrack Brain)",
    description="AI 第二大脑 — 知识注入 · 概念提取 · RAG 对话 · 记忆整合 · 知识图谱",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# ── CORS ──
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── 认证中间件 ──
from middleware.jwt_auth import JWTAuthMiddleware
app.add_middleware(JWTAuthMiddleware)

# ── 注册路由 ──
from routes.brain import router as brain_router
app.include_router(brain_router, prefix="/api/brain", tags=["Brain"])


# ── 根路由：直接返回前端 SPA（无需认证） ──
from fastapi.responses import HTMLResponse, FileResponse

FRONTEND_FILE = Path(__file__).parent / "frontend" / "index.html"

@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def serve_frontend():
    """直接返回知识图谱 + RAG 聊天前端"""
    if FRONTEND_FILE.exists():
        return HTMLResponse(content=FRONTEND_FILE.read_text(encoding="utf-8"))
    return HTMLResponse(content="<h2>前端文件未找到</h2>", status_code=404)


# ── 健康检查（无需认证） ──
@app.get("/api/brain/health", tags=["System"])
async def health_check():
    """健康检查端点"""
    store_ok = hasattr(app.state, "brain_store") and app.state.brain_store.is_connected()
    return {
        "status": "ok" if store_ok else "degraded",
        "service": "startrack-brain",
        "version": "1.0.0",
        "milvus": "connected" if store_ok else "disconnected",
        "port": config.brain_port,
    }


# ── 开发模式直接运行 ──
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=config.host,
        port=config.brain_port,
        reload=config.debug,
        log_level=config.log_level.lower(),
    )
