"""使用侧 KB 闭环分析：从 query_log.db + feedback.jsonl 找 KB 改进候选。

与 detect_gaps.py 的区别：
- detect_gaps.py 是「离线 docx → markdown → Q&A 候选」与 faq.json 比对；
- 本脚本是「真实用户 query_log + 反馈」与 faq.json 比对，覆盖使用侧盲区。

输出 reports/gap-report-from-usage-<date>.json，三类清单：

1. high_freq_out_of_scope
   - 来源：query_log.mode='out_of_scope' 按 query 字面 group by
   - 含义：用户高频问但 KB 完全不覆盖（应新增 KB）
   - 行动：人工确认后走 add_faq_entries.py 入库

2. kb_hit_but_down
   - 来源：feedback.rating='down' AND mode='rag' 用 request_id join query_log，
           按 sources[0].qa_id group by
   - 含义：KB 命中但用户不采纳（应改写现有 KB）
   - 行动：人工 review 该 qa_id 条目

3. high_freq_low_match
   - 来源：query_log 全 mode 高频 query（freq>=MIN_FREQ）与 faq.json 算 embedding 相似度，
           最高相似度 < THRESHOLD_REVIEW 的
   - 含义：用户问得多但 KB 没匹配上（应查检索或补 KB）
   - 行动：人工审 query 是 KB 缺、检索差、还是用户表述问题

复用：
- scripts/build_kb.py: EmbeddingClient + dotenv
- scripts/detect_gaps.py: cosine_sim / embed_with_retry / 阈值常量
- backend/app/query_log.py 的 SQL 模板
"""
from __future__ import annotations

import argparse
import io
import json
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
from build_kb import EmbeddingClient  # noqa: E402
from detect_gaps import cosine_sim, embed_with_retry  # noqa: E402

QUERY_LOG_DB = PROJECT_ROOT / "backend" / "data" / "query_log.db"
FEEDBACK_PATH = PROJECT_ROOT / "backend" / "data" / "feedback.jsonl"
FAQ_PATH = PROJECT_ROOT / "knowledge-base" / "qa" / "faq.json"
REPORTS_DIR = PROJECT_ROOT / "reports"

DEFAULT_SINCE_DAYS = 7
DEFAULT_MIN_FREQ = 3
DEFAULT_THRESHOLD = 0.70
DEFAULT_TOP_N_FOR_MATCH = 50  # 高频 query 取前 N 跑相似度


def _stdout_utf8() -> None:
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(
            sys.stdout.buffer, encoding="utf-8", line_buffering=True
        )


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(QUERY_LOG_DB))
    conn.row_factory = sqlite3.Row
    return conn


def _since_clause(since_days: int) -> tuple[str, list]:
    """按 ts >= since_days 天前过滤。"""
    cutoff = (datetime.now() - timedelta(days=since_days)).isoformat(timespec="seconds")
    return "ts >= ?", [cutoff]


def high_freq_out_of_scope(since_days: int, min_freq: int) -> list[dict]:
    """query_log.mode='out_of_scope' 按 query 字面 group by。"""
    where_ts, params = _since_clause(since_days)
    sql = (
        f"SELECT query, COUNT(*) AS freq, "
        f"COUNT(DISTINCT phone) AS user_count, MAX(ts) AS last_seen "
        f"FROM query_log WHERE mode='out_of_scope' AND {where_ts} "
        f"GROUP BY query HAVING freq >= ? ORDER BY freq DESC"
    )
    rows = _connect().execute(sql, params + [min_freq]).fetchall()
    return [
        {
            "query": r["query"],
            "freq": r["freq"],
            "user_count": r["user_count"],
            "last_seen": r["last_seen"],
            "action": "考虑新增 KB 条目",
        }
        for r in rows
    ]


def kb_hit_but_down(since_days: int, min_freq: int) -> list[dict]:
    """feedback.rating='down' AND mode='rag'：按 sources[0].qa_id 聚合。

    旧 feedback 无 request_id 也能用（rating + mode + question 字面过滤）。
    """
    where_ts, params = _since_clause(since_days)
    # 先拿 query_log.mode='rag' 的 request_id 集合（保证 join 到）
    rows = _connect().execute(
        f"SELECT request_id FROM query_log WHERE mode='rag' AND {where_ts}",
        params,
    ).fetchall()
    rag_rids = {r["request_id"] for r in rows if r["request_id"]}

    # 扫 feedback
    by_qa: dict[str, dict] = defaultdict(lambda: {
        "down_count": 0, "sample_questions": [], "sample_comments": [],
    })
    if not FEEDBACK_PATH.exists():
        return []
    with FEEDBACK_PATH.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            if r.get("rating") != "down":
                continue
            if r.get("mode") != "rag":
                continue
            # request_id 关联：用 rag_rids 过滤（有 request_id 的要求在 rag_rids 里）
            rid = r.get("request_id")
            if rid and rid not in rag_rids:
                continue
            sources = r.get("sources") or []
            if not sources:
                continue
            qa_id = str(sources[0].get("qa_id") or "").strip()
            if not qa_id:
                continue
            bucket = by_qa[qa_id]
            bucket["down_count"] += 1
            if len(bucket["sample_questions"]) < 3:
                bucket["sample_questions"].append(r.get("question", "")[:80])
            if r.get("comment") and len(bucket["sample_comments"]) < 3:
                bucket["sample_comments"].append(r["comment"][:120])

    items = []
    for qa_id, b in by_qa.items():
        if b["down_count"] < min_freq:
            continue
        items.append({
            "qa_id": qa_id,
            "down_count": b["down_count"],
            "sample_questions": b["sample_questions"],
            "sample_comments": b["sample_comments"],
            "action": "review KB 该条目，可能需要改写",
        })
    items.sort(key=lambda x: x["down_count"], reverse=True)
    return items


def _load_faq_questions() -> tuple[list[str], list[str]]:
    """返回 (faq_questions, faq_ids)"""
    faq = json.loads(FAQ_PATH.read_text(encoding="utf-8"))
    return [qa["question"] for qa in faq], [qa["id"] for qa in faq]


def high_freq_low_match(
    since_days: int, min_freq: int, threshold: float, top_n: int, client: EmbeddingClient,
) -> list[dict]:
    """query_log 全 mode 高频 query（>=min_freq 取前 top_n）与 faq.json 算相似度。"""
    where_ts, params = _since_clause(since_days)
    sql = (
        f"SELECT query, COUNT(*) AS freq, "
        f"COUNT(DISTINCT phone) AS user_count, MAX(ts) AS last_seen "
        f"FROM query_log WHERE {where_ts} "
        f"GROUP BY query HAVING freq >= ? ORDER BY freq DESC LIMIT ?"
    )
    rows = _connect().execute(sql, params + [min_freq, top_n]).fetchall()
    if not rows:
        return []

    high_freq_queries = [r["query"] for r in rows]
    faq_questions, faq_ids = _load_faq_questions()
    print(f"  embedding {len(high_freq_queries)} 高频 query ...")
    q_vecs = embed_with_retry(client, high_freq_queries)
    print(f"  embedding {len(faq_questions)} FAQ ...")
    faq_vecs = embed_with_retry(client, faq_questions)

    sims = cosine_sim(q_vecs, faq_vecs)
    max_idx = sims.argmax(axis=1)
    max_sim = sims[np.arange(len(high_freq_queries)), max_idx]

    items = []
    for i, r in enumerate(rows):
        if max_sim[i] >= threshold:
            continue
        items.append({
            "query": r["query"],
            "freq": r["freq"],
            "user_count": r["user_count"],
            "last_seen": r["last_seen"],
            "max_sim": round(float(max_sim[i]), 4),
            "nearest_faq_id": faq_ids[max_idx[i]],
            "nearest_faq_question": faq_questions[max_idx[i]],
            "action": "查询与 KB 相似度低：检查 KB 是否缺该话题，或检索 query 表述偏差",
        })
    items.sort(key=lambda x: x["freq"], reverse=True)
    return items


def main() -> int:
    _stdout_utf8()
    ap = argparse.ArgumentParser(description="使用侧 KB 闭环分析")
    ap.add_argument("--since-days", type=int, default=DEFAULT_SINCE_DAYS)
    ap.add_argument("--min-freq", type=int, default=DEFAULT_MIN_FREQ)
    ap.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD,
                    help="high_freq_low_match 的相似度上限（低于此值视为 KB 缺）")
    ap.add_argument("--top-n", type=int, default=DEFAULT_TOP_N_FOR_MATCH)
    ap.add_argument("--provider", default=None, choices=["bge", "dashscope"])
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    print(f"=== 使用侧 KB 闭环分析 ===")
    print(f"  since={args.since_days}d min_freq={args.min_freq} threshold={args.threshold}")

    # 1. 高频 out_of_scope
    print("\n[1/3] 高频 out_of_scope（KB 完全不覆盖）...")
    oos = high_freq_out_of_scope(args.since_days, args.min_freq)
    print(f"  → {len(oos)} 条")

    # 2. 命中但 down
    print("\n[2/3] 命中但 down（KB 该改写）...")
    down = kb_hit_but_down(args.since_days, args.min_freq)
    print(f"  → {len(down)} 条")

    # 3. 高频但相似度低（要 embedding）
    print("\n[3/3] 高频但低匹配（KB 可能缺匹配）...")
    provider = args.provider or "dashscope"
    client = EmbeddingClient(provider)
    low_match = high_freq_low_match(
        args.since_days, args.min_freq, args.threshold, args.top_n, client,
    )
    print(f"  → {len(low_match)} 条")

    # 写报告
    today = datetime.now().strftime("%Y%m%d")
    out_path = args.out or (REPORTS_DIR / f"gap-report-from-usage-{today}.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "since_days": args.since_days,
        "min_freq": args.min_freq,
        "threshold": args.threshold,
        "summary": {
            "high_freq_out_of_scope": len(oos),
            "kb_hit_but_down": len(down),
            "high_freq_low_match": len(low_match),
        },
        "high_freq_out_of_scope": oos,
        "kb_hit_but_down": down,
        "high_freq_low_match": low_match,
    }
    out_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n✓ 报告 → {out_path}")
    print(f"  三类清单: oos={len(oos)} down={len(down)} low_match={len(low_match)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())