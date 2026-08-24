# -*- coding: utf-8 -*-
"""quiz_db 持久化测试（隔离临时 DB，PRD v3 10.1）。"""
from __future__ import annotations

import pytest

from app.persistence import quiz_db


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(quiz_db, "DB_PATH", tmp_path / "quiz_test.db")
    quiz_db.reset_conn()
    yield quiz_db


def test_create_quiz_id_global_seq(db):
    q1 = db.create_quiz("t1", created_by="admin", month="2026-08")
    q2 = db.create_quiz("t2", created_by="admin", month="2026-08")
    q3 = db.create_quiz("t3", created_by="admin", month="2026-09")
    assert q1 == "Q0001"
    assert q2 == "Q0002"
    assert q3 == "Q0003"
    quiz = db.get_quiz(q1)
    assert quiz["month"] == "2026-08" and quiz["status"] == "draft"


def test_replace_keypoints_and_list(db):
    qid = db.create_quiz("t", created_by="admin", month="2026-08")
    db.replace_keypoints(qid, [
        {"section": "审核要点", "content": "A", "common_error": "e", "source_quote": "s", "suggest_quiz": True},
    ])
    kps = db.list_keypoints(qid)
    assert len(kps) == 1
    assert kps[0]["status"] == "draft"
    assert kps[0]["id"] == qid + "KP01"


def test_update_keypoint_status(db):
    qid = db.create_quiz("t", created_by="admin", month="2026-08")
    db.replace_keypoints(qid, [{"section": "x", "content": "A"}])
    kp = db.list_keypoints(qid)[0]
    db.update_keypoint(kp["id"], reviewed_by="admin", status="approved", content="B")
    kp2 = db.get_keypoint(kp["id"])
    assert kp2["status"] == "approved"
    assert kp2["content"] == "B"
    assert kp2["reviewed_by"] == "admin"


def test_replace_questions_and_list(db):
    qid = db.create_quiz("t", created_by="admin", month="2026-08")
    db.replace_questions(qid, [{
        "question": "q?", "options": '{"A": "1", "B": "2", "C": "3", "D": "4"}',
        "answer": "C", "explanation": "e", "created_by": "admin",
    }])
    qs = db.list_questions(qid)
    assert len(qs) == 1
    assert qs[0]["seq"] == 1
    assert db.count_questions(qid) == 1


def test_update_question(db):
    qid = db.create_quiz("t", created_by="admin", month="2026-08")
    db.replace_questions(qid, [{"question": "q?", "options": '{"A": "1", "B": "2", "C": "3", "D": "4"}', "answer": "A", "explanation": "e", "created_by": "a"}])
    q = db.list_questions(qid)[0]
    db.update_question(q["id"], status="approved", answer="B")
    q2 = db.get_question(q["id"])
    assert q2["status"] == "approved" and q2["answer"] == "B"


def test_targets_protect_answered(db):
    qid = db.create_quiz("t", created_by="admin", month="2026-08")
    db.replace_questions(qid, [{"question": "q?", "options": '{"A": "1", "B": "2", "C": "3", "D": "4"}', "answer": "A", "explanation": "e", "created_by": "a"}])
    q = db.list_questions(qid)[0]
    db.set_targets(qid, ["13800000001", "13800000002"])
    db.submit_answer(qid, "13800000001", q["id"], "A", True)
    # 已答用户不可移除；未答用户可移除
    removed = db.remove_targets(qid, ["13800000001", "13800000002"])
    assert removed == ["13800000002"]
    assert db.is_target(qid, "13800000001")
    assert not db.is_target(qid, "13800000002")
    # 追加
    added = db.add_targets(qid, ["13800000002", "13800000003"])
    assert added == 2


def test_submit_answer_locks_duplicate(db):
    qid = db.create_quiz("t", created_by="admin", month="2026-08")
    db.replace_questions(qid, [{"question": "q?", "options": '{"A": "1", "B": "2", "C": "3", "D": "4"}', "answer": "C", "explanation": "e", "created_by": "a"}])
    q = db.list_questions(qid)[0]
    assert db.submit_answer(qid, "13800000001", q["id"], "C", True) == "inserted"
    assert db.submit_answer(qid, "13800000001", q["id"], "A", False) == "duplicate"
    ans = db.get_answers(qid, "13800000001")
    assert len(ans) == 1 and ans[0]["selected"] == "C"
    assert db.count_answers(qid, "13800000001") == 1
    assert db.count_correct(qid, "13800000001") == 1


def test_scenes_dict_and_delete_quiz(db):
    db.ensure_default_scenes()
    names = [s["name"] for s in db.list_scenes()]
    assert "月度通知" in names and "新员工培训" in names
    assert db.add_scene("半年培训") is True
    assert db.add_scene("半年培训") is False  # 重名
    assert db.set_scene_active("季度培训", False) is True
    assert "季度培训" not in [s["name"] for s in db.list_scenes()]
    assert "季度培训" in [s["name"] for s in db.list_scenes(include_inactive=True)]
    # 删除测验连带清理要点/题目/目标/答题/导入
    qid = db.create_quiz("t", created_by="admin", month="2026-08")
    db.replace_questions(qid, [{"question": "q?", "options": '{"A": "1", "B": "2", "C": "3", "D": "4"}', "answer": "A", "explanation": "e", "created_by": "a"}])
    db.create_import(qid, "2026-08", "a.docx", 100, "admin")
    db.delete_quiz(qid)
    assert db.get_quiz(qid) is None
    assert db.list_questions(qid) == []
    assert db.latest_import_for_quiz(qid) is None


def test_sync_expired(db):
    qid = db.create_quiz("t", created_by="admin", month="2026-08")
    db.update_quiz(qid, status="published", valid_until="2026-08-01T00:00:00+08:00")
    n = db.sync_expired("2026-08-06T12:00:00+08:00")
    assert n == 1
    assert db.get_quiz(qid)["status"] == "expired"


def test_cleanup_expired_archives_12mo(db):
    qid = db.create_quiz("t", created_by="admin", month="2026-01")
    db.replace_questions(qid, [{"question": "q?", "options": '{"A": "1", "B": "2", "C": "3", "D": "4"}', "answer": "A", "explanation": "e", "created_by": "a"}])
    db.set_targets(qid, ["13800000001"])
    db.update_quiz(qid, status="published", valid_until="2024-02-01T00:00:00+08:00")
    res = db.cleanup_expired("2026-08-06T12:00:00+08:00")
    assert res["archived"] == 1
    assert db.get_quiz(qid)["status"] == "archived"
    assert db.list_questions(qid) == []
    assert db.list_target_phones(qid) == []


def test_list_active_for_user(db):
    qid = db.create_quiz("t", created_by="admin", month="2026-08")
    db.replace_questions(qid, [{"question": "q?", "options": '{"A": "1", "B": "2", "C": "3", "D": "4"}', "answer": "A", "explanation": "e", "created_by": "a"}])
    db.update_quiz(qid, status="published", valid_until="2026-08-10T00:00:00+08:00")
    db.set_targets(qid, ["13800000001"])
    active = db.list_active_for_user("13800000001", "2026-08-06T12:00:00+08:00")
    assert len(active) == 1
    # 过期后不可见
    active2 = db.list_active_for_user("13800000001", "2026-08-20T12:00:00+08:00")
    assert active2 == []
    # 非目标不可见
    assert db.list_active_for_user("13899999999", "2026-08-06T12:00:00+08:00") == []


def test_latest_import_for_quiz(db):
    qid = db.create_quiz("t", created_by="admin", month="2026-08")
    db.create_import(qid, "2026-08", "a.docx", 100, "admin")
    imp2 = db.create_import(qid, "2026-08", "b.docx", 200, "admin")
    latest = db.latest_import_for_quiz(qid)
    assert latest["id"] == imp2
    assert db.latest_import_for_quiz("Q9999") is None


def test_next_seq_max_after_delete(db):
    """删除中间行后 id 仍递增（MAX+1，不重复 COUNT 方案的主键冲突）。"""
    q1 = db.create_quiz("A", scene="x", created_by="admin")
    q2 = db.create_quiz("B", scene="x", created_by="admin")
    assert (q1, q2) == ("Q0001", "Q0002")
    # 模拟用户清理数据：删除 Q0001，只剩 Q0002
    conn = quiz_db._get_conn()
    conn.execute("DELETE FROM quizzes WHERE id = ?", (q1,))
    conn.commit()
    q3 = db.create_quiz("C", scene="x", created_by="admin")
    assert q3 == "Q0003"  # 修复前 COUNT=1 -> Q0002 主键冲突

# --- 老库 schema 迁移回归（25c9b4f → 4128364+） --------------------------------

_OLD_QUIZ_DDL = """
CREATE TABLE quizzes (
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
CREATE TABLE imports (
    id              TEXT PRIMARY KEY,
    month           TEXT NOT NULL,
    filename        TEXT NOT NULL,
    file_size       INTEGER NOT NULL,
    status          TEXT NOT NULL DEFAULT 'imported',
    raw_text_length INTEGER,
    extracted_by    TEXT NOT NULL,
    extracted_at    TEXT NOT NULL
);
CREATE TABLE questions (
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
"""


def test_migrate_legacy_schema_25c9b4f(tmp_path, monkeypatch):
    """老库（无 scene/quiz_id/scenes，month NOT NULL）首次初始化自动补齐并保数据。"""
    import sqlite3

    db_path = tmp_path / "quiz_legacy.db"
    con = sqlite3.connect(str(db_path))
    con.executescript(_OLD_QUIZ_DDL)
    con.execute(
        "INSERT INTO quizzes (id, month, title, status, created_by, created_at, updated_at) "
        "VALUES ('Q20260801', '2026-08', '8月提示', 'draft', 'admin', "
        "'2026-08-01T00:00:00+08:00', '2026-08-01T00:00:00+08:00')"
    )
    con.execute(
        "INSERT INTO imports (id, month, filename, file_size, status, extracted_by, extracted_at) "
        "VALUES ('IMP20260801', '2026-08', 'a.wps', 123, 'extracted', 'admin', "
        "'2026-08-01T00:00:00+08:00')"
    )
    con.commit()
    con.close()

    monkeypatch.setattr(quiz_db, "DB_PATH", db_path)
    quiz_db.reset_conn()

    # 迁移后旧数据保留且 scene 落空串；不填月份也能建测验/导入
    old = quiz_db.get_quiz("Q20260801")
    assert old is not None and old["scene"] == "" and old["month"] == "2026-08"
    qid = quiz_db.create_quiz("无月份测验", scene="月度通知", created_by="admin", month=None)
    assert qid.startswith("Q")
    imp_id = quiz_db.create_import(qid, None, "b.docx", 100, "admin")
    assert imp_id.startswith("IMP")
    old_imp = quiz_db.get_import("IMP20260801")
    assert old_imp is not None and old_imp["quiz_id"] == "" and old_imp["month"] == "2026-08"

    # schema 断言：quizzes.scene / imports.quiz_id / scenes 表 / month 可空
    con = sqlite3.connect(str(db_path))
    qcols = {r[1]: r[3] for r in con.execute("PRAGMA table_info(quizzes)")}
    icols = {r[1]: r[3] for r in con.execute("PRAGMA table_info(imports)")}
    assert qcols.get("scene") is not None and qcols["month"] == 0
    assert icols.get("quiz_id") is not None and icols["month"] == 0
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "scenes" in tables
    qqcols = {r[1] for r in con.execute("PRAGMA table_info(questions)")}
    assert "selected" in qqcols
    con.close()

    # 幂等：模拟服务重启再次初始化，迁移全部跳过、schema 不变
    monkeypatch.setattr(quiz_db, "_schema_ready_for", None)
    quiz_db.reset_conn()
    assert len(quiz_db.list_quizzes()) == 2
    quiz_db.create_quiz("再建一个", scene="其他", created_by="admin")
    con = sqlite3.connect(str(db_path))
    qcols2 = {r[1]: r[3] for r in con.execute("PRAGMA table_info(quizzes)")}
    assert qcols2["month"] == 0 and "scene" in qcols2
    con.close()
