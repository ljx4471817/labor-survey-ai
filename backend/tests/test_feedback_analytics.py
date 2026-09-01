"""aggregate_feedback 纯函数测试（拆自 app/api/admin.py → app/services/feedback_analytics.py）。

输入：feedback records (JSONL) + resolved_ids set
输出：看板聚合 JSON（summary / by_day / candidates / top_down_* / recent_down / resolved_*）

锁住关键不变量：adoption_rate 精度、candidates 阈值 MIN_FREQ=3、resolved_ids 去重。
"""
import sys
from datetime import datetime, timezone, timedelta

from app.services.feedback_analytics import (
    MIN_FREQ,
    aggregate_feedback,
    day_bucket_utc8,
    extract_qa_id,
    parent_matches,
)

UTC8 = timezone(timedelta(hours=8))


def _rec(rating: str, question: str = "q", ts: str = "2026-06-01T08:00:00+08:00",
         comment: str = "", sources: list | None = None,
         region: dict | None = None) -> dict:
    rec = {
        "id": f"rec-{question}-{rating}-{ts}",
        "rating": rating,
        "question": question,
        "comment": comment,
        "timestamp": ts,
        "sources": sources or [],
    }
    if region:
        rec.update(region)
    return rec


def test_empty_inputs_returns_zeros():
    out = aggregate_feedback([], set())
    assert out["summary"]["total"] == 0
    assert out["summary"]["adopted"] == 0
    assert out["summary"]["adoption_rate"] == 0.0
    assert out["candidate_improvements"] == []
    assert out["resolved_count"] == 0
    assert out["resolved_ids"] == []


def test_adoption_rate_basic():
    # 3 up + 1 down → 0.75
    records = [
        _rec("up", ts="2026-06-01T08:00:00+08:00"),
        _rec("up", ts="2026-06-01T09:00:00+08:00"),
        _rec("up", ts="2026-06-01T10:00:00+08:00"),
        _rec("down", ts="2026-06-01T11:00:00+08:00"),
    ]
    out = aggregate_feedback(records, set())
    assert out["summary"]["total"] == 4
    assert out["summary"]["adopted"] == 3
    assert out["summary"]["rejected"] == 1
    assert out["summary"]["adoption_rate"] == 0.75


def test_by_day_buckets_correctly():
    # 同一天 2 条 + 第二天 1 条
    records = [
        _rec("up", ts="2026-06-01T08:00:00+08:00"),
        _rec("down", ts="2026-06-01T15:00:00+08:00"),
        _rec("up", ts="2026-06-02T08:00:00+08:00"),
    ]
    out = aggregate_feedback(records, set())
    days = {d["day"]: d for d in out["by_day"]}
    assert days["2026-06-01"]["count"] == 2
    assert days["2026-06-01"]["adopted"] == 1
    assert days["2026-06-01"]["rejected"] == 1
    assert days["2026-06-02"]["count"] == 1


def test_candidate_missing_entry_requires_freq_and_comment():
    # 同一问题 down >= MIN_FREQ 且至少有 comment → missing_entry 候选
    records = [
        _rec("down", question="F27 怎么算", comment="不对", ts="2026-06-01T08:00:00+08:00"),
        _rec("down", question="F27 怎么算", comment="有误", ts="2026-06-01T09:00:00+08:00"),
        _rec("down", question="F27 怎么算", comment="漏了", ts="2026-06-01T10:00:00+08:00"),
    ]
    out = aggregate_feedback(records, set())
    missing = [c for c in out["candidate_improvements"] if c["type"] == "missing_entry"]
    assert len(missing) == 1
    assert missing[0]["key"] == "F27 怎么算"
    assert missing[0]["frequency"] == MIN_FREQ
    assert len(missing[0]["sample_comments"]) >= 1


def test_candidate_modify_entry_uses_qa_id():
    # 同一 qa_id 被 down ≥3 → modify_entry
    sources = [{"qa_id": "42", "question": "F27 自营"}]
    records = [
        _rec("down", question="q1", sources=sources, ts="2026-06-01T08:00:00+08:00"),
        _rec("down", question="q2", sources=sources, ts="2026-06-01T09:00:00+08:00"),
        _rec("down", question="q3", sources=sources, ts="2026-06-01T10:00:00+08:00"),
    ]
    out = aggregate_feedback(records, set())
    modify = [c for c in out["candidate_improvements"] if c["type"] == "modify_entry"]
    assert len(modify) == 1
    assert modify[0]["key"] == "42"
    assert modify[0]["frequency"] == MIN_FREQ


def test_resolved_ids_deduplicated_and_sorted():
    records = [_rec("up", ts="2026-06-01T08:00:00+08:00")]
    out = aggregate_feedback(records, {"r2", "r1", "r1", "r3"})
    # 集合去重 + sorted
    assert out["resolved_count"] == 3
    assert out["resolved_ids"] == ["r1", "r2", "r3"]


def test_recent_down_limited_to_60():
    # 100 条 down → 60 条
    records = [
        _rec("down", ts=f"2026-06-01T{i:02d}:00:00+08:00")
        for i in range(100)
    ]
    out = aggregate_feedback(records, set())
    assert len(out["recent_down"]) == 60


def test_top_down_questions_caps_at_10():
    # 15 个不同问题各 down 1 次 → top 10
    records = [
        _rec("down", question=f"Q{i}", ts="2026-06-01T08:00:00+08:00")
        for i in range(15)
    ]
    out = aggregate_feedback(records, set())
    assert len(out["top_down_questions"]) == 10


def test_extract_qa_id_handles_legacy_id_field():
    assert extract_qa_id({"qa_id": "42"}) == "42"
    assert extract_qa_id({"id": 99}) == "99"  # int 转 str
    assert extract_qa_id({"qa_id": ""}) is None
    assert extract_qa_id({}) is None


def test_day_bucket_utc8_handles_naive_and_invalid():
    # 无时区 → 视为 UTC+8
    assert day_bucket_utc8("2026-06-01T08:00:00") == "2026-06-01"
    # 带时区 → 转 UTC+8 后截断
    # UTC 0:00 → +8 是同日 08:00
    assert day_bucket_utc8("2026-06-01T00:00:00+00:00") == "2026-06-01"
    # UTC 16:00 → +8 是次日 00:00，桶到次日
    assert day_bucket_utc8("2026-06-01T16:00:00+00:00") == "2026-06-02"
    # 无效
    assert day_bucket_utc8("not-a-date") is None
    assert day_bucket_utc8("") is None
    assert day_bucket_utc8(None) is None
    # Python 3.10 fromisoformat 不支持 'Z' 后缀 → 防御性返回 None；
    # 3.11+ 支持 'Z'，正常解析并转 UTC+8（CI 用 3.11）
    if sys.version_info >= (3, 11):
        assert day_bucket_utc8("2026-06-01T00:00:00Z") == "2026-06-01"
    else:
        assert day_bucket_utc8("2026-06-01T00:00:00Z") is None


def test_parent_matches_filters_by_subtree():
    record = {"province": "贵州省", "city": "贵阳市", "county": "云岩区"}
    assert parent_matches(record, {"province": "贵州省"})
    assert parent_matches(record, {"province": "贵州省", "city": "贵阳市"})
    assert not parent_matches(record, {"province": "云南省"})
    # 空 parent 视为匹配全部
    assert parent_matches(record, {})
    # 空值字段不参与匹配
    assert parent_matches(record, {"township": ""})
