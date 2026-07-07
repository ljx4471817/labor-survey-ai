"""检索相关纯函数（无 IO / 无网络 / 无全局状态）。

从 retriever.py 拆分出来，让单元测试不需要 mock Chroma / embedding。
关键词列表从 data/scope_keywords.json 热加载（启动时读一次）。
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

from app.core.config import PROJECT_ROOT
from app.models.schemas import ChatMessage


@lru_cache(maxsize=1)
def _load_scope_config() -> dict:
    """从 JSON 文件加载范围/模糊配置（启动时读一次，lru_cache 缓存）。"""
    config_path = PROJECT_ROOT / "backend" / "data" / "scope_keywords.json"
    if not config_path.exists():
        return {"out_of_scope_keywords": [], "ambiguous_short_phrases": []}
    with config_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def get_out_of_scope_keywords() -> list[str]:
    return _load_scope_config().get("out_of_scope_keywords", [])


def get_ambiguous_short_phrases() -> set[str]:
    return set(_load_scope_config().get("ambiguous_short_phrases", []))


def is_in_scope(query: str) -> bool:
    """粗略判断是否在助手服务范围内（关键词过滤）。"""
    q = query.lower()
    return not any(kw in q for kw in get_out_of_scope_keywords())


def is_ambiguous(query: str) -> bool:
    """粗略判断问题是否模糊。"""
    q = query.strip().rstrip("？?。.!！")
    # 含 F 编号（如 "F27 劳动报酬"）—— 多轮补充场景，不算模糊
    if re.search(r"\bF\d+\b", q):
        return False
    # 短问题
    if len(q) <= 6:
        return True
    # 短问句
    if q in get_ambiguous_short_phrases():
        return True
    # "这个X" / "那个X" 类指代不明
    if q.startswith("这个") or q.startswith("那个"):
        return True
    return False


def merge_query_with_history(message: str, history: list[ChatMessage]) -> str:
    """合并历史消息到检索 query。

    msg ≥ 8 字：返回 message（干净 query；历史约束由 LLM 用 history_context 消歧）。
    msg < 8 字（追问型 "那 X 呢" / "F27 怎么填"）：拼最近 1 条 user 历史兜底。
    """
    msg = message.strip()
    if len(msg) >= 8:
        return msg
    last_user = next((m.content for m in reversed(history) if m.role == "user"), None)
    return f"{last_user} {msg}".strip() if last_user else msg


def rrf_fuse(
    vector_results: list[dict],
    bm25_results: list[dict],
    k: int,
    rrf_c: int = 60,
    bm25_weight: float = 1.0,
) -> list[dict]:
    """Reciprocal Rank Fusion：rank 归一化融合（Cormack 公式 rrf_c=60）。

    这里采用"信任加权 RRF"：vector 命中保留原始 RRF 全额，BM25 命中按
    bm25_weight 加权（默认 1.0）。同一 doc 在双系统都被命中时，
    总分 = vector_RRF + bm25_weight * bm25_RRF，自动获得"双系统命中"奖励。

    返回项保留原始 vector cosine 相似度在 score 字段，
    threshold 仍按 cosine 比对。
    """
    fused: dict[str, dict] = {}

    for rank, item in enumerate(vector_results, 1):
        qid = item["id"]
        fused[qid] = {
            **item,
            "rrf_score": 1.0 / (rrf_c + rank),
            "vector_rank": rank,
            "bm25_rank": None,
        }

    for rank, item in enumerate(bm25_results, 1):
        qid = item["id"]
        bm25_contrib = bm25_weight / (rrf_c + rank)
        if qid in fused:
            fused[qid]["rrf_score"] += bm25_contrib
            fused[qid]["bm25_rank"] = rank
        else:
            fused[qid] = {
                **item,
                "rrf_score": bm25_contrib,
                "vector_rank": None,
                "bm25_rank": rank,
                "score": 0.0,  # 无向量分数，threshold 走兜底
            }

    ranked = sorted(fused.values(), key=lambda x: x["rrf_score"], reverse=True)
    return ranked[:k]