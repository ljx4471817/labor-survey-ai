# -*- coding: utf-8 -*-
"""多场景改造测试：场景字典 / 删除测验 / my 分组 / 题量 count / 列表筛选（PRD v5）。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import BytesIO

import pytest
from fastapi import HTTPException, UploadFile

from app.api import quiz as quiz_api
from app.api import quiz_admin as quiz_admin_api
from app.models.schemas.quiz import QuizSubmitRequest
from app.models.schemas.quiz_admin import (
    GenerateRequest,
    QuizDeleteRequest,
    SceneAddRequest,
    SceneToggleRequest,
)
from app.persistence import quiz_db

UTC8 = timezone(timedelta(hours=8))


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(quiz_db, "DB_PATH", tmp_path / "quiz_test.db")
    monkeypatch.setattr(quiz_admin_api, "TMP_DIR", tmp_path / "tmp")
    quiz_db.reset_conn()
    yield quiz_db


def _seed_published(db, title="测试测验", scene="月度通知", month="2026-08", phone="13800000001"):
    qid = db.create_quiz(title, scene=scene, created_by="admin", month=month)
    db.replace_questions(qid, [{
        "question": "家务劳动者无收入应判定为？",
        "options": '{"A": "就业人口", "B": "失业人口", "C": "非劳动力", "D": "在职未就业"}',
        "answer": "C", "explanation": "根据审核要点应判为非劳动力。", "created_by": "admin",
    }])
    for q in db.list_questions(qid):
        db.update_question(q["id"], status="approved")
    valid_until = (datetime.now(UTC8) + timedelta(days=7)).isoformat(timespec="seconds")
    db.set_targets(qid, [phone])
    db.update_quiz(qid, status="published", valid_from=datetime.now(UTC8).isoformat(timespec="seconds"), valid_until=valid_until)
    return qid


def test_scenes_api_crud(db):
    quiz_db.ensure_default_scenes()
    r = quiz_admin_api.quiz_scenes(include_inactive=False, phone="13900000001")
    names = [s["name"] for s in r["items"]]
    assert "月度通知" in names and "新员工培训" in names
    # 新增
    r2 = quiz_admin_api.quiz_scene_add(SceneAddRequest(name="半年培训"), phone="13900000001")
    assert r2["created"] is True
    r3 = quiz_admin_api.quiz_scene_add(SceneAddRequest(name="半年培训"), phone="13900000001")
    assert r3["created"] is False  # 重名不重复登记
    # 停用
    r4 = quiz_admin_api.quiz_scene_toggle(SceneToggleRequest(name="半年培训", active=False), phone="13900000001")
    assert r4["ok"] is True
    active = [s["name"] for s in quiz_db.list_scenes()]
    assert "半年培训" not in active
    assert "半年培训" in [s["name"] for s in quiz_db.list_scenes(include_inactive=True)]
    # 不存在
    with pytest.raises(HTTPException) as e:
        quiz_admin_api.quiz_scene_toggle(SceneToggleRequest(name="不存在", active=True), phone="13900000001")
    assert e.value.status_code == 404


def test_delete_quiz_api_only_draft(db):
    qid = quiz_db.create_quiz("草稿测验", scene="其他", created_by="admin")
    r = quiz_admin_api.quiz_delete(QuizDeleteRequest(quiz_id=qid), phone="13900000001")
    assert r["ok"] is True
    assert quiz_db.get_quiz(qid) is None
    # 已下发不可删
    qid2 = _seed_published(db)
    with pytest.raises(HTTPException) as e:
        quiz_admin_api.quiz_delete(QuizDeleteRequest(quiz_id=qid2), phone="13900000001")
    assert e.value.status_code == 409


def test_my_groups_todo_done(db):
    qid = _seed_published(db, title="8月工作提示测试", scene="月度通知")
    data = quiz_api.my(phone="13800000001")
    assert len(data["todo"]) == 1
    assert data["todo"][0]["scene"] == "月度通知"
    assert data["done"] == []
    # 答完 → 移入已完成
    q = quiz_db.list_questions(qid)[0]
    quiz_api.submit(QuizSubmitRequest(quiz_id=qid, q_id=q["id"], selected="C"), phone="13800000001")
    data2 = quiz_api.my(phone="13800000001")
    assert data2["todo"] == []
    assert len(data2["done"]) == 1
    assert data2["done"][0]["scene"] == "月度通知"
    # 非目标不可见
    assert quiz_api.my(phone="13899999999") == {"todo": [], "done": []}


def test_do_generate_respects_count(db, monkeypatch):
    qid = quiz_db.create_quiz("培训测验", scene="新员工培训", created_by="admin", month=None)
    keypoints = [{"content": f"要点{i}", "source_quote": "s"} for i in range(5)]

    def fake_llm(messages, **kw):
        return '{"question": "q?", "options": {"A": "a", "B": "b", "C": "c", "D": "d"}, "answer": "A", "explanation": "e"}'

    monkeypatch.setattr(quiz_admin_api, "_get_llm_chat", lambda: fake_llm)
    # count 指定 3 题
    res = quiz_admin_api._do_generate(qid, keypoints, "admin", count=3)
    assert res["questions"] == 3
    assert res["requested"] == 3
    assert len(quiz_db.list_questions(qid)) == 3
    # 要点不足：count=20 但只有 5 个要点 → 实际 5 题
    res2 = quiz_admin_api._do_generate(qid, keypoints, "admin", count=20)
    assert res2["questions"] == 5
    assert res2["requested"] == 20
    assert len(quiz_db.list_questions(qid)) == 5


def test_generate_request_count_validation(db):
    """GenerateRequest.count 校验：合法上限 20 / 缺省 None / 超限拒绝（不触发异步任务）。"""
    import pydantic

    r = GenerateRequest(quiz_id="Q0001", keypoint_ids=["x"], count=20)
    assert r.count == 20
    r2 = GenerateRequest(quiz_id="Q0001", keypoint_ids=["x"])
    assert r2.count is None
    with pytest.raises(pydantic.ValidationError):
        GenerateRequest(quiz_id="Q0001", keypoint_ids=["x"], count=21)


def test_list_scene_filter(db):
    db.create_quiz("月度A", scene="月度通知", created_by="admin", month="2026-08")
    db.create_quiz("培训B", scene="新员工培训", created_by="admin")
    r = quiz_admin_api.quiz_list(scene="新员工培训", month=None, phone="13900000001")
    assert len(r["items"]) == 1
    assert r["items"][0]["scene"] == "新员工培训"
    r2 = quiz_admin_api.quiz_list(scene=None, month="2026-08", phone="13900000001")
    assert len(r2["items"]) == 1
    r3 = quiz_admin_api.quiz_list(scene=None, month=None, phone="13900000001")
    assert len(r3["items"]) == 2


def test_import_auto_register_scene(db):
    f = UploadFile(filename="半年培训材料.docx", file=BytesIO(b"x"))
    res = quiz_admin_api.quiz_import(title="半年培训测验", scene="半年培训", month="", file=f, phone="13900000001")
    assert res["quiz_id"]
    names = [s["name"] for s in quiz_db.list_scenes(include_inactive=True)]
    assert "半年培训" in names
