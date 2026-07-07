"""Chroma 检索 + BM25 关键词检索 + RRF 融合。

纯函数已拆到 rag/pure.py；本文件保留 IO 层（Chroma client / embedding / 并发调度）。
"""
from __future__ import annotations

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
    """把单条 query 转成 embedding（走 OpenAI 兼容接口）。"""
    headers = {
        "Authorization": f"Bearer {settings.dashscope_api_key}",
        "Content-Type": "application/json",
    }
    payload = {"model": settings.dashscope_model, "input": [text]}
    resp = requests.post(
        settings.embedding_url, headers=headers, json=payload, timeout=30
    )
    resp.raise_for_status()
    return resp.json()["data"][0]["embedding"]


def _vector_search(query: str, top_k: int) -> list[dict]:
    """纯向量检索 Chroma Top-K。"""
    collection = get_collection()
    embedding = embed_query(query)
    result = collection.query(
        query_embeddings=[embedding],
        n_results=top_k,
        include=["documents", "metadatas", "distances"],
    )
    items: list[dict] = []
    for i, qa_id in enumerate(result["ids"][0]):
        dist = result["distances"][0][i]
        items.append({
            "id": qa_id,
            "document": result["documents"][0][i],
            "metadata": result["metadatas"][0][i],
            "score": round(1 - dist, 4),
        })
    return items


def retrieve(query: str, top_k: int | None = None) -> list[dict]:
    """Hybrid 检索：向量 + BM25 RRF 融合（两条路径并发跑）。

    返回 [{id, document, metadata, score, rrf_score, vector_rank, bm25_rank}, ...]
    score 字段保持为向量 cosine 相似度（threshold 0.6 比对不变）。
    """
    from app.rag.bm25 import search as bm25_search

    k = top_k or settings.top_k
    candidate_k = max(k * 2, 10)

    bm25_results: list[dict] = []
    vec_fut = _EXECUTOR.submit(_vector_search, query, candidate_k)
    bm25_fut = _EXECUTOR.submit(bm25_search, query, candidate_k)
    try:
        vector_results = vec_fut.result()
    except Exception:
        logger.exception("向量检索失败")
        raise
    try:
        bm25_results = bm25_fut.result()
    except Exception as e:
        logger.warning(f"BM25 检索失败，降级为纯向量: {e}")

    fused = rrf_fuse(vector_results, bm25_results, k=k)
    top1_vector = vector_results[0]["score"] if vector_results else 0.0
    top1_bm25 = bm25_results[0]["score"] if bm25_results else 0.0
    fused_top1 = fused[0]["rrf_score"] if fused else 0
    logger.info(
        f"retrieve: query='{query[:30]}' vec_top1={top1_vector:.3f} "
        f"bm25_top1={top1_bm25:.3f} fused_top1_rrf={fused_top1:.4f}"
    )
    return fused