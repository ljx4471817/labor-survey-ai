"""共享基础模型。"""
from __future__ import annotations

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    chroma_count: int = 0
    llm_configured: bool = False
    version: str = "0.1.0"
    rag_enabled: bool = True