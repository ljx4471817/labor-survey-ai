"""会话历史 SQLite 持久化。

会话按手机号隔离；删除会话时物理删除消息，反馈与 query_log 不受影响。
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.core.config import PROJECT_ROOT

DB_PATH = Path(
    os.environ.get(
        "LSX_CONVERSATIONS_DB_PATH",
        str(PROJECT_ROOT / "backend" / "data" / "conversations.db"),
    )
)
UTC8 = timezone(timedelta(hours=8))

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id          TEXT PRIMARY KEY,
    phone       TEXT NOT NULL,
    title       TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS conversation_messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    phone           TEXT NOT NULL,
    role            TEXT NOT NULL,
    content         TEXT NOT NULL,
    mode            TEXT,
    sources_json    TEXT NOT NULL DEFAULT '[]',
    retrieval_score REAL,
    request_id      TEXT,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conversations_phone_active
    ON conversations(phone, updated_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_conversation_messages_conversation
    ON conversation_messages(conversation_id, id);
"""

_local = threading.local()
_WRITE_LOCK = threading.Lock()


def _get_conn() -> sqlite3.Connection:
    """按线程复用 SQLite 连接；端点使用普通 def，写入走进程内互斥。"""
    conn = getattr(_local, "conn", None)
    if conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(DB_PATH), check_same_thread=False, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.executescript(_SCHEMA)
        conn.commit()
        _local.conn = conn
    return conn


def reset_conn() -> None:
    """关闭当前线程连接（测试隔离 / 换库用）。"""
    conn = getattr(_local, "conn", None)
    if conn is not None:
        try:
            conn.close()
        except sqlite3.Error:
            pass
        _local.conn = None


def _now() -> str:
    return datetime.now(UTC8).isoformat(timespec="seconds")


def _row_to_conversation(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None


def get_conversation(phone: str, conversation_id: str) -> dict | None:
    """读取当前手机号拥有的会话；不存在或越权时统一返回 None。"""
    row = _get_conn().execute(
        "SELECT id, phone, title, created_at, updated_at "
        "FROM conversations WHERE id = ? AND phone = ?",
        (conversation_id, phone),
    ).fetchone()
    return _row_to_conversation(row)


def save_exchange(
    *,
    phone: str,
    conversation_id: str | None,
    user_message: str,
    assistant_message: str,
    mode: str,
    sources: list[dict[str, Any]],
    retrieval_score: float | None,
    request_id: str,
) -> dict:
    """原子保存一轮问答；首问成功后创建会话并生成标题。"""
    now = _now()
    conn = _get_conn()
    with _WRITE_LOCK:
        conversation_title: str
        if conversation_id:
            row = conn.execute(
                "SELECT id, title FROM conversations WHERE id = ? AND phone = ?",
                (conversation_id, phone),
            ).fetchone()
            if row is None:
                raise ValueError("会话不存在")
            conversation_title = row["title"]
        else:
            conversation_id = uuid.uuid4().hex[:16]
            conversation_title = user_message.strip()[:20]
            conn.execute(
                "INSERT INTO conversations (id, phone, title, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (conversation_id, phone, conversation_title, now, now),
            )

        try:
            sources_json = json.dumps(
                sources, ensure_ascii=False, separators=(",", ":")
            )
            conn.execute(
                "INSERT INTO conversation_messages "
                "(conversation_id, phone, role, content, mode, sources_json, "
                " retrieval_score, request_id, created_at) "
                "VALUES (?, ?, 'user', ?, NULL, '[]', NULL, ?, ?)",
                (conversation_id, phone, user_message, request_id, now),
            )
            conn.execute(
                "INSERT INTO conversation_messages "
                "(conversation_id, phone, role, content, mode, sources_json, "
                " retrieval_score, request_id, created_at) "
                "VALUES (?, ?, 'assistant', ?, ?, ?, ?, ?, ?)",
                (
                    conversation_id,
                    phone,
                    assistant_message,
                    mode,
                    sources_json,
                    retrieval_score,
                    request_id,
                    now,
                ),
            )
            conn.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ? AND phone = ?",
                (now, conversation_id, phone),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    return {
        "id": conversation_id,
        "title": conversation_title,
        "created_at": now,
        "updated_at": now,
    }


def list_conversations(
    phone: str, *, limit: int = 10, offset: int = 0
) -> list[dict]:
    """按最近活跃时间分页返回用户会话。"""
    rows = _get_conn().execute(
        "SELECT id, title, created_at, updated_at "
        "FROM conversations WHERE phone = ? "
        "ORDER BY updated_at DESC, id DESC LIMIT ? OFFSET ?",
        (phone, limit, offset),
    ).fetchall()
    return [dict(row) for row in rows]


def _parse_sources(raw: str) -> list[dict[str, Any]]:
    try:
        value = json.loads(raw or "[]")
        return value if isinstance(value, list) else []
    except (TypeError, ValueError, json.JSONDecodeError):
        return []


def list_messages(
    phone: str,
    conversation_id: str,
    *,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """返回最近 limit 条消息并按时间正序排列；不提供更早分页。"""
    conn = _get_conn()
    conversation = get_conversation(phone, conversation_id)
    if conversation is None:
        return []
    rows = conn.execute(
        "SELECT * FROM ("
        "  SELECT * FROM conversation_messages "
        "  WHERE conversation_id = ? AND phone = ? "
        "  ORDER BY id DESC LIMIT ?"
        ") ORDER BY id ASC",
        (conversation_id, phone, limit),
    ).fetchall()
    messages: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["sources"] = _parse_sources(item.pop("sources_json", "[]"))
        messages.append(item)
    return messages


def load_context_messages(
    phone: str,
    conversation_id: str,
    *,
    limit: int = 8,
) -> list[dict[str, str]]:
    """返回 LLM 所需的最近消息上下文，正序排列。"""
    conn = _get_conn()
    conversation = get_conversation(phone, conversation_id)
    if conversation is None:
        return []
    rows = conn.execute(
        "SELECT * FROM ("
        "  SELECT id, role, content FROM conversation_messages "
        "  WHERE conversation_id = ? AND phone = ? "
        "  ORDER BY id DESC LIMIT ?"
        ") ORDER BY id ASC",
        (conversation_id, phone, limit),
    ).fetchall()
    return [{"role": row["role"], "content": row["content"]} for row in rows]


def delete_conversation(phone: str, conversation_id: str) -> bool:
    """物理删除当前用户的一个会话及其全部消息。"""
    conn = _get_conn()
    with _WRITE_LOCK:
        row = conn.execute(
            "SELECT id FROM conversations WHERE id = ? AND phone = ?",
            (conversation_id, phone),
        ).fetchone()
        if row is None:
            return False
        try:
            conn.execute(
                "DELETE FROM conversation_messages WHERE conversation_id = ? AND phone = ?",
                (conversation_id, phone),
            )
            conn.execute(
                "DELETE FROM conversations WHERE id = ? AND phone = ?",
                (conversation_id, phone),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return True
