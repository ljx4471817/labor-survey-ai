"""近一月热点问题接口。"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from loguru import logger

from app.infra.auth import require_user
from app.services.hot_questions import get_hot_questions, load_default_questions

router = APIRouter()


@router.get("/chat/hot-questions")
def hot_questions_endpoint(phone: str = Depends(require_user)) -> dict:
    """返回当前热点问法；失败时降级为默认问题。"""
    try:
        return get_hot_questions()
    except Exception:
        logger.exception("hot questions aggregation failed")
        return {"items": load_default_questions()}
