# -*- coding: utf-8 -*-
"""测验：管理端请求模型（多场景：月度通知 / 培训 / 自定义）。"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.core.constants import QUIZ_MAX_QUESTIONS


class ExtractRequest(BaseModel):
    quiz_id: str = Field(..., min_length=1, max_length=32)


class KeypointReviewRequest(BaseModel):
    keypoint_id: str = Field(..., min_length=1, max_length=64)
    action: Literal["approve", "reject", "edit"]
    edits: dict | None = Field(default=None, description="action=edit 时的要点字段覆盖")


class GenerateRequest(BaseModel):
    quiz_id: str = Field(..., min_length=1, max_length=32)
    keypoint_ids: list[str] = Field(..., min_length=1, max_length=20)
    count: int | None = Field(default=None, ge=1, le=QUIZ_MAX_QUESTIONS, description="生成题数；缺省=全部勾选要点，每要点最多 1 题")


class QuestionReviewRequest(BaseModel):
    question_id: str = Field(..., min_length=1, max_length=64)
    action: Literal["approve", "reject", "edit"]
    edits: dict | None = Field(default=None, description="action=edit 时的题目字段覆盖")


class PublishRequest(BaseModel):
    quiz_id: str = Field(..., min_length=1, max_length=32)
    valid_until: str | None = Field(default=None, description="ISO 时间或 YYYY-MM-DD；缺省 = now+7 天")
    targets: list[str] = Field(..., min_length=1, max_length=2000)
    action: Literal["publish", "append", "remove"] = "publish"


class SceneAddRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=30, description="场景名称（唯一，用于统计分组）")


class SceneToggleRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=30)
    active: bool = True


class QuizDeleteRequest(BaseModel):
    quiz_id: str = Field(..., min_length=1, max_length=32)
