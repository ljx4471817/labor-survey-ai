# -*- coding: utf-8 -*-
"""多场景改造测试：场景字典 / 删除测验 / my 分组 / 提取数量 / 题目勾选 / 列表筛选（PRD v5）。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import BytesIO

import pytest
from fastapi import HTTPException, UploadFile

from app.api import quiz as quiz_api
from app.api import quiz_admin as quiz_admin_api
from app.models.schemas.quiz import QuizSubmitRequest
from app.models.schemas.quiz_admin import (
    ExtractRequest,
    PublishRequest,
    QuestionSelectRequest,
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
        db.update_question(q["id"], status="approved", selected=1)
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
    assert data2["done"][0]["score"] == 1  # 答对 1 题
    # 非目标不可见
    assert quiz_api.my(phone="13899999999") == {"todo": [], "done": []}


def test_do_generate_all_and_selected_reset(db, monkeypatch):
    """生成不再指定题量：按勾选要点全量生成；生成后 selected 全部重置为 0。"""
    qid = quiz_db.create_quiz("培训测验", scene="新员工培训", created_by="admin", month=None)
    keypoints = [{"content": f"要点{i}", "source_quote": "s"} for i in range(5)]

    def fake_llm(messages, **kw):
        return '{"question": "q?", "options": {"A": "a", "B": "b", "C": "c", "D": "d"}, "answer": "A", "explanation": "e"}'

    monkeypatch.setattr(quiz_admin_api, "_get_llm_chat", lambda: fake_llm)
    res = quiz_admin_api._do_generate(qid, keypoints, "admin")
    assert res["questions"] == 5
    assert len(quiz_db.list_questions(qid)) == 5
    assert all(q["selected"] == 0 for q in quiz_db.list_questions(qid))
    # 勾选 2 题后再生成 → 勾选被清空（回到全不选）
    qs = quiz_db.list_questions(qid)
    quiz_db.update_question(qs[0]["id"], selected=1)
    quiz_db.update_question(qs[1]["id"], selected=1)
    quiz_admin_api._do_generate(qid, keypoints, "admin")
    assert all(q["selected"] == 0 for q in quiz_db.list_questions(qid))


def test_extract_request_count_validation(db):
    """ExtractRequest.keypoint_count 校验：合法上限 30 / 缺省 None / 超限拒绝。"""
    import pydantic

    r = ExtractRequest(quiz_id="Q0001", keypoint_count=30)
    assert r.keypoint_count == 30
    r2 = ExtractRequest(quiz_id="Q0001")
    assert r2.keypoint_count is None
    with pytest.raises(pydantic.ValidationError):
        ExtractRequest(quiz_id="Q0001", keypoint_count=31)


def test_question_select_and_publish_only_selected(db, monkeypatch):
    import tempfile
    from pathlib import Path as _Path
    _tmp_db = _Path(tempfile.gettempdir()) / "test_qselect_whitelist.db"
    if _tmp_db.exists():
        _tmp_db.unlink()
    monkeypatch.setattr("app.api.quiz_admin.whitelist_db.DB_PATH", _tmp_db)
    monkeypatch.setattr("app.api.quiz_admin.whitelist_db._conn", None)
    """勾选题目 → 下发只发勾选题；未勾选下发 422；未勾选题不可答。"""
    qid = quiz_db.create_quiz("培训测验", scene="新员工培训", created_by="admin")
    db.replace_questions(qid, [
        {"question": f"q{i}?", "options": '{"A": "a", "B": "b", "C": "c", "D": "d"}', "answer": "A", "explanation": "e", "created_by": "a"}
        for i in range(3)
    ])
    for q in db.list_questions(qid):
        quiz_db.update_question(q["id"], status="approved")
    # 未勾选 → 下发 422
    with pytest.raises(HTTPException) as e:
        quiz_admin_api.quiz_publish(PublishRequest(quiz_id=qid, targets=["13800000001"], action="publish", valid_until=None), phone="13900000001")
    assert e.value.status_code == 422
    # 勾选 2 题
    qs = db.list_questions(qid)
    quiz_admin_api.quiz_question_select(QuestionSelectRequest(question_id=qs[0]["id"], selected=True), phone="13900000001")
    quiz_admin_api.quiz_question_select(QuestionSelectRequest(question_id=qs[1]["id"], selected=True), phone="13900000001")
    assert db.list_questions(qid)[0]["selected"] == 1
    # 发布后用户端只显示勾选 2 题
    quiz_db.update_quiz(qid, status="published", valid_from="2026-08-01T00:00:00+08:00", valid_until="2026-08-20T00:00:00+08:00")
    quiz_db.set_targets(qid, ["13800000001"])
    cur = quiz_api.current(phone="13800000001")
    assert len(cur["items"][0]["questions"]) == 2
    # 未勾选题提交 → 409
    with pytest.raises(HTTPException) as e:
        quiz_api.submit(QuizSubmitRequest(quiz_id=qid, q_id=qs[2]["id"], selected="A"), phone="13800000001")
    assert e.value.status_code == 409


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
    f = UploadFile(filename="半年培训材料.docx", file=BytesIO(b"PK\x03\x04" + b"\x00" * 100))
    res = quiz_admin_api.quiz_import(title="半年培训测验", scene="半年培训", month="", file=f, phone="13900000001")
    assert res["quiz_id"]
    names = [s["name"] for s in quiz_db.list_scenes(include_inactive=True)]
    assert "半年培训" in names


def test_review_approve_sets_selected(db):
    """确认题目即拟下发（selected=1）；打回退出下发（selected=0）；编辑保存同样拟下发。"""
    from app.models.schemas.quiz_admin import QuestionReviewRequest
    qid = quiz_db.create_quiz("培训测验", scene="新员工培训", created_by="admin")
    db.replace_questions(qid, [{
        "question": "q?", "options": '{"A": "a", "B": "b", "C": "c", "D": "d"}',
        "answer": "A", "explanation": "e", "created_by": "a",
    }])
    q = db.list_questions(qid)[0]
    assert q["selected"] == 0
    # 确认 → approved + 自动纳入下发
    quiz_admin_api.quiz_question_review(QuestionReviewRequest(question_id=q["id"], action="approve"), phone="13900000001")
    q2 = db.get_question(q["id"])
    assert q2["status"] == "approved" and q2["selected"] == 1
    # 打回 → draft + 退出下发
    quiz_admin_api.quiz_question_review(QuestionReviewRequest(question_id=q["id"], action="reject"), phone="13900000001")
    q3 = db.get_question(q["id"])
    assert q3["status"] == "draft" and q3["selected"] == 0
    # 编辑保存 → 视为确认，自动纳入下发
    quiz_admin_api.quiz_question_review(QuestionReviewRequest(question_id=q["id"], action="edit", edits={"question": "q2?"}), phone="13900000001")
    q4 = db.get_question(q["id"])
    assert q4["status"] == "approved" and q4["selected"] == 1 and q4["question"] == "q2?"

