"""近一月热点问题的聚合、标准问法映射和降级。"""
from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from pathlib import Path

from app.core.config import PROJECT_ROOT
from app.infra.auth import PROTECTED_PHONES
from app.persistence.query_log import top_qa_stats

FAQ_PATH = PROJECT_ROOT / "knowledge-base" / "qa" / "faq.json"
DEFAULT_QUESTIONS_PATH = PROJECT_ROOT / "backend" / "data" / "default_questions.json"
DEFAULT_QUESTIONS = [
    "每周工作15小时算就业吗？",
    "退休人员再就业算就业吗？",
    "一人在家打游戏帮别人账号练级赚钱",
    "F16指标怎么填",
    "入户时PAD没有网络信号怎么办",
]
MAX_HOT_QUESTIONS = 5
MIN_DISTINCT_USERS = 2
CACHE_TTL_SECONDS = 30 * 60

_cache_lock = threading.Lock()
_cache_items: list[dict[str, str]] | None = None
_cached_at: float | None = None


def build_hot_questions(
    stats: list[dict],
    standard_questions: dict[str, str],
    *,
    max_items: int = MAX_HOT_QUESTIONS,
    min_users: int = MIN_DISTINCT_USERS,
) -> list[dict[str, str]]:
    """把 QA 热度聚合映射成标准问法，并保持稳定排序。"""
    eligible = [row for row in stats if int(row.get("user_count") or 0) >= min_users]
    eligible.sort(
        key=lambda row: (
            -int(row.get("user_count") or 0),
            -int(row.get("query_count") or 0),
            str(row.get("last_asked_at") or ""),
            str(row.get("top_qa_id") or ""),
        )
    )
    items: list[dict[str, str]] = []
    for row in eligible:
        question = standard_questions.get(str(row.get("top_qa_id") or ""))
        if question:
            items.append({"question": question})
        if len(items) >= max_items:
            break
    return items


def load_faq_questions(path: Path = FAQ_PATH) -> dict[str, str]:
    """读取当前标准问法，QA id 统一三位零填充。"""
    with path.open(encoding="utf-8") as file:
        items = json.load(file)
    return {
        str(item["id"]).zfill(3): str(item["question"]).strip()
        for item in items
        if item.get("id") is not None and str(item.get("question") or "").strip()
    }


def load_default_questions(
    path: Path = DEFAULT_QUESTIONS_PATH,
) -> list[dict[str, str]]:
    """读取运营维护的兜底问题；配置异常时返回内置应急问法。"""
    try:
        with path.open(encoding="utf-8") as file:
            items = json.load(file)
        if not isinstance(items, list):
            raise ValueError("default_questions.json must be a JSON array")
        questions = [
            {"question": str(item).strip()}
            for item in items
            if str(item).strip()
        ]
        return questions[:MAX_HOT_QUESTIONS] or [
            {"question": question} for question in DEFAULT_QUESTIONS
        ]
    except (OSError, ValueError, TypeError):
        return [{"question": question} for question in DEFAULT_QUESTIONS]


def reset_hot_questions_cache() -> None:
    """清空进程内热点缓存，供配置变更和测试使用。"""
    global _cache_items, _cached_at
    with _cache_lock:
        _cache_items = None
        _cached_at = None


def get_hot_questions(
    *,
    stats_fn: Callable[[], list[dict]] = lambda: top_qa_stats(
        excluded_phones=PROTECTED_PHONES
    ),
    faq_loader: Callable[[], dict[str, str]] = load_faq_questions,
    default_loader: Callable[[], list[dict[str, str]]] = load_default_questions,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, list[dict[str, str]]]:
    """读取热点；30 分钟内复用结果，无热点时返回默认问题。"""
    global _cache_items, _cached_at
    with _cache_lock:
        now = clock()
        if _cache_items is not None and _cached_at is not None:
            if now - _cached_at < CACHE_TTL_SECONDS:
                return {"items": _cache_items}

        items = build_hot_questions(stats_fn(), faq_loader())
        if not items:
            items = default_loader()
        _cache_items = items
        _cached_at = now
        return {"items": items}
