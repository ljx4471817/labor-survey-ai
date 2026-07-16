"""Chroma 检索 + BM25 关键词检索 + RRF 融合。

精确余弦相似度实现：绕过 Chroma HNSW 近似索引，直接计算 query 与全量 embedding 的精确 cosine。
加入高置信度直达命中通道：当 top-1 cosine >= direct_hit_threshold 时直接返回，避免 RRF 噪声。
"""
from __future__ import annotations

import math
import threading
from concurrent.futures import ThreadPoolExecutor

import chromadb
import requests
from loguru import logger

from app.core.config import settings
from app.rag.pure import rrf_fuse

_CHROMA_COLLECTION = None
_COLLECTION_LOCK = threading.Lock()
_EXECUTOR = ThreadPoolExecutor(max_workers=4)

# Embedding 缓存：query -> vector，避免每次请求重新编码相同文本
_EMBED_QUERY_CACHE: dict[str, list[float]] = {}
_EMBED_QUERY_CACHE_LOCK = threading.Lock()

# 全量 embedding 缓存（用于精确余弦排序）
# key: (doc_id) -> embedding；用 count 变化触发失效
_FULL_EMBED_CACHE: dict[str, list[float]] = {}
_FULL_EMBED_CACHE_IDS: list[str] = []
_FULL_EMBED_CACHE_LOCK = threading.Lock()
_FULL_EMBED_CACHE_COUNT: int | None = None


def get_collection():
    """懒加载 Chroma client/collection 模块级单例。"""
    global _CHROMA_COLLECTION
    if _CHROMA_COLLECTION is None:
        with _COLLECTION_LOCK:
            if _CHROMA_COLLECTION is None:
                client = chromadb.PersistentClient(path=str(settings.chroma_dir))
                _CHROMA_COLLECTION = client.get_collection(settings.chroma_collection)
                logger.info(f"Chroma collection 初始化：{settings.chroma_collection}")
    return _CHROMA_COLLECTION


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


def _get_all_embeddings(collection) -> tuple[list[str], list[list[float]]]:
    """加载全量 embedding，带内存缓存。当 collection count 变化时失效。"""
    global _FULL_EMBED_CACHE, _FULL_EMBED_CACHE_IDS, _FULL_EMBED_CACHE_COUNT
    with _FULL_EMBED_CACHE_LOCK:
        current_count = collection.count()
        if _FULL_EMBED_CACHE_COUNT == current_count and _FULL_EMBED_CACHE:
            return _FULL_EMBED_CACHE_IDS, list(_FULL_EMBED_CACHE.values())
        # 缓存 miss — 拉取
        all_ids = collection.get(include=["metadatas"])["ids"]
        batch_size = 100
        all_meta: list[dict] = []
        all_emb: list[list[float]] = []
        for i in range(0, len(all_ids), batch_size):
            batch_ids = all_ids[i : i + batch_size]
            got = collection.get(ids=batch_ids, include=["metadatas", "embeddings"])
            all_meta.extend(got["metadatas"])
            all_emb.extend(got["embeddings"])
        _FULL_EMBED_CACHE_IDS = all_ids
        _FULL_EMBED_CACHE = dict(zip(all_ids, all_emb))
        _FULL_EMBED_CACHE_COUNT = current_count
        return _FULL_EMBED_CACHE_IDS, all_emb


def _exact_vector_search(query: str, top_k: int) -> list[dict]:
    """精确余弦相似度检索：内存排序，避免 HNSW 近似偏差。"""
    collection = get_collection()
    all_ids, all_emb = _get_all_embeddings(collection)
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


def retrieve(query: str, top_k: int | None = None) -> list[dict]:
    """Hybrid 检索：精确余弦 + BM25 RRF 融合 + 高置信度直达命中。"""
    from app.rag.bm25 import search as bm25_search

    k = top_k or settings.top_k
    direct_hit_threshold = float(
        getattr(settings, "rag_direct_hit_threshold", 0.75)
    )

    bm25_results: list[dict] = []
    vec_fut = _EXECUTOR.submit(_exact_vector_search, query, k)
    bm25_fut = _EXECUTOR.submit(bm25_search, query, max(k * 2, 10))
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
        top1 = vector_results[0]
        logger.info(f"direct-hit: id={top1['id']} score={top1['score']:.3f} query={query[:30]!r}")
        return [top1]

    fused = rrf_fuse(vector_results, bm25_results, k=k)
    top1_vector = vector_results[0]["score"] if vector_results else 0.0
    top1_bm25 = bm25_results[0]["score"] if bm25_results else 0.0
    fused_top1 = fused[0]["rrf_score"] if fused else 0
    logger.info(
        f"retrieve: query={query[:30]!r} vec_top1={top1_vector:.3f} "
        f"bm25_top1={top1_bm25:.3f} fused_top1_rrf={fused_top1:.4f}"
    )
    return fused