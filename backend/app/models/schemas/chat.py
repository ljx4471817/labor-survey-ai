"""对话 / 反馈相关请求/响应模型。"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(..., min_length=1, max_length=4000)


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=500)
    history: list[ChatMessage] = Field(default_factory=list, max_length=8)
    conversation_id: str | None = Field(default=None, max_length=32)
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
    conversation_id: str | None = None


class ConversationItem(BaseModel):
    id: str
    title: str
    created_at: str
    updated_at: str


class ConversationListResponse(BaseModel):
    items: list[ConversationItem] = []


class ConversationMessage(BaseModel):
    id: int
    role: Literal["user", "assistant"]
    content: str
    mode: str | None = None
    sources: list[dict[str, Any]] = Field(default_factory=list)
    retrieval_score: float | None = None
    request_id: str | None = None
    created_at: str
    feedback_state: Literal["up", "down"] | None = None


class ConversationMessagesResponse(BaseModel):
    conversation_id: str
    title: str
    messages: list[ConversationMessage] = []


class FeedbackRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=500)
    answer: str = Field(..., min_length=1)
    mode: str = Field(..., description="rag / out_of_kb / out_of_scope / ambiguous")
    rating: Literal["up", "down"]
    comment: str = Field(default="", max_length=500)
    corrected_answer: str = Field(default="", max_length=2000)
    evidence: str = Field(default="", max_length=500)
    sources: list[dict] = Field(default_factory=list)
    request_id: str = Field(..., min_length=1, max_length=12)

    @field_validator("corrected_answer", "evidence", "comment", mode="before")
    @classmethod
    def normalize_optional_text(cls, value, info):
        """正面反馈不保留修正字段；负面反馈去掉首尾空白。"""
        if info.data.get("rating") != "down":
            return ""
        return str(value or "").strip()

    @model_validator(mode="after")
    def validate_feedback_payload(self):
        """仅 RAG 可反馈；负面反馈必须包含足够长的修正答案与依据。"""
        if self.mode != "rag":
            raise ValueError("仅 RAG 命中的回答支持反馈")
        if not self.request_id.strip():
            raise ValueError("request_id 不能为空")
        if self.rating == "down":
            if len(self.corrected_answer) < 10:
                raise ValueError("正确答案至少需要 10 个字符")
            if len(self.evidence) < 10:
                raise ValueError("依据至少需要 10 个字符")
        return self


class FeedbackResponse(BaseModel):
    ok: bool
    record_id: str
