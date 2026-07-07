"""管理后台端点请求/响应模型。"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ResolveRequest(BaseModel):
    record_ids: list[str] = Field(..., min_length=1, max_length=100)


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
    remark: str = Field(default="", max_length=200)


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
    timestamp: str
    sources: list[dict] = Field(default_factory=list)
    phone: str = ""
    name: str = ""
    province: str = ""
    city: str = ""
    county: str = ""
    township: str = ""
    community: str = ""
