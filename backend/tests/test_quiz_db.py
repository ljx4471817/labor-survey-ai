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


def test_create_quiz_id_and_month_seq(db):
    q1 = db.create_quiz("2026-08", "t1", "admin")
    q2 = db.create_quiz("2026-08", "t2", "admin")
    q3 = db.create_quiz("2026-09", "t3", "admin")
    assert q1 == "Q20260801"
    assert q2 == "Q20260802"
    assert q3 == "Q20260901"
    quiz = db.get_quiz(q1)
    assert quiz["month"] == "2026-08" and quiz["status"] == "draft"


def test_replace_keypoints_and_list(db):
    qid = db.create_quiz("2026-08", "t", "admin")
    db.replace_keypoints(qid, [
        {"section": "审核要点", "content": "A", "common_error": "e", "source_quote": "s", "suggest_quiz": True},
    ])
    kps = db.list_keypoints(qid)
    assert len(kps) == 1
    assert kps[0]["status"] == "draft"
    assert kps[0]["id"] == qid + "KP01"


def test_update_keypoint_status(db):
    qid = db.create_quiz("2026-08", "t", "admin")
    db.replace_keypoints(qid, [{"section": "x", "content": "A"}])
    kp = db.list_keypoints(qid)[0]
    db.update_keypoint(kp["id"], reviewed_by="admin", status="approved", content="B")
    kp2 = db.get_keypoint(kp["id"])
    assert kp2["status"] == "approved"
    assert kp2["content"] == "B"
    assert kp2["reviewed_by"] == "admin"


def test_replace_questions_and_list(db):
    qid = db.create_quiz("2026-08", "t", "admin")
    db.replace_questions(qid, [{
        "question": "q?", "options": '{"A": "1", "B": "2", "C": "3", "D": "4"}',
        "answer": "C", "explanation": "e", "created_by": "admin",
    }])
    qs = db.list_questions(qid)
    assert len(qs) == 1
    assert qs[0]["seq"] == 1
    assert db.count_questions(qid) == 1


def test_update_question(db):
    qid = db.create_quiz("2026-08", "t", "admin")
    db.replace_questions(qid, [{"question": "q?", "options": '{"A": "1", "B": "2", "C": "3", "D": "4"}', "answer": "A", "explanation": "e", "created_by": "a"}])
    q = db.list_questions(qid)[0]
    db.update_question(q["id"], status="approved", answer="B")
    q2 = db.get_question(q["id"])
    assert q2["status"] == "approved" and q2["answer"] == "B"


def test_targets_protect_answered(db):
    qid = db.create_quiz("2026-08", "t", "admin")
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
    qid = db.create_quiz("2026-08", "t", "admin")
    db.replace_questions(qid, [{"question": "q?", "options": '{"A": "1", "B": "2", "C": "3", "D": "4"}', "answer": "C", "explanation": "e", "created_by": "a"}])
    q = db.list_questions(qid)[0]
    assert db.submit_answer(qid, "13800000001", q["id"], "C", True) == "inserted"
    assert db.submit_answer(qid, "13800000001", q["id"], "A", False) == "duplicate"
    ans = db.get_answers(qid, "13800000001")
    assert len(ans) == 1 and ans[0]["selected"] == "C"
    assert db.count_answers(qid, "13800000001") == 1
    assert db.count_correct(qid, "13800000001") == 1


def test_has_published_quiz(db):
    qid = db.create_quiz("2026-08", "t", "admin")
    assert db.has_published_quiz("2026-08") is False
    db.update_quiz(qid, status="published", valid_from="2026-08-01T00:00:00+08:00", valid_until="2026-08-08T00:00:00+08:00")
    assert db.has_published_quiz("2026-08") is True


def test_sync_expired(db):
    qid = db.create_quiz("2026-08", "t", "admin")
    db.update_quiz(qid, status="published", valid_until="2026-08-01T00:00:00+08:00")
    n = db.sync_expired("2026-08-06T12:00:00+08:00")
    assert n == 1
    assert db.get_quiz(qid)["status"] == "expired"


def test_cleanup_expired_archives_12mo(db):
    qid = db.create_quiz("2026-01", "t", "admin")
    db.replace_questions(qid, [{"question": "q?", "options": '{"A": "1", "B": "2", "C": "3", "D": "4"}', "answer": "A", "explanation": "e", "created_by": "a"}])
    db.set_targets(qid, ["13800000001"])
    db.update_quiz(qid, status="published", valid_until="2024-02-01T00:00:00+08:00")
    res = db.cleanup_expired("2026-08-06T12:00:00+08:00")
    assert res["archived"] == 1
    assert db.get_quiz(qid)["status"] == "archived"
    assert db.list_questions(qid) == []
    assert db.list_target_phones(qid) == []


def test_list_active_for_user(db):
    qid = db.create_quiz("2026-08", "t", "admin")
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


def test_latest_import_for_month(db):
    db.create_import("2026-08", "a.docx", 100, "admin")
    imp2 = db.create_import("2026-08", "b.docx", 200, "admin")
    latest = db.latest_import_for_month("2026-08")
    assert latest["id"] == imp2
