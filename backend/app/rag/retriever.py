"""Chroma 检索 + BM25 关键词检索 + RRF 融合。"""
from __future__ import annotations

import requests
from loguru import logger

from app.core.config import settings


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
    import chromadb  # 延迟加载，避免启动时硬依赖

    client = chromadb.PersistentClient(path=str(settings.chroma_dir))
    collection = client.get_collection(settings.chroma_collection)
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


def _rrf_fuse(
    vector_results: list[dict],
    bm25_results: list[dict],
    k: int,
    rrf_k: int = 60,
    bm25_boost: float = 1.0,
) -> list[dict]:
    """Reciprocal Rank Fusion：rank 归一化融合，向量与 BM25 等权。

    朴素 RRF（双系统贡献相加）在中文短文本上会因通用词（"就业"等）
    误把 vector 第 2 的 doc 挤出 Top-K。

    这里采用"信任加权 RRF"：vector 命中保留原始 RRF 全额，BM25 命中按
    bm25_boost 加权（默认 1.0）。同一 doc 在双系统都被命中时，
    总分 = vector_RRF + bm25_boost * bm25_RRF，自动获得"双系统命中"奖励。

    返回项保留原始 vector cosine 相似度在 score 字段，
    threshold 仍按 cosine 比对。
    """
    fused: dict[str, dict] = {}

    for rank, item in enumerate(vector_results, 1):
        qid = item["id"]
        fused[qid] = {
            **item,
            "rrf_score": 1.0 / (rrf_k + rank),
            "vector_rank": rank,
            "bm25_rank": None,
        }

    for rank, item in enumerate(bm25_results, 1):
        qid = item["id"]
        bm25_contrib = bm25_boost / (rrf_k + rank)
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


def retrieve(query: str, top_k: int | None = None) -> list[dict]:
    """Hybrid 检索：向量 + BM25 RRF 融合。

    返回 [{id, document, metadata, score, rrf_score, vector_rank, bm25_rank}, ...]
    score 字段保持为向量 cosine 相似度（threshold 0.6 比对不变）。
    """
    from app.rag.bm25 import search as bm25_search  # 延迟导入

    k = top_k or settings.top_k
    candidate_k = max(k * 2, 10)

    vector_results = _vector_search(query, candidate_k)
    bm25_results: list[dict] = []
    try:
        bm25_results = bm25_search(query, candidate_k)
    except Exception as e:
        logger.warning(f"BM25 检索失败，降级为纯向量: {e}")

    fused = _rrf_fuse(vector_results, bm25_results, k=k)
    top1_vector = vector_results[0]["score"] if vector_results else 0.0
    top1_bm25 = bm25_results[0]["score"] if bm25_results else 0.0
    logger.info(
        f"retrieve: query='{query[:30]}' vec_top1={top1_vector:.3f} "
        f"bm25_top1={top1_bm25:.3f} fused_top1_rrf={fused[0]['rrf_score'] if fused else 0:.4f}"
    )
    return fused


def is_in_scope(query: str) -> bool:
    """粗略判断是否在助手服务范围内（关键词过滤）。"""
    q = query.lower()
    out_of_scope = [
        "行职业编码", "编码",
        "身份证", "电话号码", "个人信息",
        "笑话", "天气", "你好", "你叫什么",
        "excel", "表格汇总", "表格模板", "做个表格",
        "小程序怎么开发", "怎么开发",
        "编访谈", "编一个", "编造", "虚构", "代签",
        "隐私泄露", "泄露隐私",
    ]
    return not any(kw in q for kw in out_of_scope)


def is_ambiguous(query: str) -> bool:
    """粗略判断问题是否模糊。"""
    q = query.strip().rstrip("？?。.!！")
    # 短问题
    if len(q) <= 6:
        return True
    # 短问句（"怎么办/怎么填/怎么算"）
    if q in {"怎么办", "怎么填", "怎么算", "怎么登", "什么意思"}:
        return True
    # "这个X" 类指代不明（"这个指标/情况/指标/数据/问题"）
    if q.startswith("这个") or q.startswith("那个"):
        return True
    return False
