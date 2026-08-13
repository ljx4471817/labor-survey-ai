# -*- coding: utf-8 -*-
"""使用侧 KB 闭环（gaps 检测 + 人工标记 ingest 候选），仅系统管理员。"""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, Query
from loguru import logger

from app.analytics.gaps import high_freq_out_of_scope, kb_hit_but_down
from app.core.config import PROJECT_ROOT
from app.infra.auth import require_system_admin
from app.models.schemas import MarkGapsRequest

router = APIRouter()

GAPS_APPROVED_DIR = PROJECT_ROOT / "reports"
UTC8 = timezone(timedelta(hours=8))


@router.get("/usage/gaps")
def usage_gaps(
    since_days: int = Query(7, ge=1, le=90),
    min_freq: int = Query(3, ge=2, le=20),
    phone: str = Depends(require_system_admin),
) -> dict:
    """使用侧 KB 闭环（轻量版：不含 embedding 调用）。

    - high_freq_out_of_scope: mode='out_of_scope' 高频 query（KB 缺覆盖）
    - kb_hit_but_down: feedback rating=down AND mode=rag

    含 embedding 的 high_freq_low_match 由 `python scripts/detect_gaps_from_usage.py` 跑。
    """
    oos = high_freq_out_of_scope(since_days, min_freq)
    down = kb_hit_but_down(since_days, min_freq)
    return {
        "since_days": since_days,
        "min_freq": min_freq,
        "summary": {"oos": len(oos), "down": len(down)},
        "high_freq_out_of_scope": oos,
        "kb_hit_but_down": down,
    }


@router.post("/usage/gaps/mark")
def mark_gaps_for_ingest(req: MarkGapsRequest, phone: str = Depends(require_system_admin)) -> dict:
    """把人工选中的 gap 候选项写到 reports/approved-from-usage-<date>.json。

    候选只有 question/_source/_marked_at，缺 answer/category/source/keywords，
    人工补完后跑 `python scripts/add_faq_entries.py reports/approved-from-usage-<date>.json` 入库。
    """
    today = datetime.now(UTC8).strftime("%Y%m%d")
    out = GAPS_APPROVED_DIR / f"approved-from-usage-{today}.json"
    out.parent.mkdir(parents=True, exist_ok=True)

    existing: list[dict] = []
    if out.exists():
        try:
            existing = json.loads(out.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = []

    existing_queries = {(it.get("question") or "").strip() for it in existing}
    new_items: list[dict] = []
    for it in req.items:
        if it.query in existing_queries:
            continue
        new_items.append({
            "question": it.query,
            "_source": req.source,
            "_marked_at": datetime.now(UTC8).isoformat(timespec="seconds"),
        })

    if not new_items:
        return {"ok": True, "count": 0, "path": str(out)}

    combined = existing + new_items
    out.write_text(json.dumps(combined, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"gaps mark: +{len(new_items)} -> {out.name} by={phone[:3]}****")
    return {
        "ok": True,
        "count": len(new_items),
        "total": len(combined),
        "path": str(out),
    }
