"""从 faq.json 构建 BM25 倒排索引。

与 build_kb.py 独立：BM25 是关键词检索，向量是语义检索，互为补充。
增量策略：--full 重建；默认跳过已存在索引（faq.json 修改需 --full）。

依赖：jieba、rank-bm25（仅运行时需要 rank-bm25；构建阶段只 jieba）

用法：
    python scripts/build_bm25.py              # 默认从 faq.json 建索引
    python scripts/build_bm25.py --full       # 强制重建
    python scripts/build_bm25.py --query "..." # 检索测试（需 rank-bm25）

输出：backend/data/bm25_index.json（tokenized_corpus + qa_ids + built_at）
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_FAQ = PROJECT_ROOT / "knowledge-base" / "qa" / "faq.json"
DEFAULT_OUT = PROJECT_ROOT / "backend" / "data" / "bm25_index.json"


sys.path.insert(0, str(PROJECT_ROOT / "backend"))
from app.rag.bm25 import tokenize


def build(faq_path: Path, out_path: Path, force: bool) -> dict:
    qas = json.loads(faq_path.read_text(encoding="utf-8"))
    if not qas:
        raise SystemExit(f"{faq_path} 为空")

    if out_path.exists() and not force:
        print(f"索引已存在：{out_path}（用 --full 强制重建）")
        return {"total": len(qas), "skipped": True}

    out_path.parent.mkdir(parents=True, exist_ok=True)
    qa_ids: list[str] = []
    tokenized: list[list[str]] = []
    for qa in qas:
        qa_id = str(qa["id"]).zfill(3)
        doc = f"{qa['question']}\n{qa['answer']}"
        tokens = tokenize(doc)
        qa_ids.append(qa_id)
        tokenized.append(tokens)

    payload = {
        "qa_ids": qa_ids,
        "tokenized_corpus": tokenized,
        "built_at": datetime.now().isoformat(timespec="seconds"),
        "source": str(faq_path.name),
    }
    out_path.write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    print(f"已写入 {out_path}：{len(qa_ids)} 条，平均每条 {sum(len(t) for t in tokenized) // max(len(tokenized), 1)} token")
    return {"total": len(qa_ids), "skipped": False}


def query(out_path: Path, q: str, k: int) -> None:
    try:
        from rank_bm25 import BM25Okapi
    except ImportError:
        raise SystemExit("未安装 rank-bm25，先 pip install rank-bm25")
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    bm25 = BM25Okapi(payload["tokenized_corpus"])
    scores = bm25.get_scores(tokenize(q))
    top = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)[:k]
    print(f"\nQuery: {q}\n")
    for i, (idx, s) in enumerate(top, 1):
        qa_id = payload["qa_ids"][idx]
        print(f"[{i}] id={qa_id}  bm25={s:.3f}")


def main() -> int:
    p = argparse.ArgumentParser(description="BM25 索引构建")
    p.add_argument("--faq", type=Path, default=DEFAULT_FAQ)
    p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    p.add_argument("--full", action="store_true", help="强制重建")
    p.add_argument("--query", default=None, help="检索测试")
    p.add_argument("-k", type=int, default=5)
    args = p.parse_args()

    if args.query:
        query(args.out, args.query, args.k)
        return 0

    t0 = time.time()
    summary = build(args.faq, args.out, args.full)
    print(f"耗时 {time.time() - t0:.2f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
