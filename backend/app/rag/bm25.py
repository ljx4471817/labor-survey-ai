"""BM25 关键词检索。

与 retriever.py 配合做 Hybrid：向量召回调语义，BM25 召回调精确术语。
索引由 scripts/build_bm25.py 离线构建，启动时加载到内存。

返回结构与 retrieve() 对齐：[{id, document, metadata, score}, ...]
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import jieba
from loguru import logger

from app.core.config import PROJECT_ROOT

INDEX_PATH = PROJECT_ROOT / "backend" / "data" / "bm25_index.json"
FAQ_PATH = PROJECT_ROOT / "knowledge-base" / "qa" / "faq.json"


@lru_cache(maxsize=1)
def _load_index() -> tuple[list[str], list[list[str]], dict[str, dict]]:
    """加载 BM25 索引 + faq 元数据。启动时调用一次，之后走缓存。

    Returns:
        qa_ids: 与 tokenized_corpus 一一对应
        tokenized_corpus: BM25Okapi 需要的语料
        meta_by_id: qa_id → {question, answer, category, source, keywords}
    """
    if not INDEX_PATH.exists():
        raise FileNotFoundError(
            f"BM25 索引不存在：{INDEX_PATH}\n请先跑 python scripts/build_bm25.py --full"
        )
    payload = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    qa_ids: list[str] = payload["qa_ids"]
    tokenized_corpus: list[list[str]] = payload["tokenized_corpus"]

    meta_by_id: dict[str, dict] = {}
    if FAQ_PATH.exists():
        for qa in json.loads(FAQ_PATH.read_text(encoding="utf-8")):
            qid = str(qa["id"]).zfill(3)
            meta_by_id[qid] = {
                "question": qa.get("question", ""),
                "answer": qa.get("answer", ""),
                "category": qa.get("category", ""),
                "source": qa.get("source", ""),
                "keywords": qa.get("keywords", []),
            }

    logger.info(
        f"BM25 索引加载：{len(qa_ids)} 条，built_at={payload.get('built_at', '?')}"
    )
    return qa_ids, tokenized_corpus, meta_by_id


def _tokenize(text: str) -> list[str]:
    return [t for t in jieba.cut(text) if t.strip()]


def search(query: str, top_k: int) -> list[dict]:
    """BM25 检索 Top-K。返回 [{id, document, metadata, score}, ...]

    score 是 BM25 原始分数（不是相似度），越大越相关。0 表示无命中。
    """
    try:
        from rank_bm25 import BM25Okapi
    except ImportError:
        raise RuntimeError("未安装 rank-bm25，先 pip install rank-bm25")

    qa_ids, tokenized_corpus, meta_by_id = _load_index()
    bm25 = BM25Okapi(tokenized_corpus)
    scores = bm25.get_scores(_tokenize(query))
    ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)[:top_k]

    results: list[dict] = []
    for idx, s in ranked:
        if s <= 0:
            continue
        qa_id = qa_ids[idx]
        meta = meta_by_id.get(qa_id, {})
        doc = f"{meta.get('question', '')}\n{meta.get('answer', '')}"
        results.append({
            "id": qa_id,
            "document": doc,
            "metadata": {
                "question": meta.get("question", ""),
                "category": meta.get("category", ""),
                "source": meta.get("source", ""),
                "keywords": ",".join(meta.get("keywords", []) or []),
            },
            "score": round(float(s), 4),
        })
    logger.info(
        f"bm25_search: query='{query[:30]}' top1_score={results[0]['score'] if results else 0:.3f}"
    )
    return results
