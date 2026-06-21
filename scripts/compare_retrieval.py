"""对比纯向量 vs Hybrid 检索的 Top-K 差异。

跑一组针对性 query（含口语化、缩写、专有名词），
分别看两种方式召回的 id 集合有何不同。

不调 LLM，只比对 retrieval。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from app.rag.bm25 import search as bm25_search  # noqa: E402
from app.rag.retriever import _vector_search  # noqa: E402

# 针对性 query：覆盖口语化、缩写、专有名词
TEST_QUERIES = [
    # 缩写 / 行业黑话
    ("PAD没网", "术语缩写"),
    ("F16指标", "指标代码"),
    ("F20", "纯指标代码"),
    ("GDP", "无关缩写（应召回低相关或拒答）"),
    # 口语化
    ("一个人没事干算失业吗", "口语化表述"),
    ("打工三天算不算工作", "口语化"),
    ("在老家种地但是户口在城里", "口语化+复杂场景"),
    # 模糊
    ("这个怎么填", "模糊（应触发 ambiguous）"),
    # 完全无关
    ("今天天气怎么样", "无关（应触发 out_of_scope）"),
    # 已有标准问题
    ("每周工作15小时算就业吗", "标准问法 1"),
    ("一个人在家打游戏帮别人账号练级赚钱", "标准问法 2"),
]


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--top-k", type=int, default=5)
    p.add_argument("--out", type=Path, default=PROJECT_ROOT / "reports" / "retrieval-compare.json")
    args = p.parse_args()

    results: list[dict] = []
    for q, label in TEST_QUERIES:
        vec = _vector_search(q, args.top_k)
        bm25 = bm25_search(q, args.top_k)
        vec_ids = [item["id"] for item in vec]
        bm25_ids = [item["id"] for item in bm25]
        only_bm25 = [i for i in bm25_ids if i not in vec_ids]
        only_vec = [i for i in vec_ids if i not in bm25_ids]
        results.append({
            "query": q,
            "label": label,
            "vector_top_k": vec_ids,
            "bm25_top_k": bm25_ids,
            "only_in_bm25": only_bm25,
            "only_in_vector": only_vec,
            "overlap": len(vec_ids) - len(only_vec),
        })
        print(f"\n[{label}] {q}")
        print(f"  vector top-{args.top_k}: {vec_ids}")
        print(f"  bm25   top-{args.top_k}: {bm25_ids}")
        print(f"  overlap={len(vec_ids) - len(only_vec)}, only_bm25={only_bm25}, only_vec={only_vec}")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(results, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n报告: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
