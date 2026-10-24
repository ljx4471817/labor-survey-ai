"""Chroma 检索 + BM25 关键词检索 + RRF 融合。

精确余弦相似度实现：绕过 Chroma HNSW 近似索引，直接计算 query 与全量 embedding 的精确 cosine。
加入高置信度直达命中通道：当 top-1 cosine >= direct_hit_threshold 时直接返回，避免 RRF 噪声。
"""
from __future__ import annotations

import functools
import math
import threading
from typing import Any
from concurrent.futures import ThreadPoolExecutor

import chromadb
import requests
from loguru import logger

from app.core.config import settings
from app.rag import index_paths
from app.rag.pure import rrf_fuse

_CHROMA_COLLECTIONS: dict[str, Any] = {}
_COLLECTION_LOCK = threading.Lock()
_EXECUTOR = ThreadPoolExecutor(max_workers=4)

# Embedding 缓存：query -> vector，避免每次请求重新编码相同文本
_EMBED_QUERY_CACHE: dict[str, list[float]] = {}
_EMBED_QUERY_CACHE_LOCK = threading.Lock()

# 全量 embedding 缓存（用于精确余弦排序）。
# key 必须包含 release_id，避免两个 release 恰好同 count 时串包。
_FULL_EMBED_CACHE: dict[str, dict[str, list[float]]] = {}
_FULL_EMBED_CACHE_IDS: dict[str, list[str]] = {}
_FULL_EMBED_CACHE_LOCK = threading.Lock()
_FULL_EMBED_CACHE_COUNT: dict[str, int | None] = {}


def get_collection(release_id: str | None = None):
    """按 release 懒加载 Chroma collection；None 保持旧 flat 索引行为。"""
    cache_key = release_id or "__legacy__"
    collection = _CHROMA_COLLECTIONS.get(cache_key)
    if collection is not None:
        return collection
    with _COLLECTION_LOCK:
        collection = _CHROMA_COLLECTIONS.get(cache_key)
        if collection is None:
            if release_id is None:
                client = chromadb.PersistentClient(path=str(settings.chroma_dir))
                collection_name = settings.chroma_collection
            else:
                client = chromadb.PersistentClient(
                    path=str(index_paths.chroma_dir(release_id))
                )
                collection_name = settings.chroma_collection
            collection = client.get_collection(collection_name)
            _CHROMA_COLLECTIONS[cache_key] = collection
            logger.info(f"Chroma collection 初始化：{cache_key}/{collection_name}")
    return collection


def _doc_type_counts(collection) -> dict[str, int]:
    """Compute the current QA/chunk distribution for one collection."""
    from collections import Counter

    metas = collection.get(include=["metadatas"])["metadatas"] or []
    counts = Counter((m or {}).get("doc_type", "qa") for m in metas)
    return {"qa": counts.get("qa", 0), "chunk": counts.get("chunk", 0), "total": len(metas)}


@functools.lru_cache(maxsize=16)
def _cached_doc_type_counts(cache_key: str, count: int) -> dict[str, int]:
    """Cache immutable release counts; legacy still invalidates by count."""
    release_id = None if cache_key == "__legacy__" else cache_key
    return _doc_type_counts(get_collection(release_id))


def count_by_doc_type(release_id: str | None = None) -> dict[str, int]:
    """返回 QA 与制度 chunk 的条数（首页分开显示用）。"""
    collection = get_collection(release_id)
    return _cached_doc_type_counts(release_id or "__legacy__", collection.count())


def shutdown_executor():
    _EXECUTOR.shutdown(wait=True)


def embed_query(text: str) -> list[float]:
    """把单条 query 转成 embedding（带缓存）。"""
    with _EMBED_QUERY_CACHE_LOCK:
        if text in _EMBED_QUERY_CACHE:
            return _EMBED_QUERY_CACHE[text]
    headers = {
        "Authorization": f"Bearer {settings.dashscope_api_key}",
        "Content-Type": "application/json",
    }
    payload = {"model": settings.dashscope_model, "input": [text]}
    resp = requests.post(
        settings.embedding_url, headers=headers, json=payload, timeout=30
    )
    resp.raise_for_status()
    emb = resp.json()["data"][0]["embedding"]
    with _EMBED_QUERY_CACHE_LOCK:
        _EMBED_QUERY_CACHE[text] = emb
    return emb


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


def _get_all_embeddings(
    collection,
    release_id: str | None = None,
) -> tuple[list[str], list[list[float]]]:
    """Load all embeddings with a release-scoped cache key."""
    cache_key = release_id or "__legacy__"
    with _FULL_EMBED_CACHE_LOCK:
        current_count = collection.count()
        if (
            _FULL_EMBED_CACHE_COUNT.get(cache_key) == current_count
            and _FULL_EMBED_CACHE.get(cache_key)
        ):
            return (
                _FULL_EMBED_CACHE_IDS[cache_key],
                list(_FULL_EMBED_CACHE[cache_key].values()),
            )
        all_ids = collection.get(include=["metadatas"])["ids"]
        batch_size = 100
        all_emb: list[list[float]] = []
        for i in range(0, len(all_ids), batch_size):
            batch_ids = all_ids[i : i + batch_size]
            got = collection.get(ids=batch_ids, include=["metadatas", "embeddings"])
            all_emb.extend(got["embeddings"])
        _FULL_EMBED_CACHE_IDS[cache_key] = all_ids
        _FULL_EMBED_CACHE[cache_key] = dict(zip(all_ids, all_emb))
        _FULL_EMBED_CACHE_COUNT[cache_key] = current_count
        return all_ids, all_emb


def _exact_vector_search(query: str, top_k: int, release_id: str | None = None) -> list[dict]:
    """精确余弦相似度检索：内存排序，避免 HNSW 近似偏差。"""
    collection = get_collection(release_id)
    all_ids, all_emb = _get_all_embeddings(collection, release_id)
    # 拉取完整 meta（仅 id -> question/source，轻量）
    all_meta = collection.get(ids=all_ids, include=["metadatas"])["metadatas"]

    q_emb = embed_query(query)
    scored = []
    for idx, (meta, emb) in enumerate(zip(all_meta, all_emb)):
        score = _cosine(q_emb, emb)
        scored.append((score, all_ids[idx], meta))
    scored.sort(key=lambda x: -x[0])

    items: list[dict] = []
    for score, qa_id, meta in scored[:top_k]:
        # 合成 document：优先使用完整 QA/document；否则回退到 meta 拼接
        document_text = _build_document_text(collection, qa_id, meta)
        items.append(
            {
                "id": qa_id,
                "document": document_text,
                "metadata": meta or {},
                "score": round(score, 4),
            }
        )
    return items


def _build_document_text(collection, doc_id: str, meta: dict | None) -> str:
    """根据 doc_type 构建完整 document 文本。chunk 直接返回 text；QA 返回 question+answer。"""
    doc_type = (meta or {}).get("doc_type", "qa")
    if doc_type == "chunk":
        # chunk meta 里有 'text' 字段
        return (meta or {}).get("text", "")
    # QA：优先从 Chroma 读完整 document（含 question+answer）
    try:
        got = collection.get(ids=[doc_id], include=["documents"])
        if got.get("ids"):
            return got["documents"][0]
    except Exception:
        pass
    # fallback：拼 question+answer
    q = (meta or {}).get("question", "")
    a = (meta or {}).get("answer", "")
    return f"{q}\n{a}".strip()


def _annotate_release_metadata(item: dict, release_id: str | None, kb_id: str | None) -> dict:
    """Attach release provenance while preserving the source QA id."""
    if not release_id:
        return item
    metadata = dict(item.get("metadata") or {})
    metadata["release_id"] = release_id
    if kb_id:
        metadata["kb_id"] = kb_id
        metadata["global_entry_key"] = f"{kb_id}:{release_id}:{item.get('id')}"
    return {**item, "metadata": metadata}


def retrieve(
    query: str,
    top_k: int | None = None,
    release_id: str | None = None,
    kb_id: str | None = None,
) -> list[dict]:
    """Hybrid 检索：精确余弦 + BM25 RRF 融合 + 高置信度直达命中。"""
    from app.rag.bm25 import search as bm25_search

    k = top_k or settings.top_k
    direct_hit_threshold = float(
        getattr(settings, "rag_direct_hit_threshold", 0.75)
    )

    bm25_results: list[dict] = []
    vec_fut = _EXECUTOR.submit(_exact_vector_search, query, k, release_id)
    bm25_fut = _EXECUTOR.submit(bm25_search, query, max(k * 2, 10), release_id)
    try:
        vector_results = vec_fut.result()
    except Exception:
        logger.exception("向量检索失败")
        raise
    try:
        bm25_results = bm25_fut.result()
    except Exception as e:
        logger.warning(f"BM25 检索失败，降级为纯向量: {e}")

    # 高置信度直达命中：top-1 cosine >= threshold 时直接返回
    if vector_results and vector_results[0]["score"] >= direct_hit_threshold:
        top1 = _annotate_release_metadata(vector_results[0], release_id, kb_id)
        logger.info(f"direct-hit: id={top1['id']} score={top1['score']:.3f} query={query[:30]!r}")
        return [top1]

    fused = rrf_fuse(vector_results, bm25_results, k=k)
    if release_id:
        fused = [
            _annotate_release_metadata(item, release_id, kb_id)
            for item in fused
        ]
    top1_vector = vector_results[0]["score"] if vector_results else 0.0
    top1_bm25 = bm25_results[0]["score"] if bm25_results else 0.0
    fused_top1 = fused[0]["rrf_score"] if fused else 0
    logger.info(
        f"retrieve: query={query[:30]!r} vec_top1={top1_vector:.3f} "
        f"bm25_top1={top1_bm25:.3f} fused_top1_rrf={fused_top1:.4f}"
    )
    return fused