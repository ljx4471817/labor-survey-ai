"""把 faq.json 入库到 Chroma 向量库。

读取 knowledge-base/qa/faq.json，调用 Embedding API（默认 DashScope OpenAI 兼容接口），写入
backend/data/chroma/ 持久化目录。支持 --full / --incremental / --query 三种模式。

依赖：chromadb、requests、python-dotenv

用法：
    python scripts/build_kb.py                       # 增量（默认）
    python scripts/build_kb.py --full                # 重建 QA（保留制度 chunk）
    python scripts/build_kb.py --provider dashscope
    python scripts/build_kb.py --query "每周工作15小时算就业吗？"   # 检索测试

环境变量（写入项目根 .env，不要提交）：
    EMBEDDING_PROVIDER=dashscope     # bge | dashscope
    DASHSCOPE_API_KEY=...
    DASHSCOPE_MODEL=text-embedding-v4
    BGE_API_KEY=...                  # 用 BGE 时填
    BGE_MODEL=BAAI/bge-large-zh-v1.5
    BGE_API_URL=https://api.bge.modelbest.cn/v1/embeddings
    CHROMA_DIR=backend/data/chroma
    CHROMA_COLLECTION=labor_survey_qa
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Iterable

import requests
from dotenv import load_dotenv

try:
    import chromadb
except ImportError:
    chromadb = None  # type: ignore[assignment]

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_FAQ = PROJECT_ROOT / "knowledge-base" / "qa" / "faq.json"
DEFAULT_CHROMA_DIR = PROJECT_ROOT / "backend" / "data" / "chroma"
DEFAULT_COLLECTION = "labor_survey_qa"

# DashScope OpenAI 兼容接口 batch 上限为 10。
# BGE 限制 16。统一取最小值 10。
BATCH_SIZE = 10


def load_faq(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"{path} 根节点必须是数组")
    return data


def embedding_hash(text: str) -> str:
    """用于 --incremental 判断是否需要重新入库。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


class EmbeddingClient:
    """统一封装 BGE / DashScope 两种 Embedding 提供方。

    都走 OpenAI 兼容格式：{"model": ..., "input": [str, ...]}，
    响应格式：{"data": [{"embedding": [...]}, ...]}。
    """

    PROVIDERS = {
        "bge": {
            "key_env": "BGE_API_KEY",
            "model_env": "BGE_MODEL",
            "model_default": "BAAI/bge-large-zh-v1.5",
            "url_env": "BGE_API_URL",
            "url_default": "https://api.bge.modelbest.cn/v1/embeddings",
        },
        "dashscope": {
            "key_env": "DASHSCOPE_API_KEY",
            "model_env": "DASHSCOPE_MODEL",
            "model_default": "text-embedding-v4",
            "url_env": None,  # 固定
            "url_default": "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings",
        },
    }

    def __init__(self, provider: str) -> None:
        self.provider = provider.lower()
        if self.provider not in self.PROVIDERS:
            raise ValueError(f"不支持的 provider: {provider}")
        cfg = self.PROVIDERS[self.provider]
        self.api_key = os.environ.get(cfg["key_env"], "").strip()
        self.model = os.environ.get(cfg["model_env"], cfg["model_default"])
        if cfg["url_env"]:
            self.url = os.environ.get(cfg["url_env"], cfg["url_default"])
        else:
            self.url = cfg["url_default"]
        if not self.api_key:
            raise SystemExit(
                f"未找到 {provider} 的 API Key（{cfg['key_env']}）"
            )

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if len(texts) > BATCH_SIZE:
            raise ValueError(f"batch 大小 {len(texts)} 超过上限 {BATCH_SIZE}")
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {"model": self.model, "input": texts}
        resp = requests.post(self.url, headers=headers, json=payload, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        return [item["embedding"] for item in data["data"]]


def chunked(seq: list, size: int) -> Iterable[list]:
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


def qa_to_chroma_record(qa: dict) -> tuple[str, str, dict]:
    """把一条 QA 转成 Chroma 需要的 (id, document, metadata)。"""
    qa_id = str(qa["id"]).zfill(3)
    doc = f"{qa['question']}\n{qa['answer']}"
    meta = {
        "doc_type": "qa",
        "qa_id": qa_id,
        "category": qa.get("category", ""),
        "source": qa.get("source", ""),
        "question": qa["question"],
        "keywords": ",".join(qa.get("keywords", []) or []),
    }
    # 可选：关联图片路径
    img = qa.get("image")
    if img:
        meta["image"] = img
    return qa_id, doc, meta


def get_existing_hashes(collection) -> set[str]:
    """拉取已入库 QA 的 embedding 输入文本 hash，用于增量判断。"""
    existing = collection.get(include=["metadatas"])
    hashes: set[str] = set()
    for meta in existing.get("metadatas", []) or []:
        if _is_qa_metadata(meta) and "embed_hash" in meta:
            hashes.add(meta["embed_hash"])
    return hashes


def _is_qa_metadata(meta: dict | None) -> bool:
    """兼容旧 QA 元数据：早期记录没有 doc_type，但始终包含 qa_id。"""
    return bool(meta) and (meta.get("doc_type") == "qa" or "qa_id" in meta)


def delete_existing_qas(collection) -> int:
    """只删除共享 collection 中的 QA，保留制度原文 chunk。"""
    existing = collection.get(include=["metadatas"])
    stale_ids = [
        record_id
        for record_id, meta in zip(
            existing.get("ids", []), existing.get("metadatas", []) or []
        )
        if _is_qa_metadata(meta)
    ]
    if stale_ids:
        collection.delete(ids=stale_ids)
    return len(stale_ids)


def build(
    faq_path: Path,
    chroma_dir: Path,
    collection_name: str,
    provider: str,
    full: bool,
) -> dict:
    if chromadb is None:
        raise SystemExit("未安装 chromadb，先 `pip install chromadb`")
    qas = load_faq(faq_path)
    if not qas:
        raise SystemExit(f"{faq_path} 为空")
    print(f"读取 {len(qas)} 条 QA")
    chroma_dir.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(chroma_dir))
    collection = client.get_or_create_collection(
        name=collection_name,
        metadata={"hnsw:space": "cosine"},
    )
    if full:
        deleted = delete_existing_qas(collection)
        print(f"已删除 {deleted} 条旧 QA（保留制度 chunk）")
    existing_hashes = set() if full else get_existing_hashes(collection)
    if existing_hashes:
        print(f"增量模式：跳过 {len(existing_hashes)} 条已入库")
    embed_client = EmbeddingClient(provider)
    added = 0
    skipped = 0
    failed = 0
    for batch in chunked(qas, BATCH_SIZE):
        ids: list[str] = []
        docs: list[str] = []
        metas: list[dict] = []
        inputs: list[str] = []
        for qa in batch:
            qa_id, doc, meta = qa_to_chroma_record(qa)
            h = embedding_hash(doc)
            if h in existing_hashes:
                skipped += 1
                continue
            meta["embed_hash"] = h
            ids.append(qa_id)
            docs.append(doc)
            metas.append(meta)
            inputs.append(doc)
        if not inputs:
            continue
        embeddings: list[list[float]] = []
        try:
            for attempt in range(3):
                try:
                    embeddings = embed_client.embed_batch(inputs)
                    break
                except requests.HTTPError as e:
                    if attempt == 2:
                        raise
                    wait = 2 ** attempt
                    print(f"  HTTP {e.response.status_code}，{wait}s 后重试...")
                    time.sleep(wait)
        except Exception as e:
            failed += len(inputs)
            print(f"  批次失败（id={ids[0]}..{ids[-1]}）：{e}")
            continue
        collection.upsert(ids=ids, documents=docs, metadatas=metas, embeddings=embeddings)
        added += len(ids)
        print(f"  +{added} / 跳过 {skipped} / 失败 {failed}", end="\r")
    print()
    return {"total": len(qas), "added": added, "skipped": skipped, "failed": failed}


def query(collection_name: str, chroma_dir: Path, provider: str, q: str, k: int) -> None:
    if chromadb is None:
        raise SystemExit("未安装 chromadb")
    client = chromadb.PersistentClient(path=str(chroma_dir))
    collection = client.get_collection(collection_name)
    embed_client = EmbeddingClient(provider)
    embedding = embed_client.embed_batch([q])[0]
    result = collection.query(query_embeddings=[embedding], n_results=k)
    print(f"\nQuery: {q}\n")
    for i, (qa_id, doc, meta, dist) in enumerate(
        zip(
            result["ids"][0],
            result["documents"][0],
            result["metadatas"][0],
            result["distances"][0],
        ),
        start=1,
    ):
        score = 1 - dist
        print(f"[{i}] id={qa_id}  score={score:.3f}  category={meta.get('category')}")
        print(f"     Q: {meta.get('question')}")
        print(f"     source: {meta.get('source')}")
        print()


def main() -> int:
    load_dotenv(PROJECT_ROOT / ".env")
    p = argparse.ArgumentParser(description="FAQ 入库 Chroma")
    p.add_argument("--faq", type=Path, default=DEFAULT_FAQ)
    p.add_argument("--chroma-dir", type=Path, default=DEFAULT_CHROMA_DIR)
    p.add_argument("--collection", default=DEFAULT_COLLECTION)
    p.add_argument(
        "--provider",
        default=os.environ.get("EMBEDDING_PROVIDER", "dashscope"),
        choices=["bge", "dashscope"],
    )
    p.add_argument(
        "--full", action="store_true", help="全量重建 QA（保留同 collection 的 chunk）"
    )
    p.add_argument("--query", default=None, help="构建后跑一次检索测试")
    p.add_argument("--k", type=int, default=5)
    args = p.parse_args()
    if args.query:
        query(args.collection, args.chroma_dir, args.provider, args.query, args.k)
        return 0
    summary = build(
        args.faq, args.chroma_dir, args.collection, args.provider, args.full
    )
    print(
        f"\n完成：total={summary['total']} added={summary['added']} "
        f"skipped={summary['skipped']} failed={summary['failed']}"
    )
    if summary["failed"] > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
