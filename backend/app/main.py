"""FastAPI 入口。"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger

from app.api.feedback_admin import router as feedback_admin_router
from app.api.gaps_admin import router as gaps_admin_router
from app.api.usage_admin import router as usage_admin_router
from app.api.whitelist_admin import router as whitelist_admin_router
from app.api.auth import router as auth_router
from app.api.chat import router as chat_router
from app.api.feedback import router as feedback_router
from app.infra.auth import require_user
# DISABLED(voice) 2026-06-21: 语音功能停用，输入法自带语音转写已够用。如需恢复：取消下面一行注释。
# from app.api.voice import router as voice_router
from app.core.config import PROJECT_ROOT, settings
from app.models.schemas import HealthResponse

if not os.environ.get("LSX_AUTH_SECRET"):
    logger.warning(
        "LSX_AUTH_SECRET 未设置：使用进程内随机密钥（重启后所有 token 失效，仅供 dev）。"
        "生产请在 .env 设置 LSX_AUTH_SECRET=<32字节随机十六进制>"
    )

app = FastAPI(
    title="劳动力调查 AI 助手",
    description="辅助调查员基于 RAG 的填报指导 API",
    version="0.1.0",
)
app.include_router(
    chat_router, prefix="/api", tags=["chat"],
    dependencies=[Depends(require_user)],
)
app.include_router(
    feedback_router, prefix="/api", tags=["feedback"],
    dependencies=[Depends(require_user)],
)
for _r in (feedback_admin_router, gaps_admin_router, usage_admin_router, whitelist_admin_router):
    app.include_router(
        _r,
        prefix="/api/admin",
        tags=[f"admin-{_r.__module__.split('.')[-1].replace('_admin', '')}"],
        dependencies=[Depends(require_user)],
    )
app.include_router(auth_router, prefix="/api/auth", tags=["auth"])
# DISABLED(voice) 2026-06-21: 语音路由停用。恢复：取消本行注释。
# app.include_router(voice_router, tags=["voice"])

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def _serve_static_page(filename: str) -> FileResponse:
    """从 STATIC_DIR 渲染单页 HTML；缺文件抛 404。"""
    path = STATIC_DIR / filename
    if not path.exists():
        raise HTTPException(404, f"{filename} not found")
    return FileResponse(str(path))


@app.get("/", include_in_schema=False)
def root() -> FileResponse:
    """本地 chat 单页入口。"""
    return _serve_static_page("index.html")


@app.get("/login", include_in_schema=False)
def login_page() -> FileResponse:
    """手机号白名单登录页入口。"""
    return _serve_static_page("login.html")


@app.get("/dashboard", include_in_schema=False)
def dashboard() -> FileResponse:
    """内部反馈看板单页入口。"""
    return _serve_static_page("dashboard.html")


@app.get("/whitelist-admin", include_in_schema=False)
def whitelist_admin() -> FileResponse:
    """白名单管理页入口。"""
    return _serve_static_page("whitelist.html")


@app.on_event("shutdown")
def shutdown():
    from app.rag.retriever import shutdown_executor
    shutdown_executor()


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    try:
        from app.rag.retriever import get_collection
        count = get_collection().count()
        chroma_ok = True
    except Exception as e:
        logger.warning(f"chroma 健康检查失败: {e}")
        chroma_ok = False
        count = 0
    return HealthResponse(
        status="ok" if chroma_ok else "degraded",
        chroma_count=count,
        llm_configured=bool(settings.llm_api_key),
    )
