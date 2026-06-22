"""
JWT 鉴权中间件 — 用于星轨智库独立服务
支持 Bearer JWT + X-API-Key 双模式
"""
import logging
import os
from pathlib import Path

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

try:
    from dotenv import load_dotenv
    _env_path = Path(__file__).parent.parent / ".env"
    if _env_path.exists():
        load_dotenv(_env_path, override=True)
except ImportError:
    pass

from config import config

logger = logging.getLogger(__name__)

# ── JWT 配置 ──
JWT_SECRET = config.jwt_secret
JWT_ALGORITHM = config.jwt_algorithm

# ── 白名单路径 ──
PUBLIC_PATHS = {
    "/api/brain/health",
    "/api/brain/docs",
    "/docs",
    "/openapi.json",
    "/redoc",
}

PUBLIC_PREFIXES = []


def _decode_jwt(token: str) -> dict | None:
    """解码并验证 JWT token"""
    try:
        import jwt
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        if payload.get("type") not in ("access",):
            return None
        return payload
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError as e:
        logger.debug(f"JWT 无效: {e}")
        return None


class JWTAuthMiddleware(BaseHTTPMiddleware):
    """JWT + API Key 双模式认证"""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        if request.method == "OPTIONS":
            return await call_next(request)

        # 白名单
        if path in PUBLIC_PATHS:
            return await call_next(request)
        for prefix in PUBLIC_PREFIXES:
            if path.startswith(prefix):
                return await call_next(request)

        # 非 /api/ 路径不拦截
        if not path.startswith("/api/"):
            return await call_next(request)

        # 认证
        user_id = None
        auth_header = request.headers.get("Authorization", "")

        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
            payload = _decode_jwt(token)
            if payload:
                user_id = payload.get("sub")
                request.state.role = payload.get("role", "user")

        # API Key fallback
        if not user_id:
            api_key = request.headers.get("X-API-Key", "")
            if api_key and api_key == os.environ.get("BRAIN_API_KEY", "brain-default-key"):
                user_id = "api-client"

        if not user_id:
            return JSONResponse(
                status_code=401,
                content={"code": 1, "msg": "认证失败。请提供有效的 JWT Token 或 X-API-Key。"},
                headers={"WWW-Authenticate": "Bearer"},
            )

        request.state.user_id = user_id
        return await call_next(request)
