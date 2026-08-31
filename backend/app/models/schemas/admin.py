# -*- coding: utf-8 -*-
"""管理后台端点请求/响应模型。"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class FeedbackReviewRequest(BaseModel):
    record_id: str = Field(..., min_length=1, max_length=12)
    action: Literal["accepted", "rejected"]


class WhitelistEntry(BaseModel):
    phone: str = Field(..., min_length=11, max_length=11, pattern=r"^1[3-9]\d{9}$")
    name: str = Field(..., min_length=1, max_length=20)
    province: str = Field(..., min_length=1, max_length=20)
    city: str = Field(..., min_length=1, max_length=20)
    county: str = Field(default="", max_length=20)
    township: str = Field(default="", max_length=30)
    community: str = Field(default="", max_length=50)
    admin_level: Literal["省级", "市级", "区县", "调查员"] = Field(
        default="调查员",
        description="管理员层级：省级/市级/区县/调查员",
    )
    # 仅系统管理员可设置/修改；业务管理员的表单/API 不出现该字段（后端强制忽略）。
    sys_role: Literal["系统管理员", "业务管理员", "普通用户"] | None = Field(
        default=None,
        description="系统职能：系统管理员/业务管理员/普通用户（仅系统管理员可设）",
    )
    remark: str = Field(default="", max_length=200)


class BatchDisableRequest(BaseModel):
    phones: list[str] = Field(..., min_length=1, max_length=500)


class MarkGapItem(BaseModel):
    query: str = Field(..., min_length=1, max_length=500)


class MarkGapsRequest(BaseModel):
    items: list[MarkGapItem] = Field(..., min_length=1, max_length=50)
    source: str = Field(..., description="high_freq_out_of_scope / kb_hit_but_down")


class FeedbackRecord(BaseModel):
    id: str
    question: str
    answer: str
    rating: Literal["up", "down"]
    comment: str = ""
    corrected_answer: str = ""
    evidence: str = ""
    request_id: str = ""
    timestamp: str
    sources: list[dict] = Field(default_factory=list)
    phone: str = ""
    name: str = ""
    province: str = ""
    city: str = ""
    county: str = ""
    township: str = ""
    community: str = ""
