"""近一月热点问题接口。"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from loguru import logger

from app.infra.auth import PROTECTED_PHONES, get_current_user, require_user
from app.persistence.query_log import top_qa_stats
from app.services.hot_questions import get_hot_questions, load_default_questions
from app.services.kb_pack_runtime import get_request_combination

router = APIRouter()


@router.get("/chat/hot-questions")
def hot_questions_endpoint(phone: str = Depends(require_user)) -> dict:
    """返回当前省份热点问法；失败时降级为默认问题。"""
    try:
        user = get_current_user(phone) or {}
        combination = get_request_combination(phone)
        province = combination.province_code or user.get("province") or "__global__"
        return get_hot_questions(
            cache_key=province,
            stats_fn=lambda: top_qa_stats(
                excluded_phones=PROTECTED_PHONES,
                province=user.get("province"),
                province_code=combination.province_code,
            ),
        )
    except Exception:
        logger.exception("hot questions aggregation failed")
        return {"items": load_default_questions()}
