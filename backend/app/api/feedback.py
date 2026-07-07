"""POST /api/feedback 端点。

把每条问答的采纳/不采纳反馈追加到 jsonl 文件。
用于：上线初期积累真实用户反馈，迭代时分析"哪些问题答得差"。
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger

from app.infra.auth import get_current_user, require_user
from app.core.config import PROJECT_ROOT
from app.models.schemas import FeedbackRequest, FeedbackResponse

router = APIRouter()

FEEDBACK_PATH = PROJECT_ROOT / "backend" / "data" / "feedback.jsonl"


@router.post("/feedback", response_model=FeedbackResponse)
def submit_feedback(
    req: FeedbackRequest,
    phone: str = Depends(require_user),
) -> FeedbackResponse:
    FEEDBACK_PATH.parent.mkdir(parents=True, exist_ok=True)
    user = get_current_user(phone)
    record = {
        "id": uuid.uuid4().hex[:12],
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "phone": phone,
        "name": user["name"] if user else "",
        "province": user["province"] if user else "",
        "city": user["city"] if user else "",
        "county": user["county"] if user else "",
        "township": (user.get("township") or "") if user else "",
        "community": user["community"] if user else "",
        "question": req.question,
        "answer": req.answer,
        "mode": req.mode,
        "rating": req.rating,
        "comment": req.comment,
        "sources": req.sources,
        "request_id": req.request_id,
    }
    try:
        with FEEDBACK_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as e:
        logger.exception("反馈写入失败")
        raise HTTPException(500, f"反馈写入失败: {e}")
    logger.info(
        f"feedback: rating={req.rating} mode={req.mode} phone={phone[:3]}**** "
        f"q='{req.question[:30]}' record_id={record['id']}"
    )
    return FeedbackResponse(ok=True, record_id=record["id"])
