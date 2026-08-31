# -*- coding: utf-8 -*-
"""反馈看板聚合 + 标记已处理 + region 5 级下钻（仅系统管理员）。"""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from loguru import logger

from app.core.config import PROJECT_ROOT
from app.infra.auth import get_current_user, require_system_admin
from app.models.schemas import FeedbackReviewRequest
from app.persistence.query_log import stats_by_region as query_stats_by_region
from app.services.feedback_analytics import (
    REGION_LEVELS,
    aggregate_feedback,
    parent_matches,
)
from app.services.feedback_reviews import (
    build_improvement_candidates,
    build_review_index,
)
from app.services.jsonl_utils import read_jsonl

router = APIRouter()

FEEDBACK_PATH = PROJECT_ROOT / "backend" / "data" / "feedback.jsonl"
RESOLVED_PATH = PROJECT_ROOT / "backend" / "data" / "feedback_resolved.jsonl"
UTC8 = timezone(timedelta(hours=8))


@router.get("/feedback/stats")
def feedback_stats(phone: str = Depends(require_system_admin)) -> dict:
    records = read_jsonl(FEEDBACK_PATH)
    review_events = read_jsonl(RESOLVED_PATH)
    review_index = build_review_index(review_events)
    reviewed_ids = set(review_index)
    logger.info(
        f"feedback_stats: total={len(records)} reviewed={len(reviewed_ids)} by={phone[:3]}****"
    )
    stats = aggregate_feedback(records, reviewed_ids)
    down_records = [record for record in records if record.get("rating") == "down"]
    review_status_counts = {"pending": 0, "accepted": 0, "rejected": 0}
    for record in down_records:
        review = review_index.get(str(record.get("id") or ""))
        status = str(review.get("action")) if review else "pending"
        review_status_counts[status] += 1
    stats.update({
        "review_status_counts": review_status_counts,
        "improvement_candidates": build_improvement_candidates(
            records,
            review_index,
        ),
    })
    return stats


@router.post("/feedback/resolve")
def resolve_feedback(req: FeedbackReviewRequest, phone: str = Depends(require_system_admin)) -> dict:
    RESOLVED_PATH.parent.mkdir(parents=True, exist_ok=True)
    user = get_current_user(phone)
    record_ids = {str(item.get("id") or "") for item in read_jsonl(FEEDBACK_PATH)}
    if req.record_id not in record_ids:
        raise HTTPException(404, "反馈记录不存在")
    ts = datetime.now(UTC8).isoformat(timespec="seconds")
    try:
        with RESOLVED_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps({
                "record_id": req.record_id,
                "action": req.action,
                "ts": ts,
                "reviewer_phone": phone,
                "reviewer_name": user.get("name", "") if user else "",
            }, ensure_ascii=False) + "\n")
    except OSError as e:
        logger.exception("resolve 写入失败")
        raise HTTPException(500, f"resolve 写入失败: {e}")
    logger.info(f"resolve: action={req.action} rid={req.record_id} by={phone[:3]}****")
    return {"ok": True, "record_id": req.record_id, "action": req.action}


@router.get("/feedback/stats/region")
def feedback_stats_by_region(
    level: str = Query(
        ..., pattern=r"^(province|city|county|township|community)$"
    ),
    parent_province: str | None = None,
    parent_city: str | None = None,
    parent_county: str | None = None,
    parent_township: str | None = None,
    phone: str = Depends(require_system_admin),
) -> dict:
    """按 region 维度下钻（5 级 cascading）。"""
    parent: dict[str, str] = {}
    if parent_province:
        parent["province"] = parent_province
    if parent_city:
        parent["city"] = parent_city
    if parent_county:
        parent["county"] = parent_county
    if parent_township:
        parent["township"] = parent_township

    target_idx = REGION_LEVELS.index(level)
    select_cols = REGION_LEVELS[: target_idx + 1]

    try:
        usage_rows = query_stats_by_region(level, parent if parent else None)
    except Exception:
        logger.exception("query_log 聚合失败")
        usage_rows = []

    records = [
        r for r in read_jsonl(FEEDBACK_PATH)
        if r.get("province")
    ]
    adoption_map: dict[tuple, dict] = {}
    for r in records:
        if not parent_matches(r, parent):
            continue
        key = tuple(r.get(c, "") for c in select_cols)
        if key not in adoption_map:
            adoption_map[key] = {"adopted": 0, "total": 0}
        adoption_map[key]["total"] += 1
        if r.get("rating") == "up":
            adoption_map[key]["adopted"] += 1

    regions: list[dict] = []
    seen_keys: set[tuple] = set()

    for ur in usage_rows:
        key = tuple(ur.get(c, "") for c in select_cols)
        seen_keys.add(key)
        ad = adoption_map.get(key, {"adopted": 0, "total": 0})
        rate = (
            round(ad["adopted"] / ad["total"], 4) if ad["total"] else None
        )
        regions.append(
            {
                **{c: ur.get(c, "") for c in select_cols},
                "usage": ur.get("count", 0),
                "adopted": ad["adopted"],
                "total_feedback": ad["total"],
                "adoption_rate": rate,
            }
        )

    for key, ad in adoption_map.items():
        if key not in seen_keys:
            rate = round(ad["adopted"] / ad["total"], 4) if ad["total"] else None
            regions.append(
                {
                    **dict(zip(select_cols, key)),
                    "usage": 0,
                    "adopted": ad["adopted"],
                    "total_feedback": ad["total"],
                    "adoption_rate": rate,
                }
            )

    regions.sort(key=lambda x: x["usage"], reverse=True)

    path: list[dict] = []
    for i in range(target_idx):
        lvl = REGION_LEVELS[i]
        val = parent.get(lvl, "")
        if val:
            path.append({lvl: val})

    return {
        "level": level,
        "parent": parent,
        "path": path,
        "regions": regions,
    }
