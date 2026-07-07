"""反馈聚合 + 区域下钻的纯函数。

拆分自 app.api.admin（v1 全堆在一个文件 → v2 拆 4 个 router + 2 个 service）。
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta

UTC8 = timezone(timedelta(hours=8))
REGION_LEVELS = ("province", "city", "county", "township", "community")

MIN_FREQ = 3
TOP_DOWN_QUESTIONS = 10
TOP_DOWN_KB = 5
RECENT_DOWN_LIMIT = 60


def extract_qa_id(source: dict) -> str | None:
    """防御性提取 qa_id：旧版可能用 'id' 字段。"""
    qid = source.get("qa_id") or source.get("id")
    return str(qid) if qid else None


def day_bucket_utc8(ts_str: str) -> str | None:
    """ISO 时间戳按 UTC+8 桶到日期。失败返回 None。"""
    try:
        dt = datetime.fromisoformat(ts_str)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC8)
    return dt.astimezone(UTC8).strftime("%Y-%m-%d")


def parent_matches(record: dict, parent: dict) -> bool:
    """判断 record 区域是否落在 parent 子树内。"""
    return all(record.get(k) == v for k, v in parent.items() if v)


def aggregate_feedback(records: list[dict], resolved_ids: set[str]) -> dict:
    """纯函数：records + resolved_ids → 看板聚合 JSON。"""
    total = len(records)
    adopted = sum(1 for r in records if r.get("rating") == "up")
    rejected = total - adopted
    adoption_rate = round(adopted / total, 4) if total else 0.0

    by_day_raw: dict[str, dict[str, int]] = defaultdict(
        lambda: {"count": 0, "adopted": 0, "rejected": 0}
    )
    for r in records:
        day = day_bucket_utc8(r.get("timestamp", ""))
        if not day:
            continue
        by_day_raw[day]["count"] += 1
        if r.get("rating") == "up":
            by_day_raw[day]["adopted"] += 1
        else:
            by_day_raw[day]["rejected"] += 1
    by_day = sorted(
        ({"day": d, **counts} for d, counts in by_day_raw.items()),
        key=lambda x: x["day"],
    )

    down_by_question: dict[str, dict] = {}
    for r in records:
        if r.get("rating") != "down":
            continue
        q = r.get("question", "").strip()
        if not q:
            continue
        bucket = down_by_question.setdefault(
            q, {"question": q, "down_count": 0, "sample_comments": []}
        )
        bucket["down_count"] += 1
        comment = r.get("comment", "").strip()
        if comment and len(bucket["sample_comments"]) < 3:
            bucket["sample_comments"].append(comment)
    top_down_questions = sorted(
        down_by_question.values(), key=lambda x: x["down_count"], reverse=True
    )[:TOP_DOWN_QUESTIONS]

    qa_down_counter: Counter[str] = Counter()
    qa_question_by_id: dict[str, str] = {}
    for r in records:
        if r.get("rating") != "down":
            continue
        for s in r.get("sources") or []:
            qid = extract_qa_id(s)
            if not qid:
                continue
            qa_down_counter[qid] += 1
            qtext = s.get("question", "")
            if qtext and qid not in qa_question_by_id:
                qa_question_by_id[qid] = qtext
    top_down_kb = [
        {
            "qa_id": qid,
            "down_count": cnt,
            "question": qa_question_by_id.get(qid, ""),
        }
        for qid, cnt in qa_down_counter.most_common(TOP_DOWN_KB)
    ]

    candidates: list[dict] = []
    for q, bucket in down_by_question.items():
        if bucket["down_count"] >= MIN_FREQ and bucket["sample_comments"]:
            candidates.append(
                {
                    "type": "missing_entry",
                    "frequency": bucket["down_count"],
                    "key": q,
                    "sample_comments": bucket["sample_comments"],
                }
            )
    for qid, cnt in qa_down_counter.items():
        if cnt >= MIN_FREQ:
            candidates.append(
                {
                    "type": "modify_entry",
                    "frequency": cnt,
                    "key": qid,
                    "question": qa_question_by_id.get(qid, ""),
                }
            )
    candidates.sort(key=lambda x: x["frequency"], reverse=True)

    recent_down = sorted(
        (r for r in records if r.get("rating") == "down"),
        key=lambda r: r.get("timestamp", ""),
        reverse=True,
    )[:RECENT_DOWN_LIMIT]

    return {
        "summary": {
            "total": total,
            "adopted": adopted,
            "rejected": rejected,
            "adoption_rate": adoption_rate,
        },
        "by_day": by_day,
        "candidate_improvements": candidates,
        "top_down_questions": top_down_questions,
        "top_down_kb": top_down_kb,
        "recent_down": recent_down,
        "resolved_count": len(resolved_ids),
        "resolved_ids": sorted(resolved_ids),
    }