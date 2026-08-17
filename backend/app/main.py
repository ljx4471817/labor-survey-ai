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
from app.api.llm_admin import router as llm_admin_router
from app.api.quiz import router as quiz_router
from app.api.quiz_admin import router as quiz_admin_router
from app.api.whitelist_admin import router as whitelist_admin_router
from app.api.auth import router as auth_router
from app.api.chat import router as chat_router
from app.api.feedback import router as feedback_router
from app.infra.auth import require_user
# DISABLED(voice) 2026-06-21
# DISABLED(voice) 2026-06-21: 语音功能停用，输入法自带语音转写已够用。如需恢复：取消下面一行注释。
# from app.api.voice import router as voice_router
from app.core.config import PROJECT_ROOT, settings
from app.models.schemas import HealthResponse
from starlette.requests import Request
from starlette.responses import JSONResponse

import time
from collections import defaultdict

_RATE_LIMIT_PER_MINUTE = int(os.environ.get("LSX_RATE_LIMIT_PER_MINUTE", "30"))
_rate_buckets: dict[str, list[float]] = defaultdict(list)


def _rate_limited(key: str) -> bool:
    """检查是否超出速率限制。返回 True 表示应拒绝。"""
    now = time.time()
    bucket = _rate_buckets[key]
    cutoff = now - 60.0
    _rate_buckets[key] = [t for t in bucket if t > cutoff]
    if len(_rate_buckets[key]) >= _RATE_LIMIT_PER_MINUTE:
        return True
    _rate_buckets[key].append(now)
    return False

if not os.environ.get("LSX_AUTH_SECRET"):
    logger.warning(
        "LSX_AUTH_SECRET 未设置：使用进程内随机密钥（重启后所有 token 失效，仅供 dev）。"
        "生产请在 .env 设置 LSX_AUTH_SECRET=<32字节随机十六进制>"
    )
if not os.environ.get("LSX_SYSTEM_ADMIN_PHONE"):
    logger.warning(
        "LSX_SYSTEM_ADMIN_PHONE 未设置：系统管理员专属功能（反馈/KB/LLM/审计/CSV 导入）将 403。"
        "生产请在 .env 设置 LSX_SYSTEM_ADMIN_PHONE=<系统管理员手机号>"
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


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    """对 /api/chat 端点应用速率限制。"""
    if request.url.path == "/api/chat" and request.method == "POST":
        auth = request.headers.get("authorization", "")
        if auth.startswith("Bearer "):
            key = auth[7:23] if len(auth) > 23 else auth[7:]
            if _rate_limited(key):
                return JSONResponse(
                    status_code=429,
                    content={"detail": "请求过于频繁，请稍后再试"},
                )
    response = await call_next(request)
    return response


for _r in (feedback_admin_router, gaps_admin_router, usage_admin_router, whitelist_admin_router, quiz_admin_router, llm_admin_router):
    app.include_router(
        _r,
        prefix="/api/admin",
        tags=[f"admin-{_r.__module__.split('.')[-1].replace('_admin', '')}"],
        dependencies=[Depends(require_user)],
    )
app.include_router(
    quiz_router, prefix="/api", tags=["quiz"], dependencies=[Depends(require_user)],
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
    resp = FileResponse(str(path))
    # 页面不缓存：避免浏览器/内嵌浏览器拿到旧版 JS（如导入按钮无响应）
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.get("/", include_in_schema=False)
def root() -> FileResponse:
    """本地 chat 单页入口。"""
    return _serve_static_page("index.html")


@app.get("/login", include_in_schema=False)
def login_page() -> FileResponse:
    """手机号白名单登录页入口。"""
    return _serve_static_page("login.html")


@app.get("/quiz-admin", include_in_schema=False)
def quiz_admin_page() -> FileResponse:
    """月度测验管理页入口。"""
    return _serve_static_page("quiz_admin.html")


@app.get("/quiz-stats", include_in_schema=False)
def quiz_stats_page() -> FileResponse:
    """完成率看板独立页（新窗口打开）。"""
    return _serve_static_page("quiz-stats.html")


@app.get("/quiz", include_in_schema=False)
def quiz_page() -> FileResponse:
    """调查员月度测验页入口。"""
    return _serve_static_page("quiz.html")


@app.get("/dashboard", include_in_schema=False)
def dashboard() -> FileResponse:
    """内部反馈看板单页入口。"""
    return _serve_static_page("dashboard.html")


@app.get("/whitelist-admin", include_in_schema=False)
def whitelist_admin() -> FileResponse:
    """白名单管理页入口。"""
    return _serve_static_page("whitelist.html")


@app.on_event("startup")
def startup():
    # LLM routing scheduler: poll MiniMax 5h quota every 10 min, switch primary/fallback.
    from app.services.llm_switch_job import scheduler
    scheduler.start()


@app.on_event("shutdown")
def shutdown():
    from app.services.llm_switch_job import scheduler
    scheduler.stop()
    from app.rag.retriever import shutdown_executor
    shutdown_executor()


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        chroma_count=0,
        qa_count=0,
        chunk_count=0,
        llm_configured=bool(settings.llm_api_key),
    )
