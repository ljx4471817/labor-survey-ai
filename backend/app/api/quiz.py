# -*- coding: utf-8 -*-
"""测验：调查员端 API（current / my / submit / history / faq）。

权限：全部 require_user（token 解析 phone，答题数据仅本人可见）。
响应风格：plain dict + HTTPException（与 whitelist_admin 一致）。
"""
from __future__ import annotations

import functools
import json
import math
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.config import PROJECT_ROOT
from app.infra.auth import require_user
from app.models.schemas.quiz import QuizSubmitRequest
from app.persistence import quiz_db
from app.services.quiz_generator import is_expired, validate_selected

router = APIRouter()

UTC8 = timezone(timedelta(hours=8))
FAQ_PATH = PROJECT_ROOT / "knowledge-base" / "qa" / "faq.json"


def _now() -> str:
    return datetime.now(UTC8).isoformat(timespec="seconds")


@functools.lru_cache(maxsize=1)
def _cached_faq(mtime_ns: int) -> dict[str, dict]:
    """按文件 mtime 缓存 faq.json 索引（id zfill(3) → 条目）。"""
    items = json.loads(FAQ_PATH.read_text(encoding="utf-8"))
    return {str(it.get("id", "")).zfill(3): it for it in items}


def _load_faq() -> dict[str, dict]:
    try:
        return _cached_faq(FAQ_PATH.stat().st_mtime_ns)
    except (FileNotFoundError, OSError):
        return {}


def _parse_options(s: str) -> dict:
    try:
        d = json.loads(s)
        return d if isinstance(d, dict) else {}
    except (TypeError, ValueError):
        return {}


def _remaining_days(valid_until: str | None) -> int | None:
    if not valid_until:
        return None
    try:
        delta = datetime.fromisoformat(valid_until) - datetime.now(UTC8)
        return max(0, math.ceil(delta.total_seconds() / 86400))
    except ValueError:
        return None


def _kb_ref(q: dict) -> dict | None:
    if q.get("kb_faq_id"):
        return {"faq_id": q["kb_faq_id"], "question": q.get("kb_question", "")}
    return None


def _quiz_summary(quiz: dict, phone: str) -> dict:
    """组装单个测验的完整视图（题目 + 已答状态；未答题不泄露答案）。"""
    questions = quiz_db.list_questions(quiz["id"])
    answers = {a["q_id"]: a for a in quiz_db.get_answers(quiz["id"], phone)}
    views = []
    for q in questions:
        view = {
            "id": q["id"],
            "seq": q["seq"],
            "question": q["question"],
            "options": _parse_options(q["options"]),
        }
        a = answers.get(q["id"])
        if a:
            view.update({
                "answered": a["selected"],
                "correct": bool(a["correct"]),
                "answer": q["answer"],
                "explanation": q["explanation"],
                "source_quote": q.get("source_quote", ""),
                "kb_ref": _kb_ref(q),
            })
        views.append(view)
    total = len(questions)
    answered = len(answers)
    return {
        "quiz_id": quiz["id"],
        "month": quiz["month"],
        "scene": quiz.get("scene") or "",
        "title": quiz["title"],
        "status": quiz["status"],
        "valid_until": quiz.get("valid_until"),
        "remaining_days": _remaining_days(quiz.get("valid_until")),
        "total": total,
        "answered": answered,
        "completed": total > 0 and answered >= total,
        "questions": views,
    }


@router.get("/quiz/current")
def current(phone: str = Depends(require_user)) -> dict:
    """当前可见测验列表；非目标用户 / 无测验返回 {"items": []}（PRD 3.2.1）。"""
    now = _now()
    quiz_db.sync_expired(now)
    quizzes = quiz_db.list_active_for_user(phone, now)
    return {"items": [_quiz_summary(q, phone) for q in quizzes]}


@router.get("/quiz/my")
def my(phone: str = Depends(require_user)) -> dict:
    """我的测验（多场景并存）：待完成 / 已完成·过期 两组。"""
    now = _now()
    quiz_db.sync_expired(now)
    todo: list[dict] = []
    todo_ids: set[str] = set()
    for q in quiz_db.list_active_for_user(phone, now):
        s = _quiz_summary(q, phone)
        if not s["completed"]:
            todo_ids.add(q["id"])
            todo.append(s)
    done: list[dict] = []
    for q in quiz_db.list_quizzes():
        if q["id"] in todo_ids or q["status"] == "archived":
            continue
        if not (quiz_db.is_target(q["id"], phone) or quiz_db.count_answers(q["id"], phone) > 0):
            continue
        s = _quiz_summary(q, phone)
        if not s["completed"] and not is_expired(q.get("valid_until"), now):
            continue  # 未完成且未过期 → 属于待完成组（理论上不会走到）
        done.append(s)
    return {"todo": todo, "done": done}


@router.post("/quiz/submit")
def submit(req: QuizSubmitRequest, phone: str = Depends(require_user)) -> dict:
    """提交单题答案（提交即锁定，不可修改）。"""
    now = _now()
    quiz = quiz_db.get_quiz(req.quiz_id)
    if not quiz:
        raise HTTPException(404, "测验不存在")
    if quiz["status"] != "published":
        raise HTTPException(409, "测验未在可作答状态")
    if is_expired(quiz.get("valid_until"), now):
        raise HTTPException(409, "测验已过期，不可作答")
    if not quiz_db.is_target(req.quiz_id, phone):
        raise HTTPException(403, "你不在本次测验的下发名单中")
    q = quiz_db.get_question(req.q_id)
    if not q or q["quiz_id"] != req.quiz_id:
        raise HTTPException(404, "题目不存在")
    opts = _parse_options(q["options"])
    if not validate_selected(opts, req.selected):
        raise HTTPException(422, "选项不合法")
    if quiz_db.is_answered(req.quiz_id, phone, req.q_id):
        raise HTTPException(409, "该题已提交，不可修改")

    selected = req.selected.strip().upper()
    correct = selected == q["answer"].strip().upper()
    quiz_db.submit_answer(req.quiz_id, phone, req.q_id, selected, correct)

    total = quiz_db.count_questions(req.quiz_id)
    answered = quiz_db.count_answers(req.quiz_id, phone)
    return {
        "correct": correct,
        "answer": q["answer"],
        "explanation": q["explanation"],
        "source_quote": q.get("source_quote", ""),
        "kb_ref": _kb_ref(q),
        "answered": answered,
        "total": total,
        "completed": total > 0 and answered >= total,
    }


@router.get("/quiz/history")
def history(
    month: str | None = Query(None, pattern=r"^\d{4}-\d{2}$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=50),
    phone: str = Depends(require_user),
) -> dict:
    """个人历史测验列表（分页）。"""
    items = []
    for q in quiz_db.list_quizzes(month=month):
        if not (quiz_db.is_target(q["id"], phone) or quiz_db.count_answers(q["id"], phone) > 0):
            continue
        total = quiz_db.count_questions(q["id"])
        answered = quiz_db.count_answers(q["id"], phone)
        items.append({
            "quiz_id": q["id"],
            "month": q["month"],
            "scene": q.get("scene") or "",
            "title": q["title"],
            "total": total,
            "answered": answered,
            "score": quiz_db.count_correct(q["id"], phone),
            "completed": total > 0 and answered >= total,
            "status": q["status"],
            "submitted_at": quiz_db.latest_answer_ts(q["id"], phone),
        })
    items.sort(key=lambda x: (x["month"] or "", x["quiz_id"]), reverse=True)  # month 可空：空排后
    total = len(items)
    start = (page - 1) * page_size
    return {"items": items[start : start + page_size], "total": total, "page": page, "page_size": page_size}


@router.get("/quiz/history/{quiz_id}")
def history_detail(quiz_id: str, phone: str = Depends(require_user)) -> dict:
    """单套测验逐题明细（含解析/KB，复习用）。"""
    quiz = quiz_db.get_quiz(quiz_id)
    if not quiz:
        raise HTTPException(404, "测验不存在")
    if not (quiz_db.is_target(quiz_id, phone) or quiz_db.count_answers(quiz_id, phone) > 0):
        raise HTTPException(403, "无权限查看该测验")
    return _quiz_summary(quiz, phone)


@router.get("/faq/{faq_id}")
def faq_detail(faq_id: str, phone: str = Depends(require_user)) -> dict:
    """KB 单条详情（「相关知识点：KB 第 X 条」点击落点）。"""
    key = str(faq_id).zfill(3)
    it = _load_faq().get(key)
    if not it:
        raise HTTPException(404, "KB 条目不存在")
    return {
        "id": key,
        "question": it.get("question", ""),
        "answer": it.get("answer", ""),
        "source": it.get("source", ""),
        "category": it.get("category", ""),
        "keywords": it.get("keywords", []),
    }
