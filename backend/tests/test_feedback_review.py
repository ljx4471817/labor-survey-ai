"""反馈复核状态与 KB 改进候选聚合测试。"""
from __future__ import annotations

import pytest

from app.models.schemas import FeedbackReviewRequest
from app.services.feedback_reviews import (
    build_improvement_candidates,
    build_review_index,
)


def _down_record(
    record_id: str,
    question: str,
    ts: str,
    *,
    qa_id: str | None = "27",
    phone: str = "13985000001",
    mode: str = "rag",
) -> dict:
    sources = [{"qa_id": qa_id, "question": "F27 劳动报酬"}] if qa_id else []
    return {
        "id": record_id,
        "question": question,
        "answer": "原答案",
        "mode": mode,
        "rating": "down",
        "timestamp": ts,
        "phone": phone,
        "name": "用户A",
        "province": "贵州省",
        "city": "贵阳市",
        "county": "云岩区",
        "township": "",
        "community": "",
        "sources": sources,
        "corrected_answer": "按发放周期填写",
        "evidence": "制度规定以发放周期判断",
    }


def _event(record_id: str, action: str, ts: str, reviewer: str = "10000000000") -> dict:
    return {
        "record_id": record_id,
        "action": action,
        "ts": ts,
        "reviewer_phone": reviewer,
        "reviewer_name": "管理员",
    }


def test_review_index_uses_latest_event():
    events = [
        _event("r1", "accepted", "2026-08-01T10:00:00"),
        _event("r1", "rejected", "2026-08-02T10:00:00"),
    ]
    index = build_review_index(events)
    assert index["r1"]["action"] == "rejected"
    assert index["r1"]["reviewer_name"] == "管理员"


def test_candidates_group_by_top1_qa_id_and_count_users():
    records = [
        _down_record("r1", "q1", "2026-08-01T10:00:00", phone="13985000001"),
        _down_record("r2", "q2", "2026-08-02T10:00:00", phone="13985000002"),
        _down_record("r3", "q3", "2026-08-03T10:00:00", phone="13985000001"),
    ]
    candidates = build_improvement_candidates(records, {})
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate["qa_id"] == "27"
    assert candidate["question"] == "F27 劳动报酬"
    assert candidate["feedback_count"] == 3
    assert candidate["user_count"] == 2
    assert candidate["status_counts"] == {
        "pending": 3,
        "accepted": 0,
        "rejected": 0,
    }
    assert candidate["latest_pending"] == "2026-08-03T10:00:00"


def test_candidates_fallback_to_question_without_qa_id():
    records = [
        _down_record("r1", "q1", "2026-08-01T10:00:00", qa_id=None),
        _down_record("r2", "q1", "2026-08-02T10:00:00", qa_id=None),
    ]
    candidates = build_improvement_candidates(records, {})
    assert len(candidates) == 1
    assert candidates[0]["qa_id"] is None
    assert candidates[0]["question"] == "q1"


def test_candidates_sort_pending_items_newest_first():
    records = [
        _down_record("r1", "old", "2026-08-01T10:00:00", qa_id="1"),
        _down_record("r2", "new", "2026-08-03T10:00:00", qa_id="2"),
    ]
    candidates = build_improvement_candidates(records, {})
    assert [item["qa_id"] for item in candidates] == ["2", "1"]


def test_positive_feedback_is_not_improvement_candidate():
    record = _down_record("r1", "q", "2026-08-01T10:00:00")
    record["rating"] = "up"
    assert build_improvement_candidates([record], {}) == []


def test_feedback_review_request_validates_action():
    req = FeedbackReviewRequest(record_id="r1", action="accepted")
    assert req.action == "accepted"
    with pytest.raises(ValueError):
        FeedbackReviewRequest(record_id="r1", action="done")
