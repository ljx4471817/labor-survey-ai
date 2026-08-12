# -*- coding: utf-8 -*-
"""测验系统 SQLite 持久化（多场景：月度通知 / 培训 / 自定义）。

独立于 whitelist.db / query_log.db 的新库 backend/data/quiz.db。
schema 见 PRD v5 5.1 DDL。模式参照 persistence/whitelist_db.py：
- _SCHEMA + 幂等迁移 + 懒连接 + WAL
- 时区一律 UTC+8 ISO8601 seconds

quiz（套）是下发与统计的最小单位；month 是可选标签（统一字段，不参与
唯一/排序），scene 是场景名称（scenes 字典，可自定义新增）。过期状态
读取时推导，清理任务负责归档与 12 个月数据保留。
"""
from __future__ import annotations

import os
import sqlite3
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path

from app.core.config import PROJECT_ROOT
from app.core.constants import QUIZ_DEFAULT_SCENES, QUIZ_RETENTION_DAYS

# QUIZ_DB_PATH 可用环境变量覆盖（测试/演示隔离库用）；默认 backend/data/quiz.db
DB_PATH = Path(os.environ.get("QUIZ_DB_PATH", str(PROJECT_ROOT / "backend" / "data" / "quiz.db")))
UTC8 = timezone(timedelta(hours=8))

_SCHEMA = """
CREATE TABLE IF NOT EXISTS quizzes (
    id          TEXT PRIMARY KEY,
    month       TEXT,
    scene       TEXT NOT NULL DEFAULT '',
    title       TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'draft',
    valid_from  TEXT,
    valid_until TEXT,
    created_by  TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_quizzes_month ON quizzes(month);

CREATE TABLE IF NOT EXISTS scenes (
    name       TEXT PRIMARY KEY,
    sort_order INTEGER NOT NULL DEFAULT 0,
    active     INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS imports (
    id              TEXT PRIMARY KEY,
    quiz_id         TEXT NOT NULL,
    month           TEXT,
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
    selected     INTEGER NOT NULL DEFAULT 0,
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

_MIGRATIONS: tuple[str, ...] = (
    "ALTER TABLE questions ADD COLUMN selected INTEGER NOT NULL DEFAULT 0",
)


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

def _next_seq(table: str, column: str, prefix: str) -> int:
    """按前缀取最大序号 +1（删除中间行后不冲突）。"""
    conn = _get_conn()
    row = conn.execute(
        f"SELECT MAX(CAST(SUBSTR({column}, {len(prefix) + 1}) AS INTEGER)) AS m "
        f"FROM {table} WHERE {column} LIKE ?",
        (prefix + "%",),
    ).fetchone()
    return (row["m"] if row and row["m"] is not None else 0) + 1

# --- quizzes -----------------------------------------------------------------

def create_quiz(title: str, scene: str = "", created_by: str = "", month: str | None = None) -> str:
    """新建 quiz（draft）。id = Q + 全局序号；month 为可选标签（多场景统一字段）。"""
    now = _now()
    with _WRITE_LOCK:
        seq = _next_seq("quizzes", "id", "Q")
        quiz_id = f"Q{seq:04d}"
        conn = _get_conn()
        conn.execute(
            "INSERT INTO quizzes (id, month, scene, title, status, created_by, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'draft', ?, ?, ?)",
            (quiz_id, month, scene, title, created_by, now, now),
        )
        conn.commit()
    return quiz_id


def get_quiz(quiz_id: str) -> dict | None:
    row = _get_conn().execute(
        "SELECT * FROM quizzes WHERE id = ?", (quiz_id,)
    ).fetchone()
    return _row(row)

def list_quizzes(month: str | None = None, scene: str | None = None, status: str | None = None) -> list[dict]:
    sql = "SELECT * FROM quizzes"
    clauses, params = [], []
    if month:
        clauses.append("month = ?")
        params.append(month)
    if scene:
        clauses.append("scene = ?")
        params.append(scene)
    if status:
        clauses.append("status = ?")
        params.append(status)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY created_at DESC, id DESC"
    return [dict(r) for r in _get_conn().execute(sql, params)]


def update_quiz(quiz_id: str, **fields) -> None:
    """按字段白名单更新 quiz。"""
    allowed = {"title", "status", "valid_from", "valid_until", "month", "scene"}
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

def create_import(quiz_id: str, month: str | None, filename: str, file_size: int, extracted_by: str) -> str:
    """新建导入记录，关联 quiz（替代旧的按 month 关联）。id = IMP + 全局序号。"""
    now = _now()
    with _WRITE_LOCK:
        seq = _next_seq("imports", "id", "IMP")
        import_id = f"IMP{seq:04d}"
        conn = _get_conn()
        conn.execute(
            "INSERT INTO imports (id, quiz_id, month, filename, file_size, status, extracted_by, extracted_at) "
            "VALUES (?, ?, ?, ?, ?, 'imported', ?, ?)",
            (import_id, quiz_id, month, filename, file_size, extracted_by, now),
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


def latest_import_for_quiz(quiz_id: str) -> dict | None:
    """返回该测验最近的导入记录（用于触发提取）。"""
    row = _get_conn().execute(
        "SELECT * FROM imports WHERE quiz_id = ? ORDER BY extracted_at DESC, id DESC LIMIT 1",
        (quiz_id,),
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


def list_questions(quiz_id: str, selected_only: bool = False) -> list[dict]:
    """题目列表；selected_only=True 时只返回已勾选（试卷）题目。"""
    sql = "SELECT * FROM questions WHERE quiz_id = ?"
    if selected_only:
        sql += " AND selected = 1"
    sql += " ORDER BY seq"
    return [dict(r) for r in _get_conn().execute(sql, (quiz_id,))]


def get_question(question_id: str) -> dict | None:
    return _row(
        _get_conn().execute("SELECT * FROM questions WHERE id = ?", (question_id,)).fetchone()
    )


def count_questions(quiz_id: str, selected_only: bool = False) -> int:
    sql = "SELECT COUNT(*) AS c FROM questions WHERE quiz_id = ?"
    if selected_only:
        sql += " AND selected = 1"
    row = _get_conn().execute(sql, (quiz_id,)).fetchone()
    return row["c"] if row else 0


def update_question(question_id: str, **fields) -> None:
    allowed = {
        "question", "options", "answer", "explanation", "source_quote",
        "kb_faq_id", "kb_question", "status", "selected",
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
        "ORDER BY q.created_at DESC, q.id DESC",
        (phone, now_iso),
    ).fetchall()
    return [dict(r) for r in rows]


# --- 场景字典（可自定义新增/停用）-----------------------------------------------

def ensure_default_scenes() -> None:
    """首次初始化内置场景（幂等，已存在不覆盖）。"""
    with _WRITE_LOCK:
        conn = _get_conn()
        for i, name in enumerate(QUIZ_DEFAULT_SCENES, start=1):
            conn.execute(
                "INSERT OR IGNORE INTO scenes (name, sort_order, active, created_at) VALUES (?, ?, 1, ?)",
                (name, i, _now()),
            )
        conn.commit()


def list_scenes(include_inactive: bool = False) -> list[dict]:
    """场景字典；默认只返回启用项，按 sort_order 排序。"""
    sql = "SELECT * FROM scenes"
    if not include_inactive:
        sql += " WHERE active = 1"
    sql += " ORDER BY sort_order, name"
    return [dict(r) for r in _get_conn().execute(sql)]


def add_scene(name: str) -> bool:
    """新增场景；重名返回 False（保持原名，避免统计分裂）。"""
    name = (name or "").strip()
    if not name:
        raise ValueError("场景名称不能为空")
    with _WRITE_LOCK:
        conn = _get_conn()
        exists = conn.execute("SELECT 1 FROM scenes WHERE name = ?", (name,)).fetchone()
        if exists:
            return False
        row = conn.execute("SELECT COALESCE(MAX(sort_order), 0) AS m FROM scenes").fetchone()
        conn.execute(
            "INSERT INTO scenes (name, sort_order, active, created_at) VALUES (?, ?, 1, ?)",
            (name, (row["m"] or 0) + 1, _now()),
        )
        conn.commit()
        return True


def set_scene_active(name: str, active: bool) -> bool:
    """停用/启用场景；返回是否更新成功（历史测验仍保留场景名）。"""
    with _WRITE_LOCK:
        conn = _get_conn()
        cur = conn.execute(
            "UPDATE scenes SET active = ? WHERE name = ?", (1 if active else 0, name)
        )
        conn.commit()
        return cur.rowcount > 0


def delete_quiz(quiz_id: str) -> None:
    """删除测验：连带清 keypoints/questions/targets/answers/imports（API 层仅放行 draft/reviewing）。"""
    with _WRITE_LOCK:
        conn = _get_conn()
        for tbl in ("answers", "targets", "questions", "keypoints", "imports"):
            conn.execute(f"DELETE FROM {tbl} WHERE quiz_id = ?", (quiz_id,))
        conn.execute("DELETE FROM quizzes WHERE id = ?", (quiz_id,))
        conn.commit()


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
            "SELECT id FROM quizzes "
            "WHERE status != 'archived' AND valid_until IS NOT NULL AND valid_until < ?",
            (cutoff,),
        ).fetchall()
        archived = 0
        for r in rows:
            qid = r["id"]
            for tbl in ("answers", "targets", "questions", "keypoints", "imports"):
                conn.execute(f"DELETE FROM {tbl} WHERE quiz_id = ?", (qid,))
            conn.execute(
                "UPDATE quizzes SET status = 'archived', updated_at = ? WHERE id = ?",
                (now_iso, qid),
            )
            archived += 1
        conn.commit()
        return {"archived": archived}
