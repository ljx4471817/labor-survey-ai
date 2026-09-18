# -*- coding: utf-8 -*-
"""双后端适配层测试。

- 纯函数 / 行对象：无 DB 依赖
- 连接 / DDL / 事务 / 线程局部：SQLite 与 PG 参数化
  （环境变量 LSX_TEST_PG_DSN 存在时 PG 用例自动启用；不存在时只跑 SQLite，
   保证本地零配置可跑）
- 5 个业务模块的关键流程：同样参数化，PG 路径被真实执行
"""
from __future__ import annotations

import os
import threading

import pytest

from app.persistence import (
    conversations as conversations_mod,
    db,
    query_log as query_log_mod,
    quiz_db,
    whitelist_db as wl,
)

PG_DSN = os.environ.get("LSX_TEST_PG_DSN")

BACKEND_PARAMS = [
    pytest.param("sqlite", id="sqlite"),
    pytest.param(
        "pg",
        id="pg",
        marks=pytest.mark.skipif(
            PG_DSN is None,
            reason="未设置 LSX_TEST_PG_DSN，跳过 PostgreSQL 参数化用例",
        ),
    ),
]


# --- 纯函数（无 DB） ----------------------------------------------------------


def test_is_pg_target():
    assert db.is_pg_target("postgres://u:p@h:5432/lsx")
    assert db.is_pg_target("postgresql://u:p@h:5432/lsx")
    assert not db.is_pg_target("backend/data/quiz.db")
    assert not db.is_pg_target("")
    assert not db.is_pg_target(None)


def test_to_pg_sql_placeholder():
    assert db._to_pg_sql("SELECT * FROM t WHERE a = ? AND b = ?") == (
        "SELECT * FROM t WHERE a = %s AND b = %s"
    )
    assert db._to_pg_sql("SELECT 1") == "SELECT 1"


def test_split_statements():
    script = "CREATE TABLE a (x INTEGER); CREATE TABLE b (y TEXT);;"
    stmts = db._split_statements(script)
    assert len(stmts) == 2
    assert stmts[0] == "CREATE TABLE a (x INTEGER)"


def test_row_access_by_name_and_index():
    row = db.Row({"a": 1, "b": "x"})
    assert row["a"] == 1          # 按列名
    assert row[0] == 1            # 按序号
    assert row[1] == "x"
    assert dict(row) == {"a": 1, "b": "x"}   # dict() 兼容
    assert list(row.keys()) == ["a", "b"]
    assert row.get("missing") is None


# --- 连接级参数化用例 ---------------------------------------------------------

_DDL = (
    "CREATE TABLE adapter_t ("
    " id INTEGER PRIMARY KEY, name TEXT NOT NULL, score DOUBLE PRECISION)"
)


@pytest.fixture(params=BACKEND_PARAMS)
def conn(request, tmp_path):
    if request.param == "sqlite":
        target = str(tmp_path / "adapter.db")
    else:
        target = PG_DSN
        pg = db.connect(target)
        pg.executescript(
            "DROP TABLE IF EXISTS adapter_t; DROP TABLE IF EXISTS adapter_u"
        )
        pg.commit()
    c = db.connect(target)
    yield c
    if request.param == "pg":
        c.executescript(
            "DROP TABLE IF EXISTS adapter_t; DROP TABLE IF EXISTS adapter_u"
        )
        c.commit()


def test_execute_roundtrip(conn):
    conn.executescript(_DDL)
    conn.commit()
    conn.execute(
        "INSERT INTO adapter_t (id, name, score) VALUES (?, ?, ?)", (1, "甲", 0.5)
    )
    conn.commit()
    row = conn.execute("SELECT * FROM adapter_t WHERE id = ?", (1,)).fetchone()
    assert row["name"] == "甲"     # 按列名
    assert row[1] == "甲"          # 按序号
    assert dict(row)["score"] == 0.5


def test_executescript_multi_statement(conn):
    conn.executescript(_DDL + "; CREATE TABLE adapter_u (x INTEGER)")
    conn.commit()
    conn.execute("INSERT INTO adapter_u (x) VALUES (1)")
    conn.commit()
    assert conn.execute("SELECT x FROM adapter_u").fetchone()["x"] == 1


def test_table_columns(conn):
    conn.executescript(_DDL)
    conn.commit()
    cols = db.table_columns(conn, "adapter_t")
    by_name = {c["name"]: c["notnull"] for c in cols}
    assert by_name["name"] == 1
    assert by_name["score"] == 0
    # 主键列 notnull 语义两边不同（SQLite INTEGER PRIMARY KEY 报 0，
    # PG 主键隐含 NOT NULL 报 1）；业务模块只用它判断普通列，不在此断言。


def test_operational_error_translated(conn):
    conn.executescript(_DDL)
    conn.commit()
    with pytest.raises(db.OperationalError):
        conn.execute("SELECT * FROM adapter_missing_table").fetchall()


def test_integrity_error_translated(conn):
    conn.executescript(_DDL)
    conn.commit()
    conn.execute(
        "INSERT INTO adapter_t (id, name, score) VALUES (?, ?, ?)", (1, "a", None)
    )
    conn.commit()
    with pytest.raises(db.Error):
        conn.execute(
            "INSERT INTO adapter_t (id, name, score) VALUES (?, ?, ?)",
            (1, "b", None),
        )
        conn.commit()


def test_rollback(conn):
    conn.executescript(_DDL)
    conn.commit()
    conn.execute(
        "INSERT INTO adapter_t (id, name, score) VALUES (?, ?, ?)", (1, "a", None)
    )
    conn.rollback()
    assert conn.execute("SELECT COUNT(*) AS c FROM adapter_t").fetchone()["c"] == 0


def test_thread_local_connections(conn):
    """同一连接对象被两个线程同时使用不报错（底层各自一份连接）。"""
    conn.executescript(_DDL)
    conn.commit()
    errors: list = []

    def worker(n: int) -> None:
        try:
            for i in range(20):
                conn.execute(
                    "INSERT INTO adapter_t (id, name, score) VALUES (?, ?, ?)",
                    (n * 100 + i, "t%d" % n, i * 0.1),
                )
                conn.commit()
                conn.execute("SELECT COUNT(*) AS c FROM adapter_t").fetchone()
        except Exception as exc:  # pragma: no cover - 失败时收集报错
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors
    assert conn.execute("SELECT COUNT(*) AS c FROM adapter_t").fetchone()["c"] == 40


def test_connect_registry_same_wrapper(tmp_path):
    target = str(tmp_path / "adapter.db")
    assert db.connect(target) is db.connect(target)


@pytest.mark.skipif(PG_DSN is None, reason="需要 LSX_TEST_PG_DSN")
def test_pg_real_schemas_execute():
    """真实业务 _SCHEMA_PG 在 PG 上可整体执行（幂等）。"""
    pg = db.connect(PG_DSN)
    for script in (
        wl._SCHEMA_PG, wl._AUDIT_SCHEMA_PG,
        query_log_mod._SCHEMA_PG, conversations_mod._SCHEMA_PG, quiz_db._SCHEMA_PG,
    ):
        pg.executescript(script)
    pg.commit()
    # 幂等：重复执行不报错
    pg.executescript(quiz_db._SCHEMA_PG)
    pg.commit()


# --- 5 个业务模块流程参数化用例 ------------------------------------------------


@pytest.fixture(params=BACKEND_PARAMS)
def backend_env(request, tmp_path, monkeypatch):
    """把 4 个库环境变量都指向当前参数化后端，并隔离各模块连接状态。"""
    if request.param == "sqlite":
        base = tmp_path
        targets = {
            "LSX_DB_WHITELIST": str(base / "whitelist.db"),
            "LSX_DB_CONVERSATIONS": str(base / "conversations.db"),
            "LSX_DB_QUERY_LOG": str(base / "query_log.db"),
            "LSX_DB_QUIZ": str(base / "quiz.db"),
        }
    else:
        targets = {k: PG_DSN for k in (
            "LSX_DB_WHITELIST", "LSX_DB_CONVERSATIONS",
            "LSX_DB_QUERY_LOG", "LSX_DB_QUIZ",
        )}
        # 清理上一次运行残留（PG 单库复用，SQLite 每测试新文件天然隔离）
        pg = db.connect(PG_DSN)
        pg.executescript(
            "DROP TABLE IF EXISTS whitelist; DROP TABLE IF EXISTS whitelist_audit;"
            " DROP TABLE IF EXISTS query_log; DROP TABLE IF EXISTS conversations;"
            " DROP TABLE IF EXISTS conversation_messages; DROP TABLE IF EXISTS quizzes;"
            " DROP TABLE IF EXISTS scenes; DROP TABLE IF EXISTS imports;"
            " DROP TABLE IF EXISTS keypoints; DROP TABLE IF EXISTS questions;"
            " DROP TABLE IF EXISTS targets; DROP TABLE IF EXISTS answers"
        )
        pg.commit()
    for k, v in targets.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(wl, "_conn", None)
    monkeypatch.setattr(query_log_mod, "_conn", None)
    quiz_db.reset_conn()
    conversations_mod.reset_conn()
    yield request.param
    monkeypatch.setattr(wl, "_conn", None)
    monkeypatch.setattr(query_log_mod, "_conn", None)
    quiz_db.reset_conn()
    conversations_mod.reset_conn()


def _region_user(phone="13900000001", level="调查员", role=None):
    user = {
        "phone": phone,
        "name": "测试员",
        "province": "贵州省",
        "city": "贵阳市",
        "county": "南明区",
        "township": "新华路街道",
        "community": "神奇路社区",
        "admin_level": level,
        "active": 1,
    }
    if role:
        user["sys_role"] = role
    return user


def test_whitelist_flow(backend_env):
    assert wl.is_whitelisted("13900000001") is False
    action = wl.upsert(_region_user())
    assert action == "inserted"
    assert wl.is_whitelisted("13900000001") is True
    user = wl.get_user("13900000001")
    assert user["name"] == "测试员"
    assert user["sys_role"] == "普通用户"
    # 更新不改 active，sys_role 仅显式传入才更新
    wl.upsert({**_region_user(), "name": "改名"})
    user = wl.get_user("13900000001")
    assert user["name"] == "改名"
    assert user["active"] == 1
    # 管理岗推导业务管理员
    wl.upsert(_region_user("13900000002", level="区县"))
    assert wl.get_user("13900000002")["sys_role"] == "业务管理员"
    wl.log_audit("13900000001", "update", "13900000002", actor_name="测试员")
    audit = wl.list_audit()
    assert audit[0]["action"] == "update"
    assert len(wl.list_active_phones()) == 2


def test_query_log_flow(backend_env):
    entry = {
        "phone": "13900000001",
        "name": "测试员",
        "province": "贵州省",
        "city": "贵阳市",
        "county": "南明区",
        "township": "新华路街道",
        "community": "神奇路社区",
        "query": "F10 怎么填",
        "mode": "rag",
        "retrieval_score": 0.87,
        "request_id": "req-1",
        "top_qa_id": "001",
    }
    query_log_mod.insert(entry)
    query_log_mod.insert({**entry, "request_id": "req-2"})
    assert query_log_mod.total_count() == 2
    regions = query_log_mod.stats_by_region(
        "county", parent={"province": "贵州省", "city": "贵阳市"}
    )
    assert regions[0]["count"] == 2
    usage = query_log_mod.search_usage({"county": "南明区"})
    assert usage[0]["query_count"] == 2
    top = query_log_mod.top_qa_stats(days=30)
    assert top[0]["top_qa_id"] == "001"


def test_conversations_flow(backend_env):
    first = conversations_mod.save_exchange(
        phone="13900000001",
        conversation_id=None,
        user_message="F10 怎么填",
        assistant_message="先问是否从事有酬劳动",
        mode="rag",
        sources=[{"qa_id": "001", "question": "q", "source": "s",
                  "category": "c", "score": 0.9, "image": None}],
        retrieval_score=0.9,
        request_id="req-1",
    )
    conversations_mod.save_exchange(
        phone="13900000001",
        conversation_id=first["id"],
        user_message="追问",
        assistant_message="再答",
        mode="rag",
        sources=[],
        retrieval_score=None,
        request_id="req-2",
    )
    convs = conversations_mod.list_conversations("13900000001")
    assert convs[0]["id"] == first["id"]
    msgs = conversations_mod.list_messages("13900000001", first["id"])
    assert [m["role"] for m in msgs] == ["user", "assistant", "user", "assistant"]
    ctx = conversations_mod.load_context_messages("13900000001", first["id"])
    assert ctx[-1]["content"] == "再答"
    assert conversations_mod.delete_conversation("13900000001", first["id"]) is True
    assert conversations_mod.list_messages("13900000001", first["id"]) == []


def test_quiz_flow(backend_env):
    quiz_db.ensure_default_scenes()
    quiz_db.ensure_default_scenes()  # 幂等（INSERT OR IGNORE / ON CONFLICT 路径）
    qid = quiz_db.create_quiz("月度测验", created_by="admin", month="2026-09")
    quiz_db.replace_keypoints(qid, [
        {"section": "审核要点", "content": "A", "suggest_quiz": True},
    ])
    quiz_db.replace_questions(qid, [
        {"question": "F10？", "options": "{}", "answer": "A", "explanation": "e"},
    ])
    quiz_db.update_question(qid + "Q01", selected=1)
    assert quiz_db.count_questions(qid, selected_only=True) == 1
    # targets：重复添加走 ON CONFLICT DO NOTHING
    assert quiz_db.set_targets(qid, ["13900000001"]) == 1
    assert quiz_db.add_targets(qid, ["13900000001", "13900000002"]) == 1
    assert quiz_db.count_targets(qid) == 2
    # answers：重复提交返回 duplicate
    assert quiz_db.submit_answer(qid, "13900000001", qid + "Q01", "A", True) == "inserted"
    assert quiz_db.submit_answer(qid, "13900000001", qid + "Q01", "B", False) == "duplicate"
    assert quiz_db.count_correct(qid, "13900000001") == 1
    assert quiz_db.add_scene("新场景") is True
    assert quiz_db.add_scene("新场景") is False  # 重名
    quiz_db.delete_quiz(qid)
    assert quiz_db.get_quiz(qid) is None