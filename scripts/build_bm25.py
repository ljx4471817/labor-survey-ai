"""从 faq.json + chunks.jsonl 构建 BM25 倒排索引（双数据源）。

与 build_kb.py / build_chunks.py 独立：BM25 是关键词检索，向量是语义检索，互为补充。
增量策略：--full 重建。

依赖：jieba、rank-bm25（仅运行时需要 rank-bm25；构建阶段只 jieba）

用法：
    python scripts/build_bm25.py              # 默认从 faq.json 建索引
    python scripts/build_bm25.py --full       # 强制重建（含 chunks 若存在）
    python scripts/build_bm25.py --query "..." # 检索测试（需 rank-bm25）

输出：backend/data/bm25_index.json（tokenized_corpus + ids + meta_by_id + built_at）
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
DEFAULT_CHUNKS = PROJECT_ROOT / "knowledge-base" / "chunks.jsonl"
DEFAULT_OUT = PROJECT_ROOT / "backend" / "data" / "bm25_index.json"


sys.path.insert(0, str(PROJECT_ROOT / "backend"))
from app.rag.bm25 import tokenize


def _load_qa_items(faq_path: Path) -> list[dict]:
    qas = json.loads(faq_path.read_text(encoding="utf-8"))
    items: list[dict] = []
    for qa in qas:
        qa_id = str(qa["id"]).zfill(3)
        meta = {
            "doc_type": "qa",
            "chunk_id": qa_id,
            "question": qa.get("question", ""),
            "answer": qa.get("answer", ""),
            "category": qa.get("category", ""),
            "source": qa.get("source", ""),
            "keywords": ",".join(qa.get("keywords", []) or []),
        }
        if qa.get("image"):
            meta["image"] = qa["image"]
        items.append({
            "id": qa_id,
            "text": f"{qa['question']}\n{qa['answer']}",
            "meta": meta,
        })
    return items


def _load_chunk_items(chunks_path: Path) -> list[dict]:
    items: list[dict] = []
    try:
        f = open(chunks_path, encoding="utf-8")
    except FileNotFoundError:
        return items
    with f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            c = json.loads(line)
            items.append({
                "id": c["chunk_id"],
                "text": f"{c['section']}\n{c['text']}",
                "meta": {
                    "doc_type": c.get("doc_type", "chunk"),
                    "chunk_id": c["chunk_id"],
                    "question": "",
                    "category": "",
                    "source": c.get("source", ""),
                    "section": c.get("section", ""),
                    "text": c.get("text", ""),
                },
            })
    return items


def build(
    faq_path: Path,
    chunks_path: Path,
    out_path: Path,
    force: bool,
) -> dict:
    qa_items = _load_qa_items(faq_path)
    if not qa_items:
        raise SystemExit(f"{faq_path} 为空")

    if out_path.exists() and not force:
        print(f"索引已存在：{out_path}（用 --full 强制重建）")
        return {"total": len(qa_items), "skipped": True}

    chunk_items = _load_chunk_items(chunks_path)
    all_items = qa_items + chunk_items
    out_path.parent.mkdir(parents=True, exist_ok=True)

    ids: list[str] = []
    tokenized: list[list[str]] = []
    meta_by_id: dict[str, dict] = {}
    for item in all_items:
        ids.append(item["id"])
        tokenized.append(tokenize(item["text"]))
        meta_by_id[item["id"]] = item["meta"]

    payload = {
        "ids": ids,
        "tokenized_corpus": tokenized,
        "meta_by_id": meta_by_id,
        "built_at": datetime.now().isoformat(timespec="seconds"),
        "source": f"{faq_path.name} + {chunks_path.name if chunk_items else '—'}",
    }
    out_path.write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    avg_tokens = sum(len(t) for t in tokenized) // max(len(tokenized), 1)
    print(
        f"已写入 {out_path}：{len(ids)} 条 "
        f"(qa={len(qa_items)} chunk={len(chunk_items)}) "
        f"平均每条 {avg_tokens} token"
    )
    return {"total": len(ids), "qa": len(qa_items), "chunk": len(chunk_items), "skipped": False}


def query(out_path: Path, q: str, k: int) -> None:
    try:
        from rank_bm25 import BM25Okapi
    except ImportError:
        raise SystemExit("未安装 rank-bm25，先 pip install rank-bm25")
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    bm25 = BM25Okapi(payload["tokenized_corpus"])
    scores = bm25.get_scores(tokenize(q))
    top = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)[:k]
    ids = payload.get("ids", payload.get("qa_ids", []))
    meta_by_id = payload.get("meta_by_id", {})
    print(f"\nQuery: {q}\n")
    for i, (idx, s) in enumerate(top, 1):
        eid = ids[idx]
        meta = meta_by_id.get(eid, {})
        doc_type = meta.get("doc_type", "qa")
        label = f"{eid} [{doc_type}]"
        if doc_type == "qa":
            label += f" Q:{meta.get('question', '?')[:30]}"
        else:
            label += f" §{meta.get('section', '?')[:30]}"
        print(f"[{i}] {label}  bm25={s:.3f}")


def main() -> int:
    p = argparse.ArgumentParser(description="BM25 索引构建")
    p.add_argument("--faq", type=Path, default=DEFAULT_FAQ)
    p.add_argument("--chunks", type=Path, default=DEFAULT_CHUNKS)
    p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    p.add_argument("--full", action="store_true", help="强制重建")
    p.add_argument("--query", default=None, help="检索测试")
    p.add_argument("-k", type=int, default=5)
    args = p.parse_args()

    if args.query:
        query(args.out, args.query, args.k)
        return 0

    t0 = time.time()
    summary = build(args.faq, args.chunks, args.out, args.full)
    print(f"耗时 {time.time() - t0:.2f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
