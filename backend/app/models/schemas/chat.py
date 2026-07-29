"""对话 / 反馈相关请求/响应模型。"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(..., min_length=1, max_length=4000)


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=500)
    history: list[ChatMessage] = Field(default_factory=list, max_length=8)
    top_k: int | None = Field(default=None, ge=1, le=20)


class SourceItem(BaseModel):
    qa_id: str
    question: str
    source: str
    category: str
    score: float
    image: str | None = None


class ChatResponse(BaseModel):
    answer: str
    sources: list[SourceItem] = []
    mode: Literal["rag", "out_of_kb", "out_of_scope", "ambiguous", "error"] = "rag"
    retrieval_score: float | None = None
    request_id: str | None = None


class FeedbackRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=500)
    answer: str = Field(..., min_length=1)
    mode: str = Field(..., description="rag / out_of_kb / out_of_scope / ambiguous")
    rating: Literal["up", "down"]
    comment: str = Field(default="", max_length=500)
    sources: list[dict] = Field(default_factory=list)
    request_id: str | None = Field(default=None, max_length=12)


class FeedbackResponse(BaseModel):
    ok: bool
    record_id: str