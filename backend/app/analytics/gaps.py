"""使用端 KB 缺口分析（不依赖 embedding 的纯函数版）。

原代码散落在 `scripts/detect_gaps_from_usage.py`，被 admin.py 通过
`sys.path.insert` 拉进 backend —— 跨层依赖反向了。

本模块只放 backend 需要的那两个纯函数：
- `high_freq_out_of_scope` — 高频 query 但 KB 完全不覆盖
- `kb_hit_but_down` — KB 命中但用户不采纳（应改写 KB）

完整的 `high_freq_low_match`（依赖 EmbeddingClient）保留在 scripts/，
作为运维 CLI 入口。
"""
from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta

from app.core.config import PROJECT_ROOT

QUERY_LOG_DB = PROJECT_ROOT / "backend" / "data" / "query_log.db"
FEEDBACK_PATH = PROJECT_ROOT / "backend" / "data" / "feedback.jsonl"


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
    """feedback.rating='down' AND mode='rag'：按 sources[0].qa_id 聚合。"""
    where_ts, params = _since_clause(since_days)
    rows = _connect().execute(
        f"SELECT request_id FROM query_log WHERE mode='rag' AND {where_ts}",
        params,
    ).fetchall()
    rag_rids = {r["request_id"] for r in rows if r["request_id"]}

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
