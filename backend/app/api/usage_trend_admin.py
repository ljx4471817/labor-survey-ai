# -*- coding: utf-8 -*-
"""使用频率趋势查询（业务管理员自动限定辖区）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.infra.auth import region_scope, require_whitelist_admin
from app.persistence.usage_trend import daily_usage_counts

router = APIRouter()


@router.get("/usage/trend")
def usage_trend(
    days: int = Query(7),
    user: dict = Depends(require_whitelist_admin),
) -> dict:
    """返回近 7 日或近 30 日每日对话次数；系统管理员不限辖区。"""
    if days not in (7, 30):
        raise HTTPException(422, "days 只支持 7 或 30")
    scope = region_scope(user)
    scope_dict = (
        None
        if scope is None
        else dict(zip(("province", "city", "county"), scope))
    )
    return {
        "days": days,
        "scope": scope_dict,
        **daily_usage_counts(days=days, scope=scope_dict),
    }
