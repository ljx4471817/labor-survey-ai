"""使用情况多维查询（按区域/姓名/手机号）。"""
from __future__ import annotations

from collections import defaultdict

from fastapi import APIRouter
from loguru import logger

from app.core.config import PROJECT_ROOT
from app.persistence.query_log import search_usage as query_log_search_usage
from app.services.jsonl_utils import read_jsonl

router = APIRouter()

FEEDBACK_PATH = PROJECT_ROOT / "backend" / "data" / "feedback.jsonl"


@router.get("/usage/search")
def search_usage(
    province: str | None = None,
    city: str | None = None,
    county: str | None = None,
    township: str | None = None,
    community: str | None = None,
    name: str | None = None,
    phone: str | None = None,
) -> dict:
    """按任意筛选条件查询使用情况（GROUP BY phone）。

    所有参数可选，region 字段精确匹配，name/phone 模糊匹配。
    合并 query_log 用量 + feedback.jsonl 反馈统计。
    """
    raw = {
        "province": province, "city": city, "county": county,
        "township": township, "community": community,
        "name": name, "phone": phone,
    }
    filters = {k: v for k, v in raw.items() if v}

    rows = query_log_search_usage(filters)

    needed = {row["phone"] for row in rows}
    feedback_by_phone: dict[str, dict] = defaultdict(lambda: {"total": 0, "adopted": 0})
    for r in read_jsonl(FEEDBACK_PATH):
        p = r.get("phone", "")
        if p not in needed:
            continue
        fb = feedback_by_phone[p]
        fb["total"] += 1
        if r.get("rating") == "up":
            fb["adopted"] += 1

    results = []
    for row in rows:
        fb = feedback_by_phone.get(row["phone"], {"total": 0, "adopted": 0})
        results.append({**row, "feedback_count": fb["total"], "adopted_count": fb["adopted"]})

    logger.info(f"usage/search: filters={list(filters.keys())} count={len(results)}")
    return {
        "filters": filters,
        "count": len(results),
        "results": results,
    }