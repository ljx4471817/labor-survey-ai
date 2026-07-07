"""RRF 融合算法 (rrf_fuse) 的纯函数测试。

覆盖：
- 空输入
- 单源（vector / bm25）
- 双源重叠（同 id 在两边）
- rrf_c / bm25_weight 参数
- 输出排序与 k 截断
- id 去重
"""
from app.rag.pure import rrf_fuse


def _v(qid, rank, score=0.9):
    return {"id": qid, "document": f"doc-{qid}", "metadata": {}, "score": score}


def _b(qid, rank):
    return {"id": qid, "document": f"doc-{qid}", "metadata": {}, "score": 0.5}


# === rrf_fuse ===

def test_both_empty_returns_empty():
    assert rrf_fuse([], [], k=5) == []


def test_only_vector_results():
    v_a = [_v("a", 1), _v("b", 2), _v("c", 3)]
    out = rrf_fuse(v_a, [], k=5, rrf_c=60)
    assert len(out) == 3
    # vector only: rrf_score = 1/(60+rank)
    assert abs(out[0]["rrf_score"] - 1 / 61) < 1e-9
    assert abs(out[1]["rrf_score"] - 1 / 62) < 1e-9
    assert abs(out[2]["rrf_score"] - 1 / 63) < 1e-9
    # bm25_rank None，score 保留原 cosine
    assert all(item["bm25_rank"] is None for item in out)
    assert out[0]["score"] == 0.9


def test_only_bm25_results_have_zero_score():
    items = [_b("x", 1), _b("y", 2)]
    out = rrf_fuse([], items, k=5, rrf_c=60, bm25_weight=1.0)
    assert len(out) == 2
    # bm25 only: rrf_score = 1.0 / (60+rank), vector_rank None
    assert abs(out[0]["rrf_score"] - 1 / 61) < 1e-9
    # 关键：score=0 不能用 cosine threshold 误判
    assert all(item["vector_rank"] is None for item in out)
    assert all(item["score"] == 0.0 for item in out)


def test_overlap_sums_scores():
    vec = [_v("a", 1, 0.95), _v("b", 2, 0.8)]
    bm = [_b("a", 1), _b("c", 2)]
    out = rrf_fuse(vec, bm, k=5, rrf_c=60, bm25_weight=1.0)
    by_id = {item["id"]: item for item in out}
    # 重叠的 a: vector_RRF + bm25_RRF = 1/61 + 1/61
    assert abs(by_id["a"]["rrf_score"] - 2 / 61) < 1e-9
    assert by_id["a"]["vector_rank"] == 1
    assert by_id["a"]["bm25_rank"] == 1
    # 仅 vector: score 保留 cosine
    assert by_id["b"]["score"] == 0.8
    assert by_id["b"]["bm25_rank"] is None
    # 仅 bm25: score = 0
    assert by_id["c"]["score"] == 0.0
    assert by_id["c"]["vector_rank"] is None


def test_output_sorted_by_rrf_score_desc():
    vec = [_v("a", 1), _v("b", 2), _v("c", 3)]
    bm = [_b("a", 1), _b("b", 1), _b("c", 1)]
    out = rrf_fuse(vec, bm, k=5)
    scores = [item["rrf_score"] for item in out]
    assert scores == sorted(scores, reverse=True)


def test_k_truncation():
    vec = [_v(f"v{i}", i) for i in range(1, 11)]
    bm = [_b(f"b{i}", i) for i in range(1, 11)]
    out = rrf_fuse(vec, bm, k=3)
    assert len(out) == 3


def test_rrf_c_changes_scores():
    items = [_v("a", 1)]
    out60 = rrf_fuse(items, [], k=5, rrf_c=60)
    out10 = rrf_fuse(items, [], k=5, rrf_c=10)
    # 较小的 rrf_c → 较高 score
    assert out10[0]["rrf_score"] > out60[0]["rrf_score"]


def test_bm25_weight_scales_contribution():
    vec = [_v("a", 1)]
    bm = [_b("a", 1)]
    out_1x = rrf_fuse(vec, bm, k=5, bm25_weight=1.0)
    out_2x = rrf_fuse(vec, bm, k=5, bm25_weight=2.0)
    # 重叠 id: 2x 加权 → rrf_score = 1/61 + 2*1/61 = 3/61
    assert abs(out_2x[0]["rrf_score"] - 3 / 61) < 1e-9
    # 加权后比 1x 大
    assert out_2x[0]["rrf_score"] > out_1x[0]["rrf_score"]


def test_id_dedup_no_duplicate_entries():
    vec = [_v("dup", 1)]
    bm = [_b("dup", 1), _b("dup", 1)]  # 同 id 出现两次
    out = rrf_fuse(vec, bm, k=5)
    ids = [item["id"] for item in out]
    assert ids.count("dup") == 1