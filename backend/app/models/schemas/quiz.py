# -*- coding: utf-8 -*-
"""月度测验：调查员端请求模型（PRD v3 6.2）。"""
from __future__ import annotations

from pydantic import BaseModel, Field


class QuizSubmitRequest(BaseModel):
    quiz_id: str = Field(..., min_length=1, max_length=32)
    q_id: str = Field(..., min_length=1, max_length=64)
    selected: str = Field(..., min_length=1, max_length=4)
