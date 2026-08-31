"""反馈复核事件与 KB 改进候选聚合。"""
from __future__ import annotations

from app.services.feedback_analytics import extract_qa_id

REVIEW_STATUSES = ("pending", "accepted", "rejected")


def build_review_index(events: list[dict]) -> dict[str, dict]:
    """按反馈记录取最新复核事件，并保留完整复核历史。"""
    indexed: dict[str, list[tuple[int, dict]]] = {}
    for sequence, event in enumerate(events):
        record_id = str(event.get("record_id") or "")
        action = str(event.get("action") or "")
        if not record_id or action not in {"accepted", "rejected"}:
            continue
        indexed.setdefault(record_id, []).append((sequence, event))

    review_index: dict[str, dict] = {}
    for record_id, event_pairs in indexed.items():
        _, latest_event = max(event_pairs, key=lambda pair: pair[0])
        history = [
            dict(event)
            for _, event in sorted(event_pairs, key=lambda pair: pair[0], reverse=True)
        ]
        review_index[record_id] = {
            **dict(latest_event),
            "history": history,
        }
    return review_index


def _review_status(record_id: str, review_index: dict[str, dict]) -> str:
    event = review_index.get(record_id)
    return str(event.get("action")) if event else "pending"


def build_improvement_candidates(
    records: list[dict],
    review_index: dict[str, dict],
) -> list[dict]:
    """把负面反馈按 top1 QA 聚合；无 QA 时回退到用户问题。"""
    grouped: dict[str, list[dict]] = {}
    for record in records:
        if record.get("rating") != "down":
            continue
        sources = record.get("sources") or []
        qa_id = extract_qa_id(sources[0]) if sources else None
        question = str(record.get("question") or "").strip()
        group_key = f"qa:{qa_id}" if qa_id else f"question:{question}"
        record_id = str(record.get("id") or "")
        item = {
            **record,
            "review_status": _review_status(record_id, review_index),
            "review": review_index.get(record_id),
        }
        grouped.setdefault(group_key, []).append(item)

    candidates: list[dict] = []
    for group_key, items in grouped.items():
        status_counts = {
            status: sum(1 for item in items if item["review_status"] == status)
            for status in REVIEW_STATUSES
        }
        latest_by_status = {
            status: max(
                (
                    str(item.get("timestamp") or "")
                    for item in items
                    if item["review_status"] == status
                ),
                default="",
            )
            for status in REVIEW_STATUSES
        }
        first_item = items[0]
        sources = first_item.get("sources") or []
        qa_id = extract_qa_id(sources[0]) if sources else None
        candidates.append({
            "group_key": group_key,
            "qa_id": qa_id,
            "question": (
                str(sources[0].get("question") or "").strip()
                if qa_id and sources
                else str(first_item.get("question") or "").strip()
            ),
            "feedback_count": len(items),
            "user_count": len({str(item.get("phone") or "") for item in items}),
            "status_counts": status_counts,
            "latest_activity": max(str(item.get("timestamp") or "") for item in items),
            "latest_by_status": latest_by_status,
            "latest_pending": latest_by_status["pending"],
            "records": sorted(
                items,
                key=lambda item: str(item.get("timestamp") or ""),
                reverse=True,
            ),
        })

    return sorted(
        candidates,
        key=lambda item: (
            item["latest_pending"],
            item["latest_activity"],
            str(item["group_key"]),
        ),
        reverse=True,
    )
