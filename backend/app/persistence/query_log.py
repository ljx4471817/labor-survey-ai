"""查询日志 SQLite 封装。

每条 chat 请求一条记录（即使 mode=out_of_scope/out_of_kb 也记）。
用于 dashboard 用量统计与 region 5 级下钻。

schema：ts / phone / name / region 5 级 / query / mode / retrieval_score
不存 answer 完整内容；用户表态后的答案由 feedback.jsonl 落盘。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone, timedelta

from app.core.config import PROJECT_ROOT

DB_PATH = PROJECT_ROOT / "backend" / "data" / "query_log.db"
UTC8 = timezone(timedelta(hours=8))

_REGION_LEVELS = ("province", "city", "county", "township", "community")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS query_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT NOT NULL,
    phone           TEXT NOT NULL,
    name            TEXT NOT NULL,
    province        TEXT NOT NULL,
    city            TEXT NOT NULL,
    county          TEXT NOT NULL,
    township        TEXT,
    community       TEXT NOT NULL,
    query           TEXT NOT NULL,
    mode            TEXT NOT NULL,
    retrieval_score REAL,
    request_id      TEXT,
    hits            INTEGER,
    latency_ms      INTEGER,
    top_qa_id       TEXT
);
CREATE INDEX IF NOT EXISTS idx_query_log_region
    ON query_log(province, city, county, township, community);
CREATE INDEX IF NOT EXISTS idx_query_log_ts
    ON query_log(ts);
CREATE INDEX IF NOT EXISTS idx_query_log_phone
    ON query_log(phone);
"""

# 老库迁移：必须先 ALTER 字段再 CREATE 引用它的索引，否则索引创建会因字段不存在而失败。
_MIGRATIONS = (
    "ALTER TABLE query_log ADD COLUMN request_id TEXT",
    "ALTER TABLE query_log ADD COLUMN hits INTEGER",
    "ALTER TABLE query_log ADD COLUMN latency_ms INTEGER",
    "ALTER TABLE query_log ADD COLUMN top_qa_id TEXT",
    "CREATE INDEX IF NOT EXISTS idx_query_log_request_id ON query_log(request_id)",
)

_conn: sqlite3.Connection | None = None


def _get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.executescript(_SCHEMA)
        for sql in _MIGRATIONS:
            try:
                _conn.execute(sql)
            except sqlite3.OperationalError:
                # 字段已存在，跳过（幂等）
                pass
        _conn.commit()
    return _conn


def insert(entry: dict) -> None:
    """插入一条 query 日志。

    必填：phone/name/province/city + query + mode
    选填：county/township/community/retrieval_score/request_id/hits/latency_ms
    （管理人员可能没有 county/community）
    """
    required = (
        "phone", "name", "province", "city", "query", "mode",
    )
    for k in required:
        if not entry.get(k):
            raise ValueError(f"缺少必填字段：{k}")

    conn = _get_conn()
    conn.execute(
        """
        INSERT INTO query_log
            (ts, phone, name, province, city, county, township, community,
             query, mode, retrieval_score, request_id, hits, latency_ms,
             top_qa_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            entry.get("ts") or datetime.now(UTC8).isoformat(timespec="seconds"),
            entry["phone"],
            entry["name"],
            entry["province"],
            entry["city"],
            entry.get("county") or "",
            entry.get("township") or "",
            entry.get("community") or "",
            entry["query"],
            entry["mode"],
            entry.get("retrieval_score"),
            entry.get("request_id"),
            entry.get("hits"),
            entry.get("latency_ms"),
            entry.get("top_qa_id"),
        ),
    )
    conn.commit()


def stats_by_region(level: str, parent: dict | None = None) -> list[dict]:
    """按 region 层级聚合用量。每行: region_fields + count。

    level: province/city/county/township/community
    parent: 上一层 region 字段（level=province 时 parent 必须为 None）
    """
    if level not in _REGION_LEVELS:
        raise ValueError(f"level 必须是 {_REGION_LEVELS} 之一")
    target_idx = _REGION_LEVELS.index(level)
    select_cols = _REGION_LEVELS[: target_idx + 1]

    where_cols = _REGION_LEVELS[:target_idx]
    where_clause = ""
    params: tuple = ()
    if where_cols:
        clauses = " AND ".join(f"{c} = ?" for c in where_cols)
        where_clause = f"WHERE {clauses}"
        params = tuple(parent[c] for c in where_cols) if parent else ()

    sql = (
        f"SELECT {', '.join(select_cols)}, COUNT(*) AS count "
        f"FROM query_log {where_clause} "
        f"GROUP BY {', '.join(select_cols)} "
        f"ORDER BY count DESC, {', '.join(select_cols)}"
    )
    rows = _get_conn().execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def total_count() -> int:
    row = _get_conn().execute("SELECT COUNT(*) AS c FROM query_log").fetchone()
    return row["c"] if row else 0


def search_usage(filters: dict) -> list[dict]:
    """按任意筛选条件查询用量（GROUP BY phone）。

    filters 可选键: province, city, county, township, community, name, phone
    region 字段精确匹配，name/phone 用 LIKE %keyword%
    返回: [{phone, name, province, city, county, township, community, query_count, last_query_at}]
    """
    where_parts: list[str] = []
    params: list[str] = []

    for col in _REGION_LEVELS:
        val = filters.get(col, "").strip()
        if val:
            where_parts.append(f"{col} = ?")
            params.append(val)

    for col in ("name", "phone"):
        val = filters.get(col, "").strip()
        if val:
            where_parts.append(f"{col} LIKE ?")
            params.append(f"%{val}%")

    where_clause = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""

    sql = (
        f"SELECT phone, MAX(name) AS name, MAX(province) AS province, "
        f"MAX(city) AS city, MAX(county) AS county, MAX(township) AS township, "
        f"MAX(community) AS community, COUNT(*) AS query_count, MAX(ts) AS last_query_at "
        f"FROM query_log {where_clause} "
        f"GROUP BY phone ORDER BY query_count DESC"
    )
    rows = _get_conn().execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def top_qa_stats(
    *,
    days: int = 30,
    now: datetime | None = None,
    excluded_phones: frozenset[str] | set[str] = frozenset(),
) -> list[dict]:
    """按 top-1 QA 聚合近 N 天的成功 RAG 查询。"""
    reference_time = now or datetime.now(UTC8)
    if reference_time.tzinfo is None:
        reference_time = reference_time.replace(tzinfo=UTC8)
    cutoff = (reference_time - timedelta(days=days)).isoformat(timespec="seconds")
    phones = sorted(set(excluded_phones))
    phone_placeholders = ", ".join("?" for _ in phones)

    sql = f"""
        SELECT top_qa_id, COUNT(*) AS query_count,
               COUNT(DISTINCT phone) AS user_count, MAX(ts) AS last_asked_at
        FROM query_log
        WHERE ts >= ? AND mode = 'rag'
          AND top_qa_id IS NOT NULL AND top_qa_id != ''
    """
    params: list[str] = [cutoff]
    if phones:
        sql += f" AND phone NOT IN ({phone_placeholders})"
        params.extend(phones)
    sql += " GROUP BY top_qa_id"

    rows = _get_conn().execute(sql, params).fetchall()
    return [dict(row) for row in rows]
