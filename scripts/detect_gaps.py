"""对比 extracted Q&A 与 faq.json 现有条目的相似度，输出缺口报告。

输入：reports/extracted-qa-*.json（来自 extract_qa_pairs.py）
对比：knowledge-base/qa/faq.json 全量条目
输出：reports/gap-report-*.json

阈值（可调）：
- ≥ --threshold-skip (0.85)：自动跳过
- ≥ --threshold-review (0.70)：列入 review_needed
- < threshold-review：列入 add_candidates

环境变量：EMBEDDING_PROVIDER / DASHSCOPE_API_KEY / BGE_API_KEY（与 build_kb.py 共用）
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import time
from pathlib import Path

import numpy as np
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
from build_kb import EmbeddingClient  # noqa: E402

FAQ_PATH = PROJECT_ROOT / "knowledge-base" / "qa" / "faq.json"
REPORTS_DIR = PROJECT_ROOT / "reports"
DEFAULT_BATCH = 10


def _stdout_utf8() -> None:
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(
            sys.stdout.buffer, encoding="utf-8", line_buffering=True
        )


def cosine_sim(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """a: [N, D], b: [M, D] → [N, M] 余弦相似度。"""
    a_norm = a / np.linalg.norm(a, axis=1, keepdims=True).clip(min=1e-9)
    b_norm = b / np.linalg.norm(b, axis=1, keepdims=True).clip(min=1e-9)
    return a_norm @ b_norm.T


def embed_with_retry(client: EmbeddingClient, texts: list[str]) -> np.ndarray:
    """BATCH_SIZE=10 分批调用 embedding，失败重试。"""
    vecs: list[list[float]] = []
    for i in range(0, len(texts), DEFAULT_BATCH):
        batch = texts[i : i + DEFAULT_BATCH]
        for attempt in range(3):
            try:
                vecs.extend(client.embed_batch(batch))
                break
            except Exception as e:
                if attempt == 2:
                    raise
                print(f"  embedding 批次 {i}-{i + len(batch)} 失败，重试：{e}")
                time.sleep(2 ** attempt)
        print(f"  embedded {min(i + DEFAULT_BATCH, len(texts))}/{len(texts)}", end="\r")
    print()
    return np.array(vecs, dtype=np.float32)


def load_faq(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("faq.json 根节点必须是数组")
    return data


def main() -> int:
    _stdout_utf8()
    p = argparse.ArgumentParser(description="Q&A 候选与 faq.json 相似度对比")
    p.add_argument("--candidates", type=Path, required=True, help="extract_qa_pairs.py 的输出 JSON")
    p.add_argument("--faq", type=Path, default=FAQ_PATH)
    p.add_argument("--threshold-skip", type=float, default=0.85)
    p.add_argument("--threshold-review", type=float, default=0.70)
    p.add_argument(
        "--provider",
        default=None,
        choices=["bge", "dashscope"],
        help="Embedding provider（默认读 EMBEDDING_PROVIDER 环境变量）",
    )
    p.add_argument("--out", type=Path, default=None)
    args = p.parse_args()

    if not args.candidates.exists():
        print(f"找不到 candidates: {args.candidates}")
        return 1
    if not args.faq.exists():
        print(f"找不到 faq: {args.faq}")
        return 1
    if args.threshold_review >= args.threshold_skip:
        print("threshold-review 必须 < threshold-skip")
        return 1

    cand_data = json.loads(args.candidates.read_text(encoding="utf-8"))
    candidates = cand_data.get("items", [])
    if not candidates:
        print("candidates 为空")
        return 1

    # regex 模式产出的条目没有 "question" 字段（结构是 q_no + question）
    # 兼容两种格式：优先取 "question"，否则取 "q_text"
    def get_q(item: dict) -> str:
        return (item.get("question") or item.get("q_text") or "").strip()

    cand_questions = [get_q(c) for c in candidates]
    if not any(cand_questions):
        print("candidates 里所有条目都没有 question 字段")
        return 1

    faq = load_faq(args.faq)
    faq_questions = [qa["question"] for qa in faq]
    faq_ids = [qa["id"] for qa in faq]
    print(f"faq 现有 {len(faq_questions)} 条，candidates {len(candidates)} 条")

    provider = args.provider or "dashscope"
    client = EmbeddingClient(provider)
    print(f"embedding provider: {provider} ({client.model})")

    print("embedding faq questions ...")
    faq_vecs = embed_with_retry(client, faq_questions)
    print("embedding candidate questions ...")
    cand_vecs = embed_with_retry(client, cand_questions)

    sims = cosine_sim(cand_vecs, faq_vecs)
    max_idx = sims.argmax(axis=1)
    max_sim = sims[np.arange(len(candidates)), max_idx]

    skipped, review_needed, add_candidates = [], [], []
    for i, cand in enumerate(candidates):
        matched_id = faq_ids[max_idx[i]]
        matched_q = faq_questions[max_idx[i]]
        record = {
            "candidate": cand,
            "max_sim": round(float(max_sim[i]), 4),
            "matched_id": matched_id,
            "matched_question": matched_q,
        }
        if max_sim[i] >= args.threshold_skip:
            skipped.append(record)
        elif max_sim[i] >= args.threshold_review:
            review_needed.append(record)
        else:
            record["matched_id"] = None
            record["matched_question"] = None
            add_candidates.append(record)

    out_path = args.out or (
        REPORTS_DIR / f"gap-report-{args.candidates.stem.replace('extracted-qa-', '')}.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "source": cand_data.get("source"),
        "candidates_file": str(args.candidates),
        "faq_count": len(faq_questions),
        "threshold_skip": args.threshold_skip,
        "threshold_review": args.threshold_review,
        "summary": {
            "candidates_total": len(candidates),
            "skipped": len(skipped),
            "review_needed": len(review_needed),
            "add_candidates": len(add_candidates),
        },
        "skipped": skipped,
        "review_needed": review_needed,
        "add_candidates": add_candidates,
    }
    out_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(
        f"\n跳过 {len(skipped)} / 待审 {len(review_needed)} / 可加 {len(add_candidates)}"
    )
    print(f"✓ 报告 → {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
