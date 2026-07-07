"""反馈看板聚合 + 标记已处理 + region 5 级下钻。"""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, HTTPException, Query
from loguru import logger

from app.core.config import PROJECT_ROOT
from app.models.schemas import ResolveRequest
from app.persistence.query_log import stats_by_region as query_stats_by_region
from app.services.feedback_analytics import (
    REGION_LEVELS,
    aggregate_feedback,
    parent_matches,
)
from app.services.jsonl_utils import read_jsonl

router = APIRouter()

FEEDBACK_PATH = PROJECT_ROOT / "backend" / "data" / "feedback.jsonl"
RESOLVED_PATH = PROJECT_ROOT / "backend" / "data" / "feedback_resolved.jsonl"
UTC8 = timezone(timedelta(hours=8))


@router.get("/feedback/stats")
def feedback_stats() -> dict:
    records = read_jsonl(FEEDBACK_PATH)
    resolved_events = read_jsonl(RESOLVED_PATH)
    resolved_ids = {e["resolved_id"] for e in resolved_events if "resolved_id" in e}
    logger.info(
        f"feedback_stats: total={len(records)} resolved={len(resolved_ids)}"
    )
    return aggregate_feedback(records, resolved_ids)


@router.post("/feedback/resolve")
def resolve_feedback(req: ResolveRequest) -> dict:
    RESOLVED_PATH.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC8).isoformat(timespec="seconds")
    try:
        with RESOLVED_PATH.open("a", encoding="utf-8") as f:
            for rid in req.record_ids:
                f.write(
                    json.dumps(
                        {"resolved_id": rid, "ts": ts}, ensure_ascii=False
                    )
                    + "\n"
                )
    except OSError as e:
        logger.exception("resolve 写入失败")
        raise HTTPException(500, f"resolve 写入失败: {e}")
    logger.info(f"resolve: count={len(req.record_ids)}")
    return {"ok": True, "count": len(req.record_ids)}


@router.get("/feedback/stats/region")
def feedback_stats_by_region(
    level: str = Query(
        ..., pattern=r"^(province|city|county|township|community)$"
    ),
    parent_province: str | None = None,
    parent_city: str | None = None,
    parent_county: str | None = None,
    parent_township: str | None = None,
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