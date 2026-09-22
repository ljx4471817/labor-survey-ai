# -*- coding: utf-8 -*-
"""建表 DDL 护栏回归测试（2026-09-22 线上死锁事故）。

事故：`conversations._get_conn()` 在**每个新线程首次调用**时都会重跑整套
CREATE TABLE / CREATE INDEX。FastAPI 的 sync 端点在 uvicorn 线程池里执行，
线程不断新增 ⇒ DDL 反复执行；它与并发中的业务写入形成 AB-BA 死锁：

    线程甲 save_exchange：持 conversation_messages 写锁 → 等 conversations 写锁
    线程乙 建表 DDL      ：持 conversations ShareLock  → 等 conversation_messages ShareLock

PG 挑一个当事务受害者 ⇒ psycopg.errors.DeadlockDetected ⇒ /api/chat 500。

修复：把 DDL 收进进程级双检锁，保证「每目标库只初始化一次」
（对齐 quiz_db.py 既有的 _schema_lock / _schema_ready_for 写法）。

本文件锁定该机制：N 个线程并发首次调用时，建表 DDL 只应发生 1 次。
"""
from __future__ import annotations

import threading

from app.persistence import conversations as conversations_mod
from app.persistence import db
from app.persistence import query_log as query_log_mod
from app.persistence import whitelist_db as whitelist_mod


class _Spy:
    """包一层 db.connect，统计「建表 DDL 执行次数」与「连接创建次数」。"""

    def __init__(self, monkeypatch):
        self.executescript_calls = []
        self.connect_calls = 0
        self._lock = threading.Lock()
        self._real_connect = db.connect
        self._monkeypatch = monkeypatch

    def __enter__(self):
        real_connect = self._real_connect

        def counting_connect(*args, **kwargs):
            with self._lock:
                self.connect_calls += 1
            conn = real_connect(*args, **kwargs)
            real_es = conn.executescript

            def es(script):
                with self._lock:
                    self.executescript_calls.append(script)
                return real_es(script)

            conn.executescript = es
            return conn

        self._monkeypatch.setattr(db, "connect", counting_connect)
        return self

    def __exit__(self, *exc):
        return False


def _run_concurrent(target_fn, n=8):
    """n 个线程同时冲 target_fn（用 Barrier 保证真正并发），返回异常文本列表。"""
    errors = []
    barrier = threading.Barrier(n)

    def worker():
        try:
            barrier.wait(timeout=5)
            target_fn()
        except Exception as exc:  # pragma: no cover - 只在护栏失效时才走到
            errors.append(repr(exc))

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)
    return errors


def test_conversations_schema_ddl_runs_once_across_threads(tmp_path, monkeypatch):
    """本次事故的现场：conversations 的建表 DDL 必须只跑一次。"""
    monkeypatch.delenv("LSX_DB_CONVERSATIONS", raising=False)
    monkeypatch.setattr(conversations_mod, "DB_PATH", tmp_path / "conversations.db")
    monkeypatch.setattr(
        conversations_mod, "_schema_ready_for", None, raising=False
    )
    conversations_mod.reset_conn()

    with _Spy(monkeypatch) as spy:
        errors = _run_concurrent(conversations_mod._get_conn)

    assert errors == []
    assert len(spy.executescript_calls) == 1, (
        f"建表 DDL 应只执行 1 次，实际 {len(spy.executescript_calls)} 次"
        "（护栏失效 ⇒ 并发首调会重跑 DDL ⇒ 与业务写入死锁）"
    )
    conversations_mod.reset_conn()


def test_whitelist_schema_init_runs_once_across_threads(tmp_path, monkeypatch):
    """whitelist_db 是同一模式的高危点（DDL + _migrate 混在首次连接里）。"""
    monkeypatch.delenv("LSX_DB_WHITELIST", raising=False)
    monkeypatch.setattr(whitelist_mod, "DB_PATH", tmp_path / "whitelist.db")
    monkeypatch.setattr(whitelist_mod, "_conn", None, raising=False)

    with _Spy(monkeypatch) as spy:
        errors = _run_concurrent(whitelist_mod._get_conn)

    assert errors == []
    assert spy.connect_calls == 1, (
        f"并发首次调用应只初始化一次，实际创建了 {spy.connect_calls} 条连接"
    )
    monkeypatch.setattr(whitelist_mod, "_conn", None, raising=False)


def test_query_log_schema_init_runs_once_across_threads(tmp_path, monkeypatch):
    """query_log 是同一模式的第三处（改动前 `if _conn is None` 存在竞态）。"""
    monkeypatch.delenv("LSX_DB_QUERY_LOG", raising=False)
    monkeypatch.setattr(query_log_mod, "DB_PATH", tmp_path / "query_log.db")
    monkeypatch.setattr(query_log_mod, "_conn", None, raising=False)

    with _Spy(monkeypatch) as spy:
        errors = _run_concurrent(query_log_mod._get_conn)

    assert errors == []
    assert spy.connect_calls == 1, (
        f"并发首次调用应只初始化一次，实际创建了 {spy.connect_calls} 条连接"
    )
    monkeypatch.setattr(query_log_mod, "_conn", None, raising=False)
