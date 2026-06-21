"""FastAPI 入口。"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger

from app.api.chat import router as chat_router
from app.api.feedback import router as feedback_router
# DISABLED(voice) 2026-06-21: 语音功能停用，输入法自带语音转写已够用。如需恢复：取消下面一行注释。
# from app.api.voice import router as voice_router
from app.core.config import PROJECT_ROOT, settings
from app.models.schemas import HealthResponse

app = FastAPI(
    title="劳动力调查 AI 助手",
    description="辅助调查员基于 RAG 的填报指导 API",
    version="0.1.0",
)
app.include_router(chat_router, prefix="/api", tags=["chat"])
app.include_router(feedback_router, prefix="/api", tags=["feedback"])
# DISABLED(voice) 2026-06-21: 语音路由停用。恢复：取消本行注释。
# app.include_router(voice_router, tags=["voice"])

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", include_in_schema=False)
def root() -> FileResponse:
    """本地 chat 单页入口。"""
    index = STATIC_DIR / "index.html"
    if not index.exists():
        return FileResponse(content=b"static/index.html not found", status_code=404)
    return FileResponse(str(index))


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    try:
        import chromadb
        client = chromadb.PersistentClient(path=str(settings.chroma_dir))
        col = client.get_collection(settings.chroma_collection)
        count = col.count()
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
