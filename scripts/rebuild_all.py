# -*- coding: utf-8 -*-
"""一站式重建脚本：QA + Chunks + BM25。

使用场景：
- 更新 faq.json 后
- 更新 knowledge-base/raw/markdown/*.md 后
- 切换 embedding provider 后
- 任何需要全量重建 Chroma + BM25 索引的场景

流程：
1. 从 4 个 markdown 源文件重新生成 chunks.jsonl
2. 从 chunks.jsonl 写入 Chroma chunk 条目
3. 从 faq.json 写入 Chroma QA 条目
4. 从 faq.json + chunks.jsonl 构建 BM25 索引

用法：
    python scripts/rebuild_all.py              # 默认全量重建
    python scripts/rebuild_all.py --incremental  # 增量模式（仅更新变动条目）
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

import chromadb
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "backend"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

load_dotenv(PROJECT_ROOT / ".env")

from build_chunks import (
    EmbeddingClient,
    text_hash,
    parse_markdown,
    split_paragraphs,
    BATCH_SIZE,
    INDICATOR_RE,
    CHUNK_SIZE,
    CHUNK_OVERLAP,
)
from build_kb import EmbeddingClient as KBEmbeddingClient  # 同构


def make_chunks_for_md(md_path: Path) -> list[dict]:
    """从单个 markdown 文件生成 chunk 列表。"""
    source = md_path.name
    doc_id = hashlib.sha1(str(md_path.resolve()).encode()).hexdigest()[:12]
    sections = parse_markdown(md_path)
    result = []
    idx = 0
    current_h2 = ""
    for sec in sections:
        heading = sec["heading"]
        level = sec["level"]
        content = sec["content"]
        if not content:
            continue
        if level == 2:
            current_h2 = heading
        section_path = f"{current_h2} / {heading}" if current_h2 and level == 3 else heading
        for chunk_text in split_paragraphs(content):
            indicators = list(dict.fromkeys(INDICATOR_RE.findall(chunk_text)))
            result.append(
                {
                    "chunk_id": f"{doc_id}#{idx:03d}",
                    "doc_id": doc_id,
                    "doc_type": "chunk",
                    "source": source,
                    "section": section_path,
                    "indicators": indicators,
                    "text": chunk_text,
                    "text_hash": text_hash(chunk_text),
                }
            )
            idx += 1
    return result


def regenerate_chunks_jsonl(md_root: Path, chunks_path: Path) -> list[dict]:
    """从所有 markdown 源文件重新生成 chunks.jsonl。"""
    all_chunks = []
    for md_path in sorted(md_root.glob("*.md")):
        try:
            chunks = make_chunks_for_md(md_path)
        except Exception as e:
            print(f"[WARN] skip {md_path.name}: {e}")
            continue
        all_chunks.extend(chunks)
        print(f"  {md_path.name}: {len(chunks)} chunks")

    # 写 chunks.jsonl
    with open(chunks_path, "w", encoding="utf-8") as f:
        for c in all_chunks:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    print(f"chunks.jsonl 已写入 {len(all_chunks)} 条")
    return all_chunks


def upsert_chunks_to_chroma(
    chunks: list[dict], chroma_dir: Path, collection_name: str, provider: str, full: bool
) -> dict:
    """把 chunk 列表写入 Chroma。"""
    chroma_dir.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(chroma_dir))
    collection = client.get_or_create_collection(
        name=collection_name, metadata={"hnsw:space": "cosine"}
    )

    if full:
        existing = collection.get(include=["metadatas"])
        stale_ids = [
            eid
            for eid, meta in zip(existing["ids"], existing["metadatas"] or [])
            if meta and (meta.get("doc_type") == "chunk")
        ]
        if stale_ids:
            collection.delete(ids=stale_ids)
            print(f"已删除 {len(stale_ids)} 条旧 chunk")

    existing_hashes: set[str] = set()
    if not full:
        existing = collection.get(include=["metadatas"])
        for meta in existing.get("metadatas", []) or []:
            if meta and "embed_hash" in meta:
                existing_hashes.add(meta["embed_hash"])

    embed_client = EmbeddingClient(provider)
    added = skipped = failed = 0
    for i in range(0, len(chunks), BATCH_SIZE):
        batch = chunks[i : i + BATCH_SIZE]
        ids, docs, metas, inputs = [], [], [], []
        for c in batch:
            if c["text_hash"] in existing_hashes:
                skipped += 1
                continue
            doc = f"{c['section']}\n{c['text']}"
            meta = {
                "doc_type": "chunk",
                "doc_id": c["doc_id"],
                "chunk_id": c["chunk_id"],
                "source": c["source"],
                "section": c["section"],
                "indicators": ",".join(c["indicators"]),
                "question": "",
                "category": "",
                "embed_hash": c["text_hash"],
            }
            ids.append(c["chunk_id"])
            docs.append(doc)
            metas.append(meta)
            inputs.append(doc)
        if not inputs:
            continue
        for attempt in range(5):
            try:
                embs = embed_client.embed_batch(inputs)
                break
            except Exception:
                time.sleep(2 * attempt + 2)
        else:
            failed += len(inputs)
            print(f"  [FAIL] chunk batch {i // BATCH_SIZE}: {len(inputs)} items")
            continue
        collection.upsert(ids=ids, documents=docs, metadatas=metas, embeddings=embs)
        added += len(inputs)
        time.sleep(0.3)
    return {"added": added, "skipped": skipped, "failed": failed}


def upsert_qa_to_chroma(
    faq_path: Path, chroma_dir: Path, collection_name: str, provider: str, full: bool
) -> dict:
    """把 faq.json 写入 Chroma。"""
    with open(faq_path, encoding="utf-8") as f:
        qas = json.loads(f.read().lstrip("﻿"))

    chroma_dir.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(chroma_dir))
    collection = client.get_or_create_collection(
        name=collection_name, metadata={"hnsw:space": "cosine"}
    )

    if full:
        existing = collection.get(include=["metadatas"])
        stale_ids = [
            eid
            for eid, meta in zip(existing["ids"], existing["metadatas"] or [])
            if meta and (meta.get("doc_type") == "qa" or "qa_id" in meta)
        ]
        if stale_ids:
            collection.delete(ids=stale_ids)
            print(f"已删除 {len(stale_ids)} 条旧 QA")

    existing_hashes: set[str] = set()
    if not full:
        existing = collection.get(include=["metadatas"])
        for meta in existing.get("metadatas", []) or []:
            if meta and "embed_hash" in meta:
                existing_hashes.add(meta["embed_hash"])

    embed_client = KBEmbeddingClient(provider)
    added = skipped = failed = 0
    for i in range(0, len(qas), BATCH_SIZE):
        batch = qas[i : i + BATCH_SIZE]
        ids, docs, metas, inputs = [], [], [], []
        for qa in batch:
            qa_id = str(qa["id"]).zfill(3)
            doc = f"{qa['question']}\n{qa['answer']}"
            h = hashlib.sha256(doc.encode()).hexdigest()[:16]
            if h in existing_hashes:
                skipped += 1
                continue
            meta = {
                "doc_type": "qa",
                "qa_id": qa_id,
                "category": qa.get("category", ""),
                "source": qa.get("source", ""),
                "question": qa["question"],
                "keywords": ",".join(qa.get("keywords", []) or []),
                "embed_hash": h,
            }
            ids.append(qa_id)
            docs.append(doc)
            metas.append(meta)
            inputs.append(doc)
        if not inputs:
            continue
        for attempt in range(5):
            try:
                embs = embed_client.embed_batch(inputs)
                break
            except Exception:
                time.sleep(2 * attempt + 2)
        else:
            failed += len(inputs)
            print(f"  [FAIL] QA batch {i // BATCH_SIZE}: {len(inputs)} items")
            continue
        collection.upsert(ids=ids, documents=docs, metadatas=metas, embeddings=embs)
        added += len(inputs)
        time.sleep(0.3)
    return {"added": added, "skipped": skipped, "failed": failed}


def build_bm25(faq_path: Path, chunks_path: Path, out_path: Path) -> dict:
    """构建 BM25 索引。"""
    import jieba
    from rank_bm25 import BM25Okapi

    def tokenize(text):
        return [t for t in jieba.cut(text) if t.strip()]

    qas = json.loads(faq_path.read_text(encoding="utf-8").lstrip("﻿"))
    qa_items = []
    for qa in qas:
        qa_id = str(qa["id"]).zfill(3)
        qa_items.append(
            {
                "id": qa_id,
                "text": f"{qa['question']}\n{qa['answer']}",
                "meta": {
                    "doc_type": "qa",
                    "chunk_id": qa_id,
                    "question": qa.get("question", ""),
                    "answer": qa.get("answer", ""),
                    "category": qa.get("category", ""),
                    "source": qa.get("source", ""),
                    "keywords": ",".join(qa.get("keywords", []) or []),
                },
            }
        )

    chunk_items = []
    with open(chunks_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            c = json.loads(line)
            chunk_items.append(
                {
                    "id": c["chunk_id"],
                    "text": f"{c['section']}\n{c['text']}",
                    "meta": {
                        "doc_type": "chunk",
                        "chunk_id": c["chunk_id"],
                        "question": "",
                        "category": "",
                        "source": c.get("source", ""),
                        "section": c.get("section", ""),
                        "text": c.get("text", ""),
                    },
                }
            )

    all_items = qa_items + chunk_items
    ids = [item["id"] for item in all_items]
    tokenized = [tokenize(item["text"]) for item in all_items]
    meta_by_id = {item["id"]: item["meta"] for item in all_items}

    payload = {
        "ids": ids,
        "tokenized_corpus": tokenized,
        "meta_by_id": meta_by_id,
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "source": f"{faq_path.name} + {chunks_path.name}",
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    avg_tokens = sum(len(t) for t in tokenized) // max(len(tokenized), 1)
    return {
        "total": len(ids),
        "qa": len(qa_items),
        "chunk": len(chunk_items),
        "avg_tokens": avg_tokens,
    }


def main():
    parser = argparse.ArgumentParser(description="一站式重建 QA + Chunks + BM25")
    parser.add_argument(
        "--incremental", action="store_true", help="增量模式（仅更新变动条目）"
    )
    parser.add_argument(
        "--md-root",
        type=Path,
        default=PROJECT_ROOT / "knowledge-base" / "raw" / "markdown",
    )
    parser.add_argument(
        "--chunks", type=Path, default=PROJECT_ROOT / "knowledge-base" / "chunks.jsonl"
    )
    parser.add_argument(
        "--faq", type=Path, default=PROJECT_ROOT / "knowledge-base" / "qa" / "faq.json"
    )
    parser.add_argument(
        "--chroma-dir", type=Path, default=PROJECT_ROOT / "backend" / "data" / "chroma"
    )
    parser.add_argument(
        "--collection", default=os.environ.get("CHROMA_COLLECTION", "labor_survey_qa")
    )
    parser.add_argument(
        "--bm25-out",
        type=Path,
        default=PROJECT_ROOT / "backend" / "data" / "bm25_index.json",
    )
    parser.add_argument("--provider", default=os.environ.get("EMBEDDING_PROVIDER", "dashscope"))
    args = parser.parse_args()

    full = not args.incremental
    t0 = time.time()

    print("=" * 60)
    print(f"模式: {'全量重建' if full else '增量更新'}")
    print("=" * 60)

    # Step 1: 从 markdown 重新生成 chunks.jsonl
    print("\n[1/4] 从 markdown 源文件重新生成 chunks.jsonl...")
    all_chunks = regenerate_chunks_jsonl(args.md_root, args.chunks)

    # Step 2: 写入 Chroma chunk 条目
    print("\n[2/4] 写入 Chroma chunk 条目...")
    chunk_stats = upsert_chunks_to_chroma(
        all_chunks, args.chroma_dir, args.collection, args.provider, full
    )
    print(f"  chunk: added={chunk_stats['added']} skipped={chunk_stats['skipped']} failed={chunk_stats['failed']}")

    # Step 3: 写入 Chroma QA 条目
    print("\n[3/4] 写入 Chroma QA 条目...")
    qa_stats = upsert_qa_to_chroma(
        args.faq, args.chroma_dir, args.collection, args.provider, full
    )
    print(f"  QA: added={qa_stats['added']} skipped={qa_stats['skipped']} failed={qa_stats['failed']}")

    # Step 4: 构建 BM25 索引
    print("\n[4/4] 构建 BM25 索引...")
    bm25_stats = build_bm25(args.faq, args.chunks, args.bm25_out)
    print(f"  BM25: total={bm25_stats['total']} (qa={bm25_stats['qa']} chunk={bm25_stats['chunk']})")

    elapsed = time.time() - t0
    print(f"\n{'=' * 60}")
    print(f"完成！耗时 {elapsed:.1f}s")
    print(f"  Chroma: {args.chroma_dir}")
    print(f"  BM25:   {args.bm25_out}")
    print(f"=" * 60)


if __name__ == "__main__":
    sys.exit(main())