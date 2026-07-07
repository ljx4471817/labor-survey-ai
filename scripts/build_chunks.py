"""Markdown → chunk → Chroma 入库。

独立于 build_kb.py：chunk 来源是 markdown 而非 faq.json。
与 QA 解耦——不依赖 faq.json / build_kb.py。

用法：
    python scripts/build_chunks.py --input xxx.md --dry-run   # 只切分打印
    python scripts/build_chunks.py --input xxx.md --full      # 切分 + 入库 Chroma
    python scripts/build_chunks.py --input xxx.md             # 增量模式

输出：
    knowledge-base/chunks.jsonl（追加/覆盖）
    Chroma collection 新增 chunk 文档（doc_type='chunk'）
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

try:
    import chromadb
except ImportError:
    chromadb = None  # type: ignore[assignment]

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CHUNKS_OUT = PROJECT_ROOT / "knowledge-base" / "chunks.jsonl"
DEFAULT_CHROMA_DIR = PROJECT_ROOT / "backend" / "data" / "chroma"
DEFAULT_COLLECTION = "labor_survey_qa"

BATCH_SIZE = 10
INDICATOR_RE = re.compile(r'(?<![A-Za-z])[FH]\d+(?:\.\d+)?')
CHUNK_SIZE = 700
CHUNK_OVERLAP = 100


# ── EmbeddingClient（与 build_kb.py 同源，独立拷贝避免导入耦合） ──

class EmbeddingClient:
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
            "model_default": "text-embedding-v3",
            "url_env": None,
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
            raise SystemExit(f"未找到 {provider} 的 API Key（{cfg['key_env']}）")

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
        return [item["embedding"] for item in resp.json()["data"]]


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def chunked(seq: list, size: int) -> list[list]:
    return [seq[i : i + size] for i in range(0, len(seq), size)]


# ── Markdown 解析 + chunk 切分 ──

def parse_markdown(md_path: Path) -> list[dict]:
    """解析 markdown 为章节树。

    返回 [{heading: str, level: int, content: str}, ...]
    """
    text = md_path.read_text(encoding="utf-8")
    lines = text.split("\n")
    sections: list[dict] = []
    current_heading = ""
    current_level = 0
    current_lines: list[str] = []

    for line in lines:
        m = re.match(r'^(#{2,3})\s+(.+)', line)
        if m:
            if current_lines:
                sections.append({
                    "heading": current_heading,
                    "level": current_level,
                    "content": "\n".join(current_lines).strip(),
                })
            current_level = len(m.group(1))
            current_heading = m.group(2).strip()
            current_lines = []
        else:
            current_lines.append(line)

    if current_lines:
        sections.append({
            "heading": current_heading,
            "level": current_level,
            "content": "\n".join(current_lines).strip(),
        })

    return sections


def extract_indicators(text: str) -> list[str]:
    codes = INDICATOR_RE.findall(text)
    seen: set[str] = set()
    result: list[str] = []
    for c in codes:
        if c not in seen:
            seen.add(c)
            result.append(c)
    return result


def make_chunks(md_path: Path) -> list[dict]:
    """解析 markdown，按 H2/H3 切 chunk。"""
    source = md_path.name
    doc_id = hashlib.sha1(str(md_path.resolve()).encode()).hexdigest()[:12]
    sections = parse_markdown(md_path)

    result: list[dict] = []
    idx = 0

    # 收集 H2→H3 层级关系以构建 section 路径
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

        text_chunks = split_paragraphs(content)

        for chunk_text in text_chunks:
            indicators = extract_indicators(chunk_text)
            result.append({
                "chunk_id": f"{doc_id}#{idx:03d}",
                "doc_id": doc_id,
                "doc_type": "chunk",
                "source": source,
                "section": section_path,
                "indicators": indicators,
                "text": chunk_text,
                "text_hash": text_hash(chunk_text),
            })
            idx += 1

    return result


def split_paragraphs(text: str) -> list[str]:
    """按段落切 chunk，贪心合并到 CHUNK_SIZE，加 overlap。"""
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        return []

    chunks: list[str] = []
    current = ""
    for p in paragraphs:
        if len(current) + len(p) <= CHUNK_SIZE:
            current = (current + "\n\n" + p).strip()
        else:
            if current:
                chunks.append(current)
            # 从当前段开头 + 上次末尾 overlap
            if chunks and CHUNK_OVERLAP > 0:
                tail = current[-CHUNK_OVERLAP:] if len(current) > CHUNK_OVERLAP else current
                current = tail + "\n\n" + p
            else:
                current = p
    if current:
        chunks.append(current)
    return chunks


# ── Chroma 入库 ──

def upsert_chunks(
    chunks: list[dict],
    chroma_dir: Path,
    collection_name: str,
    provider: str,
    full: bool,
) -> dict:
    if chromadb is None:
        raise SystemExit("未安装 chromadb，先 pip install chromadb")

    if not chunks:
        raise SystemExit("chunk 列表为空")

    chroma_dir.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(chroma_dir))
    if full:
        collection = client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
        )
        existing = collection.get(include=["metadatas"])
        stale_ids = [
            eid for eid, meta in zip(existing["ids"], existing["metadatas"] or [])
            if meta and (meta.get("doc_type") or "").startswith("chunk")
        ]
        if stale_ids:
            collection.delete(ids=stale_ids)
            print(f"已删除 {len(stale_ids)} 条旧 chunk")
    else:
        collection = client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
        )

    # 加载已有 hash 做增量去重
    existing_hashes: set[str] = set()
    if not full:
        existing = collection.get(include=["metadatas"])
        for meta in existing.get("metadatas", []) or []:
            if meta and "embed_hash" in meta:
                existing_hashes.add(meta["embed_hash"])

    embed_client = EmbeddingClient(provider)
    added = 0
    skipped = 0
    failed = 0

    for batch in chunked(chunks, BATCH_SIZE):
        ids: list[str] = []
        docs: list[str] = []
        metas: list[dict] = []
        inputs: list[str] = []

        for c in batch:
            if c["text_hash"] in existing_hashes:
                skipped += 1
                continue

            doc = f"{c['section']}\n{c['text']}"
            meta = {
                "doc_type": c["doc_type"],
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
    return {"total": len(chunks), "added": added, "skipped": skipped, "failed": failed}


def save_chunks_jsonl(chunks: list[dict], out_path: Path, full: bool) -> int:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if full and out_path.exists():
        out_path.unlink()
    with open(out_path, "a", encoding="utf-8") as f:
        for c in chunks:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    return len(chunks)


def load_chunks_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    result: list[dict] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                result.append(json.loads(line))
    return result


# ── main ──

def main() -> int:
    load_dotenv(PROJECT_ROOT / ".env")
    p = argparse.ArgumentParser(description="Markdown → chunk → Chroma 入库")
    p.add_argument("--input", type=Path, required=True, help="markdown 文件路径")
    p.add_argument("--chroma-dir", type=Path, default=DEFAULT_CHROMA_DIR)
    p.add_argument("--collection", default=DEFAULT_COLLECTION)
    p.add_argument("--chunks-out", type=Path, default=DEFAULT_CHUNKS_OUT)
    p.add_argument(
        "--provider",
        default=os.environ.get("EMBEDDING_PROVIDER", "dashscope"),
        choices=["bge", "dashscope"],
    )
    p.add_argument("--full", action="store_true", help="清空旧 chunk 后全量重建")
    p.add_argument("--dry-run", action="store_true", help="只切分打印，不入库")
    args = p.parse_args()

    if not args.input.exists():
        raise SystemExit(f"文件不存在: {args.input}")

    md_path = args.input.resolve()
    print(f"解析: {md_path}")
    chunks = make_chunks(md_path)
    print(f"切出 {len(chunks)} 个 chunk")
    sizes = [len(c["text"]) for c in chunks]
    if sizes:
        print(f"chunk 长度: min={min(sizes)} max={max(sizes)} avg={sum(sizes)//len(sizes)}")

    if args.dry_run:
        for c in chunks[:5]:
            print(f"\n--- {c['chunk_id']} ---")
            print(f"  section: {c['section']}")
            print(f"  indicators: {c['indicators']}")
            print(f"  text ({len(c['text'])} 字): {c['text'][:200]}...")
        if len(chunks) > 5:
            print(f"\n... 还有 {len(chunks) - 5} 个 chunk")
        print(f"\n[dry-run] 共 {len(chunks)} 个 chunk，未入库")
        return 0

    print(f"\n写入 {args.chunks_out}")
    save_chunks_jsonl(chunks, args.chunks_out, args.full)

    print(f"入库 Chroma ({args.provider})")
    summary = upsert_chunks(
        chunks, args.chroma_dir, args.collection, args.provider, args.full
    )
    print(
        f"完成：total={summary['total']} added={summary['added']} "
        f"skipped={summary['skipped']} failed={summary['failed']}"
    )
    return 1 if summary["failed"] > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
