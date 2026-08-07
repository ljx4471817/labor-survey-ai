# -*- coding: utf-8 -*-
"""月度测验系统 SQLite 持久化。

独立于 whitelist.db / query_log.db 的新库 backend/data/quiz.db（PRD v3 C1 已确认）。
schema 见 PRD v3 5.1 DDL。模式参照 persistence/whitelist_db.py：
- _SCHEMA + 幂等迁移 + 懒连接 + WAL
- 时区一律 UTC+8 ISO8601 seconds

quiz（套）是下发与统计的最小单位；month 仅筛选。过期状态读取时推导，
清理任务负责归档与 12 个月数据保留。
"""
from __future__ import annotations

import os
import sqlite3
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path

from app.core.config import PROJECT_ROOT
from app.core.constants import QUIZ_RETENTION_DAYS

# QUIZ_DB_PATH 可用环境变量覆盖（测试/演示隔离库用）；默认 backend/data/quiz.db
DB_PATH = Path(os.environ.get("QUIZ_DB_PATH", str(PROJECT_ROOT / "backend" / "data" / "quiz.db")))
UTC8 = timezone(timedelta(hours=8))

_SCHEMA = """
CREATE TABLE IF NOT EXISTS quizzes (
    id          TEXT PRIMARY KEY,
    month       TEXT NOT NULL,
    title       TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'draft',
    valid_from  TEXT,
    valid_until TEXT,
    created_by  TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_quizzes_month ON quizzes(month);

CREATE TABLE IF NOT EXISTS imports (
    id              TEXT PRIMARY KEY,
    month           TEXT NOT NULL,
    filename        TEXT NOT NULL,
    file_size       INTEGER NOT NULL,
    status          TEXT NOT NULL DEFAULT 'imported',
    raw_text_length INTEGER,
    extracted_by    TEXT NOT NULL,
    extracted_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS keypoints (
    id              TEXT PRIMARY KEY,
    quiz_id         TEXT NOT NULL,
    section         TEXT NOT NULL,
    content         TEXT NOT NULL,
    common_error    TEXT DEFAULT '',
    source_quote    TEXT DEFAULT '',
    suggest_quiz    INTEGER NOT NULL DEFAULT 1,
    kb_faq_id       TEXT,
    kb_question     TEXT DEFAULT '',
    kb_score        REAL,
    kb_match_status TEXT NOT NULL DEFAULT 'unmatched',
    status          TEXT NOT NULL DEFAULT 'draft',
    reviewed_by     TEXT,
    reviewed_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_keypoints_quiz ON keypoints(quiz_id);

CREATE TABLE IF NOT EXISTS questions (
    id           TEXT PRIMARY KEY,
    quiz_id      TEXT NOT NULL,
    seq          INTEGER NOT NULL,
    question     TEXT NOT NULL,
    options      TEXT NOT NULL,
    answer       TEXT NOT NULL,
    explanation  TEXT NOT NULL,
    source_quote TEXT DEFAULT '',
    kb_faq_id    TEXT,
    kb_question  TEXT DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'draft',
    created_by   TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_questions_quiz ON questions(quiz_id);

CREATE TABLE IF NOT EXISTS targets (
    quiz_id  TEXT NOT NULL,
    phone    TEXT NOT NULL,
    added_at TEXT NOT NULL,
    PRIMARY KEY (quiz_id, phone)
);

CREATE TABLE IF NOT EXISTS answers (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    quiz_id  TEXT NOT NULL,
    phone    TEXT NOT NULL,
    q_id     TEXT NOT NULL,
    selected TEXT NOT NULL,
    correct  INTEGER NOT NULL,
    ts       TEXT NOT NULL,
    UNIQUE(quiz_id, phone, q_id)
);
CREATE INDEX IF NOT EXISTS idx_answers_user ON answers(quiz_id, phone);
CREATE INDEX IF NOT EXISTS idx_answers_phone ON answers(phone);
"""

_MIGRATIONS: tuple[str, ...] = ()


def _now() -> str:
    return datetime.now(UTC8).isoformat(timespec="seconds")


_local = threading.local()
_schema_lock = threading.Lock()
_schema_ready_for: Path | None = None
# 写串行化锁：SQLite 单写者，进程级互斥避免 busy 处理器在大量并发写下的
# 惊群/饥饿开销（读操作不受影响，WAL 允许并发读）。
_WRITE_LOCK = threading.Lock()


def _get_conn() -> sqlite3.Connection:
    """当前线程懒创建并复用连接（SQLite WAL + busy_timeout）。"""
    global _schema_ready_for
    conn = getattr(_local, "conn", None)
    if conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(DB_PATH), check_same_thread=False, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        if _schema_ready_for != DB_PATH:
            with _schema_lock:
                if _schema_ready_for != DB_PATH:
                    conn.executescript(_SCHEMA)
                    for sql in _MIGRATIONS:
                        try:
                            conn.execute(sql)
                        except sqlite3.OperationalError:
                            pass
                    conn.commit()
                    _schema_ready_for = DB_PATH
        _local.conn = conn
    return conn


def reset_conn() -> None:
    """关闭并清空当前线程的连接（测试隔离 / 换库用）。"""
    conn = getattr(_local, "conn", None)
    if conn is not None:
        try:
            conn.close()
        except sqlite3.Error:
            pass
        _local.conn = None


def _row(d: dict | None) -> dict | None:
    return dict(d) if d is not None else None


# --- id 生成 -----------------------------------------------------------------

def _month_digits(month: str) -> str:
    return month.replace("-", "")


def _next_seq(table: str, column: str, prefix: str) -> int:
    """按前缀统计序号，用于 Q/IMP 的月份内序号。"""
    conn = _get_conn()
    row = conn.execute(
        f"SELECT COUNT(*) AS c FROM {table} WHERE id LIKE ?",
        (prefix + "%",),
    ).fetchone()
    return (row["c"] if row else 0) + 1


# --- quizzes -----------------------------------------------------------------

def create_quiz(month: str, title: str, created_by: str) -> str:
    """新建 quiz（draft）。id = Q + YYYYMM + 月份内序号。"""
    now = _now()
    with _WRITE_LOCK:
        seq = _next_seq("quizzes", "id", "Q" + _month_digits(month))
        quiz_id = f"Q{_month_digits(month)}{seq:02d}"
        conn = _get_conn()
        conn.execute(
            "INSERT INTO quizzes (id, month, title, status, created_by, created_at, updated_at) "
            "VALUES (?, ?, ?, 'draft', ?, ?, ?)",
            (quiz_id, month, title, created_by, now, now),
        )
        conn.commit()
    return quiz_id


def get_quiz(quiz_id: str) -> dict | None:
    row = _get_conn().execute(
        "SELECT * FROM quizzes WHERE id = ?", (quiz_id,)
    ).fetchone()
    return _row(row)

def list_quizzes(month: str | None = None, status: str | None = None) -> list[dict]:
    sql = "SELECT * FROM quizzes"
    clauses, params = [], []
    if month:
        clauses.append("month = ?")
        params.append(month)
    if status:
        clauses.append("status = ?")
        params.append(status)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY month DESC, id DESC"
    return [dict(r) for r in _get_conn().execute(sql, params)]


def update_quiz(quiz_id: str, **fields) -> None:
    """按字段白名单更新 quiz。"""
    allowed = {"title", "status", "valid_from", "valid_until"}
    sets, params = [], []
    for k, v in fields.items():
        if k not in allowed:
            raise ValueError(f"quiz 不支持字段：{k}")
        sets.append(f"{k} = ?")
        params.append(v)
    if not sets:
        return
    params.extend([_now(), quiz_id])
    with _WRITE_LOCK:
        conn = _get_conn()
        conn.execute(
            f"UPDATE quizzes SET {', '.join(sets)}, updated_at = ? WHERE id = ?",
            params,
        )
        conn.commit()


def has_published_quiz(month: str) -> bool:
    row = _get_conn().execute(
        "SELECT 1 FROM quizzes WHERE month = ? AND status IN ('published', 'expired') LIMIT 1",
        (month,),
    ).fetchone()
    return row is not None


def sync_expired(now_iso: str | None = None) -> int:
    """把超过有效期的 published quiz 批量置为 expired，返回更新条数。"""
    now_iso = now_iso or _now()
    with _WRITE_LOCK:
        conn = _get_conn()
        cur = conn.execute(
            "UPDATE quizzes SET status = 'expired', updated_at = ? "
            "WHERE status = 'published' AND valid_until IS NOT NULL AND valid_until < ?",
            (now_iso, now_iso),
        )
        conn.commit()
        return cur.rowcount


# --- imports -----------------------------------------------------------------

def create_import(month: str, filename: str, file_size: int, extracted_by: str) -> str:
    now = _now()
    with _WRITE_LOCK:
        seq = _next_seq("imports", "id", "IMP" + _month_digits(month))
        import_id = f"IMP{_month_digits(month)}{seq:02d}"
        conn = _get_conn()
        conn.execute(
            "INSERT INTO imports (id, month, filename, file_size, status, extracted_by, extracted_at) "
            "VALUES (?, ?, ?, ?, 'imported', ?, ?)",
            (import_id, month, filename, file_size, extracted_by, now),
        )
        conn.commit()
    return import_id


def get_import(import_id: str) -> dict | None:
    return _row(
        _get_conn().execute("SELECT * FROM imports WHERE id = ?", (import_id,)).fetchone()
    )


def update_import(import_id: str, **fields) -> None:
    allowed = {"status", "raw_text_length"}
    sets, params = [], []
    for k, v in fields.items():
        if k not in allowed:
            raise ValueError(f"import 不支持字段：{k}")
        sets.append(f"{k} = ?")
        params.append(v)
    if not sets:
        return
    params.append(import_id)
    with _WRITE_LOCK:
        conn = _get_conn()
        conn.execute(
            f"UPDATE imports SET {', '.join(sets)} WHERE id = ?", params,
        )
        conn.commit()


def latest_import_for_month(month: str) -> dict | None:
    """返回该月最近的导入记录（用于按 quiz 触发提取）。"""
    row = _get_conn().execute(
        "SELECT * FROM imports WHERE month = ? ORDER BY extracted_at DESC, id DESC LIMIT 1",
        (month,),
    ).fetchone()
    return _row(row)

# --- keypoints ---------------------------------------------------------------

def replace_keypoints(quiz_id: str, items: list[dict]) -> int:
    """覆盖重建：先删该 quiz 旧要点，再批量插入（同月 draft 重复导入用）。"""
    with _WRITE_LOCK:
        conn = _get_conn()
        conn.execute("DELETE FROM keypoints WHERE quiz_id = ?", (quiz_id,))
        now = _now()
        for i, it in enumerate(items, start=1):
            conn.execute(
                "INSERT INTO keypoints "
                "(id, quiz_id, section, content, common_error, source_quote, suggest_quiz, "
                " kb_faq_id, kb_question, kb_score, kb_match_status, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft')",
                (
                    f"{quiz_id}KP{i:02d}",
                    quiz_id,
                    it.get("section") or "其它",
                    it.get("content") or "",
                    it.get("common_error") or "",
                    it.get("source_quote") or "",
                    1 if it.get("suggest_quiz", True) else 0,
                    it.get("kb_faq_id"),
                    it.get("kb_question") or "",
                    it.get("kb_score"),
                    it.get("kb_match_status") or "unmatched",
                ),
            )
        conn.commit()
        return len(items)


def list_keypoints(quiz_id: str) -> list[dict]:
    return [
        dict(r)
        for r in _get_conn()
        .execute(
            "SELECT * FROM keypoints WHERE quiz_id = ? ORDER BY section, id", (quiz_id,)
        )
        .fetchall()
    ]


def get_keypoint(keypoint_id: str) -> dict | None:
    return _row(
        _get_conn().execute("SELECT * FROM keypoints WHERE id = ?", (keypoint_id,)).fetchone()
    )


def update_keypoint(keypoint_id: str, reviewed_by: str | None = None, **fields) -> None:
    allowed = {
        "section", "content", "common_error", "source_quote", "suggest_quiz",
        "kb_faq_id", "kb_question", "kb_score", "kb_match_status", "status",
    }
    sets, params = [], []
    for k, v in fields.items():
        if k not in allowed:
            raise ValueError(f"keypoint 不支持字段：{k}")
        sets.append(f"{k} = ?")
        params.append(v)
    if reviewed_by:
        sets.append("reviewed_by = ?")
        params.append(reviewed_by)
        sets.append("reviewed_at = ?")
        params.append(_now())
    if not sets:
        return
    params.append(keypoint_id)
    with _WRITE_LOCK:
        conn = _get_conn()
        conn.execute(
            f"UPDATE keypoints SET {', '.join(sets)} WHERE id = ?", params,
        )
        conn.commit()


# --- questions ---------------------------------------------------------------

def replace_questions(quiz_id: str, items: list[dict]) -> int:
    """覆盖重建题目（draft 阶段重复生成用）。"""
    with _WRITE_LOCK:
        conn = _get_conn()
        conn.execute("DELETE FROM questions WHERE quiz_id = ?", (quiz_id,))
        now = _now()
        for i, it in enumerate(items, start=1):
            conn.execute(
                "INSERT INTO questions "
                "(id, quiz_id, seq, question, options, answer, explanation, source_quote, "
                " kb_faq_id, kb_question, status, created_by, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?, ?)",
                (
                    f"{quiz_id}Q{i:02d}",
                    quiz_id,
                    i,
                    it.get("question") or "",
                    it.get("options") or "{}",
                    it.get("answer") or "",
                    it.get("explanation") or "",
                    it.get("source_quote") or "",
                    it.get("kb_faq_id"),
                    it.get("kb_question") or "",
                    it.get("created_by") or "",
                    now,
                    now,
                ),
            )
        conn.commit()
        return len(items)


def list_questions(quiz_id: str) -> list[dict]:
    return [
        dict(r)
        for r in _get_conn()
        .execute(
            "SELECT * FROM questions WHERE quiz_id = ? ORDER BY seq", (quiz_id,)
        )
        .fetchall()
    ]


def get_question(question_id: str) -> dict | None:
    return _row(
        _get_conn().execute("SELECT * FROM questions WHERE id = ?", (question_id,)).fetchone()
    )


def count_questions(quiz_id: str) -> int:
    row = _get_conn().execute(
        "SELECT COUNT(*) AS c FROM questions WHERE quiz_id = ?", (quiz_id,)
    ).fetchone()
    return row["c"] if row else 0


def update_question(question_id: str, **fields) -> None:
    allowed = {
        "question", "options", "answer", "explanation", "source_quote",
        "kb_faq_id", "kb_question", "status",
    }
    sets, params = [], []
    for k, v in fields.items():
        if k not in allowed:
            raise ValueError(f"question 不支持字段：{k}")
        sets.append(f"{k} = ?")
        params.append(v)
    if not sets:
        return
    params.extend([_now(), question_id])
    with _WRITE_LOCK:
        conn = _get_conn()
        conn.execute(
            f"UPDATE questions SET {', '.join(sets)}, updated_at = ? WHERE id = ?",
            params,
        )
        conn.commit()

# --- targets -----------------------------------------------------------------

def set_targets(quiz_id: str, phones: list[str]) -> int:
    """发布时全量覆盖目标名单。"""
    with _WRITE_LOCK:
        conn = _get_conn()
        conn.execute("DELETE FROM targets WHERE quiz_id = ?", (quiz_id,))
        added_at = _now()
        for phone in phones:
            conn.execute(
                "INSERT OR IGNORE INTO targets (quiz_id, phone, added_at) VALUES (?, ?, ?)",
                (quiz_id, phone, added_at),
            )
        conn.commit()
        return len(phones)


def add_targets(quiz_id: str, phones: list[str]) -> int:
    """下发后追加（union）。"""
    with _WRITE_LOCK:
        added_at = _now()
        added = 0
        conn = _get_conn()
        for phone in phones:
            cur = conn.execute(
                "INSERT OR IGNORE INTO targets (quiz_id, phone, added_at) VALUES (?, ?, ?)",
                (quiz_id, phone, added_at),
            )
            added += cur.rowcount
        conn.commit()
        return added


def remove_targets(quiz_id: str, phones: list[str]) -> list[str]:
    """移除目标（仅允许未作答用户）；返回实际移除的手机号。"""
    with _WRITE_LOCK:
        conn = _get_conn()
        removed: list[str] = []
        for phone in phones:
            answered = conn.execute(
                "SELECT 1 FROM answers WHERE quiz_id = ? AND phone = ? LIMIT 1",
                (quiz_id, phone),
            ).fetchone()
            if answered:
                continue
            cur = conn.execute(
                "DELETE FROM targets WHERE quiz_id = ? AND phone = ?", (quiz_id, phone)
            )
            if cur.rowcount:
                removed.append(phone)
        conn.commit()
        return removed


def list_target_phones(quiz_id: str) -> list[str]:
    rows = _get_conn().execute(
        "SELECT phone FROM targets WHERE quiz_id = ?", (quiz_id,)
    ).fetchall()
    return [r["phone"] for r in rows]


def is_target(quiz_id: str, phone: str) -> bool:
    row = _get_conn().execute(
        "SELECT 1 FROM targets WHERE quiz_id = ? AND phone = ? LIMIT 1",
        (quiz_id, phone),
    ).fetchone()
    return row is not None


def count_targets(quiz_id: str) -> int:
    row = _get_conn().execute(
        "SELECT COUNT(*) AS c FROM targets WHERE quiz_id = ?", (quiz_id,)
    ).fetchone()
    return row["c"] if row else 0


# --- answers -----------------------------------------------------------------

def submit_answer(quiz_id: str, phone: str, q_id: str, selected: str, correct: bool) -> str:
    """写单题答案。重复提交返回 'duplicate' 且不改数据（单题锁定）。"""
    with _WRITE_LOCK:
        conn = _get_conn()
        cur = conn.execute(
            "INSERT OR IGNORE INTO answers (quiz_id, phone, q_id, selected, correct, ts) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (quiz_id, phone, q_id, selected, 1 if correct else 0, _now()),
        )
        conn.commit()
    return "inserted" if cur.rowcount else "duplicate"


def is_answered(quiz_id: str, phone: str, q_id: str) -> bool:
    row = _get_conn().execute(
        "SELECT 1 FROM answers WHERE quiz_id = ? AND phone = ? AND q_id = ? LIMIT 1",
        (quiz_id, phone, q_id),
    ).fetchone()
    return row is not None


def get_answers(quiz_id: str, phone: str) -> list[dict]:
    return [
        dict(r)
        for r in _get_conn()
        .execute(
            "SELECT * FROM answers WHERE quiz_id = ? AND phone = ? ORDER BY q_id",
            (quiz_id, phone),
        )
        .fetchall()
    ]


def count_answers(quiz_id: str, phone: str) -> int:
    row = _get_conn().execute(
        "SELECT COUNT(*) AS c FROM answers WHERE quiz_id = ? AND phone = ?",
        (quiz_id, phone),
    ).fetchone()
    return row["c"] if row else 0


def count_correct(quiz_id: str, phone: str) -> int:
    row = _get_conn().execute(
        "SELECT COUNT(*) AS c FROM answers WHERE quiz_id = ? AND phone = ? AND correct = 1",
        (quiz_id, phone),
    ).fetchone()
    return row["c"] if row else 0


def latest_answer_ts(quiz_id: str, phone: str) -> str | None:
    row = _get_conn().execute(
        "SELECT MAX(ts) AS ts FROM answers WHERE quiz_id = ? AND phone = ?",
        (quiz_id, phone),
    ).fetchone()
    return row["ts"] if row and row["ts"] else None


def answered_phones(quiz_id: str) -> set[str]:
    rows = _get_conn().execute(
        "SELECT DISTINCT phone FROM answers WHERE quiz_id = ?", (quiz_id,)
    ).fetchall()
    return {r["phone"] for r in rows}


def list_active_for_user(phone: str, now_iso: str | None = None) -> list[dict]:
    """返回该用户可见的测验：是目标 且 status=published 且未过期。"""
    now_iso = now_iso or _now()
    rows = _get_conn().execute(
        "SELECT q.* FROM quizzes q JOIN targets t ON q.id = t.quiz_id "
        "WHERE t.phone = ? AND q.status = 'published' "
        "AND (q.valid_until IS NULL OR q.valid_until >= ?) "
        "ORDER BY q.month DESC, q.id DESC",
        (phone, now_iso),
    ).fetchall()
    return [dict(r) for r in rows]


# --- 清理 / 保留 -------------------------------------------------------------

def cleanup_expired(now_iso: str | None = None) -> dict:
    """12 个月数据保留：删除超期 quiz 的答案/目标/题目/要点/导入记录，quiz 置 archived。"""
    now_iso = now_iso or _now()
    cutoff = (datetime.fromisoformat(now_iso) - timedelta(
        days=QUIZ_RETENTION_DAYS
    )).isoformat(timespec="seconds")
    with _WRITE_LOCK:
        conn = _get_conn()
        rows = conn.execute(
            "SELECT id, month FROM quizzes "
            "WHERE status != 'archived' AND valid_until IS NOT NULL AND valid_until < ?",
            (cutoff,),
        ).fetchall()
        archived = 0
        for r in rows:
            qid, month = r["id"], r["month"]
            for tbl in ("answers", "targets", "questions", "keypoints"):
                conn.execute(f"DELETE FROM {tbl} WHERE quiz_id = ?", (qid,))
            conn.execute("DELETE FROM imports WHERE month = ?", (month,))
            conn.execute(
                "UPDATE quizzes SET status = 'archived', updated_at = ? WHERE id = ?",
                (now_iso, qid),
            )
            archived += 1
        conn.commit()
        return {"archived": archived}
