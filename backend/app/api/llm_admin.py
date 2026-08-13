# -*- coding: utf-8 -*-
"""LLM 路由状态与管理接口（仅系统管理员）。

手动切换语义：minimax/deepseek 写入 manual_override 并立即生效（自动 job 不再改，
连 fail-safe 也不干预）；auto 清除锁定并立即跑一次自动决策。
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.infra.auth import require_system_admin
from app.services import llm_router
from app.services.llm_switch_job import check_and_switch

router = APIRouter()


class LlmRouteRequest(BaseModel):
    """手动切换请求：provider = minimax/deepseek 锁定；auto 恢复自动。"""

    provider: Literal["minimax", "deepseek", "auto"]


def _route_payload(state: dict) -> dict:
    cfg = llm_router.provider_config(state["active_provider"]) or {}
    return {
        "active_provider": state["active_provider"],
        "active_model": cfg.get("model") or "",
        "used_5h_pct": state["used_5h_pct"],
        "used_7d_pct": state["used_7d_pct"],
        "interval_status": state["interval_status"],
        "last_check_at": state["last_check_at"],
        "last_switch_at": state["last_switch_at"],
        "consecutive_failures": state["consecutive_failures"],
        "last_error": state["last_error"],
        "manual_override": state.get("manual_override"),
        "can_manage": True,  # 仅系统管理员可访问本接口
    }


@router.get("/llm/route")
def get_llm_route(phone: str = Depends(require_system_admin)) -> dict:
    """返回当前 LLM 路由状态（含手动锁定）。"""
    return _route_payload(llm_router.load_state())


@router.post("/llm/route")
def set_llm_route(req: LlmRouteRequest, phone: str = Depends(require_system_admin)) -> dict:
    """手动切换：minimax/deepseek 锁定并立即生效；auto 恢复自动并立即决策一次。"""
    if req.provider == "auto":
        llm_router.release_manual_override()
        check_and_switch()
    else:
        llm_router.set_manual_override(req.provider)
    return _route_payload(llm_router.load_state())
