"""LLM routing status endpoint (dashboard shows which model is active)."""
from __future__ import annotations

from fastapi import APIRouter

from app.services import llm_router

router = APIRouter()


@router.get("/llm/route")
def get_llm_route() -> dict:
    """Return current LLM routing state: provider, model, usage, error."""
    state = llm_router.load_state()
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
    }
